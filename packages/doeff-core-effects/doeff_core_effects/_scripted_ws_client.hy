;;; scripted-ws-client, the I/O-free responder for the generic WebSocket client effects (ws_client_effects.hy). It opens no real
;;; socket; it answers from a script (WsScript - the list of frames that arrive per connect target, and the frames that arrive in response to sends), and records the text sent and the closes. It has no
;;; knowledge of the business domain: the contents of the script are supplied by the caller.
;;;
;;;   WsConnect        If the script has a matching url (longest prefix match of the url), WsLink (id is ws-1, ws-2, ... in the order connects were started - the number of a refused connect is not given back),
;;;                    otherwise WsConnectFailed (status None)
;;;   WsReceive        Answers the next frame of the script. When exhausted, a WsLinkClosed with the code and reason of then-close if present, otherwise a WsLinkClosed with no code and
;;;                    SCRIPT-EXHAUSTED-REASON (never leaves the caller's Program waiting forever). For an ended or unknown id, closed-answer
;;;   WsSend           Records the text sent (WsSentText), and appends to the back of the pending list the frames of the replies in the script whose keyword the sent text contains (simulates the sideband
;;;                    append -> ACK round trip). For an ended or unknown id, closed-answer
;;;   WsDisconnect     Records the close (WsSentClose) and ends the connection (later WsReceive / WsSend get the same WsLinkClosed)
;;;   WsDisconnectAll  Does the same to all open connections
;;;   ReadWsSent       The record of the text sent and the closes (a tuple of WsSentText and WsSentClose)
;;; Whatever is decided the same way as in the real responder calls the judgment defk of ws_client_effects.hy (the contract test is tests/test_ws_client_contract.hy).
;;; The public installer scripted-ws-client wraps the program in closing-links (WsDisconnectAll at the end of the scope) before installing - the same cleanup as the real one.
;;; Ordering: a state handler holding the session values (state in doeff_core_effects) is needed outside this handler.
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "ws-client" :role "foundation"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import doeff [EffectBase Program])
(import doeff_core_effects._ws_client_effects [WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll ReadWsSent WsLink WsConnectFailed
                                              WsText WsBinary WsLinkClosed WsFrame WsSent WsScript ScriptedWsEndpoint ScriptedWsText
                                              ScriptedWsBinary ScriptedWsClosed ScriptedWsFrame WsSentText WsSentClose
                                              NOT-IN-SCRIPT-REASON SCRIPT-EXHAUSTED-REASON link-name closed-answer closing-links])

