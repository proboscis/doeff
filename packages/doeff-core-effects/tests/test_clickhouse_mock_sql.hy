;;; Per-promise tests for the mock SQL answerer that carries ClickHouse's promises (_clickhouse_mock_sql.hy).
;;;   - SqlTransaction is refused with the same SqlFailed(0A000) as the real one (clickhouse-http-sql-handler), and the program does not run.
;;;   - The rowcount of SqlQuery is None (the sqlite answerer counts) and rows are readable.
;;;   - SqlInsertRows returns the number of rows written; empty gives 0; bytes raises ValueError.
;;;   - SqlEnsureTables refuses a unique index with SqlFailed(0A000).
;;;   - SqlBatch is not answered and passes to the outside; effects of other databases also pass to the outside.
;;;   - SqlUnreachable while SetSqlOutage is in force.
(require doeff-hy.macros [deftest defk defhandler <- val var with-handler])
(import pytest)
(import doeff_core_effects [state])
(import doeff_core_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SetSqlOutage SqlParam SqlRows SqlFailed SqlUnreachable SqlSchemaApplied SqlTable SqlColumn SqlColumnType SqlIndex])
(import doeff_core_effects._clickhouse_mock_sql [clickhouse-mock-sql-handler])
(import doeff_core_effects._clickhouse_http_sql [_clickhouse-transaction-refusal])
(import doeff_core_effects [sqlite-sql-handler])

(val DB "ledger")

(val ITEMS (SqlTable :name "items"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT :nullable True))
                     :primary-key #("id")))


(defk seeded []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To declare the test table and write 2 rows."
  (<- (SqlEnsureTables DB #(ITEMS)))
  (<- (SqlInsertRows DB "items" #("id" "label") #(#(1 "one") #(2 "two"))))
  None)


(defk write-nine []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "Body of the transaction: write 1 row and return a marker."
  (<- (SqlQuery DB "INSERT INTO items (id) VALUES (9)" #()))
  "ran")


(defk transaction-attempt []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that tries to write 1 row inside a transaction (nothing is written if refused)."
  (<- (seeded))
  (<- answer (SqlTransaction DB (write-nine) None))
  (<- count (SqlQuery DB "SELECT count(*) FROM items WHERE id = 9" #()))
  #(answer count.rows))


(deftest test-transaction-is-refused-like-the-real-handler
  (<- got (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (transaction-attempt)))
  (val answer (get got 0))
  (val rows (get got 1))
  (<- real (_clickhouse-transaction-refusal DB))
  (assert (= answer real) answer)
  (assert (= answer.sqlstate "0A000") answer)
  ;; The program did not run (no row was written).
  (assert (= rows #(#(0))) rows))


(deftest test-sqlite-handler-would-accept-the-transaction
  ;; Contrast: the wrapped sqlite answerer lets the same scenario through - a sign that the mock adds the promises.
  (<- got (with-handler [(state) (sqlite-sql-handler #(DB))] (transaction-attempt)))
  (val answer (get got 0))
  (val rows (get got 1))
  (assert (= answer "ran") answer)
  (assert (= rows #(#(1))) rows))


(defk read-and-write []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that checks the rowcount of a read statement and a write statement."
  (<- (seeded))
  (<- read (SqlQuery DB "SELECT id, label FROM items ORDER BY id" #()))
  (<- wrote (SqlQuery DB "INSERT INTO items (id, label) VALUES (:id, :label)" #((SqlParam :name "id" :value 3) (SqlParam :name "label" :value "three"))))
  #(read wrote))


(deftest test-query-rowcount-is-none
  (<- got (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (read-and-write)))
  (val read (get got 0))
  (val wrote (get got 1))
  (assert (= read (SqlRows :rows #(#(1 "one") #(2 "two")) :rowcount None)) read)
  (assert (= wrote (SqlRows :rows #() :rowcount None)) wrote))


(defk insert-shapes []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that checks the shape of the insert answers (row count, empty)."
  (<- (SqlEnsureTables DB #(ITEMS)))
  (<- two (SqlInsertRows DB "items" #("id" "label") #(#(1 "one") #(2 "two"))))
  (<- none (SqlInsertRows DB "items" #("id" "label") #()))
  #(two none))


(deftest test-insert-counts-written-rows
  (<- got (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (insert-shapes)))
  (val two (get got 0))
  (val none (get got 1))
  (assert (= two (SqlRows :rows #() :rowcount 2)) two)
  (assert (= none (SqlRows :rows #() :rowcount 0)) none))


(defk insert-bytes []
  {:pre [] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "program"}}
  "A scenario that inserts a bytes value (the real one cannot carry it as JSON and raises ValueError)."
  (<- (SqlEnsureTables DB #(ITEMS)))
  (<- answer (SqlInsertRows DB "items" #("id" "label") #(#(1 b"x"))))
  answer)


(deftest test-insert-of-bytes-is-refused-like-the-real-handler
  (with [(pytest.raises ValueError)]
    (<- _ (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (insert-bytes)))))


(defk unique-index []
  {:pre [] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "program"}}
  "A scenario that declares a table with a unique index."
  (<- answer (SqlEnsureTables DB #((SqlTable :name "u" :columns #((SqlColumn :name "k" :type SqlColumnType.TEXT))
                                             :indexes #((SqlIndex :name "u_k" :columns #("k") :unique True))))))
  answer)


(deftest test-unique-index-is-refused
  (<- answer (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (unique-index)))
  (assert (isinstance answer SqlFailed) answer)
  (assert (= answer.sqlstate "0A000") answer)
  (<- applied (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (SqlEnsureTables DB #(ITEMS))))
  (assert (isinstance applied SqlSchemaApplied) applied))


(defhandler batch-sink []
  "Stand-in for the outer answerer: returns a marker that SqlBatch came around from the inside."
  {:tags {:context "sql" :role "foundation"}}
  (SqlBatch [database queries commit]
    (resume "outer")))


(defk stray-batch-and-other-database []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that checks SqlBatch and effects of undeclared databases are not answered inside and pass to the outside."
  (<- batch (SqlBatch DB #() False))
  (<- other (SqlTransaction "elsewhere" (write-nine) None))
  #(batch other))


(defhandler other-sink []
  "Stand-in for the outer answerer: receives SqlTransaction for an undeclared database."
  {:tags {:context "sql" :role "foundation"}}
  (SqlTransaction [database program lock-key]
    (resume "outer-transaction")))


(deftest test-batch-and-undeclared-databases-pass-outward
  (<- got (with-handler [(state) (other-sink) (batch-sink) (clickhouse-mock-sql-handler #(DB))] (stray-batch-and-other-database)))
  (val batch (get got 0))
  (val other (get got 1))
  (assert (= batch "outer") batch)
  (assert (= other "outer-transaction") other))


(defk outage-cycle []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that sets and clears the unreachable marker and reads it."
  (<- (seeded))
  (<- (SetSqlOutage DB True))
  (<- down (SqlQuery DB "SELECT 1" #()))
  (<- (SetSqlOutage DB False))
  (<- up (SqlQuery DB "SELECT count(*) FROM items" #()))
  #(down up))


(deftest test-outage-answers-unreachable
  (<- got (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (outage-cycle)))
  (val down (get got 0))
  (val up (get got 1))
  (assert (isinstance down SqlUnreachable) down)
  (assert (= up.rows #(#(2))) up))
