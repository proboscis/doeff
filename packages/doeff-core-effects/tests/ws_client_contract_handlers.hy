;;; Interpreter (composition root) for the WebSocket client contract tests - runs the same contract Program with only the client handler swapped.
;;;
;;;   aiohttp-ws-client    real: aiohttp-ws-client. The peer is an aiohttp ws listener that listens in a loop on another thread of the same process (on a free port of 127.0.0.1 -
;;;                        it does not go out to the external network)
;;;   scripted-ws-client   fake: scripted-ws-client (no I/O - a script). The peer's behavior is expressed by the script's frame and replies
;;;
;;; The contract world (answer of ContractWsWorld - WsWorld): url = the peer's connect target, slow = a connect target whose handshake is delayed by SLOW-SECONDS (for the test of concurrently progressing connects -
;;; same behavior as url in the script), dead = an unreachable connect target, install = the installer of this interpreter's answerer (so that a test can cut an inner scope and see the cleanup at the end of the scope).
;;; The peer's behavior is prepared in the same shape for each interpreter:
;;;   * on connect, one message "hello <value of the X-Contract header>" arrives (the contract Program carries the header X-Contract: a - the script answers "hello a")
;;;   * "bin" gets one byte message PEER-BINARY, "bye" gets a close with status code PEER-BYE-CODE and reason PEER-BYE-REASON, "hi" gets "echo:hi"
;;; The maker client-into, which counts the real answerer's clients (puts the made client in a box - so that a test can read that the client of a Cancelled connect is closed).
;;; Closes the peer saw (answer of PeerClosures - a sequence of PeerClose): real = close frames the listener received (waits for count of them to arrive), fake = the script's record
;;; WsSentClose. Used by doeff_interpreter in conftest.py (the :interpreters names of deftest -> INTERPRETERS, REQUIRES).
(require doeff-hy.macros [defk deff defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import functools [partial])
(import queue [Queue])
(import socket)
(import threading)
(import typing [TYPE_CHECKING])
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects [state await-handler])
(import doeff_core_effects [ReadWsSent ScriptedWsBinary ScriptedWsClosed ScriptedWsEndpoint ScriptedWsReply ScriptedWsText WsScript WsSentClose WsSentRecord])
(import doeff_core_effects._scripted_ws_client [scripted-ws-client])
(import pytest)

