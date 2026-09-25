;;; claude の print mode の stdout の 1 行の型と、手番の終わりの型(設計 4.2 — この package が持つ型)。
;;;
;;; 分類(classify-record)は純関数: stream-json の 1 行(JSON の object)→ ClaudeLineKind。語彙の外の行は捨てずに
;;; Other { type, subtype } へ名前だけ持つ(発明しない)。1 行の逐語は ClaudeStreamLine.raw に残る。
;;;
;;; JSON の境界はこの file の 1 か所(parse-record と classify-*)。状態機械(dialogue.hy)も上の層も、分類した型だけを読む
;;; — 生の dict を読み直す 2 か所目を作らない。
(import dataclasses [dataclass field])
(import datetime [datetime])
(import json)
(import doeff_hy.frozen [FrozenMap freeze-json frozen-json-object])
(import doeff_claude_code.values [ClaudeTurn])


;; --- 消費の token ------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Usage []
  "手番の終わり(result の行)の usage。CLI が名乗った数だけを持ち、名乗らない欄は None(0 を発明しない)。
   欄: input-tokens = input_tokens / output-tokens = output_tokens / cache-creation-input-tokens = cache_creation_input_tokens /
   cache-read-input-tokens = cache_read_input_tokens / cache-creation-5m-input-tokens・cache-creation-1h-input-tokens =
   cache_creation.ephemeral_5m_input_tokens・ephemeral_1h_input_tokens / web-search-requests = server_tool_use.web_search_requests /
   service-tier = service_tier。ここに無い欄(CLI の版で増える欄)は捨てる — 要る欄はここへ足す。"
  (setv #^ (| int None) input-tokens None)
  (setv #^ (| int None) output-tokens None)
  (setv #^ (| int None) cache-creation-input-tokens None)
  (setv #^ (| int None) cache-read-input-tokens None)
  (setv #^ (| int None) cache-creation-5m-input-tokens None)
  (setv #^ (| int None) cache-creation-1h-input-tokens None)
  (setv #^ (| int None) web-search-requests None)
  (setv #^ (| str None) service-tier None))


;; --- 行の種類 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Init []
  "system/init: 会話の id・CLI の能力の宣言・model・許可のモード・MCP の server の名。"
  (#^ str session-id)
  (setv #^ (get tuple #(str ...)) capabilities #())
  (setv #^ str model "")
  (setv #^ str permission-mode "")
  (setv #^ (get tuple #(str ...)) mcp-servers #()))

(defclass [(dataclass :frozen True)] AssistantMessage []
  "assistant の message: 本文の text の block を連ねたものと、呼んだ道具の名。"
  (setv #^ str text "")
  (setv #^ (get tuple #(str ...)) tool-names #()))

(defclass [(dataclass :frozen True)] ToolResult []
  "user の行の tool_result(道具の結果が model へ返った)。"
  (setv #^ (get tuple #(str ...)) tool-use-ids #()))

(defclass [(dataclass :frozen True)] PartialMessage []
  "stream_event(--include-partial-messages)。text_delta なら本文の差分。"
  (setv #^ str text-delta ""))

(defclass [(dataclass :frozen True)] ThinkingTokens []
  (setv #^ int estimated 0))

(setv INPUT-FATES #("queued" "started" "completed" "cancelled" "discarded" "refused"))
(setv INPUT-FATE-TERMINAL #("completed" "cancelled" "discarded" "refused"))

(defclass [(dataclass :frozen True)] InputFate []
  "入力の行の運命(command_lifecycle)。state は閉語彙 INPUT-FATES。"
  (#^ str ref)
  (#^ str state))

(defclass [(dataclass :frozen True)] PermissionRequested []
  "道具の前の許可の問い(control_request can_use_tool)。答えは ClaudeAnswerPermission。
   input = 道具の入力(鍵の集合は道具ごとに開いているので凍らせた写像)/ suggestions = CLI の提案の JSON の列(凍らせる)。"
  (#^ str request-id)
  (#^ str tool-name)
  (setv #^ FrozenMap input (field :default-factory FrozenMap))
  (setv #^ (get tuple #(object ...)) suggestions #())
  (defn __post_init__ [self]
    (object.__setattr__ self "input" (frozen-json-object self.input "PermissionRequested.input"))
    (when (not (isinstance self.suggestions tuple))
      (raise (TypeError (.format "PermissionRequested.suggestions は tuple: {!r}" self.suggestions))))
    (object.__setattr__ self "suggestions" (freeze-json self.suggestions))))

(defclass [(dataclass :frozen True)] ControlResponse []
  "control_request への CLI の答え(control_response)。request-id = 結ぶ求めの id / subtype = success | error /
   still-queued = interrupt の答えが名指した、生き残る注入の ref(名乗らなければ空)。"
  (#^ str request-id)
  (#^ str subtype)
  (setv #^ (get tuple #(str ...)) still-queued #()))

(defclass [(dataclass :frozen True)] TaskEvent []
  "CLI の道具の task の開始と報せ(system/task_started・task_notification)。"
  (#^ str task-id)
  (#^ str status))

(defclass [(dataclass :frozen True)] RateLimit []
  (#^ str window)
  (setv #^ (| float None) utilization None)
  (setv #^ (| int None) resets-at None))

(defclass [(dataclass :frozen True)] TurnResult []
  "result の行(CLI の手番の終わり)。host の手番の終わりかどうかは状態機械(dialogue.hy)が決める。
   origin-kind = origin.kind(CLI が自分で起こした手番の印)/ result-text = result の本文 / usage = 消費の token /
   cost-usd = total_cost_usd / api-error-status = API の誤りの HTTP status / input-refs = user_message_uuids(名乗らなければ空)。"
  (#^ str subtype)
  (#^ bool is-error)
  (setv #^ str terminal-reason "")
  (setv #^ str origin-kind "")
  (setv #^ str result-text "")
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ (| int None) api-error-status None)
  (setv #^ (get tuple #(str ...)) input-refs #()))

(defclass [(dataclass :frozen True)] Other []
  "語彙の外の行(名前だけ持つ)。"
  (#^ str type)
  (setv #^ str subtype ""))

(setv ClaudeLineKind (| Init AssistantMessage ToolResult PartialMessage ThinkingTokens InputFate
                        PermissionRequested ControlResponse TaskEvent RateLimit TurnResult Other))

(defclass [(dataclass :frozen True)] ClaudeStreamLine []
  "stdout の 1 行。seq は会話の中で単調増加・at は読んだ時刻(doeff-time の時計)・raw は 1 行の逐語。"
  (#^ int seq)
  (#^ datetime at)
  (#^ ClaudeLineKind kind)
  (#^ str raw))


;; --- 手番の終わり ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Completed []
  "CLI が誤りなく終えた手番。usage = 消費の token(Usage)。"
  (setv #^ str result-text "")
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ (get tuple #(str ...)) input-refs #()))

(defclass [(dataclass :frozen True)] Failed []
  "CLI が誤りで終えた手番。detail = CLI が名乗った文(無ければ subtype)・api-error-status = API の誤りの HTTP status。"
  (#^ str detail)
  (setv #^ (| int None) api-error-status None)
  (setv #^ str terminal-reason "")
  (setv #^ (get tuple #(str ...)) input-refs #()))

(defclass [(dataclass :frozen True)] Interrupted []
  "止めた手番の終わり。surviving-refs = CLI が次の手番として走らせる入力(continued-by がその手番)・
   dropped-refs = 読まれずに捨てられた入力。"
  (setv #^ (get tuple #(str ...)) surviving-refs #())
  (setv #^ (get tuple #(str ...)) dropped-refs #())
  (setv #^ (| ClaudeTurn None) continued-by None))

(defclass [(dataclass :frozen True)] BackendLost []
  "終わりの行を読む前に process が消えた(OOM・kill・host の再起動)。次の手番は同じ ResumeSession で頼めばよい。"
  (#^ str detail))

(setv ClaudeTurnEnd (| Completed Failed Interrupted BackendLost))


;; --- 分類(純関数) ------------------------------------------------------------------------------

(defn #^ dict object-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner dict) inner {}))

(defn #^ str text-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner str) inner ""))

(defn #^ tuple strings-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner list) (tuple (gfor item inner :if (isinstance item str) item)) #()))

(defn #^ (| int None) int-at [value #^ str key]
  "整数ちょうど(bool・文字列・小数は数えない)。"
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (and (isinstance inner int) (not (isinstance inner bool))) inner None))

(defn #^ (| float None) number-at [value #^ str key]
  "数(整数か小数 — bool は数えない)を float で。無ければ None。"
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (and (isinstance inner #(int float)) (not (isinstance inner bool))) (float inner) None))

(defn #^ Usage usage-of [#^ dict usage]
  "result の行の usage の object → Usage(名乗らない欄は None)。"
  (setv cache (object-at usage "cache_creation"))
  (setv tier (.get usage "service_tier"))
  (Usage :input-tokens (int-at usage "input_tokens")
         :output-tokens (int-at usage "output_tokens")
         :cache-creation-input-tokens (int-at usage "cache_creation_input_tokens")
         :cache-read-input-tokens (int-at usage "cache_read_input_tokens")
         :cache-creation-5m-input-tokens (int-at cache "ephemeral_5m_input_tokens")
         :cache-creation-1h-input-tokens (int-at cache "ephemeral_1h_input_tokens")
         :web-search-requests (int-at (object-at usage "server_tool_use") "web_search_requests")
         :service-tier (if (isinstance tier str) tier None)))

(defn #^ (| dict None) parse-record [#^ str line]
  "stdout の 1 行 → JSON の object(壊れた行・object でない行は None)。"
  (setv stripped (.strip line))
  (when (not stripped) (return None))
  (try
    (setv value (json.loads stripped))
    (except [ValueError] (return None)))
  (if (isinstance value dict) value None))

(defn #^ tuple content-blocks [#^ dict record]
  (setv content (.get (object-at record "message") "content"))
  (if (isinstance content list) (tuple (gfor block content :if (isinstance block dict) block)) #()))

(defn classify-assistant [#^ dict record]
  (setv blocks (content-blocks record))
  (AssistantMessage
    :text (.join "" (gfor block blocks :if (= (.get block "type") "text") (text-at block "text")))
    :tool-names (tuple (gfor block blocks :if (= (.get block "type") "tool_use") (text-at block "name")))))

(defn classify-user [#^ dict record]
  (setv ids (tuple (gfor block (content-blocks record) :if (= (.get block "type") "tool_result")
                         (text-at block "tool_use_id"))))
  (if ids (ToolResult :tool-use-ids ids) (Other :type "user")))

(defn classify-system [#^ dict record]
  (setv subtype (text-at record "subtype"))
  (cond
    (= subtype "init")
      (Init :session-id (text-at record "session_id")
            :capabilities (strings-at record "capabilities")
            :model (text-at record "model")
            :permission-mode (text-at record "permissionMode")
            :mcp-servers (tuple (gfor server (or (.get record "mcp_servers") [])
                                      :if (isinstance server dict) (text-at server "name"))))
    (= subtype "thinking_tokens")
      (ThinkingTokens :estimated (or (int-at record "estimated_tokens") 0))
    (= subtype "task_started")
      (TaskEvent :task-id (text-at record "task_id") :status "started")
    (= subtype "task_notification")
      (TaskEvent :task-id (text-at record "task_id") :status (or (text-at record "status") "notified"))
    True (Other :type "system" :subtype subtype)))

(defn classify-rate-limit [#^ dict record]
  (setv info (object-at record "rate_limit_info"))
  (setv window (text-at info "rateLimitType"))
  (setv utilization (.get (object-at (object-at info "unifiedWindows") window) "utilization"))
  (RateLimit :window window
             :utilization (if (and (isinstance utilization #(int float)) (not (isinstance utilization bool)))
                              (float utilization) None)
             :resets-at (int-at info "resetsAt")))

(defn classify-control-request [#^ dict record]
  (setv request (object-at record "request"))
  (if (= (text-at request "subtype") "can_use_tool")
      (PermissionRequested :request-id (text-at record "request_id")
                           :tool-name (text-at request "tool_name")
                           :input (frozen-json-object (object-at request "input") "control_request の request.input")
                           :suggestions (tuple (or (.get request "permission_suggestions") [])))
      (Other :type "control_request" :subtype (text-at request "subtype"))))

(defn classify-control-response [#^ dict record]
  (setv response (object-at record "response"))
  (ControlResponse :request-id (text-at response "request_id")
                   :subtype (text-at response "subtype")
                   :still-queued (strings-at (object-at response "response") "still_queued")))

(defn classify-result [#^ dict record]
  (TurnResult :subtype (text-at record "subtype")
              :is-error (is (.get record "is_error") True)
              :terminal-reason (text-at record "terminal_reason")
              :origin-kind (text-at (object-at record "origin") "kind")
              :result-text (text-at record "result")
              :usage (usage-of (object-at record "usage"))
              :cost-usd (number-at record "total_cost_usd")
              :api-error-status (int-at record "api_error_status")
              :input-refs (strings-at record "user_message_uuids")))

(defn classify-lifecycle [#^ dict record]
  (setv state (text-at record "state"))
  (if (in state INPUT-FATES)
      (InputFate :ref (text-at record "command_uuid") :state state)
      (Other :type "command_lifecycle" :subtype state)))

(defn classify-stream-event [#^ dict record]
  (setv delta (object-at (object-at record "event") "delta"))
  (PartialMessage :text-delta (if (= (text-at delta "type") "text_delta") (text-at delta "text") "")))

(defn classify-record [#^ dict record]
  "stream-json の 1 行 → ClaudeLineKind(純関数・語彙の外は Other)。"
  (setv kind (text-at record "type"))
  (cond
    (= kind "system") (classify-system record)
    (= kind "assistant") (classify-assistant record)
    (= kind "user") (classify-user record)
    (= kind "stream_event") (classify-stream-event record)
    (= kind "command_lifecycle") (classify-lifecycle record)
    (= kind "control_request") (classify-control-request record)
    (= kind "control_response") (classify-control-response record)
    (= kind "rate_limit_event") (classify-rate-limit record)
    (= kind "result") (classify-result record)
    True (Other :type kind :subtype (text-at record "subtype"))))
