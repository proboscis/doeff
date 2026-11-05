;;; The pair of the seconds an HTTP client keeps unused connections and the seconds the peer server closes them (when writes to the record service were spaced more than 5 seconds
;;; after the previous write, every write re-established TCP and re-resolved the name). Uses real sockets against the smallest listener inside the test (the standard
;;; http.server -- it counts accepted connections and request bodies).
;;;   (1) The default client does not reconnect even after a real-time gap (6 seconds) longer than the old default (httpx keepalive_expiry of 5 seconds). Waiting for the production
;;;       write interval (30 seconds) would be slow, so the 30-second side is covered by assertions that the connection expiry a client built the public way actually holds (the Limits value itself) and that the seconds after which doeff's
;;;       listener closes unused connections are longer than that
;;;   (2) Even when writing to a connection the server closed first, it is not an error, and it reconnects exactly once and writes (0 duplicate writes) -- httpcore drops a connection closed before sending.
;;;       A request closed after being sent is not re-sent by httpx or httpcore (whether to re-send is decided only by HttpRequest's max-retries)
;;; The new public names (http-client-factory, CLIENT-KEEPALIVE-SECONDS, IDLE-CONNECTION-SECONDS) are read as module attributes -- in the pre-fix version
;;; only the tests with no such attribute fail, and the real-time test of (1) fails by behavior.
(require doeff-hy.macros [deftest defk <- val with-handler])
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import http.server)
(import socket)
(import ssl)
(import threading)
(import time)
(import httpx)
(import pytest)
(import doeff_core_effects [Await])
(import doeff_core_effects [await-handler])
(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects [http-production-handler])
(import doeff_core_effects._http_handlers :as http-handlers)
(import doeff_core_effects [HttpAddress]
        doeff_core_effects._http_server_effects [WS-CLOSE-NORMAL])

