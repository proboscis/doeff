;;; SQLite store-of-record + writer actor + lease(ADR-DOE-AGENTS-004 C3)。
;;;
;;; oracle = agentd-rust-final:src/main.rs:
;;;   migrate(:974-1064、additive ensure_column)/ upsert_snapshot
;;;   (:2320-2393、COALESCE は terminal_cause_json と result_payload_json の
;;;   2 列のみ)/ row_to_snapshot(:2261-2318)/ session_get / session_list
;;;   (:1858-1918、SELECT 列順が row index 契約)/ record_event(:2395)/
;;;   record_command(:2415)/ current_result_payload(:3975、fresh read)/
;;;   lease(:1094-1157 BEGIN IMMEDIATE + TTL 10s、heartbeat :3462 owner_pid
;;;   guard)/ 起動時 awaiting_response latch clear(main :591-596)。
;;;
;;; 設計裁定(ACP plan「C3 実行設計」裁定 1): oracle は per-connection +
;;; busy_timeout 30s の多重接続だが、Hy host は**単一 write connection を
;;; queue で直列化する writer actor** に置換する。semantics(COALESCE 2 列・
;;; first-write-wins・fresh read・report_result は payload のみ / monitor は
;;; status のみ、の 2-writer 分離)は保存し、機構だけ直列化して
;;; SQLITE_BUSY ハザード class(main.rs:22-27 の傷跡)を構造ごと除去する。
;;; conformance suite が DB を mode=ro で外部読みするため journal mode は
;;; 既定(delete)のまま、busy_timeout も oracle と同値で残す。
;;;
;;; C3 拡張(S14 X→P、plan 裁定 3): `effective_identity_json` 列を additive
;;; migration で追加。launch の PreLaunchSetup が解決した実効 identity
;;; (CODEX_HOME / CLAUDE_CONFIG_DIR)を行に永続化する。書き込みは launch の
;;; 一度きりなので COALESCE 保護(後続 upsert が識別情報を消さない)。

(require doeff-hy.macros [deff defk defhandler])

(import doeff [run])

(import dataclasses [replace])
(import datetime [datetime timezone timedelta])
(import json)
(import os)
(import queue)
(import sqlite3)
(import threading)
(import doeff_agents.sessionhost.cache_host_model [HostCacheRead HostCacheActive HostCacheWrite HostCacheLastSuccessAt])
(import doeff_agents.sessionhost.cache_host_store [cache-receipt-get cache-receipt-active cache-receipt-put cache-last-success-at])

(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  SessionStoreListActive
  SessionStoreListCleanupPending
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreResultPayload
  SessionStoreRecordEvent
  SessionStoreKnownConversationIds])
(import doeff_agents.sessionhost.policy [ACTIVE-STATUSES TERMINAL-STATUSES
                                         parse-iso])


;; ---------------------------------------------------------------------------
;; 凍結定数(oracle main.rs:18-28)
;; ---------------------------------------------------------------------------

(setv LEASE-NAME "doeff-agentd")
(setv LEASE-TTL-SECONDS 10)
;; 単一 writer 化で書き込み競合は構造的に消えるが、外部 ro 読者と共存する
;; 以上 busy_timeout=0 経路を再導入しない(oracle :22-27 の傷跡)。
(setv SQLITE-BUSY-TIMEOUT-MS 30000)


;; ---------------------------------------------------------------------------
;; schema(oracle migrate verbatim + C3 の effective_identity_json)
;; ---------------------------------------------------------------------------

(setv SCHEMA-BATCH "
CREATE TABLE IF NOT EXISTS agent_sessions (
  session_id TEXT PRIMARY KEY,
  session_name TEXT NOT NULL,
  pane_id TEXT NOT NULL,
  agent_type TEXT NOT NULL,
  work_dir TEXT NOT NULL,
  status TEXT NOT NULL,
  backend_kind TEXT NOT NULL,
  backend_ref_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  last_observed_at TEXT,
  finished_at TEXT,
  cleaned_at TEXT,
  pr_url TEXT,
  output_snippet TEXT,
  terminal_cause_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_session_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_session_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  command_type TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS agent_daemon_lease (
  lease_name TEXT PRIMARY KEY,
  owner_pid INTEGER NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_status
  ON agent_sessions(status);
CREATE INDEX IF NOT EXISTS idx_agent_session_events_session
  ON agent_session_events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_session_events_occurred
  ON agent_session_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_agent_session_commands_requested
  ON agent_session_commands(requested_at);
")

(setv ENSURE-COLUMNS
      [#("agent_sessions" "lifecycle" "TEXT NOT NULL DEFAULT 'run_to_completion'")
       #("agent_sessions" "expected_result_json" "TEXT")
       #("agent_sessions" "retries_used" "INTEGER NOT NULL DEFAULT 0")
       #("agent_sessions" "last_validation_error" "TEXT")
       #("agent_sessions" "awaiting_response" "INTEGER NOT NULL DEFAULT 0")
       #("agent_sessions" "observed_active_at" "TEXT")
       #("agent_sessions" "terminal_cause_json" "TEXT")
       #("agent_sessions" "result_payload_json" "TEXT")
       #("agent_sessions" "result_solicitations_used" "INTEGER NOT NULL DEFAULT 0")
       #("agent_sessions" "prompt_unblock_attempts" "INTEGER NOT NULL DEFAULT 0")
       #("agent_sessions" "last_output_change_at" "TEXT")
       ;; C3 拡張(S14): 解決済み実効 identity。additive なので Rust oracle が
       ;; 書いた既存 DB にもそのまま生える。
       #("agent_sessions" "effective_identity_json" "TEXT")
       ;; ADR-DOE-AGENTS-006: 会話 identity(kind 判別 union)+ incarnation
       ;; 世代 + 系譜。既存行は generation=1・conversation NULL
       ;; (identity-unknown)として生える。
       #("agent_sessions" "conversation_json" "TEXT")
       #("agent_sessions" "generation" "INTEGER NOT NULL DEFAULT 1")
       #("agent_sessions" "resumed_from_session_id" "TEXT")
       #("agent_sessions" "forked_from_session_id" "TEXT")
       #("agent_sessions" "launch_overlay_json" "TEXT")
       ;; koine session surface v0 stage 1(ADR-DOE-AGENTS-007): adopted =
       ;; 安全条項 1 の ownership marker(opt-in/fail-closed の機械面)、
       ;; turn_* = 席の自己申告打刻(writer は turn RPC のみ・wait は opaque
       ;; 保存 — 解釈権威は席側 wait_protocol.py)。
       #("agent_sessions" "adopted" "INTEGER NOT NULL DEFAULT 0")
       #("agent_sessions" "turn_holder" "TEXT")
       #("agent_sessions" "turn_since" "TEXT")
       #("agent_sessions" "turn_wait_json" "TEXT")
       ;; issue #557: attempt 中の api-limit 観測の durable latch(初回観測
       ;; 時刻)。additive migration + COALESCE first-write-wins(terminal_cause
       ;; と同格の保護 — stale な書き戻しが観測事実を消さない)。
       #("agent_sessions" "api_limit_observed_at" "TEXT")
       ;; ADR-DOE-AGENTS-009: 観測断(supply cut)の最終検出時刻。
       ;; last-write-wins + None 保護(COALESCE(excluded, existing))。
       #("agent_sessions" "observation_gap_at" "TEXT")
       ;; issue #568(ADR-DOE-AGENTS-010 R2): paste 再送補償の durable counter。
       ;; last-write-wins(単一 writer = monitor)。
       #("agent_sessions" "paste_resubmit_attempts" "INTEGER NOT NULL DEFAULT 0")
       ;; issue #568(ADR-DOE-AGENTS-010 R3): awaiting latch の武装時刻(期限の
       ;; 基点)。last-write-wins — 正の作業証拠での None clear は意図的な書き。
       #("agent_sessions" "awaiting_response_since" "TEXT")
       ;; ACP ADR 0049 R9 第 3 改訂: 上限族の外の provider 失敗の durable latch
       ;; (族名 + 初回観測時刻)。api_limit_observed_at と同格の COALESCE
       ;; first-write-wins 保護 — 観測した事実を後続の書き戻しが消さない。
       #("agent_sessions" "provider_failure_class" "TEXT")
       #("agent_sessions" "provider_failure_observed_at" "TEXT")
       ;; 発注者(ACP scheduler)申告の帰属 metadata(opaque verbatim —
       ;; conversation_json と同型)。launch の一度きりの書きを COALESCE
       ;; first-write-wins が守る。消費者は Mac 側の利用帰属台帳
       ;; (json_extract '$.action_id' の expression index が読みを支える)。
       #("agent_sessions" "launch_attribution_json" "TEXT")
       ;; 温かい session(lifecycle multi_turn — agentd 段 2 lane 2b-3・ADR-DOE-AGENTS-012
       ;; R10): monitor が手番の終わりを最初に観測した時刻。level-triggered(次の手番
       ;; で NULL)・単一 writer = monitor・素の last-write-wins。
       #("agent_sessions" "turn_ended_at" "TEXT")
       ;; 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D2): 温かい session の手番が**失敗で**終わった時に走行器が
       ;; 名乗った文(headless の turn_verdict の detail)。turn_ended_at と対の level-triggered の欄
       ;; (成功の終わり・次の手番の送りで NULL)・単一 writer = monitor・素の last-write-wins。
       #("agent_sessions" "turn_error" "TEXT")])

(setv SNAPSHOT-SELECT
      (+ "SELECT session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status, "
         "backend_kind, backend_ref_json, started_at, last_observed_at, "
         "finished_at, cleaned_at, pr_url, output_snippet, "
         "terminal_cause_json, expected_result_json, retries_used, last_validation_error, "
         "awaiting_response, observed_active_at, result_payload_json, "
         "result_solicitations_used, prompt_unblock_attempts, last_output_change_at, "
         "effective_identity_json, "
         "conversation_json, generation, resumed_from_session_id, forked_from_session_id, "
         "launch_overlay_json, "
         "adopted, turn_holder, turn_since, turn_wait_json, "
         "api_limit_observed_at, observation_gap_at, "
         "paste_resubmit_attempts, awaiting_response_since, "
         "provider_failure_class, provider_failure_observed_at, "
         "launch_attribution_json, turn_ended_at, turn_error "
         "FROM agent_sessions"))


(deff now-iso []
  {:pre [True]
   :post [(: % str)]}
  "現在時刻の ISO8601(oracle now_iso = RFC3339。store 内部の記録時刻のみに
   使う — policy の時間算術は ClockNow effect 経由のまま)。"
  (.isoformat (datetime.now timezone.utc)))

(deff open-conn [db-path]
  {:pre [(: db-path str)]
   :post [(: % sqlite3.Connection)]}
  "接続を開く(oracle open_conn :551-555 と同じ busy_timeout)。
   isolation_level None = autocommit(rusqlite 既定と同じ)— transaction は
   lease の BEGIN IMMEDIATE だけが明示的に張る。"
  (setv conn (sqlite3.connect db-path :check-same-thread False
                              :isolation-level None))
  (.execute conn f"PRAGMA busy_timeout = {SQLITE-BUSY-TIMEOUT-MS}")
  conn)

(deff db-migrate [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % "None")]}
  "schema 適用(oracle migrate: CREATE IF NOT EXISTS + additive ALTER)。
   会話 index は ensure-column の後(conversation_json は additive 列 —
   fresh DB では ALTER が先に走らないと index が張れない)。波 1-S1 /
   ADR-DOE-AGENTS-007 R7: 会話 ID → 行の行引き(db-session-by-conversation)
   と打刻の conversation 鍵を支える expression index。"
  (.executescript conn SCHEMA-BATCH)
  (for [[table column definition] ENSURE-COLUMNS]
    (ensure-column conn table column definition))
  (.execute conn
            (+ "CREATE INDEX IF NOT EXISTS idx_agent_sessions_conversation "
               "ON agent_sessions(json_extract(conversation_json, '$.session_id'))"))
  ;; 帰属列の読み口(Mac 側の利用帰属台帳が「この action の走行」を引く鍵)。
  ;; conversation index と同じ理由で ensure-column の後。
  (.execute conn
            (+ "CREATE INDEX IF NOT EXISTS idx_agent_sessions_launch_attribution_action "
               "ON agent_sessions(json_extract(launch_attribution_json, '$.action_id'))"))
  None)

