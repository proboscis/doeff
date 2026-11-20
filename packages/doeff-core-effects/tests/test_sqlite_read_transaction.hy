;;; Failure cases for SQLite read connections, SqlReadTransaction (BEGIN DEFERRED), creating missing DB files,
;;; and reading on Compute pool threads without crossing threads with sqlite3.Connection:
;;;   - c67a96905: Two SELECTs inside one SqlReadTransaction do not see a row inserted and committed by another connection in between;
;;;                once SqlReadTransaction commits, a subsequent read sees the committed row.
;;;   - ce0c0b874: open-or-create-sqlite-files creates a store at a path where the file (and parent dir) does not exist and opens it in WAL,
;;;                while open-sqlite-files and open-sqlite-readers still refuse missing files without creating them.
;;;   - Write refusal inside SqlReadTransaction: write statements via SqlQuery become SqlFailed("25006") and roll back;
;;;     SqlInsertRows, nested transactions, other databases, and non-SQL effects raise SqlTransactionMisuse; exceptions roll back.
;;;   - SetSqlOutage and closed connections return SqlUnreachable for SqlReadTransaction.
;;;   - Compute pool threads: sqlite-file-reader-handler and open-sqlite-readers inside Compute run concurrent reads on pool threads
;;;     without sharing sqlite3.Connection across threads, and passing a scheduler-thread reader connection into Compute is rejected.
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import sqlite3)
(import threading)
(import collections.abc [Callable])
(import concurrent.futures [ThreadPoolExecutor])
(import pathlib [Path])
(import pytest)
(import doeff [Gather Program Spawn])
(import doeff_core_effects [state scheduled])
(import doeff_core_effects [Compute Computed ComputeFailed thread-pool-compute-handler inline-compute-handler])
(import doeff_core_effects [SqlQuery SqlInsertRows SqlTransaction SqlReadTransaction SqlEnsureTables SetSqlOutage
                            SqlParam SqlRows SqlFailed SqlUnreachable SqlTransactionMisuse SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects [SqliteFiles open-sqlite-files open-or-create-sqlite-files open-sqlite-readers close-sqlite-files
                            sqlite-file-sql-handler sqlite-file-reader-handler sqlite-sql-handler])

(val DB "store")

(val LEDGER (SqlTable :name "ledger"
                      :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER)
                                 (SqlColumn :name "label" :type SqlColumnType.TEXT))
                      :primary-key #("id")))


(defk ledger-rows [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "Read all rows from ledger ordered by id."
  (<- answer SqlRows (SqlQuery database "SELECT id, label FROM ledger ORDER BY id" #()))
  answer.rows)


(defk insert-ledger-row [database id label]
  {:pre [(: database str) (: id int) (: label str)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "Insert one row into ledger via SqlInsertRows."
  (<- written (SqlInsertRows database "ledger" #("id" "label") #(#(id label))))
  written)


(defk seed-ledger [database rows]
  {:pre [(: database str) (: rows tuple)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "Create the ledger table and insert initial rows inside a write transaction."
  (<- (SqlEnsureTables database #(LEDGER)))
  (<- inserted (SqlTransaction database
                 (insert-ledger-rows database rows)
                 None))
  inserted)


(defk insert-ledger-rows [database rows]
  {:pre [(: database str) (: rows tuple)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "Insert multiple rows into ledger."
  (<- written (SqlInsertRows database "ledger" #("id" "label") rows))
  written)


(defk run-query [database statement]
  {:pre [(: database str) (: statement str)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "program"}}
  "Run one parameterless SqlQuery."
  (<- answer (SqlQuery database statement #()))
  answer)


(defk two-selects-with-hook [database hook]
  {:pre [(: database str) (: hook Callable)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "Run two SELECTs separated by calling hook (which commits on another connection between the two reads)."
  (<- first (ledger-rows database))
  (hook)
  (<- second (ledger-rows database))
  #(first second))


(deftest test-missing-path-store-is-created-and-opened [tmp-path]
  (val path (/ tmp-path "nested" "dir" "store.sqlite"))
  (assert (not (.exists path)))
  (<- writers (open-or-create-sqlite-files {DB (str path)}))
  (try
    (<- (with-handler [(state) (sqlite-file-sql-handler writers)]
          (seed-ledger DB #(#(1 "initial")))))
    (finally (<- (close-sqlite-files writers))))
  (assert (.exists path))
  ;; open-sqlite-readers opens the newly created WAL store read-only and reads the written row.
  (<- readers (open-sqlite-readers {DB (str path)}))
  (try
    (<- rows (with-handler [(state) (sqlite-file-sql-handler readers)]
               (SqlReadTransaction DB (ledger-rows DB))))
    (finally (<- (close-sqlite-files readers))))
  (assert (= rows #(#(1 "initial"))) rows)
  ;; Existing open-sqlite-files and open-sqlite-readers still refuse a missing path without creating it.
  (val missing (/ tmp-path "still-missing.sqlite"))
  (with [(pytest.raises sqlite3.OperationalError)]
    (<- (open-sqlite-files {DB (str missing)})))
  (assert (not (.exists missing)))
  (with [(pytest.raises sqlite3.OperationalError)]
    (<- (open-sqlite-readers {DB (str missing)})))
  (assert (not (.exists missing))))


(deftest test-open-sqlite-readers-refuses-non-wal-file [tmp-path]
  (val path (/ tmp-path "delete-mode.sqlite"))
  (val raw (sqlite3.connect (str path)))
  (try
    (.execute raw "CREATE TABLE ledger (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
    (.commit raw)
    (finally (.close raw)))
  (with [(pytest.raises sqlite3.OperationalError)]
    (<- (open-sqlite-readers {DB (str path)})))
  ;; Reader check must not have changed the file's journal_mode.
  (val check (sqlite3.connect (str path)))
  (try
    (assert (= (get (.fetchone (.execute check "PRAGMA journal_mode")) 0) "delete"))
    (finally (.close check))))


(deftest test-read-transaction-snapshot-ignores-interleaved-commit [tmp-path]
  (val path (str (/ tmp-path "snapshot.sqlite")))
  (<- writers (open-or-create-sqlite-files {DB path}))
  (<- readers (open-sqlite-readers {DB path}))
  (try
    (<- (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
          (seed-ledger DB #(#(1 "before")))))
    ;; Between the 1st and 2nd SELECT inside SqlReadTransaction, another connection inserts and commits row 2.
    (val commit-from-writer
      (fn []
        (setv other (sqlite3.connect path :isolation-level None))
        (try
          (.execute other "BEGIN IMMEDIATE")
          (.execute other "INSERT INTO ledger (id, label) VALUES (2, 'during')")
          (.execute other "COMMIT")
          (finally (.close other)))))
    (<- #(first second) (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
                          (SqlReadTransaction DB (two-selects-with-hook DB commit-from-writer))))
    (assert (= first #(#(1 "before"))) first)
    (assert (= second #(#(1 "before"))) second)
    ;; After SqlReadTransaction commits, the next read sees row 2.
    (<- after (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
                (SqlReadTransaction DB (ledger-rows DB))))
    (assert (= after #(#(1 "before") #(2 "during"))) after)
    (finally
      (<- (close-sqlite-files readers))
      (<- (close-sqlite-files writers)))))


(deftest test-read-transaction-refuses-writes-and-misuse [tmp-path]
  (val path (str (/ tmp-path "readonly.sqlite")))
  (<- writers (open-or-create-sqlite-files {DB path}))
  (<- readers (open-sqlite-readers {DB path}))
  (try
    (<- (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
          (seed-ledger DB #(#(1 "kept")))))
    ;; Write SQL via SqlQuery inside SqlReadTransaction fails with SqlFailed 25006.
    (<- write-failed (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
                       (SqlReadTransaction DB (run-query DB "INSERT INTO ledger (id, label) VALUES (2, 'nope')"))))
    (assert (and (isinstance write-failed SqlFailed) (= write-failed.sqlstate "25006")) write-failed)
    ;; Write via SqlInsertRows inside SqlReadTransaction raises SqlTransactionMisuse.
    (with [(pytest.raises SqlTransactionMisuse)]
      (<- (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
            (SqlReadTransaction DB (insert-ledger-row DB 2 "nope")))))
    ;; Nested SqlReadTransaction raises SqlTransactionMisuse.
    (defk nested-read [database]
      {:pre [(: database str)] :post [(: % tuple)]}
      (<- inner (SqlReadTransaction database (ledger-rows database)))
      inner)
    (with [(pytest.raises SqlTransactionMisuse)]
      (<- (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
            (SqlReadTransaction DB (nested-read DB)))))
    ;; Nested SqlTransaction raises SqlTransactionMisuse.
    (defk nested-write [database]
      {:pre [(: database str)] :post [(: % SqlRows)]}
      (<- inner (SqlTransaction database (insert-ledger-row database 2 "nested") None))
      inner)
    (with [(pytest.raises SqlTransactionMisuse)]
      (<- (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
            (SqlReadTransaction DB (nested-write DB)))))
    ;; Exception inside SqlReadTransaction rolls back and propagates cleanly.
    (defk raise-inside-read [database]
      {:pre [(: database str)] :post [(: % tuple)]}
      (<- (ledger-rows database))
      (raise (RuntimeError "boom in read tx")))
    (with [(pytest.raises RuntimeError :match "boom in read tx")]
      (<- (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
            (SqlReadTransaction DB (raise-inside-read DB)))))
    ;; Single-handle sqlite-file-sql-handler (without :readers) also answers SqlReadTransaction and restores query_only afterwards.
    (defk read-then-write [database]
      {:pre [(: database str)] :post [(: % tuple)]}
      (<- r1 (SqlReadTransaction database (ledger-rows database)))
      (<- (SqlTransaction database (insert-ledger-row database 2 "after-single-read") None))
      (<- r2 (SqlReadTransaction database (ledger-rows database)))
      #(r1 r2))
    (<- #(single-r1 single-r2) (with-handler [(state) (sqlite-file-sql-handler writers)]
                                 (read-then-write DB)))
    (assert (= single-r1 #(#(1 "kept"))) single-r1)
    (assert (= single-r2 #(#(1 "kept") #(2 "after-single-read"))) single-r2)
    ;; SetSqlOutage makes SqlReadTransaction return SqlUnreachable.
    (defk read-under-outage [database]
      {:pre [(: database str)] :post [(: % (| tuple SqlFailed SqlUnreachable))]}
      (<- (SetSqlOutage database True))
      (<- out (SqlReadTransaction database (ledger-rows database)))
      out)
    (<- outage-answer (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
                        (read-under-outage DB)))
    (assert (isinstance outage-answer SqlUnreachable) outage-answer)
    ;; Data remains untouched by outage or failed writes.
    (<- rows (with-handler [(state) (sqlite-file-sql-handler writers :readers readers)]
               (SqlReadTransaction DB (ledger-rows DB))))
    (assert (= rows #(#(1 "kept") #(2 "after-single-read"))) rows)
    (finally
      (<- (close-sqlite-files readers))
      (<- (close-sqlite-files writers))))
  ;; After closing readers, SqlReadTransaction returns SqlUnreachable.
  (<- closed-answer (with-handler [(state) (sqlite-file-sql-handler readers)]
                      (SqlReadTransaction DB (ledger-rows DB))))
  (assert (isinstance closed-answer SqlUnreachable) closed-answer))


(deftest test-compute-pool-reads-with-thread-local-connections [tmp-path]
  (val path (str (/ tmp-path "compute.sqlite")))
  (<- writers (open-or-create-sqlite-files {DB path}))
  (try
    (<- (with-handler [(state) (sqlite-file-sql-handler writers)]
          (seed-ledger DB #(#(1 "one") #(2 "two")))))
    (val pool (ThreadPoolExecutor :max-workers 3))
    (try
      (defk compute-one [program]
        {:pre [(: program Program)] :post [(: % (| Computed ComputeFailed))]}
        (<- outcome (Compute program))
        outcome)
      ;; Pattern 1: sqlite-file-reader-handler inside Compute opens/closes a reader connection on each pool thread.
      (defk compute-read-via-handler [store-path]
        {:pre [(: store-path str)] :post [(: % tuple)]}
        (<- answer (with-handler [(sqlite-file-reader-handler {DB store-path})]
                     (SqlReadTransaction DB (ledger-rows DB))))
        #(answer (. (threading.current-thread) ident)))
      ;; Pattern 2: open-sqlite-readers + sqlite-file-sql-handler + close-sqlite-files inside Compute.
      (defk compute-read-via-open [store-path]
        {:pre [(: store-path str)] :post [(: % tuple)]}
        (<- readers (open-sqlite-readers {DB store-path}))
        (try
          (<- answer (with-handler [(state) (sqlite-file-sql-handler readers)]
                       (SqlReadTransaction DB (ledger-rows DB))))
          #(answer (. (threading.current-thread) ident))
          (finally (<- (close-sqlite-files readers)))))
      (defk gather-reads [store-path]
        {:pre [(: store-path str)] :post [(: % list)]}
        (<- t1 (Spawn (compute-one (compute-read-via-handler store-path))))
        (<- t2 (Spawn (compute-one (compute-read-via-handler store-path))))
        (<- t3 (Spawn (compute-one (compute-read-via-open store-path))))
        (<- gathered (Gather t1 t2 t3))
        gathered)
      (<- outcomes (with-handler [(thread-pool-compute-handler pool)]
                     (gather-reads path)))
      (for [outcome outcomes]
        (assert (isinstance outcome Computed) outcome)
        (assert (= (get outcome.value 0) #(#(1 "one") #(2 "two"))) outcome))
      ;; Also verify snapshot isolation inside Compute with sqlite-file-reader-handler.
      (val commit-row-3
        (fn []
          (setv other (sqlite3.connect path :isolation-level None))
          (try
            (.execute other "BEGIN IMMEDIATE")
            (.execute other "INSERT INTO ledger (id, label) VALUES (3, 'three')")
            (.execute other "COMMIT")
            (finally (.close other)))))
      (defk compute-snapshot [store-path]
        {:pre [(: store-path str)] :post [(: % tuple)]}
        (<- answer (with-handler [(sqlite-file-reader-handler {DB store-path})]
                     (SqlReadTransaction DB (two-selects-with-hook DB commit-row-3))))
        answer)
      (<- snap (with-handler [(thread-pool-compute-handler pool)]
                 (Compute (compute-snapshot path))))
      (assert (isinstance snap Computed) snap)
      (assert (= snap.value #(#(#(1 "one") #(2 "two")) #(#(1 "one") #(2 "two")))) snap)
      ;; Negative check: passing a scheduler-thread reader connection into Compute fails check_same_thread rather than crossing threads silently.
      (<- scheduler-readers (open-sqlite-readers {DB path}))
      (try
        (defk compute-wrong-thread [bad-readers]
          {:pre [(: bad-readers SqliteFiles)] :post [(: % (| tuple SqlFailed SqlUnreachable))]}
          (<- answer (with-handler [(state) (sqlite-file-sql-handler bad-readers)]
                       (SqlReadTransaction DB (ledger-rows DB))))
          answer)
        (<- wrong (with-handler [(thread-pool-compute-handler pool)]
                    (Compute (compute-wrong-thread scheduler-readers))))
        (assert (or (isinstance wrong ComputeFailed)
                    (and (isinstance wrong Computed) (isinstance wrong.value SqlFailed)))
                wrong)
        (finally (<- (close-sqlite-files scheduler-readers))))
      (finally (.shutdown pool :wait True)))
    ;; Also works under inline-compute-handler.
    (defk inline-read [store-path]
      {:pre [(: store-path str)] :post [(: % tuple)]}
      (<- answer (with-handler [(sqlite-file-reader-handler {DB store-path})]
                   (SqlReadTransaction DB (ledger-rows DB))))
      answer)
    (<- inline-outcome (with-handler [inline-compute-handler]
                         (Compute (inline-read path))))
    (assert (isinstance inline-outcome Computed) inline-outcome)
    (assert (= inline-outcome.value #(#(1 "one") #(2 "two") #(3 "three"))) inline-outcome)
    (finally (<- (close-sqlite-files writers)))))


(deftest test-in-memory-sqlite-handler-answers-read-transaction []
  (defk memory-scenario []
    {:pre [] :post [(: % tuple)]}
    (<- (SqlEnsureTables DB #(LEDGER)))
    (<- (SqlTransaction DB (insert-ledger-row DB 1 "mem") None))
    (<- read-ok (SqlReadTransaction DB (ledger-rows DB)))
    (<- write-refused (SqlReadTransaction DB (run-query DB "INSERT INTO ledger (id, label) VALUES (2, 'bad')")))
    ;; After SqlReadTransaction finishes, query_only is restored so writes work again.
    (<- write-after (SqlTransaction DB (insert-ledger-row DB 2 "after") None))
    (<- final-rows (SqlReadTransaction DB (ledger-rows DB)))
    #(read-ok write-refused write-after final-rows))
  (<- #(read-ok write-refused write-after final-rows)
      (with-handler [(state) (sqlite-sql-handler #(DB))]
        (memory-scenario)))
  (assert (= read-ok #(#(1 "mem"))) read-ok)
  (assert (and (isinstance write-refused SqlFailed) (= write-refused.sqlstate "25006")) write-refused)
  (assert (= final-rows #(#(1 "mem") #(2 "after"))) final-rows))
