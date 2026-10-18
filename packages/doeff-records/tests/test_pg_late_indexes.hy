;; 後から作る索引(宣言から作る索引 — pg_sql.hy の頭の註の「後から作る索引」)を、育った置き場でも起動を止めずに作る(2026-10-18 の記録の
;; service の障害 — 索引の欄 role を足した版の表の用意で、CREATE INDEX が statement timeout の 60 秒を越えて毎回落ち、起動が 1 時間止まった)。
;; 作りを遅くする代役 = 別の接続が state_rows に行を書きかけたまま(RowExclusiveLock を持つ transaction)、文の上限より長く待たせる
;; (CREATE INDEX は CONCURRENTLY でもそうでなくても、表を書きかけの transaction の終わりを待つ)。文の上限は検の短い 1 秒にする。
;; 性質 1: 索引の欄を足した宣言の用意は、作りが文の上限より長くても通り、索引は使える(作りは文の上限を付けない作りの 1 本 BUILDER)。
;;         作る間も同じ表への別の書きは止まらない(CONCURRENTLY は書きとぶつかる ShareLock を取らない)。
;;   反例: 旧い版の用意(表の用意の文の全部を 1 つの transaction で)は同じ代役の下で statement timeout(57014)で落ちる。
;; 性質 2: 作り手が落ちて残った作りかけの索引(INVALID)は、次の用意が消して作り直す。
;;   反例: 旧い版の用意は CREATE INDEX IF NOT EXISTS が名を見て何もせず、作りかけのまま残す。
;; 性質 3: 同じ表の作り手の錠を別の process が持つ間、用意は自分で作らずに待ち、作り手の合図(NOTIFY)で起きて、作り手の索引を使う。
;; 性質 4: 行の在る置き場に索引の欄を足した宣言を、別の process の代役 RACERS 本が同時に用意しても、どれも断られず、deadlock も起きない
;;         (表ごとの作り手の錠 — 同じ表に 2 本の CONCURRENTLY が重なる・PostgreSQL の錠で作り手を待つと deadlock になる)。
;; 空の表の索引は表の用意の transaction が作る(作りは一瞬)ので、どの検も置き場に行を書いてから用意する。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest val])
(import concurrent.futures [ThreadPoolExecutor])
(import dataclasses [replace])
(import pytest)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [PostgresConnections PostgresDatabase DEFAULT-TIMEOUTS postgres-sql-handler])
(import doeff_core_effects.sql_effects [SqlNotify])
(import doeff_hy.frozen [FrozenMap])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.pg [drop-records-tables prepare-records-store run-statements in-transaction prefix-channel build-topic
                          PreparedStore RecordsSqlFailed])
(import doeff_records.pg_sql [schema-statements migrate-lock-key index-name field-index late-index-statement build-lock-key])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE BUILDER open-postgres postgres-connections builder-connections
                            fresh-prefix run-sql prepared-store])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
(val PG-SKIP-REASON (pg-skip-reason))
;; 本物の PostgreSQL の lock の待ちと文の上限の実時間が対象。
(val pytestmark pytest.mark.realtime)
;; 検の文の上限(ミリ秒)と、書きかけの代役が作りを待たせる秒(上限より長い)。
(val STATEMENT-MILLISECONDS 1000)
(val HOLD-SECONDS 2.0)
;; 用意が待ち始める・終わるのを見る締め切り(秒)。
(val DEADLINE 15)
;; 足す索引の欄(表 parts — 宣言に無い欄 note を索引の欄にする)。
(val ADDED-FIELD "note")


(defn noted-schema []  ; defk にできない: 検の値の組み立て(Program の外で宣言を作る)
  "法の宣言の表 parts の索引の欄に ADDED-FIELD を足した宣言(索引の欄を足した版の代役)。"
  (setv parts (get LAW-SCHEMA.tables "parts"))
  (replace LAW-SCHEMA :tables (FrozenMap (| (dict LAW-SCHEMA.tables)
                                            {"parts" (replace parts :indexes (+ parts.indexes #(ADDED-FIELD)))}))))


(defn bounded-connections []  ; defk にできない: 検の資源の組み立て
  "文の上限を STATEMENT-MILLISECONDS にした置き場の接続の貸し出し(本番の 60 秒の代役)。"
  (PostgresConnections #((PostgresDatabase :name DATABASE :dsn PG-DSN)) :size 2
                       :timeouts (replace DEFAULT-TIMEOUTS :statement-milliseconds STATEMENT-MILLISECONDS)))


(defn new-prepare [connections schema #^ str prefix]  ; defk にできない: thread の run で回す入口
  "今の版の用意を 1 度流す(置き場の接続 connections と、後から作る索引の作りの 1 本 BUILDER の下で)。"
  (setv builder (builder-connections))
  (try
    (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler builder) (postgres-sql-handler connections)]
                                   (prepare-records-store DATABASE BUILDER schema prefix))))
    (finally (.close builder))))


(defn old-prepare [connections schema #^ str prefix]  ; defk にできない: thread の run で回す入口
  "旧い版の用意(表の用意の文の全部を移行の錠の transaction 1 つで — 頭の註の反例)を 1 度流す。"
  (setv statements (run (schema-statements prefix schema))
        key (run (migrate-lock-key prefix)))
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)]
                                 (in-transaction DATABASE key (run-statements DATABASE statements))))))