;; The effects answered and the effects the clauses emit (the installer's declaration - __doeff_handles__ / __doeff_effects__ of doeff-effect-analyzer).
(val SCRIPTED-WS-HANDLES #(WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll ReadWsSent))
(val SCRIPTED-WS-EFFECTS #())


(defrecord ScriptedLink
  "One connection open on the script: link = id, url, endpoint = the script of the connect target that matched, pending = the list of frames not yet delivered."
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str url)
  (#^ ScriptedWsEndpoint endpoint)
  (#^ (get tuple #(ScriptedWsFrame ...)) pending))


(defk endpoint-for [script url]
  {:pre [(: script WsScript) (: url str)] :post [(: % (| ScriptedWsEndpoint None))] :tags {:context "ws-client" :role "judgment"}}
  "To choose the script's connect target that receives a WsConnect to url (longest prefix match of the url - None if absent = not in the script)."
  (val hits (sorted (gfor e script.endpoints :if (.startswith url (.rstrip e.url "/")) e) :key (fn [e] (len e.url))))
  (if hits (get hits -1) None))


(defk link-of [links link]
  {:pre [(: links (of tuple ScriptedLink ...)) (: link str)] :post [(: % (| ScriptedLink None))]}
  "To look up the connection with the given id from the list of open connections (None if absent)."
  (next (gfor open links :if (= open.link link) open) None))


(defk without-link [links link]
  {:pre [(: links (of tuple ScriptedLink ...)) (: link str)] :post [(: % (of tuple ScriptedLink ...))]}
  "To remove the connection with the given id from the list of open connections."
  (tuple (gfor open links :if (!= open.link link) open)))


(defk frame-of [link scripted]
  {:pre [(: link str) (: scripted (| ScriptedWsText ScriptedWsBinary ScriptedWsClosed))] :post [(: % WsFrame)]
   :tags {:context "ws-client" :role "judgment"}}
  "To turn one frame of the script into a frame delivered to the connection with the given id."
  (match scripted
    (ScriptedWsText :text text) (WsText :link link :text text)
    (ScriptedWsBinary :data data) (WsBinary :link link :data data)
    (ScriptedWsClosed :code code :reason reason) (WsLinkClosed :link link :code code :reason reason)))


(defk next-frame [open]
  {:pre [(: open ScriptedLink)] :post [(: % WsFrame)] :tags {:context "ws-client" :role "judgment"}}
  "To decide the next frame of the connection with the given id (the opening description): the head of the undelivered frames if any, and once exhausted, then-close or the WsLinkClosed of SCRIPT-EXHAUSTED-REASON
   as a WsLinkClosed."
  (match open.pending
    #() (match open.endpoint.then-close
          None (WsLinkClosed :link open.link :code None :reason SCRIPT-EXHAUSTED-REASON)
          closing (! (frame-of open.link closing)))
    _ (! (frame-of open.link (get open.pending 0)))))


(defk replies-for [endpoint text]
  {:pre [(: endpoint ScriptedWsEndpoint) (: text str)] :post [(: % (of tuple ScriptedWsFrame ...))]
   :tags {:context "ws-client" :role "judgment"}}
  "To decide the list of frames that arrive in response to the sent text (of the replies in the script, those whose keyword the sent text contains, in script order - all concatenated)."
  (tuple (gfor reply endpoint.replies :if (in reply.contains text) frame reply.frames frame)))


(defhandler scripted-ws-link-handler [#^ WsScript script]
  "Answers WsConnect, WsReceive, WsSend, WsDisconnect, WsDisconnectAll and ReadWsSent with the caller's script WsScript, without I/O (head note).
   closing-links, which closes connections at the end of the scope, is wrapped on by the installer scripted-ws-client."
  ;; The script's connections (the opening description). links = the list of open connections, ended = the list of WsLinkClosed of ended connections (a WsReceive / WsSend after close returns the same answer
  ;; ), sent = the record of text sent and closes, counter = the number of ids handed out (all are session values).
  ;; Why it stays an argument: script is a frozen script (connect targets and the frames that arrive) assembled by the caller, and nobody swaps it outside assembly.
  (session var links #())
  (session var ended #())
  (session var sent #())
  (session var counter 0)
  (WsConnect [url headers]
    ;; The number is taken before the connect starts and is not given back even if refused (the same order as the real responder - because in the real one two connects progressing concurrently must not take the same number).
    (:= counter (+ counter 1))
    (<- link str (link-name counter))
    (<- endpoint (| ScriptedWsEndpoint None) (endpoint-for script url))
    (match endpoint
      None (resume (WsConnectFailed :url url :reason NOT-IN-SCRIPT-REASON :status None))
      found (do (:= links (+ links #((ScriptedLink :link link :url url :endpoint found :pending found.frames))))
                (resume (WsLink :link link :url url)))))
  (WsReceive [link]
    (<- open (| ScriptedLink None) (link-of links link))
    (match open
      None (do (<- gone WsLinkClosed (closed-answer ended link))
               (resume gone))
      _ (do (<- frame WsFrame (next-frame open))
            (<- rest (of tuple ScriptedLink ...) (without-link links link))
            (match frame
              (WsLinkClosed) (do (:= links rest)
                                 (:= ended (+ ended #(frame))))
              _ (:= links (+ rest #((ScriptedLink :link link :url open.url :endpoint open.endpoint :pending (cut open.pending 1 None))))))
            (resume frame))))
  (WsSend [link text]
    (<- open (| ScriptedLink None) (link-of links link))
    (match open
      None (do (<- gone WsLinkClosed (closed-answer ended link))
               (resume gone))
      _ (do (:= sent (+ sent #((WsSentText :link link :text text))))
            (<- replies (of tuple ScriptedWsFrame ...) (replies-for open.endpoint text))
            (<- rest (of tuple ScriptedLink ...) (without-link links link))
            (:= links (+ rest #((ScriptedLink :link link :url open.url :endpoint open.endpoint :pending (+ open.pending replies)))))
            (resume (WsSent :link link)))))
  (WsDisconnect [link code reason]
    (<- open (| ScriptedLink None) (link-of links link))
    (match open
      None (do (<- gone WsLinkClosed (closed-answer ended link))
               (resume gone))
      _ (do (val closed (WsLinkClosed :link link :code code :reason reason))
            (:= sent (+ sent #((WsSentClose :link link :code code :reason reason))))
            (<- rest (of tuple ScriptedLink ...) (without-link links link))
            (:= links rest)
            (:= ended (+ ended #(closed)))
            (resume closed))))
  (WsDisconnectAll [code reason]
    (val closing (tuple (gfor open links (WsLinkClosed :link open.link :code code :reason reason))))
    (:= sent (+ sent (tuple (gfor open links (WsSentClose :link open.link :code code :reason reason)))))
    (:= ended (+ ended closing))
    (:= links #())
    (resume closing))
  (ReadWsSent []
    (resume sent)))


(deff scripted-ws-client [#^ WsScript script]  ; cannot be a defk: it is the maker of an installer that installs a responder, called outside a Program
  {:pre [(: script WsScript)] :post [(: % (of Callable [(| Program EffectBase)] Program))] :tags {:context "ws-client" :role "foundation"}}
  "To build the installer of the script responder (the opening description): wrap the installed program in closing-links and close the connections left open at the end of the scope."
  (setv install (scripted-ws-link-handler script))
  (setv closing (fn [#^ (| Program EffectBase) program] (install (closing-links program))))
  ;; The marker of the installing function (the same attributes defhandler sets - with_handlers refuses a Program -> Program function without the marker), and the declaration.
  (setattr closing "_doeff_is_handler_fn" True)
  (setattr closing "__doeff_name__" "scripted-ws-client")
  (setattr closing "__doeff_handler_data__" (getattr install "__doeff_handler_data__"))
  (setattr closing "__doeff_handles__" SCRIPTED-WS-HANDLES)
  (setattr closing "__doeff_effects__" SCRIPTED-WS-EFFECTS)
  closing)


;; This is the maker of an installer whose clauses cannot be read statically, so it declares the effects it answers and emits (so that the production foundation's closedness check does not count it as an "unreadable handler" -
;; the same as http-production-handler in _http_handlers_impl.hy).
(setattr scripted-ws-client "__doeff_handles__" SCRIPTED-WS-HANDLES)
(setattr scripted-ws-client "__doeff_effects__" SCRIPTED-WS-EFFECTS)
