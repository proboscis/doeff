;;; Failure cases of the SQL responder that opens an existing DB file (sqlite_file_sql.hy). With a file DB in a pytest temporary dir:
;;;   - A written row can be read even after closing the connection and reopening (it is in the file, not in a memory DB).
;;;   - The rows of an existing file (a table and rows made first with the stdlib sqlite3) can be read as they are; a missing file is refused without being created.
;;;   - journal_mode is wal.
;;;   - A constraint violation is a SqlFailed value (the 23 class); after closing and during SetSqlOutage it is a SqlUnreachable value (it does not become an exception).
;;;   - A failure inside SqlTransaction rolls back.
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import sqlite3)
(import pathlib [Path])
(import pytest)
(import doeff_core_effects [state])
(import doeff_core_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SetSqlOutage SqlParam SqlRows SqlFailed SqlUnreachable SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects [SqliteFile open-sqlite-files close-sqlite-files sqlite-file-sql-handler])

(val DB "custody")

;; The table for the check: with a primary key, and it also checks reading back BOOLEAN.
(val LEDGER (SqlTable :name "ledger"
                      :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT)
                                 (SqlColumn :name "held" :type SqlColumnType.BOOLEAN))
                      :primary-key #("id")))


(defk empty-file [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "To prepare an empty DB file with the stdlib sqlite3 (the responder opens only an existing file)."
  (val path (str (/ tmp-path "custody.db")))
  (.close (sqlite3.connect path))
  path)


(defk ledger-ids [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To read the rows of ledger (in id order)."
  (<- answer SqlRows (SqlQuery database "SELECT id, label, held FROM ledger ORDER BY id" #()))
  answer.rows)


(defk write-two [database]
  {:pre [(: database str)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "A scenario that declares a table and writes 2 rows."
  (<- (SqlEnsureTables database #(LEDGER)))
  (<- (SqlInsertRows database "ledger" #("id" "label" "held") #(#(1 "one" True))))
  (<- written (SqlQuery database "INSERT INTO ledger (id, label, held) VALUES (:id, :label, :held)"
                        #((SqlParam :name "id" :value 2) (SqlParam :name "label" :value "two") (SqlParam :name "held" :value False))))
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
  ;; It can be read even after reopening, and the BOOLEAN column comes back as bool.
  (assert (= rows #(#(1 "one" True) #(2 "two" False))) rows)
  ;; The rows are in the file (readable directly with the stdlib sqlite3).
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
  (assert (not (.exists path)) "created a missing file"))


(deftest test-the-journal-mode-is-wal [tmp-path]
  (<- path (empty-file tmp-path))
  (<- files (open-sqlite-files #((SqliteFile :name DB :path path))))
  (try
    (<- mode (with-handler [(state) (sqlite-file-sql-handler files)] (SqlQuery DB "PRAGMA journal_mode" #())))
    (finally (<- (close-sqlite-files files))))
  (assert (= mode.rows #(#("wal"))) mode)
  ;; The mark that WAL stays in the file (wal from another connection too).
  (val direct (sqlite3.connect path))
  (try
    (assert (= (get (.fetchone (.execute direct "PRAGMA journal_mode")) 0) "wal"))
    (finally (.close direct))))


(defk failures [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that fires a constraint violation and the unreachable mark in turn (each comes back as a value, not an exception)."
  (<- (write-two database))
  (<- duplicate (SqlInsertRows database "ledger" #("id" "label" "held") #(#(1 "dup" True))))
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
  (assert (= (get answers 3) #(#(1 "one" True) #(2 "two" False))) (get answers 3))
  ;; An effect after closing is a SqlUnreachable value (it does not become an exception).
  (<- closed-query (with-handler [(state) (sqlite-file-sql-handler files)] (SqlQuery DB "SELECT 1" #())))
  (<- closed-transaction (with-handler [(state) (sqlite-file-sql-handler files)] (SqlTransaction DB (ledger-ids DB) None)))
  (assert (isinstance closed-query SqlUnreachable) closed-query)
  (assert (isinstance closed-transaction SqlUnreachable) closed-transaction))


(defk insert-then-duplicate [database]
  {:pre [(: database str)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that, inside a transaction, inserts 1 row and then a row with a duplicate primary key (it stops at the second)."
  (<- (SqlQuery database "INSERT INTO ledger (id, label, held) VALUES (3, 'three', 1)" #()))
  (<- (SqlQuery database "INSERT INTO ledger (id, label, held) VALUES (1, 'dup', 1)" #()))
  "never reached here")


(defk insert-one [database]
  {:pre [(: database str)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "A program that inserts 1 row inside a transaction."
  (<- (SqlQuery database "INSERT INTO ledger (id, label, held) VALUES (4, 'four', 0)" #()))
  "inserted")


(defk transactions [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "A scenario that fires, in turn, a transaction that rolls back on failure and a transaction that commits."
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
  ;; The row 3 was rolled back and is absent.
  (assert (= (tuple (gfor row (get answers 1) (get row 0))) #(1 2)) (get answers 1))
  (assert (= (get answers 2) "inserted"))
  (assert (= (tuple (gfor row (get answers 3) (get row 0))) #(1 2 4)) (get answers 3))
  ;; The committed row is in the file.
  (val direct (sqlite3.connect path))
  (try
    (assert (= (.fetchall (.execute direct "SELECT id FROM ledger ORDER BY id")) [#(1) #(2) #(4)]))
    (finally (.close direct))))
