;;; SQLite store-of-record + writer actor + lease(ADR-DOE-AGENTS-004 C3)。
;;;
;;; oracle = packages/doeff-agentd/src/main.rs:
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

(require doeff-hy.macros [deff defhandler])

(import datetime [datetime timezone timedelta])
(import json)
(import queue)
(import sqlite3)
(import threading)

(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  SessionStoreListActive
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreResultPayload
  SessionStoreRecordEvent])
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
       #("agent_sessions" "effective_identity_json" "TEXT")])

(setv SNAPSHOT-SELECT
      (+ "SELECT session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status, "
         "backend_kind, backend_ref_json, started_at, last_observed_at, "
         "finished_at, cleaned_at, pr_url, output_snippet, "
         "terminal_cause_json, expected_result_json, retries_used, last_validation_error, "
         "awaiting_response, observed_active_at, result_payload_json, "
         "result_solicitations_used, prompt_unblock_attempts, last_output_change_at, "
         "effective_identity_json "
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
  "schema 適用(oracle migrate: CREATE IF NOT EXISTS + additive ALTER)。"
  (.executescript conn SCHEMA-BATCH)
  (for [[table column definition] ENSURE-COLUMNS]
    (ensure-column conn table column definition))
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
  payload)

(deff terminal-cause-from-dict [payload]
  {:pre [(: payload dict)]
   :post [(: % TerminalCause)]}
  "永続 JSON dict → TerminalCause(oracle の追加 optional field —
   retry_after_seconds / backend_error_code / exit_code / signal — は
   policy 契約外なので落とす。行の JSON はそのまま保たれる)。"
  (TerminalCause :category (get payload "category")
                 :reason (.get payload "reason")
                 :retryable (bool (.get payload "retryable" False))
                 :observed-at (get payload "observed_at")))

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
                            (json.loads (get db-row 25)))})

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
   消さない)。他は excluded の last-write-wins。"
  (.execute conn
    (+ "INSERT INTO agent_sessions ("
       "session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status, "
       "backend_kind, backend_ref_json, started_at, last_observed_at, "
       "finished_at, cleaned_at, pr_url, output_snippet, "
       "terminal_cause_json, expected_result_json, retries_used, last_validation_error, "
       "awaiting_response, observed_active_at, result_payload_json, "
       "result_solicitations_used, prompt_unblock_attempts, last_output_change_at, "
       "effective_identity_json"
       ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
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
       "effective_identity_json = COALESCE(agent_sessions.effective_identity_json, excluded.effective_identity_json)")
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
      (if (is (get snap "expected_result") None)
          None
          (json.dumps (get snap "expected_result") :separators #("," ":")))
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
                      :separators #("," ":")))))
  None)

(deff db-session-get [conn session-id]
  {:pre [(: conn sqlite3.Connection) (: session-id str)]
   :post [(: % (| dict None))]}
  (setv row (.fetchone (.execute conn
                                 (+ SNAPSHOT-SELECT " WHERE session_id = ?")
                                 #(session-id))))
  (if (is row None) None (snapshot-from-db-row row)))

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
  True)

(deff db-session-list [conn filters]
  {:pre [(: conn sqlite3.Connection) (: filters dict)]
   :post [(: % list)]}
  "全行を oracle と同じ ORDER BY で読み、Python 側で filter
   (oracle session_list と同じ構造)。"
  (setv rows (.fetchall (.execute conn
                                  (+ SNAPSHOT-SELECT
                                     " ORDER BY started_at DESC, session_id ASC"))))
  (lfor row rows
        :setv snap (snapshot-from-db-row row)
        :if (list-query-matches snap filters)
        snap))

(deff db-count-active [conn]
  {:pre [(: conn sqlite3.Connection)]
   :post [(: % int)]}
  (len (db-session-list conn {"status" (sorted ACTIVE-STATUSES)})))

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
         (: payload dict)]
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
   唯一の意図的破棄。latch の意味は死んだ process の再促に束縛されている)。"
  (.execute conn
            (+ "UPDATE agent_sessions SET awaiting_response = 0 "
               "WHERE awaiting_response = 1 "
               "AND status NOT IN ('done','failed','exited','stopped','cancelled')"))
  None)

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
   SIGTERM で解放しない挙動込みで oracle parity — conformance restart() は
   TTL 失効を待って再取得する(harness.py:131-149)。"
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
   :post [(: % "None — owner 交代は raise")]}
  "lease 更新(oracle heartbeat_once: 消失・owner 交代は raise —
   worker tick が log して次 tick で再試行する)。"
  (setv current (db-read-lease conn))
  (when (is current None)
    (raise (RuntimeError
             "doeff-agentd lease disappeared while daemon was running")))
  (when (!= (get current "owner_pid") owner-pid)
    (setv got (get current "owner_pid"))
    (raise (RuntimeError
             (+ "doeff-agentd lease owner changed: "
                f"expected {owner-pid} got {got}"))))
  (db-upsert-lease conn owner-pid)
  None)


