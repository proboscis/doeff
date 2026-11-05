;;; The meaning of the existing SqlTransaction is not changed: for a transaction that does not choose batched, the sequence of round trips the answerer sends to the DB
;;; (the sequence of statements per round trip) is compared with expectations written from the baseline's sequence (eaeeb75c4 -- before adding the round-trip-batching form), to confirm it is the same as the baseline.
;;;   - PostgreSQL (postgres-sql-handler and pooled-postgres-sql-handler): a relay placed between the client and a disposable PostgreSQL
;;;     (postgres_support/statement_wire_tap.py) records, per round trip, the statements the client sent on the wire.
;;;   - sqlite (sqlite-file-sql-handler -- goes through the same sqlite-answer-transaction as the in-memory DB answerer): the connection's trace callback records the statements sent,
;;;     in the order sent (sqlite has no round trips, so only the sequence of statements).
;;; Representative programs: 2 statements plus SqlInsertRows (2 rows) plus SqlNotify (with a lock key); a program that emits no SQL (with / without a lock key); one that fails midway and
;;; ROLLBACK; the shape of the custody body's commit-rows (controllers/custody_body/protocol/store.hy -- with a lock key, version SELECT x 2 -> UPSERT x 2);
;;; and SqlNotify that sqlite refuses (ROLLBACK).
;;; This file uses only names that exist at the baseline -- it was confirmed, at the time of the change, that swapping in the baseline's answerer source still gives green with the same expectations. Only the notice
;;; changed shape in a later change: SqlNotify now has the names involved (topics), and the pg_notify body is no longer empty but an argument ($2) (the number and
;;; order of round trips are unchanged).
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import queue)
(import sqlite3)
(import concurrent.futures [ThreadPoolExecutor])
(import doeff [Program])
(import doeff_core_effects [state])
(import doeff_core_effects [SqlQuery SqlInsertRows SqlTransaction SqlNotify SqlEnsureTables SqlParam SqlRows SqlFailed SqlTransactionMisuse SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects [postgres-sql-handler PostgresDatabase PostgresConnections])
(import doeff_core_effects [pooled-postgres-sql-handler])
(import doeff_core_effects [SqliteFile open-sqlite-files close-sqlite-files sqlite-file-sql-handler])
(import disposable_postgres [session-dsn session-postgres-skip-reason])
(import statement_wire_tap [StatementWireTap])

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val LOCK "invariant-lock")

(val ITEMS (SqlTable :name "items"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT))
                     :primary-key #("id")))
(val ACCOUNTS (SqlTable :name "accounts"
                        :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "version" :type SqlColumnType.INTEGER))
                        :primary-key #("id")))