;; The waits in this file are about real time (what they cover is in this file's row of the roster tests/realtime_tests.toml) - the test-sleep rule
;; (DOEFF211 of doeff-linter) is not applied to sleeps inside a file that declares itself realtime.
(val pytestmark pytest.mark.realtime)
;; aiohttp is a dependency of the extra `http-server`, and conftest.py imports this module even in environments without it (and skips that interpreter's tests).
;; Used only in type annotations and not imported at runtime (the real peer's functions import it inside themselves).
(when TYPE_CHECKING
  (import aiohttp)
  (import aiohttp [web]))

(val AIOHTTP "aiohttp-ws-client")
(val SCRIPTED "scripted-ws-client")
;; External modules that an interpreter needs (in environments without them, conftest.py skips that interpreter's tests).
(val REQUIRES {AIOHTTP "aiohttp"})

(val HEADER-NAME "X-Contract")
(val GREETING "hello a")
(val PEER-BINARY b"\x01\x02")
(val PEER-BYE-CODE 4001)
(val PEER-BYE-REASON "goodbye")
(val SCRIPTED-URL "ws://world.test/ws")
(val SCRIPTED-SLOW "ws://world.test/slow")
(val SCRIPTED-DEAD "ws://dead.test/ws")
;; Upper bound (seconds) on waiting for one close by the peer and for the listener to start.
(val PEER-SECONDS 10.0)
;; Handshake delay (seconds) of the slow connect target - the fast connect ends in the meantime, forming two concurrently progressing connects.
(val SLOW-SECONDS 1.0)


(defrecord WsWorld
  "The contract world: url = the peer's connect target, slow = a connect target with a delayed handshake, dead = an unreachable connect target, install = the installer of this interpreter's answerer (called with no arguments)."
  {:tags {:context "ws-client-test" :role "type"}}
  (#^ str url)
  (#^ str slow)
  (#^ str dead)
  (#^ (get Callable #([] (get Callable #([(| Program EffectBase)] Program)))) install))


(defrecord PeerClose
  "One close the peer saw: code = the close frame's status code (None if none), reason = the reason."
  {:tags {:context "ws-client-test" :role "type"}}
  (#^ (| int None) code)
  (#^ str reason))


(defeffect ContractWsWorld
  "Effect that asks for the contract world (WsWorld)."
  {:answer WsWorld
   :tags {:context "ws-client-test" :role "foundation"}})


(defeffect PeerClosures
  "Effect that waits for up to count closes the peer saw and reads them (answer = a sequence of PeerClose, in the order seen)."
  {:fields [(: count int)]
   :answer (get tuple #(PeerClose ...))
   :tags {:context "ws-client-test" :role "foundation"}})


(defhandler contract-ws-world [#^ WsWorld world]
  ;; Reason it stays an argument: the world is a value decided on the assembly side per interpreter (for the real one, a port bound on every run).
  (ContractWsWorld []
    (resume world)))


;; --- Real peer (an aiohttp ws listener that listens in a loop on another thread) -----------------------------------------------------------------

(defrecord Listening
  "The listener that was started: runner = aiohttp's AppRunner, port = the bound port."
  {:tags {:context "ws-client-test" :role "type"}}
  (#^ "web.AppRunner" runner)
  (#^ int port))


(defrecord EchoPeer
  "The real peer: loop = the listener's loop (another daemon thread), listening = the started listener, closures = the box of closes the peer saw."
  {:tags {:context "ws-client-test" :role "type"}}
  (#^ asyncio.AbstractEventLoop loop)
  (#^ Listening listening)
  (#^ (get Queue PeerClose) closures))


(defn :async #^ "web.WebSocketResponse" echo-peer [#^ (get Queue PeerClose) closures #^ "web.Request" request]  ; cannot be a defk: callback that aiohttp calls per request (a coroutine of the listener's loop)
  "One connection of the real peer (behavior in the header explanation): sends a greeting on connect, a byte message for \"bin\", a close for \"bye\", and echo for the rest. Puts the peer's (client's) close frame into
   closures."
  (import aiohttp [web WSMsgType])
  (setv ws (web.WebSocketResponse))
  (try
    (await (.prepare ws request))
    ;; The peer (client) left in the middle of the handshake (the client was closed by Cancel) - return without raising.
    (except [#(ConnectionResetError RuntimeError)]
      (return ws)))
  (await (.send-str ws (+ "hello " (.get request.headers HEADER-NAME "-"))))
  (setv reading True)
  (while reading
    (setv message (await (.receive ws)))
    (match message.type
      WSMsgType.TEXT (match message.data
                       "bin" (await (.send-bytes ws PEER-BINARY))
                       "bye" (do (await (.close ws :code PEER-BYE-CODE :message (.encode PEER-BYE-REASON "utf-8")))
                                 (setv reading False))
                       text (await (.send-str ws (+ "echo:" text))))
      WSMsgType.CLOSE (do (.put closures (PeerClose :code (int message.data) :reason (or message.extra "")))
                          (setv reading False))
      _ (do (.put closures (PeerClose :code (if (is ws.close-code None) None (int ws.close-code)) :reason ""))
            (setv reading False))))
  ws)


(defn :async #^ "web.WebSocketResponse" slow-peer [#^ (get Queue PeerClose) closures #^ "web.Request" request]  ; cannot be a defk: callback that aiohttp calls per request (a coroutine of the listener's loop)
  "Slow connect target: delays the handshake answer by SLOW-SECONDS and then behaves the same as echo-peer (another connect ends in the meantime - for the test of concurrently progressing connects)."
  (await (asyncio.sleep SLOW-SECONDS))
  (await (echo-peer closures request)))


(defn :async #^ Listening listen [#^ (get Queue PeerClose) closures]  ; cannot be a defk: aiohttp's real I/O (a coroutine of the listener's loop)
  "To start the listener on a free port of 127.0.0.1."
  (import aiohttp [web])
  (setv app (web.Application))
  (.add-route app.router "GET" "/ws" (partial echo-peer closures))
  (.add-route app.router "GET" "/slow" (partial slow-peer closures))
  (setv runner (web.AppRunner app :access-log None))
  (await (.setup runner))
  (await (.start (web.TCPSite runner "127.0.0.1" 0)))
  (Listening :runner runner :port (get (get runner.addresses 0) 1)))


(defk start-echo-peer []
  {:pre [] :post [(: % EchoPeer)] :tags {:context "ws-client-test" :role "foundation"}}
  "To start the real peer: start the listener on a loop running in its own daemon thread, and read the bound port."
  (val loop (asyncio.new-event-loop))
  (.start (threading.Thread :target loop.run-forever :name "ws-contract-peer" :daemon True))
  (val closures ((get Queue PeerClose)))
  (val listening (.result (asyncio.run-coroutine-threadsafe (listen closures) loop) PEER-SECONDS))
  (EchoPeer :loop loop :listening listening :closures closures))


(defk stop-echo-peer [peer]
  {:pre [(: peer EchoPeer)] :post [(: % None)] :tags {:context "ws-client-test" :role "foundation"}}
  "To end the real peer (close the listener and stop the loop)."
  (.result (asyncio.run-coroutine-threadsafe (.cleanup peer.listening.runner) peer.loop) PEER-SECONDS)
  (.call-soon-threadsafe peer.loop peer.loop.stop)
  None)


(defk vacant-port []
  {:pre [] :post [(: % int)] :tags {:context "ws-client-test" :role "foundation"}}
  "A port of 127.0.0.1 that nobody is listening on (bound and closed right away)."
  (with [probe (socket.socket)]
    (.bind probe #("127.0.0.1" 0))
    (val port (get (.getsockname probe) 1)))
  port)


(deff client-into [box]  ; cannot be a defk: callback called inside the coroutine (outside the Program) that connects as aiohttp-ws-client's client-factory
  {:pre [(: box (get Queue aiohttp.ClientSession))] :post [(: % "aiohttp.ClientSession")] :tags {:context "ws-client-test" :role "foundation"}}
  "To count the clients the real answerer makes: makes the same client as the default maker (new-client-session), puts it in the box, and then passes it on (a test reads later whether it is closed)."
  (import doeff_core_effects._aiohttp_ws_client [new-client-session])
  (setv session (new-client-session))
  (.put box session)
  session)


(defhandler live-closures [#^ (get Queue PeerClose) closures]
  ;; The closes the real peer saw (header explanation). Reason it stays an argument: the box is a value made when the interpreter started the listener.
  (PeerClosures [count]
    (var seen #())
    (while (< (len seen) count)
      (:= seen (+ seen #((.get closures :timeout PEER-SECONDS)))))
    (resume seen)))


(defk under-aiohttp [program]
  {:pre [(: program Program)] :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "ws-client-test" :role "foundation"}}
  "Run program under the real aiohttp-ws-client. The peer is an echo listener that listens only while running, and dead is a port nobody is listening on."
  (import doeff_core_effects._aiohttp_ws_client [aiohttp-ws-client])
  (<- peer EchoPeer (start-echo-peer))
  (<- dead int (vacant-port))
  (val world (WsWorld :url (.format "ws://127.0.0.1:{}/ws" peer.listening.port) :slow (.format "ws://127.0.0.1:{}/slow" peer.listening.port)
                      :dead (.format "ws://127.0.0.1:{}/ws" dead) :install aiohttp-ws-client))
  (var answer None)
  (try
    (<- ran (with_handlers [await-handler (state) (contract-ws-world world) (aiohttp-ws-client) (live-closures peer.closures)] program))
    (:= answer ran)
    (finally
      (<- (stop-echo-peer peer))))
  answer)


;; --- Fake peer (script) ---------------------------------------------------------------------------------------------------------

;; The peer's behavior script (header explanation) - the url and slow connect targets behave the same.
(val PEER-REPLIES #((ScriptedWsReply :contains "hi" :frames #((ScriptedWsText :text "echo:hi")))
                    (ScriptedWsReply :contains "bin" :frames #((ScriptedWsBinary :data PEER-BINARY)))
                    (ScriptedWsReply :contains "bye" :frames #((ScriptedWsClosed :code PEER-BYE-CODE :reason PEER-BYE-REASON)))))
(val SCRIPT (WsScript :endpoints #((ScriptedWsEndpoint :url SCRIPTED-URL :frames #((ScriptedWsText :text GREETING)) :replies PEER-REPLIES)
                                   (ScriptedWsEndpoint :url SCRIPTED-SLOW :frames #((ScriptedWsText :text GREETING)) :replies PEER-REPLIES))))


(defhandler scripted-closures
  ;; The closes the fake peer saw (header explanation) = the closes among the script answerer's records.
  (PeerClosures [count]
    (<- sent (get tuple #(WsSentRecord ...)) (ReadWsSent))
    (resume (tuple (gfor record sent :if (isinstance record WsSentClose) (PeerClose :code record.code :reason record.reason))))))


(defk scripted-install []
  {:pre [] :post [(: % (get Callable #([(| Program EffectBase)] Program)))] :tags {:context "ws-client-test" :role "foundation"}}
  "The contract world's install (no arguments) - the installer of scripted-ws-client with the script."
  (scripted-ws-client SCRIPT))


(val SCRIPTED-WORLD (WsWorld :url SCRIPTED-URL :slow SCRIPTED-SLOW :dead SCRIPTED-DEAD :install (fn [] (scripted-ws-client SCRIPT))))


(defk under-scripted [program]
  {:pre [(: program Program)] :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "ws-client-test" :role "foundation"}}
  "Run program under the fake scripted-ws-client. From outside in: state, world, script, and the closes the peer saw (read from the script's record)."
  (<- answer (with_handlers [(state) (contract-ws-world SCRIPTED-WORLD) (scripted-ws-client SCRIPT) scripted-closures] program))
  answer)


(val INTERPRETERS {AIOHTTP under-aiohttp
                   SCRIPTED under-scripted})