;; ---------------------------------------------------------------------------
;; writer actor(単一 write connection の直列化点)
;; ---------------------------------------------------------------------------

(defclass StoreActor []
  "SQLite store-of-record への唯一の玄関。connection はコンストラクタ thread で
   開いて migrate まで済ませ(起動失敗を呼び手へ loud に伝播)、以後の実行は
   actor thread に一本化される — 読みも書きも queue を通るので、すべての op
   (read-modify-write 含む)が原子的に直列化される。"
  (defn __init__ [self db-path]
    (setv self.db-path db-path)
    (setv self.conn (open-conn db-path))
    (db-migrate self.conn)
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
      (try
        (setv (get box "value") (op self.conn))
        (except [e Exception]
          (setv (get box "error") e)))
      (.set event)))

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
   再導出する)。pr_url / retries_used(vestigial)は policy 契約外。"
  (SessionRow
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
    :backend-ref (get snap "backend_ref")))

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
   "backend_ref" (or row.backend-ref {})})

(deff snapshot-from-policy-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % dict)]}
  "policy SessionRow から新規 snapshot を作る(launch の booting 行)。
   policy 契約外 field は oracle session_launch と同じ初期値。"
  (setv snap (policy-row-patch row))
  (setv (get snap "pr_url") None)
  (setv (get snap "retries_used") 0)
  snap)

(deff db-merge-policy-row [conn row]
  {:pre [(: conn sqlite3.Connection) (: row SessionRow)]
   :post [(: % "None")]}
  "SessionStoreUpsert の実体: 既存 full 行に policy patch を重ねて upsert
   (actor 内で実行されるので read-modify-write が原子的)。COALESCE 2+1 列の
   保護は SQL 側が持つ。"
  (setv existing (db-session-get conn row.session-id))
  (if (is existing None)
      (db-upsert-snapshot conn (snapshot-from-policy-row row))
      (do
        (setv merged (dict existing))
        (.update merged (policy-row-patch row))
        (db-upsert-snapshot conn merged)))
  None)


(defhandler sqlite-session-store [actor]
  ;; SessionStore substrate effect の host 束縛(DOE-004 R1)。すべて actor
  ;; 経由 = 直列化済み。oracle monitor は backend_kind="tmux" も filter する
  ;; (:3486)が、Hy host の行は launch 経路しか作らないので常に tmux —
  ;; effect 契約(active_statuses のみ)を保つ。
  (SessionStoreListActive []
    (setv snaps (.submit actor
                         (fn [conn]
                           (db-session-list conn {"status" (sorted ACTIVE-STATUSES)}))))
    (resume (lfor s snaps (snapshot-to-policy-row s))))

  (SessionStoreGet [session-id]
    (setv snap (.submit actor (fn [conn] (db-session-get conn session-id))))
    (resume (if (is snap None) None (snapshot-to-policy-row snap))))

  (SessionStoreUpsert [row]
    (.submit actor (fn [conn] (db-merge-policy-row conn row)))
    (resume None))

  (SessionStoreResultPayload [session-id]
    (resume (.submit actor
                     (fn [conn] (db-current-result-payload conn session-id)))))

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
