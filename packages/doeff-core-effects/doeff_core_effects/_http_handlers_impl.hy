(require doeff-hy.macros [defk <- do! val])
(val MODULE-TAGS {:context "http" :role "foundation"})
(require doeff-hy.handle [defhandler])

(import hashlib)
(import json)
(import pickle)
(import httpx)
(import pathlib [Path])

(import doeff_core_effects.effects [Await HttpRequest HttpResponse SlogEffect slog])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind])


(defn _failure-detail [error]
  "Name why a request never got a response (the transport error's class and text) for HttpFailed."
  (setv text (str error))
  (+ (. (type error) __name__) (if text (+ ": " text) "")))


(defk _failure-kind [error]
  {:pre [(: error httpx.RequestError)] :post [(: % HttpFailureKind)] :tags {:context "http" :role "foundation"}}
  "Name why a request never got a response, as HttpFailureKind, from the transport error's class — never its name or text,
   so an error whose name ends in Timeout without being one (or whose text says \"timed out\") is not read as a timeout."
  ;; ConnectTimeout is a TimeoutException, but no connection was made — the request never reached the server (#2337).
  (match error
    (httpx.ConnectTimeout) HttpFailureKind.CONNECT-FAILED
    (httpx.TimeoutException) HttpFailureKind.TIMED-OUT
    (httpx.ConnectError) HttpFailureKind.CONNECT-FAILED
    _ HttpFailureKind.OTHER))


(defn _default-client-factory []
  (httpx.AsyncClient))


(defn _asyncio-sleep [delay]
  (import asyncio)
  (asyncio.sleep delay))


(defn http-production-handler [* [client-factory _default-client-factory] [sleep _asyncio-sleep]]
  "Handle HttpRequest with a single async HTTP client and retry/backoff."
  (setv client (client-factory))
  (setv handler (_http-production-handler client sleep))
  (_with-client-lifecycle handler client))

;; 節を静的に読めない(client の寿命を包む関数を返す)ので、答える効果と節が出す効果を宣言する(doeff-effect-analyzer の
;; __doeff_handles__ / __doeff_effects__ — 本番の土台の閉じ具合の検が「読めない handler」と数えないため・#2337)。
(setv http-production-handler.__doeff_handles__ #(HttpRequest)
      http-production-handler.__doeff_effects__ #(Await SlogEffect))


(defn http-fixture-handler [fixture-path * mode [client-factory _default-client-factory]
                            [sleep _asyncio-sleep]]
  "Record or replay HttpRequest responses from a pickle fixture file."
  (when (not-in mode ["record" "replay"])
    (raise (ValueError (+ "Unsupported HTTP fixture mode: " (repr mode)))))
  (setv path (Path fixture-path))
  (setv fixtures (_load-fixtures path))
  (if (= mode "record")
      (do
        (setv record-handler (_http-fixture-record-handler path fixtures))
        (setv production-handler (http-production-handler :client-factory client-factory
                                                          :sleep sleep))
        (_compose-recording-handler record-handler production-handler))
      (_http-fixture-replay-handler fixtures)))


(defn _with-client-lifecycle [handler client]
  (defn lifecycle-handler [program]
    (_run-with-client-lifecycle handler client program))
  (_copy-handler-metadata lifecycle-handler handler)
  lifecycle-handler)


(defn _run-with-client-lifecycle [handler client program]
  (do!
    (try
      (<- result (handler program))
      result
      (finally
        (<- (Await (.aclose client)))))))


(defn _compose-recording-handler [record-handler production-handler]
  (defn recording-handler [program]
    (production-handler (record-handler program)))
  (_copy-handler-metadata recording-handler record-handler)
  recording-handler)


(defn _copy-handler-metadata [target source]
  (setv target.__doc__ source.__doc__)
  (setv target._doeff_is_handler_fn True)
  (setv target.__doeff_name__ source.__doeff_name__)
  (setv target.__doeff_handler_data__ source.__doeff_handler_data__))


(defn _perform-request-with-retries [client request sleep]
  (_perform-request-attempt client request sleep 0))


(defn _perform-request-attempt [client request sleep attempt-index]
  (do!
    (try
      (<- response (_perform-request-once client request))
      (when request.log-each-request
        (<- (slog "http_request"
                  :method request.method
                  :url request.url
                  :status response.status
                  :final-url response.url
                  :elapsed-seconds response.elapsed-seconds
                  :attempt (+ attempt-index 1))))
      (if (and (>= response.status 500) (< attempt-index request.max-retries))
          (do
            (<- (Await (sleep (_retry-delay-seconds attempt-index))))
            (<- next-response
                (_perform-request-attempt client request sleep (+ attempt-index 1)))
            next-response)
          response)
      (except [e httpx.RequestError]
        (if (= attempt-index request.max-retries)
            (if request.failures-as-values
                (do
                  (<- kind HttpFailureKind (_failure-kind e))
                  (HttpFailed :url request.url :detail (_failure-detail e) :kind kind))
                (raise e))
            (do
              (<- (Await (sleep (_retry-delay-seconds attempt-index))))
              (<- next-response
                  (_perform-request-attempt client request sleep (+ attempt-index 1)))
              next-response))))))


(defn _perform-request-once [client request]
  (do!
    (setv request-parts (_request-headers-and-content request))
    (setv headers (get request-parts 0))
    (setv content (get request-parts 1))
    (<- response
        (Await (.request client
                         :method request.method
                         :url request.url
                         :headers headers
                         :params request.params
                         :content content
                         :timeout (if (is request.connect-timeout-seconds None)
                                      request.timeout-seconds
                                      (httpx.Timeout request.timeout-seconds :connect request.connect-timeout-seconds))
                         :follow-redirects request.follow-redirects)))
    (HttpResponse :status response.status-code
                  :headers (dict response.headers)
                  :content response.content
                  :text response.text
                  :url (str response.url)
                  :elapsed-seconds (.total-seconds response.elapsed))))


(defn _request-headers-and-content [request]
  (setv headers (if (is request.headers None) None (dict request.headers)))
  (setv body request.body)
  (cond
    (is body None)
    #(headers None)

    (isinstance body bytes)
    #(headers body)

    (isinstance body str)
    #(headers (.encode body "utf-8"))

    True
    (do
      (setv data (_json-body-bytes body))
      (if (is headers None)
          (setv headers {"Content-Type" "application/json"})
          (when (not (_has-header headers "Content-Type"))
            (setv (get headers "Content-Type") "application/json")))
      #(headers data))))


(defn _has-header [headers header-name]
  (setv target (.lower header-name))
  (any (gfor name headers (= (.lower name) target))))


(defn _json-body-bytes [body]
  (.encode (json.dumps body :sort-keys True :separators #("," ":")) "utf-8"))


(defn _retry-delay-seconds [attempt-index]
  (* 0.25 (** 2 attempt-index)))


(defk _fixture-key [request]
  {:pre [(: request HttpRequest)] :post [(: % str)] :tags {:context "http" :role "foundation"}}
  "Name a request by every field that can change its answer, so two requests that the production handler may answer
   differently never share one fixture: method, url, params and body, plus headers (names case-folded), the timeout, how
   many times a 5xx / transport failure is retried, whether redirects are followed, and whether a failure is answered as
   a value. log-each-request is left out (it only adds log lines). Added the fields after params / body for
   agora-redesign #1159 (the HttpRequest contract test found replay answering a header-only / retry-only /
   redirect-only difference with the other request's recording)."
  (val payload {"method" request.method
                "url" request.url
                "params" (_sorted-mapping request.params)
                "body_sha256" (_body-sha256 request.body)
                "headers" (if (is request.headers None)
                              None
                              (sorted (gfor #(name value) (.items request.headers) #((.lower name) value))))
                "timeout_seconds" request.timeout-seconds
                "max_retries" request.max-retries
                "follow_redirects" request.follow-redirects
                "failures_as_values" request.failures-as-values})
  (val encoded (.encode (json.dumps payload :sort-keys True :separators #("," ":")) "utf-8"))
  (.hexdigest (hashlib.sha256 encoded)))


(defn _sorted-mapping [mapping]
  (if (is mapping None)
      None
      (sorted (.items mapping))))


(defn _body-sha256 [body]
  (cond
    (is body None)
    None

    (isinstance body bytes)
    (.hexdigest (hashlib.sha256 body))

    (isinstance body str)
    (.hexdigest (hashlib.sha256 (.encode body "utf-8")))

    True
    (.hexdigest (hashlib.sha256 (_json-body-bytes body)))))


(defn _load-fixtures [path]
  (if (not (.exists path))
      {}
      (with [fixture-file (open path "rb")]
        (pickle.load fixture-file))))


(defn _write-fixtures [path fixtures]
  (.mkdir path.parent :parents True :exist-ok True)
  (with [fixture-file (open path "wb")]
    (pickle.dump fixtures fixture-file)))


;; A fixture record is one of three answers, named by "answer" (agora-redesign #1159 — the fake answers what the
;; production handler answered, failures included):
;;   "response"  an HttpResponse's fields
;;   "failed"    the HttpFailed value (the request set failures-as-values and no response ever arrived)
;;   "raised"    the transport error the production handler raised (failures-as-values unset) — replay raises it again
(defk _record-fixture-answer [path fixtures key answer]
  {:pre [(: path Path) (: fixtures dict) (: key str) (: answer (| HttpResponse HttpFailed httpx.RequestError))]
   :post [(: % None)] :tags {:context "http" :role "foundation"}}
  "Write what the production handler answered for the request named key into the fixture file."
  (setv (get fixtures key) (match answer
                             (HttpResponse) {"answer" "response"
                                             "status" answer.status
                                             "headers" answer.headers
                                             "content" answer.content
                                             "text" answer.text
                                             "url" answer.url
                                             "elapsed_seconds" answer.elapsed-seconds}
                             (HttpFailed) {"answer" "failed" "failed" answer}
                             (httpx.RequestError) {"answer" "raised" "error" answer}))
  (_write-fixtures path fixtures)
  None)


(defk _replay-fixture-answer [fixtures key request]
  {:pre [(: fixtures dict) (: key str) (: request HttpRequest)] :post [(: % (| HttpResponse HttpFailed))]
   :tags {:context "http" :role "foundation"}}
  "Answer the request named key from its fixture record (a recorded transport error is raised again)."
  (when (not-in key fixtures)
    (raise (KeyError (+ "No recorded HTTP fixture for " (repr request)))))
  (val record (get fixtures key))
  (match (get record "answer")
    "response" (_response-from-record record)
    "failed" (get record "failed")
    "raised" (raise (get record "error"))
    other (raise (ValueError (+ "Unknown HTTP fixture answer " (repr other) " for " (repr request))))))


(defn _response-from-record [record]
  (HttpResponse :status (get record "status")
                :headers (get record "headers")
                :content (get record "content")
                :text (get record "text")
                :url (get record "url")
                :elapsed-seconds (get record "elapsed_seconds")))


(defhandler _http-production-handler [client sleep]
  "Handle HttpRequest through the async transport helper."
  (HttpRequest []
    (<- response (_perform-request-with-retries client effect sleep))
    (resume response)))


(defhandler _http-fixture-record-handler [path fixtures]
  "Record HttpRequest answers by delegating to the outer HTTP handler — a response, an HttpFailed value, or the transport
   error it raised (recorded, then raised on as before)."
  (HttpRequest []
    (<- key str (_fixture-key effect))
    (var answer None)
    (try
      (<- delegated effect)
      (:= answer delegated)
      (except [error httpx.RequestError]
        (<- (_record-fixture-answer path fixtures key error))
        (raise error)))
    (<- (_record-fixture-answer path fixtures key answer))
    (resume answer)))


(defhandler _http-fixture-replay-handler [fixtures]
  "Replay HttpRequest answers from loaded fixture records."
  (HttpRequest []
    (<- key str (_fixture-key effect))
    (<- answer (_replay-fixture-answer fixtures key effect))
    (resume answer)))