;; The waits in this file are about real time (what they are about is in this file's row of the roster tests/realtime_tests.toml) -- the sleep rule for tests
;; (DOEFF211 of doeff-linter) is not applied to sleeps in a file that declares itself realtime.
(val pytestmark pytest.mark.realtime)

;; Real-time gap (seconds) longer than the old default expiry (httpx keepalive_expiry of 5 seconds).
(val OLD-EXPIRY-GAP-SECONDS 6.0)
;; The production write interval (26 to 30 seconds measured between the UI job's writes to the record service).
(val CALL-GAP-SECONDS 30.0)
;; The form where the server closes first: the limit (seconds) the listener waits for the next request, and the gap (seconds) the client leaves that is longer than it (it writes after the peer's FIN arrives).
(val SERVER-FIRST-IDLE-SECONDS 0.3)
(val AFTER-SERVER-CLOSED-GAP-SECONDS 1.0)
;; The body the listener reads and then closes on without answering (the form closed after being sent).
(val CUT b"cut")
;; Upper limit (seconds) to wait for opening doeff's listener and for it to accept a connection.
(val EDGE-SECONDS 10.0)


(defclass Tally []
  "The number of connections the listener accepted and the sequence of request bodies (each per-connection thread adds them inside the lock)."
  (defn #^ None __init__ [self]
    "To start the counts at 0 and empty."
    (setv self.lock (threading.Lock))
    (setv self.connections 0)
    (setv #^ (get tuple #(bytes ...)) self.arrivals #())
    None)
  (defn #^ None connected [self]
    "To count one accepted connection."
    (with [self.lock]
      (setv self.connections (+ self.connections 1)))
    None)
  (defn #^ None arrived [self #^ bytes body]
    "To record one request body (in the order received)."
    (with [self.lock]
      (setv self.arrivals (+ self.arrivals #(body))))
    None))


(defclass CountingHandler [http.server.BaseHTTPRequestHandler]
  "Per-connection handler (HTTP/1.1 -- reuses connections): counts the connection, records the POST body, and answers 200. The body CUT is read and then closed on without answering.
   If the listener has idle-seconds, it waits that many seconds for the next request and closes if none comes (the form where the server closes first -- the standard socket's limit)."
  (setv protocol-version "HTTP/1.1")
  (defn #^ None setup [self]
    "To accept the limit for waiting for the next request from the listener, then open the connection and count it."
    (setv self.timeout self.server.idle-seconds)
    (.setup (super))
    (.connected self.server.tally)
    None)
  (defn #^ None do-POST [self]
    "To record the body, close without answering for CUT, and answer 200 otherwise."
    (setv body (.read self.rfile (int (.get self.headers "Content-Length" "0"))))
    (.arrived self.server.tally body)
    (if (= body CUT)
        (setv self.close-connection True)
        (do (.send-response self 200)
            (.send-header self "Content-Length" "2")
            (.end-headers self)
            (.write self.wfile b"ok")))
    None)
  (defn #^ None log-message [self #^ str format #^ (| str int) #* args]
    "To write no line per request."
    None))


(defclass CountingServer [http.server.ThreadingHTTPServer]
  "A listener that counts accepted connections and requests (a free port on 127.0.0.1). idle-seconds = the limit per connection for waiting for the next request (None = keep waiting)."
  (setv daemon-threads True)
  (defn #^ None __init__ [self #^ (| float None) idle-seconds]
    "To open on a free port and start the counts empty."
    (.__init__ (super) #("127.0.0.1" 0) CountingHandler)
    (setv self.idle-seconds idle-seconds)
    (setv self.tally (Tally))
    None))


(defrecord Observed
  "The answers of the client Program, and the number of connections the listener accepted and the sequence of request bodies."
  (#^ tuple answers)
  (#^ int connections)
  (#^ tuple arrivals))


(defk observed-through [handler idle-seconds program-of]
  {:pre [(: handler Callable) (: idle-seconds (| float None)) (: program-of Callable)] :post [(: % Observed)]
   :tags {:context "http" :role "foundation"}}
  "To start a counting listener and run the client Program for its URL (the answer of program-of) under the HTTP responder handler and await-handler,
   then stop the listener and read the counts (the client is closed at the end of the scope -- the responder creates and closes it per scope)."
  (val server (CountingServer idle-seconds))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  (val url (.format "http://127.0.0.1:{}/write" (get server.server-address 1)))
  (try
    (<- answers tuple (with-handler [await-handler handler] (program-of url)))
    (Observed :answers answers :connections server.tally.connections :arrivals server.tally.arrivals)
    (finally
      (.shutdown server)
      (.server-close server))))


(defk write-twice [gap url]
  {:pre [(: gap float) (: url str)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To issue a POST with body 1 and one with body 2, once each, leaving a real-time gap of gap seconds. Re-fires are 0 and an unreachable failure is received as a value -- the responder's re-sending must not
   hide a reconnect or a failure after sending. The answer = two answers."
  (<- first (| HttpResponse HttpFailed) (HttpRequest "POST" url :body b"1" :max-retries 0 :failures-as-values True))
  (<- (Await (asyncio.sleep gap)))
  (<- second (| HttpResponse HttpFailed) (HttpRequest "POST" url :body b"2" :max-retries 0 :failures-as-values True))
  #(first second))


(defk write-cut [url]
  {:pre [(: url str)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To issue one POST with body CUT (0 re-fires; an unreachable failure is received as a value). The answer = one answer."
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "POST" url :body CUT :max-retries 0 :failures-as-values True))
  #(answer))


(defk all-written [observed]
  {:pre [(: observed Observed)] :post [(: % bool)] :tags {:context "http" :role "program"}}
  "To judge whether all the client's answers are 200 responses (false if an unreachable failure is mixed in)."
  (all (gfor answer observed.answers (and (isinstance answer HttpResponse) (= answer.status 200)))))


(deftest test-the-default-client-keeps-its-connection-across-a-gap-longer-than-the-old-expiry
  ;; Failure case (1): with the default client (http-production-handler with no client-factory passed), write twice leaving a real-time gap of 6 seconds, longer than the old default expiry of 5 seconds.
  ;; Before the fix, the expired connection was closed and re-established before the second write (2 connections). After the fix it writes on the same connection (1 connection).
  (<- seen Observed (observed-through (http-production-handler) None (fn [url] (write-twice OLD-EXPIRY-GAP-SECONDS url))))
  (assert (! (all-written seen)) seen)
  (assert (= seen.arrivals #(b"1" b"2")) seen)
  (assert (= seen.connections 1) seen))


(defrecord MadeClient
  "How the client actually holds connections: the connection count limit, the limit on the number of unused connections kept, the seconds unused connections are kept, whether it reads the proxy env, and
   the SSLContext that verifies the peer's certificate."
  (#^ int max-connections)
  (#^ int max-keepalive)
  (#^ float keepalive-seconds)
  (#^ bool trust-env)
  (#^ ssl.SSLContext ssl-context))


(defk made-client [factory]
  {:pre [(: factory Callable)] :post [(: % MadeClient)] :tags {:context "http" :role "foundation"}}
  "To create one client with factory, read how it actually holds connections, and close it. httpx does not keep Limits on the client and passes them only to httpcore's pool,
   so the pool's attributes are read (versions: httpx 0.28.1 and httpcore 1.0.9 in uv.lock)."
  (val client (factory))
  (val pool client._transport._pool)
  (val made (MadeClient :max-connections pool._max-connections :max-keepalive pool._max-keepalive-connections
                        :keepalive-seconds pool._keepalive-expiry :trust-env client.trust-env :ssl-context pool._ssl-context))
  (<- (Await (.aclose client)))
  made)


(deftest test-the-public-client-factory-keeps-idle-connections-past-the-call-gap
  ;; The 30-second side of failure case (1): the expiry of unused connections that a client built the public way (the default form and the form with the three arguments of agora's foundation) actually
  ;; holds is the one named CLIENT-KEEPALIVE-SECONDS, and is longer than the production write interval of 30 seconds. The two connection-count limits remain httpx's defaults
  ;; (the same as a bare httpx.AsyncClient), and trust-env and verify are as passed. Before the fix there was no public way to build, and the default client's expiry was
  ;; httpx's default of 5 seconds (writes spaced 30 seconds apart reconnected every time).
  (val make http-handlers.http-client-factory)
  (val context (ssl.create-default-context))
  (<- plain MadeClient (with-handler [await-handler] (made-client httpx.AsyncClient)))
  (for [#(factory trust-env given) [#(make True None)
                                    #((fn [] (make :trust-env False)) False None)
                                    #((fn [] (make :verify context :trust-env False)) False context)]]
    (<- made MadeClient (with-handler [await-handler] (made-client factory)))
    (assert (= #(made.max-connections made.max-keepalive) #(plain.max-connections plain.max-keepalive)) made)
    (assert (= made.keepalive-seconds http-handlers.CLIENT-KEEPALIVE-SECONDS) made)
    (assert (> made.keepalive-seconds CALL-GAP-SECONDS) made)
    (assert (= made.trust-env trust-env) made)
    (assert (or (is given None) (is made.ssl-context given)) made)))


(deftest test-a-connection-the-server-closed-first-is-replaced-before-the-write-is-sent
  ;; Failure case (2): to a connection the peer server closed first (a listener that closes unused connections after SERVER-FIRST-IDLE-SECONDS), write next with a client built the public way
  ;; (agora's form, trust-env False). Before sending, httpcore sees that the unused connection's socket is readable (the peer's FIN arrived),
  ;; drops that connection, and sends on a new one: it is not an error (200 even with 0 re-fires), each write happens once (0 duplicates), and the reconnect is once (2 connections).
  (val handler (http-production-handler :client-factory (fn [] (http-handlers.http-client-factory :trust-env False))))
  (<- seen Observed (observed-through handler SERVER-FIRST-IDLE-SECONDS
                                      (fn [url] (write-twice AFTER-SERVER-CLOSED-GAP-SECONDS url))))
  (assert (! (all-written seen)) seen)
  (assert (= seen.arrivals #(b"1" b"2")) seen)
  (assert (= seen.connections 2) seen))


(deftest test-a-write-the-server-closed-after-reading-is-not-sent-again
  ;; The boundary of (2): a request the peer read and then closed on without answering (closed after being sent) is not re-sent by httpx or httpcore -- it is answered
  ;; with an unreachable failure (OTHER, RemoteProtocolError), and the peer received the write once. Whether to re-send is decided only by HttpRequest's max-retries (a non-idempotent write is issued
  ;; with 0).
  (val handler (http-production-handler :client-factory (fn [] (http-handlers.http-client-factory :trust-env False))))
  (<- seen Observed (observed-through handler None write-cut))
  (val failed (get seen.answers 0))
  (assert (and (isinstance failed HttpFailed) (= failed.kind HttpFailureKind.OTHER)) failed)
  (assert (in "RemoteProtocolError" failed.detail) failed)
  (assert (= seen.arrivals #(CUT)) seen)
  (assert (= seen.connections 1) seen))


(defrecord ServerIdle
  "The seconds after which doeff's listener closes an unused connection: the value held by the aiohttp handler of the accepted connection, and the value of the named declaration."
  (#^ float live)
  (#^ float declared))


(defk doeff-server-idle []
  {:pre [] :post [(: % ServerIdle)] :tags {:context "http" :role "foundation"}}
  "To open doeff's listener (the WebEdge of aiohttp-http-server) on a free port, make one connection, wait for the listener to accept it, read
   the seconds before an unused connection is closed from the aiohttp handler, and close, placing it beside the declared value (skipped in a venv without aiohttp)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects._aiohttp_http_server :as edge-module)
  (val edge (edge-module.WebEdge))
  (val loop (.edge-loop edge))
  (val bound (.result (asyncio.run-coroutine-threadsafe (.start edge (HttpAddress :host "127.0.0.1" :port 0) 1024 1024 #() False) loop)
                      EDGE-SECONDS))
  (val peer (socket.create-connection #("127.0.0.1" bound.port) :timeout EDGE-SECONDS))
  (val deadline (+ (time.monotonic) EDGE-SECONDS))
  (try
    (while (and (not edge.runner.server.connections) (< (time.monotonic) deadline))
      (time.sleep 0.02))
    (ServerIdle :live (float (. (get edge.runner.server.connections 0) keepalive-timeout))
                :declared edge-module.IDLE-CONNECTION-SECONDS)
    (finally
      (.close peer)
      (.result (asyncio.run-coroutine-threadsafe (.shutdown edge "closed by the test" 0.2 WS-CLOSE-NORMAL) loop) EDGE-SECONDS))))


(deftest test-the-doeff-server-keeps-an-idle-connection-longer-than-the-client
  ;; The other half of the pair: the seconds after which doeff's listener (the listeners of the table-record service 8875 and the conversation-record service 8874) closes an unused connection is
  ;; the named IDLE-CONNECTION-SECONDS (not relying on aiohttp's default), and is longer than CLIENT-KEEPALIVE-SECONDS, the seconds the client keeps unused connections
  ;; -- the form where the client writes to a connection the server closed first (failure case (2)) happens only when the server restarts and the like.
  (<- idle ServerIdle (doeff-server-idle))
  (assert (= idle.live idle.declared) idle)
  (assert (> idle.declared http-handlers.CLIENT-KEEPALIVE-SECONDS) idle))


(defk traced-twice [url]
  {:pre [(: url str)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To issue a POST with the flag trace-phases that splits into phases twice (with no gap), and a POST without the flag once (0 re-fires; an unreachable failure is received as a value).
   The answer = three answers."
  (<- first (| HttpResponse HttpFailed) (HttpRequest "POST" url :body b"1" :max-retries 0 :failures-as-values True :trace-phases True))
  (<- second (| HttpResponse HttpFailed) (HttpRequest "POST" url :body b"2" :max-retries 0 :failures-as-values True :trace-phases True))
  (<- plain (| HttpResponse HttpFailed) (HttpRequest "POST" url :body b"3" :max-retries 0 :failures-as-values True))
  #(first second plain))


(deftest test-a-traced-request-names-whether-it-made-a-connection-and-where-its-seconds-went
  ;; Splitting into phases (naming, on the caller's side, where time goes in one round trip of a change read to the record service):
  ;; the answer to a trace-phases request has HttpPhases. The first makes a connection (new-connection, connect-seconds > 0), and the second, with no gap,
  ;; reuses the same connection (connect-seconds 0). The sum of the phases does not exceed the elapsed the responder measures (phases within the same request). A request without the flag has
  ;; no phases (the default caller does not pay the cost of trace). The pre-fix answer has no attribute phases.
  (<- seen Observed (observed-through (http-production-handler) None traced-twice))
  (assert (! (all-written seen)) seen)
  (assert (= seen.connections 1) seen)
  (val first (get seen.answers 0))
  (val second (get seen.answers 1))
  (assert (and first.phases.new-connection (> first.phases.connect-seconds 0.0)) first.phases)
  (assert (and (not second.phases.new-connection) (= second.phases.connect-seconds 0.0)) second.phases)
  (for [answer #(first second)]
    (val phases answer.phases)
    (assert (> phases.wait-seconds 0.0) phases)
    (assert (<= (+ phases.connect-seconds phases.send-seconds phases.wait-seconds phases.receive-seconds) answer.elapsed-seconds) phases))
  (assert (is (. (get seen.answers 2) phases) None) seen.answers))
