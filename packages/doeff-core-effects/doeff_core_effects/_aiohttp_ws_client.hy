;;; The real responder aiohttp-ws-client for the generic WebSocket client effects (ws_client_effects.hy). It does the real I/O with aiohttp's
;;; ClientSession.ws_connect: connecting, passing frames, sending close frames; it holds no decisions. aiohttp is a dependency of the extra `http-server`.
;;;
;;;   loop          The clauses enter await-handler's shared event loop with Await and run aiohttp coroutines on it (await-handler and state are needed
;;;                 outside the handler assembly -- the session values are held with the state effect). Mounted the same way as the HttpRequest responder in _http_handlers_impl.hy
;;;   client        One aiohttp ClientSession per connection (created inside the connecting coroutine = on the shared loop, and closed together with the connection
;;;                 when it closes). One client is never reused across scopes (#3415 -- never build a shape where the next scope uses a closed client). The maker is the installer's
;;;                 client-factory (default new-client-session -- the check passes a maker that counts the clients it made)
;;;   concurrency   Several connections proceed at once under the same handler (the voice service connects per session). The connection tables (open, ended) are
;;;                 one dict in the session values; clauses refer to the same table after an Await, and never put an await between reading it and writing it. The id number is
;;;                 reserved before the connect starts (before the await) and is not given back even if refused -- if it were incremented after the await, two concurrent connects would read the same number
;;;   WsConnect     ws_connect (headers are the handshake request headers). The limit up to connecting is CONNECT-SECONDS; the limit for waiting for the 101 answer is HANDSHAKE-SECONDS.
;;;                 If the handshake is refused, WsConnectFailed (status = the refusing status); if it does not arrive, WsConnectFailed (status None). When leaving
;;;                 without the answer being decided (CancelledError -- scheduler Cancel, losing a Race), the client and ws are closed too and not left open.
;;;                 The limit for one message is MAX-MESSAGE-BYTES (aiohttp drops the connection on a longer message -> WsLinkClosed)
;;;   WsReceive     Wait for the next frame: TEXT -> WsText, BINARY -> WsBinary, CLOSE (the peer's close frame) -> WsLinkClosed with its status code and reason,
;;;                 CLOSED / ERROR (dropped without a close frame) -> WsLinkClosed with the connection's status code (1006 etc., None if absent). For ping, aiohttp's
;;;                 autoping returns a pong (even while waiting here). A finished connection returns its WsLinkClosed once more; an unknown id gets closed-answer
;;;   WsSend        send_str. A send after the peer has closed (a transport that cannot be written) is WsLinkClosed
;;;   WsDisconnect  Send a close frame, wait up to CLOSE-SECONDS for the peer's close, then close (aiohttp's ws_close). The status code and reason in the answer are the ones passed in
;;;   WsDisconnectAll Do the same as WsDisconnect to every open connection (cleanup at the end of the responder's scope -- issued by the installer's closing-links)
;;; The reason text (WsConnectFailed.reason, WsLinkClosed.reason) is only the exception type name and status (failure-detail). The exception text may carry the request url (including the query) or
;;; headers (the Bearer value of Authorization), so it is not included -- the boundary of the protocol record (fixed by tests/test_ws_client.hy).
;;; Why the real responder has no heartbeat (sending ping itself): the peer (OpenAI's sideband) sends ping and we answer with autoping.
;;; If a peer that needs us to send ping appears, add it as a WsConnect field (design note).
;;; What the real one and the script decide the same way call the decision defk in ws_client_effects.hy (the contract test is tests/test_ws_client_contract.hy).
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "ws-client" :role "foundation"})
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import aiohttp)
(import aiohttp [WSMsgType ClientWSTimeout])
(import doeff [EffectBase Program])
(import doeff_core_effects._effects [Await])
(import doeff_core_effects._http_server_effects [HttpHeader])
(import doeff_core_effects._ws_client_effects [WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll WsLink WsConnectFailed WsText WsBinary
                                              WsLinkClosed WsFrame WsSent link-name closed-answer closing-links])

;; The limit up to connecting (name lookup and TCP connect) and the limit for waiting for the handshake answer (101) after connecting (seconds) -- the same two-way split as the listening ws relay.
(val CONNECT-SECONDS 10.0)
(val HANDSHAKE-SECONDS 15.0)
;; The limit for waiting for the peer's close after sending the close frame (seconds -- aiohttp's ws_close).
(val CLOSE-SECONDS 10.0)
;; The limit for one message (bytes) -- aiohttp drops the connection on a longer message.
(val MAX-MESSAGE-BYTES (* 4 1024 1024))

