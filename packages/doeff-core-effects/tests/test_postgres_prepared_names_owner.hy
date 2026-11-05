;;; The owner of prepared statement names on a connection is doeff alone: postgres-flush prepares the statements of a batched transaction
;;; under the names doeff_pq_N and remembers them in a per-connection PreparedBook, and psycopg's automatic prepare is turned off. If psycopg holds names
;;; itself, on the cursor path it prepares the same statement as _pg3_N from the 6th time, and the following ROLLBACK discards its own memory and sends DEALLOCATE ALL,
;;; which also deletes doeff_pq_N -- PreparedBook does not know, so that connection's next batched round trip kept failing with 26000 (the 500 of put-row in merge-queue-ledger).
;;;   - Connect to a disposable PostgreSQL (postgres_support/disposable_postgres.py) with a lease limit of 1 (every borrow is the same connection).
;;;   - With both PostgreSQL handlers (postgres-sql-handler and pooled-postgres-sql-handler), fire the production path: prepare the names in a batched transaction
;;;     -> run the same statement on the cursor path more than psycopg's default threshold (5 times) -> an unbatched transaction fails on the statement limit (57014) and ROLLBACKs -> the batched
;;;     transaction once more.
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import concurrent.futures [ThreadPoolExecutor])
(import doeff_core_effects [state])
(import doeff_core_effects [SqlQuery SqlTransaction SqlEnsureTables SqlParam SqlRows SqlFailed SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects [postgres-sql-handler PostgresDatabase PostgresConnections])
(import doeff_core_effects [pooled-postgres-sql-handler])
(import disposable_postgres [session-dsn session-postgres-skip-reason])

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))
;; A count exceeding psycopg's default prepare_threshold (5).
(val CURSOR-REPEATS 8)

(val NAMED (SqlTable :name "named_rows"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT))
                     :primary-key #("id")))


(defk counted []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "To read the number of rows of a table (the same statement in a batched transaction and on the cursor path)."
  (<- answer (SqlQuery DB "SELECT count(*) FROM named_rows WHERE id >= :floor" #((SqlParam :name "floor" :value 0))))
  (assert (isinstance answer SqlRows) answer)
  (get answer.rows 0 0))


(defk timed-out []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that fails on the statement limit (statement_timeout) (57014, the same as the production pruning statement hitting its limit -- the transaction is ROLLBACKed)."
  (<- (SqlQuery DB "SET LOCAL statement_timeout = '50ms'" #()))
  (<- (SqlQuery DB "SELECT pg_sleep(1)" #()))
  "never reached")


(defk prepared-names []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To read, sorted by name, the prepared statement names on the server for the current connection (limit 1 -- the same connection)."
  (<- answer (SqlQuery DB "SELECT name FROM pg_prepared_statements ORDER BY name" #()))
  (tuple (gfor row answer.rows (get row 0))))


(defk rolled-back-path []
  {:pre [] :post [(: % dict)]
   :tags {:context "sql" :role "program"}}
  "To fire the production path (header comment) and collect the batched transaction's answers and the names on the server."
  (<- (SqlEnsureTables DB #(NAMED)))
  (<- (SqlQuery DB "DELETE FROM named_rows" #()))
  (<- first (SqlTransaction DB (counted) None True))
  (for [_ (range CURSOR-REPEATS)]
    (<- (counted)))
  (<- before (prepared-names))
  (<- failed (SqlTransaction DB (timed-out) None False))
  (<- after (prepared-names))
  (<- again (SqlTransaction DB (counted) None True))
  {"first" first "before" before "failed" failed "after" after "again" again})


(defk asserted-rolled-back-path [seen]
  {:pre [(: seen dict)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To check the answers of rolled-back-path against the rules of name ownership."
  (assert (= (get seen "first") 0) seen)
  (assert (and (isinstance (get seen "failed") SqlFailed) (= (. (get seen "failed") sqlstate) "57014")) seen)
  ;; The names on the server are doeff's only (psycopg assigns no name on the cursor path however many times it runs). They do not disappear after ROLLBACK.
  (assert (all (gfor name (get seen "before") (.startswith name "doeff_pq_"))) seen)
  (assert (get seen "before") seen)
  (assert (= (get seen "after") (get seen "before")) seen)
  ;; The batched round trip after ROLLBACK also goes through with the remembered names (it does not fail with 26000).
  (assert (= (get seen "again") 0) seen)
  None)


(deftest test-postgres-rollback-keeps-doeff-prepared-names
  {:skip-if (bool POSTGRES-SKIP-REASON) :skip-reason POSTGRES-SKIP-REASON}
  ;; With both PostgreSQL handlers, the cursor path's ROLLBACK does not delete doeff's names, and the next batched transaction goes through.
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (val plain (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
    (try
      (<- direct (with-handler [(postgres-sql-handler plain)] (rolled-back-path)))
      (finally (.close plain)))
    (val shared (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
    (try
      (<- pooled (with-handler [(state) (pooled-postgres-sql-handler shared pool)] (rolled-back-path)))
      (finally (.close shared)))
    (finally (.shutdown pool)))
  (<- (asserted-rolled-back-path direct))
  (<- (asserted-rolled-back-path pooled)))
