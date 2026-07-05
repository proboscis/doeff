;;; 直接束縛 deftest: SQLite store-of-record + writer actor + lease(C3)。
;;;
;;; store.hy の host 層物理 — migrate parity(oracle main.rs:974-1064 +
;;; effective_identity_json)・COALESCE 保護(:2354/:2360)・report_result の
;;; first-write-wins guarded UPDATE(:2174-2179)・awaiting_response latch
;;; clear(:591-596)・lease TTL/owner guard(:1094-1157/:3462)— を実 SQLite
;;; (tmpdir)で検証する。effect 束縛(sqlite-session-store)は StoreActor
;;; 経由で drive する。

(require doeff-hy.macros [deftest defk deff <-])

(import dataclasses [replace])
(import json)
(import os)
(import shutil)
(import sqlite3)
(import tempfile)

(import doeff [EffectBase])

(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  session-store-list-active
  session-store-get
  session-store-upsert
  session-store-result-payload
  session-store-record-event])
(import doeff_agents.sessionhost.store [
  StoreActor
  sqlite-session-store
  open-conn
  db-migrate
  db-upsert-snapshot
  db-session-get
  db-session-list
  db-count-active
  db-current-result-payload
  db-report-result-guarded-update
  db-clear-awaiting-latches
  db-read-lease
  db-upsert-lease
  db-acquire-lease
  db-heartbeat-once
  snapshot-to-policy-row
  snapshot-to-wire-dict])


;; ---------------------------------------------------------------------------
;; ヘルパ
;; ---------------------------------------------------------------------------

(setv EXPECTED-COLUMNS
      ["session_id" "session_name" "pane_id" "agent_type" "work_dir" "status"
       "backend_kind" "backend_ref_json" "started_at" "last_observed_at"
       "finished_at" "cleaned_at" "pr_url" "output_snippet"
       "terminal_cause_json" "lifecycle" "expected_result_json" "retries_used"
       "last_validation_error" "awaiting_response" "observed_active_at"
       "result_payload_json" "result_solicitations_used"
       "prompt_unblock_attempts" "last_output_change_at"
       "effective_identity_json"])


(defn make-snap [session-id #** overrides]
  "テスト用 snapshot dict(store-of-record の 1 行)。"
  (setv base {"session_id" session-id
              "session_name" f"name-{session-id}"
              "pane_id" "%1"
              "agent_type" "claude"
              "work_dir" "/tmp/w"
              "lifecycle" "run_to_completion"
              "status" "running"
              "backend_kind" "tmux"
              "backend_ref" {"session_name" f"name-{session-id}"
                             "pane_id" "%1"
                             "command" "claude"}
              "started_at" "2026-07-05T00:00:00+00:00"
              "last_observed_at" None
              "finished_at" None
              "cleaned_at" None
              "pr_url" None
              "output_snippet" None
              "terminal_cause" None
              "expected_result" None
              "retries_used" 0
              "last_validation_error" None
              "awaiting_response" False
              "observed_active_at" None
              "result_payload" None
              "result_solicitations_used" 0
              "prompt_unblock_attempts" 0
              "last_output_change_at" None
              "effective_identity" None})
  (.update base overrides)
  base)


(defn with-tmp-conn [thunk]
  "tmpdir の実 SQLite で thunk(conn を受ける)を回す。"
  (setv d (tempfile.mkdtemp))
  (try
    (setv conn (open-conn (os.path.join d "agentd.sqlite")))
    (try
      (db-migrate conn)
      (thunk conn)
      (finally (.close conn)))
    (finally (shutil.rmtree d :ignore-errors True))))


;; ---------------------------------------------------------------------------
;; migrate parity
;; ---------------------------------------------------------------------------

(deftest test-migrate-schema-parity
  (defn check [conn]
    ;; 4 テーブル + index が生えている
    (setv tables (sfor row (.fetchall (.execute conn
                                                "SELECT name FROM sqlite_master WHERE type = 'table'"))
                       (get row 0)))
    (for [table ["agent_sessions" "agent_session_events"
                 "agent_session_commands" "agent_daemon_lease"]]
      (assert (in table tables) f"missing table {table}"))
    ;; agent_sessions の全列(oracle 25 列 + effective_identity_json)
    (setv columns (lfor row (.fetchall (.execute conn "PRAGMA table_info(agent_sessions)"))
                        (get row 1)))
    (assert (= (sorted columns) (sorted EXPECTED-COLUMNS))
            f"column mismatch: {(sorted columns)}")
    ;; idempotent(2 回目の migrate が例外を出さない)
    (db-migrate conn))
  (with-tmp-conn check))


;; ---------------------------------------------------------------------------
;; COALESCE 保護 + first-write-wins
;; ---------------------------------------------------------------------------

(deftest test-upsert-coalesce-protection
  (defn check [conn]
    ;; payload を書いた後、payload None の upsert(monitor の定期書き戻し)が
    ;; 来ても消えない(result_payload_json COALESCE)
    (db-upsert-snapshot conn (make-snap "s1" :result_payload "{\"ok\":true}"))
    (db-upsert-snapshot conn (make-snap "s1" :status "running"))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "result_payload") "{\"ok\":true}"))
    (assert (= (get snap "status") "running"))
    ;; terminal_cause も first-write-wins
    (db-upsert-snapshot conn (make-snap "s1" :terminal_cause
                                        {"category" "run_failed"
                                         "retryable" False
                                         "observed_at" "2026-07-05T00:01:00+00:00"}))
    (db-upsert-snapshot conn (make-snap "s1" :terminal_cause
                                        {"category" "lost"
                                         "retryable" True
                                         "observed_at" "2026-07-05T00:02:00+00:00"}))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get (get snap "terminal_cause") "category") "run_failed"))
    ;; effective_identity も一度書いたら upsert では消えない(C3 拡張)
    (db-upsert-snapshot conn (make-snap "s2" :effective_identity
                                        {"CODEX_HOME" "/tmp/codex-home"}))
    (db-upsert-snapshot conn (make-snap "s2" :effective_identity None))
    (setv snap (db-session-get conn "s2"))
    (assert (= (get snap "effective_identity") {"CODEX_HOME" "/tmp/codex-home"})))
  (with-tmp-conn check))


