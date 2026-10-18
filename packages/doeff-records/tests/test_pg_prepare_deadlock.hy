;; 表の用意(移行)と同じ置き場の書きが重なっても、用意が deadlock(40P01)で起動を止めない(2026-10-16 — 新しい版の用意が、入れ替えの
;; 間も同じ置き場に答えた別の process の書きと逆の順で表の lock を取り、1 回目の起動が落ちた)。
;; 旧い版の用意は在る索引にも CREATE INDEX IF NOT EXISTS を流し、表に ShareLock を取って transaction の終わりまで持った。書きは行の表に
;; RowExclusiveLock を取る。今の用意は在る索引の文を流さず(pg_sql.hy の頭の註の「後から作る索引」)、用意済みの置き場の表には lock を
;; 取らない。
;; 書きの代役 = 別の接続で row_changes に RowExclusiveLock を取り(書きの錠の中か外で)、その後 append_rows にも取りに行く。
;; 性質 1: 書きが書きの錠の中なら、用意は書きの錠を待ち(表の lock を持たずに)、deadlock にならず transaction 1 つで通る。
;; 性質 2: 書きの錠の外の書きが表を持っていても、用意済みの置き場の用意は表の lock を待たずに transaction 1 つで通る。
;; 反例: 旧い版の用意(表の用意の文の全部を 1 つの transaction で — pg_sql.schema-statements)は同じ書きの代役の表の lock を待つ
;; (「待たない」の判定が何も確かめずに緑になる形を外す)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest val])
(import concurrent.futures [ThreadPoolExecutor])
(import pytest)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.pg [drop-records-tables prepare-records-store run-statements in-transaction PreparedStore])
(import doeff_records.pg_sql [writer-lock-key migrate-lock-key schema-statements])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE BUILDER open-postgres postgres-connections builder-connections
                            fresh-prefix run-sql prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
(val PG-SKIP-REASON (pg-skip-reason))
;; 本物の PostgreSQL の lock の待ちの実時間が対象(用意が書きの代役を待ち始めるのを締め切りつきで待つ)。
(val pytestmark pytest.mark.realtime)
;; 用意が書きの代役を待ち始めるかを見る締め切り(秒)。
(val BLOCK-DEADLINE 10)


(defn blocked-by [watcher #^ int pid]  ; defk にできない: 検の素の接続で数える
  "接続 pid の lock を待っている接続の数。"
  (get (.fetchone (.execute watcher "SELECT count(*) FROM pg_stat_activity WHERE %s = ANY(pg_blocking_pids(pid))" #(pid))) 0))


(defn new-prepare [connections counts #^ str prefix]  ; defk にできない: 用意を thread の run で回す入口の Program を作る
  "今の版の用意(後から作る索引の作りの 1 本 BUILDER と、往復を数える代役の答え手の下で)を 1 度流す関数を返す。"
  (fn []
    (setv builder (builder-connections))
    (try
      (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler builder) (postgres-sql-handler connections)
                                      (probe-sql-handler connections DATABASE (QueryProbe counts))]
                                     (prepare-records-store DATABASE BUILDER LAW-SCHEMA prefix))))
      (finally (.close builder)))))


(defn old-prepare [connections counts #^ str prefix]  ; defk にできない: 用意を thread の run で回す入口の Program を作る
  "旧い版の用意(表の用意の文の全部を移行の錠の transaction 1 つで — 頭の註の反例)を 1 度流す関数を返す。"
  (fn []
    (setv statements (run (schema-statements prefix LAW-SCHEMA))
          key (run (migrate-lock-key prefix)))
    (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (probe-sql-handler connections DATABASE (QueryProbe counts))]
                                   (in-transaction DATABASE key (run-statements DATABASE statements)))))))


