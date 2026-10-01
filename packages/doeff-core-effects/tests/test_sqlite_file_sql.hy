;;; 既存の DB の file を開く SQL の答え手(sqlite_file_sql.hy — agora-redesign #2237 の前提)の失敗ケース。pytest の一時 dir の file の DB で:
;;;   - 書いた行が、接続を閉じて開き直した後も読める(memory の DB ではなく file に在る)。
;;;   - 既存の file(先に stdlib の sqlite3 で表と行を作った物)の行をそのまま読める・無い file は作らずに断る。
;;;   - journal_mode が wal。
;;;   - 制約違反は SqlFailed(23 の類)の値・閉じた後と SetSqlOutage の間は SqlUnreachable の値(例外にならない)。
;;;   - SqlTransaction の中の失敗で巻き戻る。
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import sqlite3)
(import pytest)
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SetSqlOutage SqlParam SqlRows SqlFailed
                                        SqlUnreachable SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects.sqlite_file_sql [SqliteFile open-sqlite-files close-sqlite-files sqlite-file-sql-handler])

(val DB "custody")

;; 検の表: 主鍵つきで、BOOLEAN の読み戻しも見る。
(val LEDGER (SqlTable :name "ledger"
                      :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT)
                                 (SqlColumn :name "held" :type SqlColumnType.BOOLEAN))
                      :primary-key #("id")))


