;;; The real handler aiohttp-http-server for the generic HTTP listening effects (http_server_effects.hy) (moved from the base of the
;;; agora-controllers webapp intake, #767 / #795). It is the real I/O for aiohttp listening, sending responses, and relaying HTTP and ws, and holds no
;;; judgment (what to send and how is decided by the caller's Program through the HttpRespond value). aiohttp is a dependency of the extra `http-server`.
;;;
;;;   Listener loop  The aiohttp server and all of this module's real I/O run on a single listener loop per process (edge-loop -- runs on a daemon thread separate from
;;;                 the shared event loop of await-handler). Handler clauses enter the shared loop with Await and from there wait for a coroutine on the listener
;;;                 loop to finish (across). Even if the shared loop or the cooperative scheduler stalls, the listener loop keeps accepting connections
;;;                 (in the 2026-10-02 record job the shared loop stalled for 30 seconds and /healthz did not answer either)
;;;   Loop lag      From the moment it is created, the listener loop sleeps and wakes every EDGE-LAG-TICK-SECONDS, collects the difference between the scheduled time and the wake time (oversleep -- the time the thread
;;;                 waited for CPU plus the time other work on the loop blocked it), and announces one line to stderr per EDGE-LAG-WINDOW-SECONDS window with the tick count, the maximum, and the 99th percentile
;;;                 (edge-lag-line). The pong to a ws ping is also returned by this loop, so the listener-side round-trip lag can be read separately from the CPU wait of an external measuring client
;;;   Probe ports   A request matching the probes of HttpListen (probe-for -- GET/HEAD and a path) is handed to neither the queue nor the scheduler; the listener loop
;;;                 runs the answer Program in its own run on a separate thread (asyncio.to_thread) and answers (probe-answer). It counts one ticket
;;;   Listening     Stand up the aiohttp server on the listener loop (HttpListen). Assign a ticket to each request, line up the event
;;;                 HttpRequestArrived (containing the headers and the sender's address = request.remote) in the queue, wait for a command (with a ticket), and then fire the real I/O -- the caller only fires and does not wait, so
;;;                 a long relay does not stall other requests. Events already in the queue (Arrivals) are taken by the HttpNextRequest clause on the handler clause's thread without waiting
;;;                 (without entering the shared loop -- so that, when reads of waits caused by one write pile up in the queue, they do not make a round trip one at a time;
;;;                 plan 1). When the queue is empty, it leaves in the queue a promise that is completed from outside the scheduler, and the listener loop completes it at the tick that enqueues (without going through the shared
;;;                 loop -- avoiding the cost of the scheduler thread waking the shared loop on every wait)
;;;   Body reading  HttpReadBody reads the body of the ticket's request from request.content as a stream of chunks (the default 1 MiB limit of aiohttp's request.read does not
;;;                 apply -- the only limit is the effect's max-bytes). If the declared Content-Length exceeds the limit it is refused without reading; for a request with no declaration (chunked) or
;;;                 a false one, reading stops and is refused at the tick when the amount read exceeds the limit by even 1 byte. A refused ticket closes its connection after the answer is sent (the rest of the body is not
;;;                 left to be read and discarded by aiohttp's lingering). A body whose declared length is PREFETCH-BYTES or less is read in full by the listener loop before
;;;                 lining up the event, and HttpReadBody answers on the handler clause's side, judging the limit without entering the shared loop (
;;;                 if exceeded, it is the same HttpBodyTooLarge, and the connection is closed after the answer). For a ticket that is HttpForward'ed without reading, the body read ahead is sent
;;;                 to the upstream as is
;;;   Connections   An unused connection is closed after IDLE-CONNECTION-SECONDS (3630 seconds) -- longer than the seconds for which doeff's HTTP client keeps an unused connection
;;;                 (CLIENT-KEEPALIVE-SECONDS, 60 seconds, in _http_handlers_impl.hy) (server cutoff > client limit -- the contract in the
;;;                 header note of that file)
;;;   Sending responses  Send the status and headers of HttpRespond as they are; the body is a byte string or a file range (read length bytes from start in chunks and write them).
;;;                 The handler clause only enqueues the command to the listener loop and returns (without going through the shared loop, and without waiting for a round trip).
;;;                 Commands are handed over in the order enqueued, so they are handed over before a read or close fired later.
;;;                 A command whose ticket is not at the listener (second time, unknown ticket) -- the enqueuer has already returned -- is announced
;;;                 in one line on the listener loop and discarded.
;;;                 A write failure after the peer has already disconnected (the answer was late and the peer's limit came first -- ConnectionError) is not raised to aiohttp; it is announced in one line
;;;                 and counted per listener (raising it makes aiohttp write a traceback for each one -- in the 2026-10-02 record stall, about 2.2KB x
;;;                 265). When the listener is closed, the total of undelivered answers is announced in one line
;;;   HTTP relay    Pass the body through as a stream in both directions. Drop hop-by-hop headers and add X-Forwarded-Proto / X-Forwarded-For. Host stays as the request's
;;;                 value. If the upstream cannot be reached, 502
;;;   ws relay      First connect to the upstream with ws (502 if unreachable), then upgrade the request to ws and mirror frames in both directions. The limit for one frame is the ws-max-bytes of HttpListen.
;;;                 The relay side does not fire pings (autoping is turned off) and mirrors the ping / pong and close status codes between the endpoints as they are. There are two limits for connecting to the upstream,
;;;                 held separately from the HTTP relay (placed on a client only for the ws relay): until connected (name resolution + TCP
;;;                 connection) is WS-UPSTREAM-CONNECT-SECONDS, and the handshake answer after connecting is WS-UPSTREAM-HANDSHAKE-SECONDS -- if either is exceeded,
;;;                 502 without upgrading to ws. There is no limit on reading frames after connecting (a connection that stays silent for long is not cut)
;;;   ws termination  WsAccept upgrades the request to ws (if the handshake shape does not fit, return a refusal response -- 405 if not GET, 426 if there is no
;;;                 Upgrade: websocket, 400 otherwise -- and announce one line). Once upgraded, for each connection stand up a read loop (aiohttp provides the pong to a ping, assembly of fragments, UTF-8 validation, and the
;;;                 per-message limit ws-max-bytes) and a send box + writer task (WsSendText / WsClose only enqueue to the box and do not wait -- one slow connection does not
;;;                 block other connections or the main body). If a message arrives that makes the box's backlog exceed ws-send-max-bytes, that connection is cut on the spot (the box is discarded and the
;;;                 transport is dropped). Sends to a closed connection are silently discarded. Events (WsOpened, WsTextArrived, WsBinaryArrived, WsClosed) are lined up in the same queue as
;;;                 requests, with the monotonic clock of the tick when received put in received-at. The WsSendText clause only enqueues the work of loading the box to the listener loop and
;;;                 returns (without going through the shared loop, and without waiting for a round trip -- the same shape as sending responses; a caller's
;;;                 cycle that sends the same message to as many connections as there are had grown in proportion to the number of sends because of the round-trip wait on each send). Commands are handed over in the order enqueued, so sends to the same ticket enter the
;;;                 box in the order fired, and are handed over before a close fired later (WsClose), a read of the tally (TakeWsSendReport), and a listener close (HttpShutdown)
;;;   Closing       HttpShutdown stops accepting new requests, enqueues close 1000 to every open ws, waits up to drain-seconds for the writers to finish flushing,
;;;                 drops the remaining connections, and folds up the listener. After that HttpNextRequest yields HttpServerClosed
;;;   Send tally    Count the bytes enqueued, flushed, and discarded and the time taken to flush, hand them over with TakeWsSendReport, and reset to 0 (material for the consumer's instruments)
;;; Things decided the same way as in the scripted handler call the judgment defk's of http_server_effects.hy (carries-content for whether an answer carries a body, ws-refusal-status for the
;;; ws refusal status, send-overflows for whether to cut at the send limit, closing-of for the status code and reason announced in WsClosed -- this side's WsClose gives its status code and reason,
;;; and the peer's close gives the peer's status code and reason). The contract test is tests/test_http_server_contract.hy.
;;; Composition: await-handler is needed outside the set.
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "http-server" :role "foundation"})
(import asyncio)
(import collections [deque])
(import collections.abc [Coroutine])
(import math)
(import sys)
(import threading)
(import time)
(import typing [TypeVar])
(import pathlib [Path])
(import aiohttp)
(import aiohttp [web WSMsgType])
(import doeff_core_effects._effects [Await])
(import doeff_core_effects._scheduler [CreateExternalPromise Wait])
(import doeff_core_effects._http_server_effects [HttpAddress HttpServerClosed HttpCommand HttpEvent HttpHeader HttpRequestArrived HttpListen
                                                HttpNextRequest HttpTakeQueued HttpRespond HttpForward WsForward WsAccept WsSendText WsSendTextEach WsClose HttpShutdown HttpStopListening
                                                TakeWsSendReport WsSendReport WsOpened WsTextArrived WsBinaryArrived WsClosed
                                                HttpBodyBytes HttpBodyFileRange HttpNoBody FLUSH-SAMPLES-LIMIT DEFAULT-DRAIN-SECONDS
                                                WS-CLOSE-ABNORMAL HttpReadBody HttpBodyRead HttpBodyTooLarge HttpBodyFailed HttpBodyOutcome
                                                WS-REFUSAL-TEXT WS-CUT-REASON WsCloseFrame carries-content ws-refusal-status
                                                send-overflows closing-of HttpProbe probe-for probe-answer
                                                WsShortcut WsShortcutAnswered WsShortcutFailed ws-shortcut-for ws-shortcut-outcome ws-shortcut-stage])
