;; 表の用意(移行)と同じ置き場の書きが重なっても、用意が deadlock(40P01)で起動を止めない(2026-10-16 — 新しい版の用意が、入れ替えの
;; 間も同じ置き場に答えた別の process の書きと逆の順で表の lock を取り、1 回目の起動が落ちた)。
;; 用意の CREATE INDEX は表に ShareLock を取って transaction の終わりまで持ち、書きは行の表に RowExclusiveLock を取る。
;; 書きの代役 = 別の接続で row_changes に RowExclusiveLock を取り、用意がそこで待ち始めてから append_rows(用意が先に ShareLock を
;; 取った表)にも RowExclusiveLock を取りに行く。
;; 性質: 書きが書きの錠の中なら、用意は書きの錠を待ち(表の lock を持たずに)、deadlock にならず transaction 1 つで通る。
;; 反例: 書きの錠を取らない書き(錠の外の相手)とは deadlock になる — 用意は 40P01 で戻された transaction を流し直して通る
;; (「deadlock にならない」の判定が何も確かめずに緑になる形を外す)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest val])
(import concurrent.futures [ThreadPoolExecutor])
(import pytest)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.pg [drop-records-tables prepare-records-store PreparedStore])
(import doeff_records.pg_sql [writer-lock-key])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE open-postgres postgres-connections fresh-prefix run-sql
                            prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
(val PG-SKIP-REASON (pg-skip-reason))
;; 本物の PostgreSQL の lock の待ちと deadlock の見つけ(deadlock_timeout)の実時間が対象(用意が書きの代役を待ち始めるのを締め切りつきで待つ)。
(val pytestmark pytest.mark.realtime)


(defn blocked-by [watcher #^ int pid]  ; defk にできない: 検の素の接続で数える
  "接続 pid の lock を待っている接続の数。"
  (get (.fetchone (.execute watcher "SELECT count(*) FROM pg_stat_activity WHERE %s = ANY(pg_blocking_pids(pid))" #(pid))) 0))


(defn prepare-against-a-writer [#^ str prefix * writer-locked]  ; defk にできない: 用意を thread の run で回し、書きの代役を素の接続で流す入口
  "用意済みの置き場 prefix を、書きの代役が row_changes を持っている間にもう一度用意する。書きの代役は用意が待ち始めてから append_rows も
   取りに行く。#(用意の答え 用意が開いた transaction の数 書きの代役の失敗の SQLSTATE か None) を返す。"
  (setv connections (postgres-connections 2)
        counts (StatementCounts)
        writer (open-postgres)
        watcher (open-postgres)
        pool (ThreadPoolExecutor :max-workers 1)
        writer-failed None)
  (defn prepare []  ; defk にできない: thread の target
    (try
      (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock))
                                      (probe-sql-handler connections DATABASE (QueryProbe counts))]
                                     (prepare-records-store DATABASE LAW-SCHEMA prefix))))
      (except [error Exception]
        error)))
  (try
    (setv writer-pid (get (.fetchone (.execute writer "SELECT pg_backend_pid()")) 0))
    (.execute writer "BEGIN")
    (when writer-locked
      (.execute writer "SELECT pg_advisory_xact_lock(hashtext(%s))" #((run (writer-lock-key prefix)))))
    (.execute writer (.format "LOCK TABLE {}row_changes IN ROW EXCLUSIVE MODE" prefix))
    (setv preparing (.submit pool prepare))
    (setv deadline (+ (time.monotonic) 10))
    (while (and (= (blocked-by watcher writer-pid) 0) (< (time.monotonic) deadline))
      (time.sleep 0.02))
    (assert (= (blocked-by watcher writer-pid) 1) "用意が書きの代役を待ち始めなかった")
    (try
      (.execute writer (.format "LOCK TABLE {}append_rows IN ROW EXCLUSIVE MODE" prefix))
      (.execute writer "COMMIT")
      (except [error Exception]
        (setv writer-failed (or (getattr error "sqlstate" None) (. (type error) __name__)))
        (.execute writer "ROLLBACK")))
    #((.result preparing 30) (len (lfor flush counts.flushes :if (= flush #("BEGIN")) flush)) writer-failed)
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
      (setv #(answer begins writer-failed) (prepare-against-a-writer prefix :writer-locked True))
      (assert (isinstance answer PreparedStore) (repr answer))
      (assert (is writer-failed None) writer-failed)
      (assert (= begins 1) begins))))


(deftest test-a-prepare-retries-the-deadlock-with-a-writer-outside-the-writer-lock
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 反例と流し直し: 書きの錠の外の書きとは表の lock が逆の順になり deadlock になる。PostgreSQL は先に待った方(用意)を戻し、
  ;; 用意は transaction を流し直して通る。
  (with-prepared-store
    (fn [prefix]
      (setv #(answer begins writer-failed) (prepare-against-a-writer prefix :writer-locked False))
      (assert (isinstance answer PreparedStore) (repr answer))
      (assert (or (= writer-failed "40P01") (= begins 2)) (repr #(begins writer-failed)))
      (assert (or (is writer-failed None) (= writer-failed "40P01")) writer-failed))))