(deftest test-report-result-guarded-update
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "s1"))
    ;; 初回 = 書ける
    (assert (= (db-report-result-guarded-update conn "s1" "{\"n\":1}") 1))
    (assert (= (db-current-result-payload conn "s1") "{\"n\":1}"))
    ;; 2 回目 = no-op(first-write-wins)
    (assert (= (db-report-result-guarded-update conn "s1" "{\"n\":2}") 0))
    (assert (= (db-current-result-payload conn "s1") "{\"n\":1}"))
    ;; 終端 status には書けない
    (db-upsert-snapshot conn (make-snap "s2" :status "failed"))
    (assert (= (db-report-result-guarded-update conn "s2" "{\"n\":3}") 0))
    (assert (is (db-current-result-payload conn "s2") None)))
  (with-tmp-conn check))


;; ---------------------------------------------------------------------------
;; restart 意味論: awaiting_response latch clear
;; ---------------------------------------------------------------------------

(deftest test-awaiting-latch-clear
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "active" :awaiting_response True
                                        :status "running"))
    (db-upsert-snapshot conn (make-snap "terminal" :awaiting_response True
                                        :status "done"))
    (db-clear-awaiting-latches conn)
    ;; 非終端の latch だけが破棄される(oracle main :591-596)
    (assert (= (get (db-session-get conn "active") "awaiting_response") False))
    (assert (= (get (db-session-get conn "terminal") "awaiting_response") True)))
  (with-tmp-conn check))


;; ---------------------------------------------------------------------------
;; lease(TTL・owner guard・SIGTERM 非解放と同型の再取得物理)
;; ---------------------------------------------------------------------------

(deftest test-lease-acquire-and-heartbeat
  (defn check [conn]
    ;; 空 → 取得成功
    (db-acquire-lease conn 111)
    (setv lease (db-read-lease conn))
    (assert (= (get lease "owner_pid") 111))
    ;; TTL 内の他 pid → 拒否("lease is active")
    (setv raised None)
    (try
      (db-acquire-lease conn 222)
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None))
    (assert (in "lease is active" (str raised)))
    ;; heartbeat: owner 一致 → 更新される
    (db-heartbeat-once conn 111)
    ;; owner 交代 → raise(worker tick が log して次 tick へ)
    (setv raised None)
    (try
      (db-heartbeat-once conn 999)
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None))
    (assert (in "owner changed" (str raised)))
    ;; 失効 lease は他 pid が取得できる(restart() の TTL 待ち物理)
    (.execute conn
              "UPDATE agent_daemon_lease SET expires_at = '2000-01-01T00:00:00+00:00'")
    (db-acquire-lease conn 222)
    (assert (= (get (db-read-lease conn) "owner_pid") 222)))
  (with-tmp-conn check))


;; ---------------------------------------------------------------------------
;; effect 束縛(StoreActor + sqlite-session-store)
;; ---------------------------------------------------------------------------