;; The baseline's sequence of round trips (PostgreSQL) -- one statement is one round trip. SqlInsertRows is executemany (a per-row statement in one round trip). Arguments are in wire spelling ($1 ...).
;; At the baseline, after ROLLBACK the driver (psycopg) sent DEALLOCATE ALL (when psycopg sees ROLLBACK on a connection that remembers statements it prepared itself,
;; it discards that memory and sends it). Since psycopg's automatic prepare was stopped, it is not sent (doeff is the only owner of names).
(val INSERT-PARAMS "INSERT INTO items (id, label) VALUES ($1, $2)")
(val LOCK-WIRE "SELECT pg_advisory_xact_lock(hashtext($1))")
(val BASE-POSTGRES-ROUND-TRIPS
  {"statements, SqlInsertRows and notice (with lock)" #(#("BEGIN") #(LOCK-WIRE) #(INSERT-PARAMS) #(INSERT-PARAMS INSERT-PARAMS)
                                        #("SELECT count(*) FROM items") #("SELECT pg_notify($1, $2)") #("COMMIT"))
   "no SQL (with lock)" #(#("BEGIN") #(LOCK-WIRE) #("COMMIT"))
   "no SQL (without lock)" #(#("BEGIN") #("COMMIT"))
   "fails midway and ROLLBACK" #(#("BEGIN") #("INSERT INTO items (id, label) VALUES (110, 'x')")
                             #("INSERT INTO items (id, label) VALUES (110, 'y')") #("ROLLBACK"))
   "custody commit-rows shape (with lock)" #(#("BEGIN") #(LOCK-WIRE) #("SELECT version FROM accounts WHERE id = $1")
                                            #("SELECT version FROM accounts WHERE id = $1")
                                            #("INSERT INTO accounts (id, version) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET version = excluded.version")
                                            #("INSERT INTO accounts (id, version) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET version = excluded.version")
                                            #("COMMIT"))})

;; The baseline's sequence of statements (sqlite -- the trace callback passes the statement with values filled in).
(val BASE-SQLITE-STATEMENTS
  {"statements and SqlInsertRows (with lock)" #("BEGIN IMMEDIATE" "INSERT INTO items (id, label) VALUES (100, 'a')" "INSERT INTO items (id, label) VALUES (101, 'b')"
                                  "INSERT INTO items (id, label) VALUES (102, 'c')" "SELECT count(*) FROM items" "COMMIT")
   "no SQL (with lock)" #("BEGIN IMMEDIATE" "COMMIT")
   "fails midway and ROLLBACK" #("BEGIN IMMEDIATE" "INSERT INTO items (id, label) VALUES (110, 'x')" "INSERT INTO items (id, label) VALUES (110, 'y')"
                             "ROLLBACK")
   "custody commit-rows shape (with lock)" #("BEGIN IMMEDIATE" "SELECT version FROM accounts WHERE id = 1" "SELECT version FROM accounts WHERE id = 2"
                                            "INSERT INTO accounts (id, version) VALUES (1, 2) ON CONFLICT (id) DO UPDATE SET version = excluded.version"
                                            "INSERT INTO accounts (id, version) VALUES (2, 1) ON CONFLICT (id) DO UPDATE SET version = excluded.version"
                                            "COMMIT")
   "SqlNotify refused by sqlite" #("BEGIN IMMEDIATE" "INSERT INTO items (id, label) VALUES (120, 'n')" "ROLLBACK")})


;; --- Representative programs -----------------------------------------------------------------------------------------------------

(defk statements-and-rows [notice]
  {:pre [(: notice bool)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that sends 2 statements and SqlInsertRows (2 rows), and, if notice, emits a notice at the end."
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (:id, :label)" #((SqlParam :name "id" :value 100) (SqlParam :name "label" :value "a"))))
  (<- (SqlInsertRows DB "items" #("id" "label") #(#(101 "b") #(102 "c"))))
  (<- (SqlQuery DB "SELECT count(*) FROM items" #()))
  (when notice
    (<- (SqlNotify DB "invariant" #("table:items"))))
  "flushed")


(defk no-sql []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "A program that emits no SQL at all (pure computation only)."
  (+ 1 2))


(defk duplicate-insert []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program whose 2nd statement fails with a uniqueness violation."
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (110, 'x')" #()))
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (110, 'y')" #()))
  "never reached")


(defk checked-and-upserted []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "The shape of the custody body's commit-rows: a program that SELECTs the version per row to check, and UPSERTs per row (does not emit SqlNotify)."
  (for [id #(1 2)]
    (<- (SqlQuery DB "SELECT version FROM accounts WHERE id = :id" #((SqlParam :name "id" :value id)))))
  (for [#(id version) #(#(1 2) #(2 1))]
    (<- (SqlQuery DB "INSERT INTO accounts (id, version) VALUES (:id, :version) ON CONFLICT (id) DO UPDATE SET version = excluded.version"
                  #((SqlParam :name "id" :value id) (SqlParam :name "version" :value version)))))
  "checked and written")


(defk notice-under-sqlite []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that inserts 1 row and then emits a notice (the sqlite answerer does not accept notices, so it refuses)."
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (120, 'n')" #()))
  (<- (SqlNotify DB "invariant" #("table:items")))
  "never reached")


(defk prepared-tables []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To rebuild the check's table (do not leave the rows of the previous run)."
  (<- (SqlQuery DB "DROP TABLE IF EXISTS items" #()))
  (<- (SqlQuery DB "DROP TABLE IF EXISTS accounts" #()))
  (<- (SqlEnsureTables DB #(ITEMS ACCOUNTS)))
  None)


(defk answer-or-refusal [transaction]
  {:pre [(: transaction SqlTransaction)] :post [(: % "the transaction's answer | refusal marker")]
   :tags {:context "sql" :role "program"}}
  "To fire the transaction, and return the refusal marker if it is refused with SqlTransactionMisuse."
  (try
    (<- answer transaction)
    answer
    (except [SqlTransactionMisuse]
      "refused")))


;; --- PostgreSQL ---------------------------------------------------------------------------------------------------------

(defk postgres-round-trips [tap]
  {:pre [(: tap StatementWireTap)] :post [(: % dict)]
   :tags {:context "sql" :role "program"}}
  "To fire the representative programs one by one in a transaction that does not choose batched, and return name -> #(answer, sequence of round trips) (the round trips are what the relay recorded)."
  (<- (prepared-tables))
  (var seen {})
  (for [#(name transaction) #(#("statements, SqlInsertRows and notice (with lock)" (SqlTransaction DB (statements-and-rows True) LOCK))
                              #("no SQL (with lock)" (SqlTransaction DB (no-sql) LOCK))
                              #("no SQL (without lock)" (SqlTransaction DB (no-sql) None))
                              #("fails midway and ROLLBACK" (SqlTransaction DB (duplicate-insert) None))
                              #("custody commit-rows shape (with lock)" (SqlTransaction DB (checked-and-upserted) LOCK)))]
    (.reset tap)
    (<- answer (answer-or-refusal transaction))
    (:= seen (| seen {name #(answer (.round-trips tap))})))
  seen)


(defk tapped-dsn [tap]
  {:pre [(: tap StatementWireTap)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "To rewrite the check's DSN into a DSN that connects to the relay's port (no SSL or GSS negotiation -- the relay reads plaintext messages)."
  (import psycopg.conninfo [make-conninfo])
  (make-conninfo (or POSTGRES-DSN "") :host "127.0.0.1" :port (str tap.port) :sslmode "disable" :gssencmode "disable"))


(defk tap-target []
  {:pre [] :post [(: % (| str tuple))]
   :tags {:context "sql" :role "program"}}
  "To read the PostgreSQL port (a unix socket path or #(host port)) that the check's DSN points to."
  (import psycopg.conninfo [conninfo-to-dict])
  (val given (conninfo-to-dict (or POSTGRES-DSN "")))
  (val host (.get given "host" "127.0.0.1"))
  (val port (.get given "port" "5432"))
  (if (.startswith host "/")
      (.format "{}/.s.PGSQL.{}" host port)
      #(host (int port))))


(defk asserted-postgres-round-trips [seen]
  {:pre [(: seen dict)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To check the answers of postgres-round-trips against the baseline's sequence of round trips and answers."
  (assert (= (dfor #(name #(_ trips)) (.items seen) name trips) BASE-POSTGRES-ROUND-TRIPS)
          (dfor #(name #(_ trips)) (.items seen) name trips))
  (val answers (dfor #(name #(answer _)) (.items seen) name answer))
  (assert (= (get answers "statements, SqlInsertRows and notice (with lock)") "flushed") answers)
  (assert (= (get answers "no SQL (with lock)") 3) answers)
  (assert (= (get answers "no SQL (without lock)") 3) answers)
  (val failed (get answers "fails midway and ROLLBACK"))
  (assert (and (isinstance failed SqlFailed) (= failed.sqlstate "23505")) failed)
  (assert (= (get answers "custody commit-rows shape (with lock)") "checked and written") answers)
  None)


(deftest test-postgres-unbatched-transactions-send-the-base-round-trips
  {:skip-if (bool POSTGRES-SKIP-REASON) :skip-reason POSTGRES-SKIP-REASON}
  ;; The meaning of the existing SqlTransaction is not changed (#3605): a transaction that does not choose batched sends BEGIN, the lock, statements, the notice, and COMMIT / ROLLBACK
  ;; each in 1 round trip, in the order they come out -- for both PostgreSQL answerers, the sequence of round trips is the same as the baseline.
  (<- target (tap-target))
  (val tap (StatementWireTap target))
  (<- dsn (tapped-dsn tap))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (val plain (PostgresConnections #((PostgresDatabase :name DB :dsn dsn)) :size 1))
    (try
      (<- direct (with-handler [(postgres-sql-handler plain)] (postgres-round-trips tap)))
      (finally (.close plain)))
    (val shared (PostgresConnections #((PostgresDatabase :name DB :dsn dsn)) :size 1))
    (try
      (<- pooled (with-handler [(state) (pooled-postgres-sql-handler shared pool)] (postgres-round-trips tap)))
      (finally (.close shared)))
    (finally (.shutdown pool) (.close tap)))
  (<- (asserted-postgres-round-trips direct))
  (<- (asserted-postgres-round-trips pooled)))


;; --- sqlite ---------------------------------------------------------------------------------------------------------------

(defk drained [traced]
  {:pre [(: traced queue.SimpleQueue)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To take out, in the order received, all the statements the trace callback received."
  (tuple (gfor _ (range (.qsize traced)) (.get traced))))


(defk sqlite-statements [traced]
  {:pre [(: traced queue.SimpleQueue)] :post [(: % dict)]
   :tags {:context "sql" :role "program"}}
  "To fire the representative programs one by one in a transaction that does not choose batched, and return name -> #(answer, sequence of statements sent)."
  (<- (prepared-tables))
  (var seen {})
  (for [#(name transaction) #(#("statements and SqlInsertRows (with lock)" (SqlTransaction DB (statements-and-rows False) LOCK))
                              #("no SQL (with lock)" (SqlTransaction DB (no-sql) LOCK))
                              #("fails midway and ROLLBACK" (SqlTransaction DB (duplicate-insert) None))
                              #("custody commit-rows shape (with lock)" (SqlTransaction DB (checked-and-upserted) LOCK))
                              #("SqlNotify refused by sqlite" (SqlTransaction DB (notice-under-sqlite) None)))]
    (<- (drained traced))
    (<- answer (answer-or-refusal transaction))
    (<- statements (drained traced))
    (:= seen (| seen {name #(answer statements)})))
  seen)


(deftest test-sqlite-unbatched-transactions-run-the-base-statements [tmp-path]
  ;; The meaning of the existing SqlTransaction is not changed (#3605): a transaction that does not choose batched sends BEGIN IMMEDIATE, statements, and COMMIT / ROLLBACK in the order they come out
  ;; -- the sequence of statements is the same as the baseline (it goes through the same sqlite-answer-transaction as the sqlite_store of the custody body's answerer foundation).
  (val path (str (/ tmp-path "invariant.sqlite3")))
  (.close (sqlite3.connect path))
  (<- files (open-sqlite-files #((SqliteFile :name DB :path path))))
  (val traced (queue.SimpleQueue))
  (.set-trace-callback (. (get files.connections 0) connection) (fn [text] (.put traced text)))
  (try
    (<- seen (with-handler [(state) (sqlite-file-sql-handler files)] (sqlite-statements traced)))
    (finally (<- (close-sqlite-files files))))
  (assert (= (dfor #(name #(_ statements)) (.items seen) name statements) BASE-SQLITE-STATEMENTS)
          (dfor #(name #(_ statements)) (.items seen) name statements))
  (val answers (dfor #(name #(answer _)) (.items seen) name answer))
  (assert (= (get answers "statements and SqlInsertRows (with lock)") "flushed") answers)
  (assert (= (get answers "no SQL (with lock)") 3) answers)
  (val failed (get answers "fails midway and ROLLBACK"))
  (assert (and (isinstance failed SqlFailed) (= failed.sqlstate "23000")) failed)
  (assert (= (get answers "custody commit-rows shape (with lock)") "checked and written") answers)
  (assert (= (get answers "SqlNotify refused by sqlite") "refused") answers))
