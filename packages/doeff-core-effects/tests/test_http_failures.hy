(require doeff-hy.macros [deftest defk <- with-handler])

;;; failures-as-values for HttpRequest (so a caller can read unreachable-peer failures without knowing the exception types of the transport library):
;;;   * if set, a failure where no response ever came is answered with HttpFailed (url, detail = the exception's class and message, kind = the category of failure)
;;;   * if not set, the transport exception is raised as before (current users are unchanged)
;;;   These two are checked by test_http_request_contract.hy as a contract shared by the real one and the fake (fixture replay).
;;; Failure category kind (so consumers do not judge expiry from the string of a type name in detail):
;;;   * the handler maps it from the transport exception's class hierarchy: the 4 expiry kinds = TIMED-OUT; cannot connect = CONNECT-FAILED; the rest = OTHER
;;;   * counterexamples: an exception whose name merely ends in Timeout, or a failure whose message contains timed out, cannot be read as expiry
;;;   * kind has no default value (if a builder forgets to write it, building fails -- it never silently becomes "not expired")
;;; The unreachable peer is built with httpx's own MockTransport (the library owner's stand-in peer).

(import datetime)
(import httpx)
(import doeff_core_effects [await-handler])
(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects [http-production-handler])


(defclass GatewayTimeout [httpx.NetworkError]
  "A transport failure whose name ends in Timeout but is not an expiry category (httpx.TimeoutException) -- a counterexample where reading the type name mistakes it for an expiry.")


(defk ask [url values]
  {:pre [(: url str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "program"}}
  "To issue the same request once, changing only the failures-as-values flag (no retry)."
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "PATCH" url :max-retries 0 :failures-as-values values))
  answer)


(defk answer-from-silent-server [error-class text values]
  {:pre [(: error-class type) (: text str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))]
   :tags {:context "http" :role "foundation"}}
  "To make one request with the production handler to a peer that does not respond (MockTransport raises an exception of error-class for each request), and read the handler's answer."
  (<- answer (| HttpResponse HttpFailed)
      (with-handler [await-handler
                     (http-production-handler
                       :client-factory (fn [] (httpx.AsyncClient :transport (httpx.MockTransport (fn [request] (raise (error-class text :request request)))))))]
        (ask "https://api.test/x" values)))
  answer)


