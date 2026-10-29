;;; ClickHouse の約束を持つ模擬の SQL の答え手(clickhouse_mock_sql.hy — card ki-1c9c7903dfc4)の約束ごとの検。
;;;   - SqlTransaction は本物(clickhouse-http-sql-handler)と同じ SqlFailed(0A000)で断り、program は走らない。
;;;   - SqlQuery の rowcount は None(sqlite の答え手は数える)・行は読める。
;;;   - SqlInsertRows は書いた行の数を返す・空は 0・bytes は ValueError。
;;;   - SqlEnsureTables は一意の索引を SqlFailed(0A000)で断る。
;;;   - SqlBatch には答えず外側へ回る・他の database の effect も外側へ回る。
;;;   - SetSqlOutage の間は SqlUnreachable。
(require doeff-hy.macros [deftest defk defhandler <- val var with-handler])
(import pytest)
(import doeff_core_effects [state])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SetSqlOutage SqlParam SqlRows SqlFailed
                                        SqlUnreachable SqlSchemaApplied SqlTable SqlColumn SqlColumnType SqlIndex])
(import doeff_core_effects.clickhouse_mock_sql [clickhouse-mock-sql-handler])
(import doeff_core_effects.clickhouse_http_sql [clickhouse-transaction-refusal])
(import doeff_core_effects.sqlite_sql [sqlite-sql-handler])

(val DB "ledger")

(val ITEMS (SqlTable :name "items"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT :nullable True))
                     :primary-key #("id")))


(defk seeded []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "検の表を宣言して 2 行を書くため。"
  (<- (SqlEnsureTables DB #(ITEMS)))
  (<- (SqlInsertRows DB "items" #("id" "label") #(#(1 "一") #(2 "二"))))
  None)


(defk write-nine []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction の中身: 1 行を書いて印を返す。"
  (<- (SqlQuery DB "INSERT INTO items (id) VALUES (9)" #()))
  "ran")


(defk transaction-attempt []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で 1 行書こうとする筋書き(断られれば書かれない)。"
  (<- (seeded))
  (<- answer (SqlTransaction DB (write-nine) None))
  (<- count (SqlQuery DB "SELECT count(*) FROM items WHERE id = 9" #()))
  #(answer count.rows))


(deftest test-transaction-is-refused-like-the-real-handler
  (<- got (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (transaction-attempt)))
  (val answer (get got 0))
  (val rows (get got 1))
  (<- real (clickhouse-transaction-refusal DB))
  (assert (= answer real) answer)
  (assert (= answer.sqlstate "0A000") answer)
  ;; program は走っていない(行は書かれていない)。
  (assert (= rows #(#(0))) rows))


(deftest test-sqlite-handler-would-accept-the-transaction
  ;; 対照: 包んだ sqlite の答え手は同じ筋書きを通す — 模擬が約束を足している印。
  (<- got (with-handler [(state) (sqlite-sql-handler #(DB))] (transaction-attempt)))
  (val answer (get got 0))
  (val rows (get got 1))
  (assert (= answer "ran") answer)
  (assert (= rows #(#(1))) rows))


(defk read-and-write []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "読む文と書く文の rowcount を見る筋書き。"
  (<- (seeded))
  (<- read (SqlQuery DB "SELECT id, label FROM items ORDER BY id" #()))
  (<- wrote (SqlQuery DB "INSERT INTO items (id, label) VALUES (:id, :label)" #((SqlParam :name "id" :value 3) (SqlParam :name "label" :value "三"))))
  #(read wrote))


(deftest test-query-rowcount-is-none
  (<- got (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (read-and-write)))
  (val read (get got 0))
  (val wrote (get got 1))
  (assert (= read (SqlRows :rows #(#(1 "一") #(2 "二")) :rowcount None)) read)
  (assert (= wrote (SqlRows :rows #() :rowcount None)) wrote))


(defk insert-shapes []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "投入の答えの形(行の数・空)を見る筋書き。"
  (<- (SqlEnsureTables DB #(ITEMS)))
  (<- two (SqlInsertRows DB "items" #("id" "label") #(#(1 "一") #(2 "二"))))
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
  "bytes の値を投入する筋書き(本物は JSON で運べず ValueError)。"
  (<- (SqlEnsureTables DB #(ITEMS)))
  (<- answer (SqlInsertRows DB "items" #("id" "label") #(#(1 b"x"))))
  answer)


(deftest test-insert-of-bytes-is-refused-like-the-real-handler
  (with [(pytest.raises ValueError)]
    (<- _ (with-handler [(state) (clickhouse-mock-sql-handler #(DB))] (insert-bytes)))))


(defk unique-index []
  {:pre [] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "program"}}
  "一意の索引つきの表を宣言する筋書き。"
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
  "外側の答え手の代わり: SqlBatch が内側から回ってきた印を返す。"
  {:tags {:context "sql" :role "foundation"}}
  (SqlBatch [database queries commit]
    (resume "outer")))


(defk stray-batch-and-other-database []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "SqlBatch と宣言していない database の effect が内側で答えられず外側へ回ることを見る筋書き。"
  (<- batch (SqlBatch DB #() False))
  (<- other (SqlTransaction "elsewhere" (write-nine) None))
  #(batch other))


(defhandler other-sink []
  "外側の答え手の代わり: 宣言していない database の SqlTransaction を受ける。"
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
  "不達の印を立て・外して読む筋書き。"
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
