;;; A generic WebSocket client (connecting side) effect (consumer = the voice service attaching to OpenAI's sideband WebSocket).
;;; A foundation vocabulary paired with the listening side (WsOpened / WsTextArrived / WsClosed ... in http_server_effects.hy), with no business terms.
;;; Names do not collide with the listening side (WsConnect / WsReceive / WsSend / WsDisconnect and WsLink / WsText / WsBinary / WsLinkClosed).
;;; Handlers are swapped per mechanism:
;;;   aiohttp-ws-client   real (aiohttp_ws_client.hy -- aiohttp is a dependency of the extra `http-server`)
;;;   scripted-ws-client  no I/O -- answers with a scripted sequence of frames and records the text sent (scripted_ws_client.hy)
;;;
;;;   WsConnect        Connect to url (headers = the list of headers put on the handshake request -- Authorization etc.). Answer = WsConnectOutcome:
;;;                      WsLink(link, url)                  Connected. link = the connection id assigned by the handler (later effects point at the connection by this id). Numbers follow the order connects were started (ws-1, ws-2 ...),
;;;                                                          and the number of a refused connect is not given back -- so that concurrent connects do not take the same number (the number is reserved before the connect starts)
;;;                      WsConnectFailed(url, reason, status) Could not connect. status = the status that refused the handshake (None if it never arrived). reason is
;;;                                                          text for humans and logs, and contains neither request header values nor the url query (recording boundary)
;;;   WsReceive        Wait for the next frame of the connection id. Answer = WsFrame:
;;;                      WsText(link, text)                  one text message
;;;                      WsBinary(link, data)                one binary message
;;;                      WsLinkClosed(link, code, reason)    the connection ended (for the peer's close, its status code and reason; for our WsDisconnect, the
;;;                                                          status code and reason we passed; for a drop, 1006 etc. and an empty reason; None if there is no status code).
;;;                                                          A WsReceive after the end is the same WsLinkClosed. An unknown id gives reason = UNKNOWN-LINK-REASON
;;;   WsSend           Send one text message to the connection id. Answer = WsSendOutcome: WsSent(link), or WsLinkClosed for an ended connection
;;;   WsDisconnect     Close the connection id with a status code and reason (sends a close frame). Answer = WsLinkClosed (the status code and reason passed). For an ended connection or an unknown id,
;;;                    the WsLinkClosed of that connection (not closed again)
;;;   WsDisconnectAll  Close all open connections with a status code and reason. Answer = the list of WsLinkClosed for the closed connections (in the order opened). Used by the cleanup at the end of the handler's scope
;;;                    (closing-links -- run by the handler's installer in the program's finally). Callers may also issue it
;;; Answers are values, not exceptions -- callers cover them exhaustively with match.
;;; What both handlers decide the same way is decided by calling the defk judgments here from both: link-name (spelling of the id) and closed-answer (the answer to an ended or unknown id).
;;; That the two have the same properties is checked against both handlers by the contract tests in tests/test_ws_client_contract.hy.
;;;
;;; Script vocabulary (not in the real handler): WsScript, ScriptedWsEndpoint, ScriptedWsReply, ScriptedWsText / ScriptedWsBinary / ScriptedWsClosed =
;;; the script; WsSentText / WsSentClose = records of text and closes sent; ReadWsSent = the effect that reads the records (so tests and scenarios can peek).
(require doeff-hy.macros [defeffect defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import doeff [EffectBase Program])
(import doeff_core_effects._http_server_effects [HttpHeader])
(import doeff_core_effects._scheduler [shielded])

;; Status codes for ws close (RFC 6455 7.4.1) -- values decided by the handler.
(val WS-LINK-CLOSE-NORMAL 1000)
(val WS-LINK-CLOSE-GOING-AWAY 1001)
(val WS-LINK-CLOSE-ABNORMAL 1006)
;; Reason for the answer to an unknown id (a value decided by the handler).
(val UNKNOWN-LINK-REASON "unknown-link")
;; Reason for the answer to a connection whose script frames ran out (a value decided by the scripted handler).
(val SCRIPT-EXHAUSTED-REASON "script-exhausted")
;; Reason for the answer to a url not in the script (a value decided by the scripted handler).
(val NOT-IN-SCRIPT-REASON "url not in script")
;; The reason used when closing connections left open at the end of the handler's scope (attached by closing-links).
(val SCOPE-ENDED-REASON "handler scope ended")


(defrecord WsLink
  "Answer to WsConnect: connected. link = the connection id assigned by the handler; url = the url connected to."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str url))


(defrecord WsConnectFailed
  "Answer to WsConnect: could not connect. reason = text for humans and logs (not used for branching); status = the status that refused the handshake (None if
   it never arrived)."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str url)
  (#^ str reason)
  (#^ (| int None) status))


(val WsConnectOutcome (| WsLink WsConnectFailed))


(defrecord WsText
  "Answer to WsReceive: one text message arrived on the connection id."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str text))


(defrecord WsBinary
  "Answer to WsReceive: one binary message arrived on the connection id."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ bytes data))


(defrecord WsLinkClosed
  "The connection id ended (see the header description): code = close status code (None if absent); reason = reason text."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ (| int None) code)
  (#^ str reason))


(val WsFrame (| WsText WsBinary WsLinkClosed))


(defrecord WsSent
  "Answer to WsSend: sent to the connection id."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link))


(val WsSendOutcome (| WsSent WsLinkClosed))


(defeffect WsConnect
  "Connect to url (see the header description). headers = the list of headers put on the handshake request. Answer = WsLink | WsConnectFailed."
  {:fields [(: url str) (: headers (get tuple #(HttpHeader ...)) #())]
   :answer (| WsLink WsConnectFailed)
   :tags {:context "ws-client" :role "foundation"}})


(defeffect WsReceive
  "Wait for the next frame of the connection id (see the header description). Answer = WsText | WsBinary | WsLinkClosed."
  {:fields [(: link str)]
   :answer (| WsText WsBinary WsLinkClosed)
   :tags {:context "ws-client" :role "foundation"}})


(defeffect WsSend
  "Send one text message to the connection id (see the header description). Answer = WsSent | WsLinkClosed."
  {:fields [(: link str) (: text str)]
   :answer (| WsSent WsLinkClosed)
   :tags {:context "ws-client" :role "foundation"}})


(defeffect WsDisconnect
  "Close the connection id with a status code and reason (see the header description). Answer = WsLinkClosed."
  {:fields [(: link str) (: code int WS-LINK-CLOSE-NORMAL) (: reason str "")]
   :answer WsLinkClosed
   :tags {:context "ws-client" :role "foundation"}})


(defeffect WsDisconnectAll
  "Close all open connections with a status code and reason (see the header description). Answer = the list of WsLinkClosed for the closed connections (in the order opened)."
  {:fields [(: code int WS-LINK-CLOSE-GOING-AWAY) (: reason str SCOPE-ENDED-REASON)]
   :answer (get tuple #(WsLinkClosed ...))
   :tags {:context "ws-client" :role "foundation"}})


;; --- Judgments both handlers call (the real aiohttp-ws-client and the scripted scripted-ws-client decide with the same function) ----------------------------------

(defk link-name [n]
  {:pre [(: n int)] :post [(: % str)] :tags {:context "ws-client" :role "judgment"}}
  "Decides the spelling of the id of the n-th connection (from 1 in the order connected)."
  (.format "ws-{}" n))


(defk closed-answer [ended link]
  {:pre [(: ended (of tuple WsLinkClosed ...)) (: link str)] :post [(: % WsLinkClosed)] :tags {:context "ws-client" :role "judgment"}}
  "Decides the answer to an id that is not open (see the header description): for an ended connection (ended), the WsLinkClosed from then once more; for an unknown id, without a status code,
   UNKNOWN-LINK-REASON."
  (val known (next (gfor closed ended :if (= closed.link link) closed) None))
  (if (is known None)
      (WsLinkClosed :link link :code None :reason UNKNOWN-LINK-REASON)
      known))


(defk closing-links [program]
  {:pre [(: program (| Program EffectBase))] :post [(: % "program answer (type varies per program)")]
   :tags {:context "ws-client" :role "foundation"}}
  "Runs the program and, at the end (by answer or exception), closes all connections left open (the cleanup of the handler scope in the header description -- the handler's installer
   wraps the program with this and then applies it)."
  (try
    (<- answer program)
    answer
    (finally
      ;; shielded: waiting in a cancelled task is cancelled again, so enclose the close and wait it out
      (<- (shielded (WsDisconnectAll))))))


;; --- Script vocabulary (scripted-ws-client) ----------------------------------------------------------------------------------------------

(defrecord ScriptedWsText
  "Script frame: one text message arrives."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str text))


(defrecord ScriptedWsBinary
  "Script frame: one binary message arrives."
  {:tags {:context "ws-client" :role "type"}}
  (#^ bytes data))


(defrecord ScriptedWsClosed
  "Script frame: the peer closes with a status code and reason."
  {:tags {:context "ws-client" :role "type"}}
  (#^ int code)
  (#^ str reason))


(val ScriptedWsFrame (| ScriptedWsText ScriptedWsBinary ScriptedWsClosed))


(defrecord ScriptedWsReply
  "One script of frames that arrive in response to sends: contains = if the text sent contains this word; frames = the frames that arrive after that (appended behind the arriving sequence)."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str contains)
  (#^ (get tuple #(ScriptedWsFrame ...)) frames))


(defrecord ScriptedWsEndpoint
  "One script endpoint: url = a WsConnect to a url starting with this string accepts this script (longest match); frames = the frames that arrive on the connection made;
   replies = frames that arrive in response to sends (a word contained in the text sent -> a sequence of frames); then-close = the peer's close after the frames run out (if None,
   a WsLinkClosed with SCRIPT-EXHAUSTED-REASON and no status code -- so the caller's Program is not left waiting)."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str url)
  (setv #^ (get tuple #(ScriptedWsFrame ...)) frames #()
        #^ (get tuple #(ScriptedWsReply ...)) replies #()
        #^ (| ScriptedWsClosed None) then-close None))


(defrecord WsScript
  "The scripted-ws-client script: a sequence of endpoints. A WsConnect to a url not in it is WsConnectFailed."
  {:tags {:context "ws-client" :role "type"}}
  (#^ (get tuple #(ScriptedWsEndpoint ...)) endpoints))


(defrecord WsSentText
  "One text message sent to the connection id (script record)."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str text))


(defrecord WsSentClose
  "A close sent to the connection id (script record -- WsDisconnect, WsDisconnectAll)."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ int code)
  (#^ str reason))


(val WsSentRecord (| WsSentText WsSentClose))


(defeffect ReadWsSent
  "Reads the records of text and closes sent by the scripted handler (a tuple of WsSentText and WsSentClose in the order sent) (see the header description)."
  {:answer (get tuple #((| WsSentText WsSentClose) ...))
   :tags {:context "ws-client" :role "foundation"}})
