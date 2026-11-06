;;; The real handler for HttpRequest (http-production-handler), the record-and-replay handler (http-fixture-handler), and how to build the real client
;;; (http-client-factory).
;;;
;;; Contract for holding idle connections: the client holds an idle connection up to CLIENT-KEEPALIVE-SECONDS (60 seconds), and the peer server holds it
;;; longer than that (server cutoff > client limit -- doeff's listening aiohttp-http-server uses IDLE-CONNECTION-SECONDS of 3630 seconds, and
;;; the doeff-cluster endpoint uses 120 seconds). So the client writing to a connection the server closed first happens only on things like a server restart. In that case,
;;; if the peer's close (FIN) has arrived, httpcore discards that connection before sending and reconnects. A request closed after being sent is not resent by httpx or httpcore,
;;; and whether to resend is decided only by max-retries of HttpRequest (non-idempotent writes are issued with 0 retries).
;;;
;;; Contract for the number of requests handed to the client at once: per client scope, the handler holds two tiers of scheduler semaphores --
;;; CLIENT-ORIGIN-MAX-IN-FLIGHT slots per destination (scheme, host, port), and CLIENT-MAX-CONNECTIONS slots overall (the same number as httpcore's connection limit).
;;; A request takes the destination permit and then the overall permit before being handed to the client, and overflowing requests wait in the scheduler's FIFO, not in httpcore's queue. httpcore 1.0.9's
;;; AsyncConnectionPool checks "the whole queue x all connections" each time one request finishes, so when the queue reached thousands it used up the threads of the await bridge
;;; (screen server, 2026-10-26 16:18 UTC -- 10,651 waiting). Make the destination slots smaller than the overall slots, so that one destination (its long-polls or overflow) cannot consume the overall slots
;;; and starve short requests to other destinations. Long-polls and short requests to the same destination share the same slots (FIFO within the destination's slots).
;;;
;;; Leak of connection slots (same card): after assigning a request a new connection (not yet connected -- info is CONNECTING), httpcore 1.0.9 leaves that connection in the pool if the request is
;;; cancelled or times out before it starts using the connection. The leftover connection is neither closed nor free, and nobody will connect it, so it holds one of the connection limit
;;; indefinitely. Once as many accumulate as the limit, all later requests become PoolTimeout (screen server, 17:38 UTC -- the bridge idle for 20 minutes).
;;; The upstream fix (encode/httpcore PRs #1099 and #986) is not released, so every time a request ends by exception or cancellation, CONNECTING connections held by no request are
;;; removed from the pool (_reap-orphan-connections).
(require doeff-hy.macros [defk deff <- do! val])
(val MODULE-TAGS {:context "http" :role "foundation"})
(require doeff-hy.handle [defhandler])

(import hashlib)
(import json)
(import pickle)
(import time)
(import ssl)
(import httpx)
(import pathlib [Path])

(import collections.abc [Callable])
(import doeff [EffectBase Program])
(import doeff.program [ProgramHandler])
(import doeff_core_effects._effects [Await HttpRequest HttpResponse SlogEffect slog])
(import doeff_core_effects._http_effects [HostNotResolved HostResolved HttpFailed HttpFailureKind HttpPhases ResolveFailureKind ResolveHost])
(import doeff_core_effects._scheduler [AcquireSemaphore CreateSemaphore ReleaseSemaphore Semaphore shielded])


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


;; Seconds the client holds idle connections (the contract in the header comment). Longer than the interval between calls, so that TCP is not re-established and the name re-resolved on every call -- writes to the recording service
;; are spaced tens of seconds apart (10-30 seconds in the production screen job's timing), and with httpx's default 5 seconds, every write spaced more than 5 seconds apart reconnected (at 26-30 seconds
;; spacing one write took 165-190 ms, versus 14-22 ms inside the service). Keep it shorter than the seconds after which the peer server closes idle connections.
(val CLIENT-KEEPALIVE-SECONDS 60.0)
;; Limits on the number of connections and on the number of idle connections held -- the same values as httpx 0.28's AsyncClient defaults (no reason to change them).
(val CLIENT-MAX-CONNECTIONS 100)
(val CLIENT-MAX-KEEPALIVE-CONNECTIONS 20)
(val CLIENT-LIMITS (httpx.Limits :max-connections CLIENT-MAX-CONNECTIONS :max-keepalive-connections CLIENT-MAX-KEEPALIVE-CONNECTIONS
                                 :keepalive-expiry CLIENT-KEEPALIVE-SECONDS))
;; Upper limit on requests handed to the client at once for one destination (the contract in the header comment). Smaller than the overall limit CLIENT-MAX-CONNECTIONS, leaving the remaining slots for other destinations.
(val CLIENT-ORIGIN-MAX-IN-FLIGHT 64)


(defclass ClientGate []
  "Permits for one client scope (the contract in the header comment): total = overall permit (scheduler semaphore, CLIENT-MAX-CONNECTIONS slots); origins = destination key ->
   destination permit (created at the tick when a destination is first fired). It does not leave the scope, and is discarded with the client at the end of the scope."
  (defn #^ None __init__ [self #^ Semaphore total]
    "Takes the overall permit and starts the destination permits empty."
    (setv self.total total)
    (setv #^ (get dict #((| tuple str) Semaphore)) self.origins {})
    None))


(defk _origin-key [url]
  {:pre [(: url str)] :post [(: % (| tuple str))] :tags {:context "http" :role "foundation"}}
  "Builds the key (scheme, host, port) for looking up the destination permit. For a URL httpx cannot read, the URL itself is the key (the request is refused by the client)."
  (try
    (val parsed (httpx.URL url))
    #(parsed.scheme parsed.host parsed.port)
    (except [httpx.InvalidURL]
      url)))


(defk _origin-permit [gate url]
  {:pre [(: gate ClientGate) (: url str)] :post [(: % Semaphore)] :tags {:context "http" :role "foundation"}}
  "Looks up the destination permit for url (created with CLIENT-ORIGIN-MAX-IN-FLIGHT slots if it does not exist yet)."
  (<- key (| tuple str) (_origin-key url))
  (val known (.get gate.origins key))
  (when (is-not known None)
    (return known))
  (<- created (CreateSemaphore CLIENT-ORIGIN-MAX-IN-FLIGHT))
  ;; If another request to the same destination placed one first while this was being created, use that.
  (.setdefault gate.origins key created))


(defn _client-pools [client]
  "The list of httpcore connection pools the client holds (the default transport, and mounts added by proxy env). Empty for a stand-in
   client that holds no httpcore pool."
  (setv transports (+ [(getattr client "_transport" None)] (list (.values (getattr client "_mounts" {})))))
  (lfor transport transports
        :setv pool (getattr transport "_pool" None)
        :if (and (is-not pool None) (hasattr pool "_connections") (hasattr pool "_requests"))
        pool))


(defn #^ int _reap-orphan-connections [client]
  "Removes CONNECTING connections held by no request (the leak in the header comment) from the client's pool, and answers the number removed. They are connections not yet connected, so there is no I/O to close them.
   If requests are waiting in the queue after removal, reassign them to the freed slots (connections the assignment closes are closed in the bridge loop)."
  (setv reaped 0)
  (for [pool (_client-pools client)]
    (setv claimed (sfor request pool._requests
                        :if (is-not (getattr request "connection" None) None)
                        (id request.connection)))
    (setv orphans (lfor connection pool._connections
                        :if (and (= (.info connection) "CONNECTING") (not-in (id connection) claimed))
                        connection))
    (when orphans
      (for [connection orphans]
        (.remove pool._connections connection))
      (+= reaped (len orphans))
      (when (any (gfor request pool._requests (.is-queued request)))
        (setv closing (._assign-requests-to-connections pool))
        (when closing
          (import asyncio)
          (asyncio.ensure-future (._close-connections pool closing))))))
  reaped)


(defn :async _sent-or-reaped [client #** request-arguments]
  "Awaits client.request, and when it ends by exception or cancellation, removes leaked connections from the client's pool before raising (the leak in the header comment)."
  (try
    (return (await (.request client #** request-arguments)))
    (except [BaseException]
      (_reap-orphan-connections client)
      (raise))))


(deff http-client-factory [* #^ bool [trust-env True] #^ (| ssl.SSLContext bool) [verify True]]  ; defk にできない: the client builder (the client-factory value) that the handler calls as a plain function outside any Program at the start of the scope
  {:pre [(: trust-env bool) (: verify (| ssl.SSLContext bool))] :post [(: % httpx.AsyncClient)]}
  "Builds one real HTTP client: how idle connections are held is CLIENT-LIMITS (the contract in the header comment); everything else stays at the httpx defaults.
   trust-env = whether to read proxy env and .netrc; verify = what to check the peer's certificate with (an SSLContext or a boolean) -- both default to the same as httpx.
   The default client-factory of http-production-handler and http-fixture-handler. An assembly built with its own arguments receives the same way of holding connections in the form
   :client-factory (fn [] (http-client-factory :trust-env False))."
  (httpx.AsyncClient :limits CLIENT-LIMITS :trust-env trust-env :verify verify))


(defn _asyncio-sleep [delay]
  (import asyncio)
  (asyncio.sleep delay))


(defn _loop-getaddrinfo [host]
  "Look host up with the running loop's resolver — the same getaddrinfo an HTTP client's new connection makes — without
   connecting (the resolver of http-production-handler's ResolveHost unless a caller hands another)."
  (import asyncio)
  (import socket)
  (.getaddrinfo (asyncio.get-running-loop) host None :type socket.SOCK-STREAM))


(defn :async _resolved-or-not [resolver host timeout-seconds]
  "Await resolver's lookup of host within timeout-seconds and answer what came, as a value (ResolveHost — never raises for a
   lookup that gave no address). The kind comes from the error's class: the resolver's own error (socket.gaierror) is NOT-RESOLVED,
   the time limit running out is TIMED-OUT."
  (import asyncio)
  (import socket)
  (try
    (setv infos (await (asyncio.wait-for (resolver host) timeout-seconds)))
    (HostResolved :addresses (tuple (.fromkeys dict (gfor info infos (str (get (get info 4) 0))))))
    (except [error socket.gaierror]
      (HostNotResolved :kind ResolveFailureKind.NOT-RESOLVED :detail (_failure-detail error)))
    (except [TimeoutError]
      (HostNotResolved :kind ResolveFailureKind.TIMED-OUT
                       :detail (.format "TimeoutError: no address for {} within {} s" host timeout-seconds)))))


;; The effects http-production-handler answers, and the effects the clauses emit (the value in one place in the declaration below). ResolveHost is an effect that performs on its own the name resolution
;; the HTTP connection does internally; it uses the same resolver as the connection, so the same handler answers it.
(val HTTP-PRODUCTION-HANDLES #(HttpRequest ResolveHost))
(val HTTP-PRODUCTION-EFFECTS #(Await SlogEffect CreateSemaphore AcquireSemaphore ReleaseSemaphore))


(defn #^ ProgramHandler http-production-handler
  [* #^ (get Callable #([] object)) [client-factory http-client-factory] #^ (get Callable #([float] object)) [sleep _asyncio-sleep]
   #^ (get Callable #([str] object)) [resolver _loop-getaddrinfo]]
  "Handle HttpRequest with retry/backoff through one async HTTP client per covered scope: each time the handler value is
   installed around a program, a new client is made when that program starts and closed when it ends — the same value can
   be installed around many scopes (a value made once and installed per request used to reuse the
   client the first scope had already closed). Also handle ResolveHost — the name lookup alone, with resolver (host -> the
   awaitable of getaddrinfo's rows; the default is the running loop's getaddrinfo, the one the client's connections use)."
  (defn #^ Program scoped-handler [#^ (| Program EffectBase) program]
    (_run-with-scoped-client client-factory sleep resolver program))
  (_copy-handler-metadata scoped-handler (_http-production-handler None None sleep resolver))
  ;; Put the same declaration in the installer made on every call too -- so that the bundle of stacked_handlers can build the union of the inner handlers' declarations (reading of #3724).
  (setv scoped-handler.__doeff_handles__ HTTP-PRODUCTION-HANDLES
        scoped-handler.__doeff_effects__ HTTP-PRODUCTION-EFFECTS)
  scoped-handler)

;; The clauses cannot be read statically (it returns a function that wraps the client's lifetime), so declare the effects answered and the effects the clauses emit (of doeff-effect-analyzer's
;; __doeff_handles__ / __doeff_effects__ -- so that the check of how closed the production foundation is does not count it as an "unreadable handler"; #2337). Both the function itself and
;; the installer the function returns carry this one-place value.
(setv http-production-handler.__doeff_handles__ HTTP-PRODUCTION-HANDLES
      http-production-handler.__doeff_effects__ HTTP-PRODUCTION-EFFECTS)


(defn #^ ProgramHandler http-fixture-handler
  [#^ (| str Path) fixture-path * #^ str mode #^ (get Callable #([] object)) [client-factory http-client-factory]
   #^ (get Callable #([float] object)) [sleep _asyncio-sleep]]
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


(defn #^ Program _run-with-scoped-client [#^ (get Callable #([] object)) client-factory #^ (get Callable #([float] object)) sleep
                                          #^ (get Callable #([str] object)) resolver #^ (| Program EffectBase) program]
  "Run program under a client made for this scope only (with this scope's permits — the head comment's contract), and close that
   client when the scope ends."
  (do!
    (setv client (client-factory))
    (try
      (<- total (CreateSemaphore CLIENT-MAX-CONNECTIONS))
      (<- result ((_http-production-handler client (ClientGate total) sleep resolver) program))
      result
      (finally
        ;; Shielded: a cancelled task's waits raise again, and the client must still close.
        (<- (shielded (Await (.aclose client))))))))


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


(defn _perform-request-with-retries [client gate request sleep]
  (_perform-request-attempt client gate request sleep 0))


(defk _permitted [permit program]
  {:pre [(: permit Semaphore) (: program Program)] :post [(: % "program answer")] :tags {:context "http" :role "foundation"}}
  "Hold one permit of permit while program runs, and give it back however program ends (an answer, an error or a Cancel)."
  (<- (AcquireSemaphore permit))
  (try
    (<- answer program)
    answer
    (finally (<- (ReleaseSemaphore permit)))))


(defk _perform-request-gated [client gate request]
  {:pre [(: client "client made by client-factory (httpx.AsyncClient or a stand-in)") (: gate ClientGate) (: request HttpRequest)] :post [(: % "client answer (HttpResponse)")] :tags {:context "http" :role "foundation"}}
  "Hand one attempt to client only while holding its origin's permit and then the client-wide permit (the head comment's contract) —
   so the requests waiting beyond the limits wait in the scheduler, not in httpcore's queue. A retry's back-off holds no permit."
  (<- origin Semaphore (_origin-permit gate request.url))
  (<- response (_permitted origin (_permitted gate.total (_perform-request-once client request))))
  response)


(defn _perform-request-attempt [client gate request sleep attempt-index]
  (do!
    (try
      (<- response (_perform-request-gated client gate request))
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
                (_perform-request-attempt client gate request sleep (+ attempt-index 1)))
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
                  (_perform-request-attempt client gate request sleep (+ attempt-index 1)))
              next-response))))))


;; Names of httpcore trace events that split the interval (trace-phases of HttpRequest).
(val TRACE-CONNECT-STARTED "connection.connect_tcp.started")
(val TRACE-CONNECT-COMPLETE "connection.connect_tcp.complete")
(val TRACE-SEND-STARTED "http11.send_request_headers.started")
(val TRACE-SEND-COMPLETE "http11.send_request_body.complete")
(val TRACE-HEAD-COMPLETE "http11.receive_response_headers.complete")
(val TRACE-BODY-COMPLETE "http11.receive_response_body.complete")


(defclass PhaseTrace []
  "The httpcore trace callback of one request that set trace-phases: it keeps when each event fired (marks — httpcore event name
   -> time.monotonic()), so the handler can read the exchange's phases after the answer."
  (defn #^ None __init__ [self]
    "Start with no event seen."
    (setv #^ (get dict #(str float)) self.marks {})
    None)
  (defn :async #^ None __call__ [self #^ str name #^ object info]
    "Keep the time event name fired (httpcore awaits this for every event of the request)."
    (setv (get self.marks name) (time.monotonic))
    None))


(defk _traced-phases [tracer started]
  {:pre [(: tracer (| PhaseTrace None)) (: started float)] :post [(: % (| HttpPhases None))] :tags {:context "http" :role "foundation"}}
  "Read one exchange's phases (HttpPhases) from its trace marks and the time the request was handed to the client — None when the
   request did not set trace-phases (no tracer), or an event the phases need did not fire (an HTTP/2 or a non-httpcore transport
   names its events otherwise)."
  (when (is tracer None)
    (return None))
  (val marks tracer.marks)
  (when (any (gfor name #(TRACE-SEND-STARTED TRACE-SEND-COMPLETE TRACE-HEAD-COMPLETE TRACE-BODY-COMPLETE) (not-in name marks)))
    (return None))
  (val new-connection (in TRACE-CONNECT-COMPLETE marks))
  (val connect (if new-connection (- (get marks TRACE-CONNECT-COMPLETE) (get marks TRACE-CONNECT-STARTED)) 0.0))
  (HttpPhases :new-connection new-connection
              :connect-seconds connect
              :acquire-seconds (max 0.0 (- (get marks TRACE-SEND-STARTED) started connect))
              :send-seconds (- (get marks TRACE-SEND-COMPLETE) (get marks TRACE-SEND-STARTED))
              :wait-seconds (- (get marks TRACE-HEAD-COMPLETE) (get marks TRACE-SEND-COMPLETE))
              :receive-seconds (- (get marks TRACE-BODY-COMPLETE) (get marks TRACE-HEAD-COMPLETE))))


(defn _perform-request-once [client request]
  (do!
    (setv request-parts (_request-headers-and-content request))
    (setv headers (get request-parts 0))
    (setv content (get request-parts 1))
    (setv tracer (if request.trace-phases (PhaseTrace) None))
    (setv started (time.monotonic))
    (<- response
        (Await (_sent-or-reaped client
                                :method request.method
                                :url request.url
                                :headers headers
                                :params request.params
                                :content content
                                :timeout (if (is request.connect-timeout-seconds None)
                                             request.timeout-seconds
                                             (httpx.Timeout request.timeout-seconds :connect request.connect-timeout-seconds))
                                :follow-redirects request.follow-redirects
                                ;; pass trace only for requests with the flag (a stand-in client without an extensions argument still answers requests without the flag as before).
                                #** (if (is tracer None) {} {"extensions" {"trace" tracer}}))))
    (<- phases (| HttpPhases None) (_traced-phases tracer started))
    (setv answer (HttpResponse :status response.status-code
                               :headers (dict response.headers)
                               :content response.content
                               :text response.text
                               :url (str response.url)
                               :elapsed-seconds (.total-seconds response.elapsed)
                               :phases phases))
    ;; Break the cycle between the httpx response and the body stream. The httpx client reattaches a body stream to the response, and that stream
    ;; points back at the response (Response.stream = BoundAsyncStream(..., response=self)), so as is, the response and the body bytes/str are not released by reference counts and
    ;; stay until a cyclic GC round. A free-threading CPython widens the GC interval in proportion to the number of live objects and does not return memory released by GC to the machine,
    ;; so the resident size of a job that receives large responses every round kept rising. The body has been read and copied into the answer above, so replace the stream with an empty stream (public field stream).
    (setv response.stream (httpx.ByteStream b""))
    answer))


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
   a value. log-each-request is left out (it only adds log lines). Added the fields after params / body when
   the HttpRequest contract test found replay answering a header-only / retry-only /
   redirect-only difference with the other request's recording."
  (<- params (| (get list (get tuple #(str object))) None) (_sorted-mapping request.params))
  (<- body-sha256 (| str None) (_body-sha256 request.body))
  (val payload {"method" request.method
                "url" request.url
                "params" params
                "body_sha256" body-sha256
                "headers" (if (is request.headers None)
                              None
                              (sorted (gfor #(name value) (.items request.headers) #((.lower name) value))))
                "timeout_seconds" request.timeout-seconds
                "max_retries" request.max-retries
                "follow_redirects" request.follow-redirects
                "failures_as_values" request.failures-as-values})
  (val encoded (.encode (json.dumps payload :sort-keys True :separators #("," ":")) "utf-8"))
  (.hexdigest (hashlib.sha256 encoded)))


(defk _sorted-mapping [mapping]
  {:pre [(: mapping (| (get dict #(str object)) None))] :post [(: % (| (get list (get tuple #(str object))) None))]
   :tags {:context "http" :role "foundation"}}
  "Put the params into one order, so the same params name the same fixture whatever order they were written in."
  (if (is mapping None)
      None
      (sorted (.items mapping))))


(defk _body-sha256 [body]
  {:pre [(: body (| bytes str (get dict #(str object)) None))] :post [(: % (| str None))] :tags {:context "http" :role "foundation"}}
  "Name the request body by its sha256 in the fixture key (a JSON body by its sorted encoding)."
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


(defk _write-fixtures [path fixtures]
  {:pre [(: path Path) (: fixtures dict)] :post [(: % None)] :tags {:context "http" :role "foundation"}}
  "Keep every recorded answer in the fixture file, so a later replay finds what the production handler answered."
  (.mkdir path.parent :parents True :exist-ok True)
  (with [fixture-file (open path "wb")]
    (pickle.dump fixtures fixture-file))
  None)


;; A fixture record is one of three answers, named by "answer" (the fake answers what the
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
  (<- (_write-fixtures path fixtures))
  None)


(defk _replay-fixture-answer [fixtures key request]
  {:pre [(: fixtures dict) (: key str) (: request HttpRequest)] :post [(: % (| HttpResponse HttpFailed))]
   :tags {:context "http" :role "foundation"}}
  "Answer the request named key from its fixture record (a recorded transport error is raised again)."
  (when (not-in key fixtures)
    (raise (KeyError (+ "No recorded HTTP fixture for " (repr request)))))
  (val record (get fixtures key))
  (match (get record "answer")
    "response" (! (_response-from-record record))
    "failed" (get record "failed")
    "raised" (raise (get record "error"))
    other (raise (ValueError (+ "Unknown HTTP fixture answer " (repr other) " for " (repr request))))))


(defk _response-from-record [record]
  {:pre [(: record dict)] :post [(: % HttpResponse)] :tags {:context "http" :role "foundation"}}
  "Answer a replayed request with the HttpResponse that was recorded for it."
  (HttpResponse :status (get record "status")
                :headers (get record "headers")
                :content (get record "content")
                :text (get record "text")
                :url (get record "url")
                :elapsed-seconds (get record "elapsed_seconds")))


(defhandler _http-production-handler [client gate sleep resolver]
  "Handle HttpRequest through the async transport helper, and ResolveHost through resolver (no client, no permit — it makes
   no connection)."
  (HttpRequest []
    (<- response (_perform-request-with-retries client gate effect sleep))
    (resume response))
  (ResolveHost [host timeout-seconds]
    (<- answer (Await (_resolved-or-not resolver host timeout-seconds)))
    (resume answer)))


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
