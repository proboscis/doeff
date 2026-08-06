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
(import datetime [datetime timezone])
(import json)
(import os)
(import shutil)
(import sqlite3)
(import tempfile)

(import doeff_agents.sessionhost.policy [parse-iso])

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
  db-known-conversation-ids
  db-report-result-guarded-update
  db-clear-awaiting-latches
  db-prune-history
  db-vacuum-if-bloated
  actor-prune-history
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
       "effective_identity_json"
       "conversation_json" "generation"
       "resumed_from_session_id" "forked_from_session_id"
       "launch_overlay_json"
       ;; koine session surface v0 stage 1(ADR-DOE-AGENTS-007): ownership
       ;; marker + turn 打刻。adopted は安全条項 1 の opt-in/fail-closed の
       ;; 機械面、turn_* は席の自己申告打刻(wait は opaque 保存)。
       "adopted" "turn_holder" "turn_since" "turn_wait_json"
       ;; issue #557: attempt 中の api-limit 観測の durable latch(初回観測
       ;; 時刻、first-write-wins)。terminal 時 tail-30 は racy — 終端分類は
       ;; この latch を参照して rate_limited/retryable=true へ蒸留する。
       "api_limit_observed_at"
       ;; ADR-DOE-AGENTS-009: 観測断(supply cut)の最終検出時刻。stale
       ;; watchdog はもう terminal 化せずここへ刻印し、launch-timeout の
       ;; watch 窓は max(started_at, observation_gap_at) を基点に再スタート
       ;; する。last-write-wins + None 保護(COALESCE(excluded, existing))。
       "observation_gap_at"
       ;; issue #568(ADR-DOE-AGENTS-010): paste 再送 budget の durable
       ;; counter + awaiting latch の期限基点。
       "paste_resubmit_attempts" "awaiting_response_since"])


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


(deftest test-api-limit-latch-roundtrip-and-coalesce
  ;; issue #557: durable latch は行に往復し、stale な None 書き戻しでも
  ;; 消えない(COALESCE first-write-wins — terminal_cause と同格の保護)
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "s1" :api_limit_observed_at
                                        "2026-07-20T11:00:00+00:00"))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "api_limit_observed_at") "2026-07-20T11:00:00+00:00"))
    (db-upsert-snapshot conn (make-snap "s1" :api_limit_observed_at None))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "api_limit_observed_at") "2026-07-20T11:00:00+00:00")))
  (with-tmp-conn check))


(deftest test-observation-gap-roundtrip-last-write-wins
  ;; ADR-DOE-AGENTS-009 R1: observation_gap_at は last-write-wins(gap の
  ;; 再検出で前進する — event 有界化と launch 窓再スタートの基点)だが、
  ;; None 書き戻しでは消えない(COALESCE(excluded, existing) — 観測断の
  ;; 事実は行の寿命の間保持される)。
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "s1" :observation_gap_at
                                        "2026-07-27T20:15:00+00:00"))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "observation_gap_at") "2026-07-27T20:15:00+00:00"))
    ;; 再検出は前進する(last-write-wins)
    (db-upsert-snapshot conn (make-snap "s1" :observation_gap_at
                                        "2026-07-27T20:20:00+00:00"))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "observation_gap_at") "2026-07-27T20:20:00+00:00"))
    ;; stale な None 書き戻しでは消えない
    (db-upsert-snapshot conn (make-snap "s1" :observation_gap_at None))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "observation_gap_at") "2026-07-27T20:20:00+00:00")))
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
;; lease(TTL・owner guard・graceful 釈放と crash-path バックストップの物理)
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
    ;; 失効 lease は他 pid が取得できる(SIGKILL / crash 残骸の TTL
    ;; バックストップ — restart() の retry が頼る物理)
    (.execute conn
              "UPDATE agent_daemon_lease SET expires_at = '2000-01-01T00:00:00+00:00'")
    (db-acquire-lease conn 222)
    (assert (= (get (db-read-lease conn) "owner_pid") 222))
    ;; heartbeat 自己修復(2026-07-07 ensure spawn スパイラルの根治):
    ;; **失効した**他人名義 lease は heartbeat が再取得する(level-triggered)。
    ;; 盗んで死んだ競合者の残骸から、bind を保持する本物が回復する経路。
    (.execute conn
              "UPDATE agent_daemon_lease SET expires_at = '2000-01-01T00:00:00+00:00'")
    (db-heartbeat-once conn 111)
    (assert (= (get (db-read-lease conn) "owner_pid") 111))
    ;; 再取得後は自分名義で TTL が張り直されている(未失効)
    (setv healed (db-read-lease conn))
    (assert (> (parse-iso (get healed "expires_at"))
               (datetime.now timezone.utc)))
    ;; 消失は raise のまま(oracle parity — 自己修復は失効残骸に限る)
    (.execute conn "DELETE FROM agent_daemon_lease")
    (setv raised None)
    (try
      (db-heartbeat-once conn 111)
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None))
    (assert (in "disappeared" (str raised))))
  (with-tmp-conn check))


