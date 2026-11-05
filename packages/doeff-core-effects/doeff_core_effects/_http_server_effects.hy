;;; Effects of a generic HTTP listener (the receiving side) (consumer = the receiving end of the #795 webapp). Paired with the sending side's HttpRequest
;;; (http_effects.hy), a foundation vocabulary with no business terms. The answerer is swapped per mechanism:
;;;   aiohttp-http-server   the real listener (aiohttp_http_server.hy - aiohttp is a dependency of the extra `http-server`)
;;;   scripted-http-server  no I/O - turns a script's sequence of requests into events and records the commands received (scripted_http_server.hy)
;;;
;;;   HttpListen       Opens the listener (address, ws per-message limit, ws per-connection send limit, port sharing). Answer = the address actually bound, HttpAddress
;;;                    (pass port 0 to bind a free port - the bound port is learned from this answer). share-port = open sharing the same port with another listener
;;;                    (SO_REUSEPORT - for stacking old and new listeners during a swap. They overlap only when both open it as true, and
;;;                    the kernel distributes new connections to the open listeners). The default false is as before - a port in use cannot be opened
;;;   HttpNextRequest  The next event. Answer = HttpEvent: HttpRequestArrived (ticket, method, path, target, headers, whether an Upgrade to ws was requested, the sender's address),
;;;                    ws events (WsOpened, WsTextArrived, WsBinaryArrived, WsClosed - only for tickets raised to ws with WsAccept), HttpServerClosed
;;;   HttpTakeQueued   Takes all events already received and queued, without waiting. Answer = a tuple of HttpEvent (in received order; empty if nothing queued).
;;;                    Events taken are no longer delivered by HttpNextRequest (taking ahead in the same stream). After closing, like HttpNextRequest, it stops
;;;                    with one HttpServerClosed included. For consumers that reorder so urgent ones among queued events are processed first (
;;;                    behind 36 messages from 6 screens loading at once, a request to open a conversation on another connection waited about 1 second)
;;;   HttpRespond     Sends status, headers, and body (HttpBodyBytes / HttpBodyFileRange / HttpNoBody) to the ticket's request. Answer = None
;;;   HttpForward      Relays the ticket's request to url over HTTP (body streamed both ways, hop-by-hop headers dropped, X-Forwarded-Proto / -For added,
;;;                    502 if unreachable). Answer = None
;;;   WsForward        Relays the ticket's request to url over ws (frames copied both ways, ping / pong and the close status code passed through, 502 if unreachable). Answer = None
;;;   WsAccept         Raises the ticket's request to ws and terminates it here (handshake checks and the 101 are the answerer's - if it does not succeed, it returns a refusal response and logs one line,
;;;                    and no event comes out). When raising finishes, the event WsOpened, then each message of that connection as WsTextArrived / WsBinaryArrived, and when the connection ends
;;;                    WsClosed (status code and reason) line up in the same stream of HttpNextRequest. Answering ping with pong, assembling fragments, checking masks, checking UTF-8, and
;;;                    cutting a connection that exceeds the per-message limit (ws-max-bytes of HttpListen) are the answerer's. Answer = None
;;; Exactly one command (HttpRespond, HttpForward, WsForward, WsAccept) per ticket. A command is only fired and not waited for (a long relay does not stall other requests).
;;; The following two can be fired any number of times to a connection raised with WsAccept (both are only fired, not waited for - the answerer queues them in the per-connection send box, and the writing is the answerer's side):
;;;   WsSendText       Sends one text message. A send to a closed or unknown ticket is silently dropped. A connection whose box backlog exceeds ws-send-max-bytes of HttpListen
;;;                    (a peer that does not read) is cut on the spot (the box contents are dropped, and it is named in the reason of the event WsClosed). Answer = None
;;;   WsSendTextEach   Sends the same one text message each to the connections of a list of tickets (one request - the same rules as firing WsSendText per ticket: in ticket order, closed or unknown tickets are
;;;                    silently dropped, and a connection whose box backlog exceeds the limit is cut on the spot. The real answerer puts it into every ticket's box in one queuing pass. So that a consumer sending the same message
;;;                    to as many connections as there are makes the fixed cost per send once per message). Answer = None
;;;   WsClose          Closes with a status code and reason (flushes the message queued in the box and then sends close). Answer = None
;;; For the listener as a whole:
;;;   HttpStopListening Closes only the listener - accepts no new connections (if another listener shares the port, new connections go there).
;;;                    Open connections (ws and requests waiting for commands) and the event stream continue as they are. Fired by the retiring side in a swap, before it closes its clients
;;;                    (so the clients' reconnect after being closed does not hit the retiring side). Answer = None
;;;   HttpShutdown     Closes the listener - first closes the listener's mouth and accepts no new connections (the same as HttpStopListening - the reconnect right after a client receives the close
;;;                    hits another listener sharing the port), sends close close-code (default 1000 - the retiring side in a swap uses
;;;                    1001 going away) to all open ws, and closes after flushing the send boxes for up to drain-seconds. After that
;;;                    HttpNextRequest gives HttpServerClosed(reason). Answer = None
;;;   TakeWsSendReport Reads and resets to 0 the accounting of the send boxes (bytes queued, flushed, and dropped since the last read, and the time taken to flush - WsSendReport).
;;;                    Material for the consumer to accumulate into its own meters; the answerer does not know the consumer's meters
;;; received-at of an event = the monotonic clock seconds at the beat when the answerer received that event (the same scale as time.monotonic - the origin of the consumer's wait meter). For a scripted answerer that
;;; has no clock, the value is whatever the script's author put (None if not put).
;;; remote of a request = the sender's address (IP spelling). The real answerer puts the connection's peer (aiohttp's request.remote), and None when it cannot be determined.
;;; For a scripted answerer, it is whatever the script's author put in HttpRequestArrived (None if not put).
;;; The effect that reads a request body (for the record service, the POST body is the main thing):
;;;   HttpReadBody     Reads the ticket's request body up to max-bytes. Answer = HttpBodyOutcome:
;;;                      HttpBodyRead(data)          the whole body (b"" for no body)
;;;                      HttpBodyTooLarge(declared)  the body exceeds max-bytes - declared = the Content-Length the request declared (None if
;;;                                                  there is no declaration, as with chunked). If the declaration exceeds the limit it is refused without reading a single byte. A request with no declaration or
;;;                                                  a false declaration is read while streaming and the read is stopped and refused at the beat it exceeds the limit by even 1 byte
;;;                                                  (regardless of whether Content-Length exists, at most max-bytes + 1 byte is held in memory)
;;;                      HttpBodyFailed(reason)      could not be read (the peer cut in the middle, the body shape is broken, an unknown ticket, a ticket after a
;;;                                                  command was fired)
;;;                    Fire at most once per ticket, before the command (HttpRespond, etc.). The caller sends the answer after a refusal (413, etc.) with HttpRespond, and
;;;                    the answerer closes the connection after sending that answer (it does not read and discard the rest of the body). Not fired for a ticket passed to HttpForward (the relay copies the body by
;;;                    streaming, so reading first would leave no body to copy).
;;; The state of a file is read with StatPath of file_effects.hy (not held here).
;;; The probe entry that the answerer answers by itself (answers liveness questions even when the main stream has stopped):
;;;   probes of HttpListen = a list of HttpProbe. A GET or HEAD request whose path (excluding the query) equals a probe's path is not made an event (it does not reach HttpNextRequest);
;;;   the answerer runs that HttpProbe's answer (a Program whose answer is HttpProbeAnswer) in its own run and answers. A ticket is used the same
;;;   way as for other requests (so the order of tickets in arrival order matches between the real one and the script).
;;;   answer must be a closed Program: the answerer runs it without adding handlers, so the handlers it needs (state, the latest-value store, clock ...) are wrapped inside
;;;   answer. It uses neither the main run's state (session values) nor Await's shared event loop. If answer fails with an exception, 500 and a reason sentence
;;;   (probe-failure).
;;;   The real answerer stands the listener on a loop in a thread separate from await-handler's shared event loop, and runs answer on yet another thread -
;;;   so the probe answers even if the shared loop and the cooperative scheduler stop. The scripted answerer runs answer inside the HttpNextRequest that the request reaches in the event sequence, answers,
;;;   and leaves it in the record (HttpServed).
;;;   What to answer (how to divide liveness = the process is there and ability to work = the main stream is moving, and the thresholds) is written by the caller in answer - this has no business
;;;   terms.
;;; The ws message entry that the answerer answers on the spot (so that an urgent message's answer is not
;;; kept waiting even while the consumer's processing loop holds one item for several hundred ms):
;;;   ws-shortcuts of HttpListen = a list of WsShortcut. If one text message of a connection raised to ws contains the marker (a string) of a WsShortcut (ws-shortcut-for -
;;;   the first one), the answerer runs answer (a function that receives the ticket and the one message's text and returns a closed Program answering a tuple of texts to send) in its own run
;;;   (ws-shortcut-outcome - like probe-answer, it touches neither the main run's handlers nor state), and queues the answer texts in order into that connection's send box. After that,
;;;   it lines up that message in the queue as the event WsTextArrived as before, with the field answered carrying the list of texts queued and answered-at carrying the monotonic clock at the beat queuing finished
;;;   (a message that did not match, and a message for which answer returned an empty tuple, have answered empty and answered-at None). The main body receives the same message and
;;;   changes its own state (whether to queue the answer again is the main body's decision).
;;;   The answer can also be split into two stages (first send "loading", then read the record and send the rows): if the answer Program
;;;   answers with WsShortcutAhead (texts and the closed Program rest for the continuation), the answerer queues texts and then runs rest in the same run of its own, and receives rest's answer
;;;   in the same form (if rest is WsShortcutAhead again, it continues). If the final answer is WsShortcutCarried (texts and the value carried), it queues texts and
;;;   puts the value in the field carried of that one message's event and passes it to the main body (so the main body folds what the answerer read into its own state and does not read it again). answered is the list of texts queued
;;;   across all stages, and answered-at is the beat when the last stage finished queuing.
;;;   The order of messages per connection does not change: the answerer does not read the next message of that connection until it finishes queuing the answer (all stages) of the matched message. If answer failed or the answer's type
;;;   is wrong, nothing is queued (the answerer logs one line of the reason for WsShortcutFailed) and the message lines up with answered left empty - the main body answers as usual. If a continuation stage
;;;   failed, the texts queued so far are put in answered and carried is left empty.
;;;   The real answerer runs answer on a thread outside the listener's loop (it answers even if the main scheduler has stopped). The scripted answerer runs answer when the head of the queue is a matching
;;;   message, records the texts sent in the record (WsTextSent), and then passes the event.
;;; What the two answerers must decide the same way is decided by both calling the judgment defks here: carries-content (whether the answer carries a body - HEAD, 1xx, 204, 304 are not sent),
;;; ws-refusal-status (the status of the WsAccept refusal), send-overflows (whether to cut at the send limit), closing-of (the status code and reason named in WsClosed),
;;; probe-for (whether a request hits a probe entry), probe-failure (the answer of a probe whose answer failed), ws-shortcut-for (whether a message hits the on-the-spot answer entry).
;;; The two also run a probe's answer with the same probe-answer, and a ws entry's answer with the same ws-shortcut-outcome (both are entries called from outside a Program).
;;; That the two have the same properties is verified on both answerers by the contract test tests/test_http_server_contract.hy.
;;;
;;; Script vocabulary (not in the real listener): HttpScript, ScriptedUpstream, ScriptedBody = the script; HttpServed = the commands received and the answer the terminal receives;
;;; WsTextSent / WsCloseSent = one message and the close sent to a ws connection; ReadHttpServed = the effect that reads the record (for tests and scenarios to peek);
;;; AppendHttpScript = the effect that appends events behind a running script (the scenario's counterpart delivers them at the beat the time arrives - only scripted-http-server answers).
(require doeff-hy.macros [defk deff val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import doeff [EffectBase Program run])

;; Default limit (bytes) of one ws message (one frame in a relay).
(val DEFAULT-WS-MAX-BYTES (* 1024 1024))
;; Default limit (bytes) of each ws connection's send box - the box of a peer that does not read is allowed to pile up to this and then cut.
(val DEFAULT-WS-SEND-MAX-BYTES (* 16 1024 1024))
;; Default upper limit (seconds) of waiting to flush the send boxes when closing the listener.
(val DEFAULT-DRAIN-SECONDS 1.0)
;; ws close status codes (RFC 6455 7.4.1) - what the answerer names.
(val WS-CLOSE-NORMAL 1000)
(val WS-CLOSE-ABNORMAL 1006)
;; Body of the refusal answer for a request that cannot be raised to ws (the WsAccept refusal - what the answerer names).
(val WS-REFUSAL-TEXT "A WebSocket Upgrade (GET, Upgrade: websocket, Sec-WebSocket-Key) is required")
;; Reason in WsClosed for a connection cut at the send limit (what the answerer names).
(val WS-CUT-REASON "send box exceeded the limit (peer that does not read)")


(defrecord HttpAddress
  "The listener's address."
  (#^ str host)
  (#^ int port))


(defrecord HttpHeader
  "One HTTP header (held as a list so that the same name appearing several times is not dropped)."
  (#^ str name)
  (#^ str value))


(defrecord HttpRequestArrived
  "One request that arrived: ticket, method, path (without query), target (with query), the list of headers, whether an Upgrade to ws was requested, the beat received (header comment),
   remote = the sender's address (IP spelling - None when the answerer cannot determine it)."
  (#^ str ticket)
  (#^ str method)
  (#^ str path)
  (#^ str target)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ bool upgrade)
  (setv #^ (| float None) received-at None
        #^ (| str None) remote None))


(defrecord WsOpened
  "A ticket's request was raised to ws with WsAccept (after this, messages of this ticket arrive)."
  (#^ str ticket)
  (setv #^ (| float None) received-at None))


(defrecord WsTextArrived
  "A text message arrived on the connection of a ticket raised to ws (fragments are already assembled, UTF-8 is already checked). answered = the list of texts queued into that connection's send box before this message by the answerer's on-the-spot entry (ws-shortcuts)
   (empty if it did not match), answered-at = the monotonic clock seconds at the beat queuing finished (None if it did not match -
   header comment), carried = the value that the final stage of the answerer's entry handed to the main body (WsShortcutCarried - None if not handed)."
  (#^ str ticket)
  (#^ str text)
  (setv #^ (| float None) received-at None
        #^ (get tuple #(str ...)) answered #()
        #^ (| float None) answered-at None
        #^ object carried None))


(defrecord WsBinaryArrived
  "A binary message arrived on the connection of a ticket raised to ws (the consumer decides whether to accept it - close with WsClose if not)."
  (#^ str ticket)
  (#^ bytes data)
  (setv #^ (| float None) received-at None))


(defrecord WsClosed
  "The connection of a ticket raised to ws ended: code = the close status code (the peer's close, our WsClose, or 1006 when it dropped), reason = the reason sentence
   (when cut at the send limit, it is named so). After this, sends to this ticket are dropped."
  (#^ str ticket)
  (#^ int code)
  (#^ str reason)
  (setv #^ (| float None) received-at None))


(defrecord HttpServerClosed
  "The listener closed (HttpShutdown, the script ran out, etc.)."
  (#^ str reason))


;; The union of answers of HttpNextRequest.
(val WsEvent (| WsOpened WsTextArrived WsBinaryArrived WsClosed))
(val HttpEvent (| HttpRequestArrived WsOpened WsTextArrived WsBinaryArrived WsClosed HttpServerClosed))


(defrecord WsSendReport
  "Accounting of the send boxes (since the last read with TakeWsSendReport): queued-frames / queued-bytes = the number and bytes of messages queued into the boxes, flushed-bytes = bytes
   flushed to the peer, flush-seconds = the seconds from queuing to flushing for each flushed message (up to FLUSH-SAMPLES-LIMIT, newest first - the byte accounting is complete),
   dropped-bytes = bytes dropped (cut at the send limit, or left in the box when the connection ended), cuts = the number of connections cut at the send limit."
  (#^ int queued-frames)
  (#^ int queued-bytes)
  (#^ int flushed-bytes)
  (#^ (get tuple #(float ...)) flush-seconds)
  (#^ int dropped-bytes)
  (#^ int cuts))


;; Upper limit of timing samples one WsSendReport holds (so they do not pile up without bound while unread).
(val FLUSH-SAMPLES-LIMIT 4096)


(defrecord HttpBodyBytes
  "Body = a fixed byte string."
  (#^ bytes data))


(defrecord HttpBodyFileRange
  "Body = length bytes from start of the file at path (the answerer reads and sends it - a large file is not loaded into memory)."
  (#^ str path)
  (#^ int start)
  (#^ int length))


(defrecord HttpNoBody
  "No body (304, HEAD, 302).")


(val HttpBody (| HttpBodyBytes HttpBodyFileRange HttpNoBody))


(defrecord HttpBodyRead
  "Answer of HttpReadBody: the whole request body (b\"\" for no body)."
  (#^ bytes data))


(defrecord HttpBodyTooLarge
  "Answer of HttpReadBody: the body exceeded the limit (declared = the Content-Length the request declared, None if there was no declaration - header comment)."
  (#^ (| int None) declared))


(defrecord HttpBodyFailed
  "Answer of HttpReadBody: the body could not be read (reason = a reason sentence - header comment)."
  (#^ str reason))


;; The union of answers of HttpReadBody.
(val HttpBodyOutcome (| HttpBodyRead HttpBodyTooLarge HttpBodyFailed))


(defrecord HttpProbeAnswer
  "Answer of the probe entry (header comment): status, headers, and the body's byte string (the answer to HEAD does not send the body - carries-content)."
  (#^ int status)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ bytes body))


(defrecord HttpProbe
  "One probe entry that the answerer answers by itself (header comment): path = the path that matches (excluding the query - only GET and HEAD match), answer = a closed Program whose answer is
   HttpProbeAnswer (the answerer runs it in its own run for each request)."
  (#^ str path)
  (#^ (get Program #(HttpProbeAnswer object)) answer))


(defrecord WsShortcut
  "One ws message entry that the answerer answers on the spot (header comment): marker = matches if a message's text contains it, answer = a function that receives the ticket and the one message's text and returns a closed Program
   answering a tuple of texts to send (the answerer runs it in its own run for each message)."
  (#^ str marker)
  (#^ (get Callable #([str str] Program)) answer))


(defrecord WsShortcutAhead
  "An intermediate answer of a ws entry's answer (the two-stage answer in the header comment): texts = the list of texts to queue now, in order, into that connection, rest = the closed continuation Program that the answerer runs, likewise in its own run,
   after queuing (its answer has the same shape as answer - a tuple of texts, WsShortcutAhead, WsShortcutCarried)."
  (#^ (get tuple #(str ...)) texts)
  (#^ Program rest))


(defrecord WsShortcutCarried
  "Among the final answers of a ws entry's answer, the one that hands a value to the main body (header comment): texts = the list of texts to queue, in order, into that connection, carried = the value to put in the field carried of the event
   WsTextArrived for that message and hand to the main body (None is not handed)."
  (#^ (get tuple #(str ...)) texts)
  (#^ object carried))


(defrecord WsShortcutAnswered
  "One stage's answer of a ws entry's answer: texts = the list of texts to queue, in order, into that connection (nothing is queued if empty), rest = the continuation stage to run after queuing (None = the end),
   carried = the value the final stage hands to the main body (header comment)."
  (#^ (get tuple #(str ...)) texts)
  (setv #^ (| Program None) rest None
        #^ object carried None))


(defrecord WsShortcutFailed
  "A ws entry's answer failed or its answer had the wrong type (reason = a reason sentence - the answerer logs one line, and nothing is queued)."
  (#^ str reason))


(defclass [(dataclass :frozen True)] HttpListen [(get EffectBase HttpAddress)]
  "Opens the listener (header comment). probes = the list of probe entries the answerer answers by itself, ws-shortcuts = the list of ws message entries the answerer answers on the spot. Answer =
   the bound address, HttpAddress."
  (#^ HttpAddress address)
  (setv #^ int ws-max-bytes DEFAULT-WS-MAX-BYTES
        #^ int ws-send-max-bytes DEFAULT-WS-SEND-MAX-BYTES
        #^ (get tuple #(HttpProbe ...)) probes #()
        #^ bool share-port False
        #^ (get tuple #(WsShortcut ...)) ws-shortcuts #()))


(defclass [(dataclass :frozen True)] HttpNextRequest [(get EffectBase HttpEvent)]
  "Receives one next event (header comment).")


(defclass [(dataclass :frozen True)] HttpTakeQueued [(get EffectBase (get tuple #(HttpEvent ...)))]
  "Takes all events already received and queued, without waiting (header comment).")


(defclass [(dataclass :frozen True)] HttpRespond [(get EffectBase None)]
  "Sends status, headers, and body to the ticket's request (header comment)."
  (#^ str ticket)
  (#^ int status)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ HttpBody body))


(defclass [(dataclass :frozen True)] HttpReadBody [(get EffectBase HttpBodyOutcome)]
  "Reads the ticket's request body up to max-bytes (header comment). Answer = HttpBodyOutcome."
  (#^ str ticket)
  (#^ int max-bytes))


(defclass [(dataclass :frozen True)] HttpForward [(get EffectBase None)]
  "Relays the ticket's request to url over HTTP (header comment)."
  (#^ str ticket)
  (#^ str url))


(defclass [(dataclass :frozen True)] WsForward [(get EffectBase None)]
  "Relays the ticket's request to url over ws (header comment)."
  (#^ str ticket)
  (#^ str url))


(defclass [(dataclass :frozen True)] WsAccept [(get EffectBase None)]
  "Raises the ticket's request to ws and terminates it here (header comment)."
  (#^ str ticket))


(defclass [(dataclass :frozen True)] WsSendText [(get EffectBase None)]
  "Sends one text message to the connection of a ticket raised to ws (header comment)."
  (#^ str ticket)
  (#^ str text))


(defclass [(dataclass :frozen True)] WsSendTextEach [(get EffectBase None)]
  "Sends the same one text message each, in one request, to the connections of a list of tickets raised to ws (header comment)."
  (#^ (get tuple #(str ...)) tickets)
  (#^ str text))


(defclass [(dataclass :frozen True)] WsClose [(get EffectBase None)]
  "Closes the connection of a ticket raised to ws with a status code and reason (header comment)."
  (#^ str ticket)
  (#^ int code)
  (#^ str reason))


(defclass [(dataclass :frozen True)] HttpStopListening [(get EffectBase None)]
  "Closes only the listener - accepts no new connections, while open connections and the event stream continue (header comment).")


(defclass [(dataclass :frozen True)] HttpShutdown [(get EffectBase None)]
  "Closes the listener (header comment). reason = the reason of HttpServerClosed from then on, drain-seconds = the upper limit in seconds of waiting to flush the send boxes, close-code =
   the close status code sent to open ws."
  (#^ str reason)
  (setv #^ float drain-seconds DEFAULT-DRAIN-SECONDS
        #^ int close-code WS-CLOSE-NORMAL))


(defclass [(dataclass :frozen True)] TakeWsSendReport [(get EffectBase WsSendReport)]
  "Reads the accounting of the send boxes and resets it to 0 (header comment). Answer = WsSendReport.")


;; The union of commands to a ticket's request (the type by which the answerer and the script's record receive one command).
(val HttpCommand (| HttpRespond HttpForward WsForward WsAccept))


;; --- Judgments the answerers call in common (the real aiohttp-http-server and the scripted scripted-http-server decide with the same functions) --------------------------------

(defk carries-content [method status]
  {:pre [(: method str) (: status int)] :post [(: % bool)] :tags {:context "http-server" :role "judgment"}}
  "Whether the answer of status to a request of method carries a body (RFC 9110 6.4.1 - answers to HEAD and 1xx, 204, 304 do not send a body). An answer that does not carry one
   is not sent even if a body is passed to HttpRespond (sending it would be read as the head of the next answer on the same connection)."
  (not (or (= method "HEAD") (< status 200) (in status #(204 304)))))


(defk ws-refusal-status [method upgrade]
  {:pre [(: method str) (: upgrade bool)] :post [(: % (| int None))] :tags {:context "http-server" :role "judgment"}}
  "The refusal status for a request shape that cannot be raised to ws with WsAccept (405 if not GET, 426 if no Upgrade: websocket). None = the shape can be raised
   (the real answerer refuses with 400 if the rest of the handshake checks - Sec-WebSocket-Key, etc. - fail)."
  (cond
    (!= method "GET") 405
    (not upgrade) 426
    True None))


(defk send-overflows [held size limit]
  {:pre [(: held int) (: size int) (: limit int)] :post [(: % bool)] :tags {:context "http-server" :role "judgment"}}
  "Whether queuing a message of size bytes into a send box that already has held bytes piled up would exceed the limit (if so, the connection is cut without queuing that message -
   header comment of WsSendText)."
  (> (+ held size) limit))


(defrecord WsCloseFrame
  "The status code and reason of a ws close (the close we sent, the close we received from the peer, or what is named when the connection ended)."
  (#^ int code)
  (#^ str reason))


(defk closing-of [cut sent received lost]
  {:pre [(: cut (| str None)) (: sent (| WsCloseFrame None)) (: received (| WsCloseFrame None)) (: lost (| int None))]
   :post [(: % WsCloseFrame)] :tags {:context "http-server" :role "judgment"}}
  "To decide the status code and reason named in WsClosed when a ws connection ends: 1006 if cut at the send limit (cut = the reason for the cut); if we closed with WsClose
   (sent), that status code and reason; if the peer closed (received), the peer's status code and reason; if none of these, the status code for a dropped connection (lost - if none, 1006)."
  (cond
    (is-not cut None) (WsCloseFrame :code WS-CLOSE-ABNORMAL :reason cut)
    (is-not sent None) sent
    (is-not received None) received
    True (WsCloseFrame :code (if (is lost None) WS-CLOSE-ABNORMAL lost) :reason "")))


(defk probe-for [probes method path]
  {:pre [(: probes (of tuple HttpProbe ...)) (: method str) (: path str)] :post [(: % (| HttpProbe None))] :tags {:context "http-server" :role "judgment"}}
  "To decide whether the answerer answers a request by itself (the probe entry in the header comment): for a GET or HEAD whose path (excluding the query) equals the path of any of probes,
   that HttpProbe, otherwise None (passed to the main body as an event)."
  (if (in method #("GET" "HEAD"))
      (next (gfor probe probes :if (= probe.path path) probe) None)
      None))


(defk probe-failure [path reason]
  {:pre [(: path str) (: reason str)] :post [(: % HttpProbeAnswer)] :tags {:context "http-server" :role "judgment"}}
  "To decide the answer when a probe's answer failed (500 and a reason sentence - so that a probe that cannot produce an answer is not read as healthy)."
  (HttpProbeAnswer :status 500 :headers #((HttpHeader :name "Content-Type" :value "text/plain; charset=utf-8"))
                   :body (.encode (.format "probe for {} could not produce an answer: {}" path reason) "utf-8")))


(deff probe-answer [probe]  ; cannot be a defk: entry that the answerer calls outside the main body's Program (real = a thread outside the listener's loop; script = a handler clause) and runs a closed answer in its own run
  {:pre [(: probe HttpProbe)] :post [(: % HttpProbeAnswer)] :tags {:context "http-server" :role "foundation"}}
  "To produce the answer for one probe-entry request without touching the main body's run (scheduler, session values, Await's shared event loop) (header comment): runs answer in
   its own run, and if it failed or the answer's type is wrong, probe-failure's 500. The two answerers answer with this same function."
  (try
    (setv answer (run probe.answer))
    (except [error Exception]
      (setv answer error)))
  (if (isinstance answer HttpProbeAnswer)
      answer
      (run (probe-failure probe.path (repr answer)))))


(defk ws-shortcut-for [shortcuts text]
  {:pre [(: shortcuts (of tuple WsShortcut ...)) (: text str)] :post [(: % (| WsShortcut None))] :tags {:context "http-server" :role "judgment"}}
  "To decide whether the answerer answers one ws text message on the spot (the ws entry in the header comment): the first WsShortcut whose marker the text contains, or None if none contains it
   (it is only passed to the main body as an event)."
  (next (gfor shortcut shortcuts :if (in shortcut.marker text) shortcut) None))


(deff ws-shortcut-outcome [shortcut ticket text]  ; cannot be a defk: entry that the answerer calls outside the main body's Program (real = a thread outside the listener's loop; script = a handler clause) and runs a closed answer in its own run
  {:pre [(: shortcut WsShortcut) (: ticket str) (: text str)] :post [(: % (| WsShortcutAnswered WsShortcutFailed))]
   :tags {:context "http-server" :role "foundation"}}
  "To produce the answer texts for one message that hit a ws entry without touching the main body's run (scheduler, session values, Await's shared event loop) (header comment): runs the answer
   Program in its own run and reads its first stage's answer with ws-shortcut-stage. The two answerers answer with this same function."
  (ws-shortcut-stage (shortcut.answer ticket text)))


(deff ws-shortcut-stage [program]  ; cannot be a defk: like ws-shortcut-outcome, an entry where the answerer runs a closed stage in its own run outside the main body's Program
  {:pre [(: program Program)] :post [(: % (| WsShortcutAnswered WsShortcutFailed))] :tags {:context "http-server" :role "foundation"}}
  "To run one stage of a ws entry's answer (the answer Program, or the previous stage's rest) in its own run and read its answer (the two-stage answer in the header comment): for a tuple of texts it is
   a final stage; for WsShortcutAhead, a stage with continuation rest; for WsShortcutCarried, a WsShortcutAnswered of a final stage with value carried; if it failed or the type was
   wrong, WsShortcutFailed. The two answerers answer with this same function for each stage."
  (try
    (setv answer (run program))
    (except [error Exception]
      (return (WsShortcutFailed :reason (+ (. (type error) __name__) ": " (str error))))))
  (setv texts (if (isinstance answer (| WsShortcutAhead WsShortcutCarried)) answer.texts answer))
  (when (not (and (isinstance texts tuple) (all (gfor one texts (isinstance one str)))))
    (return (WsShortcutFailed :reason (+ "answer's answer is not a tuple of texts: " (repr answer)))))
  (cond (isinstance answer WsShortcutAhead) (WsShortcutAnswered :texts texts :rest answer.rest)
        (isinstance answer WsShortcutCarried) (WsShortcutAnswered :texts texts :carried answer.carried)
        True (WsShortcutAnswered :texts texts)))


;; --- Script vocabulary (scripted-http-server) -----------------------------------------------------------------------------

(defrecord ScriptedUpstream
  "The script's answer for one relay target: base = a relay that reached a URL starting with this prefix receives this answer, http-status = the status of the HTTP relay's answer,
   accepts-ws = accepts a ws relay (502 if it does not)."
  (#^ str base)
  (#^ int http-status)
  (#^ bool accepts-ws))


(defrecord ScriptedBody
  "The script for one ticket's request body: data = the byte string the peer sends, failed = a reason such as the peer cutting in the middle of reading (None = readable). The declared length
   (Content-Length) is put by the script's author in the request's headers (headers of HttpRequestArrived) - if not put, it is the same as chunked, with no declaration. A ticket with no body in
   the script has no body (b\"\")."
  (#^ str ticket)
  (#^ bytes data)
  (setv #^ (| str None) failed None))


(defrecord HttpScript
  "The script of scripted-http-server: the list of events that arrive (requests, and the ws events of tickets raised with WsAccept), the answers per relay target, the tickets of peers that do not read
   (stalled - sends to those tickets keep piling up in the box and are cut if they exceed the send limit), and the request bodies (bodies - the source of HttpReadBody's answers)."
  (#^ (get tuple #((| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed) ...)) arrivals)
  (setv #^ (get tuple #(ScriptedUpstream ...)) upstreams #()
        #^ (get frozenset str) stalled (frozenset)
        #^ (get tuple #(ScriptedBody ...)) bodies #()))


(defrecord HttpServed
  "One command received and the answer the terminal receives (status, the body's string - for a file range the contents, for a relay the script's answer)."
  (#^ str ticket)
  (#^ HttpCommand command)
  (#^ int status)
  (#^ str body))


(defrecord WsTextSent
  "One text message sent to the connection of a ticket raised to ws (the script's record)."
  (#^ str ticket)
  (#^ str text))


(defrecord WsCloseSent
  "A close sent to the connection of a ticket raised to ws (the script's record - the close 1000 of WsClose and HttpShutdown)."
  (#^ str ticket)
  (#^ int code)
  (#^ str reason))


(defclass [(dataclass :frozen True)] ReadHttpServed [(get EffectBase (get tuple #((| HttpServed WsTextSent WsCloseSent) ...)))]
  "Reads the commands the scripted listener received and the ws records it sent (a tuple in received order of HttpServed, WsTextSent, and WsCloseSent) (header comment).")


(defclass [(dataclass :frozen True)] AppendHttpScript [(get EffectBase None)]
  "Appends arrivals behind the running script's event list (header comment). bodies = the scripts of the bodies of the requests added. Answer = None."
  (#^ (get tuple #((| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed) ...)) arrivals)
  (setv #^ (get tuple #(ScriptedBody ...)) bodies #()))