(deftest test-the-failure-kind-comes-from-the-transport-error-class
  (for [#(error-class kind) [#(httpx.ConnectTimeout HttpFailureKind.CONNECT-FAILED)
                            #(httpx.ReadTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.WriteTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.PoolTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.ConnectError HttpFailureKind.CONNECT-FAILED)
                            #(httpx.ReadError HttpFailureKind.OTHER)
                            #(httpx.RemoteProtocolError HttpFailureKind.OTHER)]]
    (<- failed (| HttpResponse HttpFailed) (answer-from-silent-server error-class "no answer" True))
    (assert (= failed.kind kind) #(error-class failed))))


(deftest test-a-failure-that-only-looks-like-a-timeout-is-not-read-as-one
  ;; detail carries the type name and message, so a stopgap that reads detail would read the 1st (type name ending in ...Timeout) as expiry, and if it read the message it would
  ;; read the 2nd and 3rd as expiry too (the counterexample of #850). kind is decided only from the class hierarchy.
  (for [#(error-class text detail kind) [#(GatewayTimeout "upstream gave up" "GatewayTimeout: upstream gave up" HttpFailureKind.OTHER)
                                        #(httpx.ConnectError "timed out" "ConnectError: timed out" HttpFailureKind.CONNECT-FAILED)
                                        #(httpx.ReadError "ReadTimeout: peer went away" "ReadError: ReadTimeout: peer went away"
                                          HttpFailureKind.OTHER)]]
    (<- failed (| HttpResponse HttpFailed) (answer-from-silent-server error-class text True))
    (assert (= failed (HttpFailed :url "https://api.test/x" :detail detail :kind kind)) failed)))


(deftest test-a-failure-cannot-be-made-without-its-kind
  (try
    (HttpFailed :url "https://api.test/x" :detail "ReadTimeout: timed out")
    (assert False "an HttpFailed without kind could be built")
    (except [TypeError])))


(defclass TimeoutSpy []
  "A client that records the per-request timeout argument and returns a 200 response (a test of the connection-phase limit -- the real transport does not count time).
   The request entry point accepts the same arguments as the client entry point the handler calls (HttpAsyncClient in http_handlers.pyi)."
  (defn #^ None __init__ [self]
    "To start the list of recorded timeouts empty."
    (setv #^ (get tuple #((| float httpx.Timeout None) ...)) self.timeouts #()))
  (defn :async #^ httpx.Response request [self #^ str method #^ str url *
                                         #^ (| (get dict #(str (| str int float bool None))) None) [params None]
                                         #^ (| bytes None) [content None]
                                         #^ (| (get dict #(str str)) None) [headers None]
                                         #^ (| float httpx.Timeout None) [timeout None]
                                         #^ bool [follow-redirects True]]
    "To record the request timeout and return a 200 response without sending."
    (setv self.timeouts (+ self.timeouts #(timeout)))
    (setv response (httpx.Response 200 :content b"ok" :request (httpx.Request method url)))
    (setattr response "elapsed" (datetime.timedelta 0))
    response)
  (defn :async #^ None aclose [self]
    "The close entry point (it holds no resources so it does nothing)."
    None))


(defk ask-with-connect-limit []
  {:pre [] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To issue one request with a connection-phase limit and one without."
  (<- limited HttpResponse (HttpRequest "GET" "https://api.test/a" :timeout-seconds 15.0 :connect-timeout-seconds 2.0 :max-retries 0))
  (<- plain HttpResponse (HttpRequest "GET" "https://api.test/b" :timeout-seconds 15.0 :max-retries 0))
  #(limited plain))


(deftest test-a-connect-limit-is-passed-to-the-client-only-for-the-connection
  ;; Failure case (#2337): connect-timeout-seconds is passed to the client as a limit for the connection phase only (the overall limit stays timeout-seconds),
  ;; and a request without it passes only the overall limit as before.
  (val spy (TimeoutSpy))
  (<- answers tuple (with-handler [await-handler (http-production-handler :client-factory (fn [] spy))] (ask-with-connect-limit)))
  (assert (= (tuple (gfor a answers a.status)) #(200 200)) answers)
  (val limited (get spy.timeouts 0))
  (val plain (get spy.timeouts 1))
  (assert (and (isinstance limited httpx.Timeout) (= limited.connect 2.0) (= limited.read 15.0)) limited)
  (assert (= plain 15.0) plain))


(defclass Made []
  "The number of ClosingSpy made and the number closed (read at the end of the test)."
  (defn #^ None __init__ [self]
    "To start the counts at 0."
    (setv self.opened 0)
    (setv self.closed 0)))


(defclass ClosingSpy []
  "A client that refuses requests after it is closed (a test of the per-scope client lifetime -- the httpx client also refuses requests after it is closed).
   It records the number made and the number closed in the builder's count (Made)."
  (defn #^ None __init__ [self #^ Made made]
    "To add the creation to the count and start as an open client."
    (setv self.made made)
    (setv self.closed False)
    (setv made.opened (+ made.opened 1)))
  (defn :async #^ httpx.Response request [self #^ str method #^ str url *
                                         #^ (| (get dict #(str (| str int float bool None))) None) [params None]
                                         #^ (| bytes None) [content None]
                                         #^ (| (get dict #(str str)) None) [headers None]
                                         #^ (| float httpx.Timeout None) [timeout None]
                                         #^ bool [follow-redirects True]]
    "If closed, refuse as httpx does; if open, return a 200 response without sending."
    (when self.closed
      (raise (RuntimeError "Cannot send a request, as the client has been closed.")))
    (setv response (httpx.Response 200 :content b"ok" :request (httpx.Request method url)))
    (setattr response "elapsed" (datetime.timedelta 0))
    response)
  (defn :async #^ None aclose [self]
    "To mark it closed and add to the closed count."
    (setv self.closed True)
    (setv self.made.closed (+ self.made.closed 1))))


(defk ask-once [url]
  {:pre [(: url str)] :post [(: % HttpResponse)] :tags {:context "http" :role "program"}}
  "To issue one GET with no retry."
  (<- answer HttpResponse (HttpRequest "GET" url :max-retries 0))
  answer)


(deftest test-one-handler-value-installed-around-two-scopes-answers-both
  ;; Failure case: build one handler value and cover two separate scopes with it (the shape of a user who holds the value and covers it per request).
  ;; Before the fix, the two scopes used the one client made when the value was built, and it was closed at the end of the first scope, so the second request failed with "client has been closed".
  ;; After the fix, a client is made and closed per scope: both answer, and made = closed = 2.
  (val made (Made))
  (val handler (http-production-handler :client-factory (fn [] (ClosingSpy made))))
  (<- first HttpResponse (with-handler [await-handler handler] (ask-once "https://api.test/one")))
  (<- second HttpResponse (with-handler [await-handler handler] (ask-once "https://api.test/two")))
  (assert (= #(first.status second.status) #(200 200)) #(first second))
  (assert (= #(made.opened made.closed) #(2 2)) #(made.opened made.closed)))