(import aiohttp.web_protocol [PayloadAccessError])
(import doeff [run])

;; Number of bytes per chunk when sending a file range.
(val FILE-CHUNK-BYTES 262144)
;; Number of bytes per chunk when reading a request body (near the limit, only the remainder is read).
(val BODY-CHUNK-BYTES 262144)
;; Upper limit of the declared length of a body the listener loop reads in full before lining up the event (body reading in the header note -- one chunk).
(val PREFETCH-BYTES BODY-CHUNK-BYTES)

;; Seconds until an unused connection (keep-alive) is closed (connection handling in the header note). Set longer than the seconds the client holds (CLIENT-KEEPALIVE-SECONDS, 60 seconds),
;; so the case where the client writes to a connection the server closed does not normally occur. The value equals the default of aiohttp's AppRunner (RequestHandler's 3630 seconds -- "longer than the
;; intermediate reverse proxy"), so current behavior does not change. It is set by name rather than relying on the default -- aiohttp's default differs by how it is set up (run_app uses 75 seconds) and also changes by version.
(val IDLE-CONNECTION-SECONDS 3630.0)
;; Limits for HTTP relay: the connection limit to the upstream and the limit between reads (seconds -- same as the old nginx proxy_read_timeout 300s).
(val CONNECT-SECONDS 10.0)
(val HTTP-READ-SECONDS 300.0)
;; The two limits when the ws relay connects to the upstream (seconds -- the ws relay in the header note. Held separately from the two above for the HTTP relay).
;;   Until connected   The combined limit of name resolution and the TCP connection. While the upstream is being swapped, the connect SYN is silently dropped and no refusal returns -- give up shorter than the peer's connect
;;                    limit and return 502, so the peer can retry immediately
;;   Handshake answer  After connecting, the limit between reads while waiting for the upstream's 101. Do not cut a slow answer from a connected upstream with the short "until connected" limit
;;                    (if cut, the peer keeps retrying against an upstream with slow answers)
(val WS-UPSTREAM-CONNECT-SECONDS 3.0)
(val WS-UPSTREAM-HANDSHAKE-SECONDS 15.0)
;; Headers not mirrored in the relay (hop-by-hop -- RFC 9110 7.6.1). The ws relay does not mirror handshake headers either (aiohttp builds the handshake to the upstream).
(val HOP-BY-HOP (frozenset #("connection" "keep-alive" "proxy-authenticate" "proxy-authorization" "te" "trailer" "trailers"
                            "transfer-encoding" "upgrade")))
