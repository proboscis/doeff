;;; The no-I/O answerer scripted-http-server of the generic HTTP listener effect (http_server_effects.hy) (the ws termination is
;;; #811 change 3a). It opens no real socket, and answers from a script (HttpScript -- the sequence of events that arrive, the answers per forwarding target, and the tickets of peers that do not read). It knows no business:
;;; the script's contents are passed in by the caller.
;;;
;;;   HttpListen       Does nothing (treated as opened). Answer = the given address as is (port 0 stays 0 -- the script does not bind a port). Remembers the send limit, the ws mouths, and
;;;                    the probe mouths. Sharing the port (share-port) does nothing since the script has no port
;;;   HttpStopListening From then on, discards the script's new requests (HttpRequestArrived -- including those that hit a probe mouth) without passing them to the body (the same as a real listener
;;;                    not accepting new connections after closing its mouth). Events of opened tickets keep being passed
;;;   HttpNextRequest  Passes the script's events in order, and HttpServerClosed when exhausted. The WsOpened of a ticket raised with WsAccept, and the WsClosed of a ticket
;;;                    ended by WsClose or by a cut at the send limit, are inserted at the head of the queue on that beat (the same order as the events a real listener emits immediately). After HttpShutdown,
;;;                    HttpServerClosed (with that reason) no matter what remains in the queue. A request that hits a probe mouth (probe-for) is not passed; here, the same as the real one,
;;;                    it is answered with probe-answer (answer in its own run -- touching neither the handler nor the state of the body's run), recorded, and it moves on to the next event.
;;;                    The script has no threads, so the shape where the body's flow does not make probes wait is expressed as "does not reach the body and answers without waiting for the body's commands".
;;;                    If a one-text message of an opened ticket hits a mouth that answers on the spot (ws-shortcuts of HttpListen -- ws-shortcut-for), the same as the real one,
;;;                    ws-shortcut-outcome treats the answer text as sent to that ticket (the same decision as WsSendText -- script-sent), and passes
;;;                    the message with the accumulated text attached (answered-at stays the script's received-at). A message whose answer fell is passed as is, sending nothing
;;;   HttpTakeQueued   Takes all the events currently in the script's queue (including those added by AppendHttpScript) the same way as HttpNextRequest (script-take -- discards,
;;;                    probe answers, bookkeeping). Even if the queue is exhausted it does not claim to be closed (the next HttpNextRequest does). After HttpShutdown, a single
;;;                    HttpServerClosed with the close reason
;;;   HttpReadBody     Answers from the script's body (ScriptedBody): if the declared length (Content-Length in the request head) exceeds the limit, HttpBodyTooLarge without reading;
;;;                    if the body exceeds the limit, HttpBodyTooLarge (None if there is no declaration); if failed is present, HttpBodyFailed; otherwise HttpBodyRead (b"" for a ticket whose script
;;;                    has no body). Only once per ticket -- a ticket already read, one that received a command, or an unknown ticket is HttpBodyFailed (the same as the real one)
;;;   HttpRespond      Records the received command. The body the terminal receives = a byte string is read as UTF-8 (unreadable bytes are replaced), and a file range is
;;;                    read and cut out via the file system effect (ReadBytes -- the outer file answerer, mostly memory-file-handler). The body of an answer that carries no body
;;;                    (carries-content -- HEAD, 1xx, 204, 304) is empty
;;;   HttpForward      Treated as answered with the http-status of the script's forwarding target (longest match at the head of the url). 502 if none matches
;;;   WsForward        101 if the script's forwarding target accepts ws, 502 if it does not or none matches
;;;   WsAccept         Treated as raised with 101 and recorded. For a request of a shape that cannot be raised to ws (ws-refusal-status -- 405 for non-GET, 426 without Upgrade)
;;;                    a refusal answer is recorded and it is not raised (no ws events come out)
;;;   WsSendText       For a raised ticket, records one message (WsTextSent -- the peer reads at once: counted as the accumulated bytes flushed, taking 0 seconds). A ticket of a peer that does not read
;;;                    (the script's stalled) keeps accumulating in the box. A message that would exceed the send limit if added to the box's accumulation (send-overflows) is not accumulated and the connection is cut
;;;                    (the accumulation is counted as discarded -- WsClosed 1006 and the cut reason). Closed and unknown tickets are discarded
;;;   WsClose          For a raised ticket, records the close (WsCloseSent), discards the box's accumulation, and inserts WsClosed (the same status code and reason -- closing-of) at the head of the queue
;;;   HttpShutdown     Records close close-code (default 1000) for every open ticket, and from then on HttpServerClosed
;;;   TakeWsSendReport Reads the send tally (WsSendReport) and resets it to 0
;;;   ReadHttpServed   Record of received commands and sent ws messages (a tuple of HttpServed, WsTextSent, WsCloseSent)
;;;   AppendHttpScript Adds to the end of the script's event queue (the scenario's counterpart delivers them on the beat when their time comes). Also adds the script for the body of the added requests
;;; What is decided the same as the real answerer calls the judgment defk of http_server_effects.hy (the contract test is tests/test_http_server_contract.hy).
;;; Commands are fired at the ticket of a request that was delivered (a command to a ticket not delivered is a KeyError -- the same as the real answerer).
;;; Order: put the file answerer outside this handler. The place that holds the session's values (the state of doeff_core_effects) is needed further outside.
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "http-server" :role "foundation"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import doeff_core_effects._http_server_effects [HttpListen HttpNextRequest HttpTakeQueued HttpRespond HttpForward WsForward WsAccept WsSendText WsSendTextEach WsClose
                                                HttpShutdown HttpStopListening TakeWsSendReport WsSendReport ReadHttpServed AppendHttpScript HttpServed
                                                WsTextSent WsCloseSent WsOpened WsClosed HttpServerClosed HttpScript ScriptedUpstream
                                                HttpBodyBytes HttpBodyFileRange HttpNoBody DEFAULT-WS-SEND-MAX-BYTES FLUSH-SAMPLES-LIMIT
                                                HttpReadBody HttpBodyRead HttpBodyTooLarge HttpBodyFailed
                                                HttpBodyOutcome HttpRequestArrived ScriptedBody WS-CUT-REASON WS-REFUSAL-TEXT WsCloseFrame
                                                carries-content ws-refusal-status send-overflows closing-of HttpProbe HttpProbeAnswer
                                                probe-for probe-answer WsTextArrived WsBinaryArrived HttpHeader
                                                WsShortcut WsShortcutAnswered WsShortcutFailed ws-shortcut-for ws-shortcut-outcome ws-shortcut-stage])