(deff ensure-column [conn table column definition]
  {:pre [(: conn sqlite3.Connection) (: table str) (: column str)
         (: definition str)]
   :post [(: % "None")]}
  (setv names (lfor row (.fetchall (.execute conn f"PRAGMA table_info({table})"))
                    (get row 1)))
  (when (not-in column names)
    (.execute conn f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
  None)


;; ---------------------------------------------------------------------------
;; snapshot(store-of-record の 1 行、dict 表現 — key は wire field 名)
;; ---------------------------------------------------------------------------

(deff terminal-cause-to-dict [cause]
  {:pre [(: cause TerminalCause)]
   :post [(: % dict)]}
  "TerminalCause → 永続 JSON dict(serde 順・None reason は省略)。"
  (setv payload {"category" cause.category})
  (when (is-not cause.reason None)
    (setv (get payload "reason") cause.reason))
  (setv (get payload "retryable") cause.retryable)
  (setv (get payload "observed_at") cause.observed-at)
  ;; 限度の断りの範囲と戻りの時刻(2026-09-23・在る時だけ — 凍結表の外の情報の欄)。
  (when (is-not cause.limit-scope None)
    (setv (get payload "limit_scope") cause.limit-scope))
  (when (is-not cause.limit-resets-at-ms None)
    (setv (get payload "limit_resets_at_ms") cause.limit-resets-at-ms))
  payload)

(deff terminal-cause-from-dict [payload]
  {:pre [(: payload dict)]
   :post [(: % (| TerminalCause None))]}
  "永続 JSON dict → TerminalCause(oracle の追加 optional field —
   retry_after_seconds / backend_error_code / exit_code / signal — は
   policy 契約外なので落とす。行の JSON はそのまま保たれる)。
   契約の必須欄(category / observed_at が str)を持たない payload は None —
   行の typed な眺めでは『cause なし』(段 10 lane 10h・agora-redesign #84: 手で書かれた
   {\"cause\": …} の行を session.get / session.resume が KeyError 'category' で読めず、
   会話の --resume が断られていた)。wire(snapshot-to-wire-dict)は raw の JSON を
   そのまま運び、DB の COALESCE(first-write-wins)が raw を消さないので、None は
   発明ではなく『typed には読めない』の正直な形。"
  (setv category (.get payload "category"))
  (setv observed-at (.get payload "observed_at"))
  (if (and (isinstance category str) (isinstance observed-at str))
      (TerminalCause :category category
                     :reason (.get payload "reason")
                     :retryable (bool (.get payload "retryable" False))
                     :observed-at observed-at
                     :limit-scope (let [scope (.get payload "limit_scope")] (if (isinstance scope str) scope None))
                     :limit-resets-at-ms (let [resets (.get payload "limit_resets_at_ms")]
                                           (if (and (isinstance resets int) (not (isinstance resets bool))) resets None)))
      None))

(deff snapshot-from-db-row [db-row]
  {:pre [(: db-row tuple)]
   :post [(: % dict)]}
  "SELECT 行 → snapshot dict(oracle row_to_snapshot の index 契約)。"
  {"session_id" (get db-row 0)
   "session_name" (get db-row 1)
   "pane_id" (get db-row 2)
   "agent_type" (get db-row 3)
   "work_dir" (get db-row 4)
   "lifecycle" (get db-row 5)
   "status" (get db-row 6)
   "backend_kind" (get db-row 7)
   "backend_ref" (json.loads (get db-row 8))
   "started_at" (get db-row 9)
   "last_observed_at" (get db-row 10)
   "finished_at" (get db-row 11)
   "cleaned_at" (get db-row 12)
   "pr_url" (get db-row 13)
   "output_snippet" (get db-row 14)
   "terminal_cause" (if (is (get db-row 15) None)
                        None
                        (json.loads (get db-row 15)))
   "expected_result" (if (is (get db-row 16) None)
                         None
                         (json.loads (get db-row 16)))
   "retries_used" (int (get db-row 17))
   "last_validation_error" (get db-row 18)
   "awaiting_response" (!= (int (get db-row 19)) 0)
   "observed_active_at" (get db-row 20)
   "result_payload" (get db-row 21)
   "result_solicitations_used" (int (get db-row 22))
   "prompt_unblock_attempts" (int (get db-row 23))
   "last_output_change_at" (get db-row 24)
   "effective_identity" (if (is (get db-row 25) None)
                            None
                            (json.loads (get db-row 25)))
   "conversation" (if (is (get db-row 26) None)
                      None
                      (json.loads (get db-row 26)))
   "generation" (int (get db-row 27))
   "resumed_from_session_id" (get db-row 28)
   "forked_from_session_id" (get db-row 29)
   "launch_overlay" (if (is (get db-row 30) None)
                        None
                        (json.loads (get db-row 30)))
   "adopted" (!= (int (get db-row 31)) 0)
   "turn_holder" (get db-row 32)
   "turn_since" (get db-row 33)
   "turn_wait" (if (is (get db-row 34) None)
                   None
                   (json.loads (get db-row 34)))
   "api_limit_observed_at" (get db-row 35)
   "observation_gap_at" (get db-row 36)
   "paste_resubmit_attempts" (int (get db-row 37))
   "awaiting_response_since" (get db-row 38)
   "provider_failure_class" (get db-row 39)
   "provider_failure_observed_at" (get db-row 40)
   "launch_attribution" (if (is (get db-row 41) None)
                            None
                            (json.loads (get db-row 41)))
   "turn_ended_at" (get db-row 42)
   "turn_error" (get db-row 43)})

(deff snapshot-to-wire-dict [snap]
  {:pre [(: snap dict)]
   :post [(: % dict)]}
  "snapshot dict → wire / event payload 形(serde SessionSnapshot parity:
   skip_serializing_if な optional 6 field と C3 拡張 effective_identity は
   None のとき field ごと省略、他の Option は null で残る)。"
  (setv wire {"session_id" (get snap "session_id")
              "session_name" (get snap "session_name")
              "pane_id" (get snap "pane_id")
              "agent_type" (get snap "agent_type")
              "work_dir" (get snap "work_dir")
              "lifecycle" (get snap "lifecycle")
              "status" (get snap "status")
              "backend_kind" (get snap "backend_kind")
              "backend_ref" (get snap "backend_ref")
              "started_at" (get snap "started_at")
              "last_observed_at" (get snap "last_observed_at")
              "finished_at" (get snap "finished_at")
              "cleaned_at" (get snap "cleaned_at")
              "pr_url" (get snap "pr_url")
              "output_snippet" (get snap "output_snippet")})
  (when (is-not (get snap "terminal_cause") None)
    (setv (get wire "terminal_cause") (get snap "terminal_cause")))
  (when (is-not (get snap "expected_result") None)
    (setv (get wire "expected_result") (get snap "expected_result")))
  (setv (get wire "retries_used") (get snap "retries_used"))
  (when (is-not (get snap "last_validation_error") None)
    (setv (get wire "last_validation_error") (get snap "last_validation_error")))
  (setv (get wire "awaiting_response") (get snap "awaiting_response"))
  (when (is-not (get snap "observed_active_at") None)
    (setv (get wire "observed_active_at") (get snap "observed_active_at")))
  (when (is-not (get snap "result_payload") None)
    (setv (get wire "result_payload") (get snap "result_payload")))
  (setv (get wire "result_solicitations_used")
        (get snap "result_solicitations_used"))
  (setv (get wire "prompt_unblock_attempts") (get snap "prompt_unblock_attempts"))
  (when (is-not (get snap "last_output_change_at") None)
    (setv (get wire "last_output_change_at") (get snap "last_output_change_at")))
  (when (is-not (.get snap "effective_identity") None)
    (setv (get wire "effective_identity") (get snap "effective_identity")))
  ;; ADR-006: conversation / lineage は None のとき field ごと省略
  ;; (identity-unknown を wire で null と区別しない)。generation は常在。
  (when (is-not (.get snap "conversation") None)
    (setv (get wire "conversation") (get snap "conversation")))
  (setv (get wire "generation") (.get snap "generation" 1))
  (when (is-not (.get snap "resumed_from_session_id") None)
    (setv (get wire "resumed_from_session_id")
          (get snap "resumed_from_session_id")))
  (when (is-not (.get snap "forked_from_session_id") None)
    (setv (get wire "forked_from_session_id")
          (get snap "forked_from_session_id")))
  (when (is-not (.get snap "launch_overlay") None)
    (setv (get wire "launch_overlay") (get snap "launch_overlay")))
  ;; 帰属 metadata も None のとき field ごと省略(未申告を wire で null と
  ;; 区別しない — launch_overlay と同じ規律)。
  (when (is-not (.get snap "launch_attribution") None)
    (setv (get wire "launch_attribution") (get snap "launch_attribution")))
  ;; ADR-007: adopted は常在 bool(ownership marker は不在と false を区別
  ;; しない)、turn_* は None のとき field ごと省略(未打刻は不可視 — R6 の
  ;; 既知限界を wire でも正直に)。
  (setv (get wire "adopted") (bool (.get snap "adopted" False)))
  (when (is-not (.get snap "turn_holder") None)
    (setv (get wire "turn_holder") (get snap "turn_holder")))
  (when (is-not (.get snap "turn_since") None)
    (setv (get wire "turn_since") (get snap "turn_since")))
  (when (is-not (.get snap "turn_wait") None)
    (setv (get wire "turn_wait") (get snap "turn_wait")))
  ;; 温かい session(multi_turn): 手番の終わりの刻印は None のとき欄ごと省略
  ;; (run_to_completion / interactive では常に不在 — 未観測を null と区別しない)。
  (when (is-not (.get snap "turn_ended_at") None)
    (setv (get wire "turn_ended_at") (get snap "turn_ended_at")))
  ;; 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D2): 手番の失敗の文も None のとき欄ごと省略(成功の終わりと区別しない null を書かない)。
  (when (is-not (.get snap "turn_error") None)
    (setv (get wire "turn_error") (get snap "turn_error")))
  wire)