;; The effects it answers and the effects the clauses issue (the installer's declaration -- __doeff_handles__ / __doeff_effects__ of doeff-effect-analyzer).
(val AIOHTTP-WS-HANDLES #(WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll))
(val AIOHTTP-WS-EFFECTS #(Await))


(defrecord OpenLink
  "One open connection: link = id, url, session = the aiohttp client for this connection only, ws = the connected socket."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str url)
  (#^ aiohttp.ClientSession session)
  (#^ aiohttp.ClientWebSocketResponse ws))


(defn #^ aiohttp.ClientSession new-client-session []  ; cannot be a defk: a client maker called inside the connecting coroutine (on the running event loop) (the default of client-factory)
  "To make the aiohttp client for one connection: it holds the limit up to connecting and the limit for the handshake answer, and sets no total (a connected ws is not cut by time)."
  (aiohttp.ClientSession :timeout (aiohttp.ClientTimeout :total None :connect CONNECT-SECONDS :sock-read HANDSHAKE-SECONDS)))


(defn #^ str failure-detail [#^ BaseException error]  ; cannot be a defk: called inside an aiohttp coroutine (outside a Program)
  "To decide the reason text for a failed connect / a drop: only the exception type name and, if there is an answer, its status (the protocol record boundary in the opening note -- the exception text may carry the request
   url or headers, so it is not included. Not used for branching)."
  (if (isinstance error aiohttp.ClientResponseError)
      (.format "{}: status {}" (. (type error) __name__) error.status)
      (. (type error) __name__)))


(defn :async #^ (| OpenLink WsConnectFailed) open-link [#^ (get Callable #([] aiohttp.ClientSession)) client-factory #^ str link #^ str url
                                                         #^ (get tuple #(HttpHeader ...)) headers]  ; cannot be a defk: real aiohttp I/O (a coroutine on the shared event loop)
  "To connect to url (WsConnect in the opening note): make a client for this connection only and ws_connect. WsConnectFailed when refused or unreachable.
   When leaving without the answer being decided as an OpenLink (refused, unreachable, CancelledError), ws and client are closed in finally and not left open."
  (setv session (client-factory))
  (setv ws None)
  (setv outcome None)
  (try
    (setv ws (await (.ws-connect session url :headers (dfor h headers h.name h.value) :max-msg-size MAX-MESSAGE-BYTES
                                 :timeout (ClientWSTimeout :ws-receive None :ws-close CLOSE-SECONDS))))
    (setv outcome (OpenLink :link link :url url :session session :ws ws))
    (except [error aiohttp.WSServerHandshakeError]
      (setv outcome (WsConnectFailed :url url :reason (failure-detail error) :status error.status)))
    (except [error #(aiohttp.ClientError OSError asyncio.TimeoutError)]
      (setv outcome (WsConnectFailed :url url :reason (failure-detail error) :status None)))
    (finally
      (when (not (isinstance outcome OpenLink))
        (when (is-not ws None)
          (await (.close ws)))
        (await (.close session)))))
  outcome)


(defn :async #^ WsFrame receive-frame [#^ OpenLink open]  ; cannot be a defk: real aiohttp I/O (a coroutine on the shared event loop)
  "To wait for the next frame of the connection with the id (WsReceive in the opening note). Wait for the next one during ping / pong / the middle of closing."
  (while True
    (setv message (await (.receive open.ws)))
    (match message.type
      WSMsgType.TEXT (return (WsText :link open.link :text message.data))
      WSMsgType.BINARY (return (WsBinary :link open.link :data message.data))
      WSMsgType.CLOSE (return (WsLinkClosed :link open.link :code (int message.data) :reason (or message.extra "")))
      WSMsgType.CLOSED (return (WsLinkClosed :link open.link :code (lost-code open.ws) :reason ""))
      WSMsgType.ERROR (return (WsLinkClosed :link open.link :code (lost-code open.ws) :reason (failure-detail message.data)))
      _ None)))


(defn #^ (| int None) lost-code [#^ aiohttp.ClientWebSocketResponse ws]  ; cannot be a defk: real I/O reading the state of the aiohttp socket
  "To read the status code of a connection that ended without a close frame (the close_code aiohttp set -- None if absent)."
  (if (is ws.close-code None) None (int ws.close-code)))


(defn :async #^ (| WsSent WsLinkClosed) send-text [#^ OpenLink open #^ str text]  ; cannot be a defk: real aiohttp I/O (a coroutine on the shared event loop)
  "To send one text message to the connection with the id (WsSend in the opening note). A closed or unwritable connection is WsLinkClosed."
  (when open.ws.closed
    (return (WsLinkClosed :link open.link :code (lost-code open.ws) :reason "")))
  (try
    (await (.send-str open.ws text))
    (except [error #(aiohttp.ClientError ConnectionResetError RuntimeError)]
      (return (WsLinkClosed :link open.link :code (lost-code open.ws) :reason (failure-detail error)))))
  (WsSent :link open.link))


(defn :async #^ WsLinkClosed close-link [#^ OpenLink open #^ int code #^ str reason]  ; cannot be a defk: real aiohttp I/O (a coroutine on the shared event loop)
  "To close the connection with the id with a status code and reason (WsDisconnect in the opening note): send a close frame, wait up to CLOSE-SECONDS for the peer's close, and close the client too."
  (try
    (await (.close open.ws :code code :message (.encode reason "utf-8")))
    (except [#(aiohttp.ClientError ConnectionResetError RuntimeError asyncio.TimeoutError)] None)
    (finally
      (await (.close open.session))))
  (WsLinkClosed :link open.link :code code :reason reason))


(defn :async #^ None drop-link [#^ OpenLink open]  ; cannot be a defk: real aiohttp I/O (a coroutine on the shared event loop)
  "To close the client of a finished connection (cleanup after the peer closed / it dropped)."
  (await (.close open.session))
  None)


(defhandler aiohttp-ws-link-handler [#^ (get Callable #([] aiohttp.ClientSession)) client-factory]
  "Answers WsConnect, WsReceive, WsSend, WsDisconnect, WsDisconnectAll with real aiohttp WebSocket I/O (the head note). The closing-links that closes
   connections at the end of the scope is wrapped on by the installer aiohttp-ws-client. await-handler and state are needed outside this set."
  ;; The real connections (opening note). open = the table of open connections (id -> OpenLink), ended = the table of answers for finished connections (id -> WsLinkClosed -- WsReceive /
  ;; WsSend after closing return the same answer). Both are one dict in the session values: clauses refer to the same table after an Await, and put no await between
  ;; reading and writing it (with a tuple in a session var, the value taken at the start of the clause would be written back after the await, erasing the writes of concurrent clauses). counter = the number of ids handed out (a session var
  ;; -- incremented before the await, so two concurrent WsConnect do not read the same number).
  ;; Why it stays an argument: the client maker is decided on the assembly side (the check passes a maker that counts the clients it made).
  (session val open ((get dict #(str OpenLink))))
  (session val ended ((get dict #(str WsLinkClosed))))
  (session var counter 0)
  (WsConnect [url headers]
    ;; Reserve the number before the connect starts and do not give it back even if refused (if incremented after the await, two concurrent connects take the same number -- the scripted responder has the same order).
    (:= counter (+ counter 1))
    (<- link str (link-name counter))
    (<- opened (| OpenLink WsConnectFailed) (Await (open-link client-factory link url headers)))
    (match opened
      (OpenLink) (do (setv (get open link) opened)
                     (resume (WsLink :link link :url url)))
      _ (resume opened)))
  (WsReceive [link]
    (match (.get open link)
      None (do (<- gone WsLinkClosed (closed-answer (tuple (.values ended)) link))
               (resume gone))
      found (do (<- frame WsFrame (Await (receive-frame found)))
                (match frame
                  (WsLinkClosed) (do (<- (Await (drop-link found)))
                                     (.pop open link None)
                                     (setv (get ended link) frame))
                  _ None)
                (resume frame))))
  (WsSend [link text]
    (match (.get open link)
      None (do (<- gone WsLinkClosed (closed-answer (tuple (.values ended)) link))
               (resume gone))
      found (do (<- outcome (| WsSent WsLinkClosed) (Await (send-text found text)))
                (match outcome
                  (WsLinkClosed) (do (<- (Await (drop-link found)))
                                     (.pop open link None)
                                     (setv (get ended link) outcome))
                  _ None)
                (resume outcome))))
  (WsDisconnect [link code reason]
    (match (.get open link)
      None (do (<- gone WsLinkClosed (closed-answer (tuple (.values ended)) link))
               (resume gone))
      found (do (<- closed WsLinkClosed (Await (close-link found code reason)))
                (.pop open link None)
                (setv (get ended link) closed)
                (resume closed))))
  (WsDisconnectAll [code reason]
    (var closing #())
    (for [found (tuple (.values open))]
      (<- closed WsLinkClosed (Await (close-link found code reason)))
      (.pop open found.link None)
      (setv (get ended found.link) closed)
      (:= closing (+ closing #(closed))))
    (resume closing)))


(deff aiohttp-ws-client [* [client-factory new-client-session]]  ; cannot be a defk: called outside a Program, the maker of the installer that wraps the responder on
  {:pre [(: client-factory (get Callable #([] aiohttp.ClientSession)))] :post [(: % (of Callable [(| Program EffectBase)] Program))] :tags {:context "ws-client" :role "foundation"}}
  "To make the installer of the real responder (opening note): wrap the program it is put over with closing-links, closing connections left open at the end of the scope.
   client-factory = the per-connection client maker (default new-client-session)."
  (setv install (aiohttp-ws-link-handler client-factory))
  (setv closing (fn [#^ (| Program EffectBase) program] (install (closing-links program))))
  ;; The marker of the wrapping function (set the same attributes defhandler sets -- with_handlers refuses a Program -> Program function without the marker), and the declarations.
  (setattr closing "_doeff_is_handler_fn" True)
  (setattr closing "__doeff_name__" "aiohttp-ws-client")
  (setattr closing "__doeff_handler_data__" (getattr install "__doeff_handler_data__"))
  (setattr closing "__doeff_handles__" AIOHTTP-WS-HANDLES)
  (setattr closing "__doeff_effects__" AIOHTTP-WS-EFFECTS)
  closing)


;; The installer maker cannot statically read its clauses, so it declares the effects it answers and the effects it issues (so the check for the closedness of the production base does not count it as an "unreadable handler"
;; -- the same as http-production-handler in _http_handlers_impl.hy).
(setattr aiohttp-ws-client "__doeff_handles__" AIOHTTP-WS-HANDLES)
(setattr aiohttp-ws-client "__doeff_effects__" AIOHTTP-WS-EFFECTS)
