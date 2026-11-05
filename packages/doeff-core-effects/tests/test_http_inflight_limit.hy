;;; How many requests the HTTP handler hands to the client at once, and the connection slots left after cancellations and timeouts (the screen's server
;;; fired thousands of requests without limit at a single in-process client, the httpcore wait queue reached 10,651, the threads of the await bridge were all used up by the allocation check, and afterwards
;;; every later request stayed at PoolTimeout for 20 minutes even though the bridge was idle). Run against a real socket and a minimal listener inside the test (an asyncio
;;; server on the bridge loop - it counts requests and, on each arrival, measures the length of the client's httpcore queue).
;;;   (1) Even when 1,000 requests are issued at once, the httpcore queue (the number of requests in the pool) does not exceed the per-origin limit CLIENT-ORIGIN-MAX-IN-FLIGHT - before the fix,
;;;       all of them lined up in the httpcore queue
;;;   (2) Even when one origin fills its slots with requests that never answer, requests to other origins get answers - before the fix, the unanswering origin held all 100 connections of the connection limit,
;;;       and requests to other origins hit PoolTimeout
;;;   (3) If a request waiting in the queue is cancelled right after a new connection is assigned to it, httpcore 1.0.9 leaves an unconnected connection (CONNECTING) in the pool
;;;       (shown with a plain client as the shape of the upstream bug - when upstream fixes it this assertion fails, signalling that the workaround can be removed). The handler's send path (_sent-or-reaped)
;;;       leaves none, and the next request of a client limited to 1 connection gets a connection
;;;   (4) After 600 requests cancelled and timed out through the handler, the next request of the same client does not hit PoolTimeout, and no CONNECTING is left in the pool
(require doeff-hy.macros [deftest defk <- val with-handler])
(import asyncio)
(import random)
(import httpx)
(import pytest)
(import doeff_core_effects [Await])
(import doeff_core_effects [await-handler])
(import doeff_core_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_core_effects [http-production-handler])
(import doeff_core_effects._http_handlers_impl [CLIENT-MAX-CONNECTIONS CLIENT-ORIGIN-MAX-IN-FLIGHT http-client-factory _sent-or-reaped])
(import doeff_core_effects [Cancel Gather Spawn])

;; The waits in this file are in real time (what counts is listed in the row for this file in the roster tests/realtime_tests.toml).
(val pytestmark pytest.mark.realtime)

;; The number of requests issued at once ((1)), and the number of requests issued with cancellations and timeouts mixed in ((4)).
(val BURST 1000)
(val STORM 600)
;; The number of requests sent in (2) to the unanswering origin - it exceeds the per-origin limit and also the connection limit of 100.
(val HELD-REQUESTS 150)
;; Upper limit for waiting for an answer (seconds - the same limit as the pool's wait for a connection slot).
(val ANSWER-SECONDS 5.0)
;; Timeout limit (seconds) of the requests that time out in (4), and the upper limit (seconds) of the pause the listener inserts before answering.
(val STORM-TIMEOUT-SECONDS 0.003)
(val STORM-DELAY-SECONDS 0.01)
(val OK-RESPONSE b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")


(defclass Lane []
  "A minimal HTTP/1.1 listener inside the test (opened on the bridge loop). It counts received requests (arrivals), and on each arrival records the maximum length of the watched client's httpcore
   queue (the number of requests in the pool) (peak). held = requests whose target (request-target) is /held do not answer until released is set.
   filled = set when fill-at unanswering requests have been received. delay = upper limit (seconds, random) of the pause before answering."
  (defn #^ None __init__ [self * #^ int [fill-at 0] #^ float [delay 0.0]]
    "To start the counts at 0 and start without a watched client."
    (setv self.arrivals 0 self.held 0 self.peak 0 self.fill-at fill-at self.delay delay self.client None
          self.released (asyncio.Event) self.filled (asyncio.Event) self.server None)
    None)
  (defn #^ httpx.AsyncClient watched [self]
    "To watch the client the handler creates at the start of the scope (passed to the client-factory of http-production-handler)."
    (setv self.client (http-client-factory))
    self.client)
  (defn :async #^ str open [self]
    "To open on a free port of 127.0.0.1 and answer the head of the URL."
    (setv self.server (await (asyncio.start-server self.serve "127.0.0.1" 0)))
    (.format "http://127.0.0.1:{}" (get (.getsockname (get self.server.sockets 0)) 1)))
  (defn :async #^ None release [self]
    "To make the unanswering requests answer."
    (.set self.released)
    None)
  (defn :async #^ None close [self]
    "To close the listener (releasing the unanswering requests first)."
    (.set self.released)
    (.close self.server)
    None)
  (defn :async #^ None serve [self reader writer]
    "To receive and answer the requests of one connection in order (reusing the connection)."
    (try
      (while True
        (setv head (await (.readuntil reader b"\r\n\r\n")))
        (+= self.arrivals 1)
        (when (is-not self.client None)
          (setv self.peak (max self.peak (len self.client._transport._pool._requests))))
        (when (.startswith (get (.split head b" ") 1) b"/held")
          (+= self.held 1)
          (when (= self.held self.fill-at)
            (.set self.filled))
          (await (.wait self.released)))
        (when (> self.delay 0.0)
          (await (asyncio.sleep (* (random.random) self.delay))))
        (.write writer OK-RESPONSE)
        (await (.drain writer)))
      (except [Exception]
        (.close writer)))
    None))


(defk opened [lane]
  {:pre [(: lane Lane)] :post [(: % str)] :tags {:context "http" :role "foundation"}}
  "To open the listener on the bridge loop and answer the head of the URL."
  (<- base str (Await (.open lane)))
  base)


(defk asked [url timeout values]
  {:pre [(: url str) (: timeout float) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "program"}}
  "To issue a GET once (no re-firing - the handler's resending must not hide the wait for a slot)."
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "GET" url :max-retries 0 :timeout-seconds timeout :failures-as-values values))
  answer)


(defk all-ok [answers]
  {:pre [(: answers tuple)] :post [(: % bool)] :tags {:context "http" :role "program"}}
  "To judge whether all the answers are 200 responses."
  (all (gfor answer answers (and (isinstance answer HttpResponse) (= answer.status 200)))))


(defk spawned [requests]
  {:pre [(: requests list)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To run a list of request Programs concurrently, one per task, and answer the tuple of tasks."
  (var tasks #())
  (for [request requests]
    (<- task (Spawn request))
    (:= tasks (+ tasks #(task))))
  tasks)


(defk burst [url count]
  {:pre [(: url str) (: count int)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To issue count GETs at once (one per task) and wait for all the answers."
  (<- tasks tuple (spawned (lfor _ (range count) (asked url ANSWER-SECONDS False))))
  (<- answers (Gather #* tasks))
  (tuple answers))


(deftest test-a-burst-waits-in-the-scheduler-not-in-the-httpcore-queue
  ;; (1): Issue 1,000 at once. Before the fix, all of them lined up in the httpcore queue (queue maximum ~ 1,000). After the fix, it stays within the per-origin limit.
  (val lane (Lane))
  (<- answers tuple
      (with-handler [await-handler (http-production-handler :client-factory lane.watched)]
        (do-burst lane)))
  (assert (! (all-ok answers)))
  (assert (= lane.arrivals BURST) lane.arrivals)
  (assert (<= lane.peak CLIENT-ORIGIN-MAX-IN-FLIGHT) lane.peak)
  (assert (< CLIENT-ORIGIN-MAX-IN-FLIGHT CLIENT-MAX-CONNECTIONS)))


(defk do-burst [lane]
  {:pre [(: lane Lane)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To open the listener, issue BURST requests at once, and close the listener."
  (<- base str (opened lane))
  (try
    (<- answers tuple (burst (+ base "/burst") BURST))
    answers
    (finally (<- (Await (.close lane))))))


(defk held-then-other [held other]
  {:pre [(: held Lane) (: other Lane)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To issue HELD-REQUESTS unanswering requests to the held origin, issue one to the other origin once the origin's slots are full, and answer that answer,
   the number of requests received until held is released, and the answers of held after release."
  (<- held-base str (opened held))
  (<- other-base str (opened other))
  (try
    (<- tasks tuple (spawned (lfor _ (range HELD-REQUESTS) (asked (+ held-base "/held") 30.0 False))))
    (<- (Await (.wait held.filled)))
    (<- answer (| HttpResponse HttpFailed) (asked (+ other-base "/other") ANSWER-SECONDS True))
    (val arrived-while-held held.held)
    (<- (Await (.release held)))
    (<- held-answers (Gather #* tasks))
    #(answer arrived-while-held (tuple held-answers))
    (finally
      (<- (Await (.close held)))
      (<- (Await (.close other))))))


(deftest test-a-held-origin-leaves-room-for-another-origin
  ;; (2): Issue 150 to the unanswering origin (exceeding the connection limit of 100), and once the origin's slots are full, issue one to another origin. Before the fix, the unanswering origin held all 100
  ;; connections, and the request to the other origin became PoolTimeout (HttpFailed). After the fix, only the origin's 64 slots are filled, and the other origin gets an answer.
  (val held (Lane :fill-at CLIENT-ORIGIN-MAX-IN-FLIGHT))
  (val other (Lane))
  (<- seen tuple
      (with-handler [await-handler (http-production-handler)]
        (held-then-other held other)))
  (val answer (get seen 0))
  (val arrived-while-held (get seen 1))
  (val held-answers (get seen 2))
  (assert (and (isinstance answer HttpResponse) (= answer.status 200)) answer)
  (assert (= arrived-while-held CLIENT-ORIGIN-MAX-IN-FLIGHT) arrived-while-held)
  (assert (! (all-ok held-answers))))


(defn :async #^ tuple leak-scenario [#^ bool reaped]
  "With a client limited to 1 connection, issue an unanswering request A, queue request B, cancel A, cancel B at the moment a new connection is assigned to B,
   and answer the list of info of the connections left in the pool and the outcome of the next request (a status number or PoolTimeout) (head note (3)). reaped = whether to send by the handler's send path
   (_sent-or-reaped) or by a plain client.request."
  (setv lane (Lane))
  (setv base (await (.open lane)))
  (setv client (httpx.AsyncClient :limits (httpx.Limits :max-connections 1)))
  (setv pool client._transport._pool)
  (defn send [path]
    (if reaped
        (_sent-or-reaped client :method "GET" :url (+ base path))
        (.request client "GET" (+ base path))))
  (try
    (setv held (asyncio.ensure-future (send "/held")))
    (setv queued (asyncio.ensure-future (send "/queued")))
    ;; Spin until B lines up in the pool's queue (A gets a connection and waits for an answer).
    (while (not (any (gfor request pool._requests (= request.request.url.target b"/queued"))))
      (await (asyncio.sleep 0)))
    (setv waiting (next (gfor request pool._requests :if (= request.request.url.target b"/queued") request)))
    (.cancel held)
    ;; At the moment A's cancellation closes the connection and a new connection is assigned to B, cancel B (B has not woken up yet).
    (while (is waiting.connection None)
      (await (asyncio.sleep 0)))
    (.cancel queued)
    (await (asyncio.gather held queued :return-exceptions True))
    (setv left (tuple (gfor connection pool._connections (.info connection))))
    (setv outcome
          (try
            (. (await (.request client "GET" (+ base "/next") :timeout (httpx.Timeout ANSWER-SECONDS :pool 0.5))) status-code)
            (except [httpx.PoolTimeout]
              "PoolTimeout")))
    #(left outcome)
    (finally
      (await (.aclose client))
      (await (.close lane)))))


(deftest test-a-request-cancelled-as-it-is-given-a-new-connection-leaves-no-slot-behind
  ;; (3): A plain client leaves an unconnected connection, and the next request of a client limited to 1 connection becomes PoolTimeout (the shape of upstream httpcore 1.0.9).
  ;; The handler's send path leaves none, and the next request gets an answer.
  (<- plain tuple (with-handler [await-handler] (Await (leak-scenario False))))
  (assert (= plain #(#("CONNECTING") "PoolTimeout")) plain)
  (<- reaped tuple (with-handler [await-handler] (Await (leak-scenario True))))
  (assert (= reaped #(#() 200)) reaped))


(defk storm [lane]
  {:pre [(: lane Lane)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "To issue STORM GETs at once (half with a short limit so they time out), cancel from just after issuing every third one, wait for the remaining answers, then issue one more,
   and answer that answer and the number of CONNECTING left in the pool at that time."
  (<- base str (opened lane))
  (try
    (<- tasks tuple (spawned (lfor index (range STORM)
                                   (asked (+ base "/storm") (if (% index 2) STORM-TIMEOUT-SECONDS ANSWER-SECONDS) True))))
    (val cancelled (cut tasks 0 None 3))
    (for [task cancelled]
      (<- (Await (asyncio.sleep 0)))
      (<- (Cancel task)))
    (<- (Gather #* (lfor #(index task) (enumerate tasks) :if (% index 3) task)))
    (<- answer (| HttpResponse HttpFailed) (asked (+ base "/next") ANSWER-SECONDS True))
    (val connecting (len (lfor connection lane.client._transport._pool._connections :if (= (.info connection) "CONNECTING") connection)))
    #(answer connecting)
    (finally (<- (Await (.close lane))))))


(deftest test-the-next-request-after-a-storm-of-cancels-and-timeouts-gets-an-answer
  ;; (4): After 600 cancellations and timeouts through the handler, the next request of the same client gets an answer, and no unconnected connection is left in the pool.
  (val lane (Lane :delay STORM-DELAY-SECONDS))
  (<- seen tuple
      (with-handler [await-handler (http-production-handler :client-factory lane.watched)]
        (storm lane)))
  (val answer (get seen 0))
  (val connecting (get seen 1))
  (assert (and (isinstance answer HttpResponse) (= answer.status 200)) answer)
  (assert (= connecting 0) connecting))
