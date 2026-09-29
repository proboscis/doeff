;; doeff の汎用の SQL の effect に載せ替えた PostgreSQL の置き場が、旧い版(自前の psycopg の host — #880 の前)と
;; 同じ表の形と同じ錠を使うこと。旧い版と新しい版が同じ置き場に重なっても(入れ替えの途中)壊れないための約束:
;;   - 表の用意の DDL は旧い版と同じ字面(tests/pg_ddl_before_880.json = 旧い版の schema-statements の "records_" の答えの写し)。
;;     答え手の方言への書き換え(postgres-statement)を通した後の、driver に渡る字面で比べる。
;;   - 書きの錠と移行の錠の鍵は旧い版の錠の文の引数と同じ字面(接頭辞 + "records-writer" / "records-migrate" — 区切りなし)で、
;;     PostgreSQL の hashtext が同じ整数を返す(= 同じ advisory lock の番号)。
;;   - 旧い版の錠の文(`SELECT pg_advisory_xact_lock(hashtext(%s))`)を別の接続で取っている間、新しい版の書きは待つ
;;     (同じ錠を取り合う — 変更の列の番号に穴が出ない土台)。
;;   - 配列の引数は `IN (:t0, …)` に展げ、空の組は文にしない。
;; 実 PostgreSQL の検は env DOEFF_RECORDS_TEST_PG_DSN が無ければ skip。
(require doeff-hy.macros [deftest val var <-])
(import json)
(import os)
(import pathlib [Path])
(import threading)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.sql_effects [SqlQuery SqlParam SqlRows])
(import doeff_core_effects.postgres_sql [postgres-sql-handler postgres-statement])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [ExpectAbsent Written])
(import doeff_records.effects [PutRow])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [pg-records-handler drop-records-tables DEFAULT-POLL-SECONDS])
(import doeff_records.pg_sql [schema-statements writer-lock-key migrate-lock-key in-list changes-statement terminal-rows-statement])
(import tests.interpreters [PG-DSN-VARIABLE DATABASE ORIGIN-HOST open-postgres postgres-connections fresh-prefix run-sql prepared-store])

(val PG-DSN (.get os.environ PG-DSN-VARIABLE))
(val BEFORE-880-DDL (json.loads (.read-text (/ (. (Path __file__) parent) "pg_ddl_before_880.json") :encoding "utf-8")))
;; 旧い版の錠の文と鍵の字面(pg_sql.hy の lock-statement・migrate-lock-statement の写し — #880 の前)。
(val BEFORE-880-LOCK-TEXT "SELECT pg_advisory_xact_lock(hashtext(%s))")
(val BEFORE-880-WRITER-KEY (fn [prefix] (+ prefix "records-writer")))
(val BEFORE-880-MIGRATE-KEY (fn [prefix] (+ prefix "records-migrate")))


(deftest test-the-ddl-reaches-the-driver-with-the-text-of-the-version-before-880
  (<- statements (schema-statements "records_" LAW-SCHEMA))
  (var texts [])
  (for [statement statements]
    (<- bound (postgres-statement statement.text statement.params))
    (.append texts bound.text))
  (assert (= texts BEFORE-880-DDL) (repr texts)))


(deftest test-the-lock-keys-have-the-text-of-the-version-before-880
  (for [prefix ["records_" "t0123456789ab_" "app_"]]
    (<- writer (writer-lock-key prefix))
    (<- migrate (migrate-lock-key prefix))
    (assert (= writer (BEFORE-880-WRITER-KEY prefix)))
    (assert (= migrate (BEFORE-880-MIGRATE-KEY prefix)))))


(deftest test-arrays-expand-into-in-lists-and-an-empty-list-is-refused
  (<- expanded (in-list "t" #("a" "b" "c")))
  (assert (= expanded.text ":t0, :t1, :t2"))
  (assert (= (lfor p expanded.params #(p.name p.value)) [#("t0" "a") #("t1" "b") #("t2" "c")]))
  (<- changes (changes-statement "records_" 0 9 #("parts" "tickets") 50))
  (<- bound (postgres-statement changes.text changes.params))
  (assert (in "ledger IN (%(t0)s, %(t1)s)" bound.text) bound.text)
  (<- terminal (terminal-rows-statement "records_" "parts" "state" #("closed")))
  (assert (in "IN (:t0)" terminal.text) terminal.text)
  ;; 空の組は `IN ()`(構文の誤り)にしない — 組み立てが断り、呼び手の枝(pg.hy)が文を流さない。
  (var refused False)
  (try
    (<- (in-list "t" #()))
    (except [AssertionError] (:= refused True)))
  (assert refused))


(deftest test-postgres-hashes-the-old-and-new-lock-keys-to-the-same-number
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (val connections (postgres-connections 1))
  (val connection (open-postgres))
  (try
    (for [prefix ["records_" "t0123456789ab_"]]
      (for [#(new-key old-key) [#((run (writer-lock-key prefix)) (BEFORE-880-WRITER-KEY prefix))
                                #((run (migrate-lock-key prefix)) (BEFORE-880-MIGRATE-KEY prefix))]]
        ;; 新しい版の道: 中立の記法の文を答え手が流す。
        (val answer (run-sql connections (SqlQuery DATABASE "SELECT hashtext(:k)" #((SqlParam :name "k" :value new-key)))))
        (assert (isinstance answer SqlRows) (repr answer))
        ;; 旧い版の道: psycopg に `%s` の文を直に流す。
        (val old-number (get (.fetchone (.execute connection "SELECT hashtext(%s)" #(old-key))) 0))
        (assert (= (get answer.rows 0 0) old-number) (repr #(new-key (get answer.rows 0 0) old-key old-number)))))
    (finally
      (.close connection)
      (.close connections))))


(deftest test-a-write-waits-while-the-version-before-880-holds-the-writer-lock
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 旧い版の process の代役: 別の接続で旧い版の錠の文を流して transaction を開いたままにする。新しい版の PutRow は同じ錠を待ち、
  ;; 旧い版が commit した後で書く。
  (val connections (postgres-connections 2))
  (val store (prepared-store connections (fresh-prefix)))
  (val old (open-postgres))
  (val answers [])
  (defn write []  ; defk にできない: thread の target
    (.append answers (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                                     (pg-records-handler store MAKER ORIGIN-HOST DEFAULT-POLL-SECONDS)]
                                                    (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent)))))))
  (try
    (.execute old "BEGIN")
    (.execute old BEFORE-880-LOCK-TEXT #((BEFORE-880-WRITER-KEY store.prefix)))
    (val writer (threading.Thread :target write))
    (.start writer)
    ;; 新しい版の書きが advisory lock を待つまで(pg_locks に未許可の advisory の行が現れるまで)見る。
    (var waiting 0)
    (val deadline (+ (time.monotonic) 10))
    (while (and (= waiting 0) (< (time.monotonic) deadline))
      (:= waiting (get (.fetchone (.execute old "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")) 0))
      (time.sleep 0.05))
    (assert (= waiting 1) waiting)
    (assert (= answers []) (repr answers))
    (.execute old "COMMIT")
    (.join writer 10)
    (assert (= (lfor a answers (. (type a) __name__)) ["Written"]) (repr answers))
    (finally
      (.close old)
      (run-sql connections (drop-records-tables store))
      (.close connections))))
