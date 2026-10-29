;; 表の用意(移行)と同じ置き場の書きが重なっても、用意が deadlock(40P01)で起動を止めない(2026-10-16 — 新しい版の用意が、入れ替えの
;; 間も同じ置き場に答えた別の process の書きと逆の順で表の lock を取り、1 回目の起動が落ちた)。
;; 用意の CREATE INDEX は表に ShareLock を取って transaction の終わりまで持ち、書きは行の表に RowExclusiveLock を取る。
;; 書きの代役 = 別の接続で row_changes に RowExclusiveLock を取り、用意がそこで待ち始めてから append_rows(用意が先に ShareLock を
;; 取った表)にも RowExclusiveLock を取りに行く。
;; 性質: 書きが書きの錠の中なら、用意は書きの錠を待ち(表の lock を持たずに)、deadlock にならず transaction 1 つで通る。
;; 性質(card ki-954c58540d0f): 既に在る置き場の用意は既に在る表の索引の文を流さない(足りない索引は索引の用意の task が CONCURRENTLY で
;; 作る — pg.hy の頭の註)ので、表の lock を取らない。書きの錠を取らない書き(錠の外の相手)が 3 つの表に RowExclusiveLock を持ったままでも、
;; 用意は待たずに transaction 1 つで通る(前の版はここで deadlock になり、40P01 の流し直しで通っていた)。
;; 反例: 既に在る表へ移行の transaction の中で CREATE INDEX IF NOT EXISTS を流す前の形(空の置き場の文の全部 — schema-statements)は、
;; 同じ書きの代役を待つ(「待たない」の判定が何も確かめずに緑になる形を外す)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [defk deftest val <-])
(import concurrent.futures [ThreadPoolExecutor])
(import pytest)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.pg [drop-records-tables prepare-records-store PreparedStore])
(import doeff_records.pg_sql [writer-lock-key migrate-lock-key schema-statements])
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


(defn prepare-against-a-writer [#^ str prefix]  ; defk にできない: 用意を thread の run で回し、書きの代役を素の接続で流す入口
  "用意済みの置き場 prefix を、書きの錠の中の書きの代役が row_changes を持っている間にもう一度用意する。書きの代役は用意が待ち始めてから
   append_rows も取りに行く。#(用意の答え 用意が開いた transaction の数 書きの代役の失敗の SQLSTATE か None) を返す。"
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
                                     (prepare-records-store DATABASE DATABASE LAW-SCHEMA prefix))))
      (except [error Exception]
        error)))
  (try
    (setv writer-pid (get (.fetchone (.execute writer "SELECT pg_backend_pid()")) 0))
    (.execute writer "BEGIN")
    (.execute writer "SELECT pg_advisory_xact_lock(hashtext(%s))" #((run (writer-lock-key prefix))))
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


(defn prepare-beside-a-writer [#^ str prefix #^ object program]  ; defk にできない: 用意を thread の run で回し、書きの代役を素の接続で流す入口
  "書きの錠を取らない書きの代役が置き場 prefix の 3 つの表に RowExclusiveLock を持っている間に program(用意)を流す。program が終わるか
   代役を待ち始めるまで待ち、代役を commit してから #(program の答え 代役を待ち始めた本数) を返す。"
  (setv connections (postgres-connections 2)
        writer (open-postgres)
        watcher (open-postgres)
        pool (ThreadPoolExecutor :max-workers 1))
  (defn prepare []  ; defk にできない: thread の target
    (try
      (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock))
                                      (probe-sql-handler connections DATABASE (QueryProbe (StatementCounts)))]
                                     program)))
      (except [error Exception]
        error)))
  (try
    (setv writer-pid (get (.fetchone (.execute writer "SELECT pg_backend_pid()")) 0))
    (.execute writer "BEGIN")
    (for [table #("state_rows" "append_rows" "row_changes")]
      (.execute writer (.format "LOCK TABLE {}{} IN ROW EXCLUSIVE MODE" prefix table)))
    (setv preparing (.submit pool prepare))
    (setv deadline (+ (time.monotonic) 10))
    (while (and (not (.done preparing)) (= (blocked-by watcher writer-pid) 0) (< (time.monotonic) deadline))
      (time.sleep 0.02))
    (setv blocked (blocked-by watcher writer-pid))
    (.execute writer "COMMIT")
    #((.result preparing 30) blocked)
    (finally
      (.shutdown pool)
      (.close writer)
      (.close watcher)
      (.close connections))))


(defk statements-in-order [statements]
  {:pre [(: statements tuple)] :post [(: % None)] :tags {:context "records" :role "program"}}
  "前の形の用意の文を順に流すため(反例 — 移行の transaction の中身)。"
  (for [statement statements]
    (<- (SqlQuery DATABASE statement.text statement.params)))
  None)


(defk prepared-before-the-fix [prefix]
  {:pre [(: prefix str)] :post [(: % "SqlTransaction の答え")] :tags {:context "records" :role "program"}}
  "前の形の用意(反例): 空の置き場の文の全部を、既に在る表にも移行の錠の transaction の中で流すため。"
  (<- statements (schema-statements prefix LAW-SCHEMA))
  (<- key (migrate-lock-key prefix))
  (<- answer (SqlTransaction DATABASE (statements-in-order statements) :lock-key key))
  answer)


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
      (setv #(answer begins writer-failed) (prepare-against-a-writer prefix))
      (assert (isinstance answer PreparedStore) (repr answer))
      (assert (is writer-failed None) writer-failed)
      (assert (= begins 1) begins))))


(deftest test-a-prepare-of-a-store-takes-no-table-lock-beside-a-writer-outside-the-writer-lock
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 書きの錠の外の書きが 3 つの表を持っている間も、既に在る置き場の用意は待たずに通る(頭の註の性質)。
  (with-prepared-store
    (fn [prefix]
      (setv #(answer blocked) (prepare-beside-a-writer prefix (prepare-records-store DATABASE DATABASE LAW-SCHEMA prefix)))
      (assert (isinstance answer PreparedStore) (repr answer))
      (assert (= blocked 0) blocked)
      (assert (= answer.indexes.missing #()) (repr answer.indexes.missing)))))


(deftest test-the-form-before-the-fix-waits-for-the-same-writer
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 反例: 既に在る表へ transaction の中で CREATE INDEX IF NOT EXISTS を流す前の形は、同じ書きの代役を待つ。
  (with-prepared-store
    (fn [prefix]
      (setv #(_answer blocked) (prepare-beside-a-writer prefix (prepared-before-the-fix prefix)))
      (assert (= blocked 1) blocked))))
