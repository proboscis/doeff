(require doeff-hy.macros [<- do!])
(require doeff-hy.handle [defhandler])

(import hashlib)
(import json)
(import pickle)
(import httpx)
(import pathlib [Path])

(import doeff_core_effects.effects [Await HttpRequest HttpResponse slog])


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
      (<- (slog "http_request"
                :method request.method
                :url request.url
                :status response.status
                :final-url response.url
                :elapsed-seconds response.elapsed-seconds
                :attempt (+ attempt-index 1)))
      (if (and (>= response.status 500) (< attempt-index request.max-retries))
          (do
            (<- (Await (sleep (_retry-delay-seconds attempt-index))))
            (<- next-response
                (_perform-request-attempt client request sleep (+ attempt-index 1)))
            next-response)
          response)
      (except [e httpx.RequestError]
        (if (= attempt-index request.max-retries)
            (raise e)
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
                         :timeout request.timeout-seconds
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


(defn _fixture-key [request]
  (setv payload {"method" request.method
                 "url" request.url
                 "params" (_sorted-mapping request.params)
                 "body_sha256" (_body-sha256 request.body)})
  (setv encoded (.encode (json.dumps payload :sort-keys True :separators #("," ":"))
                         "utf-8"))
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


(defn _record-fixture-response [path fixtures key response]
  (when (not (isinstance response HttpResponse))
    (raise (TypeError (+ "HttpRequest fixture recorder received non-HttpResponse: "
                         (repr response)))))
  (setv (get fixtures key) (_response-to-record response))
  (_write-fixtures path fixtures))


(defn _replay-fixture-response [fixtures key request]
  (when (not-in key fixtures)
    (raise (KeyError (+ "No recorded HTTP fixture for " (repr request)))))
  (_response-from-record (get fixtures key)))


(defn _response-to-record [response]
  {"status" response.status
   "headers" response.headers
   "content" response.content
   "text" response.text
   "url" response.url
   "elapsed_seconds" response.elapsed-seconds})


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
  "Record HttpRequest responses by delegating to the outer HTTP handler."
  (HttpRequest []
    (setv key (_fixture-key effect))
    (<- response effect)
    (_record-fixture-response path fixtures key response)
    (resume response)))


(defhandler _http-fixture-replay-handler [fixtures]
  "Replay HttpRequest responses from loaded fixture records."
  (HttpRequest []
    (setv key (_fixture-key effect))
    (resume (_replay-fixture-response fixtures key effect))))