(val WS-HANDSHAKE-PREFIX "sec-websocket-")
;; Close status codes that cannot be sent: no status code (1005) is a normal close (1000); an abnormal close (1006, 1015 -- a disconnect without a close frame) and
;; the peer vanishing without sending a close frame are closed with 1011 (an abnormality on the relay side) -- so the endpoint does not misread it as an "intended disconnect" and stop reconnecting.
(val NO-STATUS-CLOSE 1005)
(val ABNORMAL-CLOSE-CODES (frozenset #(1006 1015)))
(val NORMAL-CLOSE 1000)
(val RELAY-FAILED-CLOSE 1011)

;; Where the single listener loop per process is kept (key EDGE-KEY -> loop), and the lock used when creating it (header note -- do not add threads per listener).
(val EDGE-LOOPS {})
(val EDGE-LOOP-LOCK (threading.Lock))
(val EDGE-KEY "edge")
;; Tick interval and the window seconds for announcing one line, for the listener loop's lag instrument (loop lag in the header note). Running instruments are held as key EDGE-KEY -> the task's future.
(val EDGE-LAG-TICK-SECONDS 0.05)
(val EDGE-LAG-WINDOW-SECONDS 60.0)
(val EDGE-LAG-METERS {})
;; The type of the answer that across carries from the listener loop.
(val Carried (TypeVar "Carried"))


(defn #^ bool upgrade-asked [#^ web.Request request]  ; cannot be a defk: real I/O at the intake that reads the aiohttp request headers
  "To put on the event whether the request asks for an Upgrade to ws."
  (= (.lower (.get request.headers "Upgrade" "")) "websocket"))


(defn #^ frozenset hop-names [#^ str connection]  ; cannot be a defk: real I/O of the relay (building aiohttp headers)
  "To decide the names of headers not to mirror (the fixed hop-by-hop ones plus the headers named by the Connection header -- RFC 9110 7.6.1)."
  (| HOP-BY-HOP (frozenset (gfor token (.split connection ",") :if (.strip token) (.lower (.strip token))))))


(defn #^ list forwarded-headers [#^ web.Request request #^ bool ws]  ; cannot be a defk: real I/O of the relay (building aiohttp headers)
  "To add X-Forwarded-Proto / X-Forwarded-For to the headers passed to the upstream (excluding hop-by-hop and, for ws, handshake headers). The value is a list of (name value) pairs
   (headers with the same name are not dropped even if there are several)."
  (setv hops (hop-names (.get request.headers "Connection" ""))
        kept (lfor [name value] (.items request.headers)
                   :if (not (or (in (.lower name) hops) (and ws (.startswith (.lower name) WS-HANDSHAKE-PREFIX))
                                (in (.lower name) #("x-forwarded-proto" "x-forwarded-for"))))
                   #(name value))
        proto (.get request.headers "X-Forwarded-Proto" request.scheme)
        chain (.get request.headers "X-Forwarded-For" "")
        peer (or request.remote ""))
  (+ kept [#("X-Forwarded-Proto" proto) #("X-Forwarded-For" (if chain (+ chain ", " peer) peer))]))


(defn #^ int sendable-close [#^ (| int None) code]  ; cannot be a defk: real I/O of the ws relay (building close frames)
  "To turn a received close status code into one that can be sent again (no status code is a normal close; abnormal is 1011 -- header note)."
  (cond
    (or (is code None) (= code NO-STATUS-CLOSE)) NORMAL-CLOSE
    (in code ABNORMAL-CLOSE-CODES) RELAY-FAILED-CLOSE
    True code))


(defn :async #^ None pump [#^ (| web.WebSocketResponse aiohttp.ClientWebSocketResponse) source
                           #^ (| web.WebSocketResponse aiohttp.ClientWebSocketResponse) target]  ; cannot be a defk: real I/O of the ws relay (mirrors the aiohttp socket)
  "To mirror frames in one direction: text, bytes, ping, and pong as they are; close is mirrored to the peer together with its status code, and then it ends. It ends if the peer has already closed."
  (while True
    (setv message (await (.receive source)))
    (try
      (match message.type
        WSMsgType.TEXT (await (.send-str target message.data))
        WSMsgType.BINARY (await (.send-bytes target message.data))
        WSMsgType.PING (await (.ping target message.data))
        WSMsgType.PONG (await (.pong target message.data))
        WSMsgType.CLOSE (do (await (.close target :code (sendable-close message.data)
                                           :message (.encode (or message.extra "") "utf-8")))
                            (return None))
        _ (do (await (.close target :code RELAY-FAILED-CLOSE)) (return None)))
      (except [#(ConnectionResetError RuntimeError aiohttp.ClientError)]
        (await (.close source :code RELAY-FAILED-CLOSE))
        (return None)))))


(defn #^ None relay-failed [#^ str line]  ; cannot be a defk: real I/O of the relay (one line in place of an access log)
  "To announce a relay failure in one line on stderr (no access log is emitted, so which port failed can be told from outside)."
  (print (+ "http-server: " line) :file sys.stderr :flush True)
  None)


(defk edge-lag-line [overshoots window-seconds]
  {:pre [(: overshoots tuple) (> (len overshoots) 0) (: window-seconds float)] :post [(: % str)]
   :tags {:context "http-server" :role "judgment"}}
  "To turn the oversleeps (a tuple of seconds) of one window of the listener loop into one line with the tick count, the maximum, and the 99th percentile (the value at the 99% position when sorted -- the
   ceil(0.99 n)-th from the bottom) (loop lag in the header note)."
  (val ordered (sorted overshoots))
  (val p99 (get ordered (- (math.ceil (* 0.99 (len ordered))) 1)))
  (.format "edge-loop lag: window={:.0f}s ticks={} max_ms={:.1f} p99_ms={:.1f}"
           window-seconds (len ordered) (* 1000.0 (get ordered -1)) (* 1000.0 p99)))


(defn :async #^ None meter-edge-lag [#^ float tick-seconds #^ float window-seconds]  ; cannot be a defk: real I/O that sleeps on the listener loop and reads the clock
  "To keep measuring the listener loop's oversleep and announce one line per window (loop lag in the header note). Runs until the listener loop stops."
  (setv overshoots #()
        window-start (time.monotonic))
  (while True
    (setv due (+ (time.monotonic) tick-seconds))
    (await (asyncio.sleep tick-seconds))
    (setv woke (time.monotonic))
    (setv overshoots (+ overshoots #((max 0.0 (- woke due)))))
    (when (>= (- woke window-start) window-seconds)
      (print (+ "http-server: " (run (edge-lag-line overshoots window-seconds))) :file sys.stderr :flush True)
      (setv overshoots #()
            window-start woke))))


(defn #^ int refusal-status [#^ web.Request request]  ; cannot be a defk: real I/O at the intake that reads the aiohttp request headers
  "To decide the refusal status returned to a request that cannot be upgraded to ws (a shape refusal is the same ws-refusal-status as the scripted handler -- 405 if not GET,
   426 if there is no Upgrade: websocket. Even if the shape is right, refusing in the remaining handshake validation gives 400)."
  (or (run (ws-refusal-status request.method (upgrade-asked request))) 400))


(defclass WsPeer []
  "One connection upgraded to ws and its send box (touched only on the listener loop). outbox = a queue of enqueued items (#(\"text\" text enqueued-tick)
   or #(\"close\" #(status-code reason) enqueued-tick)), pending = total bytes of text in the box, closed = nothing is enqueued after this (a close was enqueued, cut, or finished),
   cut = the reason it was cut at the send limit (None = not cut), sent = the close we enqueued (WsCloseFrame), received = the close received from the peer,
   wake = the signal that wakes the writer, writer = the writer's task."

  (defn #^ None __init__ [self #^ str ticket #^ web.Request request #^ web.WebSocketResponse ws]
    (setv self.ticket ticket
          self.request request
          self.ws ws
          self.outbox (deque)
          self.pending 0
          self.closed False
          self.cut None
          #^ (| WsCloseFrame None) self.sent None
          #^ (| WsCloseFrame None) self.received None
          self.wake (asyncio.Event)
          self.writer None)
    None))


(defclass Arrivals []
  "The intake queue (requests, ws events, and close markers). Only the listener loop enqueues (put), and a receiver can take an event already in the queue from any thread without waiting
   (take-now -- without entering the shared loop); if empty, it leaves a promise that is completed from outside the scheduler and is woken
   at the tick when something is enqueued (wake-on-arrival -- through neither the shared loop nor a coroutine of the listener loop). Items can be taken in the order enqueued."

  (defn #^ None __init__ [self]
    ;; ready = events enqueued and not yet taken (deque append and popleft are safe across threads). sleepers = the promises of receivers that saw an empty queue and are waiting
    ;; to be woken. lock = a lock so that "see empty and leave" and "enqueue and wake" do not interrupt each other (so a wake-up is not missed).
    (setv self.ready (deque)
          self.sleepers #()
          self.lock (threading.Lock))
    None)

  (defn #^ None put [self #^ HttpEvent event]
    "To append an event to the end of the queue and wake all receivers waiting on an empty queue (on the listener loop). A woken receiver takes from the queue itself, and
     if another receiver took first, it leaves its promise again (so a wake-up is not lost even if a promise of a cancelled wait is woken)."
    (.append self.ready event)
    (with [_ self.lock]
      (setv waking self.sleepers
            self.sleepers #()))
    (for [promise waking]
      (.complete promise None))
    None)

  (defn #^ None wake-on-arrival [self promise]
    "If the queue is empty, to complete promise (a promise completed from outside the scheduler) with None at the tick when the next item is enqueued (on the receiver's thread). If something was enqueued
     before leaving it, complete it immediately."
    (with [_ self.lock]
      (setv now (bool self.ready))
      (when (not now)
        (setv self.sleepers (+ self.sleepers #(promise)))))
    (when now
      (.complete promise None))
    None)

  (defn #^ (| HttpEvent None) take-now [self]
    "To take the event at the head of the queue without waiting (from any thread -- None if empty)."
    (try
      (.popleft self.ready)
      (except [IndexError]
        None))))


(defclass WebEdge []
  "The aiohttp listener, the per-ticket wait for commands, and the connections upgraded to ws (touched only on the listener loop -- header note. Handler clauses
   pass through across). The session value of the listener's handler."

  (defn #^ None __init__ [self]
    (setv self.address None
          self.ws-max-bytes None
          self.ws-send-max-bytes None
          ;; The intake queue (the listener loop enqueues; handler clauses take without waiting if something is there -- Arrivals).
          self.arrivals (Arrivals)
          ;; There are two relay clients: the HTTP relay (client) and the ws relay (ws-client -- the limits for connecting to the upstream differ).
          self.client None
          self.ws-client None
          self.runner None
          ;; The opened listening port (HttpStopListening closes only the port -- open connections are kept by the runner).
          self.site None
          self.waiting {}
          self.unread {}
          ;; ticket -> the answer for a body the listener loop read ahead (body reading in the header note). Put before the listener loop lines up the event, and the handler clause's
          ;; thread takes it exactly once on a read (one dict pop). Removed from a ticket whose command has been handed over.
          self.prefetched {}
          self.oversized (set)
          self.peers {}
          self.shut None
          self.ws-send-drain None
          ;; Probe ports the handler answers by itself (the probes of HttpListen).
          self.probes #()
          ;; Ports for one ws message that the handler answers on the spot (the ws-shortcuts of HttpListen).
          self.ws-shortcuts #()
          self.count 0
          ;; Number of answers that did not arrive because the peer disconnected first (since this listener started -- not reset to 0).
          self.dropped-answers 0)
    (self.reset-report)
    None)

  (defn #^ None reset-report [self]
    "To reset the send tally to 0 (after handing it over with TakeWsSendReport)."
    (setv self.queued-frames 0
          self.queued-bytes 0
          self.flushed-bytes 0
          self.flush-seconds (deque :maxlen FLUSH-SAMPLES-LIMIT)
          self.dropped-bytes 0
          self.cuts 0)
    None)

  (defn #^ asyncio.AbstractEventLoop edge-loop [self]
    "To detach the listener from stalls of the shared event loop and scheduler, draw the single listener loop per process (if none or closed, create one,
     run it on its own daemon thread, and attach the lag instrument -- header note)."
    (with [EDGE-LOOP-LOCK]
      (setv loop (.get EDGE-LOOPS EDGE-KEY))
      (when (or (is loop None) (.is-closed loop))
        (setv loop (asyncio.new-event-loop))
        (.start (threading.Thread :target loop.run-forever :name "doeff-http-edge" :daemon True))
        (setv (get EDGE-LOOPS EDGE-KEY) loop
              (get EDGE-LAG-METERS EDGE-KEY) (asyncio.run-coroutine-threadsafe
                                               (meter-edge-lag EDGE-LAG-TICK-SECONDS EDGE-LAG-WINDOW-SECONDS) loop)))
      loop))

  (defn :async #^ Carried across [self #^ (get Coroutine #(object object Carried)) coroutine]
    "To run a coroutine on the listener loop from a handler clause (Await -- the shared event loop) and wait for its answer. If the wait is cancelled, the listener
     loop's task is cancelled too."
    (await (asyncio.wrap-future (asyncio.run-coroutine-threadsafe coroutine (self.edge-loop)))))

  (defn :async #^ HttpAddress start [self #^ HttpAddress address #^ int ws-max-bytes #^ int ws-send-max-bytes
                                     #^ (get tuple #(HttpProbe ...)) probes
                                     #^ bool share-port #^ (get tuple #(WsShortcut ...)) ws-shortcuts]
    "To open the listener and answer the bound address (requests arriving after opening are all lined up in the queue except those matching a probe port). share-port = open sharing the same port with
     another listener (SO_REUSEPORT). ws-shortcuts = ports that answer one ws message on the spot (header note)."
    (setv self.address address
          self.ws-max-bytes ws-max-bytes
          self.ws-send-max-bytes ws-send-max-bytes
          self.probes probes
          self.ws-shortcuts ws-shortcuts)
    (setv self.client (aiohttp.ClientSession :auto-decompress False
                                             :timeout (aiohttp.ClientTimeout :total None :sock-connect CONNECT-SECONDS
                                                                             :sock-read HTTP-READ-SECONDS)))
    ;; A client only for the ws relay: aiohttp's ws_connect does not accept a per-request ClientTimeout and connects with the session's value, so to keep the limits
    ;; separate from the HTTP relay the session is separate (the connection pool is also separate, and the ws relay does not reuse unused connections of the HTTP relay but connects each time).
    ;; connect = everything until a connection is obtained (waiting for a free connection slot + name resolution + TCP connection -- sock-connect does not include name resolution).
    ;; sock-read = the limit between reads after the request is written, effective only for the handshake answer (aiohttp removes the read limit on receiving 101).
    ;; total is not set (a connected ws is not cut by time).
    (setv self.ws-client (aiohttp.ClientSession :timeout (aiohttp.ClientTimeout :total None :connect WS-UPSTREAM-CONNECT-SECONDS
                                                                                :sock-read WS-UPSTREAM-HANDSHAKE-SECONDS)))
    (setv app (web.Application))
    (.add-route app.router "*" "/{tail:.*}" self.dispatch)
    (setv self.runner (web.AppRunner app :access-log None :keepalive-timeout IDLE-CONNECTION-SECONDS))
    (await (.setup self.runner))
    (setv self.site (web.TCPSite self.runner self.address.host self.address.port :reuse-port share-port))
    (await (.start self.site))
    (setv bound (get self.runner.addresses 0))
    (HttpAddress :host self.address.host :port (get bound 1)))

  (defn #^ (| HttpEvent None) take-arrival [self]
    "To take the next event already in the intake queue on the handler clause's thread without waiting (without entering the shared loop --
     None if the queue is empty, and the handler clause leaves a promise with wake-on-arrival and waits). After closing, HttpServerClosed (whatever remains in the queue)."
    (when (is-not self.shut None)
      (return (HttpServerClosed :reason self.shut)))
    (setv event (.take-now self.arrivals))
    (if (is event None)
        None
        (self.closed-or event)))

  (defn #^ (get tuple #(HttpEvent ...)) take-queued [self]
    "To take every event already in the intake queue on the handler clause's thread without waiting (HttpTakeQueued -- in received order; an empty tuple if empty). After
     closing, stop after including one HttpServerClosed (later events are not taken)."
    (setv taken #())
    (while True
      (setv event (.take-arrival self))
      (when (is event None)
        (return taken))
      (setv taken (+ taken #(event)))
      (when (isinstance event HttpServerClosed)
        (return taken))))

  (defn #^ HttpEvent closed-or [self #^ HttpEvent event]
    "To hand over a taken event -- after closing, an event taken that is not the close marker is replaced with HttpServerClosed carrying the close reason."
    (if (and (is-not self.shut None) (not (isinstance event HttpServerClosed)))
        (HttpServerClosed :reason self.shut)
        event))

  (defn :async #^ web.StreamResponse dispatch [self #^ web.Request request]
    "To handle one aiohttp request: if it matches a probe port, answer it ourselves on the listener loop; otherwise line it up in the queue (receive)."
    (setv probe (run (probe-for self.probes request.method request.path)))
    (if (is probe None)
        (await (self.receive request))
        (await (self.answer-probe probe))))

  (defn :async #^ web.Response answer-probe [self #^ HttpProbe probe]
    "To answer one request on a probe port without going through the queue or the scheduler (header note). answer runs in its own run on a separate thread, so it does not block the listener
     loop. It uses one ticket, counted the same way as other requests."
    (setv self.count (+ self.count 1))
    (setv answer (await (asyncio.to-thread probe-answer probe)))
    (web.Response :status answer.status :body answer.body :headers (lfor header answer.headers #(header.name header.value))))

  (defn :async #^ None settle [self #^ str ticket #^ HttpCommand command]
    "To hand the main body's command to the ticket's request (handing over to the same ticket twice raises KeyError -- one judgment per request)."
    (self.hand-over ticket command)
    None)

  (defn #^ None hand-over [self #^ str ticket #^ HttpCommand command]
    "To hand the main body's command to the ticket's request and stop letting that ticket's body be read any more (on the listener loop -- a body read
     enqueued before the receiving coroutine wakes also becomes HttpBodyFailed). A body that was read ahead and that nobody took is handed to the receiving coroutine together with the command (the relay sends it). Handing over to the same ticket twice
     raises KeyError."
    (setv waiting (.pop self.waiting ticket))
    (.pop self.unread ticket None)
    (.set-result waiting #(command (.pop self.prefetched ticket None)))
    None)

  (defn #^ None posted [self #^ str ticket #^ HttpCommand command]
    "To hand an enqueued command (post) to its ticket on the listener loop. If the ticket is not at the listener (second time, unknown ticket) or the peer left first and the ticket's wait
     was cancelled, announce one line and discard -- the enqueuer has already returned and there is nowhere to raise to, and raising onto the listener loop becomes a callback traceback
     (sending responses in the header note, #2757)."
    (try
      (self.hand-over ticket command)
      (except [error #(KeyError asyncio.InvalidStateError)]
        (relay-failed (.format "command {} for ticket {} was dropped because it was enqueued for a ticket that is not at the listener (second time or unknown) or whose peer left first: {!r}"
                               (. (type command) __name__) ticket error))))
    None)

  (defn #^ None post [self #^ str ticket #^ HttpCommand command]
    "To only enqueue the main body's command to the listener loop and return (sending an answer -- without going through the shared loop, without waiting for a round trip).
     The listener loop runs in the order enqueued, so this is handed over before the coroutine of an across fired later. The body read ahead is removed here before enqueuing --
     a read fired after the answer does not take the body read ahead, even if it comes before the command is handed over."
    (.pop self.prefetched ticket None)
    (.call-soon-threadsafe (self.edge-loop) self.posted ticket command)
    None)

  (defn :async #^ web.StreamResponse receive [self #^ web.Request request]
    "One aiohttp request: assign a ticket, read ahead a body with a small declaration (body reading in the header note), line up the event, and wait for the main body's command, then fire the real I/O.
     The received time is stamped before the read-ahead."
    (setv received-at (time.monotonic))
    (setv self.count (+ self.count 1))
    (setv ticket (str self.count)
          waiting (.create-future (asyncio.get-running-loop)))
    (setv (get self.waiting ticket) waiting)
    (setv early (await (self.prefetch ticket request)))
    (if (is early None)
        (setv (get self.unread ticket) request)
        (setv (get self.prefetched ticket) early))
    (.put self.arrivals (HttpRequestArrived :ticket ticket :method request.method :path request.path :target request.raw-path
                                            :upgrade (upgrade-asked request)
                                            :headers (tuple (gfor [name value] (.items request.headers) (HttpHeader :name name :value value)))
                                            :received-at received-at
                                            :remote request.remote))
    (setv [command unclaimed] (await waiting))
    ;; The body of a ticket that has received a command is no longer read (hand-over removed it at the tick of handing over). A ticket whose body was refused at the limit closes the connection after the answer is sent.
    (setv cut-off (in ticket self.oversized))
    (.discard self.oversized ticket)
    (match command
      (HttpRespond :status status :headers headers :body body) (await (self.respond request status headers body cut-off))
      (HttpForward :url url) (await (self.relay-http request url unclaimed))
      (WsForward :url url) (await (self.relay-ws request url))
      (WsAccept :ticket accepted) (await (self.terminate-ws request accepted))))

  (defn :async #^ web.StreamResponse terminate-ws [self #^ web.Request request #^ str ticket]
    "To upgrade the ticket's request to ws and terminate it: line up each message as an event from the read loop, leave sending to the writer task, and line up WsClosed when finished."
    (setv ws (web.WebSocketResponse :max-msg-size self.ws-max-bytes))
    (when (not (. (.can-prepare ws request) ok))
      (setv status (refusal-status request))
      (relay-failed (.format "ws handshake of ticket {} did not succeed: wrong shape (status {})" ticket status))
      (return (web.Response :status status :text WS-REFUSAL-TEXT)))
    (try
      (await (.prepare ws request))
      (except [error #(ConnectionResetError RuntimeError web.HTTPException)]
        (relay-failed (.format "ws handshake of ticket {} did not succeed: {!r}" ticket error))
        (return ws)))
    (setv peer (WsPeer ticket request ws))
    (setv (get self.peers ticket) peer)
    (setv peer.writer (asyncio.create-task (self.write-loop peer)))
    (.put self.arrivals (WsOpened :ticket ticket :received-at (time.monotonic)))
    (try
      ;; async for cannot read the peer's close reason (message.extra), so run receive directly and keep the peer's close.
      (while True
        (setv message (await (.receive ws)))
        (match message.type
          WSMsgType.TEXT (await (self.text-arrived ticket message.data))
          WSMsgType.BINARY (.put self.arrivals (WsBinaryArrived :ticket ticket :data message.data :received-at (time.monotonic)))
          WSMsgType.CLOSE (do (setv peer.received (WsCloseFrame :code (int message.data) :reason (or message.extra "")))
                              (break))
          _ (break)))
      (except [#(ConnectionResetError RuntimeError)] None)
      (finally
        (await (self.finish-peer peer))))
    ws)

  (defn :async #^ None text-arrived [self #^ str ticket #^ str text]
    "To line up one ws text message in the queue. For a message matching a port that answers on the spot (ws-shortcut-for), run answer in its own run on a thread outside the listener loop
     (ws-shortcut-outcome), finish enqueuing the answer's text into that connection's box, and then line it up carrying the enqueued text and tick (header note -- the read loop waits for this,
     so the next message of the same connection is lined up after this). If answer fails, announce one line and line it up without enqueuing anything."
    (setv received-at (time.monotonic))
    (setv shortcut (run (ws-shortcut-for self.ws-shortcuts text)))
    (when (is shortcut None)
      (.put self.arrivals (WsTextArrived :ticket ticket :text text :received-at received-at))
      (return None))
    (setv outcome (await (asyncio.to-thread ws-shortcut-outcome shortcut ticket text)))
    ;; Enqueue stage by stage (the two-stage answer in the header note): for a stage with a continuation rest, enqueue and then run rest, likewise on a thread outside the loop.
    (setv sent #()
          carried None
          answered-at None)
    (while (is-not outcome None)
      (match outcome
        (WsShortcutAnswered :texts texts :rest rest :carried value)
          (do (for [one texts]
                (self.send-text ticket one))
              (when texts
                (setv sent (+ sent texts)
                      answered-at (time.monotonic)))
              (setv carried value
                    outcome (if (is rest None) None (await (asyncio.to-thread ws-shortcut-stage rest)))))
        (WsShortcutFailed :reason reason)
          (do (relay-failed (.format "could not answer one ws message of ticket {} on the spot (handing it to the main body): {}" ticket reason))
              (setv carried None
                    outcome None))))
    ;; If there is no answer text, enqueue nothing and line it up in the same shape as a message that did not match (answered-at only when something was enqueued -- header note).
    (.put self.arrivals (WsTextArrived :ticket ticket :text text :received-at received-at :answered sent
                                       :answered-at answered-at :carried carried))
    None)

  (defn :async #^ None finish-peer [self #^ WsPeer peer]
    "To fold up a connection whose read loop has finished: if we enqueued a close, wait for the writer to finish flushing; otherwise discard the box, stop the writer, and line up WsClosed."
    (setv closing (and peer.closed (is peer.cut None)))
    (when (and closing (is-not peer.writer None))
      (await (asyncio.wait #{peer.writer} :timeout (or self.ws-send-drain DEFAULT-DRAIN-SECONDS))))
    (self.seal peer 0)
    (when (is-not peer.writer None)
      (.cancel peer.writer))
    (.pop self.peers peer.ticket None)
    ;; The status code and reason to announce are decided by the same closing-of as the scripted handler (in the order: cut, our close, the peer's close, the status code when lost).
    (setv lost peer.ws.close-code)
    (setv frame (run (closing-of peer.cut peer.sent peer.received (if (is lost None) None (int lost)))))
    (.put self.arrivals (WsClosed :ticket peer.ticket :code frame.code :reason frame.reason :received-at (time.monotonic)))
    None)

  (defn :async #^ None write-loop [self #^ WsPeer peer]
    "The per-connection writer: write the box's messages to the peer in order (counting the flushed tally and time taken), and on an enqueued close send close and finish. If it cannot write, discard the box."
    (while True
      (while (not peer.outbox)
        (when peer.closed
          (return None))
        (.clear peer.wake)
        (await (.wait peer.wake)))
      (setv [kind payload queued-at] (.popleft peer.outbox))
      (match kind
        "text"
          (do (setv size (len (.encode payload "utf-8")))
              (setv peer.pending (- peer.pending size))
              (try
                (await (.send-str peer.ws payload))
                (except [#(ConnectionResetError RuntimeError aiohttp.ClientError)]
                  (self.seal peer size)
                  (return None))
                (except [asyncio.CancelledError]
                  ;; The connection ended mid-write (the read loop cancelled the writer): count the one message taken out as discarded too, then let the cancellation through.
                  (self.seal peer size)
                  (raise)))
              (setv self.flushed-bytes (+ self.flushed-bytes size))
              (.append self.flush-seconds (- (time.monotonic) queued-at)))
        "close"
          (do (try
                (await (.close peer.ws :code (get payload 0) :message (.encode (get payload 1) "utf-8")))
                (except [#(ConnectionResetError RuntimeError aiohttp.ClientError)] None))
              (return None)))))

  (defn #^ None seal [self #^ WsPeer peer #^ int unsent]
    "To close the connection's box and discard its contents (unsent = bytes the writer took out but could not write -- counted as discarded together with the box's contents)."
    (setv peer.closed True)
    (setv dropped (+ peer.pending unsent))
    (setv peer.outbox (deque) peer.pending 0)
    (setv self.dropped-bytes (+ self.dropped-bytes dropped))
    (.set peer.wake)
    None)

  (defn #^ None post-text [self #^ str ticket #^ str text]
    "To only enqueue the main body's send to the listener loop and return (without going through the shared loop, without waiting for a round trip -- ws termination in the header note). The listener loop runs in the order
     enqueued, so sends to the same ticket enter the box in the order fired, and are handed over before the coroutine of an across fired later (close, read of the tally, listener close)."
    (.call-soon-threadsafe (self.edge-loop) self.send-text ticket text)
    None)

  (defn #^ None post-text-each [self #^ (get tuple #(str ...)) tickets #^ str text]
    "To enqueue to the listener loop, only once, the work of sending one text message to the connections of a list of tickets (enters the boxes in ticket order with the same rules as send-text)."
    (.call-soon-threadsafe (self.edge-loop) self.send-text-each tickets text)
    None)

  (defn #^ None send-text-each [self #^ (get tuple #(str ...)) tickets #^ str text]
    "To enqueue one text message into the boxes of a list of tickets (does not wait -- call on the listener loop). The per-ticket rules are the same as send-text."
    (for [ticket tickets]
      (self.send-text ticket text))
    None)

  (defn #^ None send-text [self #^ str ticket #^ str text]
    "To enqueue one text message into the ticket's connection box (does not wait -- call on the listener loop). Closed and unknown tickets are discarded. If it is a message that makes the backlog exceed the limit,
     cut the connection on the spot."
    (setv peer (.get self.peers ticket))
    (when (or (is peer None) peer.closed)
      (return None))
    (setv size (len (.encode text "utf-8"))
          limit self.ws-send-max-bytes)
    (when (is limit None)
      (raise (RuntimeError "WsSendText before HttpListen")))
    ;; Whether to cut is decided by the same send-overflows as the scripted handler (a message exceeding the limit is cut without being enqueued).
    (if (run (send-overflows peer.pending size limit))
        (do (setv self.cuts (+ self.cuts 1)
                  peer.cut WS-CUT-REASON)
            (self.seal peer 0)
            (when peer.request.transport
              (.abort peer.request.transport)))
        (do (.append peer.outbox #("text" text (time.monotonic)))
            (setv peer.pending (+ peer.pending size)
                  self.queued-frames (+ self.queued-frames 1)
                  self.queued-bytes (+ self.queued-bytes size))
            (.set peer.wake)))
    None)

  (defn :async #^ None close-ws [self #^ str ticket #^ int code #^ str reason]
    "To enqueue a close into the ticket's connection (send close after flushing the messages already enqueued -- does not wait)."
    (setv peer (.get self.peers ticket))
    (when (or (is peer None) peer.closed)
      (return None))
    (.append peer.outbox #("close" #(code reason) (time.monotonic)))
    (setv peer.closed True
          peer.sent (WsCloseFrame :code code :reason reason))
    (.set peer.wake)
    None)

  (defn :async #^ None stop-listening [self]
    "To close only the listening port (no new connections are accepted -- open connections and the flow of events are kept by the runner). The second time does nothing."
    (when (is-not self.site None)
      (setv site self.site
            self.site None)
      (await (.stop site)))
    None)

  (defn :async #^ None shutdown [self #^ str reason #^ float drain-seconds #^ int close-code]
    "To close the listener: first close the listening port (no new connections are accepted), enqueue close close-code to every open ws, wait up to drain-seconds
     for the writers to finish flushing, and drop the rest and fold up."
    (setv self.shut reason
          self.ws-send-drain drain-seconds)
    (await (self.stop-listening))
    (setv peers (list (.values self.peers)))
    (for [peer peers]
      (await (self.close-ws peer.ticket close-code reason)))
    (setv writers (lfor peer peers :if (is-not peer.writer None) peer.writer))
    (when writers
      (await (asyncio.wait writers :timeout drain-seconds)))
    (for [peer (list (.values self.peers))]
      (when peer.request.transport
        (.abort peer.request.transport)))
    (when (is-not self.runner None)
      (await (.cleanup self.runner)))
    (for [client #(self.client self.ws-client)]
      (when (is-not client None)
        (await (.close client))))
    (when (> self.dropped-answers 0)
      (relay-failed (.format "closing the listener -- undelivered answers in total: {} (peer closed first)" self.dropped-answers)))
    (.put self.arrivals (HttpServerClosed :reason reason))
    None)

  (defn :async #^ WsSendReport take-report [self]
    "To hand over the send tally and reset it to 0."
    (setv report (WsSendReport :queued-frames self.queued-frames :queued-bytes self.queued-bytes :flushed-bytes self.flushed-bytes
                               :flush-seconds (tuple self.flush-seconds) :dropped-bytes self.dropped-bytes :cuts self.cuts))
    (self.reset-report)
    report)

  (defn :async #^ HttpBodyOutcome read-body [self #^ str ticket #^ int max-bytes]  ; cannot be a defk: real I/O that reads the aiohttp request body (a coroutine on the event loop)
    "To read the ticket's request body as a stream up to the limit (header note -- if the declaration exceeds the limit, refuse without reading; stop and refuse at the tick when the amount read exceeds the limit).
     Only once per ticket: a ticket already read, one that has received a command, or an unknown ticket gets HttpBodyFailed."
    (setv request (.pop self.unread ticket None))
    (when (is request None)
      (return (HttpBodyFailed :reason (.format "the request body of ticket {} cannot be read (unknown ticket, already-read ticket, or ticket after a command was fired)" ticket))))
    (setv declared request.content-length)
    (when (and (is-not declared None) (> declared max-bytes))
      (.add self.oversized ticket)
      (return (HttpBodyTooLarge :declared declared)))
    (await (self.drain-body ticket request max-bytes)))

  (defn :async #^ (| HttpBodyOutcome None) prefetch [self #^ str ticket #^ web.Request request]  ; cannot be a defk: real I/O that reads the aiohttp request body (a coroutine on the event loop)
    "To read in full on the listener loop, before lining up the event, a body whose declared length is PREFETCH-BYTES or less (body reading in the header note). A body with no declaration
     (chunked), a large body, and a ws Upgrade are not read (None -- HttpReadBody reads on the listener loop while watching the limit)."
    (setv declared request.content-length)
    (if (or (is declared None) (> declared PREFETCH-BYTES) (upgrade-asked request))
        None
        (await (self.drain-body ticket request declared))))

  (defn #^ (| HttpBodyOutcome None) take-prefetched [self #^ str ticket #^ int max-bytes]
    "To take once on the handler clause's thread a ticket's body that the listener loop read ahead, and judge it against the limit (None for a ticket not read ahead, already taken, or one whose command
     was handed over). If it exceeds the limit, it is HttpBodyTooLarge (the declared length) and the ticket is added to oversized -- the listener loop runs in the order enqueued, so
     this enters before an answer fired later, and the connection is closed after the answer (header note)."
    (setv early (.pop self.prefetched ticket None))
    (match early
      (HttpBodyRead :data data) (if (> (len data) max-bytes)
                                    (do (.call-soon-threadsafe (self.edge-loop) self.oversized.add ticket)
                                        (HttpBodyTooLarge :declared (len data)))
                                    early)
      _ early))

  (defn :async #^ HttpBodyOutcome drain-body [self #^ str ticket #^ web.Request request #^ int max-bytes]  ; cannot be a defk: real I/O that reads the aiohttp request body (a coroutine on the event loop)
    "To read the ticket's request body in chunks up to max-bytes as a stream (stop and refuse at the tick when the amount read exceeds the limit, and put the ticket into oversized)."
    (setv declared request.content-length)
    (setv chunks [] total 0)
    (try
      (while True
        (setv chunk (await (.read request.content (min BODY-CHUNK-BYTES (- (+ max-bytes 1) total)))))
        (when (not chunk)
          (break))
        (.append chunks chunk)
        (setv total (+ total (len chunk)))
        (when (> total max-bytes)
          (.add self.oversized ticket)
          (return (HttpBodyTooLarge :declared declared))))
      (except [error #(ConnectionError web.RequestPayloadError PayloadAccessError)]
        (return (HttpBodyFailed :reason (.format "could not read the request body of ticket {}: {!r}" ticket error)))))
    (HttpBodyRead :data (.join b"" chunks)))

  (defn :async #^ web.StreamResponse respond [self #^ web.Request request #^ int status #^ (get tuple #(HttpHeader ...)) headers
                                              #^ (| HttpBodyBytes HttpBodyFileRange HttpNoBody) body #^ bool cut-off]
    "To send as is the answer the translation handler decided (a file range is read in chunks and written). cut-off = a ticket whose body was refused at the limit -- close the connection after sending
     (the rest of the body is not read and discarded)."
    (setv response (web.StreamResponse :status status))
    (for [header headers]
      (if (= (.lower header.name) "content-length")
          (setv response.content-length (int header.value))
          (.add response.headers header.name header.value)))
    ;; An answer that carries no body (HEAD, 1xx, 204, 304) does not send the body even if one is passed -- decided by the same carries-content as the scripted handler.
    (setv sent-body (if (run (carries-content request.method status)) body (HttpNoBody)))
    (try
      (match sent-body
        (HttpBodyBytes :data data)
          (do (setv response.content-length (len data))
              (await (.prepare response request))
              (await (.write response data)))
        (HttpBodyFileRange :path path :start start :length length)
          (do (await (.prepare response request))
              (with [handle (open path "rb")]
                (.seek handle start)
                (setv left length)
                (while (> left 0)
                  (setv chunk (.read handle (min left FILE-CHUNK-BYTES)))
                  (when (not chunk) (break))
                  (setv left (- left (len chunk)))
                  (await (.write response chunk)))))
        (HttpNoBody) (await (.prepare response request)))
      (await (.write-eof response))
      (when cut-off
        ;; Close the written answer after the transport has flushed it. Closing on the protocol side means aiohttp does not do lingering for the rest of the body.
        (.force-close request.protocol))
      (except [error ConnectionError]
        ;; The peer disconnected first (sending responses in the header note) -- do not raise to aiohttp; announce in one line and count. The response returned is folded up by aiohttp (a write
        ;; to a closed transport raising ConnectionError is silently accepted by aiohttp).
        (setv self.dropped-answers (+ self.dropped-answers 1))
        (relay-failed (.format "answer to {} {} (status {}) was not delivered because the peer closed first -- number {} on this listener: {}"
                               request.method request.path status self.dropped-answers error))))
    response)

  (defn :async #^ web.StreamResponse relay-http [self #^ web.Request request #^ str url #^ (| HttpBodyOutcome None) unclaimed]
    "To mirror an HTTP request to the upstream as a stream (502 if unreachable). unclaimed = a body the listener loop read ahead and nobody took (body reading
     in the header note -- a body read in full is sent as is)."
    (setv response None)
    (setv data (match unclaimed
                 (HttpBodyRead :data read) read
                 _ (if request.body-exists request.content None)))
    (try
      (with [:async upstream (.request self.client request.method url :headers (forwarded-headers request False)
                                       :data data :allow-redirects False)]
        (setv response (web.StreamResponse :status upstream.status :reason upstream.reason))
        (setv hops (hop-names (.get upstream.headers "Connection" "")))
        (for [[name value] (.items upstream.headers)]
          (when (not-in (.lower name) hops)
            (.add response.headers name value)))
        (await (.prepare response request))
        (for [:async chunk (.iter-any upstream.content)]
          (await (.write response chunk)))
        (await (.write-eof response))
        response)
      (except [error #(aiohttp.ClientError asyncio.TimeoutError)]
        (relay-failed (+ "HTTP relay to upstream " url " was cut: " (repr error)))
        (when (is response None)
          (return (web.Response :status 502 :text (+ "cannot reach the upstream: " url ": " (repr error)))))
        ;; Cut mid-body: drop the connection without sending the end marker (the chunked terminator) -- so the partial body is not accepted as a complete answer.
        (.force-close response)
        (when request.transport
          (.close request.transport))
        response)))

  (defn :async #^ web.StreamResponse relay-ws [self #^ web.Request request #^ str url]
    "To connect a ws request to the upstream and mirror frames in both directions (connect to the upstream first, and if unreachable, 502 without upgrading -- the same when the limit until connected or the limit for the handshake
     answer is exceeded. The limits are held by the ws relay's client -- header note)."
    (setv protocols (lfor p (.split (.get request.headers "Sec-WebSocket-Protocol" "") ",") :if (.strip p) (.strip p)))
    (try
      (setv upstream (await (.ws-connect self.ws-client url :headers (forwarded-headers request True) :autoping False
                                         :max-msg-size self.ws-max-bytes :protocols protocols)))
      (except [error #(aiohttp.ClientError asyncio.TimeoutError)]
        (relay-failed (+ "cannot reach upstream " url " by ws: " (repr error)))
        (return (web.Response :status 502 :text (+ "cannot reach the upstream by ws: " url ": " (repr error))))))
    (setv client (web.WebSocketResponse :autoping False :max-msg-size self.ws-max-bytes
                                        :protocols (if upstream.protocol [upstream.protocol] [])))
    (try
      (await (.prepare client request))
      (await (asyncio.gather (pump client upstream) (pump upstream client)))
      (finally
        ;; Even if one direction's mirroring exits with an exception, close both sides and end the partner's mirroring.
        (await (.close upstream :code RELAY-FAILED-CLOSE))
        (await (.close client :code RELAY-FAILED-CLOSE))))
    client))


(defk read-on-edge [edge ticket max-bytes]
  {:pre [(: edge WebEdge) (: ticket str) (: max-bytes int)] :post [(: % HttpBodyOutcome)]}
  "To answer a read of a ticket's body: a body the listener loop read ahead is judged and answered on the handler clause's thread (without entering the shared loop -- body
   reading in the header note), and a ticket not read ahead is read from the shared loop on the listener loop (across)."
  (match (.take-prefetched edge ticket max-bytes)
    None (do (<- outcome HttpBodyOutcome (Await (.across edge (.read-body edge ticket max-bytes))))
             outcome)
    early early))


(defk next-on-edge [edge]
  {:pre [(: edge WebEdge)] :post [(: % HttpEvent)]}
  "To hand the receiver the next event: if it is already in the intake queue, take it on the handler clause's thread without waiting (without entering the shared loop -- listening in the header note);
   if empty, leave in the queue a promise that is completed from outside the scheduler and wait until something is enqueued, then take again on waking (without going back and forth between the shared loop and the listener loop
   during the wait -- avoiding the cost of the scheduler thread waking the shared loop on every wait)."
  (match (.take-arrival edge)
    None (do (<- promise (CreateExternalPromise))
             (.wake-on-arrival edge.arrivals promise)
             (<- (Wait promise.future))
             (<- arrival HttpEvent (next-on-edge edge))
             arrival)
    event event))


(defhandler aiohttp-http-server
  "Answer the HTTP listening effects (HttpListen, HttpNextRequest, HttpRespond, ws send/receive, and so on) with aiohttp's real I/O (header note).
   await-handler is needed outside the set."
  ;; Real I/O of the listening effects (header note). The listener object is created only once as the session value. Clauses enter the shared event loop of await-handler with Await and
  ;; from there wait on a coroutine of the listener loop with across (await-handler is needed outside the set).
  (session val edge (WebEdge))
  (HttpListen [address ws-max-bytes ws-send-max-bytes probes share-port ws-shortcuts]
    (<- bound HttpAddress (Await (.across edge (.start edge address ws-max-bytes ws-send-max-bytes probes share-port ws-shortcuts))))
    (resume bound))
  (HttpNextRequest []
    (<- arrival HttpEvent (next-on-edge edge))
    (resume arrival))
  (HttpTakeQueued []
    ;; Take only the events already in the queue, without entering the shared loop (listening in the header note -- does not wait).
    (resume (.take-queued edge)))
  (HttpReadBody [ticket max-bytes]
    (<- outcome HttpBodyOutcome (read-on-edge edge ticket max-bytes))
    (resume outcome))
  (HttpRespond [ticket status headers body]
    ;; The answer is only enqueued to the listener loop (without going through the shared loop -- sending responses in the header note).
    (.post edge ticket effect)
    (resume None))
  (HttpForward [ticket url]
    (<- (Await (.across edge (.settle edge ticket effect))))
    (resume None))
  (WsForward [ticket url]
    (<- (Await (.across edge (.settle edge ticket effect))))
    (resume None))
  (WsAccept [ticket]
    (<- (Await (.across edge (.settle edge ticket effect))))
    (resume None))
  (WsSendText [ticket text]
    ;; The send is only enqueued to the listener loop (without going through the shared loop -- ws termination in the header note).
    (.post-text edge ticket text)
    (resume None))
  (WsSendTextEach [tickets text]
    ;; A send to a list of tickets is also enqueued to the listener loop only once (the per-ticket enqueuing is the same as send-text).
    (.post-text-each edge tickets text)
    (resume None))
  (WsClose [ticket code reason]
    (<- (Await (.across edge (.close-ws edge ticket code reason))))
    (resume None))
  (HttpStopListening []
    (<- (Await (.across edge (.stop-listening edge))))
    (resume None))
  (HttpShutdown [reason drain-seconds close-code]
    (<- (Await (.across edge (.shutdown edge reason drain-seconds close-code))))
    (resume None))
  (TakeWsSendReport []
    (<- report WsSendReport (Await (.across edge (.take-report edge))))
    (resume report)))
