;;; Hy session host(ADR-DOE-AGENTS-004 C3)— 寿命の外部性の唯一の家。
;;;
;;; oracle = packages/doeff-agentd/src/main.rs。ここが verbatim 移植するのは
;;; host 層の凍結物理: wire 封筒(独自 JSON-lines、main.rs:210-235)・
;;; CLI(parse_args :600-693)・default path(:718-742)・単一インスタンス
;;; 拒否(prepare_socket_path :1079-1092)・serve/dispatch(:1159-1301)。
;;;
;;; C3 実行設計(ACP plan)の実装順 1 = walking skeleton: daemon.status と
;;; wire 封筒のみ本実装し、契約上存在する他 method は「not implemented
;;; (C3 skeleton)」で loud に落とす — conformance suite を
;;; CONFORMANCE_AGENTD_BIN でこの host に向けると全 S 行が正しく red に
;;; なることを確認するための骨格。DB / lease / monitor は C3-impl-2 以降。

(require doeff-hy.macros [deff])

(import dataclasses [dataclass])
(import json)
(import os)
(import socket)
(import sys)
(import threading)


;; ---------------------------------------------------------------------------
;; 凍結定数(oracle main.rs:18-169)
;; ---------------------------------------------------------------------------

(setv DEFAULT-MONITOR-INTERVAL-MS 1000)
(setv DEFAULT-MAX-RUNNING-SESSIONS 10)
(setv LEASE-NAME "doeff-agentd")
(setv LEASE-TTL-SECONDS 10)
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
(setv REPORT-RESULT-MCP-SUBCOMMAND "report-result-mcp")

;; 構造化 wire エラーコード(oracle :107-120)。
(setv RPC-ERR-AWAIT-TIMEOUT -32000)
(setv RPC-ERR-NO-SUCH-SESSION -32001)
(setv RPC-ERR-RESULT-REJECTED -32002)
(setv RPC-ERR-ALREADY-TERMINAL -32003)

;; 契約上存在する RPC method(dispatch_request_result :1247-1301)。skeleton
;; では daemon.status 以外を「not implemented」で loud に落とすための一覧 —
;; unknown method(契約外)とは区別する。
(setv CONTRACT-METHODS
      #{"daemon.status" "session.launch" "session.get" "session.list"
        "session.capture" "session.send" "session.cancel" "session.cleanup"
        "session.await_result" "session.report_result"})


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


(deff dispatch-method [method params config]
  {:pre [(: method str) (: params JsonValue) (: config HostConfig)]
   :post [(: % JsonValue)]}
  "dispatch_request_result(:1247-1301)。skeleton: daemon.status のみ本実装。
   契約 method は not-implemented で loud、契約外は oracle と同文言の
   unknown method。"
  (when (= method "daemon.status")
    ;; C3-impl-2 で active_sessions / lease を DB 実測に置換する。
    (return {"state" "running"
             "pid" (os.getpid)
             "db_path" config.db-path
             "socket_path" config.socket-path
             "max_running" config.max-running
             "active_sessions" 0
             "lease" None}))
  (if (in method CONTRACT-METHODS)
      (raise (RuntimeError f"not implemented (C3 skeleton): {method}"))
      (raise (RuntimeError f"unknown method: {method}"))))

(deff dispatch-line [line config]
  {:pre [(: line str) (: config HostConfig)]
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
    (setv value (dispatch-method method params config))
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

(deff handle-stream [conn config]
  {:pre [(: conn socket.socket) (: config HostConfig)]
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
        (setv response (dispatch-line line config))
        (.write stream (+ response "\n"))
        (.flush stream)))
    (except [e Exception]
      (print f"doeff-sessionhost client error: {e}" :file sys.stderr))
    (finally
      (.close conn)))
  None)

(deff serve [config]
  {:pre [(: config HostConfig)]
   :post [(: % "戻らない(accept loop)")]}
  "bind → accept loop(connection 毎 thread)。monitor / heartbeat thread は
   C3-impl-2/3 でここに生える(oracle serve :1159-1180)。"
  (prepare-socket-path config.socket-path)
  (setv listener (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
  (.bind listener config.socket-path)
  (.listen listener 64)
  (while True
    (try
      (setv #(conn _addr) (.accept listener))
      (except [e OSError]
        (print f"doeff-sessionhost accept error: {e}" :file sys.stderr)
        (continue)))
    (setv worker (threading.Thread :target handle-stream
                                   :args #(conn config)
                                   :daemon True))
    (.start worker)))


;; ---------------------------------------------------------------------------
;; entry(oracle main :566-598)
;; ---------------------------------------------------------------------------

(deff run-report-result-mcp [args]
  {:pre [(: args list)]
   :post [(: % "None")]}
  "stdio MCP ↔ socket relay(oracle run_report_result_mcp :761-811)。
   C3-impl-4 で実装 — それまでは loud に落とす(黙った成功偽装をしない)。"
  (raise (NotImplementedError
           "report-result-mcp relay is not implemented yet (C3-impl-4)")))

(defn main []
  "console script entry(pyproject: doeff-sessionhost)。subcommand dispatch は
   oracle main と同順: report-result-mcp が最初(DB/lease/serve を触らない)。"
  (setv raw (list (cut sys.argv 1 None)))
  (when (and raw (= (get raw 0) REPORT-RESULT-MCP-SUBCOMMAND))
    (run-report-result-mcp (list (cut raw 1 None)))
    (return None))
  (setv config (parse-args raw))
  (for [parent [(os.path.dirname config.db-path)
                (os.path.dirname config.socket-path)]]
    (when parent
      (os.makedirs parent :exist-ok True)))
  ;; C3-impl-2: open_conn + migrate + acquire_lease + awaiting_response
  ;; latch clear がここに入る(oracle main :581-596)。
  (serve config)
  None)