(defn outcome-of [thunk]  ; defk にできない: thread の target
  "thunk を流し、答えか上がった例外を返す。"
  (try
    (thunk)
    (except [error Exception]
      error)))


(defn index-row [connection #^ str name]  ; defk にできない: 検の素の接続で読む
  "索引 name の (oid, 使えるか, 一意か)(無ければ None)。"
  (.fetchone (.execute connection "SELECT indexrelid::bigint, indisvalid, indisunique FROM pg_index WHERE indexrelid = to_regclass(%s)"
                       #(name))))


(defn wait-until [check]  ; defk にできない: 検の素の接続の待ち
  "check が真になるまで DEADLINE 秒まで待つ(真になったか)。"
  (setv deadline (+ (time.monotonic) DEADLINE))
  (while (and (not (check)) (< (time.monotonic) deadline))
    (time.sleep 0.02))
  (check))


(defn row-insert [#^ str prefix #^ str ledger #^ str key]  ; defk にできない: 検の素の接続の文
  "state_rows に行 1 つを直に書く文と引数(書きの代役)。"
  #((.format "INSERT INTO {}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
               VALUES (%s, %s, '{{}}', 1, 0, 'w', 'h', 1)" prefix)
    #(ledger key)))


(defn with-prepared-store [check]  ; defk にできない: 検の置き場の組み立てと後片付け
  "使い捨ての置き場を法の宣言で用意し、state_rows に行を 1 つ書いて(育った置き場の代役 — 空の表の索引は transaction の中で作るので)
   check に接頭辞を渡し、後で表を消す。"
  (setv connections (postgres-connections 1)
        store (prepared-store connections (fresh-prefix)))
  (try
    (with [connection (open-postgres)]
      (.execute connection #* (row-insert store.prefix "parts" "grown")))
    (check store.prefix)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(defn prepare-while-a-row-is-being-written [#^ str prefix prepare]  ; defk にできない: 用意を thread の run で回し、書きの代役を素の接続で流す入口
  "書きの代役が state_rows に行を書きかけたまま HOLD-SECONDS 秒待つ間に、宣言 noted-schema の用意 prepare を流す。作る索引が catalog に
   現れたら(作りが始まったら)、別の書きを 1 つ流して止まらずに済むかを見る。#(用意の答え 別の書きが済んだか) を返す。"
  (setv connections (bounded-connections)
        holder (open-postgres)
        other (open-postgres)
        pool (ThreadPoolExecutor :max-workers 1)
        added (run (index-name prefix ADDED-FIELD)))
  (try
    (.execute holder "BEGIN")
    (.execute holder #* (row-insert prefix "parts" "held"))
    (setv started (time.monotonic)
          preparing (.submit pool outcome-of (fn [] (prepare connections (noted-schema) prefix))))
    (setv other-done False)
    (when (wait-until (fn [] (or (is-not (index-row other added) None) (.done preparing))))
      (when (not (.done preparing))
        ;; 作りの最中の別の書き(文の上限つき — 止まれば 57014 で落ちる)。
        (.execute other (.format "SET statement_timeout = {}" STATEMENT-MILLISECONDS))
        (.execute other #* (row-insert prefix "parts" "other"))
        (setv other-done True)))
    (time.sleep (max 0 (- HOLD-SECONDS (- (time.monotonic) started))))
    (.execute holder "COMMIT")
    #((.result preparing DEADLINE) other-done)
    (finally
      (.shutdown pool)
      (.close holder)
      (.close other)
      (.close connections))))


(deftest test-an-added-declared-index-is-built-past-the-statement-timeout-without-stopping-writes
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (with-prepared-store
    (fn [prefix]
      (setv #(answer other-done) (prepare-while-a-row-is-being-written prefix new-prepare))
      (assert (isinstance answer PreparedStore) (repr answer))
      (assert other-done "作りの最中の別の書きが流れなかった")
      (with [watcher (open-postgres)]
        (setv row (index-row watcher (run (index-name prefix ADDED-FIELD)))))
      (assert (and (is-not row None) (get row 1)) (repr row)))))


(deftest test-the-old-single-transaction-prepare-falls-to-the-statement-timeout
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 反例: 旧い版の用意は、書きかけの代役の終わりを文の上限の内に待ち切れず 57014 で落ちる(本番の障害と同じ落ち方)。
  (with-prepared-store
    (fn [prefix]
      (setv #(answer _) (prepare-while-a-row-is-being-written prefix old-prepare))
      (assert (and (isinstance answer RecordsSqlFailed) (= answer.sqlstate "57014")) (repr answer)))))


(defn abandon-a-build [#^ str prefix #^ str name]  ; defk にできない: 検の素の接続の文
  "索引 name を消し、同じ名の作りを落として作りかけ(INVALID)を残す(作り手が落ちた代役 — 一意の索引を同じ ledger の 2 行の上に作る)。"
  (with [connection (open-postgres)]
    (.execute connection (.format "DROP INDEX {}" name))
    (.execute connection #* (row-insert prefix "parts" "a"))
    (.execute connection #* (row-insert prefix "parts" "b"))
    (try
      (.execute connection (.format "CREATE UNIQUE INDEX CONCURRENTLY {} ON {}state_rows (ledger)" name prefix))
      (except [Exception]
        None))
    (index-row connection name)))


(deftest test-an-abandoned-build-is-dropped-and-built-again
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (with-prepared-store
    (fn [prefix]
      (setv name (run (index-name prefix "label"))
            abandoned (abandon-a-build prefix name)
            connections (postgres-connections 1))
      (try
        (assert (and (is-not abandoned None) (not (get abandoned 1))) (repr abandoned))
        ;; 反例: 旧い版の用意は名を見て何もせず、作りかけのまま残す。
        (old-prepare connections LAW-SCHEMA prefix)
        (with [watcher (open-postgres)]
          (assert (= (index-row watcher name) abandoned) "旧い版の用意が作りかけに触った"))
        (setv answer (new-prepare connections LAW-SCHEMA prefix))
        (assert (isinstance answer PreparedStore) (repr answer))
        (with [watcher (open-postgres)]
          (setv rebuilt (index-row watcher name)))
        (assert (and (is-not rebuilt None) (get rebuilt 1) (not (get rebuilt 2))) (repr rebuilt))
        (finally (.close connections))))))


(deftest test-a-prepare-waits-for-the-builder-holding-the-table-and-uses-its-index
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (with-prepared-store
    (fn [prefix]
      (setv index (run (field-index prefix "label"))
            create (run (late-index-statement index True))
            connections (postgres-connections 1)
            other (open-postgres)
            pool (ThreadPoolExecutor :max-workers 1))
      (try
        (.execute other (.format "DROP INDEX {}" index.name))
        ;; 別の process の作り手の代役: 表 state_rows の作り手の錠を持つ。
        (assert (get (.fetchone (.execute other "SELECT pg_try_advisory_lock(hashtext(%s))" #((run (build-lock-key index))))) 0))
        (setv preparing (.submit pool outcome-of (fn [] (new-prepare connections LAW-SCHEMA prefix))))
        (time.sleep 1.0)
        (assert (not (.done preparing)) (repr (.result preparing)))
        (assert (is (index-row other index.name) None) "用意が作り手の錠を持たずに作った")
        (.execute other create.text)
        (setv built (index-row other index.name))
        (.execute other "SELECT pg_advisory_unlock(hashtext(%s))" #((run (build-lock-key index))))
        ;; 作り手の合図(本番の作り手と同じ SqlNotify — 別の接続の貸し出しなので別の process の合図として届く)。
        (setv notifier (postgres-connections 1))
        (try
          (run (scheduled (with_handlers [(postgres-sql-handler notifier)]
                                         (SqlNotify DATABASE (run (prefix-channel prefix)) #((run (build-topic index)))))))
          (finally (.close notifier)))
        (setv answer (.result preparing DEADLINE))
        (assert (isinstance answer PreparedStore) (repr answer))
        (assert (= (index-row other index.name) built) "用意が作り手の索引を作り直した")
        (finally
          (.shutdown pool)
          (.close other)
          (.close connections))))))


(val RACERS 4)
(val ROUNDS 3)


(defn race-to-add-the-index [#^ str prefix]  ; defk にできない: RACERS 本の thread でそれぞれ別の run を回す入口
  "RACERS 本(別の process の代役 — 接続の貸し出しも作りの 1 本も別)が同時に noted-schema を用意し、上がった例外の列を返す。"
  (import threading)
  (setv start (threading.Barrier RACERS)
        raised [])
  (defn attempt []  ; defk にできない: thread の target
    (setv connections (postgres-connections 2))
    (.wait start)
    (try
      (setv answer (outcome-of (fn [] (new-prepare connections (noted-schema) prefix))))
      (when (not (isinstance answer PreparedStore))
        (.append raised answer))
      (finally (.close connections))))
  (setv threads (lfor _ (range RACERS) (threading.Thread :target attempt)))
  (for [t threads] (.start t))
  (for [t threads] (.join t))
  raised)


(deftest test-concurrent-prepares-adding-an-index-build-it-once-without-deadlock
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (with [watcher (open-postgres)]
    (defn deadlocks []  ; defk にできない: 検の素の接続で数える
      (get (.fetchone (.execute watcher "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")) 0))
    (setv before (deadlocks))
    (for [_ (range ROUNDS)]
      (with-prepared-store
        (fn [prefix]
          (setv raised (race-to-add-the-index prefix))
          (assert (= raised []) (repr raised))
          (setv row (index-row watcher (run (index-name prefix ADDED-FIELD))))
          (assert (and (is-not row None) (get row 1)) (repr row)))))
    (assert (= (deadlocks) before) (repr #(before (deadlocks))))))