(deftest test-lease-release-owner-idempotent-and-successor
  ;; issue #565: graceful shutdown は自 lease を釈放し、後継は TTL 待ちなしで
  ;; 即 acquire できる。fail-loud acquire(未失効他人名義の拒否)は不変。
  ;; import はテスト内(red commit が module import ごと他 test を巻き込まない)。
  (import doeff_agents.sessionhost.store [db-release-lease])
  (defn check [conn]
    ;; 自分名義 → 削除される
    (db-acquire-lease conn 111)
    (db-release-lease conn 111)
    (assert (is (db-read-lease conn) None))
    ;; 冪等: lease 不在の release は no-op(raise しない)
    (db-release-lease conn 111)
    (assert (is (db-read-lease conn) None))
    ;; 釈放後は後継 pid が TTL 待ちなしで即 acquire 成功(本丸 —
    ;; launchd KeepAlive の即 spawn 後継が敗死しない物理)
    (db-acquire-lease conn 222)
    (assert (= (get (db-read-lease conn) "owner_pid") 222))
    ;; 他 pid 名義の未失効 lease は触らない(生きた二重 host の検出面を
    ;; release が壊さない)
    (db-release-lease conn 111)
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


;; ---------------------------------------------------------------------------
;; ADR-DOE-AGENTS-006: conversation / generation / lineage の store 契約
;; ---------------------------------------------------------------------------

(deftest test-conversation-coalesce-and-lineage-roundtrip
  ;; 発見済み会話 identity は後続 upsert(None)に消されない(COALESCE)。
  ;; generation / lineage は roundtrip する。
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "s1"))
    (db-upsert-snapshot conn (make-snap "s1"
                               :conversation {"session_id" "conv-1"
                                              "rollout_path" "/r.jsonl"}))
    (db-upsert-snapshot conn (make-snap "s1" :status "blocked"))
    (setv back (db-session-get conn "s1"))
    (assert (= (get (get back "conversation") "session_id") "conv-1"))
    (db-upsert-snapshot conn (make-snap "s2"
                               :generation 3
                               :resumed_from_session_id "s1"
                               :conversation {"session_id" "conv-1"}))
    (setv b2 (db-session-get conn "s2"))
    (assert (= (get b2 "generation") 3))
    (assert (= (get b2 "resumed_from_session_id") "s1"))
    (assert (is (get b2 "forked_from_session_id") None))
    ;; wire 形: conversation は常在化せず None なら省略、generation は常在
    (setv wire (snapshot-to-wire-dict (db-session-get conn "s1")))
    (assert (in "conversation" wire))
    (assert (= (get wire "generation") 1))
    (setv wire3 (snapshot-to-wire-dict (make-snap "s3")))
    (assert (not-in "conversation" wire3))
    (assert (not-in "resumed_from_session_id" wire3)))
  (with-tmp-conn check))


