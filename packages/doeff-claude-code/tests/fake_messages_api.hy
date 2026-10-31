;;; 偽の Anthropic の Messages API(127.0.0.1 の小さな HTTP server)— 本物の claude CLI を ANTHROPIC_BASE_URL で向け、CLI がどの
;;; access token で呼んだかを記す検の相手。本物の API にも本物の資格にも触らない。
;;;
;;; 振る舞い:
;;;   - POST /v1/messages の Authorization: Bearer <token> を呼びの順に記す(MessagesCall)。取り消した token(revoke)には 401 と
;;;     authentication_error の本文(預かり所が更新した後の上流と同じ形)を返す。
;;;   - 道具を持つ最初の呼び(会話の本体の最初の呼び)は、検が release するまで答えを待たせ(その間に検が資格を替える)、道具 Read の
;;;     呼び(tool_use)を SSE で返す。CLI は道具を走らせてから次の呼びを送る — その呼びの token が、替えた後の token かを検が読む。
;;;   - ほかの呼びは本文 "done" を SSE で返す(stream でない呼びには JSON の message で返す)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import json)
(import threading)

;; 取り消した token への上流の答えの本文(親 card の実測と同じ)。
(val REVOKED-BODY {"type" "error" "error" {"type" "authentication_error" "message" "OAuth access token has been revoked."}})
(val HOLD-SECONDS 60.0)


(defrecord MessagesCall
  "偽の API が受けた /v1/messages の呼び 1 つ: token = Authorization の Bearer の値 / main = 道具を持つ呼び(会話の本体)か /
   status = 返した HTTP の状態。"
  (#^ str token)
  (#^ bool main)
  (#^ int status))


(defclass FakeMessagesApi [ThreadingHTTPServer]
  "偽の API の server(http.server の基底を継ぐ — 記録は要求の答え手の thread が書く)。read-path = 最初の本体の呼びで CLI に読ませる
   file の path。first-main = 最初の本体の呼びが届いた合図・release = 検がその呼びに答えさせる合図(資格を替えた後に鳴らす)。
   start で 127.0.0.1 の空いた port に開き、close で閉じる。"
  (defn __init__ [self #^ str read-path]
    (.__init__ (super) #("127.0.0.1" 0) FakeMessagesHandler)
    (setv self.read-path read-path
          self.lock (threading.Lock)
          self.calls []
          self.revoked (set)
          self.answered-main 0
          self.first-main (threading.Event)
          self.release (threading.Event)
          self.thread (threading.Thread :target self.serve-forever :name "fake-messages-api" :daemon True)))

  (defn start [self]
    (.start self.thread)
    self)

  (defn [property] #^ str url [self]
    (.format "http://127.0.0.1:{}" (get self.server-address 1)))

  (defn revoke [self #^ str token]
    (with [self.lock] (.add self.revoked token)))

  (defn #^ tuple seen [self]
    (with [self.lock] (tuple self.calls)))

  (defn close [self]
    (.set self.release)
    (.shutdown self)
    (.server-close self)))


(defclass FakeMessagesHandler [BaseHTTPRequestHandler]
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
    (assert (isinstance api FakeMessagesApi) (type api))
    (setv raw (.read self.rfile (int (or (.get self.headers "content-length") 0))))
    (when (not (.startswith self.path "/v1/messages"))
      (.send-json self 404 {"type" "error" "error" {"type" "not_found_error" "message" "fake api"}})
      (return None))
    (when (in "count_tokens" self.path)
      (.send-json self 200 {"input_tokens" 1})
      (return None))
    (setv request (json.loads raw)
          token (.removeprefix (or (.get self.headers "Authorization") "") "Bearer ")
          main (bool (.get request "tools")))
    (with [api.lock]
      (setv revoked (in token api.revoked))
      (setv first-main (and main (not revoked) (= api.answered-main 0)))
      (when (and main (not revoked)) (+= api.answered-main 1))
      (.append api.calls (MessagesCall :token token :main main :status (if revoked 401 200))))
    (when revoked
      (.send-json self 401 REVOKED-BODY)
      (return None))
    (when first-main
      (.set api.first-main)
      (.wait api.release HOLD-SECONDS))
    (setv model (.get request "model" "claude-fake"))
    (if (.get request "stream")
        (.stream-answer self model (if first-main (.tool-blocks self api) (.text-blocks self)))
        (.send-json self 200 {"id" "msg_fake" "type" "message" "role" "assistant" "model" model
                              "content" [{"type" "text" "text" "done"}] "stop_reason" "end_turn" "stop_sequence" None
                              "usage" {"input_tokens" 3 "output_tokens" 1}})))

  (defn text-blocks [self]
    #("end_turn" [#({"type" "text" "text" ""} {"type" "text_delta" "text" "done"})]))

  (defn tool-blocks [self api]
    #("tool_use" [#({"type" "tool_use" "id" "toolu_fake_read" "name" "Read" "input" {}}
                    {"type" "input_json_delta" "partial_json" (json.dumps {"file_path" api.read-path})})]))

  (defn stream-answer [self #^ str model blocks]
    (setv #(stop-reason parts) blocks)
    (.send-response self 200)
    (.send-header self "content-type" "text/event-stream")
    (.send-header self "cache-control" "no-cache")
    (.send-header self "connection" "close")
    (.end-headers self)
    (.event self "message_start" {"type" "message_start"
                                  "message" {"id" "msg_fake" "type" "message" "role" "assistant" "model" model "content" []
                                             "stop_reason" None "stop_sequence" None
                                             "usage" {"input_tokens" 3 "output_tokens" 1}}})
    (for [#(index #(start delta)) (enumerate parts)]
      (.event self "content_block_start" {"type" "content_block_start" "index" index "content_block" start})
      (.event self "content_block_delta" {"type" "content_block_delta" "index" index "delta" delta})
      (.event self "content_block_stop" {"type" "content_block_stop" "index" index}))
    (.event self "message_delta" {"type" "message_delta" "delta" {"stop_reason" stop-reason "stop_sequence" None}
                                  "usage" {"output_tokens" 2}})
    (.event self "message_stop" {"type" "message_stop"})
    (setv self.close-connection True))

  (defn event [self #^ str name data]
    (.write self.wfile (.encode (.format "event: {}\ndata: {}\n\n" name (json.dumps data)) "utf-8"))
    (.flush self.wfile)))
