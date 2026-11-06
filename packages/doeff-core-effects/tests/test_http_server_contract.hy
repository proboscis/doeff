;;; HTTP listener contract tests - the real implementation (aiohttp-http-server) and the fake (scripted-http-server), both answering the same effects (HttpListen, HttpNextRequest, HttpRespond, HttpReadBody, HttpForward, WsAccept, WsSendText,
;;; WsClose, HttpShutdown, TakeWsSendReport), go through the same deftests.
;;; The interpreter assembly and the contract world are in http_server_contract_handlers.hy. Every deftest runs the same Program (serve - opens the listener, makes the peer
;;; send requests, answers them, and closes once everything is settled) and checks what the peer received, the sequence of events, and the answer after closing.
;;;
;;;   * The status, headers (same-named headers are not dropped) and body (byte string, file range) of HttpRespond reach the peer as-is. An answer without a body has an empty body
;;;   * HEAD and 204 answers send no body (even if HttpBodyBytes is given)
;;;   * HttpForward delivers the upstream status to the peer, and an unreachable upstream gives 502
;;;   * WsForward to an unreachable upstream refuses the request with 502 without upgrading it to ws, and no ws events occur
;;;   * A ticket upgraded by WsAccept yields WsOpened; the peer's message yields WsTextArrived / WsBinaryArrived; WsSendText reaches the peer; and the status code and reason of WsClose appear in
;;;     both the close the peer receives and the WsClosed event. If the peer closes, WsClosed carries the peer's status code and reason
;;;   * WsAccept on a request without Upgrade is refused with 426, and no ws events occur
;;;   * A message over the send limit cuts the connection on the spot (WsClosed 1006, the cut reason, and the cut count)
;;;   * HttpShutdown sends close 1000 and a reason to open ws connections, and every later HttpNextRequest gives HttpServerClosed (with that reason)
;;;   * HttpReadBody reads up to exactly the limit, and beyond it gives HttpBodyTooLarge (declared length; None if undeclared); only once per ticket; cannot read after the command
;;;   * A request matching the probes of HttpListen (GET/HEAD and a path) is answered by the responder via answer and does not become an event of the body. answer runs in the responder's own run
;;;     (an answer that reads a handler of the body's run gives 500)
;;; Properties of the responder alone are in test_http_server.hy (real: bound port, HTTP forwarding body and X-Forwarded-Proto, ws forwarding, received-at. fake: longest match of the
;;; upstream, a peer that does not read its box, appending to the script later, failure in the middle of a read).
(require doeff-hy.macros [defk deftest <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import doeff_core_effects [HttpAddress HttpBodyBytes HttpBodyFailed HttpBodyFileRange HttpBodyOutcome HttpBodyRead HttpBodyTooLarge HttpEvent HttpForward HttpHeader HttpListen HttpNextRequest HttpNoBody HttpReadBody HttpRequestArrived HttpRespond HttpServerClosed HttpShutdown TakeWsSendReport WsAccept WsBinaryArrived WsClose WsClosed WsForward WsOpened WsSendReport WsSendText WsSendTextEach WsTextArrived HttpProbe HttpProbeAnswer WsShortcut]
        doeff_core_effects._http_server_effects [WS-CUT-REASON WS-REFUSAL-TEXT WsShortcutAhead WsShortcutCarried])
(import http_server_contract_handlers [ContractWorld PeerAnswer PeerAnswers PeerRequest PeerSend UPSTREAM-STATUS World])

(val SHUTDOWN-REASON "the listener closed")
(val SEND-LIMIT 64)
(val BODY-LIMIT 8)
(val HUGE-DECLARED 100000000)
(val GREETING (.encode "hello" "utf-8"))


(defrecord ReadNote
  "Record of a body read: path, first answer, second answer to the same ticket."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ str path)
  (#^ HttpBodyOutcome first)
  (#^ HttpBodyOutcome again))


(defrecord Answered
  "Result of firing a command at one request: settled = whether this request was settled here (a ticket upgraded to ws settles with WsClosed), note = record of the body read."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ bool settled)
  (setv #^ (| ReadNote None) note None))


(defrecord Served
  "Answer of serve: events = events received until closing (comparison shape - event-shape), again = the answer received once more after closing, answers = what the peer
   received (in send order), reads = records of body reads, report = the final tally of sends."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ tuple events)
  (#^ tuple again)
  (#^ tuple answers)
  (#^ (get tuple #(ReadNote ...)) reads)
  (#^ WsSendReport report))


(defk event-shape [event]
  {:pre [(: event HttpEvent)] :post [(: % tuple)] :tags {:context "http-server-test" :role "judgment"}}
  "Comparison fields of an event (the header and the received beat differ per responder so they are excluded - the header includes what the peer's client adds)."
  (match event
    (HttpRequestArrived :ticket t :method m :path p :target target :upgrade u) #("request" t m p target u)
    (WsOpened :ticket t) #("opened" t)
    (WsTextArrived :ticket t :text text) #("text" t text)
    (WsBinaryArrived :ticket t :data data) #("binary" t data)
    (WsClosed :ticket t :code code :reason reason) #("closed" t code reason)
    (HttpServerClosed :reason reason) #("server-closed" reason)))


(defk respond [ticket status headers body]
  {:pre [(: ticket str) (: status int) (: headers tuple) (: body (| HttpBodyBytes HttpBodyFileRange HttpNoBody))] :post [(: % None)]
   :tags {:context "http-server-test" :role "program"}}
  "Send an answer to the ticket."
  (<- (HttpRespond :ticket ticket :status status :headers headers :body body))
  None)


(defk read-and-answer [ticket path]
  {:pre [(: ticket str) (: path str)] :post [(: % ReadNote)] :tags {:context "http-server-test" :role "program"}}
  "Read the body up to BODY-LIMIT (also firing a second read), answer 200 for a readable body, 413 (declared length) when over the limit, and 400 when unreadable.
   Answer = the record of the read."
  (<- outcome HttpBodyOutcome (HttpReadBody :ticket ticket :max-bytes BODY-LIMIT))
  (<- again HttpBodyOutcome (HttpReadBody :ticket ticket :max-bytes BODY-LIMIT))
  (<- (match outcome
        (HttpBodyRead :data data) (respond ticket 200 #() (HttpBodyBytes :data data))
        (HttpBodyTooLarge :declared declared) (respond ticket 413 #() (HttpBodyBytes :data (.encode (str declared) "utf-8")))
        (HttpBodyFailed :reason reason) (respond ticket 400 #() (HttpBodyBytes :data (.encode reason "utf-8")))))
  (ReadNote :path path :first outcome :again again))


(defk answer-request [event world]
  {:pre [(: event HttpRequestArrived) (: world World)] :post [(: % Answered)] :tags {:context "http-server-test" :role "program"}}
  "Fire a per-path command at one request."
  (val t event.ticket)
  (match event.path
    "/text" (do (<- (respond t 200 #((HttpHeader :name "X-Contract" :value "a") (HttpHeader :name "X-Contract" :value "b")
                                     (HttpHeader :name "Content-Type" :value "text/plain; charset=utf-8"))
                             (HttpBodyBytes :data GREETING)))
                (Answered :settled True))
    "/file" (do (<- (respond t 206 #((HttpHeader :name "Content-Length" :value "5")) (HttpBodyFileRange :path world.site :start 2 :length 5)))
                (Answered :settled True))
    "/nothing" (do (<- (respond t 204 #() (HttpNoBody)))
                   (Answered :settled True))
    "/nothing-but-bytes" (do (<- (respond t 204 #() (HttpBodyBytes :data b"dropped")))
                             (Answered :settled True))
    "/head" (do (<- (respond t 200 #((HttpHeader :name "Content-Length" :value "11")) (HttpNoBody)))
                (Answered :settled True))
    "/relay" (do (<- (HttpForward :ticket t :url (+ world.upstream "/x")))
                 (Answered :settled True))
    "/dead" (do (<- (HttpForward :ticket t :url (+ world.dead "/x")))
                (Answered :settled True))
    ;; A ws forward to an unreachable upstream is refused and no ws events occur - it settles here.
    "/dead-ws" (do (<- (WsForward :ticket t :url (+ world.dead "/x")))
                   (Answered :settled True))
    "/ws" (do (<- (WsAccept :ticket t))
              ;; A request without Upgrade is refused and no ws events occur - it settles here.
              (Answered :settled (not event.upgrade)))
    "/late-read" (do (<- (respond t 204 #() (HttpNoBody)))
                     (<- late HttpBodyOutcome (HttpReadBody :ticket t :max-bytes BODY-LIMIT))
                     (Answered :settled True :note (ReadNote :path event.path :first late :again late)))
    path :if (.startswith path "/body") (do (<- note ReadNote (read-and-answer t path))
                                            (Answered :settled True :note note))
    other (raise (ValueError (+ "path not in the contract Program: " other)))))


(defk answer-ws [event]
  {:pre [(: event (| WsOpened WsTextArrived WsBinaryArrived))] :post [(: % None)] :tags {:context "http-server-test" :role "program"}}
  "Answer one ws message: \"bye\" = close with 4001, \"big\" = send one message of twice the send limit, \"stop\" = close the listener, other text is echoed,
   one binary message closes with 1003."
  (match event
    (WsTextArrived :ticket t :text "bye") (<- (WsClose :ticket t :code 4001 :reason "goodbye"))
    (WsTextArrived :ticket t :text "big") (<- (WsSendText :ticket t :text (* "x" (* 2 SEND-LIMIT))))
    ;; "each" = send one message, a list of tickets (this one and an unknown one), and one message in that order. "each-big" = send one message of twice the limit with a list of tickets.
    (WsTextArrived :ticket t :text "each") (do (<- (WsSendText :ticket t :text "a"))
                                               (<- (WsSendTextEach :tickets #(t "ghost") :text "b"))
                                               (<- (WsSendText :ticket t :text "c")))
    (WsTextArrived :ticket t :text "each-big") (<- (WsSendTextEach :tickets #("ghost" t) :text (* "x" (* 2 SEND-LIMIT))))
    (WsTextArrived :text "stop") (<- (HttpShutdown :reason SHUTDOWN-REASON :drain-seconds 2.0))
    (WsTextArrived :ticket t :text text) (<- (WsSendText :ticket t :text (+ "echo:" text)))
    (WsBinaryArrived :ticket t) (<- (WsClose :ticket t :code 1003 :reason "text only"))
    (WsOpened) None)
  None)


(defk serve [requests]
  {:pre [(: requests tuple)] :post [(: % Served)] :tags {:context "http-server-test" :role "program"}}
  "Contract Program: open a listener, make the peer send requests, answer them, and close once all requests are settled. After closing, receive once more and read what the peer
   received and the send tally."
  (<- world World (ContractWorld))
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0) :ws-send-max-bytes SEND-LIMIT))
  (<- (PeerSend :address bound :requests requests))
  (var events #())
  (var reads #())
  (var settled 0)
  (var shut False)
  (var open True)
  (while open
    (<- event HttpEvent (HttpNextRequest))
    (<- shape tuple (event-shape event))
    (:= events (+ events #(shape)))
    (match event
      (HttpServerClosed) (:= open False)
      (HttpRequestArrived) (do (<- answered Answered (answer-request event world))
                               (:= settled (+ settled (if answered.settled 1 0)))
                               (:= reads (+ reads (if (is answered.note None) #() #(answered.note)))))
      (WsClosed) (:= settled (+ settled 1))
      _ (<- (answer-ws event)))
    (when (and open (not shut) (= settled (len requests)))
      (:= shut True)
      (<- (HttpShutdown :reason SHUTDOWN-REASON :drain-seconds 0.5))))
  (<- again HttpEvent (HttpNextRequest))
  (<- again-shape tuple (event-shape again))
  (<- answers tuple (PeerAnswers))
  (<- report WsSendReport (TakeWsSendReport))
  (Served :events events :again again-shape :answers answers :reads reads :report report))


(defk header-values [answer name]
  {:pre [(: answer PeerAnswer) (: name str)] :post [(: % list)] :tags {:context "http-server-test" :role "judgment"}}
  "List of values of header name that the peer received (case-insensitive on the name, in arrival order)."
  (lfor h answer.headers :if (= (.lower h.name) (.lower name)) h.value))


(defk request-shapes [requests]
  {:pre [(: requests tuple)] :post [(: % list)] :tags {:context "http-server-test" :role "judgment"}}
  "Shape of events when a sequence of requests arrives (tickets in order from 1)."
  (lfor [index r] (enumerate requests)
        #("request" (str (+ index 1)) r.method (get (.partition r.target "?") 0) r.target r.ws)))


(val CLOSED #(#("server-closed" SHUTDOWN-REASON)))


(deftest test-an-answer-reaches-the-peer-as-sent
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "GET" :target "/text?q=1") (PeerRequest :method "GET" :target "/file")
                  (PeerRequest :method "GET" :target "/nothing")))
  (<- served Served (serve requests))
  (<- shapes list (request-shapes requests))
  (assert (= served.events (+ (tuple shapes) CLOSED)) served.events)
  (val text (get served.answers 0))
  (val file (get served.answers 1))
  (val nothing (get served.answers 2))
  (assert (= [text.status text.body] [200 GREETING]) text)
  ;; Same-named headers both arrive.
  (assert (= (! (header-values text "X-Contract")) ["a" "b"]) text.headers)
  (assert (= (! (header-values text "Content-Type")) ["text/plain; charset=utf-8"]) text.headers)
  ;; The file range is 5 bytes from the 2nd byte of site.
  (assert (= [file.status file.body (! (header-values file "Content-Length"))] [206 b"nsole" ["5"]]) file)
  (assert (= [nothing.status nothing.body] [204 b""]) nothing))


(deftest test-head-and-no-content-answers-carry-no-body
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  ;; Send over a raw socket - every byte arriving after the answer headers is seen as body.
  (val requests #((PeerRequest :method "HEAD" :target "/head" :raw True) (PeerRequest :method "HEAD" :target "/text" :raw True)
                  (PeerRequest :method "GET" :target "/nothing-but-bytes" :raw True) (PeerRequest :method "GET" :target "/text")))
  (<- served Served (serve requests))
  (val head (get served.answers 0))
  (val head-with-bytes (get served.answers 1))
  (val no-content (get served.answers 2))
  (val after (get served.answers 3))
  (assert (= [head.status head.body (! (header-values head "Content-Length"))] [200 b"" ["11"]]) head)
  ;; Even if HttpBodyBytes is given, HEAD and 204 send no body.
  (assert (= [head-with-bytes.status head-with-bytes.body] [200 b""]) head-with-bytes)
  (assert (= [no-content.status no-content.body] [204 b""]) no-content)
  (assert (= [after.status after.body] [200 GREETING]) after))


(deftest test-a-forward-answers-with-the-upstream-status-or-502
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "POST" :target "/relay" :body b"payload") (PeerRequest :method "GET" :target "/dead")))
  (<- served Served (serve requests))
  (assert (= (lfor a served.answers a.status) [UPSTREAM-STATUS 502]) served.answers)
  (assert (= served.events (+ (tuple (! (request-shapes requests))) CLOSED)) served.events))


(deftest test-a-ws-forward-to-an-unreachable-upstream-answers-502
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "GET" :target "/dead-ws" :ws True)))
  (<- served Served (serve requests))
  ;; A refused ticket yields no ws events.
  (assert (= served.events (+ (tuple (! (request-shapes requests))) CLOSED)) served.events)
  (val refused (get served.answers 0))
  (assert (= [refused.status refused.texts refused.closed] [502 #() None]) refused))


(deftest test-a-ws-exchange-reaches-both-sides-and-our-close-is-named
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("hi" "bye")))))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "hi") #("text" "1" "bye")
                                #("closed" "1" 4001 "goodbye"))
                              CLOSED))
          served.events)
  (val answer (get served.answers 0))
  (assert (= [answer.status answer.texts answer.closed] [101 #("echo:hi") #(4001 "goodbye")]) answer)
  (assert (= [served.report.queued-frames served.report.queued-bytes served.report.flushed-bytes served.report.dropped-bytes
              served.report.cuts (len served.report.flush-seconds)]
             [1 7 7 0 0 1])
          served.report))


(deftest test-a-send-to-a-list-of-tickets-keeps-the-order-and-drops-unknown-tickets
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("each") :replies 3 :leave #(4002 "the peer leaves")))))
  (val answer (get served.answers 0))
  (assert (= answer.texts #("a" "b" "c")) answer)
  (assert (= [served.report.queued-frames served.report.queued-bytes served.report.flushed-bytes served.report.dropped-bytes
              served.report.cuts]
             [3 3 3 0 0])
          served.report))


(deftest test-an-oversized-send-to-a-list-of-tickets-cuts-the-connection
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("each-big")))))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "each-big") #("closed" "1" 1006 WS-CUT-REASON))
                              CLOSED))
          served.events)
  (assert (= [served.report.queued-bytes served.report.cuts] [0 1]) served.report))


(deftest test-a-peer-close-and-a-binary-close-name-their-code
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("hi") :replies 1 :leave #(4002 "the peer leaves"))
                  (PeerRequest :method "GET" :target "/ws" :ws True :sends #(b"\x01"))))
  (<- served Served (serve requests))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "hi") #("closed" "1" 4002 "the peer leaves")
                                #("request" "2" "GET" "/ws" "/ws" True) #("opened" "2") #("binary" "2" b"\x01")
                                #("closed" "2" 1003 "text only"))
                              CLOSED))
          served.events)
  (val left (get served.answers 0))
  (val binary (get served.answers 1))
  ;; On a connection the peer closed, a close from the listener is not counted as received by the peer.
  (assert (= [left.texts left.closed] [#("echo:hi") None]) left)
  (assert (= [binary.texts binary.closed] [#() #(1003 "text only")]) binary))


(deftest test-a-ws-accept-without-upgrade-is-refused
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "GET" :target "/ws")))
  (<- served Served (serve requests))
  ;; A refused ticket yields no ws events.
  (assert (= served.events (+ (tuple (! (request-shapes requests))) CLOSED)) served.events)
  (val refused (get served.answers 0))
  (assert (= [refused.status refused.body] [426 (.encode WS-REFUSAL-TEXT "utf-8")]) refused))


(deftest test-an-oversized-send-cuts-the-connection
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("big")))))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "big") #("closed" "1" 1006 WS-CUT-REASON))
                              CLOSED))
          served.events)
  (val cut (get served.answers 0))
  (assert (= [cut.texts cut.closed] [#() None]) cut)
  ;; A message over the limit is cut without being queued - queued, flushed and dropped bytes are 0, and cuts are 1.
  (assert (= [served.report.queued-frames served.report.queued-bytes served.report.flushed-bytes served.report.dropped-bytes
              served.report.cuts]
             [0 0 0 0 1])
          served.report))


(deftest test-a-shutdown-closes-open-ws-and-stays-closed
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("stop")))))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "stop")) CLOSED)) served.events)
  ;; HttpNextRequest after closing also gives HttpServerClosed with the same reason.
  (assert (= served.again (get CLOSED 0)) served.again)
  (val answer (get served.answers 0))
  (assert (= answer.closed #(1000 SHUTDOWN-REASON)) answer))


;; Boundary requests and the first answer (the second is HttpBodyFailed in every case).
(val BODY-CASES #(#((PeerRequest :method "POST" :target "/body/exact" :body b"12345678") (HttpBodyRead :data b"12345678") #(200 b"12345678"))
                  #((PeerRequest :method "POST" :target "/body/over" :body b"123456789") (HttpBodyTooLarge :declared 9) #(413 b"9"))
                  #((PeerRequest :method "POST" :target "/body/chunked-fit" :body b"abcdefgh" :chunked True)
                    (HttpBodyRead :data b"abcdefgh") #(200 b"abcdefgh"))
                  #((PeerRequest :method "POST" :target "/body/chunked-over" :body b"abcdefghijkl" :chunked True)
                    (HttpBodyTooLarge :declared None) #(413 b"None"))
                  #((PeerRequest :method "GET" :target "/body/empty") (HttpBodyRead :data b"") #(200 b""))
                  ;; A declaration far over the limit: a real peer sends only headers - if it is not refused without reading, no answer comes.
                  #((PeerRequest :method "POST" :target "/body/huge-declared" :declared HUGE-DECLARED)
                    (HttpBodyTooLarge :declared HUGE-DECLARED) #(413 (.encode (str HUGE-DECLARED) "ascii")))))


(deftest test-a-body-is-read-once-up-to-the-limit
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve (tuple (gfor c BODY-CASES (get c 0)))))
  (assert (= (tuple (gfor note served.reads #(note.path note.first)))
             (tuple (gfor [request outcome _answer] BODY-CASES #(request.target outcome))))
          served.reads)
  ;; Only once per ticket - the second cannot read.
  (assert (all (gfor note served.reads (isinstance note.again HttpBodyFailed))) served.reads)
  (assert (= (lfor a served.answers #(a.status a.body)) (lfor c BODY-CASES (get c 2))) served.answers))


(deftest test-a-body-cannot-be-read-after-the-command
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "POST" :target "/late-read" :body b"abc"))))
  (val late (. (get served.reads 0) first))
  (assert (isinstance late HttpBodyFailed) late)
  (assert (= (. (get served.answers 0) status) 204) served.answers))


;; --- Probe hook where the responder answers by itself -----------------------------------------------------------------------

(val PROBE-FAILED "probe for /peek could not produce an answer")


(defk fixed-probe-answer [status text]
  {:pre [(: status int) (: text str)] :post [(: % HttpProbeAnswer)] :tags {:context "http-server-test" :role "program"}}
  "Answer Program of the probe hook (closed - uses no effect)."
  (HttpProbeAnswer :status status :headers #((HttpHeader :name "Content-Type" :value "text/plain; charset=utf-8"))
                   :body (.encode text "utf-8")))


(defk peek-world []
  {:pre [] :post [(: % HttpProbeAnswer)] :tags {:context "http-server-test" :role "program"}}
  "Answer of a probe that tries to read a handler of the body's run (the contract world) - answer runs in the responder's own run so it does not reach, and the answer is 500."
  (<- world World (ContractWorld))
  (HttpProbeAnswer :status 200 :headers #() :body (.encode world.site "utf-8")))


(defk serve-probed [requests probes reaching]
  {:pre [(: requests tuple) (: probes tuple) (: reaching int)] :post [(: % Served)] :tags {:context "http-server-test" :role "program"}}
  "Contract Program with a probe hook: open a listener, make the peer send requests, answer requests that reach the body with 200 \"program\", and close when `reaching` of them have
   arrived. After closing, receive once more and read what the peer received."
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0) :probes probes))
  (<- (PeerSend :address bound :requests requests))
  (var events #())
  (var reached 0)
  (var open True)
  (while open
    (<- event HttpEvent (HttpNextRequest))
    (<- shape tuple (event-shape event))
    (:= events (+ events #(shape)))
    (match event
      (HttpServerClosed) (:= open False)
      (HttpRequestArrived :ticket t) (do (<- (respond t 200 #() (HttpBodyBytes :data b"program")))
                                         (:= reached (+ reached 1))
                                         (when (= reached reaching)
                                           (<- (HttpShutdown :reason SHUTDOWN-REASON :drain-seconds 0.5))))
      _ None))
  (<- again HttpEvent (HttpNextRequest))
  (<- again-shape tuple (event-shape again))
  (<- answers tuple (PeerAnswers))
  (<- report WsSendReport (TakeWsSendReport))
  (Served :events events :again again-shape :answers answers :reads #() :report report))


(deftest test-a-probe-is-answered-by-the-server-and-never-reaches-the-program
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  ;; A request whose path matches with GET/HEAD is answered by the responder via answer and does not become an event of the body (the ticket is counted). POST and other paths reach the body.
  ;; answer runs in the responder's own run - an answer that reads a handler of the body's run (the contract world) gives 500 and a reason.
  (val probes #((HttpProbe :path "/healthz" :answer (fixed-probe-answer 200 "alive"))
                (HttpProbe :path "/readyz" :answer (fixed-probe-answer 503 "not ready"))
                (HttpProbe :path "/peek" :answer (peek-world))))
  (val requests #((PeerRequest :method "GET" :target "/healthz") (PeerRequest :method "HEAD" :target "/healthz")
                  (PeerRequest :method "GET" :target "/readyz?verbose=1") (PeerRequest :method "GET" :target "/peek")
                  (PeerRequest :method "POST" :target "/healthz") (PeerRequest :method "GET" :target "/text")))
  (<- served Served (serve-probed requests probes 2))
  (assert (= served.events (+ #(#("request" "5" "POST" "/healthz" "/healthz" False) #("request" "6" "GET" "/text" "/text" False))
                              CLOSED))
          served.events)
  (assert (= (lfor a (cut served.answers 3) #(a.status a.body)) [#(200 b"alive") #(200 b"") #(503 b"not ready")]) served.answers)
  (assert (= (! (header-values (get served.answers 0) "Content-Type")) ["text/plain; charset=utf-8"]) served.answers)
  (val peeked (get served.answers 3))
  (assert (= peeked.status 500) peeked)
  (assert (.startswith (.decode peeked.body "utf-8") PROBE-FAILED) peeked)
  (assert (= (lfor a (cut served.answers 4 None) #(a.status a.body)) [#(200 b"program") #(200 b"program")]) served.answers))


;; --- Hook for one ws message that the responder answers on the spot -------------------------------------------------------

(defk early-texts [ticket text]
  {:pre [(: ticket str) (: text str)] :post [(: % (get tuple #(str ...)))] :tags {:context "http-server-test" :role "program"}}
  "answer of the ws hook (closed - uses no effect): answers a matched message with one \"early:\" message ahead of the body."
  #((+ "early:" text)))


(defk no-texts [ticket text]
  {:pre [(: ticket str) (: text str)] :post [(: % (get tuple #(str ...)))] :tags {:context "http-server-test" :role "program"}}
  "answer of a ws hook with no answer text - queues nothing and reaches the body in the same shape as an unmatched message."
  #())


(defk broken-texts [ticket text]
  {:pre [(: ticket str) (: text str)] :post [(: % (get tuple #(str ...)))] :tags {:context "http-server-test" :role "program"}}
  "answer of a failing ws hook - tries to read a handler of the body's run (the contract world) (answer runs in the responder's own run so it does not reach)."
  (<- world World (ContractWorld))
  #(world.site))


(defk serve-shortcut [requests shortcuts]
  {:pre [(: requests tuple) (: shortcuts tuple)] :post [(: % Served)] :tags {:context "http-server-test" :role "program"}}
  "Contract Program with a ws hook: open a listener, make the peer send requests, and answer a ws message with \"late:\" if the responder answered it first, otherwise
   \"echo:\" (\"bye\" closes with 4001), and close once all requests are settled. The event shape adds the list of queued texts answered and the carried value carried."
  (<- world World (ContractWorld))
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0) :ws-send-max-bytes SEND-LIMIT :ws-shortcuts shortcuts))
  (<- (PeerSend :address bound :requests requests))
  (var events #())
  (var settled 0)
  (var shut False)
  (var open True)
  (while open
    (<- event HttpEvent (HttpNextRequest))
    (<- shape tuple (event-shape event))
    (:= events (+ events #((if (isinstance event WsTextArrived) (+ shape #(event.answered event.carried)) shape))))
    (match event
      (HttpServerClosed) (:= open False)
      (HttpRequestArrived) (do (<- answered Answered (answer-request event world))
                               (:= settled (+ settled (if answered.settled 1 0))))
      (WsClosed) (:= settled (+ settled 1))
      (WsTextArrived :ticket t :text "bye") (<- (WsClose :ticket t :code 4001 :reason "goodbye"))
      (WsTextArrived :ticket t :text text :answered answered)
        (<- (WsSendText :ticket t :text (+ (if answered "late:" "echo:") text)))
      _ None)
    (when (and open (not shut) (= settled (len requests)))
      (:= shut True)
      (<- (HttpShutdown :reason SHUTDOWN-REASON :drain-seconds 0.5))))
  (<- answers tuple (PeerAnswers))
  (<- report WsSendReport (TakeWsSendReport))
  (Served :events events :again #() :answers answers :reads #() :report report))


(deftest test-a-ws-shortcut-answers-before-the-program-and-keeps-the-order-of-the-connection
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  ;; A message containing marker is answered first by the responder via answer, and the same message reaches the body carrying the list of queued texts answered and the beat when queuing finished (the body's answer is
  ;; after that). A message where answer failed and a message with an empty answer reach the body with nothing queued and answered left empty, and the body answers as usual. An unmatched
  ;; message stays as it is.
  ;; The order of messages on the same connection does not change.
  (val shortcuts #((WsShortcut :marker "fast" :answer early-texts) (WsShortcut :marker "boom" :answer broken-texts)
                   (WsShortcut :marker "quiet" :answer no-texts)))
  (<- served Served (serve-shortcut #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("fast1" "hi" "boom1" "quiet1" "bye")))
                                    shortcuts))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1")
                                #("text" "1" "fast1" #("early:fast1") None) #("text" "1" "hi" #() None)
                                #("text" "1" "boom1" #() None) #("text" "1" "quiet1" #() None) #("text" "1" "bye" #() None)
                                #("closed" "1" 4001 "goodbye"))
                              CLOSED))
          served.events)
  (val answer (get served.answers 0))
  (assert (= [answer.status answer.texts answer.closed] [101 #("early:fast1" "late:fast1" "echo:hi" "echo:boom1" "echo:quiet1") #(4001 "goodbye")])
          answer))


(defk staged-rest [text]
  {:pre [(: text str)] :post [(: % WsShortcutCarried)] :tags {:context "http-server-test" :role "program"}}
  "Continuation stage of the 2-stage ws hook (closed): queues a \"second:\" message and carries the value \"kept:\" to the body."
  (WsShortcutCarried :texts #((+ "second:" text)) :carried (+ "kept:" text)))


(defk staged-texts [ticket text]
  {:pre [(: ticket str) (: text str)] :post [(: % WsShortcutAhead)] :tags {:context "http-server-test" :role "program"}}
  "answer of the 2-stage ws hook: first queues a \"first:\" message, then runs the continuation stage staged-rest."
  (WsShortcutAhead :texts #((+ "first:" text)) :rest (staged-rest text)))


(defk broken-rest []
  {:pre [] :post [(: % (get tuple #(str ...)))] :tags {:context "http-server-test" :role "program"}}
  "A failing continuation stage - tries to read a handler of the body's run (the contract world)."
  (<- world World (ContractWorld))
  #(world.site))


(defk staged-broken [ticket text]
  {:pre [(: ticket str) (: text str)] :post [(: % WsShortcutAhead)] :tags {:context "http-server-test" :role "program"}}
  "answer of a ws hook whose first stage queues and whose continuation stage fails."
  (WsShortcutAhead :texts #((+ "first:" text)) :rest (broken-rest)))


(defk texts-starting [texts prefixes]
  {:pre [(: texts tuple) (: prefixes tuple)] :post [(: % tuple)] :tags {:context "http-server-test" :role "judgment"}}
  "The texts that start with one of prefixes, in the order sent."
  (tuple (gfor text texts :if (.startswith text prefixes) text)))


(deftest test-a-staged-ws-shortcut-sends-each-stage-before-the-next-and-carries-a-value-to-the-program
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  ;; 2-stage answer: queue the first stage's text and then run the continuation stage, queue the continuation stage's text too, and then one message carrying the texts of all stages and the value carried
  ;; by the final stage reaches the body. A message whose continuation stage failed reaches the body carrying the text queued in the first stage, without a carried value. The order of messages on the same connection does not change.
  (val shortcuts #((WsShortcut :marker "stage" :answer staged-texts) (WsShortcut :marker "split" :answer staged-broken)))
  (<- served Served (serve-shortcut #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("stage1" "hi" "split1" "bye")))
                                    shortcuts))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1")
                                #("text" "1" "stage1" #("first:stage1" "second:stage1") "kept:stage1") #("text" "1" "hi" #() None)
                                #("text" "1" "split1" #("first:split1") None) #("text" "1" "bye" #() None)
                                #("closed" "1" 4001 "goodbye"))
                              CLOSED))
          served.events)
  (val answer (get served.answers 0))
  (assert (= [answer.status answer.closed] [101 #(4001 "goodbye")]) answer)
  ;; How the answerer's texts interleave with the body's answers to earlier messages is not fixed: the answerer answers "split1" on its own thread
  ;; as soon as it reads it, ahead of a body still answering "stage1" and "hi" (the point of the entry) -- the real answerer sent "first:split1"
  ;; before "late:stage1" on a busy machine. What is fixed: each side keeps the message order, and a matched message's body answer follows its own stages.
  (val texts answer.texts)
  (assert (= (sorted texts) (sorted ["first:stage1" "second:stage1" "late:stage1" "echo:hi" "first:split1" "late:split1"])) texts)
  (<- staged tuple (texts-starting texts #("first:" "second:")))
  (assert (= staged #("first:stage1" "second:stage1" "first:split1")) texts)
  (<- bodily tuple (texts-starting texts #("late:" "echo:")))
  (assert (= bodily #("late:stage1" "echo:hi" "late:split1")) texts)
  (assert (< (.index texts "second:stage1") (.index texts "late:stage1")) texts)
  (assert (< (.index texts "first:split1") (.index texts "late:split1")) texts))
