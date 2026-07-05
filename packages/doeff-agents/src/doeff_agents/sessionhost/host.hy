;;; Hy session host(ADR-DOE-AGENTS-004 C3)— 寿命の外部性の唯一の家。
;;;
;;; oracle = packages/doeff-agentd/src/main.rs。ここが verbatim 移植するのは
;;; host 層の凍結物理: wire 封筒(独自 JSON-lines、main.rs:210-235)・
;;; CLI(parse_args :600-693)・default path(:718-742)・単一インスタンス
;;; 拒否(prepare_socket_path :1079-1092)・serve/dispatch(:1159-1301)。
;;;
;;; C3 実行設計(ACP plan)の実装順 4 まで: wire 封筒 + store-of-record
;;; (store.hy の StoreActor / lease / latch clear)+ RPC→program 写像
;;; (launch-session / monitor-cycle / capture / send / cancel / cleanup)+
;;; monitor / heartbeat thread + result 経路(await_result の blocking poll /
;;; report_result の first-write-wins)。全 10 契約 method 実装済み。
;;;
;;; report-result-mcp stdio relay は relaymain.py(stdlib-only 純 Python)に
;;; 住む: relay の boot レイテンシは report-vs-turn-end race の凍結物理で、
;;; Hy import 連鎖を払うと golden path で solicitation を焼く(S1 実測)。
;;; subcommand dispatch も hostmain.py が Hy import より先に行う。

(require doeff-hy.macros [deff defk <-])

(import dataclasses [dataclass replace])
(import json)
(import os)
(import socket)
(import sqlite3)
(import sys)
(import threading)
(import time)

(import doeff [run])

(import doeff_agents.sessionhost.effects [
  MonitorKnobs
  SessionRow
  clock-now
  session-store-get
  session-store-record-event
  session-store-upsert
  tmux-capture
  tmux-has-session
  tmux-kill-session
  tmux-send-keys])
(import doeff_agents.sessionhost.impls.claude_code [claude-code-impl])
(import doeff_agents.sessionhost.impls.codex [codex-impl])
(import doeff_agents.sessionhost.launch [launch-session])
(import doeff_agents.sessionhost.policy [
  cause-if-absent
  is-terminal-status
  iso-format
  make-cause
  monitor-cycle
  tail-chars])
(import doeff_agents.sessionhost.schema [validate-against-schema])
(import doeff_agents.sessionhost.substrate [real-substrate])
(import doeff_agents.sessionhost.store [
  LEASE-TTL-SECONDS
  StoreActor
  db-acquire-lease
  db-clear-awaiting-latches
  db-count-active
  db-heartbeat-once
  db-read-lease
  db-record-command
  db-record-event
  db-report-result-guarded-update
  db-session-get
  db-session-list
  snapshot-to-wire-dict
  sqlite-session-store])


;; ---------------------------------------------------------------------------
;; 凍結定数(oracle main.rs:18-169)
;; ---------------------------------------------------------------------------

(setv DEFAULT-MONITOR-INTERVAL-MS 1000)
(setv DEFAULT-MAX-RUNNING-SESSIONS 10)
(setv LAUNCH-TIMEOUT-SECONDS 60)
(setv STALE-OBSERVATION-THRESHOLD-SECONDS 300)
(setv DEFAULT-RESULT-SOLICITATION-LIMIT 2)
(setv DEFAULT-PROMPT-STALL-SECONDS 180)
(setv DEFAULT-PROMPT-UNBLOCK-LIMIT 3)
;; sh -c 越しに走るので settings JSON は single-quote で word splitting を
;; 生き延びる(oracle DEFAULT_PROMPT_JUDGE_CMD :163-164 verbatim)。judge は
;; agent session ではなく裁定 subprocess — one-shot 実行が oracle 凍結物理
;; そのものなので live-transport rule の対象外(inline nosemgrep。同定数の
;; もう 1 箇所は oracle main.rs:164)。
(setv DEFAULT-PROMPT-JUDGE-CMD
      "claude -p --settings '{\"disableAllHooks\":true}' --model haiku") ; nosemgrep: doeff-agents-no-claude-print-mode

;; 構造化 wire エラーコード(oracle :107-120)。
(setv RPC-ERR-AWAIT-TIMEOUT -32000)
(setv RPC-ERR-NO-SUCH-SESSION -32001)
(setv RPC-ERR-RESULT-REJECTED -32002)
(setv RPC-ERR-ALREADY-TERMINAL -32003)

;; session.await_result の timeout 物理(oracle :89-103)。
(setv DEFAULT-AWAIT-TIMEOUT-SECONDS 600.0)
(setv MIN-AWAIT-TIMEOUT-SECONDS 1.0)
(setv MAX-AWAIT-TIMEOUT-SECONDS 3600.0)
(setv AWAIT-POLL-INTERVAL-SECONDS 0.5)