(defk empty-file [tmp-path]
  {:pre [(: tmp-path "Path")] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "stdlib の sqlite3 で空の DB の file を用意するため(答え手は既存の file しか開かない)。"
  (val path (str (/ tmp-path "custody.db")))
  (.close (sqlite3.connect path))
  path)


(defk ledger-ids [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "ledger の行(id 順)を読むため。"
  (<- answer SqlRows (SqlQuery database "SELECT id, label, held FROM ledger ORDER BY id" #()))
  answer.rows)


(defk write-two [database]
  {:pre [(: database str)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "表を宣言して 2 行を書く筋書き。"
  (<- (SqlEnsureTables database #(LEDGER)))
  (<- (SqlInsertRows database "ledger" #("id" "label" "held") #(#(1 "一" True))))
  (<- written (SqlQuery database "INSERT INTO ledger (id, label, held) VALUES (:id, :label, :held)"
                        #((SqlParam :name "id" :value 2) (SqlParam :name "label" :value "二") (SqlParam :name "held" :value False))))
  written)


(deftest test-written-rows-survive-closing-and-reopening [tmp-path]
  (<- path (empty-file tmp-path))
  (<- first (open-sqlite-files #((SqliteFile :name DB :path path))))
  (try
    (<- written (with-handler [(state) (sqlite-file-sql-handler first)] (write-two DB)))
    (finally (<- (close-sqlite-files first))))
  (assert (= written.rowcount 1) written)
  (<- again (open-sqlite-files #((SqliteFile :name DB :path path))))
  (try
    (<- rows (with-handler [(state) (sqlite-file-sql-handler again)] (ledger-ids DB)))
    (finally (<- (close-sqlite-files again))))
  ;; 開き直した後も読め、BOOLEAN の欄は bool で戻る。
  (assert (= rows #(#(1 "一" True) #(2 "二" False))) rows)
  ;; 行は file に在る(stdlib の sqlite3 で直に読める)。
  (val direct (sqlite3.connect path))
  (try
    (assert (= (.fetchall (.execute direct "SELECT id FROM ledger ORDER BY id")) [#(1) #(2)]))
    (finally (.close direct))))


(deftest test-rows-of-an-existing-file-are-read-as-they-are [tmp-path]
  (val path (str (/ tmp-path "existing.db")))
  (val seed (sqlite3.connect path))
  (try
    (.execute seed "CREATE TABLE ledger (id INTEGER PRIMARY KEY, label TEXT NOT NULL, held BOOLEAN NOT NULL)")
    (.executemany seed "INSERT INTO ledger VALUES (?, ?, ?)" [#(7 "seven" 1) #(8 "eight" 0)])
    (.commit seed)
    (finally (.close seed)))
  (<- files (open-sqlite-files #((SqliteFile :name DB :path path))))
  (try
    (<- picked (with-handler [(state) (sqlite-file-sql-handler files)]
                 (SqlQuery DB "SELECT id, label, held FROM ledger WHERE id = :id" #((SqlParam :name "id" :value 8)))))
    (<- every (with-handler [(state) (sqlite-file-sql-handler files)] (ledger-ids DB)))
    (finally (<- (close-sqlite-files files))))
  (assert (= picked.rows #(#(8 "eight" False))) picked)
  (assert (= every #(#(7 "seven" True) #(8 "eight" False))) every))


(deftest test-a-missing-file-is-refused-not-created [tmp-path]
  (val path (/ tmp-path "missing.db"))
  (with [(pytest.raises sqlite3.OperationalError)]
    (<- (open-sqlite-files #((SqliteFile :name DB :path (str path))))))
  (assert (not (.exists path)) "無い file を作った"))


(deftest test-the-journal-mode-is-wal [tmp-path]
  (<- path (empty-file tmp-path))
  (<- files (open-sqlite-files #((SqliteFile :name DB :path path))))
  (try
    (<- mode (with-handler [(state) (sqlite-file-sql-handler files)] (SqlQuery DB "PRAGMA journal_mode" #())))
    (finally (<- (close-sqlite-files files))))
  (assert (= mode.rows #(#("wal"))) mode)
  ;; WAL は file に残る印(別の接続からも wal)。
  (val direct (sqlite3.connect path))
  (try
    (assert (= (get (.fetchone (.execute direct "PRAGMA journal_mode")) 0) "wal"))
    (finally (.close direct))))


(defk failures [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "制約違反と不達の印を順に撃つ筋書き(どれも例外ではなく値で返る)。"
  (<- (write-two database))
  (<- duplicate (SqlInsertRows database "ledger" #("id" "label" "held") #(#(1 "重ね" True))))
  (<- (SetSqlOutage database True))
  (<- down-query (SqlQuery database "SELECT 1" #()))
  (<- down-transaction (SqlTransaction database (ledger-ids database) None))
  (<- (SetSqlOutage database False))
  (<- back (ledger-ids database))
  #(duplicate down-query down-transaction back))


(deftest test-failures-are-values-not-exceptions [tmp-path]
  (<- path (empty-file tmp-path))
  (<- files (open-sqlite-files #((SqliteFile :name DB :path path))))
  (try
    (<- answers (with-handler [(state) (sqlite-file-sql-handler files)] (failures DB)))
    (finally (<- (close-sqlite-files files))))
  (val duplicate (get answers 0))
  (assert (and (isinstance duplicate SqlFailed) (= duplicate.sqlstate "23000")) duplicate)
  (assert (isinstance (get answers 1) SqlUnreachable) (get answers 1))
  (assert (isinstance (get answers 2) SqlUnreachable) (get answers 2))
  (assert (= (get answers 3) #(#(1 "一" True) #(2 "二" False))) (get answers 3))
  ;; 閉じた後の effect は SqlUnreachable の値(例外にならない)。
  (<- closed-query (with-handler [(state) (sqlite-file-sql-handler files)] (SqlQuery DB "SELECT 1" #())))
  (<- closed-transaction (with-handler [(state) (sqlite-file-sql-handler files)] (SqlTransaction DB (ledger-ids DB) None)))
  (assert (isinstance closed-query SqlUnreachable) closed-query)
  (assert (isinstance closed-transaction SqlUnreachable) closed-transaction))


(defk insert-then-duplicate [database]
  {:pre [(: database str)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で 1 行を入れた後に主鍵の重なる行を入れる program(2 つ目で止まる)。"
  (<- (SqlQuery database "INSERT INTO ledger (id, label, held) VALUES (3, '三', 1)" #()))
  (<- (SqlQuery database "INSERT INTO ledger (id, label, held) VALUES (1, '重ね', 1)" #()))
  "ここへは来ない")


(defk insert-one [database]
  {:pre [(: database str)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で 1 行を入れる program。"
  (<- (SqlQuery database "INSERT INTO ledger (id, label, held) VALUES (4, '四', 0)" #()))
  "入れた")


(defk transactions [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "失敗で巻き戻る transaction と commit する transaction を順に撃つ筋書き。"
  (<- (write-two database))
  (<- failed (SqlTransaction database (insert-then-duplicate database) None))
  (<- after-failure (ledger-ids database))
  (<- committed (SqlTransaction database (insert-one database) None))
  (<- after-commit (ledger-ids database))
  #(failed after-failure committed after-commit))


(deftest test-a-failure-inside-a-transaction-rolls-back [tmp-path]
  (<- path (empty-file tmp-path))
  (<- files (open-sqlite-files #((SqliteFile :name DB :path path))))
  (try
    (<- answers (with-handler [(state) (sqlite-file-sql-handler files)] (transactions DB)))
    (finally (<- (close-sqlite-files files))))
  (val failed (get answers 0))
  (assert (and (isinstance failed SqlFailed) (= failed.sqlstate "23000")) failed)
  ;; 3 の行は巻き戻って無い。
  (assert (= (tuple (gfor row (get answers 1) (get row 0))) #(1 2)) (get answers 1))
  (assert (= (get answers 2) "入れた"))
  (assert (= (tuple (gfor row (get answers 3) (get row 0))) #(1 2 4)) (get answers 3))
  ;; commit した行は file に在る。
  (val direct (sqlite3.connect path))
  (try
    (assert (= (.fetchall (.execute direct "SELECT id FROM ledger ORDER BY id")) [#(1) #(2) #(4)]))
    (finally (.close direct))))
