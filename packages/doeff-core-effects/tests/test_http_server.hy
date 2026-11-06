;;; Tests of properties that only one of the two answerers has, for the general-purpose HTTP listening effects (http_server_effects.hy). Properties common to both (how answers arrive, HEAD and
;;; answers without a body, relay status and 502, ws round trips and closes, refusals, cutting at the send limit, after close, body reads) are run on both answerers by the contract tests of test_http_server_contract.hy
;;; .
;;;   - Scripted answerer (scripted-http-server): longest match of the relay target and 502 for a target that does not accept ws, the reason for a file range that cannot be read, the count of a peer's box that does not read
;;;     piling up and being cut, appending to a running script, a body the peer cut midway through a read.
;;;   - Real answerer (aiohttp-http-server): the body of an HTTP relay and X-Forwarded-Proto, the ws relay (frame round trips and the close status code), the one-line announcement and count of the answer to a request the peer cut first
;;;     (no traceback -- #2757), the port bound with port 0,
;;;     received-at of events, the probe port answers even when the main flow (the shared event loop or the scheduler) is blocked (#2776),
;;;     answers (HttpRespond), reads of small declared bodies (HttpReadBody), and arrivals already in the queue (HttpNextRequest) do not enter the shared event loop
;;;     (children (3) 3b and 3c of #3688, and plan 1), sending one ws text message (WsSendText) also does not enter the shared event loop and arrives in the order fired,
;;;     and the two limits when the ws relay connects to the target (up to connecting, and the handshake answer)
;;;     (skipped in a venv without aiohttp).
(require doeff-hy.macros [deftest defk deff defhandler <- val var with-handler])
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import http.client)
(import pathlib [Path])
(import queue)
(import socket)
(import threading)
(import time)
(import pytest)
(import doeff [EffectBase run with_handlers Program])
(import doeff_core_effects [Await])
(import doeff_core_effects [state await-handler])
(import doeff_core_effects [PublishLatest ReadLatest])
(import doeff_core_effects [process-latest-handler])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [HttpAddress HttpHeader HttpRequestArrived HttpServerClosed HttpListen HttpNextRequest HttpRespond HttpForward WsForward HttpBodyBytes HttpBodyFileRange HttpNoBody HttpScript ScriptedUpstream ReadHttpServed HttpEvent WsAccept WsSendText WsClose HttpShutdown TakeWsSendReport WsSendReport WsTextArrived WsClosed HttpTakeQueued WsCloseSent AppendHttpScript HttpReadBody HttpBodyRead HttpBodyFailed HttpBodyOutcome ScriptedBody HttpProbe HttpProbeAnswer HttpStopListening WsTextSent]
        doeff_core_effects._http_server_effects [WS-CUT-REASON])
(import doeff_core_effects [scripted-http-server])

;; The waits in this file are real time (what they are about is in this file's row of the roster tests/realtime_tests.toml) -- the sleeps inside a file that declares itself realtime
;; are not subject to the test sleep rule (DOEFF211 of doeff-linter).
(val pytestmark pytest.mark.realtime)


(defk serve-until-closed [site upstream]
  {:pre [(: site str) (: upstream str)] :post [(: % int)]}
  "Test Program: open the listener and, until it is closed, fire one command per path (answer = number of requests received). /file is 2 to 6 bytes of a file on the site,
   /head is a HEAD answer, /relay is HTTP to upstream, /ws is ws to upstream, anything else is a one-line body."
  (<- (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (var count 0)
  (while True
    (<- event (| HttpRequestArrived HttpServerClosed) (HttpNextRequest))
    (when (isinstance event HttpServerClosed)
      (return count))
    (:= count (+ count 1))
    (val t event.ticket)
    (cond
      (= event.path "/file") (<- (HttpRespond :ticket t :status 206 :headers #((HttpHeader :name "Content-Length" :value "5"))
                                              :body (HttpBodyFileRange :path site :start 2 :length 5)))
      (= event.path "/head") (<- (HttpRespond :ticket t :status 200 :headers #((HttpHeader :name "Content-Length" :value "11"))
                                              :body (HttpNoBody)))
      (.startswith event.path "/relay") (<- (HttpForward :ticket t :url (+ upstream (cut event.target 6 None))))
      (= event.path "/ws") (<- (WsForward :ticket t :url upstream))
      True (<- (HttpRespond :ticket t :status 200 :headers #((HttpHeader :name "X-Seen" :value (str (len event.headers))))
                            :body (HttpBodyBytes :data (.encode (+ "hello " event.method) "utf-8")))))))


;; --- Scripted answerer --------------------------------------------------------------------------------------------------------------

(defn #^ HttpRequestArrived arrival [#^ (| int str) n #^ str path * #^ str [method "GET"] #^ (| str None) [target None]
                                     #^ (get tuple #(HttpHeader ...)) [headers #()] #^ bool [upgrade False]
                                     #^ (| str None) [remote None]]
  "To build one scripted request (ticket = the string of n; if target is not given, it equals the path)."
  (HttpRequestArrived :ticket (str n) :method method :path path :target (if (is target None) path target)
                      :headers headers :upgrade upgrade :remote remote))


(defk served-after [site upstream]
  {:pre [(: site str) (: upstream str)] :post [(: % tuple)]}
  "To run the Program until it closes and read the record of the script."
  (<- _count int (serve-until-closed site upstream))
  (<- served tuple (ReadHttpServed))
  served)


(deftest test-the-scripted-server-picks-the-longest-upstream-and-refuses-ws-to-a-plain-one
  (val script (HttpScript :arrivals #((arrival 1 "/relay/api/x" :target "/relay/api/x?q=1") (arrival 2 "/relay/other")
                                      (arrival 3 "/ws" :upgrade True))
                          :upstreams #((ScriptedUpstream :base "http://up" :http-status 201 :accepts-ws False)
                                       (ScriptedUpstream :base "http://up/api" :http-status 202 :accepts-ws True))))
  (<- served tuple (with-handler [(state) (memory-file-handler (MemoryFiles)) (scripted-http-server script)]
                     (served-after "/site/app.js" "http://up")))
  ;; The relay url is answered by the longest match of the head of the script's relay targets.
  (assert (= (. (get served 0) command) (HttpForward :ticket "1" :url "http://up/api/x?q=1")) served)
  (assert (= (lfor s served s.status) [202 201 502]) served))


(deftest test-a-file-range-that-cannot-be-read-names-the-reason
  (val script (HttpScript :arrivals #((arrival 1 "/file"))))
  (<- served tuple (with-handler [(state) (memory-file-handler (MemoryFiles)) (scripted-http-server script)]
                     (served-after "/gone.js" "http://up")))
  (assert (.startswith (. (get served 0) body) "cannot read file") served))


(defk ws-echo [limit publish]
  {:pre [(: limit int) (: publish Callable)] :post [(: % tuple)]}
  "Test Program: upgrade to ws, answer each message, and run until closed (answer = the sequence of events received and the count of the last send). text \"stop\" = close the
   listener, anything else is echo. publish = the port that hands the bound destination to the test."
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0) :ws-send-max-bytes limit))
  (publish bound)
  (var seen #(bound))
  (while True
    (<- event HttpEvent (HttpNextRequest))
    (:= seen (+ seen #(event)))
    (match event
      (HttpServerClosed) (do (<- report WsSendReport (TakeWsSendReport))
                             (return (+ seen #(report))))
      (HttpRequestArrived :ticket t) (<- (WsAccept :ticket t))
      (WsTextArrived :ticket t :text "stop") (<- (HttpShutdown :reason "the test closed it" :drain-seconds 2.0))
      (WsTextArrived :ticket t :text text) (<- (WsSendText :ticket t :text (+ "echo:" text)))
      _ None)))


(defk named-echo [name address publish]
  {:pre [(: name str) (: address HttpAddress) (: publish Callable)] :post [(: % int)]}
  "Test Program (sharing the port and closing only the listener -- the form where old and new listeners overlap in a swap): share the port and listen, upgrade to ws, and answer each message
   with the name name attached. text \"leave\" = close only the listener (leave the open connections) and answer \"left\"; \"stop\" = close the listener; anything else is
   a named echo. publish = the port that hands the bound destination to the test. Answer = the number of connections upgraded to ws."
  (<- bound HttpAddress (HttpListen :address address :share-port True))
  (publish bound)
  (var accepted 0)
  (while True
    (<- event HttpEvent (HttpNextRequest))
    (match event
      (HttpServerClosed) (return accepted)
      (HttpRequestArrived :ticket t) (do (:= accepted (+ accepted 1))
                                         (<- (WsAccept :ticket t)))
      (WsTextArrived :ticket t :text "leave") (do (<- (HttpStopListening))
                                                  (<- (WsSendText :ticket t :text "left")))
      (WsTextArrived :text "stop") (<- (HttpShutdown :reason "the test closed it" :drain-seconds 1.0))
      (WsTextArrived :text "go-away") (<- (HttpShutdown :reason "stepping aside" :drain-seconds 1.0 :close-code 1001))
      (WsTextArrived :ticket t :text text) (<- (WsSendText :ticket t :text (+ name ":" text)))
      _ None)))


(defk echo-served [name]
  {:pre [(: name str)] :post [(: % tuple)]}
  "To run named-echo until it closes on a scripted listener, and pair the answer (the number of connections upgraded) with the record of the script."
  (<- accepted int (named-echo name (HttpAddress :host "127.0.0.1" :port 0) (fn [bound] None)))
  (<- served tuple (ReadHttpServed))
  #(accepted served))


(deftest test-the-scripted-server-closes-open-sockets-with-the-code-the-shutdown-names
  ;; Failure case (close on the side that steps aside in a swap): the open ticket is closed with the status code HttpShutdown names (1001 going away). Before the fix
  ;; there was no status code field and it closed with 1000 (red).
  (val script (HttpScript :arrivals #((arrival 1 "/" :upgrade True) (WsTextArrived :ticket "1" :text "go-away"))))
  (<- answer tuple (with-handler [(state) (scripted-http-server script)] (echo-served "A")))
  (val served (get answer 1))
  (assert (= (lfor e served :if (isinstance e WsCloseSent) #(e.ticket e.code)) [#("1" 1001)]) served))


(deftest test-the-scripted-server-hands-no-new-request-after-it-stops-listening
  ;; Failure case (closing only the listener): after closing only the listener, a new request that arrives is not handed to the main body, while one message on an open connection is.
  (val script (HttpScript :arrivals #((arrival 1 "/" :upgrade True) (WsTextArrived :ticket "1" :text "leave")
                                      (arrival 2 "/" :upgrade True) (WsTextArrived :ticket "1" :text "still"))))
  (<- answer tuple (with-handler [(state) (scripted-http-server script)] (echo-served "A")))
  (val accepted (get answer 0))
  (val served (get answer 1))
  (assert (= accepted 1) served)
  (assert (= (lfor e served :if (isinstance e WsTextSent) e.text) ["left" "A:still"]) served))


(defk do-served [program]
  {:pre [(: program Program)] :post [(: % tuple)]}
  "To read the record of the script after running the Program."
  (<- answer tuple program)
  (<- served tuple (ReadHttpServed))
  #(answer served))


(deftest test-a-stalled-peer-fills-its-box-until-the-limit-cuts-it
  ;; The box of peer b, which does not read, accumulates one 7-byte message, and the next message exceeds the 10-byte limit, so it is cut. The accumulation goes to the discarded count.
  (val script (HttpScript :arrivals #((arrival "b" "/ws" :upgrade True) (WsTextArrived :ticket "b" :text "hi")
                                      (WsTextArrived :ticket "b" :text "hi") (WsTextArrived :ticket "b" :text "never")
                                      (arrival "d" "/ws" :upgrade True) (WsTextArrived :ticket "d" :text "stop"))
                          :stalled (frozenset #("b"))))
  (<- answer tuple (with-handler [(state) (scripted-http-server script)] (do-served (ws-echo 10 (fn [bound] None)))))
  (val events (cut (get answer 0) 1 -1))
  (val report (get (get answer 0) -1))
  (val served (get answer 1))
  ;; The script does not bind a port -- the destination stays as given.
  (assert (= (get (get answer 0) 0) (HttpAddress :host "127.0.0.1" :port 0)))
  (assert (= (lfor e events (. (type e) __name__))
             ["HttpRequestArrived" "WsOpened" "WsTextArrived" "WsTextArrived" "WsClosed" "WsTextArrived"
              "HttpRequestArrived" "WsOpened" "WsTextArrived" "HttpServerClosed"])
          events)
  (assert (= (lfor e events :if (isinstance e WsClosed) #(e.ticket e.code e.reason)) [#("b" 1006 WS-CUT-REASON)]) events)
  (assert (= [report.queued-frames report.queued-bytes report.flushed-bytes report.dropped-bytes report.cuts] [1 7 0 7 1]) report)
  ;; Sends to b after it is closed are discarded, and closing the listener sends close 1000 to the still-open d.
  (assert (= (lfor s served :if (isinstance s WsCloseSent) #(s.ticket s.code)) [#("d" 1000)]) served))


(defk append-then-drain []
  {:pre [] :post [(: % tuple)]}
  "To see that appending an event to a running script delivers the continuation even after it was exhausted (answer = the names of the types of events received)."
  (<- first HttpEvent (HttpNextRequest))
  (<- (AppendHttpScript :arrivals #((arrival "z" "/late"))))
  (<- second HttpRequestArrived (HttpNextRequest))
  (<- third HttpEvent (HttpNextRequest))
  #((. (type first) __name__) second.ticket (. (type third) __name__)))


(defn #^ None test-an-appended-script-is-served-after-the-script-ran-dry []
  (assert (= (run (scheduled (with_handlers [(state) (scripted-http-server (HttpScript :arrivals #()))] (append-then-drain))))
             #("HttpServerClosed" "z" "HttpServerClosed"))))


(defk read-broken-body []
  {:pre [] :post [(: % HttpBodyOutcome)] :tags {:context "http-server" :role "program"}}
  "To read, on a script, a body that the peer cut midway through a read."
  (<- event HttpRequestArrived (HttpNextRequest))
  (<- outcome HttpBodyOutcome (HttpReadBody :ticket event.ticket :max-bytes 8))
  outcome)


(deftest test-the-scripted-server-names-a-body-the-peer-cut-off
  (val script (HttpScript :arrivals #((arrival 1 "/broken" :method "POST"))
                          :bodies #((ScriptedBody :ticket "1" :data b"12" :failed "the peer cut it midway"))))
  (<- outcome HttpBodyOutcome (with-handler [(state) (scripted-http-server script)] (read-broken-body)))
  (assert (= outcome (HttpBodyFailed :reason "the peer cut it midway")) outcome))


(defk two-arrivals []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server" :role "program"}}
  "To receive two scripted requests (answer = the two events received)."
  (<- first HttpRequestArrived (HttpNextRequest))
  (<- second HttpRequestArrived (HttpNextRequest))
  #(first second))


(defk take-queued-then-next []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "To receive the first scripted one with HttpNextRequest, take the rest together with HttpTakeQueued, then see a second HttpTakeQueued, an HttpTakeQueued for an event added later,
   an HttpNextRequest after exhaustion, and an HttpTakeQueued after close (answer = the tuple of tickets and type names)."
  (<- first HttpRequestArrived (HttpNextRequest))
  (<- rest tuple (HttpTakeQueued))
  (<- again tuple (HttpTakeQueued))
  (<- (AppendHttpScript :arrivals #((arrival "z" "/late"))))
  (<- late tuple (HttpTakeQueued))
  (<- dry HttpEvent (HttpNextRequest))
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 0.0))
  (<- shut tuple (HttpTakeQueued))
  #(first.ticket (tuple (gfor e rest e.ticket)) again (tuple (gfor e late e.ticket)) (. (type dry) __name__)
    (tuple (gfor e shut #((. (type e) __name__) e.reason)))))


(deftest test-the-scripted-server-hands-every-queued-arrival-at-once
  ;; HttpTakeQueued hands over all events currently in the script's queue in the order received, and what was handed over is not handed over again by HttpNextRequest. If the queue is empty, an empty tuple (does not
  ;; announce the close), events added later can also be taken, and after close it is the one close reason.
  (val script (HttpScript :arrivals #((arrival 1 "/a") (arrival 2 "/b") (arrival 3 "/c"))))
  (<- got tuple (with-handler [(state) (scripted-http-server script)] (take-queued-then-next)))
  (assert (= got #("1" #("2" "3") #() #("z") "HttpServerClosed" #(#("HttpServerClosed" "the test closed it")))) got))


(deftest test-the-scripted-server-hands-over-the-remote-the-script-names
  ;; The sender address arrives as the value the script writer put in, and if none was put in, None (cannot be named).
  (val script (HttpScript :arrivals #((arrival 1 "/a" :remote "203.0.113.7") (arrival 2 "/b"))))
  (<- events tuple (with-handler [(state) (scripted-http-server script)] (two-arrivals)))
  (assert (= (lfor e events e.remote) ["203.0.113.7" None]) events))


;; --- Real answerer --------------------------------------------------------------------------------------------------------------

(defn #^ int free-port []
  "To have the OS choose one free port and return its number."
  (with [s (socket.socket)]
    (.bind s #("127.0.0.1" 0))
    (get (.getsockname s) 1)))


(defn #^ None test-the-aiohttp-server-relays-http-bodies-and-ws-frames [#^ Path tmp-path]
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server"))
  (import aiohttp [web WSMsgType])
  (import doeff_core_effects [aiohttp-http-server])
  (setv port (free-port) seen [])
  (defk serve-on-port [upstream]
    {:pre [(: upstream str)] :post [(: % int)]}
    "To open the test Program on a fixed port (swap the port of HttpListen)."
    (<- (HttpListen :address (HttpAddress :host "127.0.0.1" :port port)))
    (while True
      (<- event HttpRequestArrived (HttpNextRequest))
      (val t event.ticket)
      (cond
        (.startswith event.path "/relay") (<- (HttpForward :ticket t :url (+ upstream (cut event.target 6 None))))
        (= event.path "/ws") (<- (WsForward :ticket t :url (+ upstream "/ws")))
        True (<- (HttpRespond :ticket t :status 200 :headers #() :body (HttpNoBody))))))
  (defn :async #^ (get tuple #(web.AppRunner int)) upstream-app []
    "To open the relay-target counterpart (an HTTP echo and a ws echo) on a free port, and return the runner and the port."
    (defn :async #^ web.Response echo [#^ web.Request request]
      "To return the received body with 201 and note the X-Forwarded-Proto that the relay added."
      (.append seen (.get request.headers "X-Forwarded-Proto"))
      (web.Response :status 201 :body (await (.read request))))
    (defn :async #^ web.WebSocketResponse ws-echo [#^ web.Request request]
      "To echo the ws text, and on bye send a close with status code 4002."
      (setv ws (web.WebSocketResponse))
      (await (.prepare ws request))
      (for [:async message ws]
        (if (= message.data "bye")
            (do (await (.close ws :code 4002)) (break))
            (await (.send-str ws (+ "echo:" message.data)))))
      ws)
    (setv app (web.Application))
    (.add-route app.router "GET" "/ws" ws-echo)
    (.add-route app.router "*" "/{tail:.*}" echo)
    (setv runner (web.AppRunner app))
    (await (.setup runner))
    (setv up (free-port))
    (await (.start (web.TCPSite runner "127.0.0.1" up)))
    #(runner up))
  (defn :async #^ None scenario []
    "To open the counterpart, run the real answerer on another thread, and check the HTTP relay and the ws relay."
    (setv #(runner up) (await (upstream-app)))
    (.start (threading.Thread :target (fn [] (try (run (scheduled (with_handlers [await-handler (state) aiohttp-http-server]
                                                                                 (serve-on-port (.format "http://127.0.0.1:{}" up)))))
                                                  (except [asyncio.CancelledError] None)))
                              :daemon True))
    (setv base (.format "http://127.0.0.1:{}" port))
    (with [:async session (aiohttp.ClientSession)]
      (for [_ (range 200)]
        (try (with [:async r (.get session (+ base "/"))] (break))
             (except [aiohttp.ClientError] (await (asyncio.sleep 0.05)))))
      (with [:async r (.post session (+ base "/relay/x") :data b"body")]
        (assert (= [r.status (await (.read r))] [201 b"body"])))
      (assert (= seen ["http"]))
      (with [:async ws (.ws-connect session (+ base "/ws"))]
        (await (.send-str ws "hi"))
        (assert (= (. (await (.receive ws :timeout 5)) data) "echo:hi"))
        (await (.send-str ws "bye"))
        (setv closing (await (.receive ws :timeout 5)))
        (assert (= [closing.type closing.data] [WSMsgType.CLOSE 4002]))))
    (await (.cleanup runner)))
  (asyncio.run (scenario)))


(defn #^ None test-the-aiohttp-server-binds-a-free-port-and-stamps-events []
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server"))
  (import aiohttp [WSMessage])
  (import queue)
  (import doeff_core_effects [aiohttp-http-server])
  (setv result (queue.Queue) bound (queue.Queue))
  (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [await-handler (state) aiohttp-http-server]
                                                                                       (ws-echo 64 bound.put))))))
                            :daemon True))
  (defn :async #^ WSMessage scenario [#^ str base]
    "To upgrade to ws, do one round trip, have stop close the listener, and return the last frame received."
    (with [:async session (aiohttp.ClientSession)]
      (with [:async ws (.ws-connect session (+ base "/ws"))]
        (await (.send-str ws "hi"))
        (assert (= (. (await (.receive ws :timeout 5)) data) "echo:hi"))
        (await (.send-str ws "stop"))
        (await (.receive ws :timeout 5)))))
  ;; Open on port 0 and learn the bound port from the HttpListen answer.
  (setv port (. (.get bound :timeout 30) port))
  (assert (> port 0))
  (asyncio.run (scenario (.format "http://127.0.0.1:{}" port)))
  (setv [_bound #* events report] (.get result :timeout 30))
  ;; Events carry the monotonic clock of the beat they were received at (the scripted answerer keeps the value the script writer put in).
  (assert (all (gfor e events :if (not (isinstance e HttpServerClosed)) (isinstance e.received-at float))) events)
  ;; A request carries the sender address (the connection's peer = 127.0.0.1).
  (assert (= (lfor e events :if (isinstance e HttpRequestArrived) e.remote) ["127.0.0.1"]) events)
  (assert (= (. (get events -1) reason) "the test closed it"))
  (assert (>= report.flushed-bytes (len "echo:hi"))))


(defn #^ None test-a-shared-port-hands-new-connections-to-the-listener-that-stays []
  ;; Failure case (old and new listeners overlap in a swap): after connecting to listener A, open listener B sharing the same port,
  ;; and A closes only its listener. Connections opened on A keep round-tripping, and every new connection hits B (before A closes its listener the kernel distributes them).
  ;; Before the fix HttpListen had no port-sharing field and A could not open (red).
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server"))
  (import doeff_core_effects [aiohttp-http-server])
  (setv port (free-port) address (HttpAddress :host "127.0.0.1" :port port)
        bound-a (queue.Queue) bound-b (queue.Queue) result-a (queue.Queue) result-b (queue.Queue))
  (defn #^ None open-listener [#^ str name #^ queue.Queue bound #^ queue.Queue result]
    "To run the listener Program on another thread (the answer count goes to result)."
    (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [await-handler (state) aiohttp-http-server]
                                                                                         (named-echo name address bound.put))))))
                              :daemon True)))
  (open-listener "A" bound-a result-a)
  (.get bound-a :timeout 10)
  (setv base (.format "http://127.0.0.1:{}/" port))
  (defn :async #^ str round-trip [#^ aiohttp.ClientWebSocketResponse ws #^ str text]
    "To send one message and read one answer."
    (await (.send-str ws text))
    (. (await (.receive ws :timeout 5)) data))
  (defn :async #^ str ask-new [#^ aiohttp.ClientSession session #^ str text]
    "To open a new connection, do one round trip, return the answer, and close."
    (with [:async ws (.ws-connect session base)]
      (await (round-trip ws text))))
  (defn :async #^ None scenario []
    "To connect to A, open B overlapping, and count where new connections go after A closes only its listener."
    (with [:async session (aiohttp.ClientSession)]
      (with [:async first (.ws-connect session base)]
        (assert (= (await (round-trip first "hi")) "A:hi"))
        (open-listener "B" bound-b result-b)
        (await (asyncio.to-thread bound-b.get :timeout 10))
        (assert (= (await (round-trip first "leave")) "left"))
        ;; Connections opened on A, which closed only its listener, keep round-tripping.
        (assert (= (await (round-trip first "still")) "A:still"))
        ;; Every new connection hits B.
        (setv names (lfor _ (range 6) (await (ask-new session "who"))))
        (assert (= names (* ["B:who"] 6)) names)
        (await (.send-str first "stop")))
      (with [:async last (.ws-connect session base)]
        (await (.send-str last "stop")))))
  (asyncio.run (scenario))
  (assert (= (.get result-a :timeout 10) 1))
  (assert (= (.get result-b :timeout 10) 7)))


(defn #^ None test-a-shutdown-stops-listening-before-it-closes-with-the-code-it-names []
  ;; Failure case (close on the side that steps aside in a swap): when listeners A and B share a port, A closes naming the close status code 1001 (going away).
  ;; A's clients receive 1001, and every new connection they re-establish right after receiving it hits B (A closes its listener before closing its clients).
  ;; Before the fix HttpShutdown had no status code field (clients received 1000), and the listener was closed after the send box was fully drained, so a re-establishment right after
  ;; could hit A (red).
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server"))
  (import aiohttp [WSMsgType])
  (import doeff_core_effects [aiohttp-http-server])
  (setv port (free-port) address (HttpAddress :host "127.0.0.1" :port port)
        bound-a (queue.Queue) bound-b (queue.Queue) result-a (queue.Queue) result-b (queue.Queue))
  (defn #^ None open-listener [#^ str name #^ queue.Queue bound #^ queue.Queue result]
    "To run the listener Program on another thread (the answer count goes to result)."
    (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [await-handler (state) aiohttp-http-server]
                                                                                         (named-echo name address bound.put))))))
                              :daemon True)))
  (open-listener "A" bound-a result-a)
  (.get bound-a :timeout 10)
  (setv base (.format "http://127.0.0.1:{}/" port))
  (defn :async #^ str ask-new [#^ aiohttp.ClientSession session #^ str text]
    "To open a new connection, do one round trip, return the answer, and close."
    (with [:async ws (.ws-connect session base)]
      (await (.send-str ws text))
      (. (await (.receive ws :timeout 5)) data)))
  (defn :async #^ None scenario []
    "To connect to A, open B overlapping, and count where the re-establishments right after A is made to close with 1001 go."
    (with [:async session (aiohttp.ClientSession)]
      (with [:async first (.ws-connect session base)]
        (await (.send-str first "hi"))
        (assert (= (. (await (.receive first :timeout 5)) data) "A:hi"))
        (open-listener "B" bound-b result-b)
        (await (asyncio.to-thread bound-b.get :timeout 10))
        (await (.send-str first "go-away"))
        (setv closing (await (.receive first :timeout 5)))
        (assert (= [closing.type closing.data] [WSMsgType.CLOSE 1001]) closing))
      ;; Every re-establishment right after receiving the close hits B.
      (setv names (lfor _ (range 6) (await (ask-new session "who"))))
      (assert (= names (* ["B:who"] 6)) names)
      (with [:async last (.ws-connect session base)]
        (await (.send-str last "stop")))))
  (asyncio.run (scenario))
  (assert (= (.get result-a :timeout 10) 1))
  (assert (= (.get result-b :timeout 10) 7)))


(defn #^ None test-the-aiohttp-server-names-an-answer-the-peer-left-in-one-line-and-counts-it [#^ (get pytest.CaptureFixture str) capfd
                                                                                               #^ pytest.LogCaptureFixture caplog]
  ;; Answer to a request the peer cut first: do not emit aiohttp's traceback (Error handling request); leave a one-line announcement and
  ;; the listener's count (the total at close). The next request is answered as before.
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import queue)
  (import struct)
  (import time)
  (import doeff_core_effects [aiohttp-http-server])
  (setv bound (queue.Queue) left (threading.Event) result (queue.Queue))
  (defk answer-after-the-peer-left []
    {:pre [] :post [(: % int)]}
    "Test Program: hand over the bound destination, answer the first request after the peer has left, answer the second normally and close (answer = the number answered)."
    (<- address HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
    (.put bound address)
    (<- late HttpRequestArrived (HttpNextRequest))
    (.wait left 10)
    (<- (HttpRespond :ticket late.ticket :status 200 :headers #() :body (HttpBodyBytes :data b"too late")))
    (<- on-time HttpRequestArrived (HttpNextRequest))
    (<- (HttpRespond :ticket on-time.ticket :status 200 :headers #() :body (HttpBodyBytes :data b"on time")))
    (<- (HttpShutdown :reason "the test closed it" :drain-seconds 1.0))
    (<- (HttpNextRequest))
    2)
  (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [await-handler (state) aiohttp-http-server]
                                                                                       (answer-after-the-peer-left))))))
                            :daemon True))
  (setv port (. (.get bound :timeout 30) port))
  ;; First: send and cut at once with RST (the answer is late and the peer's limit comes first -- the production curl -m 2). Let aiohttp receive the cut before answering.
  (with [peer (socket.create-connection #("127.0.0.1" port))]
    (.sendall peer b"GET /late HTTP/1.1\r\nHost: test\r\n\r\n")
    (.setsockopt peer socket.SOL-SOCKET socket.SO-LINGER (struct.pack "ii" 1 0)))
  (time.sleep 0.3)
  (.set left)
  ;; Second: answer normally.
  (setv reply b"")
  (with [peer (socket.create-connection #("127.0.0.1" port) :timeout 10)]
    (.sendall peer b"GET /on-time HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\r\n")
    (while True
      (setv chunk (.recv peer 4096))
      (when (not chunk) (break))
      (setv reply (+ reply chunk))))
  (assert (= (.get result :timeout 30) 2))
  (assert (.endswith reply b"on time") reply)
  (setv err (. (.readouterr capfd) err))
  ;; aiohttp's traceback appears in the records of logger aiohttp.server (caught by pytest's logging capture).
  (assert (= (lfor record caplog.records :if (.startswith record.name "aiohttp") (.getMessage record)) []) caplog.text)
  (assert (not-in "Error handling request" err) err)
  (assert (not-in "Traceback" err) err)
  (assert (= (.count err "was not delivered because the peer closed first") 1) err)
  (assert (in "answer to GET /late (status 200)" err) err)
  (assert (in "number 1 on this listener" err) err)
  (assert (in "undelivered answers in total: 1" err) err))


;; --- The probe port is not caught up in a stall of the main flow-------------------------------------------------------------
;; The record job of 2026-10-02: the listener, timers, and Await all rode one event loop shared by the process, and while the loop was stopped for 30 seconds /healthz did not
;; answer either (#2734). The probe port answers on the listener's loop, with the answer on its own run on another thread, so even if the main flow (the shared loop or the
;; scheduler) is blocked /healthz answers and /readyz follows the answer's judgment (age of the beat). A request that reaches the main body does not answer while blocked (control).

;; The limit for waiting for a probe answer, the beat age at which /readyz says 503, the seconds to wait until the peer exceeds that age, the limit for waiting for a request that reaches the main body, and the blocking limit (insurance until the peer releases)
;; -- all in seconds.
(val PROBE-SECONDS 0.5)
(val READY-STALE-SECONDS 0.1)
(val STALE-WAIT-SECONDS 0.15)
(val WORK-SECONDS 0.2)
(val HOLD-SECONDS 2.0)


(defrecord Beat
  "The last beat of the main flow (monotonic clock seconds) -- put in the latest-value store, and read by the /readyz answer in another run."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ float at))


(defrecord Fetched
  "The answer to one peer request: the status (None if there is no answer in time) and the body."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ (| int None) status)
  (#^ str body))


(defrecord HeldAnswers
  "What the peer received while the main flow was blocked: health and ready = answers of the probe port, work = the answer to the request that reaches the main body."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ Fetched health)
  (#^ Fetched ready)
  (#^ Fetched work))


(defk alive []
  {:pre [] :post [(: % HttpProbeAnswer)] :tags {:context "http-server-test" :role "program"}}
  "The /healthz answer: 200 if the process is there (does not look at the main flow)."
  (HttpProbeAnswer :status 200 :headers #() :body b"alive"))


(defk readiness [stale-seconds]
  {:pre [(: stale-seconds float)] :post [(: % HttpProbeAnswer)] :tags {:context "http-server-test" :role "program"}}
  "The /readyz answer: if the last beat in the store is older than stale-seconds (or absent yet) it says 503, and if newer, 200."
  (<- beat (| Beat None) (ReadLatest Beat))
  (val stalled (if (is beat None) None (- (time.monotonic) beat.at)))
  (if (and (is-not stalled None) (<= stalled stale-seconds))
      (HttpProbeAnswer :status 200 :headers #() :body b"ready")
      (HttpProbeAnswer :status 503 :headers #() :body (.encode (.format "the main flow has not ticked for {} seconds" stalled) "utf-8"))))


(defk fetch [address path seconds]
  {:pre [(: address HttpAddress) (: path str) (: seconds float)] :post [(: % Fetched)] :tags {:context "http-server-test" :role "foundation"}}
  "One peer request: send a GET and read the answer within seconds seconds (status None if it does not come)."
  (val connection (http.client.HTTPConnection address.host address.port :timeout seconds))
  (try
    (.request connection "GET" path)
    (val response (.getresponse connection))
    (Fetched :status response.status :body (.decode (.read response) "utf-8"))
    (except [TimeoutError]
      (Fetched :status None :body ""))
    (finally
      (.close connection))))


(deff ask-while-held [address held release box]  ; defk にできない: a callback that threading.Thread calls on another thread
  {:pre [(: address HttpAddress) (: held threading.Event) (: release threading.Event) (: box queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "After the main flow is blocked, wait until the beat is old, send two probe-port requests and one request that reaches the main body, put the answers in box, and release the flow (put the exception on failure)."
  (try
    (.wait held HOLD-SECONDS)
    (time.sleep STALE-WAIT-SECONDS)
    (.put box (HeldAnswers :health (run (fetch address "/healthz" PROBE-SECONDS)) :ready (run (fetch address "/readyz" PROBE-SECONDS))
                           :work (run (fetch address "/work" WORK-SECONDS))))
    (except [error Exception]
      (.put box error))
    (finally
      (.set release)))
  None)


(defk hold-the-loop [held release]
  {:pre [(: held threading.Event) (: release threading.Event)] :post [(: % None)] :tags {:context "http-server-test" :role "program"}}
  "Form 1 of blocking the main flow: stop the thread of await-handler's shared event loop until the peer releases (a coroutine that waits synchronously on the loop)."
  (<- (Await ((fn :async [] (.set held) (.wait release HOLD-SECONDS)))))
  None)


(defk hold-the-scheduler [held release]
  {:pre [(: held threading.Event) (: release threading.Event)] :post [(: % None)] :tags {:context "http-server-test" :role "program"}}
  "Form 2 of blocking the main flow: stop the thread of the cooperative scheduler (the main body's run) until the peer releases."
  (.set held)
  (.wait release HOLD-SECONDS)
  None)


(defk probe-while-held [hold]
  {:pre [(: hold Callable)] :post [(: % HeldAnswers)] :tags {:context "http-server-test" :role "program"}}
  "Open the listener with a probe port, put one beat, block the main flow with hold, and return what the peer received in the meantime. The /readyz answer is
   a closed Program with the store and state wrapped inside."
  (val board (.format "test-http-probe-{}" (time.monotonic-ns)))
  (val held (threading.Event))
  (val release (threading.Event))
  (val box (queue.Queue))
  (val probes #((HttpProbe :path "/healthz" :answer (alive))
                (HttpProbe :path "/readyz" :answer (with_handlers [(state) (process-latest-handler board)] (readiness READY-STALE-SECONDS)))))
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0) :probes probes))
  (<- (with_handlers [(state) (process-latest-handler board)] (PublishLatest (Beat :at (time.monotonic)))))
  (.start (threading.Thread :target ask-while-held :args #(bound held release box) :daemon True))
  (<- (hold held release))
  ;; Answer up to /work the request that reached the main body while blocked (the peer has already left) before closing -- if a request with no answer is left, aiohttp waits at close.
  ;; In the form where the probe port goes through the queue (before the fix), /healthz and /readyz also arrive here.
  (var answering True)
  (while answering
    (<- arrived HttpRequestArrived (HttpNextRequest))
    (<- (HttpRespond :ticket arrived.ticket :status 200 :headers #() :body (HttpBodyBytes :data b"late")))
    (:= answering (!= arrived.path "/work")))
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 0.2))
  (val got (.get box :timeout HOLD-SECONDS))
  (when (isinstance got Exception)
    (raise got))
  got)


(defk held-answers [hold]
  {:pre [(: hold Callable)] :post [(: % HeldAnswers)] :tags {:context "http-server-test" :role "program"}}
  "To run probe-while-held under the real answerer (skipped in a venv without aiohttp)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects [aiohttp-http-server])
  (<- got HeldAnswers (with-handler [await-handler (state) aiohttp-http-server] (probe-while-held hold)))
  got)


(deftest test-a-probe-answers-while-the-shared-loop-is-held
  ;; Even if the thread of the shared event loop is stopped, /healthz is 200 and /readyz says 503 by the beat age. A request that reaches the main body does not answer (control).
  (<- got HeldAnswers (held-answers hold-the-loop))
  (assert (= got.health (Fetched :status 200 :body "alive")) got)
  (assert (= got.ready.status 503) got)
  (assert (in "has not ticked" got.ready.body) got)
  (assert (is got.work.status None) got))


(deftest test-a-probe-answers-while-the-scheduler-is-held
  ;; The same when the thread of the cooperative scheduler (the main body's run) is stopped.
  (<- got HeldAnswers (held-answers hold-the-scheduler))
  (assert (= got.health (Fetched :status 200 :body "alive")) got)
  (assert (= got.ready.status 503) got)
  (assert (in "has not ticked" got.ready.body) got)
  (assert (is got.work.status None) got))


;; --- Answers are only queued onto the listener's loop-----------------------------------------------------

(defclass [(dataclass :frozen True)] ReadAwaits [EffectBase]
  "Read the number of times the answerer's clause entered the shared event loop, as counted by awaits-counted (answer = int).")


(defhandler awaits-counted
  ;; To sandwich between the real answerer and await-handler, count the times (Await) the answerer's clause enters the shared event loop, then pass through unchanged.
  (session var entered 0)
  (Await [coroutine]
    (:= entered (+ entered 1))
    (reperform effect))
  (ReadAwaits []
    (resume entered)))


(defk respond-awaits [box]
  {:pre [(: box queue.Queue)] :post [(: % int)] :tags {:context "http-server-test" :role "program"}}
  "To open on port 0 and put the bound destination in box, answer one request, and return the number of times the shared event loop was entered during that answer (one HttpRespond)."
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (<- arrived HttpRequestArrived (HttpNextRequest))
  (<- before int (ReadAwaits))
  (<- (HttpRespond :ticket arrived.ticket :status 200 :headers #() :body (HttpBodyBytes :data b"sent")))
  (<- after int (ReadAwaits))
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 0.5))
  (- after before))


(deff ask-once [box answers]  ; defk にできない: a callback that threading.Thread calls on another thread
  {:pre [(: box queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "To send one GET once the listener is open and put the answer in answers (put the exception on failure)."
  (try
    (.put answers (run (fetch (.get box :timeout HOLD-SECONDS) "/one" WORK-SECONDS)))
    (except [error Exception]
      (.put answers error)))
  None)


(defk responded-awaits []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "To run respond-awaits under the real answerer and return #(number of entries into the shared loop, the answer the peer received) (skipped in a venv without aiohttp)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects [aiohttp-http-server])
  (val box (queue.Queue))
  (val answers (queue.Queue))
  (.start (threading.Thread :target ask-once :args #(box answers) :daemon True))
  (<- entered int (with-handler [await-handler (state) awaits-counted aiohttp-http-server] (respond-awaits box)))
  (val got (.get answers :timeout HOLD-SECONDS))
  (when (isinstance got Exception)
    (raise got))
  #(entered got))


(deftest test-the-aiohttp-server-hands-an-answer-to-the-edge-without-a-round-trip
  ;; The answer (HttpRespond) is only queued onto the listener's loop and does not enter the shared event loop (before the fix, one Await -- a round trip shared loop -> listener loop ->
  ;; back). The peer receives the answer it was sent unchanged.
  (<- got tuple (responded-awaits))
  (assert (= got #(0 (Fetched :status 200 :body "sent"))) got))


(defk listen-answer-a-gone-ticket-and-close []
  {:pre [] :post [(: % str)] :tags {:context "http-server-test" :role "program"}}
  "To open the listener, fire an answer at a ticket the listener does not have, and then close (the answer only queues and returns, so nothing is raised on the firing side)."
  (<- (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (<- (HttpRespond :ticket "no-such" :status 200 :headers #() :body (HttpNoBody)))
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 0.2))
  "returned")


(defk answer-a-ticket-the-edge-does-not-hold []
  {:pre [] :post [(: % str)] :tags {:context "http-server-test" :role "program"}}
  "To run listen-answer-a-gone-ticket-and-close under the real answerer (skipped in a venv without aiohttp)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects [aiohttp-http-server])
  (<- done str (with-handler [await-handler (state) aiohttp-http-server] (listen-answer-a-gone-ticket-and-close)))
  done)


(deftest test-an-answer-to-a-ticket-the-edge-does-not-hold-is-named-in-one-line [capfd]
  ;; If the ticket of the queued answer is not in the listener (second time, unknown ticket), nothing is raised on the firing side, and the listener's loop announces it in one line without a traceback.
  (<- done str (answer-a-ticket-the-edge-does-not-hold))
  (val err (. (.readouterr capfd) err))
  (assert (= done "returned"))
  (assert (in "command HttpRespond for ticket no-such" err) err)
  (assert (not-in "Traceback" err) err))


;; --- ws sends are only queued onto the listener's loop------------------------------------------------------------------
;; Before the fix each WsSendText waited for one Await (a round trip shared loop -> listener loop -> back), and the one cycle of a caller that sends the same message to as many connections as there are
;; stretched in proportion to the number of sends (a screen job, in the same setup as production, 1.32 ms -> 0.45 ms per send).

(val SENT-TEXTS 5)
(val SENT-CLOSE-CODE 4001)


(defk send-awaits [box]
  {:pre [(: box queue.Queue)] :post [(: % int)] :tags {:context "http-server-test" :role "program"}}
  "To open on port 0 and put the bound destination in box, upgrade one ws request, and return the number of times the shared event loop was entered while sending one text message SENT-TEXTS times
   (fire a close right after the sends -- check on the peer's side that the queued sends are delivered before the close)."
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (<- arrived HttpRequestArrived (HttpNextRequest))
  (<- (WsAccept :ticket arrived.ticket))
  (<- before int (ReadAwaits))
  (for [index (range SENT-TEXTS)]
    (<- (WsSendText :ticket arrived.ticket :text (+ "t-" (str index)))))
  (<- after int (ReadAwaits))
  (<- (WsClose :ticket arrived.ticket :code SENT-CLOSE-CODE :reason "finished sending"))
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 2.0))
  (- after before))


(deff hear-until-closed [box answers]  ; defk にできない: a callback that threading.Thread calls on another thread
  {:pre [(: box queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "To connect by ws once the listener is open, and put the sequence of text messages received up to the close frame and the close status code in answers (put the exception on failure)."
  (import aiohttp)
  (defn :async #^ tuple heard [#^ HttpAddress bound]
    "To connect by ws, receive the number of sends + one close frame, and answer the sequence of text messages (in the order received) and the contents of the first non-text frame (the close status code)
     (if the close overtakes the sends, the text sequence is short and the status code comes first)."
    (with [:async session (aiohttp.ClientSession)]
      (with [:async ws (.ws-connect session (.format "http://127.0.0.1:{}/ws" bound.port))]
        (setv frames (lfor _ (range (+ SENT-TEXTS 1)) (await (.receive ws :timeout HOLD-SECONDS))))
        #((tuple (gfor frame frames :if (= frame.type aiohttp.WSMsgType.TEXT) frame.data))
          (next (gfor frame frames :if (!= frame.type aiohttp.WSMsgType.TEXT) frame.data) None)))))
  (try
    (.put answers (asyncio.run (heard (.get box :timeout HOLD-SECONDS))))
    (except [error Exception]
      (.put answers error)))
  None)


(defk sent-awaits []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "To run send-awaits under the real answerer and return #(number of entries into the shared loop, the sequence of text messages the peer received, the close status code) (skipped in a venv
   without aiohttp)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects [aiohttp-http-server])
  (val box (queue.Queue))
  (val answers (queue.Queue))
  (.start (threading.Thread :target hear-until-closed :args #(box answers) :daemon True))
  (<- entered int (with-handler [await-handler (state) awaits-counted aiohttp-http-server] (send-awaits box)))
  (val got (.get answers :timeout HOLD-SECONDS))
  (when (isinstance got Exception)
    (raise got))
  #(entered #* got))


(deftest test-the-aiohttp-server-queues-ws-texts-on-the-edge-without-a-round-trip
  ;; Sending one text message (WsSendText) is only queued onto the listener's loop and does not enter the shared event loop (before the fix one Await per send = 5).
  ;; The peer receives all of them in the order fired, and the close fired after that (WsClose -- passed through across) arrives after the sends (it does not overtake the queued sends).
  (<- got tuple (sent-awaits))
  (assert (= got #(0 (tuple (gfor index (range SENT-TEXTS) (+ "t-" (str index)))) SENT-CLOSE-CODE)) got))


;; --- A small declared body is read first by the listener's loop-----------------------------------------

;; The body the peer sends (with a declared length) and the read limit.
(val SMALL-BODY b"small body")
(val READ-LIMIT 64)


(defk read-awaits [box]
  {:pre [(: box queue.Queue)] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "To open on port 0 and put the bound destination in box, read the body of one request (one HttpReadBody), and answer with the body read. Answer = #(number of entries into
   the shared event loop during the read, the read's answer)."
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (<- arrived HttpRequestArrived (HttpNextRequest))
  (<- before int (ReadAwaits))
  (<- outcome HttpBodyOutcome (HttpReadBody :ticket arrived.ticket :max-bytes READ-LIMIT))
  (<- after int (ReadAwaits))
  (val data (match outcome
              (HttpBodyRead :data read) read
              _ b"unread"))
  (<- (HttpRespond :ticket arrived.ticket :status 200 :headers #() :body (HttpBodyBytes :data data)))
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 0.5))
  #((- after before) outcome))


(defk post-body [address path body seconds]
  {:pre [(: address HttpAddress) (: path str) (: body bytes) (: seconds float)] :post [(: % Fetched)]
   :tags {:context "http-server-test" :role "foundation"}}
  "One peer request: send a POST with a body that has a declared length (Content-Length), and read the answer within seconds seconds (status None if it does not come)."
  (val connection (http.client.HTTPConnection address.host address.port :timeout seconds))
  (try
    (.request connection "POST" path :body body)
    (val response (.getresponse connection))
    (Fetched :status response.status :body (.decode (.read response) "utf-8"))
    (except [TimeoutError]
      (Fetched :status None :body ""))
    (finally
      (.close connection))))


(deff send-once [box answers]  ; defk にできない: a callback that threading.Thread calls on another thread
  {:pre [(: box queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "To send one POST with a body once the listener is open and put the answer in answers (put the exception on failure)."
  (try
    (.put answers (run (post-body (.get box :timeout HOLD-SECONDS) "/one" SMALL-BODY WORK-SECONDS)))
    (except [error Exception]
      (.put answers error)))
  None)


(defk read-body-awaits []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "To run read-awaits under the real answerer and return #(number of entries into the shared loop, the read's answer, the answer the peer received) (in a venv without aiohttp:
   skipped)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects [aiohttp-http-server])
  (val box (queue.Queue))
  (val answers (queue.Queue))
  (.start (threading.Thread :target send-once :args #(box answers) :daemon True))
  (<- read tuple (with-handler [await-handler (state) awaits-counted aiohttp-http-server] (read-awaits box)))
  (val got (.get answers :timeout HOLD-SECONDS))
  (when (isinstance got Exception)
    (raise got))
  (+ read #(got)))


(deftest test-the-aiohttp-server-reads-a-small-declared-body-without-a-round-trip
  ;; A body whose declared length is small is read to the end before the listener's loop lines up the arrival, and HttpReadBody answers without entering the shared event loop (before the fix
  ;; one Await -- a round trip shared loop -> listener loop -> back). The body read goes back to the peer unchanged.
  (<- got tuple (read-body-awaits))
  (assert (= got #(0 (HttpBodyRead :data SMALL-BODY) (Fetched :status 200 :body "small body"))) got))


;; --- Arrivals already in the queue are taken without a round trip----------------------------------------------------------------
;; When one write wakes pending waits all at once, the following requests pile up in the listener's queue. Each time the receiving loop took one it made a round trip shared loop -> listener loop ->
;; back, so the wave was lined up one at a time (measured locally: with 20 waiters the read queue was 68.5 ms).

;; The number of requests sent at once, the seconds to wait from the peer finishing sending until the listener finishes lining up in the queue, and the limit in seconds for waiting for the peer's answers.
(val WAVE-SIZE 8)
(val SETTLE-SECONDS 0.5)
(val WAVE-SECONDS 10.0)


(defk take-arrivals [n]
  {:pre [(: n int)] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "To take n arrivals in order from the receiving queue (answer = the tuple in the order taken)."
  (if (= n 0)
      #()
      (do (<- head HttpRequestArrived (HttpNextRequest))
          (<- tail tuple (take-arrivals (- n 1)))
          (+ #(head) tail))))


(defk answer-each [arrivals]
  {:pre [(: arrivals tuple)] :post [(: % None)] :tags {:context "http-server-test" :role "program"}}
  "To answer each arrival with the path of its request as the body."
  (for [arrival arrivals]
    (<- (HttpRespond :ticket arrival.ticket :status 200 :headers #() :body (HttpBodyBytes :data (.encode arrival.path "utf-8")))))
  None)


(defk take-a-wave [box sent]
  {:pre [(: box queue.Queue) (: sent queue.Queue)] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "To open on port 0 and put the bound destination in box, receive the first arrival, wait until the peer has sent all the rest and they line up in the queue, take the rest, and answer all of them.
   Answer = #(number of entries into the shared event loop while taking the rest, the arrivals in the order taken)."
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (<- first HttpRequestArrived (HttpNextRequest))
  (for [_ (range WAVE-SIZE)]
    (.get sent :timeout WAVE-SECONDS))
  (time.sleep SETTLE-SECONDS)
  (<- before int (ReadAwaits))
  (<- rest tuple (take-arrivals (- WAVE-SIZE 1)))
  (<- after int (ReadAwaits))
  (val taken (+ #(first) rest))
  (<- (answer-each taken))
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 0.5))
  #((- after before) taken))


(defk take-a-wave-at-once [box sent]
  {:pre [(: box queue.Queue) (: sent queue.Queue)] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "In the same wave as take-a-wave, to take the rest after the first arrival all at once with one HttpTakeQueued and answer all of them.
   Answer = #(number of entries into the shared event loop while taking the rest, the arrivals in the order taken). Also check that an HttpTakeQueued after close names the close reason."
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (<- first HttpRequestArrived (HttpNextRequest))
  (for [_ (range WAVE-SIZE)]
    (.get sent :timeout WAVE-SECONDS))
  (time.sleep SETTLE-SECONDS)
  (<- before int (ReadAwaits))
  (<- rest tuple (HttpTakeQueued))
  (<- after int (ReadAwaits))
  (val taken (+ #(first) rest))
  (<- (answer-each taken))
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 0.5))
  (<- shut tuple (HttpTakeQueued))
  (assert (= (lfor e shut (. (type e) __name__)) ["HttpServerClosed"]) shut)
  #((- after before) taken))


(deff ask-in-wave [address path sent answers]  ; defk にできない: a callback that threading.Thread calls on another thread
  {:pre [(: address HttpAddress) (: path str) (: sent queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "After finishing sending one GET, put a mark in sent and put the answer #(path body) in answers (put the exception on failure)."
  (setv connection (http.client.HTTPConnection address.host address.port :timeout WAVE-SECONDS))
  (try
    (.request connection "GET" path)
    (.put sent path)
    (setv response (.getresponse connection))
    (.put answers #(path (.decode (.read response) "utf-8")))
    (except [error Exception]
      (.put sent error)
      (.put answers error))
    (finally
      (.close connection)))
  None)


(deff fire-wave [box sent answers]  ; defk にできない: a callback that threading.Thread calls on another thread
  {:pre [(: box queue.Queue) (: sent queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "To send WAVE-SIZE GETs at once from separate connections once the listener is open (/w0 ...)."
  (setv address (.get box :timeout WAVE-SECONDS))
  (for [n (range WAVE-SIZE)]
    (.start (threading.Thread :target ask-in-wave :args #(address (.format "/w{}" n) sent answers) :daemon True)))
  None)


(defk wave-awaits [take]
  {:pre [(: take Callable)] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "To run the wave-taking program take (take-a-wave or take-a-wave-at-once) under the real answerer and return #(number of entries into the shared loop, the arrivals in the order taken
   the set of answers the peer received) (skipped in a venv without aiohttp)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects [aiohttp-http-server])
  (val box (queue.Queue))
  (val sent (queue.Queue))
  (val answers (queue.Queue))
  (.start (threading.Thread :target fire-wave :args #(box sent answers) :daemon True))
  (<- got tuple (with-handler [await-handler (state) awaits-counted aiohttp-http-server] (take box sent)))
  (val replies (frozenset (gfor _ (range WAVE-SIZE) (.get answers :timeout WAVE-SECONDS))))
  (+ got #(replies)))


(deftest test-the-aiohttp-server-hands-every-queued-arrival-at-once
  ;; HttpTakeQueued takes all the arrivals already in the queue at once without entering the shared event loop. Neither missed ones nor reordering
  ;; occur: the remaining WAVE-SIZE - 1 after the first are taken in ticket order, and every peer receives the answer for its own path. After close, the close reason.
  (<- got tuple (wave-awaits take-a-wave-at-once))
  (val entered (get got 0))
  (val taken (get got 1))
  (val replies (get got 2))
  (val paths (frozenset (gfor n (range WAVE-SIZE) (.format "/w{}" n))))
  (assert (= entered 0) got)
  (assert (= (len taken) WAVE-SIZE) taken)
  (assert (= (lfor a taken (int a.ticket)) (sorted (gfor a taken (int a.ticket)))) taken)
  (assert (= (frozenset (gfor a taken a.path)) paths) taken)
  (assert (= replies (frozenset (gfor p paths #(p p)))) replies))


(deftest test-the-aiohttp-server-takes-queued-arrivals-without-a-round-trip
  ;; The arrivals already in the queue are taken by HttpNextRequest without entering the shared event loop (before the fix one Await per taken arrival -- shared loop ->
  ;; listener loop -> back). Neither missed ones nor reordering occur: the order taken stays the ticket order, and every peer receives the answer for its own path.
  (<- got tuple (wave-awaits take-a-wave))
  (val entered (get got 0))
  (val taken (get got 1))
  (val replies (get got 2))
  (val paths (frozenset (gfor n (range WAVE-SIZE) (.format "/w{}" n))))
  (assert (= entered 0) got)
  (assert (= (lfor a taken (int a.ticket)) (sorted (gfor a taken (int a.ticket)))) taken)
  (assert (= (frozenset (gfor a taken a.path)) paths) taken)
  (assert (= replies (frozenset (gfor p paths #(p p)))) replies))


;; --- The two limits when the ws relay connects to the target (up to connecting, and the handshake answer)-------------------------------------------------------------
;; While the relay target is being swapped, the connecting SYN to the target is silently dropped (neither an answer nor a refusal comes back). Before the fix the ws relay waited up to the connection limit shared with the HTTP relay
;; (CONNECT-SECONDS, 10 seconds) before returning 502, and since that was the same length as the peer's connect limit, the peer's retry was delayed. The ws relay
;; holds two separate limits when connecting to the target: up to connecting (name lookup + TCP connection -- WS-UPSTREAM-CONNECT-SECONDS) and the handshake
;; answer after connecting (WS-UPSTREAM-HANDSHAKE-SECONDS). The test shortens only the limit it looks at, to save seconds.
;;   * A target whose SYN gets no answer is given up on at "up to connecting" and gets 502 (before the fix it waited 10 seconds)
;;   * A target on a closed port gets 502 without waiting for the limit
;;   * Even if a connected target answers the handshake later than the "up to connecting" limit, it is connected and frames pass instead of 502 (if the whole connect were cut by one limit,
;;     the peer would keep retrying against a target slow to answer)
;;   * A target that connected but does not answer the handshake is given up on at "the handshake answer" and gets 502 (before the fix it waited 300 seconds)
;;   * After connecting, it does not cut even if silent for longer than the "handshake answer" limit (there is no limit on frame reads)

;; The shortened limit (the side looked at), the seconds the target delays the handshake answer (later than the shortened limit), the seconds the peer is silent after connecting (longer than the shortened limit), the limit until the relay's
;; answer (502) reaches the peer (shortened limit + margin -- shorter than the 3-second and 15-second limits of the sides not shortened), the limit for the peer to wait for one answer, and the limit for the target's
;; counterpart to keep listening (insurance for when the peer does not come) -- all in seconds.
(val CUT-SECONDS 0.3)
(val LATE-ANSWER-SECONDS 0.6)
(val QUIET-SECONDS 0.9)
(val ANSWER-WITHIN-SECONDS 2.0)
(val DIAL-SECONDS 20.0)
(val UPSTREAM-LIFE-SECONDS 30.0)
;; The upper limit on the number of connections that fill the wait queue when building a port that drops SYN, and the seconds after which the queue is deemed full (the connect does not return).
(val STALL-FILL-LIMIT 8)
(val STALL-PROBE-SECONDS 0.2)
;; The names (Python spelling) of the constants for the shortened limits in the real answerer's module.
(val CONNECT-LIMIT "WS_UPSTREAM_CONNECT_SECONDS")
(val HANDSHAKE-LIMIT "WS_UPSTREAM_HANDSHAKE_SECONDS")


(defrecord Dialed
  "The result of the peer connecting to the listener by ws: status = 101 if upgraded, the refusal status if refused, None if there was no answer in time. seconds = seconds until the connect answer.
   echoes = the sequence of one-message answers received after upgrading (in the order sent -- a non-text message is the type name and its contents)."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ (| int None) status)
  (#^ float seconds)
  (#^ (get tuple #(str ...)) echoes))


(defrecord Stalled
  "A port whose connecting SYN gets no answer: the port, and the sequence of sockets kept open to hold that state (closing them all releases it)."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ int port)
  (#^ (get tuple #(socket.socket ...)) held))


(defk stalled-port []
  {:pre [] :post [(: % Stalled)] :tags {:context "http-server-test" :role "foundation"}}
  "To build a 127.0.0.1 port whose connecting SYN gets no answer (the form while a relay target is being swapped): if a listener that nobody accepts from (backlog 0) has its wait queue
   filled with connections, the kernel silently drops later SYNs. That it is filled is confirmed by the next connect not returning within STALL-PROBE-SECONDS. Skipped on an OS that does not drop SYN even when the wait
   queue is full."
  (val listener (socket.socket))
  (.bind listener #("127.0.0.1" 0))
  (.listen listener 0)
  (val port (get (.getsockname listener) 1))
  (var held #(listener))
  (var stalled False)
  (while (and (not stalled) (<= (len held) STALL-FILL-LIMIT))
    (val filler (socket.socket))
    (.settimeout filler STALL-PROBE-SECONDS)
    (try
      (.connect filler #("127.0.0.1" port))
      (:= held (+ held #(filler)))
      (except [TimeoutError]
        (.close filler)
        (:= stalled True))))
  (when (not stalled)
    (for [opened held]
      (.close opened))
    (pytest.skip "this OS does not drop SYN to a listener whose wait queue is full"))
  (Stalled :port port :held held))


(defk silent-listener []
  {:pre [] :post [(: % socket.socket)] :tags {:context "http-server-test" :role "foundation"}}
  "To build a 127.0.0.1 listener whose TCP connection succeeds (the kernel queues it in the wait queue) but that nobody accepts from and that answers nothing (the form of a target that connected but does not answer
   the handshake)."
  (val listener (socket.socket))
  (.bind listener #("127.0.0.1" 0))
  (.listen listener)
  listener)


(deff answer-ws-late [delay box]  ; defk にできない: a callback that threading.Thread calls on another thread
  {:pre [(: delay float) (: box queue.Queue)] :post [(: % None)] :tags {:context "http-server-test" :role "foundation"}}
  "The target's counterpart: accept the TCP connection at once, delay the ws handshake answer (101) by delay seconds, and after upgrading return each text message with echo: attached.
   It listens on this thread's event loop, puts the bound port in box, and listens until one ws connection ends (at most UPSTREAM-LIFE-SECONDS)."
  (import aiohttp [web])
  (setv loop (asyncio.new-event-loop)
        served (asyncio.Event)
        app (web.Application))
  (.add-route app.router "GET" "/ws"
              (fn :async [request]
                (await (asyncio.sleep delay))
                (setv ws (web.WebSocketResponse))
                (try
                  (await (.prepare ws request))
                  (for [:async message ws]
                    (await (.send-str ws (+ "echo:" message.data))))
                  (finally
                    (.set served)))
                ws))
  (setv runner (web.AppRunner app))
  (.run-until-complete loop (.setup runner))
  (.run-until-complete loop (.start (web.TCPSite runner "127.0.0.1" 0)))
  (.put box (get (get runner.addresses 0) 1))
  (try
    (.run-until-complete loop (asyncio.wait-for (.wait served) UPSTREAM-LIFE-SECONDS))
    (except [TimeoutError] None)
    (finally
      (.run-until-complete loop (.cleanup runner))
      (.close loop)))
  None)


(defk late-ws-upstream [delay]
  {:pre [(: delay float)] :post [(: % str)] :tags {:context "http-server-test" :role "foundation"}}
  "To open, on another thread, a ws counterpart (answer-ws-late) that delays the handshake answer by delay seconds, and return its url."
  (val box (queue.Queue))
  (.start (threading.Thread :target answer-ws-late :args #(delay box) :daemon True))
  (.format "http://127.0.0.1:{}/ws" (.get box :timeout DIAL-SECONDS)))


(defk dial [address texts quiet]
  {:pre [(: address HttpAddress) (: texts tuple) (: quiet float)] :post [(: % Dialed)]
   :tags {:context "http-server-test" :role "foundation"}}
  "One peer ws connect (an event loop only for this thread and an aiohttp client): connect to the listener's /ws and measure the seconds until the answer (101 or a refusal),
   and if upgraded, stay quiet for quiet seconds, send texts one message at a time, receive one message back, and close."
  (import aiohttp [ClientSession ClientWebSocketResponse WSMsgType WSServerHandshakeError])
  (val loop (asyncio.new-event-loop))
  (val session (ClientSession :loop loop))
  (val started (time.monotonic))
  (var echoes #())
  (try
    (val answer (try
                  (.run-until-complete loop (asyncio.wait-for (.ws-connect session (.format "http://{}:{}/ws" address.host address.port))
                                                              DIAL-SECONDS))
                  (except [refused WSServerHandshakeError] refused.status)
                  (except [TimeoutError] None)))
    (val seconds (- (time.monotonic) started))
    (match answer
      (ClientWebSocketResponse)
        (do (time.sleep quiet)
            (for [text texts]
              (.run-until-complete loop (.send-str answer text))
              (val message (.run-until-complete loop (.receive answer :timeout DIAL-SECONDS)))
              (:= echoes (+ echoes #((match message.type
                                       WSMsgType.TEXT message.data
                                       other (.format "{}: {!r}" other.name message.data))))))
            (.run-until-complete loop (.close answer))
            (Dialed :status 101 :seconds seconds :echoes echoes))
      status (Dialed :status status :seconds seconds :echoes #()))
    (finally
      (.run-until-complete loop (.close session))
      (.close loop))))


(deff dial-once [box answers texts quiet]  ; defk にできない: a callback that threading.Thread calls on another thread
  {:pre [(: box queue.Queue) (: answers queue.Queue) (: texts tuple) (: quiet float)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "Once the listener is open, connect by ws once and put the result in answers (put the exception on failure), and send /done to make the listener close."
  (setv address (.get box :timeout DIAL-SECONDS))
  (try
    (.put answers (run (dial address texts quiet)))
    (except [error Exception]
      (.put answers error))
    (finally
      (run (fetch address "/done" DIAL-SECONDS))))
  None)


(defk relay-until-done [upstream box release]
  {:pre [(: upstream str) (: box queue.Queue) (: release Callable)] :post [(: % int)]
   :tags {:context "http-server-test" :role "program"}}
  "To open on port 0 and put the bound destination in box, relay requests other than /done to upstream by ws (WsForward), and when /done arrives fold up the target's
   counterpart with release and then close (answer = the number relayed). The counterpart is folded up first so that, when the relay is still waiting on the target (the form where the limit does not take effect), the listener's
   close does not keep company with that wait."
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (var relayed 0)
  (var open True)
  (while open
    (<- arrived HttpRequestArrived (HttpNextRequest))
    (match arrived.path
      "/done" (do (<- (HttpRespond :ticket arrived.ticket :status 204 :headers #() :body (HttpNoBody)))
                  (:= open False))
      _ (do (<- (WsForward :ticket arrived.ticket :url upstream))
            (:= relayed (+ relayed 1)))))
  (release)
  (<- (HttpShutdown :reason "the test closed it" :drain-seconds 0.5))
  relayed)


(defk dialed-through [upstream texts quiet release]
  {:pre [(: upstream str) (: texts tuple) (: quiet float) (: release Callable)] :post [(: % Dialed)]
   :tags {:context "http-server-test" :role "program"}}
  "To run relay-until-done under the real answerer and return the result of the peer connecting by ws once (skipped in a venv without aiohttp)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects [aiohttp-http-server])
  (val box (queue.Queue))
  (val answers (queue.Queue))
  (.start (threading.Thread :target dial-once :args #(box answers texts quiet) :daemon True))
  (<- _relayed int (with-handler [await-handler (state) aiohttp-http-server] (relay-until-done upstream box release)))
  (val got (.get answers :timeout DIAL-SECONDS))
  (when (isinstance got Exception)
    (raise got))
  got)


(defk shrink [monkeypatch limit]
  {:pre [(: monkeypatch pytest.MonkeyPatch) (: limit str)] :post [(: % None)] :tags {:context "http-server-test" :role "foundation"}}
  "To shorten the real answerer's limit constant limit to CUT-SECONDS only for this test (the listener reads the constant when it opens; in a venv without aiohttp:
   skipped)."
  (pytest.importorskip "aiohttp" :reason "aiohttp is a dependency of the extra http-server")
  (import doeff_core_effects._aiohttp_http_server :as edge-module)
  (.setattr monkeypatch edge-module limit CUT-SECONDS)
  None)


(deftest test-a-ws-forward-gives-up-an-upstream-that-drops-the-connect [monkeypatch]
  ;; A target whose SYN gets no answer is given up on at the "up to connecting" limit, and gets 502 without upgrading to ws. The answer does not come before the limit (it is giving up, not a refusal).
  (<- (shrink monkeypatch CONNECT-LIMIT))
  (<- stalled Stalled (stalled-port))
  (<- got Dialed (dialed-through (.format "http://127.0.0.1:{}/ws" stalled.port) #() 0.0
                                 (fn [] (for [opened stalled.held] (.close opened)))))
  (assert (= got.status 502) got)
  (assert (<= CUT-SECONDS got.seconds ANSWER-WITHIN-SECONDS) got))


(deftest test-a-ws-forward-to-a-closed-port-answers-502-at-once
  ;; A target on a closed port (the refusal returns at once) gets 502 without waiting for the limit. The limit is not shortened (3 seconds up to connecting) -- the answer is earlier than that.
  (<- got Dialed (dialed-through (.format "http://127.0.0.1:{}/ws" (free-port)) #() 0.0 (fn [] None)))
  (assert (= got.status 502) got)
  (assert (< got.seconds ANSWER-WITHIN-SECONDS) got))


(deftest test-a-ws-forward-keeps-an-upstream-that-answers-the-handshake-late [monkeypatch]
  ;; Even if a connected target answers the handshake later than the "up to connecting" limit, it connects instead of 502, and frames round-trip.
  (<- (shrink monkeypatch CONNECT-LIMIT))
  (<- upstream str (late-ws-upstream LATE-ANSWER-SECONDS))
  (<- got Dialed (dialed-through upstream #("hi") 0.0 (fn [] None)))
  (assert (= [got.status got.echoes] [101 #("echo:hi")]) got)
  (assert (>= got.seconds LATE-ANSWER-SECONDS) got))


(deftest test-a-ws-forward-gives-up-an-upstream-that-never-answers-the-handshake [monkeypatch]
  ;; A target that connected but does not answer the handshake is given up on at the "handshake answer" limit, and gets 502 without upgrading to ws.
  (<- (shrink monkeypatch HANDSHAKE-LIMIT))
  (<- listener socket.socket (silent-listener))
  (<- got Dialed (dialed-through (.format "http://127.0.0.1:{}/ws" (get (.getsockname listener) 1)) #() 0.0 listener.close))
  (assert (= got.status 502) got)
  (assert (<= CUT-SECONDS got.seconds ANSWER-WITHIN-SECONDS) got))


(deftest test-a-relayed-ws-stays-open-through-a-silence-longer-than-the-handshake-limit [monkeypatch]
  ;; After connecting, it does not cut even if silent for longer than the "handshake answer" limit -- the one message after the silence round-trips.
  (<- (shrink monkeypatch HANDSHAKE-LIMIT))
  (<- upstream str (late-ws-upstream 0.0))
  (<- got Dialed (dialed-through upstream #("hi") QUIET-SECONDS (fn [] None)))
  (assert (= [got.status got.echoes] [101 #("echo:hi")]) got))
