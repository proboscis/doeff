;;; 偽の Anthropic の Messages API(127.0.0.1 の小さな HTTP server)— 本物の claude CLI に subagent を 1 つ走らせ、subagent の API の
;;; 呼びに何を答えたかを記す検の相手(card ki-e798888dce4e)。本物の API にも本物の資格にも触らない。
;;;
;;; 振る舞い(要求の中身で決める):
;;;   - 最後の user の message が道具の結果を持つ呼び(本体が subagent の答えを受けた後)= 本文 "done"。
;;;   - 本文に MAIN-MARK を持つ呼び(本体の最初の呼び)= 道具 Agent の呼び(prompt に SUBAGENT-MARK)。
;;;   - ほかの呼び(subagent の呼び)= 本文 "sub done"。
;;;   どの呼びも呼びの番号から決まる usage(SseUsage)を名乗り、受けた呼びを AnsweredCall で記す。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import json)
(import threading)

(val MAIN-MARK "MAIN-TASK-MARK")
(val SUBAGENT-MARK "SUBAGENT-TASK-MARK")
(val AGENT-TOOL-USE-ID "toolu_fake_agent")


(defrecord AnsweredCall
  "偽の API が答えた /v1/messages の呼び 1 つ: role = main-tool(本体の Agent の呼び)・main-final(本体の最後の答え)・subagent /
   message-id = 答えた message の id / model = 要求の model / input・cache-write・cache-read・output = 答えで名乗った 4 種の token /
   request-id = 答えの頭 request-id に置いた要求の id(本物の API と同じ頭 — CLI はこの値を stream-json の行の request_id と transcript の
   requestId に書く・card ki-b99ace070bf0)。"
  (#^ str role)
  (#^ str message-id)
  (#^ str model)
  (#^ int input)
  (#^ int cache-write)
  (#^ int cache-read)
  (#^ int output)
  (#^ str request-id))


(defn #^ str request-text [#^ dict request]
  "要求の message の本文(text の block と文字列の content)を連ねた物。"
  (.join "\n" (gfor message (.get request "messages" [])
                    :setv content (.get message "content")
                    part (if (isinstance content list) content [{"type" "text" "text" (str (or content ""))}])
                    :if (and (isinstance part dict) (= (.get part "type") "text"))
                    (.get part "text" ""))))

(defn #^ bool answers-a-tool [#^ dict request]
  "最後の message が道具の結果を持つか。"
  (setv messages (.get request "messages" []))
  (setv content (if messages (.get (get messages -1) "content") None))
  (and (isinstance content list) (any (gfor part content (and (isinstance part dict) (= (.get part "type") "tool_result"))))))


(defclass FakeSubagentApi [ThreadingHTTPServer]
  "偽の API の server。start で 127.0.0.1 の空いた port に開き、close で閉じる。answered = 答えた呼びの記録(要求の答え手の thread が
   書く)。"
  (defn __init__ [self]
    (.__init__ (super) #("127.0.0.1" 0) FakeSubagentHandler)
    (setv self.lock (threading.Lock)
          self.answered []
          self.thread (threading.Thread :target self.serve-forever :name "fake-subagent-api" :daemon True)))

  (defn start [self]
    (.start self.thread)
    self)

  (defn [property] #^ str url [self]
    (.format "http://127.0.0.1:{}" (get self.server-address 1)))

  (defn #^ tuple seen [self]
    (with [self.lock] (tuple self.answered)))

  (defn close [self]
    (.shutdown self)
    (.server-close self)))


(defclass FakeSubagentHandler [BaseHTTPRequestHandler]
  "http.server の 1 つの要求の答え手(偽の API の振る舞いは頭の註)。"
  (setv protocol-version "HTTP/1.1")

  (defn log-message [self #^ str format #* args] None)

  (defn send-json [self #^ int status body]
    (setv raw (.encode (json.dumps body) "utf-8"))
    (.send-response self status)
    (.send-header self "content-type" "application/json")
    (.send-header self "content-length" (str (len raw)))
    (.end-headers self)
    (.write self.wfile raw))

  (defn do-GET [self]
    (.send-json self 404 {"type" "error" "error" {"type" "not_found_error" "message" "fake api"}}))

  (defn do-POST [self]
    (setv api self.server)
    (assert (isinstance api FakeSubagentApi) (type api))
    (setv raw (.read self.rfile (int (or (.get self.headers "content-length") 0))))
    (when (not (.startswith self.path "/v1/messages"))
      (.send-json self 404 {"type" "error" "error" {"type" "not_found_error" "message" "fake api"}})
      (return None))
    (when (in "count_tokens" self.path)
      (.send-json self 200 {"input_tokens" 1})
      (return None))
    (setv request (json.loads raw)
          model (.get request "model" "claude-fake")
          role (cond (answers-a-tool request) "main-final"
                     (in MAIN-MARK (request-text request)) "main-tool"
                     True "subagent"))
    (with [api.lock]
      (setv number (+ (len api.answered) 1))
      (setv call (AnsweredCall :role role :message-id (.format "msg_fake_{:04d}" number) :model model
                               :input (+ 100 number) :cache-write (* 10 number) :cache-read (+ 1000 number) :output (+ 7 number)
                               :request-id (.format "req_fake_{:04d}" number)))
      (.append api.answered call))
    (if (.get request "stream")
        (.stream-answer self call (if (= role "main-tool") (.tool-blocks self) (.text-blocks self role)))
        (.send-json self 200 {"id" call.message-id "type" "message" "role" "assistant" "model" model
                              "content" [{"type" "text" "text" "done"}] "stop_reason" "end_turn" "stop_sequence" None
                              "usage" {"input_tokens" call.input "output_tokens" call.output
                                       "cache_creation_input_tokens" call.cache-write "cache_read_input_tokens" call.cache-read}})))

  (defn text-blocks [self #^ str role]
    #("end_turn" [#({"type" "text" "text" ""} {"type" "text_delta" "text" (if (= role "subagent") "sub done" "done")})]))

  (defn tool-blocks [self]
    #("tool_use" [#({"type" "tool_use" "id" AGENT-TOOL-USE-ID "name" "Agent" "input" {}}
                    {"type" "input_json_delta"
                     "partial_json" (json.dumps {"description" "probe" "subagent_type" "general-purpose"
                                                 "prompt" (.format "{} reply with one word" SUBAGENT-MARK)})})]))

  (defn stream-answer [self #^ AnsweredCall call blocks]
    (setv #(stop-reason parts) blocks)
    (.send-response self 200)
    (.send-header self "content-type" "text/event-stream")
    (.send-header self "request-id" call.request-id)
    (.send-header self "cache-control" "no-cache")
    (.send-header self "connection" "close")
    (.end-headers self)
    (.event self "message_start" {"type" "message_start"
                                  "message" {"id" call.message-id "type" "message" "role" "assistant" "model" call.model
                                             "content" [] "stop_reason" None "stop_sequence" None
                                             "usage" {"input_tokens" call.input "output_tokens" 1
                                                      "cache_creation_input_tokens" call.cache-write
                                                      "cache_read_input_tokens" call.cache-read}}})
    (for [#(index #(start delta)) (enumerate parts)]
      (.event self "content_block_start" {"type" "content_block_start" "index" index "content_block" start})
      (.event self "content_block_delta" {"type" "content_block_delta" "index" index "delta" delta})
      (.event self "content_block_stop" {"type" "content_block_stop" "index" index}))
    (.event self "message_delta" {"type" "message_delta" "delta" {"stop_reason" stop-reason "stop_sequence" None}
                                  "usage" {"output_tokens" call.output}})
    (.event self "message_stop" {"type" "message_stop"})
    (setv self.close-connection True))

  (defn event [self #^ str name data]
    (.write self.wfile (.encode (.format "event: {}\ndata: {}\n\n" name (json.dumps data)) "utf-8"))
    (.flush self.wfile)))
