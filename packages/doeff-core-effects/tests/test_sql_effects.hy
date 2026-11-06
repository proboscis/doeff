;;; Tests for the generic SQL effects (sql_effects.hy).
;;;   - Fire every effect contract at the I/O-free handler (sqlite-sql-handler): argument rewriting and value normalization; the two failure types (SqlFailed with SQLSTATE classes,
;;;     SqlUnreachable); the three transaction contracts (roll back at the first failure and do not resume; refuse forbidden effects and nesting; roll back on exception);
;;;     DDL declaration; passing undeclared databases to the outside.
;;;   - For the real handlers (postgres-sql-handler, clickhouse-http-sql-handler), fire dialect rewriting and answer mapping with pure functions. Integration tests against a real DB
;;;     are skipped unless the DSN / URL environment variables (DOEFF_SQL_TEST_POSTGRES_DSN, DOEFF_SQL_TEST_CLICKHOUSE_URL) are set.
;;;   - The handler that does not block the scheduler (pooled-postgres-sql-handler -- #880 U2), on a real PG: the same scenario gives the same answers; another task makes progress next to a slow query;
;;;     the transaction lock number is the same as the old hashtext way of writing it; cancellation rolls back and returns the connection and the permit.
;;;   - On a real PG, postgres-sql-handler also does not block the scheduler (#1215): another task makes progress next to a slow query; only transactions with the same lock-key
;;;     are serialized, and different keys run side by side.
;;;   - Round-trip batching (SqlBatch -- #3605): BEGIN and the lock take the same round trip as the first statement; the signal and the commit bundle take the same round trip as COMMIT; a program that issues no SQL takes
;;;     0 round trips (counted with a fake handler). If a statement in the middle of a bundle fails, the rest are not sent and ROLLBACK runs; SQL after a commit bundle is refused; a bundle outside a transaction is refused
;;;     (sqlite and real PG). The next transaction can keep using the same connection after a statement failed inside the pipeline (pooled). In an environment without pipeline mode,
;;;     it fails at the point where the connection lease is created.
(require doeff-hy.macros [deftest defk deff <- val var with-handler])
(require doeff-hy.record [defrecord])
(import os)
(import json)
(import importlib.util)
(import threading)
(import queue)
(import select)
(import signal)
(import subprocess)
(import sys)
(import time)
(import socket)
(import http.server)
(import urllib.parse)
(import collections.abc [Callable])
(import concurrent.futures [Future ThreadPoolExecutor])
(import dataclasses [dataclass replace])
(import decimal [Decimal])
(import doeff [Program])
(import doeff_core_effects [state])
(import doeff_core_effects [Get Put])
(import doeff_core_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SqlNotify SetSqlOutage SqlParam SqlRows SqlFailed SqlUnreachable SqlSchemaApplied SqlTransactionMisuse SqlTable SqlColumn SqlColumnType SqlIndex]
        doeff_core_effects._sql_effects [normalized-value normalized-rows split-statement])
(import doeff_core_effects._sql_effects :as sd)
(import doeff_core_effects._sql_transaction [TransactionFlush run-in-batched-transaction])
(import doeff_core_effects [sqlite-sql-handler]
        doeff_core_effects._sqlite_sql [sqlite-statement sqlite-failure])
(import doeff_core_effects [postgres-sql-handler PostgresDatabase PostgresConnections PostgresTimeouts]
        doeff_core_effects._postgres_sql [postgres-statement postgres-insert-statement postgres-failure postgres-schema-statements])
(import doeff_core_effects [pooled-postgres-sql-handler])
(import doeff_core_effects [CreateExternalPromise Wait Spawn Gather Cancel TaskCancelledError])
(import doeff_core_effects [clickhouse-http-sql-handler clickhouse-query-request clickhouse-answer clickhouse-schema-statements ClickHouseDatabase]
        doeff_core_effects._clickhouse_http_sql [clickhouse-statement clickhouse-insert-request clickhouse-kill-request clickhouse-waits clickhouse-rows clickhouse-written-rows clickhouse-failure ClickHouseWaits ClickHouseRequest ClickHouseResponse ClickHouseParam WAIT-MARGIN-SECONDS KILL-GRACE-SECONDS USER-AGENT RoundTrips EXIT-SETTLE-SECONDS EXIT-KILL-GRACE-SECONDS])
(import disposable_postgres [session-dsn session-postgres-skip-reason])
(import pytest)

;; The waits in this file are about real time (which waits count is listed in this file's row of the roster tests/realtime_tests.toml) -- the rule on sleeps in tests (DOEFF211 of doeff-linter)
;; is not applied to sleeps inside a file that declares itself realtime.
(val pytestmark pytest.mark.realtime)

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val CLICKHOUSE-URL (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_URL"))
;; Skip for the real-PG tests (no DSN or no psycopg). Unless the env var is set, the DSN is the throwaway PostgreSQL that conftest started
;; (postgres_support/disposable_postgres.py). If it could not be started, the reason is named.
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POOLED-SKIP-REASON (cond POSTGRES-SKIP-REASON POSTGRES-SKIP-REASON
                              (is (importlib.util.find-spec "psycopg") None) "psycopg is missing (add it with uv run --with psycopg)"
                              True ""))
(val POOLED-SKIP (bool POOLED-SKIP-REASON))

;; Test tables: a table of rows with a primary key, and a table with a unique index.
(val ITEMS (SqlTable :name "items"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT)
                                (SqlColumn :name "weight" :type SqlColumnType.FLOAT :nullable True)
                                (SqlColumn :name "blob" :type SqlColumnType.BYTES :nullable True)
                                (SqlColumn :name "flag" :type SqlColumnType.BOOLEAN :nullable True)
                                (SqlColumn :name "doc" :type SqlColumnType.JSON :nullable True))
                     :primary-key #("id")
                     :indexes #((SqlIndex :name "items_label" :columns #("label") :unique True))))


(defk seeded [database]
  {:pre [(: database str)] :post [(: % SqlSchemaApplied)]
   :tags {:context "sql" :role "program"}}
  "To declare and prepare the test table."
  (<- applied (SqlEnsureTables database #(ITEMS)))
  applied)


(defk count-items [database]
  {:pre [(: database str)] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "To read the number of rows in items."
  (<- answer SqlRows (SqlQuery database "SELECT count(*) FROM items" #()))
  (val counted (get answer.rows 0 0))
  (assert (isinstance counted int) counted)
  counted)


;; --- Argument rewriting and value normalization --------------------------------------------------------------------------------------------------

(defk round-trip [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that writes every value of the closed set and reads it back (sqlite and real PostgreSQL give the same answer)."
  (<- (seeded database))
  (<- inserted (SqlInsertRows database "items" #("id" "label" "weight" "blob" "flag" "doc")
                              #(#(1 "one" 1.5 b"\x00\xff" True "{\"a\": 1}") #(2 "two" None None False None))))
  (<- one (SqlQuery database "INSERT INTO items (id, label) VALUES (:id, :label)" #((SqlParam :name "id" :value 3) (SqlParam :name "label" :value "a:b"))))
  (<- read (SqlQuery database
                     "SELECT id, label, weight, blob, flag, doc, ':id' FROM items WHERE id >= :low AND label <> ':low' -- :ignored
                      ORDER BY id"
                     #((SqlParam :name "low" :value 1))))
  #(inserted one read))


(deftest test-sqlite-rewrites-neutral-params-and-normalizes-values
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (round-trip DB)))
  (val inserted (get answers 0))
  (val one (get answers 1))
  (val read (get answers 2))
  (assert (= inserted (SqlRows :rows #() :rowcount 2)) inserted)
  (assert (= one (SqlRows :rows #() :rowcount 1)) one)
  ;; `:name` inside a literal or a comment is not an argument; BOOLEAN is bool, BYTES is bytes, JSON is text.
  (assert (= read.rows #(#(1 "one" 1.5 b"\x00\xff" True "{\"a\": 1}" ":id") #(2 "two" None None False None ":id") #(3 "a:b" None None None None ":id")))
          read)
  (assert (= read.rowcount 3)))


(deftest test-params-must-match-the-statement
  (<- bound (sqlite-statement "SELECT :a, :a, x::text" #((SqlParam :name "a" :value 1))))
  (assert (= #(bound.text bound.values) #("SELECT ?, ?, x::text" #(1 1))) bound)
  (for [#(statement params) [#("SELECT :a" #()) #("SELECT 1" #((SqlParam :name "a" :value 1)))
                             #("SELECT :a" #((SqlParam :name "a" :value 1) (SqlParam :name "a" :value 2)))]]
    (try
      (<- (sqlite-statement statement params))
      (assert False (.format "Not refused: {}" statement))
      (except [ValueError])))
  (try
    (<- (sqlite-statement "SELECT :a" #((SqlParam :name "a" :value (json.loads "[1]")))))
    (assert False "A value outside the closed set got through")
    (except [TypeError]))
  (assert (= (! (normalized-value (Decimal "2"))) 2)))


(deftest test-a-remembered-split-still-checks-each-call-and-keeps-values-in-order
  ;; The second time for the same statement uses the remembered split (the same tuple) -- even so, params are checked on every call, and values are lined up in the order of `?`.
  (val statement "SELECT :b, :a, :b, ':a' -- :c")
  (<- first (split-statement statement))
  (<- again (split-statement statement))
  (assert (is first again) "The second time for the same statement did not return the remembered split")
  (<- bound (sqlite-statement statement #((SqlParam :name "a" :value 1) (SqlParam :name "b" :value "x"))))
  (assert (= #(bound.text bound.values) #("SELECT ?, ?, ?, ':a' -- :c" #("x" 1 "x"))) bound)
  (try
    (<- (sqlite-statement statement #((SqlParam :name "a" :value 1))))
    (assert False "A missing param got through on a remembered statement")
    (except [ValueError])))


(deftest test-split-memo-holds-at-most-its-part-limit-after-a-stream-of-value-count-statements
;; Even when statements that line up as many arguments as there are values (`IN (:k0, ...)`) are streamed for each number of values, the parts the memo holds (SqlText, SqlPlaceholder) stay
;; within SPLIT-MEMO-PART-LIMIT (when limited only by the number of statements, it held 4096 statements x about 120 parts).
;; The memo is one per module, so set aside its contents before streaming, stream from empty, and restore at the end (so as not to pollute the memo of other tests).
  (val saved #((dict sd.SPLIT-MEMO) (get sd.SPLIT-MEMO-PARTS 0)))
  (.clear sd.SPLIT-MEMO)
  (setv (get sd.SPLIT-MEMO-PARTS 0) 0)
  (try
    (val wide (+ "SELECT cid FROM events WHERE cid IN (" (.join ", " (gfor i (range 200) f":k{i}")) ")"))
    (<- first (split-statement wide))
    (<- again (split-statement wide))
    (assert (= (len first) 401) (len first))
    (assert (= first again) "The split of a statement that is not remembered differs on each call")
    (assert (not-in wide sd.SPLIT-MEMO) "A statement with hundreds of parts was remembered")
    ;; The shape of present-cold-keys in agora-records: a different statement for each combination of cid count x seq count (exceeds 4096 statements, and the total number of parts exceeds 300,000).
    (val stream (lfor c (range 1 41) s (range 1 121)
                      (+ "SELECT cid, record_seq, rev FROM events WHERE cid IN (" (.join ", " (gfor i (range c) f":cid{i}"))
                         ") AND record_seq IN (" (.join ", " (gfor i (range s) f":seq{i}")) ")")))
    (val fixed (lfor i (range 6000) f"SELECT body FROM events WHERE cid = :cid AND record_seq = :seq AND kind = 'k{i}'"))
    (for [statement (+ stream fixed)]
      (<- (split-statement statement)))
    (val held (sum (gfor parts (.values sd.SPLIT-MEMO) (len parts))))
    (assert (= held (get sd.SPLIT-MEMO-PARTS 0)) #(held (get sd.SPLIT-MEMO-PARTS 0)))
    (assert (<= held sd.SPLIT-MEMO-PART-LIMIT) held)
    (assert (<= (len sd.SPLIT-MEMO) sd.SPLIT-MEMO-LIMIT) (len sd.SPLIT-MEMO))
    (assert (all (gfor parts (.values sd.SPLIT-MEMO) (<= (len parts) sd.SPLIT-MEMO-STATEMENT-PARTS))))
    ;; The second time for a remembered statement returns the remembered split itself (even if remembering stops at the limit, the speed for remembered statements does not drop).
    (val kept (next (iter sd.SPLIT-MEMO)))
    (<- kept-again (split-statement kept))
    (assert (is kept-again (get sd.SPLIT-MEMO kept)))
    (finally
      (.clear sd.SPLIT-MEMO)
      (.update sd.SPLIT-MEMO (get saved 0))
      (setv (get sd.SPLIT-MEMO-PARTS 0) (get saved 1)))))


(deftest test-plain-rows-pass-through-and-other-values-are-still-normalized
;; A row whose value types are exactly the closed set is only copied; a row mixing other types goes through normalized-value as before.
  (<- plain (normalized-rows [#(1 "a" 1.5 b"x" True None)]))
  (assert (= plain #(#(1 "a" 1.5 b"x" True None))) plain)
  (<- mixed (normalized-rows [#(1 (Decimal "2.5") (bytearray b"y"))]))
  (assert (= mixed #(#(1 2.5 b"y"))) mixed)
  (try
    (<- (normalized-rows [#(1 (object))]))
    (assert False "A type with no copy rule got through")
    (except [TypeError])))


;; --- The two failure types ------------------------------------------------------------------------------------------------------------

(defk failures [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that collects a refusal (unique violation), a statement error, unreachability, and the answer after recovery."
  (<- (seeded database))
  (<- (SqlQuery database "INSERT INTO items (id, label) VALUES (1, 'x')" #()))
  (<- duplicate (SqlQuery database "INSERT INTO items (id, label) VALUES (1, 'y')" #()))
  (<- broken (SqlQuery database "SELEC 1" #()))
  (<- (SetSqlOutage database True))
  (<- down (SqlQuery database "SELECT 1" #()))
  (<- down-insert (SqlInsertRows database "items" #("id" "label") #(#(9 "z"))))
  (<- (SetSqlOutage database False))
  (<- back (SqlQuery database "SELECT 1" #()))
  #(duplicate broken down down-insert back))


(deftest test-sqlite-gives-failures-as-values
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (failures DB)))
  (val duplicate (get answers 0))
  (val broken (get answers 1))
  (val down (get answers 2))
  (val down-insert (get answers 3))
  (val back (get answers 4))
  (assert (and (isinstance duplicate SqlFailed) (= duplicate.sqlstate "23000")) duplicate)
  (assert (and (isinstance broken SqlFailed) (= broken.sqlstate "42000")) broken)
  (assert (isinstance down SqlUnreachable) down)
  (assert (isinstance down-insert SqlUnreachable) down-insert)
  (assert (= back (SqlRows :rows #(#(1)) :rowcount 1)) back))


(defk out-of-range [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that passes an integer outside the INTEGER range (64 bit) both as an argument bind and as a row insert."
  (<- (seeded database))
  (<- bound (SqlQuery database "INSERT INTO items (id, label) VALUES (:id, 'x')" #((SqlParam :name "id" :value (** 2 63)))))
  (<- inserted (SqlInsertRows database "items" #("id" "label") #(#((** 2 63) "y"))))
  (<- after (SqlQuery database "SELECT count(*) FROM items" #()))
  #(bound inserted after))


(deftest test-sqlite-refuses-an-out-of-range-integer-like-postgres
;; Real PostgreSQL refuses values outside the bigint range with 22003 (numeric_value_out_of_range). The sqlite3 bind raises OverflowError (not a kind of sqlite3.Error),
;; so unless the handler turns it into a value, the exception escapes into the caller's Program, and the mock and the real answers diverge.
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (out-of-range DB)))
  (val bound (get answers 0))
  (val inserted (get answers 1))
  (val after (get answers 2))
  (assert (and (isinstance bound SqlFailed) (= bound.sqlstate "22003")) bound)
  (assert (and (isinstance inserted SqlFailed) (= inserted.sqlstate "22003")) inserted)
  ;; The connection is usable even after the refusal (no row was inserted).
  (assert (= after (SqlRows :rows #(#(0)) :rowcount 1)) after))


(deftest test-sqlite-exception-classes-map-to-sqlstate-classes
  (assert (= (! (sqlite-failure #("IntegrityError" "DatabaseError" "Error") "UNIQUE constraint failed")) (SqlFailed :sqlstate "23000" :reason "UNIQUE constraint failed")))
  (assert (= (. (! (sqlite-failure #("DataError" "DatabaseError") "too big")) sqlstate) "22000"))
  (assert (= (. (! (sqlite-failure #("OperationalError" "DatabaseError") "database is locked")) sqlstate) "40001"))
  (assert (= (. (! (sqlite-failure #("OperationalError" "DatabaseError") "no such table: x")) sqlstate) "42000"))
  (assert (isinstance (! (sqlite-failure #("ProgrammingError" "DatabaseError") "Cannot operate on a closed database.")) SqlUnreachable)))


;; --- The transaction contracts ---------------------------------------------------------------------------------------------------

(defk insert-two [database log]
  {:pre [(: database str) (: log list)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that inserts 2 rows inside a transaction (leaves its progress in log)."
  (<- (SqlQuery database "INSERT INTO items (id, label) VALUES (10, 'a')" #()))
  (.append log "first row")
  (<- (SqlInsertRows database "items" #("id" "label") #(#(11 "b"))))
  (.append log "second row")
  "inserted")


(defk fail-second [database log]
  {:pre [(: database str) (: log list)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that causes a unique violation after inserting the first row. Checks that even when exceptions are caught broadly, it is not resumed after the failure."
  (<- (SqlQuery database "INSERT INTO items (id, label) VALUES (20, 'c')" #()))
  (.append log "first row")
  (try
    (<- (SqlQuery database "INSERT INTO items (id, label) VALUES (20, 'd')" #()))
    (.append log "resumed after the failure")
    (except [Exception]
      (.append log "the failure was thrown in as an exception")))
  "never reached")


(defk misuse [database program]
  {:pre [(: database str) (: program Program)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A scenario that turns the refusal of a transaction with SqlTransactionMisuse into a refusal message and returns it."
  (try
    (<- (SqlTransaction database program None))
    "not refused"
    (except [error SqlTransactionMisuse]
      (str error))))


(defk ask-state []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that emits a forbidden effect (state Get) inside a transaction."
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (30, 'e')" #()))
  (<- (Get "key"))
  "never reached")


(defk nested []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that emits a nested transaction inside a transaction."
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (31, 'f')" #()))
  (<- (SqlTransaction DB (count-items DB) None))
  "never reached")


(defk other-database []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that queries a different database inside a transaction."
  (<- (SqlQuery "other" "SELECT 1" #()))
  "never reached")


(defk raising []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that raises an exception after inserting 1 row."
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (40, 'g')" #()))
  (raise (RuntimeError "program error")))


(defk transactions [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that fires the three transaction contracts in order."
  (<- (seeded database))
  (val committed-log [])
  (<- committed (SqlTransaction database (insert-two database committed-log) "lock-a"))
  (<- after-commit (count-items database))
  (val failed-log [])
  (<- failed (SqlTransaction database (fail-second database failed-log) None))
  (<- after-failure (count-items database))
  (<- refused-state (misuse database (ask-state)))
  (<- refused-nested (misuse database (nested)))
  (<- refused-other (misuse database (other-database)))
  (<- after-misuse (count-items database))
  (var raised "")
  (try
    (<- (SqlTransaction database (raising) None))
    (except [error RuntimeError] (:= raised (str error))))
  (<- after-raise (count-items database))
  #(committed (tuple committed-log) after-commit failed (tuple failed-log) after-failure refused-state refused-nested refused-other after-misuse
    raised after-raise))


(deftest test-sqlite-transaction-keeps-the-three-promises
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (transactions DB)))
  (val committed (get answers 0))
  (val committed-log (get answers 1))
  (val after-commit (get answers 2))
  (val failed (get answers 3))
  (val failed-log (get answers 4))
  (val after-failure (get answers 5))
  (val refused-state (get answers 6))
  (val refused-nested (get answers 7))
  (val refused-other (get answers 8))
  (val after-misuse (get answers 9))
  (val raised (get answers 10))
  (val after-raise (get answers 11))
  ;; The answer of the committed program is returned as is, and the 2 rows remain.
  (assert (= #(committed committed-log after-commit) #("inserted" #("first row" "second row") 2)))
  ;; (1) Roll back at the first failure and do not resume the program (no exception is thrown in -- it does not continue even for a program that catches broadly).
  (assert (and (isinstance failed SqlFailed) (= failed.sqlstate "23000")) failed)
  (assert (= failed-log #("first row")) failed-log)
  (assert (= after-failure 2) after-failure)
  ;; (2) Forbidden effects, nesting and a different database are refused, and rolled back.
  (assert (in "Get" refused-state) refused-state)
  (assert (in "SqlTransaction" refused-nested) refused-nested)
  (assert (in "other" refused-other) refused-other)
  (assert (= after-misuse 2) after-misuse)
  ;; (3) An exception of the program is passed through after rolling back.
  (assert (= #(raised after-raise) #("program error" 2))))


;; --- DDL declaration and how databases are divided -------------------------------------------------------------------------------------------

(defk declared-twice []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that runs the same declaration twice (does nothing if it already exists) and checks that the unique index is effective."
  (<- first (seeded DB))
  (<- second (seeded DB))
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (1, 'same')" #()))
  (<- duplicate-label (SqlQuery DB "INSERT INTO items (id, label) VALUES (2, 'same')" #()))
  #(first second duplicate-label))


(deftest test-sqlite-draws-the-schema-declaration
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (declared-twice)))
  (val first (get answers 0))
  (val second (get answers 1))
  (val duplicate-label (get answers 2))
  (assert (= first.statements
             #("CREATE TABLE IF NOT EXISTS items (id INTEGER NOT NULL, label TEXT NOT NULL, weight REAL, blob BLOB, flag BOOLEAN, doc TEXT, PRIMARY KEY (id))"
               "CREATE UNIQUE INDEX IF NOT EXISTS items_label ON items (label)"))
          first)
  (assert (= first second))
  (assert (and (isinstance duplicate-label SqlFailed) (= duplicate-label.sqlstate "23000")) duplicate-label))


(defk two-databases []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that prepares the same table in 2 databases and writes to only one."
  (<- (seeded "hot"))
  (<- (seeded "cold"))
  (<- (SqlQuery "hot" "INSERT INTO items (id, label) VALUES (1, 'x')" #()))
  #((! (count-items "hot")) (! (count-items "cold"))))


(deftest test-each-handler-answers-only-its-declared-databases
  ;; The inner handler answers only hot, and cold is passed to the outer handler (the form that lines up real PostgreSQL and ClickHouse in the same set).
  (<- counts (with-handler [(state) (sqlite-sql-handler #("cold")) (sqlite-sql-handler #("hot"))] (two-databases)))
  (assert (= counts #(1 0)) counts))


;; --- Rewriting and answer mapping of the real handlers (pure functions) ----------------------------------------------------------------------------------

(deftest test-postgres-rewrites-to-pyformat-and-escapes-percent
  (<- bound (postgres-statement "SELECT '%s :x', x::bigint, :x FROM t WHERE y LIKE 'a%' AND z = :z -- :x"
                                #((SqlParam :name "x" :value 1) (SqlParam :name "z" :value None))))
  (assert (= bound.text "SELECT '%%s :x', x::bigint, %(x)s FROM t WHERE y LIKE 'a%%' AND z = %(z)s -- :x") bound)
  (<- insert (postgres-insert-statement "items" #("id" "label")))
  (assert (= insert "INSERT INTO items (id, label) VALUES (%s, %s)") insert)
  (try
    (<- (postgres-insert-statement "items; DROP TABLE x" #("id")))
    (assert False "A name that needs quoting got through")
    (except [ValueError])))


(deftest test-postgres-errors-map-to-the-two-failure-values
  (assert (= (! (postgres-failure "23505" #("UniqueViolation" "IntegrityError" "DatabaseError" "Error") "dup")) (SqlFailed :sqlstate "23505" :reason "dup")))
  (assert (= (! (postgres-failure None #("DataError" "DatabaseError" "Error") "NUL")) (SqlFailed :sqlstate "22000" :reason "NUL")))
  (assert (= (! (postgres-failure None #("OperationalError" "DatabaseError" "Error") "refused")) (SqlUnreachable :reason "refused")))
  (assert (= (! (postgres-failure None #("InterfaceError" "Error") "closed")) (SqlUnreachable :reason "closed")))
  (assert (= (! (postgres-failure None #("Error") "other")) (SqlFailed :sqlstate None :reason "other"))))


(deftest test-postgres-connection-sqlstates-map-to-unreachable
  ;; Connection exceptions (all of class 08) and 57P01, 57P02, 57P03 (disconnect by administrator, disconnect by crash, starting up / shutting down) are
  ;; unreachable even if they carry a SQLSTATE.
  (for [sqlstate ["08000" "08001" "08003" "08006" "08P01" "57P01" "57P02" "57P03"]]
    (<- answer (postgres-failure sqlstate #("OperationalError" "DatabaseError" "Error") "gone"))
    (assert (= answer (SqlUnreachable :reason "gone")) #(sqlstate answer)))
  ;; Counterexamples: the rest of class 57 (57014 = statement cancellation, 57000) and engine answers that are not connections (23505, 40001, 53300) remain SqlFailed.
  (for [sqlstate ["57014" "57000" "23505" "40001" "53300"]]
    (<- engine-answer (postgres-failure sqlstate #("OperationalError" "DatabaseError" "Error") "engine"))
    (assert (= engine-answer (SqlFailed :sqlstate sqlstate :reason "engine")) #(sqlstate engine-answer))))


;; --- Connection limits ------------------------------------------------------------------------------------------
;; After the DB pod was replaced, a connection without limits kept waiting on a TCP connection with no peer, and the record service froze for 44 minutes.

(deftest test-postgres-connections-carry-the-timeouts
  ;; The default lease puts a limit on connecting, keepalive, the reply to sent bytes, and statements on the connections it opens.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn ""))))
  (assert (= (.connection-options connections DB)
             {"connect_timeout" 5 "keepalives" 1 "keepalives_idle" 10 "keepalives_interval" 5 "keepalives_count" 3
              "tcp_user_timeout" 15000 "options" "-c statement_timeout=60000 -c idle_in_transaction_session_timeout=30000"})
          (.connection-options connections DB))
  ;; With the declaration that puts no statement limit (None), options is not passed (the options of the DSN are not erased).
  (val unbounded (PostgresConnections #((PostgresDatabase :name DB :dsn ""))
                                      :timeouts (PostgresTimeouts :connect-seconds 2 :keepalive-idle-seconds 1 :keepalive-interval-seconds 1
                                                                  :keepalive-count 1 :unacknowledged-milliseconds 900
                                                                  :statement-milliseconds None
                                                                  :idle-transaction-milliseconds None)))
  (assert (not-in "options" (.connection-options unbounded DB)))
  (assert (= (get (.connection-options unbounded DB) "tcp_user_timeout") 900)))


(val PSYCOPG-SKIP (is (importlib.util.find-spec "psycopg") None))


(deftest test-postgres-connect-to-a-silent-server-answers-unreachable
  {:skip-if PSYCOPG-SKIP :skip-reason "psycopg is missing"}
  ;; When opening toward a port that only accepts connections and answers nothing (the same form as during the replacement of the DB pod -- the kernel finishes the TCP handshake, and the PostgreSQL startup answer never comes),
  ;; SqlUnreachable is returned at the limit (the libpq lower bound of 2 seconds). Counterexample = a connection without limits keeps waiting for an answer until the port closes.
  (val silent (socket.create-server #("127.0.0.1" 0)))
  (val port (get (.getsockname silent) 1))
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (.format "host=127.0.0.1 port={} dbname=x user=x" port)))
                                        :timeouts (PostgresTimeouts :connect-seconds 2 :keepalive-idle-seconds 1
                                                                    :keepalive-interval-seconds 1 :keepalive-count 1
                                                                    :unacknowledged-milliseconds 1000 :statement-milliseconds 1000
                                                                    :idle-transaction-milliseconds None)))
  (val started (time.monotonic))
  (try
    (<- answer (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (finally (.close connections) (.close silent)))
  (val elapsed (- (time.monotonic) started))
  (assert (isinstance answer SqlUnreachable) answer)
  (assert (< elapsed 6) elapsed))


(defclass CuttingProxy []
  "A TCP relay placed in front of the real PG (test scaffolding) -- cut closes the running connections on both sides (the form where the port closes midway because of the replacement of the DB pod)."

  (defn #^ None __init__ [self #^ str host #^ int port]  ; defk にできない: initializing the test scaffolding's resources (socket and thread)
    "To open the relay's listener and start, for each accepted connection, a thread that connects to the destination and forwards."
    (setv self.target #(host port)
          self.listener (socket.create-server #("127.0.0.1" 0))
          self.port (get (.getsockname self.listener) 1)
          self.live []
          self.lock (threading.Lock))
    (.start (threading.Thread :target self.accept :daemon True)))

  (defn #^ None accept [self]  ; defk にできない: target of the test scaffolding's thread
    "To connect accepted connections to the destination and forward in both directions (ends when the listener closes)."
    (while True
      (try
        (setv [client _] (.accept self.listener))
        (except [OSError] (return None)))
      (setv upstream (.connect-upstream self))
      (with [self.lock] (.extend self.live [client upstream]))
      (for [[a b] [[client upstream] [upstream client]]]
        (.start (threading.Thread :target self.pipe :args #(a b) :daemon True)))))

  (defn #^ socket.socket connect-upstream [self]  ; defk にできない: opens the test scaffolding's socket
    "To connect to the destination. If host starts with /, connect to the PostgreSQL unix socket dir (the throwaway PostgreSQL does not listen on TCP)."
    (setv #(host port) self.target)
    (if (.startswith host "/")
      (do
        (setv upstream (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
        (.connect upstream (os.path.join host (.format ".s.PGSQL.{}" port)))
        upstream)
      (socket.create-connection self.target)))

  (defn #^ None pipe [self #^ socket.socket a #^ socket.socket b]  ; defk にできない: target of the test scaffolding's thread
    "To forward the bytes read from a to b (ends when either closes)."
    (try
      (while True
        (setv data (.recv a 65536))
        (when (not data) (break))
        (.sendall b data))
      (except [OSError] None)))

  (defn #^ None cut [self]  ; defk にできない: operation of the test scaffolding
    "To close all running connections on both sides (the listener stays open -- the next connection is let through)."
    (with [self.lock]
      (for [s self.live]
        (try (.shutdown s socket.SHUT-RDWR) (except [OSError] None))
        (.close s))
      (.clear self.live)))

  (defn #^ None close [self]  ; defk にできない: cleanup of the test scaffolding
    "To stop the relay."
    (.cut self)
    (.close self.listener)))


(deftest test-postgres-recovers-after-the-connection-is-cut-mid-way
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; Counterexample on a real PG (acceptance of #1479 -- the production DB pod is not replaced): when the connection is closed midway at the relay port, the next statement on that connection gets
  ;; SqlUnreachable, and the statement after that answers on a new connection within 25 seconds (the broken connection is discarded when returned, and the next borrow reconnects).
  (import psycopg.conninfo [conninfo-to-dict make-conninfo])
  (val target (conninfo-to-dict (or POSTGRES-DSN "")))
  (val proxy (CuttingProxy (.get target "host" "127.0.0.1") (int (.get target "port" 5432))))
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (make-conninfo (or POSTGRES-DSN "") :host "127.0.0.1"
                                                                                         :port (str proxy.port))))
                                        :size 1))
  (try
    (<- before (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (.cut proxy)
    (val cut-at (time.monotonic))
    (<- broken (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (<- after (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (val recovered-in (- (time.monotonic) cut-at))
    (finally (.close connections) (.close proxy)))
  (assert (isinstance before SqlRows) before)
  (assert (isinstance broken SqlUnreachable) broken)
  (assert (isinstance after SqlRows) after)
  (assert (< recovered-in 25) recovered-in))


(defk select-one []
  {:pre [] :post [(: % "answer of SqlQuery")] :tags {:context "sql" :role "program"}}
  "To read 1 row as the body of a transaction (only checks whether the lock was taken)."
  (<- answer (SqlQuery DB "SELECT 1" #()))
  answer)


(defk locked-write-after-a-stalled-transaction [idle-milliseconds]
  {:pre [(: idle-milliseconds (| int None))] :post [(: % "answer of SqlTransaction")]
   :tags {:context "sql" :role "program"}}
  "To fire a transaction with the same lock next to a connection stalled midway through a transaction (does nothing after BEGIN and the lock -- the form where it never reached the returning finally).
  idle-milliseconds = the limit for doing nothing midway through a transaction (None = not set). The statement limit is 3 seconds."
  (val timeouts (PostgresTimeouts :connect-seconds 5 :keepalive-idle-seconds 10 :keepalive-interval-seconds 5 :keepalive-count 3
                                  :unacknowledged-milliseconds 15000 :statement-milliseconds 3000
                                  :idle-transaction-milliseconds idle-milliseconds))
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2 :timeouts timeouts))
  (val stalled (.acquire connections DB))
  (.execute stalled "BEGIN")
  (.execute stalled "SELECT pg_advisory_xact_lock(hashtext('stalled-1846'))")
  (try
    (<- answer (with-handler [(postgres-sql-handler connections)]
                 (SqlTransaction DB (select-one) "stalled-1846")))
    answer
    (finally (.release connections DB stalled) (.close connections))))


(deftest test-postgres-cuts-a-stalled-transaction-so-its-lock-is-freed
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; Real PG: a transaction that stalled while holding a lock is cut by the engine at the limit (0.5 seconds), which frees the lock, and a transaction with the same lock
  ;; goes through. With a connection without limits the lock is not freed, and a transaction with the same lock fails with 57014 at the statement limit (3 seconds) (the form of the incident in the landing ledger on 2026-09-30).
  ;;
  (<- freed (locked-write-after-a-stalled-transaction 500))
  (assert (= (get freed.rows 0 0) 1) freed)
  (<- blocked (locked-write-after-a-stalled-transaction None))
  (assert (isinstance blocked SqlFailed) blocked)
  (assert (= blocked.sqlstate "57014") blocked))


(deftest test-postgres-cuts-a-statement-at-the-statement-timeout
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; Real PG: a statement that exceeds the statement limit (0.5 seconds) is cancelled by the engine (57014) and returned as SqlFailed. The connection is returned and the next statement answers.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN "")))
                                        :timeouts (PostgresTimeouts :connect-seconds 5 :keepalive-idle-seconds 10
                                                                    :keepalive-interval-seconds 5 :keepalive-count 3
                                                                    :unacknowledged-milliseconds 15000 :statement-milliseconds 500
                                                                    :idle-transaction-milliseconds None)))
  (try
    (<- slow (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT pg_sleep(3)" #())))
    (<- next (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (finally (.close connections)))
  (assert (= slow.sqlstate "57014") slow)
  (assert (= (get next.rows 0 0) 1) next))


(deftest test-postgres-draws-the-schema-declaration
  (<- statements (postgres-schema-statements #(ITEMS)))
  (assert (= statements
             #("CREATE TABLE IF NOT EXISTS items (id bigint NOT NULL, label text NOT NULL, weight double precision, blob bytea, flag boolean, doc json, PRIMARY KEY (id))"
               "CREATE UNIQUE INDEX IF NOT EXISTS items_label ON items (label)"))
          statements))


(val CLICKHOUSE (ClickHouseDatabase :name "cold" :url "http://127.0.0.1:9/" :database "archive" :user "reader" :password "secret" :deadline 2.0))


(deftest test-clickhouse-rewrites-to-typed-params
  (<- bound (clickhouse-statement "SELECT :i, :s, :n, :b, :f, :y, ':i'"
                                  #((SqlParam :name "i" :value 7) (SqlParam :name "s" :value "a\tb\\c\n") (SqlParam :name "n" :value None)
                                    (SqlParam :name "b" :value True) (SqlParam :name "f" :value 1.5) (SqlParam :name "y" :value b"\x01"))))
  (assert (= bound.text "SELECT {i:Int64}, {s:String}, {n:Nullable(String)}, {b:Bool}, {f:Float64}, {y:String}, ':i'") bound)
  (assert (= bound.params #((ClickHouseParam :name "i" :text b"7") (ClickHouseParam :name "s" :text b"a\\tb\\\\c\\n")
                            (ClickHouseParam :name "n" :text b"\\N") (ClickHouseParam :name "b" :text b"true")
                            (ClickHouseParam :name "f" :text b"1.5") (ClickHouseParam :name "y" :text b"\x01")))
          bound.params)
  (<- request (clickhouse-query-request CLICKHOUSE "SELECT :i" #((SqlParam :name "i" :value 7))))
  (assert (= request.url (+ "http://127.0.0.1:9/?database=archive&default_format=JSONCompactEachRow"
                            "&output_format_json_quote_64bit_integers=0&wait_end_of_query=1&max_execution_time=2"
                            "&timeout_overflow_mode=throw&query_id=" request.query-id "&param_i=7"))
          request.url)
  (assert (= request.body b"SELECT {i:Int64}"))
  (assert (= (lfor h request.headers #(h.name h.value)) [#("X-ClickHouse-User" "reader") #("X-ClickHouse-Key" "secret")]))
  (assert (not-in "secret" (repr CLICKHOUSE)) "The credentials appeared in repr"))


(defk url-settings [url]
  {:pre [(: url str)] :post [(: % list)]
   :tags {:context "sql" :role "program"}}
  "To read the pairs of setting names and values carried in the request URL (tests of the deadline and query_id)."
  (urllib.parse.parse-qsl (. (urllib.parse.urlsplit url) query)))


(deftest test-clickhouse-waits-outlast-the-server-deadline
  ;; Failure case: the declaration held only the client's wait (timeout 30 seconds) and did not send a deadline to the server. In the middle of a slow query,
  ;; the client dropped the connection, and ClickHouse 24.11 crashed at the point of writing the answer to the departed connection. The declaration holds only the deadline, and the wait is derived from the deadline.
  (try
    (ClickHouseDatabase :name "cold" :url "http://127.0.0.1:9/" :database "archive" :user "reader" :password "" :timeout 30.0)
    (assert False "A declaration with only the client's wait could be created")
    (except [TypeError]))
  (for [bad #(0 0.0 -1.0 (float "inf") (float "nan") True)]
    (try
      (ClickHouseDatabase :name "cold" :url "http://127.0.0.1:9/" :database "archive" :user "reader" :password "" :deadline bad)
      (assert False (.format "A declaration with deadline {!r} could be created (0 means no deadline in ClickHouse)" bad))
      (except [ValueError])))
  (for [#(deadline wait) #(#(30.0 30.0) #(30.0 29.0) #(0.0 1.0))]
    (try
      (ClickHouseWaits :deadline deadline :wait wait :grace 1.0)
      (assert False (.format "A pair with wait {} at or below deadline {} could be created" wait deadline))
      (except [ValueError])))
  (<- waits (clickhouse-waits CLICKHOUSE))
  (assert (= waits (ClickHouseWaits :deadline 2.0 :wait (+ 2.0 WAIT-MARGIN-SECONDS) :grace KILL-GRACE-SECONDS)) waits)
  (assert (> waits.wait waits.deadline) waits)
  ;; Every request -- query, insert, and the DDL of a table declaration -- sends the deadline and a query_id that differs for each request.
  (<- query (clickhouse-query-request CLICKHOUSE "SELECT 1" #()))
  (<- again (clickhouse-query-request CLICKHOUSE "SELECT 1" #()))
  (<- insert (clickhouse-insert-request CLICKHOUSE "events" #("id") #(#(1))))
  (for [request #(query again insert)]
    (val settings (dict (! (url-settings request.url))))
    (assert (= (get settings "max_execution_time") "2") settings)
    (assert (= (get settings "timeout_overflow_mode") "throw") settings)
    (assert (= (get settings "query_id") request.query-id) #(settings request.query-id))
    (assert (= request.waits waits) request.waits))
  (assert (!= query.query-id again.query-id) "query_id overlapped between requests")
  (<- kill (clickhouse-kill-request query))
  (assert (= kill.url "http://127.0.0.1:9/") kill.url)
  (assert (= kill.body (.encode (.format "KILL QUERY WHERE query_id = '{}' ASYNC" query.query-id) "ascii")) kill.body)
  (assert (= kill.headers query.headers) kill.headers)
  (try
    (<- (clickhouse-kill-request (replace query :query-id "x' OR 1 = 1 --")))
    (assert False "A query_id outside the expected form was put into the KILL QUERY statement")
    (except [ValueError])))


(deftest test-clickhouse-insert-is-one-json-body
  (<- request (clickhouse-insert-request CLICKHOUSE "events" #("id" "body") #(#(1 "one") #(2 None))))
  (assert (in "query=INSERT+INTO+events+%28id%2C+body%29+FORMAT+JSONCompactEachRow" request.url) request.url)
  (assert (= request.body (.encode "[1, \"one\"]\n[2, null]\n" "utf-8")) request.body)
  (try
    (<- (clickhouse-insert-request CLICKHOUSE "events" #("body") #(#(b"\x00"))))
    (assert False "A bytes value got into the JSON")
    (except [ValueError])))


(deftest test-clickhouse-answers-map-to-rows-and-failures
  (<- rows (clickhouse-rows b"[1,\"a\",null,true,1.5]\n\n[18446744073709551615,\"b\",\"c\",false,0]\n"))
  (assert (= rows #(#(1 "a" None True 1.5) #(18446744073709551615 "b" "c" False 0))) rows)
  (assert (= (! (clickhouse-written-rows "{\"read_rows\":\"0\",\"written_rows\":\"3\"}")) 3))
  (assert (is (! (clickhouse-written-rows None)) None))
  (<- failed (clickhouse-failure (ClickHouseResponse :status 404 :body b"Code: 60. DB::Exception: Unknown table\n" :exception-code "60" :summary None)))
  (assert (= failed (SqlFailed :sqlstate None :reason "Code: 60. DB::Exception: Unknown table")) failed)
  (<- gateway (clickhouse-failure (ClickHouseResponse :status 502 :body b"Bad Gateway" :exception-code None :summary None)))
  (assert (= gateway (SqlUnreachable :reason "HTTP 502: Bad Gateway")) gateway)
  (try
    (<- (clickhouse-rows b"[[1,2]]\n"))
    (assert False "An array column got through beyond the closed set")
    (except [TypeError])))


(deftest test-clickhouse-answers-split-only-at-the-row-newline
  ;; The JSON of ClickHouse emits U+0085 (NEL) as a raw C2 85, and U+2028 and U+2029 can also arrive raw -- the only row separator is \n.
  (<- rows (clickhouse-rows (.encode "[1,\"a\u0085b\"]\n[2,\"c\u2028d\u2029e\"]\n" "utf-8")))
  (assert (= rows #(#(1 "a\u0085b") #(2 "c\u2028d\u2029e"))) rows))

(deftest test-clickhouse-draws-the-schema-and-refuses-what-it-lacks
  (<- statements (clickhouse-schema-statements
                   #((SqlTable :name "events" :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER)
                                                          (SqlColumn :name "body" :type SqlColumnType.JSON :nullable True))
                               :primary-key #("id") :indexes #((SqlIndex :name "events_body" :columns #("body")))))))
  (assert (= statements
             #("CREATE TABLE IF NOT EXISTS events (id Int64, body Nullable(String), INDEX events_body (body) TYPE minmax GRANULARITY 1) ENGINE = MergeTree ORDER BY (id)"))
          statements)
  (<- unique (clickhouse-schema-statements #(ITEMS)))
  (assert (and (isinstance unique SqlFailed) (= unique.sqlstate "0A000")) unique))


(defk clickhouse-without-engine []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that collects the refusal of a transaction and an unreachable HTTP answer (nothing listens on 127.0.0.1:9)."
  (<- refused (SqlTransaction "cold" (count-items "cold")))
  (<- unreachable (SqlQuery "cold" "SELECT 1"))
  #(refused unreachable))


(deftest test-clickhouse-refuses-transactions-and-reports-unreachable
  (<- answers (with-handler [(clickhouse-http-sql-handler #(CLICKHOUSE))] (clickhouse-without-engine)))
  (val refused (get answers 0))
  (val unreachable (get answers 1))
  (assert (and (isinstance refused SqlFailed) (= refused.sqlstate "0A000")) refused)
  (assert (isinstance unreachable SqlUnreachable) unreachable))


(defclass SlowClickHouse [http.server.BaseHTTPRequestHandler]
  "A fake ClickHouse HTTP port that answers a query POST with 1 row [1] after delay seconds (for the test that checks whether the handler blocks the scheduler)."
  (deff do-POST [self]  ; defk にできない: a plain callback called by http.server
    {:pre [(: self http.server.BaseHTTPRequestHandler)] :post [(: % (type None))]}
    "To discard the body and answer one JSONCompactEachRow row after 1 second."
    (.read self.rfile (int (.get self.headers "Content-Length" "0")))
    (time.sleep 1.0)
    (setv body b"[1]\n")
    (.send-response self 200)
    (.send-header self "Content-Length" (str (len body)))
    (.end-headers self)
    (.write self.wfile body)
    None)

  (deff log-message [self format #* args]  ; defk にできない: a plain callback called by http.server
    {:pre [(: self http.server.BaseHTTPRequestHandler) (: format str) (: args tuple)] :post [(: % (type None))]}
    "To keep request lines out of the test output."
    None))


(defk clickhouse-finished-at []
  {:pre [] :post [(: % float)]
   :tags {:context "sql" :role "program"}}
  "To send one query to ClickHouse and answer the monotonic clock at the moment it finished."
  (<- answer SqlRows (SqlQuery "cold" "SELECT 1" #()))
  (time.monotonic))


(defk slow-clickhouse-beside-ticks []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To check whether another task can tick next to a ClickHouse query that takes 1 second (if blocked, the ticks come after the query)."
  (<- slow (Spawn (clickhouse-finished-at)))
  (<- seen (ticks 5 0.05))
  (<- finished (Wait slow))
  #(seen finished))


(deftest test-clickhouse-does-not-block-the-scheduler
  (val server (http.server.ThreadingHTTPServer #("127.0.0.1" 0) SlowClickHouse))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  (try
    (val declared (ClickHouseDatabase :name "cold" :url (.format "http://127.0.0.1:{}" (get server.server-address 1)) :database "default"
                                      :user "default" :password "" :deadline 10.0))
    (<- answer (with-handler [(clickhouse-http-sql-handler #(declared))] (slow-clickhouse-beside-ticks)))
    (finally (.shutdown server) (.server-close server)))
  (val seen (get answer 0))
  (val finished (get answer 1))
  ;; The 5 ticks (0.25 seconds) finish before the 1-second query finishes (if blocked, all of them come after the end).
  (assert (< (get seen -1) finished) #(seen finished)))


(defrecord Received
  "One POST received by the fake ClickHouse (kind = query or kill; query-id / deadline / overflow = the query_id, max_execution_time and
   timeout_overflow_mode of the URL; body = the body; agent = User-Agent; at = the monotonic clock at the moment of receipt)."
  (#^ str kind)
  (#^ (| str None) query-id)
  (#^ (| str None) deadline)
  (#^ (| str None) overflow)
  (#^ bytes body)
  (#^ (| str None) agent)
  (#^ float at))


(defclass FakeClickHouse [http.server.BaseHTTPRequestHandler]
  "A fake ClickHouse HTTP port for checking the deadline and KILL QUERY (the server's fields mode and cancels decide the behavior).
     mode \"deadline\" = a server that keeps the deadline: runs the query a little longer than the max_execution_time seconds of the URL (the server reads the deadline only at
                       processing boundaries), and returns an answer refused at the deadline (500, code 159 TIMEOUT_EXCEEDED).
     mode \"stuck\"    = a stuck server: does not answer a query until KILL QUERY arrives, and when it receives a KILL (if cancels is true) returns a cancellation answer (500, code 394 QUERY_WAS_CANCELLED)
                       for the named query. If cancels is false, the query does not stop even when a KILL is received.
   Turns each received POST into a Received and puts it into the server's received (a queue across threads)."
  (deff do-POST [self]  ; defk にできない: a plain callback called by http.server
    {:pre [(: self http.server.BaseHTTPRequestHandler)] :post [(: % (type None))]}
    "To record the received POST and answer with the behavior of mode."
    (setv settings (dict (urllib.parse.parse-qsl (. (urllib.parse.urlsplit self.path) query)))
          body (.read self.rfile (int (.get self.headers "Content-Length" "0")))
          kind (if (.startswith body b"KILL QUERY") "kill" "query"))
    (.put self.server.received (Received :kind kind :query-id (.get settings "query_id") :deadline (.get settings "max_execution_time")
                                         :overflow (.get settings "timeout_overflow_mode") :body body
                                         :agent (.get self.headers "User-Agent") :at (time.monotonic)))
    (setv failure (cond
                    (= kind "kill") (do (when self.server.cancels (.set self.server.killed)) None)
                    (= self.server.mode "deadline")
                      (do (time.sleep (+ (float (get settings "max_execution_time")) 0.3))
                          #("159" b"Code: 159. DB::Exception: Timeout exceeded: elapsed 1.3 seconds, maximum: 1. (TIMEOUT_EXCEEDED)\n"))
                    (.wait self.server.killed 10.0) #("394" b"Code: 394. DB::Exception: Query was cancelled. (QUERY_WAS_CANCELLED)\n")
                    True None))
    (cond
      (= kind "kill") (do (.send-response self 200) (.send-header self "Content-Length" "0") (.end-headers self))
      (is-not failure None) (do (.send-response self 500)
                                (.send-header self "X-ClickHouse-Exception-Code" (get failure 0))
                                (.send-header self "Content-Length" (str (len (get failure 1))))
                                (.end-headers self)
                                (.write self.wfile (get failure 1))))
    None)

  (deff log-message [self format #* args]  ; defk にできない: a plain callback called by http.server
    {:pre [(: self http.server.BaseHTTPRequestHandler) (: format str) (: args tuple)] :post [(: % (type None))]}
    "To keep request lines out of the test output."
    None))


(defk fake-clickhouse [mode cancels]
  {:pre [(: mode str) (: cancels bool)] :post [(: % http.server.ThreadingHTTPServer)]
   :tags {:context "sql" :role "program"}}
  "To start a fake ClickHouse (FakeClickHouse -- mode and cancels are described in the class docstring)."
  (val server (http.server.ThreadingHTTPServer #("127.0.0.1" 0) FakeClickHouse))
  (setv server.mode mode
        server.cancels cancels
        server.killed (threading.Event)
        server.received (queue.SimpleQueue))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  server)


(defk received-by [server]
  {:pre [(: server http.server.ThreadingHTTPServer)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To read the POSTs received by the fake ClickHouse in the order received."
  (tuple (lfor _ (range (.qsize server.received)) (.get-nowait server.received))))


(defk fake-database [server deadline]
  {:pre [(: server http.server.ThreadingHTTPServer) (: deadline float)] :post [(: % ClickHouseDatabase)]
   :tags {:context "sql" :role "program"}}
  "To create the declaration of the store cold that points to the fake ClickHouse port."
  (ClickHouseDatabase :name "cold" :url (.format "http://127.0.0.1:{}" (get server.server-address 1)) :database "default"
                      :user "default" :password "" :deadline deadline))


(defk ask-cold [statement]
  {:pre [(: statement str)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "program"}}
  "To send one query to the store cold and return the answer (including failure values)."
  (<- answer (SqlQuery "cold" statement #()))
  answer)


(deftest test-clickhouse-waits-for-the-server-to-refuse-at-its-deadline
  ;; Failure case: the client dropped the connection after waiting 30 seconds, and the server crashed at the point of writing the answer to the departed connection. The client sends the deadline
  ;; and waits longer than the deadline, so it receives the answer that the server refused at the deadline as a SqlFailed while keeping the connection (KILL QUERY is not fired).
  (<- server (fake-clickhouse "deadline" False))
  (try
    (<- declared (fake-database server 1.0))
    (<- answer (with-handler [(clickhouse-http-sql-handler #(declared))]
                 (ask-cold "SELECT sleepEachRow(1) FROM numbers(10) SETTINGS max_block_size = 1")))
    (finally (.shutdown server) (.server-close server)))
  (assert (and (isinstance answer SqlFailed) (is answer.sqlstate None) (in "Code: 159" answer.reason) (in "TIMEOUT_EXCEEDED" answer.reason))
          answer)
  (<- received (received-by server))
  (assert (= (lfor r received r.kind) ["query"]) (.format "KILL QUERY was fired at a server that refuses at the deadline: {!r}" received))
  (val asked (get received 0))
  (assert (and (= asked.deadline "1") (= asked.overflow "throw") (is-not asked.query-id None)) asked)
  (assert (= asked.agent USER-AGENT) asked))


(defk stuck-request [server]
  {:pre [(: server http.server.ThreadingHTTPServer)] :post [(: % ClickHouseRequest)]
   :tags {:context "sql" :role "program"}}
  "To create one query to the stuck fake server with a set of waits shrunk to test seconds (wait 0.5 seconds, grace 1 second)."
  (<- declared (fake-database server 0.2))
  (<- request (clickhouse-query-request declared "SELECT 1" #()))
  (replace request :waits (ClickHouseWaits :deadline 0.2 :wait 0.5 :grace 1.0)))


(deftest test-clickhouse-kills-the-query-before-leaving-a-stuck-server
  ;; Failure case: a client that went past its wait left by only closing the socket, and the server's query kept running.
  ;; When the wait is exceeded, fire KILL QUERY on a different connection before closing the original connection, and receive the cancellation answer on the original connection.
  (<- server (fake-clickhouse "stuck" True))
  (try
    (<- request (stuck-request server))
    (<- answer (clickhouse-answer request False))
    (finally (.set server.killed) (.shutdown server) (.server-close server)))
  ;; The cancellation answer (394) is written to the original connection after the KILL -- that it was received proves the original connection was not closed at the time of the KILL.
  (assert (and (isinstance answer SqlFailed) (in "QUERY_WAS_CANCELLED" answer.reason)) answer)
  (<- received (received-by server))
  (assert (= (lfor r received r.kind) ["query" "kill"]) received)
  (val asked (get received 0))
  (val kill (get received 1))
  (assert (= asked.query-id request.query-id) #(asked request.query-id))
  (assert (= kill.body (.encode (.format "KILL QUERY WHERE query_id = '{}' ASYNC" request.query-id) "ascii")) kill)
  ;; The KILL is fired after the wait (0.5 seconds) is exceeded (not within the wait).
  (assert (>= (- kill.at asked.at) 0.45) #(asked kill)))


(deftest test-clickhouse-leaves-unreachable-only-after-the-kill-and-grace
  ;; From a server whose query does not stop even after receiving KILL QUERY, fire the KILL, wait for the answer on the original connection during the grace period, and then leave with SqlUnreachable.
  (<- server (fake-clickhouse "stuck" False))
  (val started (time.monotonic))
  (try
    (<- request (stuck-request server))
    (<- answer (clickhouse-answer request False))
    (finally (.set server.killed) (.shutdown server) (.server-close server)))
  (val elapsed (- (time.monotonic) started))
  (assert (isinstance answer SqlUnreachable) answer)
  (assert (and (in request.query-id answer.reason) (in "KILL QUERY was accepted" answer.reason)) answer.reason)
  (<- received (received-by server))
  (assert (= (lfor r received r.kind) ["query" "kill"]) received)
  ;; It waits for wait 0.5 seconds + grace 1 second (the KILL round trip is fast) -- it does not leave without waiting for the grace.
  (assert (<= 1.4 elapsed 5.0) elapsed))


;; --- Draining at the end of the process ----------------------------------------------------------------------------

;; A child process that ends on SIGTERM (fires a query; the shape is described in the file's header comment).
(val EXIT-CHILD (os.path.join (os.path.dirname (os.path.abspath __file__)) "clickhouse_exit_child.py"))
;; The upper limit in seconds for waiting for the child's import and end (a few seconds for the import; the drain takes at most EXIT-SETTLE-SECONDS + 2 x EXIT-KILL-GRACE-SECONDS).
(val EXIT-CHILD-LIMIT-SECONDS 60.0)


(defrecord Seen
  "One event seen by the fake ClickHouse that watches the end of the process (kind = query, kill, answered = finished writing the answer to the query, closed = saw the close of the query's
   connection; at = the monotonic clock at the moment seen; answered-before-close = when closed, whether the close came after the answer was fully written)."
  (#^ str kind)
  (#^ float at)
  (#^ bool answered-before-close))


(deff client-closed [connection seconds]  ; defk にできない: socket watching called from a plain callback called by http.server
  {:pre [(: connection socket.socket) (: seconds float)] :post [(: % bool)]
   :tags {:context "sql" :role "program"}}
  "To know whether the client closed the query's connection (watched for at most seconds seconds) without reading the byte after the body (a closed connection is readable with 0 bytes)."
  (setv readable (. (select.select [connection] [] [] seconds) [0]))
  (and (bool readable) (= (.recv connection 1 socket.MSG-PEEK) b"")))


(defclass ExitWatchingClickHouse [http.server.BaseHTTPRequestHandler]
  "A fake ClickHouse HTTP port that watches the end of the process (the server's field mode decides the behavior).
     mode \"slow\"  = answers a query with 1 row after 1 second (if the client closes in the meantime, records the close without writing the answer).
     mode \"stuck\" = does not answer a query until KILL QUERY arrives, and when it receives a KILL returns a cancellation answer (500, code 394).
   After the answer it watches for the client's close and records whether the close came after the answer was fully written (answered-before-close) -- before the fix, the close came before the answer was written (the production
   ClickHouse crashed at the point of writing the answer to that closed connection). Turns the events seen into Seen and puts them into the server's seen (a queue across threads)."
  (deff do-POST [self]  ; defk にできない: a plain callback called by http.server
    {:pre [(: self http.server.BaseHTTPRequestHandler)] :post [(: % (type None))]}
    "To record the received POST, answer with the behavior of mode, and record the client's close."
    (setv body (.read self.rfile (int (.get self.headers "Content-Length" "0"))))
    (when (.startswith body b"KILL QUERY")
      (.put self.server.seen (Seen :kind "kill" :at (time.monotonic) :answered-before-close False))
      (.set self.server.killed)
      (.send-response self 200) (.send-header self "Content-Length" "0") (.end-headers self)
      (return None))
    (.put self.server.seen (Seen :kind "query" :at (time.monotonic) :answered-before-close False))
    (setv until (+ (time.monotonic) (if (= self.server.mode "slow") 1.0 EXIT-CHILD-LIMIT-SECONDS)))
    (while (and (< (time.monotonic) until) (not (.is-set self.server.killed)))
      (when (client-closed self.connection 0.05)
        (.put self.server.seen (Seen :kind "closed" :at (time.monotonic) :answered-before-close False))
        (return None)))
    (if (= self.server.mode "slow")
        (do (setv answer b"[1]\n")
            (.send-response self 200))
        (do (setv answer b"Code: 394. DB::Exception: Query was cancelled. (QUERY_WAS_CANCELLED)\n")
            (.send-response self 500)
            (.send-header self "X-ClickHouse-Exception-Code" "394")))
    (.send-header self "Content-Length" (str (len answer)))
    (.end-headers self)
    (.write self.wfile answer)
    (.flush self.wfile)
    (.put self.server.seen (Seen :kind "answered" :at (time.monotonic) :answered-before-close False))
    (when (client-closed self.connection EXIT-CHILD-LIMIT-SECONDS)
      (.put self.server.seen (Seen :kind "closed" :at (time.monotonic) :answered-before-close True)))
    (setv self.close-connection True)
    None)

  (deff log-message [self format #* args]  ; defk にできない: a plain callback called by http.server
    {:pre [(: self http.server.BaseHTTPRequestHandler) (: format str) (: args tuple)] :post [(: % (type None))]}
    "To keep request lines out of the test output."
    None))


(defrecord ExitWatch
  "One observation of ending a child process with SIGTERM (seen = the events the fake server saw, in the order seen; signalled = the moment SIGTERM was sent; code = the child's
   exit code; stderr = the child's stderr)."
  (#^ tuple seen)
  (#^ float signalled)
  (#^ int code)
  (#^ str stderr))


(defk terminate-mid-round-trip [mode shape]
  {:pre [(: mode str) (: shape str)] :post [(: % ExitWatch)]
   :tags {:context "sql" :role "program"}}
  "To start a child process (shape) that queries the fake ClickHouse (mode), send SIGTERM as soon as the server receives the query, and collect the events the
   server saw until the child ended."
  (val server (http.server.ThreadingHTTPServer #("127.0.0.1" 0) ExitWatchingClickHouse))
  (setv server.mode mode
        server.killed (threading.Event)
        server.seen (queue.SimpleQueue))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  (val child (subprocess.Popen [sys.executable EXIT-CHILD (.format "http://127.0.0.1:{}" (get server.server-address 1)) shape]
                               :stderr subprocess.PIPE :text True))
  (try
    (val first (.get server.seen :timeout EXIT-CHILD-LIMIT-SECONDS))
    (assert (= first.kind "query") first)
    (.send-signal child signal.SIGTERM)
    (val signalled (time.monotonic))
    (val stderr (get (.communicate child :timeout EXIT-CHILD-LIMIT-SECONDS) 1))
    ;; Wait for the events the server writes after the child ends (the close after the answer).
    (time.sleep 0.3)
    (ExitWatch :seen (+ #(first) (tuple (lfor _ (range (.qsize server.seen)) (.get-nowait server.seen))))
               :signalled signalled :code child.returncode :stderr stderr)
    (finally
      (when (is (.poll child) None) (.kill child) (.wait child))
      (.set server.killed)
      (.shutdown server)
      (.server-close server))))


(deftest test-clickhouse-reads-the-answer-before-a-stopped-service-ends
  ;; Failure case (10-24 08:23 JST): when the stop signal ended the service's Program and Python exited, the daemon round-trip
  ;; thread vanished without being waited for, and the server crashed writing the answer to a connection the OS had closed. The drain at the end finishes only after reading the answer.
  (<- watch (terminate-mid-round-trip "slow" "stop"))
  (assert (= watch.code 0) watch)
  (assert (= (lfor s watch.seen s.kind) ["query" "answered" "closed"]) watch)
  (val answered (get watch.seen 1))
  (assert (. (get watch.seen 2) answered-before-close) watch)
  ;; The answer was written after the SIGTERM (at the time of the signal the round trip was still running -- the drain made the process wait for its end).
  (assert (> answered.at watch.signalled) watch))


(deftest test-clickhouse-reads-the-answer-before-a-default-sigterm-ends-the-process
  ;; A process that does not install a SIGTERM receiver (the default SIGTERM ends immediately without even running atexit) also ends with the same SIGTERM after finishing reading the round trip.
  (<- watch (terminate-mid-round-trip "slow" "plain"))
  (assert (= watch.code (- signal.SIGTERM)) watch)
  (assert (= (lfor s watch.seen s.kind) ["query" "answered" "closed"]) watch)
  (assert (. (get watch.seen 2) answered-before-close) watch))


(deftest test-clickhouse-kills-a-stuck-query-before-a-stopped-service-ends
  ;; A round trip that does not finish within the drain seconds (EXIT-SETTLE-SECONDS) fires KILL QUERY before closing the connection, and leaves after reading the cancellation answer
  ;; (it does not wait for the 60-second deadline -- it ends within the grace from the stop signal to the KILL).
  (<- watch (terminate-mid-round-trip "stuck" "stop"))
  (assert (= watch.code 0) watch)
  (assert (= (lfor s watch.seen s.kind) ["query" "kill" "answered" "closed"]) watch)
  (val kill (get watch.seen 1))
  (val closed (get watch.seen 3))
  (assert closed.answered-before-close watch)
  ;; The KILL is fired after waiting the drain seconds, and the child ends within the grace (8.5 seconds of the shim).
  (assert (>= (- kill.at watch.signalled) (- EXIT-SETTLE-SECONDS 0.5)) watch)
  (assert (< (- closed.at watch.signalled) (+ EXIT-SETTLE-SECONDS EXIT-KILL-GRACE-SECONDS EXIT-KILL-GRACE-SECONDS 1.0)) watch))


(deftest test-clickhouse-round-trips-refuse-to-start-once-settling-began
  ;; A round trip that starts after the drain began is not put on the ledger (the process ends leaving that round trip behind, so it does not even open a connection).
  (val trips (RoundTrips))
  (<- request (clickhouse-query-request (ClickHouseDatabase :name "cold" :url "http://127.0.0.1:9" :database "default" :user "default"
                                                            :password "") "SELECT 1" #()))
  (val done (.enter trips request))
  (assert (= (.close trips) #(#(request done))))
  (assert (is (.enter trips request) None))
  (.leave trips request done)
  (assert (.is-set done)))


;; --- Integration with a real DB (skipped if the environment variables are not set) --------------------------------------------------------------------------------

(defk fresh [database]
  {:pre [(: database str)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "To drop and recreate the test table on the real DB (so no rows from a previous run remain)."
  (<- dropped SqlRows (SqlQuery database "DROP TABLE IF EXISTS items" #()))
  dropped)


(defk postgres-journey []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "Runs the same scenario as sqlite on real PostgreSQL."
  (<- (fresh DB))
  (<- trip (round-trip DB))
  (<- (fresh DB))
  (<- promises (transactions DB))
  #(trip promises))


(deftest test-postgres-answers-like-the-sqlite-handler
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2))
  (try
    (<- real (with-handler [(postgres-sql-handler connections)] (postgres-journey)))
    (finally (.close connections)))
  (<- memory (with-handler [(state) (sqlite-sql-handler #(DB))] (postgres-journey)))
  (val real-trip (get real 0))
  (val real-promises (get real 1))
  (val memory-trip (get memory 0))
  (val memory-promises (get memory 1))
  (assert (= (cut real-trip 1 None) (cut memory-trip 1 None)) #(real-trip memory-trip))
  ;; The real one has finer SQLSTATEs (23505), so compare by the 2-character class.
  (val class-of (fn [answer] (if (isinstance answer SqlFailed) (cut (or answer.sqlstate "") 0 2) answer)))
  (assert (= (tuple (map class-of real-promises)) (tuple (map class-of memory-promises))) #(real-promises memory-promises)))


(defk clickhouse-journey []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that declares a table on real ClickHouse, inserts into it, and reads with arguments."
  (<- (SqlQuery "cold" "DROP TABLE IF EXISTS events" #()))
  (<- (SqlEnsureTables "cold" #((SqlTable :name "events" :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER)
                                                                    (SqlColumn :name "body" :type SqlColumnType.TEXT :nullable True)
                                                                    (SqlColumn :name "ok" :type SqlColumnType.BOOLEAN))
                                          :primary-key #("id")))))
  (<- inserted (SqlInsertRows "cold" "events" #("id" "body" "ok") #(#(1 "a\tb" True) #(2 None False))))
  (<- read (SqlQuery "cold" "SELECT id, body, ok FROM events WHERE id >= :low AND body IS NULL OR body = :body ORDER BY id"
                     #((SqlParam :name "low" :value 2) (SqlParam :name "body" :value "a\tb"))))
  (<- broken (SqlQuery "cold" "SELECT * FROM no_such_table" #()))
  #(inserted read broken))


(deftest test-clickhouse-answers-over-http
  {:skip-if (is CLICKHOUSE-URL None)
   :skip-reason "DOEFF_SQL_TEST_CLICKHOUSE_URL is not set"}
  (val declared (ClickHouseDatabase :name "cold" :url (or CLICKHOUSE-URL "") :database (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_DATABASE" "default")
                                    :user (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_USER" "default")
                                    :password (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_PASSWORD" "") :deadline 10.0))
  (<- answers (with-handler [(clickhouse-http-sql-handler #(declared))] (clickhouse-journey)))
  (val inserted (get answers 0))
  (val read (get answers 1))
  (val broken (get answers 2))
  (assert (= inserted (SqlRows :rows #() :rowcount 2)) inserted)
  (assert (= read (SqlRows :rows #(#(1 "a\tb" True) #(2 None False)) :rowcount None)) read)
  (assert (and (isinstance broken SqlFailed) (in "no_such_table" broken.reason)) broken))


(deftest test-clickhouse-server-refuses-at-the-deadline-over-http
  {:skip-if (is CLICKHOUSE-URL None)
   :skip-reason "DOEFF_SQL_TEST_CLICKHOUSE_URL is not set"}
  ;; Real ClickHouse refuses a 20-second query at the deadline that was sent (max_execution_time 1 second), and the client receives that answer as a SqlFailed
  ;; (wait = deadline + margin -- the client does not leave first).
  (val declared (ClickHouseDatabase :name "cold" :url (or CLICKHOUSE-URL "") :database (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_DATABASE" "default")
                                    :user (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_USER" "default")
                                    :password (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_PASSWORD" "") :deadline 1.0))
  (val started (time.monotonic))
  (<- answer (with-handler [(clickhouse-http-sql-handler #(declared))]
               (ask-cold "SELECT sleepEachRow(0.5) FROM numbers(40) SETTINGS max_block_size = 1")))
  (val elapsed (- (time.monotonic) started))
  (assert (and (isinstance answer SqlFailed) (in "TIMEOUT_EXCEEDED" answer.reason)) answer)
  (assert (< elapsed (+ 1.0 WAIT-MARGIN-SECONDS)) elapsed))


;; --- PostgreSQL handler that does not block the scheduler (pooled-postgres-sql-handler). Needs a real PG (skipped if the environment variables are not set) ---

(defk pause [seconds]
  {:pre [(: seconds float)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To wait seconds by the wall clock (a promise completed from outside -- only this task waits)."
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer seconds (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  None)


(defk finished-at [statement]
  {:pre [(: statement str)] :post [(: % float)]
   :tags {:context "sql" :role "program"}}
  "To run one statement and answer the monotonic clock at the moment it finished."
  (<- answer SqlRows (SqlQuery DB statement #()))
  (time.monotonic))


(defk ticks [count seconds]
  {:pre [(: count int) (: seconds float)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To tick count times every seconds and answer the monotonic clock at each tick (it is late if the scheduler is blocked)."
  (var seen #())
  (for [_ (range count)]
    (<- (pause seconds))
    (:= seen (+ seen #((time.monotonic)))))
  seen)


(defk slow-beside-ticks []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To check whether another task can tick next to 2 slow queries that compete for the permit (1 connection). Both sleep 1 second, so whichever takes the permit
   first, the later one waits until the earlier one returns the permit, and the end times are 1 second apart (if they ran side by side on 2 connections, they would end at about the same time). The order
   in which the permit is taken depends on how the driver threads wake up and is not fixed -- do not assume in the assertion that the one spawned first takes it first."
  (<- slow (Spawn (finished-at "SELECT pg_sleep(1.0)")))
  (<- queued (Spawn (finished-at "SELECT pg_sleep(1.0)")))
  (<- seen (ticks 5 0.05))
  (<- finished (Gather slow queued))
  #(seen (tuple finished)))


(defk advisory-lock-held []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "To read, from pg_locks, the number (bigint key) of the advisory lock held by this connection inside a transaction."
  (<- held SqlRows (SqlQuery DB (+ "SELECT ((classid::bigint << 32) | objid::bigint) FROM pg_locks "
                                   "WHERE locktype = 'advisory' AND pid = pg_backend_pid()") #()))
  (assert (= (len held.rows) 1) held.rows)
  (get held.rows 0 0))


(defk lock-numbers [key]
  {:pre [(: key str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To line up the lock number taken by a transaction with a lock-key and the value of the new way of writing, SELECT hashtext(:k)."
  (<- held (SqlTransaction :database DB :program (advisory-lock-held) :lock-key key))
  (<- hashed SqlRows (SqlQuery DB "SELECT hashtext(:k)" #((SqlParam :name "k" :value key))))
  #(held (get hashed.rows 0 0)))


(defk insert-then-sleep []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To insert 1 row inside a transaction and then sleep for a long time (it is cancelled during the sleep)."
  (<- (SqlQuery DB "INSERT INTO pooled_cancel (id) VALUES (1)" #()))
  (<- (SqlQuery DB "SELECT pg_sleep(1.5)" #()))
  None)


(defk cancel-mid-transaction []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To cancel a task midway through a transaction and check whether the cancellation arrives, whether the inserted row does not remain, and whether the permit is returned so that the next query goes through."
  (<- (SqlQuery DB "DROP TABLE IF EXISTS pooled_cancel" #()))
  (<- (SqlQuery DB "CREATE TABLE pooled_cancel (id bigint)" #()))
  (<- task (Spawn (SqlTransaction :database DB :program (insert-then-sleep) :lock-key "pooled-cancel")))
  (<- (pause 0.5))
  (<- (Cancel task))
  (var cancelled False)
  (try
    (<- (Wait task))
    (except [TaskCancelledError] (:= cancelled True)))
  (<- counted SqlRows (SqlQuery DB "SELECT count(*) FROM pooled_cancel" #()))
  #(cancelled (get counted.rows 0 0)))



(deftest test-pooled-postgres-answers-like-the-sqlite-handler
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (<- real (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (postgres-journey)))
    (finally (.close connections) (.shutdown pool)))
  (<- memory (with-handler [(state) (sqlite-sql-handler #(DB))] (postgres-journey)))
  (assert (= (cut (get real 0) 1 None) (cut (get memory 0) 1 None)) #(real memory))
  (val class-of (fn [answer] (if (isinstance answer SqlFailed) (cut (or answer.sqlstate "") 0 2) answer)))
  (assert (= (tuple (map class-of (get real 1))) (tuple (map class-of (get memory 1)))) #(real memory)))


(deftest test-pooled-postgres-does-not-block-the-scheduler
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 1 connection: the slow query holds the permit and the second one waits for it. Even so, the ticks of another task are not delayed.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (val pool (ThreadPoolExecutor :max-workers 1))
  (try
    (<- answer (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (slow-beside-ticks)))
    (finally (.close connections) (.shutdown pool)))
  (val seen (get answer 0))
  (val first-done (min (get answer 1)))
  (val second-done (max (get answer 1)))
  (assert (= (len seen) 5))
  ;; All the ticks finish before the query that took the permit first (1 second) finishes.
  (assert (< (max seen) first-done) #(seen first-done))
  ;; The later one waited until the permit (1 connection) was returned (if they ran side by side, they would end at about the same time).
  (assert (>= (- second-done first-done) 0.9) #(first-done second-done)))


(deftest test-pooled-postgres-takes-the-same-advisory-lock-as-before
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  (import psycopg)
  (val keys #("records-writer" "agora-records-writer" "records-migrate" "conversation-01J0000000000000000000000"))
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (var numbers #())
  (try
    (for [key keys]
      (<- pair (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (lock-numbers key)))
      (:= numbers (+ numbers #(pair))))
    (finally (.close connections) (.shutdown pool)))
  ;; Read the hashtext of the same keys with the old way of writing (lock-statement in pg_sql.hy of doeff-records -- binds the key to a positional %s).
  (val old (with [connection (psycopg.connect (or POSTGRES-DSN "") :autocommit True)]
             (tuple (gfor key keys (get (.fetchone (.execute connection "SELECT hashtext(%s)" #(key))) 0)))))
  (for [[key pair before] (zip keys numbers old)]
    ;; The lock number taken by the transaction = hashtext(:k) of the new way = hashtext(%s) of the old way.
    (assert (= (get pair 0) (get pair 1) before) #(key pair before))))


(deftest test-pooled-postgres-rolls-back-and-returns-the-connection-on-cancel
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  (import psycopg)
  ;; 1 connection: if a query after the cancellation goes through, the permit and the connection have been returned.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (val pool (ThreadPoolExecutor :max-workers 1))
  (try
    (<- answer (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (cancel-mid-transaction)))
    (val idle (list (. (get connections.idle DB) queue)))
    (assert (= (len idle) 1) idle)
    (assert (= (. (get idle 0) info transaction-status) psycopg.pq.TransactionStatus.IDLE))
    (finally (.close connections) (.shutdown pool)))
  ;; The cancellation arrives, and the row inserted inside the transaction does not remain because of the rollback.
  (assert (= answer #(True 0)) answer))


(defk slow-read-then-commit []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "A batched transaction program that reads while sleeping 1 second in the first round trip and inserts 1 row in the commit bundle of the second round trip (for the test that cancels the waiter during the first round trip)."
  (<- (SqlQuery DB "SELECT pg_sleep(1.0)" #()))
  (<- answers (SqlBatch DB #((SqlQuery DB "INSERT INTO pooled_cancel (id) VALUES (1)" #())) :commit True))
  (len answers))


(defk cancel-mid-batched-transaction []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To cancel a task during the first round trip of a batched transaction and check whether the cancellation arrives, whether the second round trip (the row insert and COMMIT) is not sent, and whether the permit is returned so that the next
   query goes through."
  (<- (SqlQuery DB "DROP TABLE IF EXISTS pooled_cancel" #()))
  (<- (SqlQuery DB "CREATE TABLE pooled_cancel (id bigint)" #()))
  (<- task (Spawn (SqlTransaction :database DB :program (slow-read-then-commit) :lock-key "pooled-cancel" :batched True)))
  (<- (pause 0.5))
  (<- (Cancel task))
  (var cancelled False)
  (try
    (<- (Wait task))
    (except [TaskCancelledError] (:= cancelled True)))
  (<- counted SqlRows (SqlQuery DB "SELECT count(*) FROM pooled_cancel" #()))
  #(cancelled (get counted.rows 0 0)))


(deftest test-a-batched-transaction-cancelled-in-its-first-round-trip-sends-no-second
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  (import psycopg)
  ;; A batched transaction does not return to the scheduler between round trips and runs as one job of the driver thread. Even so, if the waiter leaves during the first round trip,
  ;; the second round trip (the row write and COMMIT) is not sent but ROLLBACK runs, and the connection is returned in the form outside a transaction (the same as when it returned on every round trip).
  ;; 1 connection: if a query after the cancellation goes through, the permit and the connection have been returned.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (<- answer (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (cancel-mid-batched-transaction)))
    (val idle (list (. (get connections.idle DB) queue)))
    (assert (= (len idle) 1) idle)
    (assert (= (. (get idle 0) info transaction-status) psycopg.pq.TransactionStatus.IDLE))
    (finally (.close connections) (.shutdown pool)))
  (assert (= answer #(True 0)) answer))


(defk abandon-mid-transaction []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "To leave a task midway through a transaction without either Cancel or Wait and finish the parent program (triggers the cleanup at the end of the run -- #2684)."
  (<- (SqlQuery DB "DROP TABLE IF EXISTS pooled_abandon" #()))
  (<- (SqlQuery DB "CREATE TABLE pooled_abandon (id bigint)" #()))
  (<- (Spawn (SqlTransaction :database DB :program (insert-into-abandon-then-sleep) :lock-key "pooled-abandon")))
  (<- (pause 0.5))
  "left")


(defk insert-into-abandon-then-sleep []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To insert 1 row inside a transaction and then sleep for a long time (the parent run ends during the sleep)."
  (<- (SqlQuery DB "INSERT INTO pooled_abandon (id) VALUES (1)" #()))
  (<- (SqlQuery DB "SELECT pg_sleep(1.5)" #()))
  None)


(defn #^ (get tuple #(str PostgresConnections)) abandoned-in-its-own-run [#^ (get Callable #([PostgresConnections] list)) answerer-of]  ; defk にできない: the test's entry point that runs the parent run to completion in another thread (the parent does not end inside this test's own run)
  "To run one run to completion that ends leaving a task midway through a transaction, and return the connection lease afterwards. answerer-of = connection lease -> set of handlers."
  (import warnings)
  (import doeff [run with_handlers])
  (import doeff_core_effects [scheduled])
  (setv connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (defn #^ str ask []  ; defk にできない: the body of a run that is run to completion in another thread
    (with [_ (warnings.catch-warnings)]
      (warnings.simplefilter "ignore")
      (run (scheduled (with_handlers (answerer-of connections) (abandon-mid-transaction))))))
  (with [runner (ThreadPoolExecutor :max-workers 1)]
    (setv answer (.result (.submit runner ask) :timeout 20)))
  #(answer connections))


(deftest test-postgres-returns-the-connection-of-a-transaction-the-run-left-behind
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; Root cause (#2684): when the run's parent ends and drops the task midway through a transaction, the finally that returns the connection did not run, and
  ;; the connection stayed borrowed and stayed midway through the transaction (free lease slots 0, free permits 0). Now a Cancel arrives at the end of the run, and
  ;; the finally rolls back and returns the connection.
  (import psycopg)
  (val pool (ThreadPoolExecutor :max-workers 2))
  (for [answerer-of [(fn [connections] [(state) (postgres-sql-handler connections)])
                     (fn [connections] [(state) (pooled-postgres-sql-handler connections pool)])]]
    (val outcome (abandoned-in-its-own-run answerer-of))
    (val answer (get outcome 0))
    (val connections (get outcome 1))
    (try
      (assert (= answer "left") answer)
      (val idle (list (. (get connections.idle DB) queue)))
      (assert (= (len idle) 1) f"The connection was not returned (free lease slots {(len idle)})")
      (assert (= (. (get idle 0) info transaction-status) psycopg.pq.TransactionStatus.IDLE))
      (with [admin (psycopg.connect (or POSTGRES-DSN "") :autocommit True)]
        (assert (= (get (.fetchone (.execute admin "SELECT count(*) FROM pooled_abandon")) 0) 0) "A row inserted inside the transaction remained"))
      (finally (.close connections))))
  (.shutdown pool))


(defn #^ (| SqlRows SqlFailed SqlUnreachable) answer-while-terminated [#^ (get Callable #([PostgresConnections] list)) answerer-of]  ; defk にできない: the test's entry point that runs the handler in a run on another thread and cuts the connection from this thread
  "To read the answer when an administrator cut the connection midway through a long query (pg_terminate_backend -- SQLSTATE 57P01).
   answerer-of = connection lease -> set of handlers (a list)."
  (import psycopg)
  (import uuid)
  (import doeff [run with_handlers])
  (import doeff_core_effects [scheduled])
  (setv marker (.format "doeff_terminate_{}" (. (uuid.uuid4) hex))
        connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1)
        answers [])
  (defn #^ None ask []  ; defk にできない: target of a thread
    (.append answers (run (scheduled (with_handlers (answerer-of connections)
                                                    (SqlQuery DB (.format "SELECT pg_sleep(30) /* {} */" marker) #()))))))
  (setv worker (threading.Thread :target ask))
  (.start worker)
  (with [admin (psycopg.connect (or POSTGRES-DSN "") :autocommit True)]
    (setv terminated False deadline (+ (time.monotonic) 10))
    (while (and (not terminated) (< (time.monotonic) deadline))
      (setv terminated (get (.fetchone (.execute admin "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity
                                                        WHERE query LIKE %s AND pid <> pg_backend_pid()" #((.format "%{}%" marker)))) 0))
      (time.sleep 0.05)))
  (.join worker 20)
  (.close connections)
  (get answers 0))


(deftest test-postgres-reads-an-administrator-disconnect-as-unreachable
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; Counterexample on a real PG: the answer when the connection is cut by an administrator (57P01) midway through a query is SqlUnreachable for both the synchronous handler and the non-blocking handler
  ;; (before the ruling of #880 it was SqlFailed (57P01)).
  (val sync-answer (answer-while-terminated (fn [connections] [(postgres-sql-handler connections)])))
  (assert (isinstance sync-answer SqlUnreachable) sync-answer)
  (val pool (ThreadPoolExecutor :max-workers 1))
  (try
    (val pooled-answer (answer-while-terminated (fn [connections] [(state) (pooled-postgres-sql-handler connections pool)])))
    (finally (.shutdown pool)))
  (assert (isinstance pooled-answer SqlUnreachable) pooled-answer))


;; --- postgres-sql-handler also does not block the scheduler. Needs a real PG (skipped if the environment variables are not set) -------------------------
;; Before, the driver call was fired synchronously on the scheduler thread, so while a slow statement ran, the other tasks of the same run (/healthz of the artifact store) waited too.

(defk timed-sleep [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To read, inside a transaction, the start and end times after taking the lock (epoch seconds of PG's clock_timestamp) with a sleep in between."
  (<- began SqlRows (SqlQuery DB "SELECT extract(epoch FROM clock_timestamp())::float8" #()))
  (<- (SqlQuery DB "SELECT pg_sleep(:s)" #((SqlParam :name "s" :value seconds))))
  (<- ended SqlRows (SqlQuery DB "SELECT extract(epoch FROM clock_timestamp())::float8" #()))
  #((get began.rows 0 0) (get ended.rows 0 0)))


(defk locked-pair [first-key second-key]
  {:pre [(: first-key str) (: second-key str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To run 2 transactions with keys side by side and check whether another task can tick next to them (each transaction sleeps 0.4 seconds inside the lock)."
  (val started (time.monotonic))
  (<- first (Spawn (SqlTransaction :database DB :program (timed-sleep 0.4) :lock-key first-key)))
  (<- second (Spawn (SqlTransaction :database DB :program (timed-sleep 0.4) :lock-key second-key)))
  (<- seen (ticks 4 0.05))
  (<- spans (Gather first second))
  #((tuple (gfor t seen (- t started))) (tuple spans)))


(deftest test-postgres-does-not-block-the-scheduler
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 1 connection: a slow query (pg_sleep 1 second) holds the connection and the second one waits for a connection. Even so, the ticks of another task are not delayed. The handler is
  ;; just one postgres-sql-handler (no state or pool layered on -- the assembly side does not change).
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (try
    (<- answer (with-handler [(postgres-sql-handler connections)] (slow-beside-ticks)))
    (finally (.close connections)))
  (val seen (get answer 0))
  (val first-done (min (get answer 1)))
  (val second-done (max (get answer 1)))
  (assert (= (len seen) 5))
  ;; All the ticks finish before the query that took the connection first (1 second) finishes (with a blocking handler the ticks would come after the slow query).
  (assert (< (max seen) first-done) #(seen first-done))
  ;; The later one waited until the connection (1 connection) was returned (if they ran side by side, they would end at about the same time).
  (assert (>= (- second-done first-done) 0.9) #(first-done second-done)))


(deftest test-postgres-serializes-only-transactions-with-the-same-lock-key
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 2 connections: 2 transactions with the same key are serialized by the lock (their time spans do not overlap); with different keys they run side by side (the spans overlap -- the serialization is due to the lock,
  ;; not to a blocked scheduler). During both, the ticks of another task are not delayed.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2))
  (try
    (<- same (with-handler [(postgres-sql-handler connections)] (locked-pair "doeff-1215-lock" "doeff-1215-lock")))
    (<- apart (with-handler [(postgres-sql-handler connections)] (locked-pair "doeff-1215-a" "doeff-1215-b")))
    (finally (.close connections)))
  (val overlap (fn [spans] (- (min (get spans 0 1) (get spans 1 1)) (max (get spans 0 0) (get spans 1 0)))))
  (assert (<= (overlap (get same 1)) 0) same)
  (assert (> (overlap (get apart 1)) 0.2) apart)
  ;; The ticks (4 times x 0.05 seconds) finish within 0.6 seconds of the start. The 2 with the same key are serialized and take 0.8 seconds or more in total, so with a blocking handler the ticks would come
  ;; after that.
  (for [result #(same apart)]
    (assert (< (max (get result 0)) 0.6) result)))


(deftest test-postgres-connections-keep-the-dsn-options-and-add-the-statement-bound
  {:skip-if PSYCOPG-SKIP :skip-reason "psycopg is missing"}
  ;; If the DSN declares options (-c search_path ...), the -c for the statement limit is appended to it (if it won by keyword, the -c of the DSN would silently vanish, and
  ;; the table was created in public instead of the named schema). A database that declares none gets only the statement limit.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn "postgresql://u@/d?host=%2Ftmp&options=-c%20search_path%3Ds1")
                                          (PostgresDatabase :name "plain" :dsn "postgresql://u@/d"))))
  (assert (= (get (.connection-options connections DB) "options") "-c search_path=s1 -c statement_timeout=60000 -c idle_in_transaction_session_timeout=30000")
          (.connection-options connections DB))
  (assert (= (get (.connection-options connections "plain") "options") "-c statement_timeout=60000 -c idle_in_transaction_session_timeout=30000")
          (.connection-options connections "plain")))


;; --- Batching round trips (SqlBatch, BEGIN and COMMIT riding along) --------------------------------------------------------------

(defk recorded-flush [flush]
  {:pre [(: flush TransactionFlush)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "One round trip of the fake handler: remembers what was sent in the state flushes and returns an answer of 0 rows for each of the requests (for testing how round trips are counted -- uses no DB)."
  (<- seen (Get "flushes"))
  (<- (Put "flushes" (+ seen #(flush))))
  (tuple (gfor _ flush.requests (SqlRows :rows #() :rowcount 0))))


(defk recorded-rollback []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "ROLLBACK of the fake handler: increments the state rollbacks by 1."
  (<- seen (Get "rollbacks"))
  (<- (Put "rollbacks" (+ seen 1)))
  None)


(defk recorded-transaction [program]
  {:pre [(: program Program)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To run program in a transaction of the fake handler (it accepts signals -- the same as the PostgreSQL handler) and return #(answer, tuple of round trips, number of ROLLBACKs)."
  (<- (Put "flushes" #()))
  (<- (Put "rollbacks" 0))
  (<- answer (run-in-batched-transaction DB program (fn [flush] (recorded-flush flush)) (fn [] (recorded-rollback)) :accepts-notices True))
  (<- flushes (Get "flushes"))
  (<- rollbacks (Get "rollbacks"))
  #(answer flushes rollbacks))


(defk computed-only []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "A program that emits no SQL at all (pure computation only)."
  (+ 1 2))


(defk one-query []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that runs one statement and ends without emitting a commit bundle."
  (<- (SqlQuery DB "SELECT 1" #()))
  "read")


(defk notified-batch []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "A program that emits a signal and ends with a commit bundle of 2 statements (answer = the number of answers of the bundle)."
  (<- (SqlNotify DB "changes" #("table:parts")))
  (<- answers (SqlBatch DB #((SqlQuery DB "SELECT 1" #()) (SqlQuery DB "SELECT 2" #())) :commit True))
  (len answers))


(defk notified-only []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that emits only a signal."
  (<- (SqlNotify DB "changes" #("table:parts")))
  "signal only")


(defk empty-commit []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that emits only a commit bundle without statements (there is nothing to send)."
  (<- answers (SqlBatch DB #() :commit True))
  (assert (= answers #()) answers)
  "empty bundle")


(defk round-trip-shapes []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To fire the scenarios of round trip shapes in order with the fake handler."
  (<- pure (recorded-transaction (computed-only)))
  (<- single (recorded-transaction (one-query)))
  (<- batched (recorded-transaction (notified-batch)))
  (<- signal (recorded-transaction (notified-only)))
  (<- empty (recorded-transaction (empty-commit)))
  #(pure single batched signal empty))


(deftest test-a-transaction-shares-round-trips-with-begin-and-commit
  ;; Failure case (#3605): a program that emits no SQL takes 0 round trips (neither BEGIN nor COMMIT is sent). BEGIN and the lock take the same round trip as the first statement, and the commit bundle and the remembered
  ;; signal take the same round trip as COMMIT.
  (<- shapes (with-handler [(state)] (round-trip-shapes)))
  (val pure (get shapes 0))
  (val single (get shapes 1))
  (val batched (get shapes 2))
  (val signal (get shapes 3))
  (val empty (get shapes 4))
  (assert (= pure #(3 #() 0)) pure)
  (assert (= (get single 0) "read") single)
  (assert (= (get single 1) #((TransactionFlush :opening True :requests #((SqlQuery DB "SELECT 1" #())) :notices #() :closing False)
                              (TransactionFlush :opening False :requests #() :notices #() :closing True)))
          single)
  (assert (= (get batched 0) 2) batched)
  (assert (= (get batched 1) #((TransactionFlush :opening True :requests #((SqlQuery DB "SELECT 1" #()) (SqlQuery DB "SELECT 2" #()))
                                                 :notices #((SqlNotify DB "changes" #("table:parts"))) :closing True)))
          batched)
  (assert (= (get signal 1) #((TransactionFlush :opening True :requests #() :notices #((SqlNotify DB "changes" #("table:parts"))) :closing True))) signal)
  (assert (= empty #("empty bundle" #() 0)) empty)
  (assert (all (gfor shape shapes (= (get shape 2) 0))) shapes))


(defk failing-batch [database third]
  {:pre [(: database str) (: third str)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program whose 2nd statement of a bundle fails with a unique violation (third = the 3rd statement of the bundle). The answer of the bundle never comes -- if it did, the program would return an answer and commit."
  (<- (SqlBatch database #((SqlQuery database "INSERT INTO items (id, label) VALUES (70, 'x')" #())
                           (SqlQuery database "INSERT INTO items (id, label) VALUES (70, 'y')" #())
                           (SqlQuery database third #()))
                :commit True))
  "resumed after the bundle")


(defk query-after-commit [database]
  {:pre [(: database str)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that emits a statement after a commit bundle."
  (<- (SqlBatch database #((SqlQuery database "INSERT INTO items (id, label) VALUES (80, 'c')" #())) :commit True))
  (<- (SqlQuery database "SELECT 1" #()))
  "never reached")


(defk stray-batch-refusal [database]
  {:pre [(: database str)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "To emit a bundle outside a transaction and return the refusal message."
  (try
    (<- (SqlBatch database #((SqlQuery database "SELECT 1" #()))))
    "not refused"
    (except [error SqlTransactionMisuse]
      (str error))))


(defk batch-in-default-transaction [database]
  {:pre [(: database str)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that emits a bundle inside a transaction that did not choose batched (it is refused, and the statements of the bundle are not sent)."
  (<- (SqlBatch database #((SqlQuery database "INSERT INTO items (id, label) VALUES (81, 'u')" #())) :commit True))
  "never reached")


(defk misuse-under [database program batched]
  {:pre [(: database str) (: program Program) (: batched bool)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A scenario that turns the refusal with SqlTransactionMisuse of a transaction that chose / did not choose batched into a refusal message and returns it."
  (try
    (<- (SqlTransaction database program :lock-key None :batched batched))
    "not refused"
    (except [error SqlTransactionMisuse]
      (str error))))


(defk batch-promises [database third]
  {:pre [(: database str) (: third str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that fires the contracts of bundles in order: a bundle in which a statement midway fails (the 1st statement is also rolled back); a statement after a commit bundle (refused -- the commit remains); a bundle inside a transaction that did not choose batched
   (refused -- nothing is written); a bundle outside a transaction (refused)."
  (<- (seeded database))
  (<- failed (SqlTransaction database (failing-batch database third) :lock-key "batch-lock" :batched True))
  (<- after-failure (count-items database))
  (<- refused (misuse-under database (query-after-commit database) True))
  (<- after-misuse (count-items database))
  (<- unbatched (misuse-under database (batch-in-default-transaction database) False))
  (<- after-unbatched (count-items database))
  (<- stray (stray-batch-refusal database))
  #(failed after-failure refused after-misuse stray unbatched after-unbatched))


(defk asserted-batch-promises [promises]
  {:pre [(: promises tuple)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To apply the same checks to the answers of batch-promises on sqlite and on real PG."
  (val failed (get promises 0))
  (assert (and (isinstance failed SqlFailed) (.startswith (or failed.sqlstate "") "23")) failed)
  (assert (= (get promises 1) 0) promises)
  (assert (in "emitted SqlQuery after it ended with a commit bundle" (get promises 2)) promises)
  (assert (= (get promises 3) 1) promises)
  (assert (in "outside a transaction" (get promises 4)) promises)
  (assert (in "did not choose batched" (get promises 5)) promises)
  (assert (= (get promises 6) 1) promises)
  None)


(deftest test-sqlite-batch-stops-at-the-first-failure-and-refuses-sql-after-commit
  ;; Failure case (#3605): if a statement midway through a bundle fails, the transaction is rolled back and the program is not resumed; SQL after a commit bundle is refused (the commit remains);
  ;; a bundle outside a transaction is refused. sqlite sends the bundle in order (the answers are the same as the PostgreSQL handler's).
  (<- promises (with-handler [(state) (sqlite-sql-handler #(DB))] (batch-promises DB "INSERT INTO items (id, label) VALUES (71, 'z')")))
  (<- (asserted-batch-promises promises)))


(defk postgres-batch-promises []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To fire the contracts of bundles on real PostgreSQL and attach whether the 3rd statement of the bundle (advances a sequence -- a sequence does not go back on rollback) was sent."
  (<- (fresh DB))
  (<- (SqlQuery DB "DROP SEQUENCE IF EXISTS batch_probe" #()))
  (<- (SqlQuery DB "CREATE SEQUENCE batch_probe" #()))
  (<- promises (batch-promises DB "SELECT nextval('batch_probe')"))
  (<- probe SqlRows (SqlQuery DB "SELECT is_called FROM batch_probe" #()))
  #(promises (get probe.rows 0 0)))


(deftest test-postgres-batch-stops-at-the-first-failure-and-refuses-sql-after-commit
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; Failure case (#3605): a bundle is sent in one pipeline. If a statement midway fails, PostgreSQL does not run the statements after it (the 3rd statement that advances the sequence, COMMIT),
  ;; the answer is the failure of the statement that failed first, and the 1st statement is also rolled back by ROLLBACK. SQL after a commit bundle and a bundle outside a transaction are refused.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (try
    (<- answer (with-handler [(postgres-sql-handler connections)] (postgres-batch-promises)))
    (finally (.close connections)))
  (<- (asserted-batch-promises (get answer 0)))
  (assert (is (get answer 1) False) "A statement after the failed statement was sent (the sequence advanced)"))


(defk failed-pipeline []
  {:pre [] :post [(: % "answer of SqlTransaction")]
   :tags {:context "sql" :role "program"}}
  "To prepare the table and fire a transaction in which a statement midway through a bundle fails (a failure inside the pipeline)."
  (<- (fresh DB))
  (<- (seeded DB))
  (<- failed (SqlTransaction DB (failing-batch DB "SELECT 1") :lock-key "pooled-batch" :batched True))
  failed)


(defk counted-after-insert []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "A program with one commit bundle that inserts 1 row and counts (answer = the number of rows after the insert)."
  (<- answers (SqlBatch DB #((SqlQuery DB "INSERT INTO items (id, label) VALUES (90, 'n')" #())
                             (SqlQuery DB "SELECT count(*) FROM items" #()))
                :commit True))
  (get (. (get answers 1) rows) 0 0))


(deftest test-pooled-postgres-reuses-the-connection-after-a-failed-pipeline
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; Failure case (#3605, condition 2 of cisco-c8): the connection of a transaction in which a statement failed inside the pipeline is returned in the form that has left pipeline mode and rolled the transaction back,
  ;; and the next transaction can use the same connection as usual (a connection returned with the pipeline left open is discarded on return and becomes a different connection). 1 connection.
  (import psycopg)
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (val pool (ThreadPoolExecutor :max-workers 1))
  (try
    (<- failed (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (failed-pipeline)))
    ;; Read the form of the returned connection before closing the lease (once closed, the state is UNKNOWN).
    (val first (list (. (get connections.idle DB) queue)))
    (val returned (tuple (gfor c first #(c.pgconn.pipeline-status c.info.transaction-status))))
    (<- counted (with-handler [(state) (pooled-postgres-sql-handler connections pool)]
                  (SqlTransaction :database DB :program (counted-after-insert) :lock-key "pooled-batch" :batched True)))
    (val second (list (. (get connections.idle DB) queue)))
    (finally (.close connections) (.shutdown pool)))
  (assert (and (isinstance failed SqlFailed) (.startswith (or failed.sqlstate "") "23")) failed)
  (assert (= returned #(#(psycopg.pq.PipelineStatus.OFF psycopg.pq.TransactionStatus.IDLE))) returned)
  (assert (= counted 1) counted)
  (assert (and (= (len second) 1) (is (get second 0) (get first 0))) "The connection after the failed pipeline was not kept in use and was discarded"))


(defclass CountingPool [ThreadPoolExecutor]
  "A pool that counts the number of jobs submitted to the driver's pool (child (3) of #3688 -- the number of switches to the driver's thread for one transaction)."
  (defn #^ None __init__ [self]  ; defk にできない: initialization of an Executor (a contract of concurrent.futures -- outside the VM)
    (.__init__ (super) :max-workers 2)
    (setv self.submitted 0))
  (defn #^ Future submit [self #^ (get Callable #(... object)) call #^ object #* args #^ object #** kwargs]  ; defk にできない: the entry point of an Executor (a contract of concurrent.futures -- outside the VM)
    (setv self.submitted (+ self.submitted 1))
    (.submit (super) call #* args #** kwargs)))


(defk fresh-seeded []
  {:pre [] :post [(: % SqlSchemaApplied)]
   :tags {:context "sql" :role "program"}}
  "To drop and recreate the test table (preparation before counting -- statements outside a transaction)."
  (<- (fresh DB))
  (<- applied (seeded DB))
  applied)


(defk read-then-commit []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "A program that counts rows in the 1st round trip and inserts 1 row in the commit bundle of the 2nd round trip (the same 2 round trips as a write to the record store -- answer = the number of rows after the insert)."
  (<- (SqlQuery DB "SELECT count(*) FROM items" #()))
  (<- answers (SqlBatch DB #((SqlQuery DB "INSERT INTO items (id, label) VALUES (91, 'r')" #())
                             (SqlQuery DB "SELECT count(*) FROM items" #()))
                :commit True))
  (get (. (get answers 1) rows) 0 0))


(deftest test-pooled-postgres-drives-a-batched-transaction-in-one-job
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; Failure case: a batched transaction runs borrowing, all the round trips with the checks between them, and returning as one job of the driver, and does not return to the scheduler's thread between round trips
  ;; -- both a 2-round-trip write and a 1-round-trip transaction submit one job to the pool (before, one per round trip, so 2 and 1 --
  ;; during the scheduler's wait from the answer of the 1st round trip to the 2nd round trip, the store write's lock was also held, and the more crowded it got, the more writes waiting for the lock piled up).
  ;; It also checks the answers, and that the connection is returned as the same single connection.
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (val pool (CountingPool))
  (try
    (<- (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (fresh-seeded)))
    (val before-two pool.submitted)
    (<- two (with-handler [(state) (pooled-postgres-sql-handler connections pool)]
              (SqlTransaction :database DB :program (read-then-commit) :lock-key "pooled-jobs" :batched True)))
    (val two-trips (- pool.submitted before-two))
    (val before-one pool.submitted)
    (<- one (with-handler [(state) (pooled-postgres-sql-handler connections pool)]
              (SqlTransaction :database DB :program (counted-after-insert) :lock-key "pooled-jobs" :batched True)))
    (val one-trip (- pool.submitted before-one))
    (val idle (list (. (get connections.idle DB) queue)))
    (finally (.close connections) (.shutdown pool)))
  (assert (= #(two one) #(1 2)) #(two one))
  (assert (= #(two-trips one-trip) #(1 1)) #(two-trips one-trip))
  (assert (= (len idle) 1) idle))


(deftest test-postgres-connections-refuse-an-environment-without-pipeline-mode [monkeypatch]
  {:skip-if PSYCOPG-SKIP :skip-reason "psycopg is missing"}
  ;; Failure case (#3605, condition 1 of cisco-c8): in an environment where psycopg / libpq lacks pipeline mode, it fails naming it at the point where the connection lease is created (at startup)
  ;; -- it does not silently fall back to the path that sends statements one at a time.
  (import psycopg)
  (.setattr monkeypatch psycopg.Pipeline "is_supported" (classmethod (fn [cls] False)))
  (var refused "")
  (try
    (PostgresConnections #((PostgresDatabase :name DB :dsn "")))
    (except [error RuntimeError]
      (:= refused (str error))))
  (assert (in "pipeline mode" refused) refused)
  (assert (in "libpq" refused) refused))