(deftest test-terminal-row-never-reactivated
  ;; law conversation-outlives-incarnation の機械面: terminal → active の
  ;; UPDATE は単一 writer が loud に拒否する(resume は新行を作る)。
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "s1" :status "done"))
    (setv raised None)
    (try
      (db-upsert-snapshot conn (make-snap "s1" :status "running"))
      (except [e RuntimeError]
        (setv raised (str e))))
    (assert (is-not raised None))
    (assert (in "may not be reactivated" raised))
    ;; terminal のままの後続 upsert(cleanup 時刻の記録等)は通る
    (db-upsert-snapshot conn (make-snap "s1" :status "done"
                               :cleaned_at "2026-07-05T01:00:00+00:00"))
    (assert (= (get (db-session-get conn "s1") "status") "done")))
  (with-tmp-conn check))


(deftest test-known-conversation-ids
  ;; terminal 行を含む全行から会話 ID 集合を導出(発見 arm の除外集合)。
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "s1"
                               :conversation {"session_id" "conv-b"}))
    (db-upsert-snapshot conn (make-snap "s2" :status "done"
                               :conversation {"session_id" "conv-a"}))
    (db-upsert-snapshot conn (make-snap "s3"))
    (assert (= (db-known-conversation-ids conn) ["conv-a" "conv-b"])))
  (with-tmp-conn check))


;; ---------------------------------------------------------------------------
;; 2026-07-27 sessionhost wedge 根治: hot path の status filter は SQL 側で
;; 適用し(idx_agent_sessions_status)、監査履歴(events / commands)は
;; retention で bounded に保つ。terminal 含む全行の full scan + 外部ソート +
;; 全行 JSON decode が毎 tick 走り、肥大 DB(1.5GB)で単一 StoreActor を
;; 飽和させた実 incident の再発防止。
;; ---------------------------------------------------------------------------

(deftest test-session-list-status-filter-applied-in-sql
  ;; status filter 付き一覧は WHERE を SQL に押し下げる(terminal 行を
  ;; 読み・decode しない)。結果集合は従来の Python filter と同値。
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "s1" :status "running"
                               :started_at "2026-07-05T00:00:03+00:00"))
    (db-upsert-snapshot conn (make-snap "s2" :status "done"
                               :started_at "2026-07-05T00:00:02+00:00"))
    (db-upsert-snapshot conn (make-snap "s3" :status "blocked"
                               :started_at "2026-07-05T00:00:01+00:00"))
    (setv statements [])
    (.set-trace-callback conn (fn [sql] (.append statements sql)))
    (try
      (setv snaps (db-session-list conn {"status" ["blocked" "booting"
                                                   "blocked_api" "running"]}))
      (finally (.set-trace-callback conn None)))
    (assert (= (lfor s snaps (get s "session_id")) ["s1" "s3"]))
    (setv list-sqls (lfor s statements :if (in "FROM agent_sessions" s) s))
    (assert list-sqls)
    (for [sql list-sqls]
      (assert (in "WHERE status IN" sql)
              f"status filter must be applied in SQL, got: {sql}"))
    ;; filter 無しは従来どおり全行(API 契約: session.list の無条件一覧)
    (setv all-snaps (db-session-list conn {}))
    (assert (= (len all-snaps) 3)))
  (with-tmp-conn check))