(import doeff_core_effects._file_effects [ReadBytes FileFailed])

(val CLOSED-REASON "the script's request queue is exhausted")
(val EMPTY-REPORT (WsSendReport :queued-frames 0 :queued-bytes 0 :flushed-bytes 0 :flush-seconds #() :dropped-bytes 0 :cuts 0))


(defk upstream-for [script url]
  {:pre [(: script HttpScript) (: url str)] :post [(: % (| ScriptedUpstream None))]}
  "To choose the script's forwarding target that receives a forwarded url (longest match at the head -- None if none = unreachable)."
  (val hits (sorted (gfor u script.upstreams :if (.startswith url (.rstrip u.base "/")) u) :key (fn [u] (len u.base))))
  (if hits (get hits -1) None))


(defk body-text [body]
  {:pre [(: body (| HttpBodyBytes HttpBodyFileRange HttpNoBody))] :post [(: % str)]}
  "To turn the sent body into the string the terminal receives (a file range is read via the file system effect -- if unreadable, a reason text)."
  (match body
    (HttpBodyBytes :data data) (.decode data "utf-8" :errors "replace")
    (HttpBodyFileRange :path path :start start :length length)
      (do (<- read (| bytes FileFailed) (ReadBytes path))
          (if (isinstance read FileFailed)
              (+ "cannot read file: " read.detail)
              (.decode (cut read start (+ start length)) "utf-8" :errors "replace")))
    (HttpNoBody) ""))


(defk served-of [script command arrival]
  {:pre [(: script HttpScript) (: command (| HttpRespond HttpForward WsForward WsAccept)) (: arrival HttpRequestArrived)]
   :post [(: % HttpServed)]}
  "To turn a received command into the answer the terminal receives (the same dispatch as a real listener -- arrival = the delivered request of the command's ticket). The body of an answer that carries no body
   (carries-content -- HEAD, 1xx, 204, 304) is empty; WsAccept for a request of a shape that cannot be raised to ws is a refusal answer (ws-refusal-status)."
  (match command
    (HttpRespond :ticket ticket :status status :body body)
      (do (<- carried bool (carries-content arrival.method status))
          (<- text str (if carried (body-text body) (body-text (HttpNoBody))))
          (HttpServed :ticket ticket :command command :status status :body text))
    (HttpForward :ticket ticket :url url)
      (do (<- upstream (| ScriptedUpstream None) (upstream-for script url))
          (if (is upstream None)
              (HttpServed :ticket ticket :command command :status 502 :body (+ "Does not reach the forwarding target: " url))
              (HttpServed :ticket ticket :command command :status upstream.http-status :body (+ "Script's forwarding target " upstream.base))))
    (WsForward :ticket ticket :url url)
      (do (<- upstream (| ScriptedUpstream None) (upstream-for script url))
          (if (and (is-not upstream None) upstream.accepts-ws)
              (HttpServed :ticket ticket :command command :status 101 :body (+ "ws forwarding " upstream.base))
              (HttpServed :ticket ticket :command command :status 502 :body (+ "Does not reach the forwarding target via ws: " url))))
    (WsAccept :ticket ticket)
      (do (<- refusal (| int None) (ws-refusal-status arrival.method arrival.upgrade))
          (if (is refusal None)
              (HttpServed :ticket ticket :command command :status 101 :body "ws was terminated here")
              (HttpServed :ticket ticket :command command :status refusal :body WS-REFUSAL-TEXT)))))


(defk tally-queued [tally size]
  {:pre [(: tally WsSendReport) (: size int)] :post [(: % WsSendReport)]}
  "To add the tally of one message of size bytes accumulated in the box."
  (WsSendReport :queued-frames (+ tally.queued-frames 1) :queued-bytes (+ tally.queued-bytes size) :flushed-bytes tally.flushed-bytes
                :flush-seconds tally.flush-seconds :dropped-bytes tally.dropped-bytes :cuts tally.cuts))


(defk tally-flushed [tally size]
  {:pre [(: tally WsSendReport) (: size int)] :post [(: % WsSendReport)]}
  "To add the tally of one message of size bytes in the box flushed to the peer (the script's peer reads at once -- taking 0 seconds. The sample is from the newest up to the limit)."
  (WsSendReport :queued-frames tally.queued-frames :queued-bytes tally.queued-bytes :flushed-bytes (+ tally.flushed-bytes size)
                :flush-seconds (cut (+ tally.flush-seconds #(0.0)) (- FLUSH-SAMPLES-LIMIT) None)
                :dropped-bytes tally.dropped-bytes :cuts tally.cuts))


(defk tally-dropped [tally size cut]
  {:pre [(: tally WsSendReport) (: size int) (: cut bool)] :post [(: % WsSendReport)]}
  "To add the tally of size bytes of the box discarded (and, if cut = a connection cut at the send limit, the number of cuts)."
  (WsSendReport :queued-frames tally.queued-frames :queued-bytes tally.queued-bytes :flushed-bytes tally.flushed-bytes
                :flush-seconds tally.flush-seconds :dropped-bytes (+ tally.dropped-bytes size) :cuts (+ tally.cuts (if cut 1 0))))


(defk without-ticket [backlog ticket]
  {:tp [V] :pre [(: backlog (of dict str V)) (: ticket str)] :post [(: % (of dict str V))]}
  "To remove a ticket's box accumulation from the table."
  (dfor [t n] (.items backlog) :if (!= t ticket) t n))


(defk declared-length [headers]
  {:pre [(: headers (of tuple HttpHeader ...))] :post [(: % (| int None))]}
  "To read the body length declared in the request head (Content-Length) (None if absent or not a number -- the same as chunked, no declaration)."
  (val said (next (gfor h headers :if (= (.lower h.name) "content-length") (.strip h.value)) None))
  (if (and (is-not said None) (.isdigit said)) (int said) None))


(defk scripted-body-outcome [declared body max-bytes]
  {:pre [(: declared (| int None)) (: body (| ScriptedBody None)) (: max-bytes int)] :post [(: % HttpBodyOutcome)]}
  "To turn one script body into the answer of HttpReadBody (the same order as the real one: refuse without reading if the declaration exceeds the limit -> failure during reading -> refuse if the amount read
   exceeds the limit -> read successfully)."
  (val data (if (is body None) b"" body.data))
  (cond
    (and (is-not declared None) (> declared max-bytes)) (HttpBodyTooLarge :declared declared)
    (and (is-not body None) (is-not body.failed None)) (HttpBodyFailed :reason body.failed)
    (> (len data) max-bytes) (HttpBodyTooLarge :declared declared)
    True (HttpBodyRead :data data)))


(defrecord ScriptSend
  "The session values after one text message is sent to one script ticket: the record, the opened tickets, the box accumulation, the send tally, and the queue of events not yet delivered."
  (#^ (get tuple #(object ...)) served)
  (#^ (get frozenset str) opened)
  (#^ (get dict #(str int)) backlog)
  (#^ WsSendReport tally)
  (#^ (get tuple #(object ...)) pending))


(defk script-sent [ticket text limit stalled served opened backlog tally pending]
  {:pre [(: ticket str) (: text str) (: limit int) (: stalled (of frozenset str)) (: served (of tuple object ...))
         (: opened (of frozenset str)) (: backlog (of dict str int))
         (: tally WsSendReport) (: pending (of tuple object ...))]
   :post [(: % ScriptSend)] :tags {:context "http-server" :role "foundation"}}
  "To treat one text message as sent to a script ticket (the same decision for WsSendText / WsSendTextEach per ticket and for the one message of a ws mouth's answer -- WsSendText in the header comment):
   closed and unknown tickets are discarded; a message exceeding the limit is not accumulated and the connection is cut (the box's accumulation is discarded and WsClosed is inserted at the head of the queue); a peer that does not read accumulates in the box;
   otherwise it is recorded (the peer reads at once)."
  (val size (len (.encode text "utf-8")))
  (val held (.get backlog ticket 0))
  (<- overflows bool (send-overflows held size limit))
  (cond
    (not-in ticket opened) (ScriptSend :served served :opened opened :backlog backlog :tally tally :pending pending)
    overflows
      (do (<- cut-tally WsSendReport (tally-dropped tally held True))
          (<- rest dict (without-ticket backlog ticket))
          (<- frame WsCloseFrame (closing-of WS-CUT-REASON None None None))
          (ScriptSend :served served :opened (- opened #{ticket}) :backlog rest :tally cut-tally
                      :pending (+ #((WsClosed :ticket ticket :code frame.code :reason frame.reason)) pending)))
    (in ticket stalled)
      (do (<- queued WsSendReport (tally-queued tally size))
          (ScriptSend :served served :opened opened :backlog (| backlog {ticket (+ held size)}) :tally queued :pending pending))
    True
      (do (<- queued WsSendReport (tally-queued tally size))
          (<- flushed WsSendReport (tally-flushed queued size))
          (ScriptSend :served (+ served #((WsTextSent :ticket ticket :text text))) :opened opened :backlog backlog :tally flushed
                      :pending pending))))


(defk bodies-by-ticket [bodies]
  {:pre [(: bodies (of tuple ScriptedBody ...))] :post [(: % (of dict str ScriptedBody))]}
  "To turn the body scripts into a table that can be looked up by ticket."
  (dfor b bodies b.ticket b))


(defrecord ProbeHit
  "Of the script's next events, the request that hit a probe mouth that the listener answers itself, and the mouth that was hit."
  (#^ HttpRequestArrived arrival)
  (#^ HttpProbe probe))


(defk probe-hit [probes event]
  {:pre [(: probes (of tuple HttpProbe ...)) (: event (| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed WsOpened))] :post [(: % (| ProbeHit None))]
   :tags {:context "http-server" :role "judgment"}}
  "To decide whether the script's next event is a request to a probe mouth that the listener answers itself (None if not hit -- ws events are also passed to the body)."
  (if (isinstance event HttpRequestArrived)
      (do (<- probe (| HttpProbe None) (probe-for probes event.method event.path))
          (if (is probe None) None (ProbeHit :arrival event :probe probe)))
      None))


(defk probe-served [arrival answer]
  {:pre [(: arrival HttpRequestArrived) (: answer HttpProbeAnswer)] :post [(: % HttpServed)] :tags {:context "http-server" :role "judgment"}}
  "To turn a probe mouth's answer into a record of the answer the terminal receives (as with the real one, the body of a HEAD answer is empty -- carries-content)."
  (<- carried bool (carries-content arrival.method answer.status))
  (HttpServed :ticket arrival.ticket
              :command (HttpRespond :ticket arrival.ticket :status answer.status :headers answer.headers
                                    :body (HttpBodyBytes :data answer.body))
              :status answer.status
              :body (if carried (.decode answer.body "utf-8" :errors "replace") "")))


(defrecord ScriptTake
  "The result of taking one event from the script's queue: the event to pass to the body (None if the queue is exhausted), and the session values after taking (the queue, the record, the opened tickets, the send
   tally, the box accumulation, the tickets whose bodies can be read, and the delivered requests)."
  (#^ (| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed WsOpened None) event)
  (#^ (get tuple #(object ...)) pending)
  (#^ (get tuple #(object ...)) served)
  (#^ frozenset opened)
  (#^ WsSendReport tally)
  (#^ dict backlog)
  (#^ dict readable)
  (#^ dict arrived))


(defk script-take [mouths shortcuts limit stalled listening pending served opened tally backlog readable arrived]
  {:pre [(: mouths (of tuple HttpProbe ...)) (: shortcuts (of tuple WsShortcut ...)) (: limit int) (: stalled frozenset) (: listening bool)
         (: pending tuple) (: served tuple) (: opened frozenset) (: tally WsSendReport) (: backlog dict) (: readable dict) (: arrived dict)]
   :post [(: % ScriptTake)] :tags {:context "http-server" :role "foundation"}}
  "To take the event at the head of the script's queue once (the single way of taking for HttpNextRequest and HttpTakeQueued): after only the listener is closed, discard new requests;
   a request that hits a probe mouth is not passed to the body but answered and recorded here; attach the head event's bookkeeping (discard the box accumulation of closed tickets, remember the tickets whose bodies can be read and
   the delivered requests). If a one-text message of an opened ticket hits a mouth that answers on the spot (shortcuts), the same as the real one, ws-shortcut-outcome sends the answer text to the ticket
   (script-sent -- send limit limit, non-reading peer stalled), and passes the message with the accumulated text attached (answered-at stays the script's received-at -- the script has no
   clock). If the queue is exhausted, the event is None."
  (var rest (if listening pending (tuple (gfor e pending :if (not (isinstance e HttpRequestArrived)) e))))
  (var record served)
  (var answering True)
  (while (and answering rest)
    (<- hit (| ProbeHit None) (probe-hit mouths (get rest 0)))
    (if (is hit None)
        (:= answering False)
        (do (<- answer HttpServed (probe-served hit.arrival (probe-answer hit.probe)))
            (:= record (+ record #(answer)))
            (:= rest (cut rest 1 None)))))
  (when (not rest)
    (return (ScriptTake :event None :pending rest :served record :opened opened :tally tally :backlog backlog :readable readable
                        :arrived arrived)))
  (var head (get rest 0))
  (var later (cut rest 1 None))
  (var still-open opened)
  (var counted tally)
  (var boxes backlog)
  (var reading readable)
  (var handed arrived)
  (when (and (isinstance head WsTextArrived) (in head.ticket opened))
    (<- shortcut (| WsShortcut None) (ws-shortcut-for shortcuts head.text))
    (when (is-not shortcut None)
      ;; Send stage by stage (the same as the real one -- a stage that has a continuation rest is sent, then rest runs; a stage that fell is passed with the text sent so far).
      (var outcome (ws-shortcut-outcome shortcut head.ticket head.text))
      (var answered #())
      (var carried None)
      (while (isinstance outcome WsShortcutAnswered)
        (for [one outcome.texts]
          (<- sent ScriptSend (script-sent head.ticket one limit stalled record still-open boxes counted later))
          (:= record sent.served)
          (:= still-open sent.opened)
          (:= boxes sent.backlog)
          (:= counted sent.tally)
          (:= later sent.pending))
        (:= answered (+ answered outcome.texts))
        (:= carried outcome.carried)
        (:= outcome (if (is outcome.rest None) None (ws-shortcut-stage outcome.rest))))
      (when (isinstance outcome WsShortcutFailed)
        (:= carried None))
      ;; If there is no answer text, nothing is sent, and it is passed in the same shape as a message that did not hit (the same as the real one -- answered only when accumulated).
      (when (or answered (is-not carried None))
        (:= head (WsTextArrived :ticket head.ticket :text head.text :received-at head.received-at :answered answered
                                :answered-at (if answered head.received-at None) :carried carried)))))
  (when (isinstance head WsClosed)
    ;; A ticket whose peer closed: subsequent sends are discarded; the box accumulation goes to the discarded tally.
    (:= still-open (- still-open #{head.ticket}))
    (<- dropped WsSendReport (tally-dropped counted (.get boxes head.ticket 0) False))
    (:= counted dropped)
    (<- kept dict (without-ticket boxes head.ticket))
    (:= boxes kept))
  (when (isinstance head HttpRequestArrived)
    (<- declared (| int None) (declared-length head.headers))
    (:= reading (| readable {head.ticket declared}))
    (:= handed (| arrived {head.ticket head})))
  (ScriptTake :event head :pending later :served record :opened still-open :tally counted :backlog boxes :readable reading
              :arrived handed))


(defhandler scripted-http-server [#^ HttpScript script]
  "Answers the HTTP listener effects without I/O from the caller's script HttpScript (the sequence of events that arrive, the answers of forwarding targets, peers that do not read) (header comment)."
  ;; The script's listener (header comment). pending = events not yet delivered, served = the sequence of records of received commands and sent ws messages, opened = tickets raised to ws and open,
  ;; backlog = the box accumulation (bytes) of tickets of peers that do not read, limit = the send limit (remembered by HttpListen), tally = the send tally, closed = the reason of HttpShutdown
  ;; (None = open), body-table = ticket -> body script, readable = tickets whose bodies can still be read -> declared length (the delivered requests' tickets go in; tickets that were read or received a command
  ;; are removed), arrived = delivered requests (ticket -> HttpRequestArrived -- the shape of a command's answer is decided by the request's method and Upgrade), mouths = the probe mouths answered
  ;; by itself (remembered by HttpListen) (all are session values).
  ;; Why script stays an argument: script is a frozen script (the sequence of events, the answers of forwarding targets, and peers that do not read) assembled by the caller, and there is no counterpart to swap it outside the set.
  (session var pending script.arrivals)
  (session var body-table (dfor b script.bodies b.ticket b))
  (session var readable {})
  (session var arrived {})
  (session var served #())
  (session var opened (frozenset))
  (session var backlog {})
  (session var limit DEFAULT-WS-SEND-MAX-BYTES)
  (session var tally EMPTY-REPORT)
  (session var closed None)
  (session var mouths #())
  ;; The mouth for one ws message answered on the spot (remembered by HttpListen).
  (session var shortcuts #())
  ;; Whether new requests are accepted (false after HttpStopListening).
  (session var listening True)
  (HttpListen [address ws-max-bytes ws-send-max-bytes probes share-port ws-shortcuts]
    (:= limit ws-send-max-bytes)
    (:= mouths probes)
    (:= shortcuts ws-shortcuts)
    (resume address))
  (HttpStopListening []
    (:= listening False)
    (resume None))
  (HttpNextRequest []
    ;; After closing, the close reason no matter what remains in the queue. Otherwise take one from the head of the queue (script-take -- includes discarding new requests after only the listener is closed, and the probe
    ;; mouth's answers; header comment).
    (if (is-not closed None)
        (resume (HttpServerClosed :reason closed))
        (do (<- took ScriptTake (script-take mouths shortcuts limit script.stalled listening pending served opened tally backlog readable arrived))
            (:= pending took.pending)
            (:= served took.served)
            (:= opened took.opened)
            (:= tally took.tally)
            (:= backlog took.backlog)
            (:= readable took.readable)
            (:= arrived took.arrived)
            (resume (if (is took.event None) (HttpServerClosed :reason CLOSED-REASON) took.event)))))
  (HttpTakeQueued []
    ;; Take all the events currently in the script's queue (no waiting; even if the queue is exhausted, it does not claim to be closed -- the next HttpNextRequest does). After closing, a single close reason.
    (var taken #())
    (var more (is closed None))
    (while more
      (<- took ScriptTake (script-take mouths shortcuts limit script.stalled listening pending served opened tally backlog readable arrived))
      (:= pending took.pending)
      (:= served took.served)
      (:= opened took.opened)
      (:= tally took.tally)
      (:= backlog took.backlog)
      (:= readable took.readable)
      (:= arrived took.arrived)
      (if (is took.event None)
          (:= more False)
          (:= taken (+ taken #(took.event)))))
    (resume (if (is closed None) taken #((HttpServerClosed :reason closed)))))
  (HttpReadBody [ticket max-bytes]
    (if (not-in ticket readable)
        (resume (HttpBodyFailed :reason (.format "The body of ticket {}'s request cannot be read (unknown ticket, ticket already read, ticket after a command was fired)" ticket)))
        (do (<- outcome HttpBodyOutcome (scripted-body-outcome (get readable ticket) (.get body-table ticket) max-bytes))
            (<- rest dict (without-ticket readable ticket))
            (:= readable rest)
            (resume outcome))))
  (HttpRespond [ticket status headers body]
    (<- answer HttpServed (served-of script effect (get arrived ticket)))
    (:= served (+ served #(answer)))
    (<- rest dict (without-ticket readable ticket))
    (:= readable rest)
    (resume None))
  (HttpForward [ticket url]
    (<- answer HttpServed (served-of script effect (get arrived ticket)))
    (:= served (+ served #(answer)))
    (<- rest dict (without-ticket readable ticket))
    (:= readable rest)
    (resume None))
  (WsForward [ticket url]
    (<- answer HttpServed (served-of script effect (get arrived ticket)))
    (:= served (+ served #(answer)))
    (<- rest dict (without-ticket readable ticket))
    (:= readable rest)
    (resume None))
  (WsAccept [ticket]
    (<- answer HttpServed (served-of script effect (get arrived ticket)))
    (:= served (+ served #(answer)))
    (<- rest dict (without-ticket readable ticket))
    (:= readable rest)
    ;; A refused ticket (an answer other than 101) is not raised -- no ws events come out.
    (when (= answer.status 101)
      (:= opened (| opened #{ticket}))
      (:= pending (+ #((WsOpened :ticket ticket)) pending)))
    (resume None))
  (WsSendText [ticket text]
    ;; The decision for one message to a ticket is the single script-sent (the same as the ws mouth's answer -- header comment).
    (<- sent ScriptSend (script-sent ticket text limit script.stalled served opened backlog tally pending))
    (:= served sent.served)
    (:= opened sent.opened)
    (:= backlog sent.backlog)
    (:= tally sent.tally)
    (:= pending sent.pending)
    (resume None))
  (WsSendTextEach [tickets text]
    ;; In ticket order, the same decision as WsSendText (the contract test compares the same sequence for both answerers).
    (for [ticket tickets]
      (<- sent ScriptSend (script-sent ticket text limit script.stalled served opened backlog tally pending))
      (:= served sent.served)
      (:= opened sent.opened)
      (:= backlog sent.backlog)
      (:= tally sent.tally)
      (:= pending sent.pending))
    (resume None))
  (WsClose [ticket code reason]
    (when (in ticket opened)
      (:= served (+ served #((WsCloseSent :ticket ticket :code code :reason reason))))
      (<- dropped WsSendReport (tally-dropped tally (.get backlog ticket 0) False))
      (:= tally dropped)
      (<- rest dict (without-ticket backlog ticket))
      (:= backlog rest)
      (:= opened (- opened #{ticket}))
      ;; The status code and reason claimed are the same closing-of as the real one (our own close).
      (<- frame WsCloseFrame (closing-of None (WsCloseFrame :code code :reason reason) None None))
      (:= pending (+ #((WsClosed :ticket ticket :code frame.code :reason frame.reason)) pending)))
    (resume None))
  (HttpShutdown [reason drain-seconds close-code]
    (:= listening False)
    (for [ticket (sorted opened)]
      (:= served (+ served #((WsCloseSent :ticket ticket :code close-code :reason reason))))
      (<- dropped WsSendReport (tally-dropped tally (.get backlog ticket 0) False))
      (:= tally dropped))
    (:= backlog {})
    (:= opened (frozenset))
    (:= closed reason)
    (resume None))
  (TakeWsSendReport []
    (val report tally)
    (:= tally EMPTY-REPORT)
    (resume report))
  (ReadHttpServed []
    (resume served))
  (AppendHttpScript [arrivals bodies]
    (:= pending (+ pending arrivals))
    (<- added dict (bodies-by-ticket bodies))
    (:= body-table (| body-table added))
    (resume None)))