;; ---------------------------------------------------------------------------
;; 行の読み書き(oracle の SQL verbatim + effective_identity_json)
;; ---------------------------------------------------------------------------

(deff db-upsert-snapshot [conn snap]
  {:pre [(: conn sqlite3.Connection) (: snap dict)]
   :post [(: % "None")]}
  "INSERT … ON CONFLICT DO UPDATE(oracle upsert_snapshot)。COALESCE 保護は
   terminal_cause_json / result_payload_json(oracle :2354/:2360)+ C3 の
   effective_identity_json(launch が一度だけ書く識別情報を後続 upsert が
   消さない)+ ADR-006 の conversation_json(発見済み会話 identity を後続
   upsert が消さない)。他は excluded の last-write-wins。"
  ;; ADR-DOE-AGENTS-006 law conversation-outlives-incarnation の機械面:
  ;; terminal に達した行は決して active 系へ戻らない(resume は新しい
  ;; incarnation 行を作る)。単一 writer のここが唯一の防衛線。
  (setv guard-sid (get snap "session_id"))
  (setv guard-row (.fetchone (.execute conn
                               "SELECT status FROM agent_sessions WHERE session_id = ?"
                               #(guard-sid))))
  (when (is-not guard-row None)
    (setv existing-status (get guard-row 0))
    (setv incoming-status (get snap "status"))
    (when (and (in existing-status TERMINAL-STATUSES)
               (in incoming-status ACTIVE-STATUSES))
      (raise (RuntimeError
               (+ f"terminal session row may not be reactivated: '{guard-sid}' "
                  f"is '{existing-status}' and cannot move to '{incoming-status}' "
                  "(ADR-DOE-AGENTS-006: resume creates a new incarnation row)")))))
  (.execute conn
    (+ "INSERT INTO agent_sessions ("
       "session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status, "
       "backend_kind, backend_ref_json, started_at, last_observed_at, "
       "finished_at, cleaned_at, pr_url, output_snippet, "
       "terminal_cause_json, expected_result_json, retries_used, last_validation_error, "
       "awaiting_response, observed_active_at, result_payload_json, "
       "result_solicitations_used, prompt_unblock_attempts, last_output_change_at, "
       "effective_identity_json, "
       "conversation_json, generation, resumed_from_session_id, forked_from_session_id, "
       "launch_overlay_json, "
       "adopted, turn_holder, turn_since, turn_wait_json, "
       "api_limit_observed_at, observation_gap_at, "
       "paste_resubmit_attempts, awaiting_response_since, "
       "provider_failure_class, provider_failure_observed_at, "
       "launch_attribution_json, turn_ended_at, turn_error"
       ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
       "ON CONFLICT(session_id) DO UPDATE SET "
       "session_name = excluded.session_name, "
       "pane_id = excluded.pane_id, "
       "agent_type = excluded.agent_type, "
       "work_dir = excluded.work_dir, "
       "lifecycle = excluded.lifecycle, "
       "status = excluded.status, "
       "backend_kind = excluded.backend_kind, "
       "backend_ref_json = excluded.backend_ref_json, "
       "started_at = excluded.started_at, "
       "last_observed_at = excluded.last_observed_at, "
       "finished_at = excluded.finished_at, "
       "cleaned_at = excluded.cleaned_at, "
       "pr_url = excluded.pr_url, "
       "output_snippet = excluded.output_snippet, "
       "terminal_cause_json = COALESCE(agent_sessions.terminal_cause_json, excluded.terminal_cause_json), "
       "expected_result_json = excluded.expected_result_json, "
       "retries_used = excluded.retries_used, "
       "last_validation_error = excluded.last_validation_error, "
       "awaiting_response = excluded.awaiting_response, "
       "observed_active_at = excluded.observed_active_at, "
       "result_payload_json = COALESCE(agent_sessions.result_payload_json, excluded.result_payload_json), "
       "result_solicitations_used = excluded.result_solicitations_used, "
       "prompt_unblock_attempts = excluded.prompt_unblock_attempts, "
       "last_output_change_at = excluded.last_output_change_at, "
       "effective_identity_json = COALESCE(agent_sessions.effective_identity_json, excluded.effective_identity_json), "
       "conversation_json = COALESCE(agent_sessions.conversation_json, excluded.conversation_json), "
       "generation = excluded.generation, "
       "resumed_from_session_id = excluded.resumed_from_session_id, "
       "forked_from_session_id = excluded.forked_from_session_id, "
       "launch_overlay_json = COALESCE(agent_sessions.launch_overlay_json, excluded.launch_overlay_json), "
       ;; ADR-007: turn_* も last-write-wins で安全 — 全書き込みが actor で
       ;; 直列化され、merge 経路(db-merge-policy-row)は actor 内で existing を
       ;; 再読してから重ねるため、turn RPC の UPDATE を stale な monitor 書き
       ;; 戻しが巻き戻す隙間は構造的に無い。
       "adopted = excluded.adopted, "
       "turn_holder = excluded.turn_holder, "
       "turn_since = excluded.turn_since, "
       "turn_wait_json = excluded.turn_wait_json, "
       ;; issue #557: durable latch は first-write-wins — 初回観測時刻が正で、
       ;; stale な None 書き戻しにも後続観測の再打刻にも動じない。
       "api_limit_observed_at = COALESCE(agent_sessions.api_limit_observed_at, excluded.api_limit_observed_at), "
       ;; ADR-DOE-AGENTS-009: 観測断の刻印は last-write-wins(再検出で前進)
       ;; だが None 書き戻しでは消えない — COALESCE の引数順が api_limit と
       ;; 逆(excluded 優先)なのはそのため。
       "observation_gap_at = COALESCE(excluded.observation_gap_at, agent_sessions.observation_gap_at), "
       ;; issue #568(ADR-DOE-AGENTS-010): counter は素の last-write-wins、
       ;; since は None clear が意図的な書き(正の作業証拠での解除)なので
       ;; COALESCE 保護を持たない — 全書き込みは actor 直列 + merge 経路が
       ;; existing を再読して重ねるため stale clobber の隙間は無い。
       "paste_resubmit_attempts = excluded.paste_resubmit_attempts, "
       "awaiting_response_since = excluded.awaiting_response_since, "
       ;; ACP ADR 0049 R9 第 3 改訂: 上限族の外の provider 失敗 latch も
       ;; first-write-wins(api_limit_observed_at と同格)。族名と時刻は対で
       ;; 意味を持つので、同じ COALESCE 規律を両方に掛ける。
       "provider_failure_class = COALESCE(agent_sessions.provider_failure_class, excluded.provider_failure_class), "
       "provider_failure_observed_at = COALESCE(agent_sessions.provider_failure_observed_at, excluded.provider_failure_observed_at), "
       ;; 帰属は launch の一度きりの申告 — 後続 upsert(monitor の書き戻し
       ;; を含む)が消しても上書きしてもいけない。conversation_json /
       ;; effective_identity_json と同格の first-write-wins。
       "launch_attribution_json = COALESCE(agent_sessions.launch_attribution_json, excluded.launch_attribution_json), "
       ;; 温かい session(multi_turn): 手番の終わりの刻印は level-triggered — None の
       ;; 書きは「次の手番が走り出した」の事実なので COALESCE 保護を持たない
       ;; (単一 writer = monitor・merge 経路が existing を再読して重ねる)。
       "turn_ended_at = excluded.turn_ended_at, "
       ;; 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D2): turn_ended_at と対 — 同じ level-triggered の規律。
       "turn_error = excluded.turn_error")
    #((get snap "session_id")
      (get snap "session_name")
      (get snap "pane_id")
      (get snap "agent_type")
      (get snap "work_dir")
      (get snap "lifecycle")
      (get snap "status")
      (get snap "backend_kind")
      (json.dumps (get snap "backend_ref") :sort-keys True
                  :separators #("," ":"))
      (get snap "started_at")
      (get snap "last_observed_at")
      (get snap "finished_at")
      (get snap "cleaned_at")
      (get snap "pr_url")
      (get snap "output_snippet")
      (if (is (get snap "terminal_cause") None)
          None
          (json.dumps (get snap "terminal_cause") :separators #("," ":")))
      ;; expected_result は oracle では serde Value(BTreeMap)= key ソート。
      ;; terminal_cause は struct(宣言順)なのでソートしない。
      (if (is (get snap "expected_result") None)
          None
          (json.dumps (get snap "expected_result") :sort-keys True
                      :separators #("," ":")))
      (int (get snap "retries_used"))
      (get snap "last_validation_error")
      (int (bool (get snap "awaiting_response")))
      (get snap "observed_active_at")
      (get snap "result_payload")
      (int (get snap "result_solicitations_used"))
      (int (get snap "prompt_unblock_attempts"))
      (get snap "last_output_change_at")
      (if (is (.get snap "effective_identity") None)
          None
          (json.dumps (get snap "effective_identity") :sort-keys True
                      :separators #("," ":")))
      (if (is (.get snap "conversation") None)
          None
          (json.dumps (get snap "conversation") :sort-keys True
                      :separators #("," ":")))
      (int (.get snap "generation" 1))
      (.get snap "resumed_from_session_id")
      (.get snap "forked_from_session_id")
      (if (is (.get snap "launch_overlay") None)
          None
          (json.dumps (get snap "launch_overlay") :sort-keys True
                      :separators #("," ":")))
      ;; .get 既定値: ADR-007 以前の snapshot dict(旧テスト fixture 等)にも
      ;; additive に振る舞う — 列既定値(adopted=0 / turn_* NULL)と同値。
      (int (bool (.get snap "adopted" False)))
      (.get snap "turn_holder")
      (.get snap "turn_since")
      (if (is (.get snap "turn_wait") None)
          None
          (json.dumps (get snap "turn_wait") :sort-keys True
                      :separators #("," ":") :ensure-ascii False))
      ;; .get 既定値: issue #557 以前の snapshot dict にも additive に振る舞う。
      (.get snap "api_limit_observed_at")
      ;; ADR-DOE-AGENTS-009 以前の snapshot dict にも additive に振る舞う。
      (.get snap "observation_gap_at")
      ;; issue #568(ADR-DOE-AGENTS-010)以前の snapshot dict にも additive。
      (int (.get snap "paste_resubmit_attempts" 0))
      (.get snap "awaiting_response_since")
      ;; ACP ADR 0049 R9 第 3 改訂以前の snapshot dict にも additive に振る舞う。
      (.get snap "provider_failure_class")
      (.get snap "provider_failure_observed_at")
      ;; 帰属 metadata(opaque verbatim — conversation と同じ直列化規律)。
      (if (is (.get snap "launch_attribution") None)
          None
          (json.dumps (get snap "launch_attribution") :sort-keys True
                      :separators #("," ":")))
      ;; lane 2b-3 以前の snapshot dict にも additive に振る舞う。
      (.get snap "turn_ended_at")
      (.get snap "turn_error")))
  None)

(deff db-session-get [conn session-id]
  {:pre [(: conn sqlite3.Connection) (: session-id str)]
   :post [(: % (| dict None))]}
  (setv row (.fetchone (.execute conn
                                 (+ SNAPSHOT-SELECT " WHERE session_id = ?")
                                 #(session-id))))
  (if (is row None) None (snapshot-from-db-row row)))

(deff db-session-by-conversation [conn conversation-id]
  {:pre [(: conn sqlite3.Connection) (: conversation-id str)
         (> (len conversation-id) 0)]
   :post [(: % (| dict None))]}
  "会話 ID → 行の行引き(波 1-S1 / ADR-DOE-AGENTS-007 R7・law
   conversation-lookup-never-probes)。解決則 = 非終端の最新行、無ければ
   最新の terminal 行、無ければ None(会話資源契約草案の解決則の store 面 —
   conversationId → 生きた宿り高々 1、無ければ最新 terminal 宿り)。
   同一性欄(conversation_json.session_id)の完全一致のみで引く —
   部分一致・全欄横断は『言及』を『登記』と読む。SELECT のみ(substrate
   不接触は構造)。idx_agent_sessions_conversation(expression index)が
   支える。"
  (for [statuses [(sorted ACTIVE-STATUSES) (sorted TERMINAL-STATUSES)]]
    (setv placeholders (.join ", " (lfor _ statuses "?")))
    (setv row (.fetchone (.execute conn
                           (+ SNAPSHOT-SELECT
                              " WHERE json_extract(conversation_json, '$.session_id') = ?"
                              f" AND status IN ({placeholders})"
                              " ORDER BY started_at DESC, session_id ASC LIMIT 1")
                           (tuple (+ [conversation-id] statuses)))))
    (when (is-not row None)
      (return (snapshot-from-db-row row))))
  None)

(deff list-query-matches [snap filters]
  {:pre [(: snap dict) (: filters dict)]
   :post [(: % bool)]}
  "oracle list_query_matches(status は集合員、他は等値)。"
  (setv statuses (.get filters "status"))
  (when (and (is-not statuses None)
             (not-in (get snap "status") statuses))
    (return False))
  (for [key ["agent_type" "backend_kind" "lifecycle"]]
    (setv wanted (.get filters key))
    (when (and (is-not wanted None) (!= wanted (get snap key)))
      (return False)))
  ;; ADR-007 §8: adopted filter(bool)。対話席一覧の主 filter
  ;; (adopted=true で adopt 行だけを出す)。
  (setv adopted-wanted (.get filters "adopted"))
  (when (and (is-not adopted-wanted None)
             (!= (bool (.get snap "adopted" False)) (bool adopted-wanted)))
    (return False))
  True)

(deff db-session-list [conn filters]
  {:pre [(: conn sqlite3.Connection) (: filters dict)]
   :post [(: % list)]}
  "oracle と同じ ORDER BY の一覧。filter 意味論は list-query-matches
   (oracle session_list)のままだが、status だけは SQL 側でも適用する
   (idx_agent_sessions_status)— 毎 tick の ListActive が terminal 含む
   全履歴行を full scan + 外部ソート + JSON decode し、肥大 DB で単一
   StoreActor を飽和させた 2026-07-27 wedge の hot path 根治。"
  (setv statuses (.get filters "status"))
  (setv order-by " ORDER BY started_at DESC, session_id ASC")
  ;; 行は cursor のまま渡す(頁で止めた先の行を fetch も decode もしない)。
  (setv rows
        (cond
          (is statuses None)
          (.execute conn (+ SNAPSHOT-SELECT order-by))
          ;; 空集合 filter は SQL の `IN ()` が書けないので構造的に空
          ;; (従来の Python filter と同値: どの行も一致しない)。
          (= (len statuses) 0)
          []
          True
          (do
            (setv placeholders (.join ", " (lfor _ statuses "?")))
            (.execute conn
                      (+ SNAPSHOT-SELECT
                         f" WHERE status IN ({placeholders})"
                         order-by)
                      (tuple statuses)))))
  ;; 頁(limit / offset — 2026-09-23 会社 Mac の実弾): 一致した行を新しい順に offset 件飛ばして
  ;; limit 件だけ decode して返す。limit が無い一覧は従来どおり全件(API 契約の無条件一覧)。
  ;; 終端の履歴 6,087 行を毎 heartbeat 全件 decode + 行ごとの wire 導出で返し(15 MB・5〜34 秒)、
  ;; agentd の 10 秒の読みの期限を越え続けた。上限を持つ呼び手(agentd の transcript の候補)は
  ;; 必要な件数だけを頁で読み、履歴の長さに比例する仕事を器に撃たせない。
  (setv limit (.get filters "limit"))
  (setv offset (or (.get filters "offset") 0))
  (setv out [])
  (setv skipped 0)
  (for [row rows]
    (when (and (is-not limit None) (>= (len out) limit))
      (break))
    (setv snap (snapshot-from-db-row row))
    (when (list-query-matches snap filters)
      (if (< skipped offset)
          (setv skipped (+ skipped 1))
          (.append out snap))))
  out)

(deff db-count-active [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % int)]}
  (len (db-session-list conn {"status" (sorted ACTIVE-STATUSES)})))

(deff db-session-list-cleanup-pending [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % list)]}
  "単一掃き取り(issue #568 / ADR-DOE-AGENTS-010 R5)の対象集合: 終端 status ∧
   cleaned_at IS NULL ∧ run_to_completion ∧ 非 adopted。刈り取り免除
   (ADR-DOE-AGENTS-007 安全条項 1)は SQL で継承する — 対話席・adopt 席の
   substrate に掃き取りは触れない。cleaned_at の刻印(掃き取り側)で集合が
   有界に収束するため、毎 tick の再読は idx_agent_sessions_status の range
   scan + 少数行の decode に留まる。"
  (setv statuses (sorted TERMINAL-STATUSES))
  (setv placeholders (.join ", " (lfor _ statuses "?")))
  (setv rows (.fetchall (.execute conn
                          (+ SNAPSHOT-SELECT
                             f" WHERE status IN ({placeholders})"
                             " AND cleaned_at IS NULL"
                             " AND lifecycle = 'run_to_completion'"
                             " AND adopted = 0"
                             " ORDER BY started_at ASC, session_id ASC")
                          (tuple statuses))))
  (lfor row rows (snapshot-from-db-row row)))

(deff db-known-conversation-ids [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % list)]}
  "store が知る全会話 ID(terminal 含む全行。ADR-006 発見 arm の除外集合)。"
  (setv rows (.fetchall (.execute conn
                          "SELECT conversation_json FROM agent_sessions WHERE conversation_json IS NOT NULL")))
  (sorted (sfor row rows
                :setv conv (json.loads (get row 0))
                :if (isinstance (.get conv "session_id") str)
                (get conv "session_id"))))

(deff db-current-result-payload [conn session-id]
  {:pre [(: conn sqlite3.Connection) (: session-id str)]
   :post [(: % (| str None))]}
  "result_payload_json の fresh read(oracle current_result_payload :3975 —
   report_result は別経路で書くため、tick の手元 snapshot を信じない)。"
  (setv row (.fetchone (.execute conn
                                 "SELECT result_payload_json FROM agent_sessions WHERE session_id = ?"
                                 #(session-id))))
  (if (is row None) None (get row 0)))

(deff db-record-event [conn session-id event-type payload]
  {:pre [(: conn sqlite3.Connection) (: session-id str) (: event-type str)
         (: payload dict)]
   :post [(: % "None")]}
  (.execute conn
            (+ "INSERT INTO agent_session_events "
               "(session_id, event_type, occurred_at, payload_json) "
               "VALUES (?, ?, ?, ?)")
            #(session-id event-type (now-iso)
              (json.dumps payload :separators #("," ":"))))
  None)

(deff db-record-command [conn session-id command-type status error payload]
  {:pre [(: conn sqlite3.Connection) (: session-id (| str None))
         (: command-type str) (: status str) (: error (| str None))
         ;; oracle record_command<T: Serialize>(:2415)は任意 JSON 値 —
         ;; session.send は message 文字列を payload としてそのまま監査する。
         (: payload (| dict str))]
   :post [(: % "None")]}
  (setv now (now-iso))
  (.execute conn
            (+ "INSERT INTO agent_session_commands "
               "(session_id, command_type, requested_at, completed_at, status, payload_json, error) "
               "VALUES (?, ?, ?, ?, ?, ?, ?)")
            #(session-id command-type now now status
              (json.dumps payload :separators #("," ":")) error))
  None)

(deff db-clear-awaiting-latches [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % "None")]}
  "起動時の awaiting_response latch 全 clear(oracle main :591-596 verbatim —
   唯一の意図的破棄。latch の意味は死んだ process の再促に束縛されている)。
   期限の基点 awaiting_response_since も同時に消す(issue #568 /
   ADR-DOE-AGENTS-010 R3 — 基点も同じく死んだ process の配送に束縛されている)。
   headless の行は対象外(段 10 lane 10h・agora-redesign #84): headless の latch は
   『手番の途中』の事実そのもので、消すと再起動後の復帰(headless.hy
   recover-headless-rows — backend の生死を観測して手番の途中の行を終端に倒す)が
   判断できず、死んだ手番が running のまま永久に残る(実弾 2026-09-14 14:35)。"
  (.execute conn
            (+ "UPDATE agent_sessions SET awaiting_response = 0, "
               "awaiting_response_since = NULL "
               "WHERE awaiting_response = 1 "
               "AND backend_kind != 'headless' "
               "AND status NOT IN ('done','failed','exited','stopped','cancelled')"))
  None)

;; ---------------------------------------------------------------------------
;; 監査履歴の retention(2026-07-27 sessionhost wedge 根治)
;;
;; agent_session_events / agent_session_commands は append-only の監査面で、
;; runtime は読まない(プログラム的読者は conformance harness のみ — テスト
;; 実行中の新鮮な行だけを見る)。無限成長させると store ファイルが実測
;; 1.5GB(session_blocked 251k 行 / 1.16GB)まで肥大し、単一 write connection
;; の I/O 局所性を壊す。retention 境界より古い行を bounded batch で刈り、
;; ファイルの物理回収(VACUUM)は freelist が支配的なときの起動時のみ行う。
;; ---------------------------------------------------------------------------

(setv HISTORY-PRUNE-BATCH-ROWS 5000)
;; VACUUM は全書き換え(1.5GB 実測 ~8s)なので、回収に値する空きがあるとき
;; だけ: freelist が全 page の 1/4 以上 かつ 絶対量 floor(4KiB page で
;; ~10MiB)以上。serve 中は走らせない — 起動時(accept 開始前)専用。
(setv VACUUM-FREELIST-RATIO 0.25)
(setv VACUUM-FREELIST-FLOOR-PAGES 2560)

(deff db-prune-history [conn cutoff-iso batch-limit]
  {:pre [(: conn sqlite3.Connection) (: cutoff-iso str)
         (: batch-limit int) (> batch-limit 0)]
   :post [(: % int)]}
  "retention 境界より古い監査行(events / commands)を最大 batch-limit 行ずつ
   削除する。戻り値 = この呼び出しの削除行数(0 = 収束)。subquery は時刻列
   index(idx_agent_session_events_occurred / _commands_requested)の range
   scan — 収束確認も O(index probe) で、肥大テーブルの full scan を actor に
   持ち込まない。"
  (setv deleted 0)
  (for [[table column] [#("agent_session_events" "occurred_at")
                        #("agent_session_commands" "requested_at")]]
    (setv cursor (.execute conn
                           (+ f"DELETE FROM {table} WHERE id IN ("
                              f"SELECT id FROM {table} WHERE {column} < ? "
                              "LIMIT ?)")
                           #(cutoff-iso batch-limit)))
    (setv deleted (+ deleted cursor.rowcount)))
  deleted)

(deff actor-prune-history [actor cutoff-iso batch-limit]
  {:pre [(: actor StoreActor) (: cutoff-iso str)
         (: batch-limit int) (> batch-limit 0)]
   :post [(: % int)]}
  "prune を収束まで回す 1 pass。batch 毎に別 actor op として submit するので
   client op が間に割り込める — actor を長時間占有しない。戻り値 = 削除合計。"
  (setv total 0)
  (while True
    (setv deleted (.submit actor
                           (fn [conn] (db-prune-history conn cutoff-iso
                                                        batch-limit))))
    (setv total (+ total deleted))
    (when (= deleted 0)
      (break)))
  total)

(deff db-vacuum-if-bloated [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % bool)]}
  "freelist が支配的なときだけ VACUUM(戻り値 = 実行したか)。健全な DB の
   起動毎全書き換えを避ける threshold は定数参照。"
  (setv freelist (get (.fetchone (.execute conn "PRAGMA freelist_count")) 0))
  (setv pages (get (.fetchone (.execute conn "PRAGMA page_count")) 0))
  (when (or (< freelist VACUUM-FREELIST-FLOOR-PAGES)
            (< freelist (* VACUUM-FREELIST-RATIO pages)))
    (return False))
  (.execute conn "VACUUM")
  True)


(deff db-report-result-guarded-update [conn session-id payload]
  {:pre [(: conn sqlite3.Connection) (: session-id str) (: payload str)]
   :post [(: % int)]}
  "first-write-wins の guarded UPDATE(oracle session_report_result
   :2174-2179 verbatim)。result_payload_json 未設定かつ非終端のときだけ
   書ける。戻り値 = affected 行数(0 = 既に書かれているか終端)。
   status はここでは書かない — done 化は monitor の観測所有。"
  (setv cursor
        (.execute conn
                  (+ "UPDATE agent_sessions SET result_payload_json = ? "
                     "WHERE session_id = ? "
                     "AND result_payload_json IS NULL "
                     "AND status NOT IN ('done','failed','exited','stopped','cancelled')")
                  #(payload session-id)))
  cursor.rowcount)


(deff db-late-result-accept [conn session-id payload accepted-at]
  {:pre [(: conn sqlite3.Connection) (: session-id str) (: payload str)
         (: accepted-at str)]
   :post [(: % int)]}
  "遅延 result 受理の guarded UPDATE(ADR-DOE-AGENTS-009 R4)。死亡裁定
   クラス(status=exited)+ result 未永続の行だけに書ける唯一の
   terminal→terminal 上書き経路: result 到着は死亡推定の反証そのものなので
   status=done へ、誤裁定の残滓(terminal_cause / last_validation_error)は
   クリアし、finished_at は受理時刻へ前進する(死亡刻印時刻は誤裁定の時刻 —
   完了の最初の証拠は result 到着)。upsert の COALESCE(terminal_cause_json
   first-write-wins)はこの直接 UPDATE を通らないため干渉しない。
   戻り値 = affected 行数(0 = 既に書かれているか対象外)。"
  (setv cursor
        (.execute conn
                  (+ "UPDATE agent_sessions SET result_payload_json = ?, "
                     "status = 'done', "
                     "last_validation_error = NULL, "
                     "terminal_cause_json = NULL, "
                     "finished_at = ? "
                     "WHERE session_id = ? "
                     "AND result_payload_json IS NULL "
                     "AND status = 'exited'")
                  #(payload accepted-at session-id)))
  cursor.rowcount)


(deff db-mark-cleaned [conn session-id cleaned-at]
  {:pre [(: conn sqlite3.Connection) (: session-id str) (: cleaned-at str)]
   :post [(: % "None")]}
  "substrate cleanup の記帳(first-write-wins — finalize の cleaned_at と
   同義)。遅延 result 受理後の RPC 層 cleanup(ADR-DOE-AGENTS-009 R4)が
   actor op として呼ぶ。"
  (.execute conn
            (+ "UPDATE agent_sessions "
               "SET cleaned_at = COALESCE(cleaned_at, ?) "
               "WHERE session_id = ?")
            #(cleaned-at session-id))
  None)


;; ---------------------------------------------------------------------------
;; lease(oracle :1094-1157 / heartbeat :3462-3476)
;; ---------------------------------------------------------------------------

(deff db-read-lease [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % (| dict None))]}
  (setv row (.fetchone (.execute conn
                                 (+ "SELECT lease_name, owner_pid, heartbeat_at, expires_at "
                                    "FROM agent_daemon_lease WHERE lease_name = ?")
                                 #(LEASE-NAME))))
  (if (is row None)
      None
      {"lease_name" (get row 0)
       "owner_pid" (int (get row 1))
       "heartbeat_at" (get row 2)
       "expires_at" (get row 3)}))

(deff db-upsert-lease [conn owner-pid]
  {:pre [(: conn sqlite3.Connection) (: owner-pid int)]
   :post [(: % "None")]}
  (setv now (datetime.now timezone.utc))
  (setv expires (+ now (timedelta :seconds LEASE-TTL-SECONDS)))
  (.execute conn
            (+ "INSERT INTO agent_daemon_lease "
               "(lease_name, owner_pid, heartbeat_at, expires_at) "
               "VALUES (?, ?, ?, ?) "
               "ON CONFLICT(lease_name) DO UPDATE SET "
               "owner_pid = excluded.owner_pid, "
               "heartbeat_at = excluded.heartbeat_at, "
               "expires_at = excluded.expires_at")
            #(LEASE-NAME owner-pid (.isoformat now) (.isoformat expires)))
  None)

(deff db-acquire-lease [conn owner-pid]
  {:pre [(: conn sqlite3.Connection) (: owner-pid int)]
   :post [(: % "None — 生存 lease は raise")]}
  "BEGIN IMMEDIATE の下で未失効 lease を拒否(oracle acquire_lease)。
   graceful shutdown は db-release-lease が自 lease を先に消す(issue #565、
   oracle の SIGTERM 非解放からの意図的乖離)ので、この fail-loud 検査に
   かかるのは SIGKILL / crash の残骸(TTL 失効待ち)か生きた二重 host のみ。
   conformance restart() の TTL retry はその crash-path バックストップ
   (harness.py)。"
  (.execute conn "BEGIN IMMEDIATE")
  (try
    (setv existing (db-read-lease conn))
    (when (is-not existing None)
      (setv expires (parse-iso (get existing "expires_at")))
      (when (and (is-not expires None)
                 (> expires (datetime.now timezone.utc)))
        (setv owner (get existing "owner_pid"))
        (setv expires-raw (get existing "expires_at"))
        (raise (RuntimeError
                 (+ "doeff-agentd lease is active: "
                    f"owner_pid={owner} expires_at={expires-raw}")))))
    (db-upsert-lease conn owner-pid)
    (.execute conn "COMMIT")
    (except [e Exception]
      (.execute conn "ROLLBACK")
      (raise)))
  None)

(deff db-heartbeat-once [conn owner-pid]
  {:pre [(: conn sqlite3.Connection) (: owner-pid int)]
   :post [(: % "None — 未失効の owner 交代は raise")]}
  "lease 更新。消失は raise(oracle parity)。owner 交代は 2 相
   (2026-07-07 ensure spawn スパイラルの根治、oracle からの意図的乖離):
   - 相手 lease が**失効済み** → 再取得(level-triggered 自己修復)。socket
     bind が排他の実体で lease はその影 — bind を保持する自分が、盗んで死んだ
     競合者の残骸(死 pid 名義の失効 lease)から回復しないと heartbeat が
     永久にエラーし続ける(実測: expected 68021 got 88831 の無限連発)。
   - 相手 lease が**未失効** → raise(worker tick が log して次 tick へ)。
     これは生きた二重 host(別 socket 同一 DB の誤構成)の検出面なので残す。
   判定と upsert は BEGIN IMMEDIATE で原子化(read→upsert の隙間に競合の
   acquire が挟まると未失効 lease を盗むため)。"
  (.execute conn "BEGIN IMMEDIATE")
  (try
    (setv current (db-read-lease conn))
    (when (is current None)
      (raise (RuntimeError
               "doeff-agentd lease disappeared while daemon was running")))
    (when (!= (get current "owner_pid") owner-pid)
      (setv expires (parse-iso (get current "expires_at")))
      (when (and (is-not expires None)
                 (> expires (datetime.now timezone.utc)))
        (setv got (get current "owner_pid"))
        (raise (RuntimeError
                 (+ "doeff-agentd lease owner changed: "
                    f"expected {owner-pid} got {got}")))))
    (db-upsert-lease conn owner-pid)
    (.execute conn "COMMIT")
    (except [e Exception]
      (.execute conn "ROLLBACK")
      (raise)))
  None)

(deff db-release-lease [conn owner-pid]
  {:pre [(: conn sqlite3.Connection) (: owner-pid int)]
   :post [(: % bool)]}
  "graceful shutdown の lease 釈放(issue #565 — oracle からの意図的乖離)。
   BEGIN IMMEDIATE 下で**自 owner-pid 名義の行だけ**を削除する。冪等
   (不在は no-op)。他 pid 名義は未失効・失効を問わず触らない — 生きた
   二重 host の検出面(acquire / heartbeat の fail-loud)を release が
   壊さないため。TTL は SIGKILL / crash 経路のバックストップとして残る。
   戻り値 = 釈放したか。"
  (.execute conn "BEGIN IMMEDIATE")
  (setv released False)
  (try
    (setv current (db-read-lease conn))
    (when (and (is-not current None)
               (= (get current "owner_pid") owner-pid))
      (.execute conn
                (+ "DELETE FROM agent_daemon_lease "
                   "WHERE lease_name = ? AND owner_pid = ?")
                #(LEASE-NAME owner-pid))
      (setv released True))
    (.execute conn "COMMIT")
    (except [e Exception]
      (.execute conn "ROLLBACK")
      (raise)))
  released)


;; 器の入れ替えの blue/green(acp/host_slots): 新しい器の区画の store を、いま手番を受けている器の store の写しで
;; 始める。終端の行(会話の前の session)を新しい器が持っていないと `--resume` の元が引けず(resume-session の
;; 実在の admission)、器を入れ替えるたびに cache を捨てて履歴から起こし直すことになる。
(setv SEED-SIDECAR-SUFFIXES #("-journal" "-wal" "-shm"))

(deff db-seed-from [source-path target-path]
  {:pre [(: source-path str) (: target-path str)]
   :post [(: % int)]}
  "source の store の一貫した写し(sqlite の backup — 書き手が走っていても 1 つの読みの断面)を target に据える。
   写しからは lease の行を消す(持ち主は source の器 — 残すと新しい器の db-acquire-lease が生きた lease として
   断る)。据えは使い捨ての名に書いてから os.replace(途中で落ちても target は前のまま)。target の古い付属
   file(-journal / -wal / -shm)は据える前に除く — 残った hot journal は開いた拍に**新しい写し**へ巻き戻しを
   当てる。⚠ 呼び手(host-slot seed)は target の器が起きていないことを確かめてから呼ぶ(起きている器の
   store を置き換えない)。戻り値 = 写した session の行数。"
  (setv staged (+ target-path ".seeding"))
  (for [path [staged (+ staged "-journal")]]
    (when (os.path.exists path)
      (os.remove path)))
  (setv source (sqlite3.connect f"file:{source-path}?mode=ro" :uri True))
  (try
    (.execute source f"PRAGMA busy_timeout = {SQLITE-BUSY-TIMEOUT-MS}")
    (setv target (sqlite3.connect staged))
    (try
      (.backup source target)
      (.execute target "DELETE FROM agent_daemon_lease")
      (.commit target)
      (setv rows (get (.fetchone (.execute target "SELECT COUNT(*) FROM agent_sessions")) 0))
      (finally
        (.close target)))
    (finally
      (.close source)))
  (for [suffix SEED-SIDECAR-SUFFIXES]
    (when (os.path.exists (+ target-path suffix))
      (os.remove (+ target-path suffix))))
  (os.replace staged target-path)
  rows)


;; ---------------------------------------------------------------------------
;; writer actor(単一 write connection の直列化点)
;; ---------------------------------------------------------------------------

(defk db-journal-seq [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % int)]}
  "出来事の journal(agent_session_events)の先端 = 最大の id(空なら 0)。session.wait_events の
   答えと待ちの座はこの値(段 12 lane 12b・agora-redesign #207 根 1)。"
  (setv row (.fetchone (.execute conn "SELECT COALESCE(MAX(id), 0) FROM agent_session_events")))
  (int (get row 0)))


(defclass StoreActor []
  "SQLite store-of-record への唯一の玄関。connection はコンストラクタ thread で
   開いて migrate まで済ませ(起動失敗を呼び手へ loud に伝播)、以後の実行は
   actor thread に一本化される — 読みも書きも queue を通るので、すべての op
   (read-modify-write 含む)が原子的に直列化される。

   出来事の journal の合図(段 12 lane 12b・agora-redesign #207 根 1): op が store を変えた
   (conn.total_changes が進んだ)拍に journal の先端(db-journal-seq)を読み直し、進んでいれば
   journal-seq に写して待ち手(wait-journal = RPC session.wait_events の long-poll)を起こす。
   出来事の insert がどの路(SessionStoreRecordEvent・host の結果の受理の直の db-record-event)を
   通っても、合図の定義点はこの actor の 1 点 — 呼び手が合図を覚える必要は無い。"
  (defn __init__ [self db-path]
    (setv self.db-path db-path)
    (setv self.conn (open-conn db-path))
    (db-migrate self.conn)
    (setv self._journal (threading.Condition))
    (setv self.journal-seq (run (db-journal-seq self.conn)))
    (setv self._queue (queue.Queue))
    (setv self._thread (threading.Thread :target self._run :daemon True
                                         :name "sessionhost-store"))
    (.start self._thread))

  (defn _run [self]
    (while True
      (setv item (.get self._queue))
      (when (is item None)
        (break))
      (setv #(op box event) item)
      (setv before self.conn.total-changes)
      (try
        (setv (get box "value") (op self.conn))
        (except [e Exception]
          (setv (get box "error") e)))
      (.set event)
      (when (!= self.conn.total-changes before)
        (self._advance-journal)))
    ;; 降りる時は待ち手を全部起こす(上限まで待たせない — 答えは今の先端)。
    (with [self._journal]
      (.notify-all self._journal)))

  (defn _advance-journal [self]
    "actor thread だけが呼ぶ: journal の先端を読み直し、進んでいれば待ち手を起こす。"
    (setv seq (run (db-journal-seq self.conn)))
    (with [self._journal]
      (when (> seq self.journal-seq)
        (setv self.journal-seq seq)
        (.notify-all self._journal))))

  (defn wait-journal [self after timeout]
    "出来事の journal の先端が ``after`` を越えるまで待つ(上限 ``timeout`` 秒・0 = 待たずに今の先端)。
     戻り = 今の先端(呼び手が次の ``after`` にする)。どの thread からも呼べる(条件変数と int だけに触る —
     connection には触らない)。"
    (with [self._journal]
      (.wait-for self._journal (fn [] (> self.journal-seq after)) :timeout (max 0.0 timeout))
      self.journal-seq))

  (defn submit [self op]
    "op(conn を取る callable)を actor thread で実行し、結果を返す /
     例外を再送出する(呼び手視点は同期)。"
    (setv box {})
    (setv event (threading.Event))
    (.put self._queue #(op box event))
    (.wait event)
    (when (in "error" box)
      (raise (get box "error")))
    (.get box "value"))

  (defn close [self]
    (.put self._queue None)
    (.join self._thread :timeout 5)
    (.close self.conn)))


;; ---------------------------------------------------------------------------
;; policy SessionRow ⇄ snapshot(SessionStore effect の host 束縛)
;; ---------------------------------------------------------------------------

(deff snapshot-to-policy-row [snap]
  {:pre [(: snap dict)]
   :post [(: % SessionRow)]}
  "store-of-record の行 → policy 可視 SessionRow(monitor はこれから毎 cycle
   再導出する)。pr_url / retries_used(vestigial)は policy 契約外。
   読んだ時点の欄の写し(read-base)を付ける — 書き戻しの merge はこの写しから
   変わった欄だけを重ねる(db-merge-policy-row)。"
  (setv row (SessionRow
    :session-id (get snap "session_id")
    :session-name (get snap "session_name")
    :pane-id (get snap "pane_id")
    :agent-type (get snap "agent_type")
    :lifecycle (get snap "lifecycle")
    :status (get snap "status")
    :started-at (get snap "started_at")
    :last-observed-at (get snap "last_observed_at")
    :finished-at (get snap "finished_at")
    :cleaned-at (get snap "cleaned_at")
    :output-snippet (get snap "output_snippet")
    :last-output-change-at (get snap "last_output_change_at")
    :awaiting-response (get snap "awaiting_response")
    :observed-active-at (get snap "observed_active_at")
    :expected-result (get snap "expected_result")
    :result-payload (get snap "result_payload")
    :last-validation-error (get snap "last_validation_error")
    :result-solicitations-used (get snap "result_solicitations_used")
    :prompt-unblock-attempts (get snap "prompt_unblock_attempts")
    :terminal-cause (if (is (get snap "terminal_cause") None)
                        None
                        (terminal-cause-from-dict (get snap "terminal_cause")))
    :effective-identity (.get snap "effective_identity")
    :work-dir (get snap "work_dir")
    :backend-kind (get snap "backend_kind")
    :backend-ref (get snap "backend_ref")
    :conversation (.get snap "conversation")
    :generation (.get snap "generation" 1)
    :resumed-from-session-id (.get snap "resumed_from_session_id")
    :forked-from-session-id (.get snap "forked_from_session_id")
    :launch-overlay (.get snap "launch_overlay")
    :launch-attribution (.get snap "launch_attribution")
    :adopted (bool (.get snap "adopted" False))
    :api-limit-observed-at (.get snap "api_limit_observed_at")
    :observation-gap-at (.get snap "observation_gap_at")
    :paste-resubmit-attempts (int (.get snap "paste_resubmit_attempts" 0))
    :awaiting-response-since (.get snap "awaiting_response_since")
    :provider-failure-class (.get snap "provider_failure_class")
    :provider-failure-observed-at (.get snap "provider_failure_observed_at")
    :turn-ended-at (.get snap "turn_ended_at")
    :turn-error (.get snap "turn_error")))
  (replace row :read-base (policy-row-patch row)))

(deff policy-row-patch [row]
  {:pre [(: row SessionRow)]
   :post [(: % dict)]}
  "SessionRow が所有する snapshot field の patch(merge 用)。"
  {"session_id" row.session-id
   "session_name" row.session-name
   "pane_id" row.pane-id
   "agent_type" row.agent-type
   "lifecycle" row.lifecycle
   "status" row.status
   "started_at" row.started-at
   "last_observed_at" row.last-observed-at
   "finished_at" row.finished-at
   "cleaned_at" row.cleaned-at
   "output_snippet" row.output-snippet
   "last_output_change_at" row.last-output-change-at
   "awaiting_response" row.awaiting-response
   "observed_active_at" row.observed-active-at
   "expected_result" row.expected-result
   "result_payload" row.result-payload
   "last_validation_error" row.last-validation-error
   "result_solicitations_used" row.result-solicitations-used
   "prompt_unblock_attempts" row.prompt-unblock-attempts
   "terminal_cause" (if (is row.terminal-cause None)
                        None
                        (terminal-cause-to-dict row.terminal-cause))
   "effective_identity" row.effective-identity
   "work_dir" row.work-dir
   "backend_kind" row.backend-kind
   "backend_ref" (or row.backend-ref {})
   "conversation" row.conversation
   "generation" row.generation
   "resumed_from_session_id" row.resumed-from-session-id
   "forked_from_session_id" row.forked-from-session-id
   "launch_overlay" row.launch-overlay
   ;; 帰属 metadata は launch が一度だけ書く出自申告。SQL 側 COALESCE
   ;; first-write-wins が最終防衛する(monitor の書き戻しは None を運ぶ
   ;; だけなので消えない)。
   "launch_attribution" row.launch-attribution
   ;; ADR-007: adopted は行ごとに不変(adopt が作った行だけ true)なので
   ;; patch に含めて安全。turn_* は policy 契約外 — patch に含めない
   ;; (merge 経路では existing の打刻が保存され、新規行は
   ;; snapshot-from-policy-row が NULL 初期値を与える)。
   "adopted" row.adopted
   ;; issue #557: durable latch は policy(monitor)が唯一の writer。
   ;; SQL 側 COALESCE が first-write-wins を最終防衛する。
   "api_limit_observed_at" row.api-limit-observed-at
   ;; ADR-DOE-AGENTS-009: 観測断の刻印も policy(monitor)が唯一の writer。
   ;; SQL 側 COALESCE(excluded, existing)が None 書き戻しから防衛する。
   "observation_gap_at" row.observation-gap-at
   ;; issue #568(ADR-DOE-AGENTS-010): counter / since も policy が唯一の
   ;; writer(素の last-write-wins — merge 経路が existing を再読して重ねる)。
   "paste_resubmit_attempts" row.paste-resubmit-attempts
   "awaiting_response_since" row.awaiting-response-since
   ;; ACP ADR 0049 R9 第 3 改訂: provider 失敗 latch も policy(monitor)が
   ;; 唯一の writer。SQL 側 COALESCE が first-write-wins を最終防衛する。
   "provider_failure_class" row.provider-failure-class
   "provider_failure_observed_at" row.provider-failure-observed-at
   ;; 温かい session(multi_turn — ADR-DOE-AGENTS-012 R10): 手番の終わりの刻印も
   ;; policy(monitor)が唯一の writer(level-triggered・素の last-write-wins)。
   "turn_ended_at" row.turn-ended-at
   ;; 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D2): 手番の失敗の文も monitor が唯一の writer(turn_ended_at と対)。
   "turn_error" row.turn-error})

(defk changed-policy-patch [row]
  {:pre [(: row SessionRow)]
   :post [(: % dict)]}
  "書き戻しで重ねる欄: store から読んだ行なら、読んだ時点の写し(read-base)から変わった欄だけ。
   読んでいない行(read-base None — launch が作る行)は全欄。"
  (setv patch (policy-row-patch row))
  (if (is row.read-base None)
      patch
      (dfor [k v] (.items patch)
            :if (or (not-in k row.read-base) (!= v (get row.read-base k)))
            k v)))

(deff snapshot-from-policy-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % dict)]}
  "policy SessionRow から新規 snapshot を作る(launch の booting 行)。
   policy 契約外 field は oracle session_launch と同じ初期値。"
  (setv snap (policy-row-patch row))
  (setv (get snap "pr_url") None)
  (setv (get snap "retries_used") 0)
  ;; ADR-007: 新規行の turn 打刻は未打刻(NULL)から始まる。
  (setv (get snap "turn_holder") None)
  (setv (get snap "turn_since") None)
  (setv (get snap "turn_wait") None)
  snap)

(defk db-merge-policy-row [conn row]
  {:pre [(: conn sqlite3.Connection) (: row SessionRow)]
   :post [(: % "None")]}
  "SessionStoreUpsert の実体: 既存 full 行に policy patch を重ねて upsert
   (actor 内で実行されるので、この関数の中の read-modify-write は原子的)。COALESCE 2+1 列の
   保護は SQL 側が持つ。
   書き手が store から読んだ行(read-base を持つ)なら、重ねるのは読んだ値から変わった欄だけ
   (changed-policy-patch)。書き手の読みと書きの間に別の書き手が着地しても、書き手が触らなかった
   欄はその着地の値のまま残る — 書き手の読みから書きまでは actor の外なので、全欄を重ねると
   古い読みで他人の書きを消す(2026-09-23 の実弾: 監視の書き戻しが送信の新 pid を旧 pid へ戻した)。"
  (setv existing (db-session-get conn row.session-id))
  (if (is existing None)
      (db-upsert-snapshot conn (snapshot-from-policy-row row))
      (do
        (setv merged (dict existing))
        (.update merged (! (changed-policy-patch row)))
        (db-upsert-snapshot conn merged)))
  None)


(defhandler sqlite-session-store [actor]
  (HostCacheLastSuccessAt [session-id]
    (resume (.submit actor (fn [conn] (cache-last-success-at conn session-id)))))
  (HostCacheRead [operation-id]
    (resume (.submit actor (fn [conn] (cache-receipt-get conn operation-id)))))
  (HostCacheActive [session-id]
    (resume (.submit actor (fn [conn] (cache-receipt-active conn session-id)))))
  (HostCacheWrite [record]
    (.submit actor (fn [conn] (cache-receipt-put conn record)))
    (resume None))
  ;; SessionStore substrate effect の host 束縛(DOE-004 R1)。すべて actor
  ;; 経由 = 直列化済み。oracle monitor は backend_kind="tmux" も filter する
  ;; (:3486)が、Hy host の行は launch 経路しか作らないので常に tmux —
  ;; effect 契約(active_statuses のみ)を保つ。
  (SessionStoreListActive []
    (setv snaps (.submit actor
                         (fn [conn]
                           (db-session-list conn {"status" (sorted ACTIVE-STATUSES)}))))
    (resume (lfor s snaps (snapshot-to-policy-row s))))

  (SessionStoreListCleanupPending []
    (setv snaps (.submit actor db-session-list-cleanup-pending))
    (resume (lfor s snaps (snapshot-to-policy-row s))))

  (SessionStoreGet [session-id]
    (setv snap (.submit actor (fn [conn] (db-session-get conn session-id))))
    (resume (if (is snap None) None (snapshot-to-policy-row snap))))

  (SessionStoreUpsert [row]
    (.submit actor (fn [conn] (run (db-merge-policy-row conn row))))
    (resume None))

  (SessionStoreResultPayload [session-id]
    (resume (.submit actor
                     (fn [conn] (db-current-result-payload conn session-id)))))

  (SessionStoreKnownConversationIds []
    (resume (.submit actor db-known-conversation-ids)))

  (SessionStoreRecordEvent [session-id event-type row]
    ;; oracle は event payload に full snapshot を記録する(record_event 呼び
    ;; 出しは常に upsert 済みの snapshot を渡す)。fresh read で同じ形にする。
    (.submit actor
             (fn [conn]
               (setv snap (db-session-get conn session-id))
               (setv payload (if (is snap None)
                                 (policy-row-patch row)
                                 (snapshot-to-wire-dict snap)))
               (db-record-event conn session-id event-type payload)))
    (resume None)))