(defn prepare-beside-a-writer [#^ str prefix prepare-of * writer-locked]  ; defk にできない: 用意を thread の run で回し、書きの代役を素の接続で流す入口
  "用意済みの置き場 prefix を、書きの代役が row_changes を持っている間にもう一度用意する(prepare-of = 接続の貸し出しと数え → 用意を 1 度
   流す関数)。書きの代役は、用意が待ち始めるか締め切りが来てから append_rows も取りに行く。
   #(用意の答え 用意が書きの代役を待ったか 用意が開いた transaction の数 書きの代役の失敗の SQLSTATE か None) を返す。"
  (setv connections (postgres-connections 2)
        counts (StatementCounts)
        writer (open-postgres)
        watcher (open-postgres)
        pool (ThreadPoolExecutor :max-workers 1)
        writer-failed None
        prepare (prepare-of connections counts prefix))
  (defn attempt []  ; defk にできない: thread の target
    (try
      (prepare)
      (except [error Exception]
        error)))
  (try
    (setv writer-pid (get (.fetchone (.execute writer "SELECT pg_backend_pid()")) 0))
    (.execute writer "BEGIN")
    (when writer-locked
      (.execute writer "SELECT pg_advisory_xact_lock(hashtext(%s))" #((run (writer-lock-key prefix)))))
    (.execute writer (.format "LOCK TABLE {}row_changes IN ROW EXCLUSIVE MODE" prefix))
    (setv preparing (.submit pool attempt))
    (setv deadline (+ (time.monotonic) BLOCK-DEADLINE))
    (while (and (= (blocked-by watcher writer-pid) 0) (not (.done preparing)) (< (time.monotonic) deadline))
      (time.sleep 0.02))
    (setv waited (> (blocked-by watcher writer-pid) 0))
    (try
      (.execute writer (.format "LOCK TABLE {}append_rows IN ROW EXCLUSIVE MODE" prefix))
      (.execute writer "COMMIT")
      (except [error Exception]
        (setv writer-failed (or (getattr error "sqlstate" None) (. (type error) __name__)))
        (.execute writer "ROLLBACK")))
    #((.result preparing 30) waited (len (lfor flush counts.flushes :if (= flush #("BEGIN")) flush)) writer-failed)
    (finally
      (.shutdown pool)
      (.close writer)
      (.close watcher)
      (.close connections))))


(defn with-prepared-store [check]  ; defk にできない: 検の置き場の組み立てと後片付け
  "使い捨ての置き場を用意して check に接頭辞を渡し、後で表を消す。"
  (setv connections (postgres-connections 1)
        store (prepared-store connections (fresh-prefix)))
  (try
    (check store.prefix)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(deftest test-a-prepare-waits-for-a-writer-holding-the-writer-lock-without-deadlock
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 入れ替えの間も答える別の process の書きの代役: 書きの錠の中で表を取る。用意は書きの錠を待ち、transaction 1 つで通る。
  (with-prepared-store
    (fn [prefix]
      (setv #(answer waited begins writer-failed) (prepare-beside-a-writer prefix new-prepare :writer-locked True))
      (assert (isinstance answer PreparedStore) (repr answer))
      (assert waited "用意が書きの錠を待たなかった")
      (assert (is writer-failed None) writer-failed)
      (assert (= begins 1) begins))))


(deftest test-a-prepare-of-a-prepared-store-takes-no-table-lock-beside-a-writer-outside-the-writer-lock
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 書きの錠の外の書き(旧い版の終わりの書きなど)が表を持っていても、用意済みの置き場の用意は表の lock を待たずに通る。
  (with-prepared-store
    (fn [prefix]
      (setv #(answer waited begins writer-failed) (prepare-beside-a-writer prefix new-prepare :writer-locked False))
      (assert (isinstance answer PreparedStore) (repr answer))
      (assert (not waited) "用意が書きの代役の表の lock を待った")
      (assert (is writer-failed None) writer-failed)
      (assert (= begins 1) begins))))


(deftest test-the-old-single-transaction-prepare-waits-for-the-table-locks-of-a-writer
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 反例: 旧い版の用意は、在る索引の CREATE INDEX IF NOT EXISTS でも表に ShareLock を取りに行き、同じ書きの代役を待つ(この待ちが
  ;; 書きの錠の外の書きとの deadlock の元で、置き場の書きを止める元)。
  (with-prepared-store
    (fn [prefix]
      (setv #(answer waited begins writer-failed) (prepare-beside-a-writer prefix old-prepare :writer-locked False))
      (assert waited "旧い版の用意が書きの代役を待たなかった"))))