;; await が「終端」と見なす status(oracle is_await_terminal_status
;; :2925-2930 — is_terminal_status の 5 つ + lost)。
(setv AWAIT-TERMINAL-STATUSES
      #{"done" "failed" "cancelled" "exited" "stopped" "lost"})

;; wire 上の任意 JSON 値(serde_json::Value 相当)。id / params / result の
;; contract 型 — json.loads が返し得る全形。
(setv JsonValue (| dict list str int float bool None))


(defclass RpcHostError [Exception]
  "構造化 wire エラー(oracle RpcError :241-253)— dispatch が code を
   error_code として封筒に載せる。"
  (defn __init__ [self code message]
    (.__init__ Exception self message)
    (setv self.code code)
    (setv self.message message)))


(defclass [(dataclass :frozen True :kw-only True)] HostConfig []
  "daemon 設定(oracle Config)。monitor-interval は秒(float)で持つ。"
  #^ str db-path
  #^ str socket-path
  #^ str tmux-bin
  #^ float monitor-interval-seconds
  #^ int max-running
  #^ int result-solicitation-limit
  #^ int prompt-stall-seconds
  #^ int prompt-unblock-limit
  #^ (| str None) prompt-judge-cmd)


;; ---------------------------------------------------------------------------
;; env knob / default path(oracle :695-742)
;; ---------------------------------------------------------------------------

(deff env-u32 [name]
  {:pre [(: name str)]
   :post [(: % (| int None))]}
  "非負 int の env 読み(oracle env_u32: parse 失敗は None = 既定へ fallback)。"
  (setv raw (.get os.environ name))
  (when (is raw None) (return None))
  (try
    (setv value (int (.strip raw)))
    (except [ValueError] (return None)))
  (if (>= value 0) value None))

(deff env-positive-i64 [name]
  {:pre [(: name str)]
   :post [(: % (| int None))]}
  "正 int の env 読み(oracle env_positive_i64: 0 以下・parse 失敗は None)。"
  (setv raw (.get os.environ name))
  (when (is raw None) (return None))
  (try
    (setv value (int (.strip raw)))
    (except [ValueError] (return None)))
  (if (> value 0) value None))

(deff normalize-prompt-judge-cmd [raw]
  {:pre [(: raw str)]
   :post [(: % (| str None))]}
  "空白のみの judge cmd は None = judge 無効(oracle normalize_prompt_judge_cmd)。
   conformance は『空文字 = 無効』のこの意味論に依存する(ハザード 1)。"
  (setv trimmed (.strip raw))
  (if (= trimmed "") None trimmed))

(deff home-dir []
  {:pre [True]
   :post [(: % str)]}
  (.get os.environ "HOME" "."))

(deff xdg-state-home []
  {:pre [True]
   :post [(: % str)]}
  (setv explicit (.get os.environ "XDG_STATE_HOME"))
  (if (is-not explicit None)
      explicit
      (os.path.join (home-dir) ".local" "state")))

(deff default-db-path []
  {:pre [True]
   :post [(: % str)]}
  "$XDG_STATE_HOME/doeff/agentd.sqlite(oracle default_db_path :718-720)。"
  (os.path.join (xdg-state-home) "doeff" "agentd.sqlite"))

(deff default-socket-path []
  {:pre [True]
   :post [(: % str)]}
  "$XDG_RUNTIME_DIR/doeff/agentd.sock、無ければ /tmp/doeff-agentd-$USER.sock
   (oracle default_socket_path :722-730。USER → LOGNAME → \"unknown\")。"
  (setv runtime-dir (.get os.environ "XDG_RUNTIME_DIR"))
  (when (is-not runtime-dir None)
    (return (os.path.join runtime-dir "doeff" "agentd.sock")))
  (setv user (or (.get os.environ "USER") (.get os.environ "LOGNAME") "unknown"))
  (os.path.join "/tmp" f"doeff-agentd-{user}.sock"))


;; ---------------------------------------------------------------------------
;; CLI(oracle parse_args :600-693)
;; ---------------------------------------------------------------------------

(deff arg-at [args index]
  {:pre [(: args list) (: index int)]
   :post [(: % (| str None))]}
  "args.get(index) 同等(範囲外は None — oracle は --db/--socket の値欠落を
   黙って default へ fallback する。この quirk も parity)。"
  (if (< index (len args)) (get args index) None))

(deff required-arg [args index flag]
  {:pre [(: args list) (: index int) (: flag str)]
   :post [(: % str)]}
  (setv value (arg-at args index))
  (when (is value None)
    (raise (ValueError f"{flag} requires a value")))
  value)

(deff parse-args [args]
  {:pre [(: args list)]
   :post [(: % HostConfig)]}
  "oracle parse_args の verbatim 移植: flag 網羅・env knob の parse 時読み・
   unknown argument 拒否・command は serve のみ。"
  (setv db-path None)
  (setv socket-path None)
  (setv tmux-bin "tmux")
  (setv monitor-interval-seconds (/ DEFAULT-MONITOR-INTERVAL-MS 1000))
  (setv max-running DEFAULT-MAX-RUNNING-SESSIONS)
  (setv result-solicitation-limit
        (or (env-u32 "DOEFF_AGENTD_RESULT_SOLICITATIONS")
            DEFAULT-RESULT-SOLICITATION-LIMIT))
  (setv prompt-stall-seconds
        (or (env-positive-i64 "DOEFF_AGENTD_PROMPT_STALL_SECS")
            DEFAULT-PROMPT-STALL-SECONDS))
  (setv prompt-unblock-limit
        (or (env-u32 "DOEFF_AGENTD_PROMPT_UNBLOCK_ATTEMPTS")
            DEFAULT-PROMPT-UNBLOCK-LIMIT))
  (setv prompt-judge-cmd
        (normalize-prompt-judge-cmd
          (.get os.environ "DOEFF_AGENTD_PROMPT_JUDGE_CMD"
                DEFAULT-PROMPT-JUDGE-CMD)))
  (setv command "serve")
  (setv index 0)
  (while (< index (len args))
    (setv arg (get args index))
    (cond
      (= arg "--db")
      (do (+= index 1)
          (setv db-path (arg-at args index)))
      (= arg "--socket")
      (do (+= index 1)
          (setv socket-path (arg-at args index)))
      (= arg "--tmux")
      (do (+= index 1)
          (setv tmux-bin (required-arg args index "--tmux")))
      (= arg "--monitor-interval-ms")
      (do (+= index 1)
          (setv raw (required-arg args index "--monitor-interval-ms"))
          (setv millis (int raw))
          (when (< millis 0)
            (raise (ValueError "--monitor-interval-ms must be non-negative")))
          (setv monitor-interval-seconds (/ millis 1000)))
      (= arg "--max-running")
      (do (+= index 1)
          (setv raw (required-arg args index "--max-running"))
          (setv max-running (int raw))
          (when (< max-running 0)
            (raise (ValueError "--max-running must be non-negative"))))
      (= arg "--result-solicitations")
      (do (+= index 1)
          (setv raw (required-arg args index "--result-solicitations"))
          (setv result-solicitation-limit (int raw))
          (when (< result-solicitation-limit 0)
            (raise (ValueError "--result-solicitations must be non-negative"))))
      (= arg "--prompt-stall-secs")
      (do (+= index 1)
          (setv raw (required-arg args index "--prompt-stall-secs"))
          (setv prompt-stall-seconds (int raw))
          (when (<= prompt-stall-seconds 0)
            (raise (ValueError "--prompt-stall-secs must be positive"))))
      (= arg "--prompt-unblock-attempts")
      (do (+= index 1)
          (setv raw (required-arg args index "--prompt-unblock-attempts"))
          (setv prompt-unblock-limit (int raw))
          (when (< prompt-unblock-limit 0)
            (raise (ValueError "--prompt-unblock-attempts must be non-negative"))))
      (= arg "--prompt-judge-cmd")
      (do (+= index 1)
          (setv raw (required-arg args index "--prompt-judge-cmd"))
          (setv prompt-judge-cmd (normalize-prompt-judge-cmd raw)))
      (= arg "serve")
      (setv command arg)
      True
      (raise (ValueError f"unknown argument: {arg}")))
    (+= index 1))
  (when (!= command "serve")
    (raise (ValueError f"unsupported command: {command}")))
  (HostConfig
    :db-path (or db-path (default-db-path))
    :socket-path (or socket-path (default-socket-path))
    :tmux-bin tmux-bin
    :monitor-interval-seconds monitor-interval-seconds
    :max-running max-running
    :result-solicitation-limit result-solicitation-limit
    :prompt-stall-seconds prompt-stall-seconds
    :prompt-unblock-limit prompt-unblock-limit
    :prompt-judge-cmd prompt-judge-cmd))


;; ---------------------------------------------------------------------------
;; wire 封筒(oracle RpcRequest/RpcResponse :210-235、dispatch_request :1215)
;; ---------------------------------------------------------------------------

(deff ok-response [id result]
  {:pre [(: id JsonValue) (: result JsonValue)]
   :post [(: % str)]}
  "成功封筒: result は常に載る(JSON null も値 — session.get の不在は
   `\"result\":null`)。serde の compact 出力と同じ separators。"
  (json.dumps {"id" id "ok" True "result" result} :separators #("," ":")))

(deff err-response [id message code]
  {:pre [(: id JsonValue) (: message str) (: code (| int None))]
   :post [(: % str)]}
  "失敗封筒: error は常に載り、error_code は構造化エラーのみ
   (skip_serializing_if parity — None のとき field ごと省略)。"
  (setv payload {"id" id "ok" False "error" message})
  (when (is-not code None)
    (setv (get payload "error_code") code))
  (json.dumps payload :separators #("," ":")))


;; ---------------------------------------------------------------------------
;; RPC→program 写像(plan「C3 実行設計」の host 骨格)
;;
;; 各 RPC は C1/C2 の共有 program(launch-session / monitor-cycle)か、下の
;; 小 program(capture / send / cancel / cleanup)を handler stack
;; (sqlite-session-store ∘ real-substrate ∘ codex-impl ∘ claude-code-impl)
;; で実行する。純 read(get / list / daemon.status)は program 化せず store
;; 直読。record_command(監査)は host 所有 — program は event を書く。
;; oracle は record_command → upsert → event の順だが、Hy は program 完了後に
;; command を記録する(commands 表は wire 契約外の監査ログで、conformance が
;; assert するのは events 表のみ)。
;; ---------------------------------------------------------------------------

(deff host-binary-path []
  {:pre [True]
   :post [(: % str)]}
  "result channel に配線する自分自身の実行 path(oracle agentd_binary_path
   :747-752 = current_exe。console script `doeff-sessionhost` の絶対 path)。"
  (setv candidate (get sys.argv 0))
  (if candidate (os.path.abspath candidate) "doeff-sessionhost"))

(defn run-hosted [config actor program]
  "handler stack で program を実行する(RPC 写像と monitor tick の共通経路)。
   substrate(生 IO)と store(SQLite actor)が外側、per-kind impl が内側。"
  (setv result-command (host-binary-path))
  (run ((sqlite-session-store actor)
        ((real-substrate config.tmux-bin)
         ((codex-impl result-command)
          ((claude-code-impl result-command)
           program))))))


(defk require-session-row [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session 行の必須読み(oracle require_session :2257 — 文言 verbatim)。"
  (<- row (session-store-get session-id))
  (when (is row None)
    (raise (RuntimeError f"session is not registered: {session-id}")))
  row)

(defk capture-program [session-id lines]
  {:pre [(: session-id str) (: lines int) (> lines 0)]
   :post [(: % str)]}
  "session.capture(oracle session_capture :1943-1953): live capture +
   snippet(tail 500)/ last_observed_at の書き戻し + session_captured。"
  (<- row (require-session-row session-id))
  (<- text (tmux-capture row.pane-id lines))
  (<- now (clock-now))
  (setv updated (replace row
                         :output-snippet (tail-chars (or text " ") 500)
                         :last-observed-at (iso-format now)))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_captured" updated))
  text)

(defk send-program [session-id message literal submit]
  {:pre [(: session-id str) (: message str) (: literal bool) (: submit bool)]
   :post [(: % SessionRow)]}
  "session.send(oracle session_send :1955-1974): live pane へのキー配送 +
   session_sent event。"
  (<- row (require-session-row session-id))
  (<- _ (tmux-send-keys row.pane-id message literal submit))
  (<- _ (session-store-record-event session-id "session_sent" row))
  row)

(defk cancel-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.cancel(oracle session_cancel :1976-2003): tmux kill(生存時)→
   stopped + cause cancelled(first-write-wins)+ session_cancelled。"
  (<- row (require-session-row session-id))
  (<- exists (tmux-has-session row.session-name))
  (when exists
    (<- _ (tmux-kill-session row.session-name)))
  (<- now (clock-now))
  (setv now-str (iso-format now))
  (setv updated (replace row :status "stopped"
                             :finished-at now-str
                             :last-observed-at now-str))
  (setv updated (cause-if-absent
                  updated (make-cause "cancelled" "session.cancel requested"
                                      now-str)))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_cancelled" updated))
  updated)

(defk cleanup-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.cleanup(oracle session_cleanup :2005-2039): tmux kill(生存時)、
   非終端なら stopped + cause cancelled、finished_at は既存優先、cleaned_at
   刻印 + session_cleaned。"
  (<- row (require-session-row session-id))
  (<- exists (tmux-has-session row.session-name))
  (when exists
    (<- _ (tmux-kill-session row.session-name)))
  (<- now (clock-now))
  (setv now-str (iso-format now))
  (setv updated row)
  (when (not (is-terminal-status updated.status))
    (setv updated (replace updated :status "stopped"))
    (setv updated (cause-if-absent
                    updated
                    (make-cause "cancelled"
                                "session.cleanup stopped a non-terminal session"
                                now-str))))
  (when (is updated.finished-at None)
    (setv updated (replace updated :finished-at now-str)))
  (setv updated (replace updated :cleaned-at now-str
                                 :last-observed-at now-str))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_cleaned" updated))
  updated)


;; ---------------------------------------------------------------------------
;; wire params(serde 既定値の再現)
;; ---------------------------------------------------------------------------

(deff params-object [params method]
  {:pre [(: params JsonValue) (: method str)]
   :post [(: % dict)]}
  "params は object 必須(serde: from_value(Null) は struct へ deserialize
   できない — 全 field optional でも同じ)。"
  (when (not (isinstance params dict))
    (raise (RuntimeError f"invalid params for {method}: expected an object")))
  params)

(deff required-str-param [params key method]
  {:pre [(: params dict) (: key str) (: method str)]
   :post [(: % str)]}
  (setv value (.get params key))
  (when (not (isinstance value str))
    (raise (RuntimeError f"invalid params for {method}: missing field `{key}`")))
  value)

(deff build-launch-program-params [params config]
  {:pre [(: params dict) (: config HostConfig)]
   :post [(: % dict)]}
  "wire LaunchParams(oracle :429-463)→ launch program params。serde 既定値
   (mcp_servers {} / skip_trust_setup False / lifecycle run_to_completion /
   session_env {})を再現し、host 所有値(socket_path / max_running)を注入。"
  (for [key ["session_id" "session_name" "agent_type" "work_dir"]]
    (when (not (isinstance (.get params key) str))
      (raise (RuntimeError
               f"invalid params for session.launch: missing field `{key}`"))))
  {"session_id" (get params "session_id")
   "session_name" (get params "session_name")
   "agent_type" (get params "agent_type")
   "work_dir" (get params "work_dir")
   "command" (.get params "command")
   "prompt" (.get params "prompt")
   "model" (.get params "model")
   "effort" (.get params "effort")
   "mcp_servers" (or (.get params "mcp_servers") {})
   "skip_trust_setup" (bool (.get params "skip_trust_setup" False))
   "lifecycle" (or (.get params "lifecycle") "run_to_completion")
   "session_env" (or (.get params "session_env") {})
   "expected_result" (.get params "expected_result")
   "socket_path" config.socket-path
   "max_running" config.max-running})


(deff wire-snapshot [actor session-id]
  {:pre [(: actor StoreActor) (: session-id str)]
   :post [(: % dict)]}
  "行の fresh read → wire 形(program 完了後の応答用)。"
  (setv snap (.submit actor (fn [conn] (db-session-get conn session-id))))
  (when (is snap None)
    (raise (RuntimeError f"session is not registered: {session-id}")))
  (snapshot-to-wire-dict snap))

(deff record-command [actor session-id command-type payload]
  {:pre [(: actor StoreActor) (: session-id str) (: command-type str)
         (: payload (| dict str))]
   :post [(: % "None")]}
  (.submit actor
           (fn [conn]
             (db-record-command conn session-id command-type "completed"
                                None payload)))
  None)


;; ---------------------------------------------------------------------------
;; result 経路(oracle session_await_result :2052-2111 /
;; build_await_response :2209-2255 / session_report_result :2136-2207)
;; ---------------------------------------------------------------------------

(deff build-await-response [snap]
  {:pre [(: snap dict)]
   :post [(: % dict)]}
  "await の成功応答: result は done かつ contract の永続 payload がある時だけ
   {\"payload\": …}(ADR 0035: 結果源はデータチャネル経由の payload のみ —
   transcript fallback は存在しない)。それ以外は result null +
   validation_error に monitor の記録した reason。"
  (setv response {"session" (snapshot-to-wire-dict snap)})
  (setv result-value None)
  (setv validation-error (get snap "last_validation_error"))
  (when (and (= (get snap "status") "done")
             (is-not (get snap "expected_result") None))
    (setv parse-ok False)
    (setv parsed None)
    (setv raw (get snap "result_payload"))
    (when (is-not raw None)
      (try
        (setv parsed (json.loads raw))
        (setv parse-ok True)
        (except [Exception])))
    (if parse-ok
        (do
          (setv result-value {"payload" parsed})
          (setv validation-error None))
        (when (is validation-error None)
          (setv validation-error
                "session reached 'done' without a reported result payload"))))
  (setv (get response "result") result-value)
  (when (is-not validation-error None)
    (setv (get response "validation_error") validation-error))
  response)


(deff report-result-op [conn session-id payload]
  {:pre [(: conn sqlite3.Connection) (: session-id str) (: payload JsonValue)]
   :post [(: % dict)]}
  "session.report_result の実体(actor 内で 1 op として実行 = 原子的)。
   終端 + payload 有り = idempotent already_reported / 終端 + 無し = -32003 /
   contract 無し = error / schema 不適合 = -32002 + session_result_rejected
   event、payload は永続しない・再検証しない(ADR 0035 R4)/ 適合 =
   first-write-wins guarded UPDATE(status は書かない — done 化は monitor)。"
  (setv snap (db-session-get conn session-id))
  (when (is snap None)
    (raise (RuntimeError f"session is not registered: {session-id}")))
  (setv status (get snap "status"))
  (when (is-terminal-status status)
    (when (is-not (get snap "result_payload") None)
      (return {"accepted" True "already_reported" True}))
    (raise (RpcHostError RPC-ERR-ALREADY-TERMINAL
             (+ f"session '{session-id}' already reached terminal status "
                f"'{status}' without a result"))))
  (setv spec (get snap "expected_result"))
  (when (or (is spec None) (not (isinstance spec dict)))
    (raise (RuntimeError
             (+ f"session '{session-id}' has no result contract; "
                "report_result is not applicable"))))
  (setv reason (validate-against-schema payload (.get spec "payload_schema")
                                        "payload"))
  (when (is-not reason None)
    (db-record-event conn session-id "session_result_rejected"
                     {"session_id" session-id "reason" reason})
    (raise (RpcHostError RPC-ERR-RESULT-REJECTED
             f"reported result does not satisfy its schema: {reason}")))
  ;; byte-faithful 永続化 = serde to_string parity: compact・非 ASCII 素通し・
  ;; **key はソート**(serde_json::Value の Map は BTreeMap — oracle は挿入順
  ;; でなく辞書順で書く。S17 の raw column 突き合わせが検出した実測物理)。
  (setv payload-json (json.dumps payload :sort-keys True
                                 :separators #("," ":")
                                 :ensure-ascii False))
  (setv affected (db-report-result-guarded-update conn session-id payload-json))
  (when (= affected 0)
    (setv fresh (db-session-get conn session-id))
    (when (and (is-not fresh None) (is-not (get fresh "result_payload") None))
      (return {"accepted" True "already_reported" True}))
    (raise (RpcHostError RPC-ERR-ALREADY-TERMINAL
             (+ f"session '{session-id}' finished before the result could be "
                "recorded"))))
  (db-record-event conn session-id "session_result_reported"
                   {"session_id" session-id})
  {"accepted" True})


(deff await-result-blocking [actor session-id timeout-seconds]
  {:pre [(: actor StoreActor) (: session-id str)
         (: timeout-seconds (| int float))]
   :post [(: % dict)]}
  "終端 status まで 500ms poll で block(oracle
   session_await_result_with_interval — deadline は connection thread の
   stack にのみ生きる transient。daemon 再起動で in-flight await は
   socket 切断として落ちる = oracle と同じ)。"
  (setv started (time.monotonic))
  (setv snap (.submit actor (fn [conn] (db-session-get conn session-id))))
  (when (is snap None)
    (raise (RpcHostError RPC-ERR-NO-SUCH-SESSION
             f"no session with id '{session-id}'")))
  (while True
    (when (in (get snap "status") AWAIT-TERMINAL-STATUSES)
      (return (build-await-response snap)))
    (when (>= (- (time.monotonic) started) timeout-seconds)
      (setv secs (int timeout-seconds))
      (raise (RpcHostError RPC-ERR-AWAIT-TIMEOUT
               (+ f"session.await_result timed out after {secs}s "
                  f"for session '{session-id}'"))))
    (time.sleep AWAIT-POLL-INTERVAL-SECONDS)
    (setv snap (.submit actor (fn [conn] (db-session-get conn session-id))))
    (when (is snap None)
      (raise (RpcHostError RPC-ERR-NO-SUCH-SESSION
               f"no session with id '{session-id}'")))))


(deff dispatch-method [method params config actor]
  {:pre [(: method str) (: params JsonValue) (: config HostConfig)
         (: actor StoreActor)]
   :post [(: % JsonValue)]}
  "dispatch_request_result(:1247-1301)。session.await_result /
   session.report_result は C3-impl-4 — それまで not-implemented で loud。
   契約外 method は oracle と同文言の unknown method。"
  (when (= method "daemon.status")
    (return {"state" "running"
             "pid" (os.getpid)
             "db_path" config.db-path
             "socket_path" config.socket-path
             "max_running" config.max-running
             "active_sessions" (.submit actor db-count-active)
             "lease" (.submit actor db-read-lease)}))

  (when (= method "session.launch")
    (setv wire-params (params-object params "session.launch"))
    (setv program-params (build-launch-program-params wire-params config))
    ;; DOE-003 R3 staged enforcement の warning(oracle :1721-1730 verbatim —
    ;; 運用ログは host の外部性。S11b が daemon log でこの文言を assert する)。
    (when (and (= (get program-params "agent_type") "claude")
               (not-in "CLAUDE_CONFIG_DIR" (get program-params "session_env")))
      (setv sid-for-warning (get program-params "session_id"))
      (print (+ "doeff-agentd WARNING: claude session "
                f"{sid-for-warning} launched without an explicit "
                "CLAUDE_CONFIG_DIR auth profile (ADR-DOE-AGENTS-003 R3: "
                "enforcement follows once callers migrate)")
             :file sys.stderr)
      (.flush sys.stderr))
    (setv row (run-hosted config actor (launch-session program-params)))
    (setv sid row.session-id)
    (setv wire (wire-snapshot actor sid))
    (record-command actor sid "session.launch" wire)
    (return wire))

  (when (= method "session.get")
    (setv p (params-object params "session.get"))
    (setv sid (required-str-param p "session_id" "session.get"))
    (setv snap (.submit actor (fn [conn] (db-session-get conn sid))))
    (return (if (is snap None) None (snapshot-to-wire-dict snap))))

  (when (= method "session.list")
    (setv p (params-object params "session.list"))
    (setv filters {"status" (.get p "status")
                   "agent_type" (.get p "agent_type")
                   "backend_kind" (.get p "backend_kind")
                   "lifecycle" (.get p "lifecycle")})
    (setv snaps (.submit actor (fn [conn] (db-session-list conn filters))))
    (return (lfor s snaps (snapshot-to-wire-dict s))))

  (when (= method "session.capture")
    (setv p (params-object params "session.capture"))
    (setv sid (required-str-param p "session_id" "session.capture"))
    (setv lines (int (.get p "lines" 100)))
    (setv text (run-hosted config actor (capture-program sid lines)))
    (return {"text" text}))

  (when (= method "session.send")
    (setv p (params-object params "session.send"))
    (setv sid (required-str-param p "session_id" "session.send"))
    (setv message (required-str-param p "message" "session.send"))
    (setv enter (bool (.get p "enter" True)))
    (setv literal (bool (.get p "literal" True)))
    (run-hosted config actor (send-program sid message literal enter))
    (record-command actor sid "session.send" message)
    (return {"sent" True}))

  (when (= method "session.cancel")
    (setv p (params-object params "session.cancel"))
    (setv sid (required-str-param p "session_id" "session.cancel"))
    (run-hosted config actor (cancel-program sid))
    (setv wire (wire-snapshot actor sid))
    (record-command actor sid "session.cancel" wire)
    (return wire))

  (when (= method "session.cleanup")
    (setv p (params-object params "session.cleanup"))
    (setv sid (required-str-param p "session_id" "session.cleanup"))
    (run-hosted config actor (cleanup-program sid))
    (setv wire (wire-snapshot actor sid))
    (record-command actor sid "session.cleanup" wire)
    (return wire))

  (when (= method "session.await_result")
    (setv p (params-object params "session.await_result"))
    (setv sid (required-str-param p "session_id" "session.await_result"))
    (setv timeout-raw (.get p "timeout_seconds"))
    (setv timeout-seconds
          (if (is timeout-raw None)
              DEFAULT-AWAIT-TIMEOUT-SECONDS
              (float timeout-raw)))
    (setv timeout-seconds (max MIN-AWAIT-TIMEOUT-SECONDS
                               (min MAX-AWAIT-TIMEOUT-SECONDS timeout-seconds)))
    (return (await-result-blocking actor sid timeout-seconds)))

  (when (= method "session.report_result")
    (setv p (params-object params "session.report_result"))
    (setv sid (required-str-param p "session_id" "session.report_result"))
    (when (not-in "payload" p)
      (raise (RuntimeError
               "invalid params for session.report_result: missing field `payload`")))
    (setv payload (get p "payload"))
    (return (.submit actor (fn [conn] (report-result-op conn sid payload)))))

  (raise (RuntimeError f"unknown method: {method}")))

(deff dispatch-line [line config actor]
  {:pre [(: line str) (: config HostConfig) (: actor StoreActor)]
   :post [(: % str)]}
  "1 リクエスト行 → 1 レスポンス行。parse 失敗は id=null の invalid request
   (:1197-1205)。RpcRequest は id + method 必須、params default null。"
  (try
    (setv request (json.loads line))
    (except [e Exception]
      (return (err-response None f"invalid request: {e}" None))))
  (when (not (isinstance request dict))
    (return (err-response None "invalid request: expected an object" None)))
  (when (not-in "id" request)
    (return (err-response None "invalid request: missing field `id`" None)))
  (setv id (get request "id"))
  (setv method (.get request "method"))
  (when (not (isinstance method str))
    (return (err-response None "invalid request: missing field `method`" None)))
  (setv params (.get request "params"))
  (try
    (setv value (dispatch-method method params config actor))
    (ok-response id value)
    (except [e RpcHostError]
      (err-response id e.message e.code))
    (except [e Exception]
      (err-response id (str e) None))))


;; ---------------------------------------------------------------------------
;; serve(oracle prepare_socket_path :1079 / serve :1159 / handle_stream :1182)
;; ---------------------------------------------------------------------------

(deff prepare-socket-path [socket-path]
  {:pre [(: socket-path str)]
   :post [(: % "None — 生存 socket は raise")]}
  "既存 socket の connect probe: 生きていれば単一インスタンス拒否、
   死んでいれば stale unlink(oracle prepare_socket_path)。"
  (when (not (os.path.exists socket-path))
    (return None))
  (setv probe (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
  (setv alive False)
  (try
    (.connect probe socket-path)
    (setv alive True)
    (except [OSError])
    (finally (.close probe)))
  (when alive
    (raise (RuntimeError
             f"doeff-sessionhost is already listening on {socket-path}")))
  (os.remove socket-path)
  None)

(deff handle-stream [conn config actor]
  {:pre [(: conn socket.socket) (: config HostConfig) (: actor StoreActor)]
   :post [(: % "None")]}
  "connection 毎の JSON-lines ループ(oracle handle_stream)。EOF で終了、
   空行は skip、エラーは stderr へ(接続は落とすが daemon は落とさない)。"
  (try
    (with [stream (.makefile conn "rw" :encoding "utf-8" :newline "\n")]
      (while True
        (setv line (.readline stream))
        (when (= line "")
          (break))
        (when (= (.strip line) "")
          (continue))
        (setv response (dispatch-line line config actor))
        (.write stream (+ response "\n"))
        (.flush stream)))
    (except [e Exception]
      (print f"doeff-sessionhost client error: {e}" :file sys.stderr))
    (finally
      (.close conn)))
  None)


(defn run-worker-tick [worker thunk]
  "1 tick の隔離実行(oracle run_worker_tick :3433-3445 — worker thread は
   例外で死なず log して次 tick へ。disk-full storm が両 worker を黙殺した
   傷跡)。"
  (try
    (thunk)
    (except [e Exception]
      (print f"doeff-sessionhost {worker} error: {e}" :file sys.stderr))))


(defn heartbeat-loop [actor owner-pid]
  "lease 更新 loop(oracle heartbeat_loop :3454-3460、interval = TTL/3)。"
  (setv interval (max (// LEASE-TTL-SECONDS 3) 1))
  (while True
    (run-worker-tick
      "heartbeat"
      (fn [] (.submit actor (fn [conn] (db-heartbeat-once conn owner-pid)))))
    (time.sleep interval)))


(deff build-monitor-knobs [config]
  {:pre [(: config HostConfig)]
   :post [(: % MonitorKnobs)]}
  "MonitorKnobs の組み立て。stale / launch timeout は oracle と同じく
   **use-site で env を読む**(:47-50 / :79-85 — conformance が rebuild 無しで
   watchdog を秒単位へ圧縮する調整口。tick 毎に評価される)。"
  (MonitorKnobs
    :prompt-stall-seconds config.prompt-stall-seconds
    :result-solicitation-limit config.result-solicitation-limit
    :prompt-unblock-limit config.prompt-unblock-limit
    :launch-timeout-seconds (or (env-positive-i64 "DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS")
                                LAUNCH-TIMEOUT-SECONDS)
    :stale-observation-seconds (or (env-positive-i64 "DOEFF_AGENTD_STALE_OBSERVATION_SECS")
                                   STALE-OBSERVATION-THRESHOLD-SECONDS)
    :judge-cmd config.prompt-judge-cmd))


(defn monitor-loop [config actor]
  "monitor loop(oracle monitor_loop :3447-3452)。tick = monitor-cycle
   program の実行 — per-session 隔離は program 所有(policy.hy:622、oracle の
   tick 隔離より強い)。run-worker-tick は backstop。"
  (while True
    (run-worker-tick
      "monitor"
      (fn [] (run-hosted config actor (monitor-cycle (build-monitor-knobs config)))))
    (time.sleep config.monitor-interval-seconds)))


(deff serve [config actor]
  {:pre [(: config HostConfig) (: actor StoreActor)]
   :post [(: % "戻らない(accept loop)")]}
  "bind → monitor / heartbeat thread → accept loop(connection 毎 thread、
   oracle serve :1159-1180)。"
  (prepare-socket-path config.socket-path)
  (setv listener (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
  (.bind listener config.socket-path)
  (.listen listener 64)
  (setv monitor (threading.Thread :target monitor-loop
                                  :args #(config actor)
                                  :daemon True
                                  :name "sessionhost-monitor"))
  (.start monitor)
  (setv heartbeat (threading.Thread :target heartbeat-loop
                                    :args #(actor (os.getpid))
                                    :daemon True
                                    :name "sessionhost-heartbeat"))
  (.start heartbeat)
  (while True
    (try
      (setv #(conn _addr) (.accept listener))
      (except [e OSError]
        (print f"doeff-sessionhost accept error: {e}" :file sys.stderr)
        (continue)))
    (setv worker (threading.Thread :target handle-stream
                                   :args #(conn config actor)
                                   :daemon True))
    (.start worker)))


;; ---------------------------------------------------------------------------
;; entry(oracle main :566-598)
;; ---------------------------------------------------------------------------

(defn main []
  "serve entry(console script doeff-sessionhost の serve 経路 — subcommand
   dispatch は hostmain.py 所有で、report-result-mcp は relaymain.py へ
   Hy import より先に分岐済み)。"
  (setv raw (list (cut sys.argv 1 None)))
  (setv config (parse-args raw))
  (for [parent [(os.path.dirname config.db-path)
                (os.path.dirname config.socket-path)]]
    (when parent
      (os.makedirs parent :exist-ok True)))
  ;; oracle main :581-596: open+migrate(StoreActor 構築で完了)→ lease 取得
  ;; (未失効 lease は loud に拒否)→ awaiting_response latch 全 clear。
  (setv actor (StoreActor config.db-path))
  (setv owner-pid (os.getpid))
  (.submit actor (fn [conn] (db-acquire-lease conn owner-pid)))
  (.submit actor (fn [conn] (db-clear-awaiting-latches conn)))
  (serve config actor)
  None)
