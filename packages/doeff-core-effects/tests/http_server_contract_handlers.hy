;;; Interpreters for the HTTP listener contract tests (composition root) -- run the same contract Program with only the listener handler swapped.
;;;
;;;   aiohttp-http-server   real: aiohttp-http-server (really listens on a free 127.0.0.1 port). The peer (the terminal that sends PeerSend requests) is
;;;                         an aiohttp client in a separate thread, which sends requests one at a time on an event loop used only by that thread, reading each answer before moving on
;;;   scripted-http-server  fake: scripted-http-server (no I/O -- a script). The peer's requests are turned from the same PeerRequest into script events (AppendHttpScript),
;;;                         and what the peer receives is read from the script's record (ReadHttpServed)
;;;
;;; The contract world is prepared in the same shape for each interpreter (the answer to ContractWorld -- World):
;;;   * site     a file whose body is read by range (contents SITE-CONTENT): real = a real file in a temporary dir; fake = a memory file
;;;   * upstream the relay target (answers every path with UPSTREAM-STATUS): real = a wsgiref listener on 127.0.0.1; fake = the script's ScriptedUpstream
;;;   * dead     an unreachable relay target: real = a port nobody listens on; fake = a destination not in the script
;;; The shape of a peer request is the single PeerRequest, and both the real terminal's way of sending (visit) and the script's events (scripted-arrivals) are built from it.
;;; Used from conftest.py's doeff_interpreter (the deftest :interpreters names -> INTERPRETERS and REQUIRES).
(require doeff-hy.macros [defk deff defhandler <- val var])
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import os)
(import queue)
(import socket)
(import tempfile)
(import threading)
(import wsgiref.simple-server [make-server])
(import doeff [EffectBase Program run with_handlers])
(import doeff_core_effects [state await-handler])
(import doeff_core_effects [MemoryFile MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [AppendHttpScript HttpAddress HttpHeader HttpRequestArrived HttpRespond HttpScript HttpServed ReadHttpServed ScriptedBody ScriptedUpstream WsBinaryArrived WsCloseSent WsClosed WsTextArrived WsTextSent])
(import doeff_core_effects [scripted-http-server])

(val AIOHTTP "aiohttp-http-server")
(val SCRIPTED "scripted-http-server")
;; External modules the interpreters need (in environments without them, conftest.py skips the tests of that interpreter).
(val REQUIRES {AIOHTTP "aiohttp"})

(val SITE-CONTENT b"console.log(1)")
(val MEMORY-SITE "/contract-site/app.js")
(val UPSTREAM-STATUS 201)
(val UPSTREAM-BODY b"upstream")
(val SCRIPTED-UPSTREAM "http://upstream.test")
(val SCRIPTED-DEAD "http://dead.test")
;; The upper bound (seconds) for the peer to wait for one answer or one message.
(val PEER-SECONDS 10)
(val WS-OPEN-STATUS 101)


(defrecord World
  "The contract world: site = the path of the file whose body is read by range; upstream = the base of the relay target that answers; dead = the base of an unreachable relay target."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ str site)
  (#^ str upstream)
  (#^ str dead))


(defrecord PeerRequest
  "One request the peer sends: method, target (path and query), headers, body (None = not sent), chunked (send the body without declaring a length),
   declared (do not send the body, only declare Content-Length), raw (the real one does not go through the client but sends only the headers on a bare socket and reads the bytes until the connection closes
   as they are -- declared is also sent on a bare socket), ws (connect over ws), sends (the sequence of messages to send over ws --
   a str is text and bytes is bytes), and replies and leave (if leave is given, after receiving replies messages the peer closes with a status code and reason. If not, it receives until the listener
   closes)."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ str method)
  (#^ str target)
  (setv #^ (get tuple #(HttpHeader ...)) headers #()
        #^ (| bytes None) body None
        #^ bool chunked False
        #^ (| int None) declared None
        #^ bool raw False
        #^ bool ws False
        #^ (get tuple #((| str bytes) ...)) sends #()
        #^ int replies 0
        #^ (| tuple None) leave None))


(defrecord PeerAnswer
  "What the peer received: status, headers, body (b\"\" for ws), the sequence of text messages received over ws, and the close received from the listener (#(status-code reason) -- None if none was received)."
  {:tags {:context "http-server-test" :role "type"}}
  (#^ int status)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ bytes body)
  (#^ (get tuple #(str ...)) texts)
  (#^ (| tuple None) closed))


(defclass [(dataclass :frozen True)] ContractWorld [EffectBase]
  "An effect that asks for the contract world (World).")


(defclass [(dataclass :frozen True)] PeerSend [EffectBase]
  "An effect that makes the peer send requests (a sequence of PeerRequest) to address in order (fire only, no waiting). Answer = None."
  (#^ HttpAddress address)
  (#^ tuple requests))


(defclass [(dataclass :frozen True)] PeerAnswers [EffectBase]
  "An effect that waits for the peer to finish sending and asks for what was received for each request (a sequence of PeerAnswer -- in sending order).")


(defhandler contract-world [world]
  ;; Reason it stays an argument: the world is a value decided on the assembly side per interpreter (the real one is a temporary dir and port made on every run).
  (ContractWorld []
    (resume world)))


;; --- Used by both: the shape of the peer request ---------------------------------------------------------------------------------------------------

(defk path-of [target]
  {:pre [(: target str)] :post [(: % str)] :tags {:context "http-server-test" :role "judgment"}}
  "The path of target (without the query)."
  (get (.partition target "?") 0))


(defk sent-headers [request]
  {:pre [(: request PeerRequest)] :post [(: % tuple)] :tags {:context "http-server-test" :role "judgment"}}
  "Of the headers the peer sends, those the contract looks at: the headers written, plus the declaration of the body length (Content-Length if a body is sent; Transfer-Encoding if chunked;
   only the declaration if declared)."
  (val length (cond
                (is-not request.declared None) #((HttpHeader :name "Content-Length" :value (str request.declared)))
                request.chunked #((HttpHeader :name "Transfer-Encoding" :value "chunked"))
                (is-not request.body None) #((HttpHeader :name "Content-Length" :value (str (len request.body))))
                True #()))
  (+ request.headers length))


;; --- The real peer (an aiohttp client in a separate thread) ------------------------------------------------------------------------------------

(defk visit-http [loop session base request]
  {:pre [(: loop asyncio.AbstractEventLoop) (: session "aiohttp.ClientSession") (: base str) (: request PeerRequest)]
   :post [(: % PeerAnswer)] :tags {:context "http-server-test" :role "foundation"}}
  "Send one HTTP request and read the answer to the end."
  (val response (.run-until-complete loop (.request session request.method (+ base request.target) :data request.body
                                                    :chunked (if request.chunked True None)
                                                    :headers (dfor h request.headers h.name h.value))))
  (val body (.run-until-complete loop (.read response)))
  (.release response)
  (PeerAnswer :status response.status :headers (tuple (gfor [name value] (.items response.headers) (HttpHeader :name name :value value)))
              :body body :texts #() :closed None))


(defk read-to-end [raw]
  {:pre [(: raw socket.socket)] :post [(: % bytes)] :tags {:context "http-server-test" :role "foundation"}}
  "Read the socket until the peer closes it."
  (var received b"")
  (var chunk (.recv raw 65536))
  (while chunk
    (:= received (+ received chunk))
    (:= chunk (.recv raw 65536)))
  received)


(defk visit-raw [address request]
  {:pre [(: address HttpAddress) (: request PeerRequest)] :post [(: % PeerAnswer)] :tags {:context "http-server-test" :role "foundation"}}
  "Send only the headers (plus the Content-Length declaration if declared is given -- the body is not sent) on a bare socket, and read the answer until the connection closes. The answer body =
   all the bytes that arrived after the headers (if bytes come after an answer that carries no body, they also show up as the body). A request whose declaration exceeds the limit gets no answer unless the responder
   refuses it without reading."
  (var received b"")
  (val length (if (is request.declared None) "" (.format "Content-Length: {}\r\n" request.declared)))
  (with [raw (socket.create-connection #(address.host address.port) :timeout PEER-SECONDS)]
    (.sendall raw (.encode (.format "{} {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n{}\r\n"
                                    request.method request.target address.host length)
                           "ascii"))
    (<- all-bytes bytes (read-to-end raw))
    (:= received all-bytes))
  (val parts (.partition received b"\r\n\r\n"))
  (val lines (.split (.decode (get parts 0) "latin-1") "\r\n"))
  (PeerAnswer :status (int (get (.split (get lines 0)) 1))
              :headers (tuple (gfor line (cut lines 1 None) :setv [name _colon value] (.partition line ":")
                                    (HttpHeader :name name :value (.strip value))))
              :body (get parts 2) :texts #() :closed None))


(defk talk-ws [loop ws request]
  {:pre [(: loop asyncio.AbstractEventLoop) (: ws "aiohttp.ClientWebSocketResponse") (: request PeerRequest)]
   :post [(: % PeerAnswer)] :tags {:context "http-server-test" :role "foundation"}}
  "Send sends in order over the connected ws and receive. If leave is given, close with a status code and reason after receiving replies messages; if not, receive until the listener closes."
  (import aiohttp [WSMsgType])
  (for [item request.sends]
    (.run-until-complete loop (if (isinstance item bytes) (.send-bytes ws item) (.send-str ws item))))
  (var texts #())
  (var closed None)
  (var reading True)
  (var message None)
  (while (and reading (not (and (is-not request.leave None) (>= (len texts) request.replies))))
    (:= message (.run-until-complete loop (.receive ws :timeout PEER-SECONDS)))
    (match message.type
      WSMsgType.TEXT (:= texts (+ texts #(message.data)))
      WSMsgType.CLOSE (do (:= closed #(message.data message.extra))
                          (:= reading False))
      _ (:= reading False)))
  (match request.leave
    #(code reason) :if reading (.run-until-complete loop (.close ws :code code :message (.encode reason "utf-8")))
    _ None)
  (.run-until-complete loop (.close ws))
  (PeerAnswer :status WS-OPEN-STATUS :headers #() :body b"" :texts texts :closed closed))


(defk visit-ws [loop session base request]
  {:pre [(: loop asyncio.AbstractEventLoop) (: session "aiohttp.ClientSession") (: base str) (: request PeerRequest)]
   :post [(: % PeerAnswer)] :tags {:context "http-server-test" :role "foundation"}}
  "Connect and talk over ws. If the handshake is refused, the answer is only the status of the refusal."
  (import aiohttp)
  (var ws None)
  (var refused None)
  (try
    (:= ws (.run-until-complete loop (.ws-connect session (+ base request.target))))
    (except [error aiohttp.WSServerHandshakeError]
      (:= refused error.status)))
  (if (is refused None)
      (do (<- talked PeerAnswer (talk-ws loop ws request))
          talked)
      (PeerAnswer :status refused :headers #() :body b"" :texts #() :closed None)))


(defk visit [address requests]
  {:pre [(: address HttpAddress) (: requests tuple)] :post [(: % tuple)] :tags {:context "http-server-test" :role "foundation"}}
  "The real peer: send requests one at a time, reading each answer before moving on (an event loop used only by this thread, and an aiohttp client)."
  (import aiohttp)
  (val base (.format "http://{}:{}" address.host address.port))
  (val loop (asyncio.new-event-loop))
  ;; Send each request on a new connection (so the next request does not reuse a connection the listener closed after answering a refusal at the limit).
  (val session (aiohttp.ClientSession :loop loop :connector (aiohttp.TCPConnector :loop loop :force-close True)))
  (var answers #())
  (try
    (for [request requests]
      (<- answer PeerAnswer (cond
                              request.ws (visit-ws loop session base request)
                              (or request.raw (is-not request.declared None)) (visit-raw address request)
                              True (visit-http loop session base request)))
      (:= answers (+ answers #(answer))))
    (finally
      (.run-until-complete loop (.close session))
      (.close loop)))
  answers)


(deff visit-in-thread [address requests outbox]  ; defk にできない: a callback that threading.Thread calls in a separate thread
  {:pre [(: address HttpAddress) (: requests tuple) (: outbox queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "Run the real peer and put the sequence of answers (or the exception on failure) in outbox."
  (try
    (.put outbox (run (visit address requests)))
    (except [error Exception]
      (.put outbox error)))
  None)


(defhandler live-peer
  ;; The real peer (header comment). outbox = the box where the running peer puts the sequence of answers (a session value -- made by PeerSend).
  (session var outbox None)
  (PeerSend [address requests]
    (val box (queue.Queue))
    (.start (threading.Thread :target visit-in-thread :args #(address requests box) :daemon True))
    (:= outbox box)
    (resume None))
  (PeerAnswers []
    (match outbox
      None (raise (RuntimeError "PeerAnswers was fired before PeerSend"))
      box (do (val got (.get box :timeout (* 3 PEER-SECONDS)))
              (if (isinstance got Exception)
                  (raise got)
                  (resume got))))))


(deff upstream-app [environ start-response]  ; defk にできない: a WSGI callback that wsgiref calls per request
  {:pre [(: environ dict) (: start-response Callable)] :post [(: % list)]
   :tags {:context "http-server-test" :role "foundation"}}
  "The real relay target: answers every request with UPSTREAM-STATUS and UPSTREAM-BODY."
  (start-response (.format "{} Upstream" UPSTREAM-STATUS) [#("Content-Type" "text/plain")
                                                          #("Content-Length" (str (len UPSTREAM-BODY)))])
  [UPSTREAM-BODY])


(defk vacant-port []
  {:pre [] :post [(: % int)] :tags {:context "http-server-test" :role "foundation"}}
  "A 127.0.0.1 port nobody listens on (bind it and close it right away)."
  (with [probe (socket.socket)]
    (.bind probe #("127.0.0.1" 0))
    (val port (get (.getsockname probe) 1)))
  port)


(defk under-aiohttp [program]
  {:pre [(: program Program)] :post [(: % "the answer of the contract Program (the type differs per Program)")]
   :tags {:context "http-server-test" :role "foundation"}}
  "Run program under the real aiohttp-http-server. site is a real file in a temporary dir; upstream is a wsgiref listener that listens only while running."
  (import doeff_core_effects [aiohttp-http-server])
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (val site (os.path.join (os.path.realpath directory) "app.js"))
    (with [handle (open site "wb")]
      (.write handle SITE-CONTENT))
    (val upstream (make-server "127.0.0.1" 0 upstream-app))
    (.start (threading.Thread :target upstream.serve-forever :daemon True))
    (<- dead int (vacant-port))
    (val world (World :site site :upstream (.format "http://127.0.0.1:{}" upstream.server-port) :dead (.format "http://127.0.0.1:{}" dead)))
    (try
      (<- ran (with_handlers [await-handler (state) (contract-world world) aiohttp-http-server live-peer] program))
      (:= answer ran)
      (finally
        (.shutdown upstream)
        (.server-close upstream))))
  answer)


;; --- The fake peer (script) ---------------------------------------------------------------------------------------------------------

(defk scripted-arrivals [requests first]
  {:pre [(: requests tuple) (: first int)] :post [(: % tuple)] :tags {:context "http-server-test" :role "judgment"}}
  "Turn the sequence of peer requests into script events and bodies (tickets are assigned in order from first -- the same as the real listener assigning them in arrival order). Answer = #(events bodies)."
  (var arrivals #())
  (var bodies #())
  (for [[index request] (enumerate requests)]
    (val ticket (str (+ first index)))
    (<- path str (path-of request.target))
    (<- headers tuple (sent-headers request))
    (:= arrivals (+ arrivals #((HttpRequestArrived :ticket ticket :method request.method :path path :target request.target
                                                   :headers headers :upgrade request.ws))))
    (when (is-not request.body None)
      (:= bodies (+ bodies #((ScriptedBody :ticket ticket :data request.body)))))
    (when request.ws
      (:= arrivals (+ arrivals (tuple (gfor item request.sends
                                            (if (isinstance item bytes)
                                                (WsBinaryArrived :ticket ticket :data item)
                                                (WsTextArrived :ticket ticket :text item))))))
      (match request.leave
        #(code reason) (:= arrivals (+ arrivals #((WsClosed :ticket ticket :code code :reason reason))))
        None None)))
  #(arrivals bodies))


(defk scripted-answer [served ticket request]
  {:pre [(: served tuple) (: ticket str) (: request PeerRequest)] :post [(: % PeerAnswer)]
   :tags {:context "http-server-test" :role "judgment"}}
  "From the script's record, read what the peer of the request with the ticket received (the script's HttpServed is \"the answer the terminal receives\")."
  (val told (lfor s served :if (and (isinstance s HttpServed) (= s.ticket ticket)) s))
  (when (!= (len told) 1)
    (raise (AssertionError (.format "there should be exactly 1 command for ticket {}: {!r}" ticket told))))
  (val answer (get told 0))
  (cond
    (and request.ws (= answer.status WS-OPEN-STATUS))
      (PeerAnswer :status answer.status :headers #() :body b""
                  :texts (tuple (gfor s served :if (and (isinstance s WsTextSent) (= s.ticket ticket)) s.text))
                  :closed (next (gfor s served :if (and (isinstance s WsCloseSent) (= s.ticket ticket)) #(s.code s.reason)) None))
    request.ws (PeerAnswer :status answer.status :headers #() :body b"" :texts #() :closed None)
    True (PeerAnswer :status answer.status
                     :headers (if (isinstance answer.command HttpRespond) answer.command.headers #())
                     :body (.encode answer.body "utf-8") :texts #() :closed None)))


(defhandler scripted-peer
  ;; The fake peer (header comment). asked = the sequence of requests sent so far (ticket = position + 1).
  (session var asked #())
  (PeerSend [address requests]
    (<- plan tuple (scripted-arrivals requests (+ (len asked) 1)))
    (<- (AppendHttpScript :arrivals (get plan 0) :bodies (get plan 1)))
    (:= asked (+ asked requests))
    (resume None))
  (PeerAnswers []
    (<- served tuple (ReadHttpServed))
    (var answers #())
    (for [[index request] (enumerate asked)]
      (<- answer PeerAnswer (scripted-answer served (str (+ index 1)) request))
      (:= answers (+ answers #(answer))))
    (resume answers)))


(val SCRIPTED-WORLD (World :site MEMORY-SITE :upstream SCRIPTED-UPSTREAM :dead SCRIPTED-DEAD))


(defk under-scripted [program]
  {:pre [(: program Program)] :post [(: % "the answer of the contract Program (the type differs per Program)")]
   :tags {:context "http-server-test" :role "foundation"}}
  "Run program under the fake scripted-http-server. From the outside in: state, memory file (site), world, script (events empty -- the peer
   adds them with PeerSend; the relay target is only SCRIPTED-UPSTREAM), peer."
  (val files (MemoryFiles :files #((MemoryFile :path MEMORY-SITE :content SITE-CONTENT)) :dirs #((os.path.dirname MEMORY-SITE))))
  (val script (HttpScript :arrivals #()
                          :upstreams #((ScriptedUpstream :base SCRIPTED-UPSTREAM :http-status UPSTREAM-STATUS :accepts-ws False))))
  (<- answer (with_handlers [(state) (memory-file-handler files) (contract-world SCRIPTED-WORLD) (scripted-http-server script) scripted-peer]
               program))
  answer)


(val INTERPRETERS {AIOHTTP under-aiohttp
                   SCRIPTED under-scripted})