(deftest test-prune-history-retention-and-batching
  ;; cutoff より古い events / commands だけを bounded batch で削除する。
  ;; 戻り値 = この呼び出しで削除した行数(0 = 収束)。
  (defn check [conn]
    (for [[i occurred] (enumerate ["2026-07-01T00:00:00+00:00"
                                   "2026-07-02T00:00:00+00:00"
                                   "2026-07-03T00:00:00+00:00"
                                   "2026-07-20T00:00:00+00:00"
                                   "2026-07-21T00:00:00+00:00"])]
      (.execute conn
                (+ "INSERT INTO agent_session_events "
                   "(session_id, event_type, occurred_at, payload_json) "
                   "VALUES (?, ?, ?, ?)")
                #(f"s{i}" "session_observed" occurred "{}")))
    (for [[i requested] (enumerate ["2026-07-01T00:00:00+00:00"
                                    "2026-07-02T00:00:00+00:00"
                                    "2026-07-20T00:00:00+00:00"])]
      (.execute conn
                (+ "INSERT INTO agent_session_commands "
                   "(session_id, command_type, requested_at, completed_at, "
                   "status, payload_json, error) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)")
                #(f"s{i}" "session.send" requested requested "completed" "{}" None)))
    (setv cutoff "2026-07-13T00:00:00+00:00")
    ;; batch 2: events 2 + commands 2
    (assert (= (db-prune-history conn cutoff 2) 4))
    ;; 残りの old: events 1 + commands 0
    (assert (= (db-prune-history conn cutoff 2) 1))
    ;; 収束
    (assert (= (db-prune-history conn cutoff 2) 0))
    (setv remaining-events
          (.fetchall (.execute conn
                               "SELECT occurred_at FROM agent_session_events ORDER BY id")))
    (assert (= (lfor r remaining-events (get r 0))
               ["2026-07-20T00:00:00+00:00" "2026-07-21T00:00:00+00:00"]))
    (setv remaining-commands
          (.fetchall (.execute conn
                               "SELECT requested_at FROM agent_session_commands ORDER BY id")))
    (assert (= (lfor r remaining-commands (get r 0))
               ["2026-07-20T00:00:00+00:00"])))
  (with-tmp-conn check))


(deftest test-actor-prune-history-converges
  ;; StoreActor 経由の prune pass: batch を別 op として逐次 submit し
  ;; (client op が間に割り込める — actor を長時間占有しない)、収束までの
  ;; 削除合計を返す。
  (setv d (tempfile.mkdtemp))
  (try
    (setv actor (StoreActor (os.path.join d "agentd.sqlite")))
    (try
      (.submit actor
               (fn [conn]
                 (for [i (range 5)]
                   (.execute conn
                             (+ "INSERT INTO agent_session_events "
                                "(session_id, event_type, occurred_at, payload_json) "
                                "VALUES (?, ?, ?, ?)")
                             #(f"s{i}" "session_observed"
                               "2026-07-01T00:00:00+00:00" "{}")))))
      (assert (= (actor-prune-history actor "2026-07-13T00:00:00+00:00" 2) 5))
      (assert (= (.submit actor
                          (fn [conn]
                            (get (.fetchone (.execute conn
                                              "SELECT COUNT(*) FROM agent_session_events"))
                                 0)))
                 0))
      ;; 収束済みの再 pass は 0
      (assert (= (actor-prune-history actor "2026-07-13T00:00:00+00:00" 2) 0))
      (finally (.close actor)))
    (finally (shutil.rmtree d :ignore-errors True))))


(deftest test-vacuum-if-bloated-threshold
  ;; freelist が支配的(かつ絶対量が floor 超)になったときだけ VACUUM する。
  ;; 小さな健全 DB では走らない(起動毎の全書き換えを避ける)。
  (defn check [conn]
    (assert (= (db-vacuum-if-bloated conn) False))
    ;; ~16MB 注入 → 全削除で freelist を支配的にする
    (setv blob (* "x" (* 4 1024 1024)))
    (for [i (range 4)]
      (.execute conn
                (+ "INSERT INTO agent_session_events "
                   "(session_id, event_type, occurred_at, payload_json) "
                   "VALUES (?, ?, ?, ?)")
                #(f"s{i}" "session_observed" "2026-07-01T00:00:00+00:00" blob)))
    (.execute conn "DELETE FROM agent_session_events")
    (assert (= (db-vacuum-if-bloated conn) True))
    ;; vacuum 後は freelist が回収済み → 再実行は no-op
    (assert (= (db-vacuum-if-bloated conn) False)))
  (with-tmp-conn check))