(defk drive-store [actor op]
  {:pre [(: actor StoreActor) (: op EffectBase)]
   :post [(: % "effect の store 解釈結果")]}
  "sqlite-session-store で 1 effect を回す最小ドライバ。"
  (<- result ((sqlite-session-store actor) op))
  result)


(deftest test-sqlite-session-store-effects
  (setv d (tempfile.mkdtemp))
  (try
    (setv actor (StoreActor (os.path.join d "agentd.sqlite")))
    (try
      ;; policy row の upsert で full 行が生える(work-dir / backend-ref 込み)
      (setv row (SessionRow :session-id "s1"
                            :session-name "n1"
                            :pane-id "%1"
                            :agent-type "claude"
                            :lifecycle "run_to_completion"
                            :status "booting"
                            :started-at "2026-07-05T00:00:00+00:00"
                            :work-dir "/tmp/w"
                            :backend-ref {"session_name" "n1"
                                          "pane_id" "%1"
                                          "command" "claude --yolo"}))
      (<- _ (drive-store actor (session-store-upsert row)))
      (<- active (drive-store actor (session-store-list-active)))
      (assert (= (len active) 1))
      (assert (= (. (get active 0) session-id) "s1"))
      (assert (= (. (get active 0) work-dir) "/tmp/w"))
      ;; get で policy row として読める
      (<- got (drive-store actor (session-store-get "s1")))
      (assert (= got.status "booting"))
      (assert (= (get got.backend-ref "command") "claude --yolo"))
      ;; result payload の fresh read(report_result は別経路で書く)
      (<- missing (drive-store actor (session-store-result-payload "s1")))
      (assert (is missing None))
      (.submit actor
               (fn [conn] (db-report-result-guarded-update conn "s1" "{\"ok\":true}")))
      (<- payload (drive-store actor (session-store-result-payload "s1")))
      (assert (= payload "{\"ok\":true}"))
      ;; monitor の書き戻し(payload None の row)が payload を消さない
      (<- fresh (drive-store actor (session-store-get "s1")))
      (setv updated (replace fresh :status "running" :result-payload None))
      (<- _ (drive-store actor (session-store-upsert updated)))
      (<- after (drive-store actor (session-store-result-payload "s1")))
      (assert (= after "{\"ok\":true}"))
      ;; 終端遷移 + event 記録(payload は wire 形 snapshot)
      (<- final (drive-store actor (session-store-get "s1")))
      (setv done-row (replace final :status "done"))
      (<- _ (drive-store actor (session-store-upsert done-row)))
      (<- _ (drive-store actor (session-store-record-event "s1" "session_done" done-row)))
      (setv events (.submit actor
                            (fn [conn]
                              (.fetchall (.execute conn
                                                   "SELECT event_type, payload_json FROM agent_session_events ORDER BY id")))))
      (assert (= (len events) 1))
      (assert (= (get (get events 0) 0) "session_done"))
      (setv payload-json (json.loads (get (get events 0) 1)))
      (assert (= (get payload-json "session_id") "s1"))
      (assert (= (get payload-json "status") "done"))
      (assert (= (get payload-json "result_payload") "{\"ok\":true}"))
      ;; 終端になったので active 一覧から消える(level-triggered 再読の面)
      (<- active2 (drive-store actor (session-store-list-active)))
      (assert (= (len active2) 0))
      (finally (.close actor)))
    (finally (shutil.rmtree d :ignore-errors True))))


(deftest test-wire-dict-skip-serializing-parity
  ;; serde skip_serializing_if parity: None の optional field は key ごと省略
  (setv snap (make-snap "s1"))
  (setv wire (snapshot-to-wire-dict snap))
  (for [key ["terminal_cause" "expected_result" "last_validation_error"
             "observed_active_at" "result_payload" "last_output_change_at"
             "effective_identity"]]
    (assert (not-in key wire) f"{key} should be omitted when None"))
  ;; 非 skip の Option は null で残る(serde: last_observed_at 等)
  (for [key ["last_observed_at" "finished_at" "cleaned_at" "pr_url"
             "output_snippet"]]
    (assert (in key wire) f"{key} must stay as null"))
  ;; 値が入れば field が現れる
  (setv snap2 (make-snap "s2" :result_payload "{}"
                         :effective_identity {"CODEX_HOME" "/x"}))
  (setv wire2 (snapshot-to-wire-dict snap2))
  (assert (= (get wire2 "result_payload") "{}"))
  (assert (= (get wire2 "effective_identity") {"CODEX_HOME" "/x"})))
