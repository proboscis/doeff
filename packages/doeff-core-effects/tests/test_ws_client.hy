;;; Checks for properties that only one of the answerers of the generic WebSocket client effects (ws_client_effects.hy) has. Properties common to both
;;; (connecting, frame round trips, peer close and own close, after close, unknown id, cleanup at the end of the scope) are run on both answerers by the contract
;;; test test_ws_client_contract.hy.
;;;   - Scripted answerer (scripted-ws-client): when the script's frames run out (with or without then-close), that replies per word contained in the sent text all
;;;     chain in script order, longest match of the connection target, and the record of sent text and closes (ReadWsSent).
;;;   - Real answerer (aiohttp-ws-client): the status of a refused handshake rides on WsConnectFailed; the client of a Cancelled connect closes and the remaining connect
;;;     keeps working; the reason text (reason) carries no request header values or url query (skipped in a venv without aiohttp).
(require doeff-hy.macros [defk deftest <- val var with-handler])
(import asyncio)
(import typing [TYPE_CHECKING])
(import functools [partial])
(import queue [Queue])
(import doeff_core_effects [Await])
(import doeff_core_effects [state])
(import doeff_core_effects [Spawn Cancel])
(import doeff_core_effects [HttpHeader])
(import doeff_core_effects [WsConnect WsReceive WsSend WsDisconnect ReadWsSent WsLink WsConnectFailed WsConnectOutcome WsText WsBinary WsLinkClosed WsFrame WsSent WsSendOutcome WsScript ScriptedWsEndpoint ScriptedWsReply ScriptedWsText ScriptedWsBinary ScriptedWsClosed WsSentText WsSentRecord]
        doeff_core_effects._ws_client_effects [SCRIPT-EXHAUSTED-REASON NOT-IN-SCRIPT-REASON])
(import doeff_core_effects._scripted_ws_client [scripted-ws-client])
(import ws_client_contract_handlers [ContractWsWorld WsWorld PEER-SECONDS client-into])
(import pytest)

;; The waits in this file target real time (what is targeted is in this file's row of the roster tests/realtime_tests.toml) -- the sleep-check rule (doeff-linter DOEFF211)
;; is not applied to sleeps inside a file that declares itself realtime.
(val pytestmark pytest.mark.realtime)
;; aiohttp is a dependency of the extra `http-server` (tests of the real answerer are skipped where it is absent) -- used only in type annotations, and not imported at runtime.
(when TYPE_CHECKING
  (import aiohttp))

(val SIDEBAND-APPEND "{\"commit\": true, \"append\": \"x\"}")


(defk connected [url]
  {:pre [(: url str)] :post [(: % str)] :tags {:context "ws-client-test" :role "program"}}
  "Connect to url and return the id."
  (<- outcome WsConnectOutcome (WsConnect :url url))
  (assert (isinstance outcome WsLink) outcome)
  outcome.link)


(defk drain [link]
  {:pre [(: link str)] :post [(: % (get tuple #(WsFrame ...)))] :tags {:context "ws-client-test" :role "program"}}
  "Receive the frames of the id's connection until WsLinkClosed arrives (answer = the received frames, including the close)."
  (var frames #())
  (var reading True)
  (while reading
    (<- frame WsFrame (WsReceive :link link))
    (:= frames (+ frames #(frame)))
    (:= reading (not (isinstance frame WsLinkClosed))))
  frames)


(defk drain-two [first-url second-url]
  {:pre [(: first-url str) (: second-url str)] :post [(: % (get tuple #((get tuple #(WsFrame ...)) (get tuple #(WsFrame ...)))))] :tags {:context "ws-client-test" :role "program"}}
  "Connect to 2 urls and receive each one's frames until close (answer = #(1st frame sequence 2nd frame sequence))."
  (<- first str (connected first-url))
  (<- second str (connected second-url))
  (<- first-frames (get tuple #(WsFrame ...)) (drain first))
  (<- second-frames (get tuple #(WsFrame ...)) (drain second))
  #(first-frames second-frames))


(deftest test-the-scripted-client-ends-an-exhausted-script-without-a-code-or-with-the-scripted-close
  (val script (WsScript :endpoints #((ScriptedWsEndpoint :url "ws://open.test" :frames #((ScriptedWsText :text "one")))
                                     (ScriptedWsEndpoint :url "ws://closing.test" :frames #((ScriptedWsBinary :data b"\x00"))
                                                         :then-close (ScriptedWsClosed :code 4004 :reason "end of script")))))
  (<- got (get tuple #((get tuple #(WsFrame ...)) (get tuple #(WsFrame ...)))) (with-handler [(state) (scripted-ws-client script)] (drain-two "ws://open.test/a" "ws://closing.test/b")))
  ;; A script without then-close, when exhausted, is script-exhausted with no status code (does not make the caller wait).
  (assert (= (get got 0) #((WsText :link "ws-1" :text "one") (WsLinkClosed :link "ws-1" :code None :reason SCRIPT-EXHAUSTED-REASON))) got)
  (assert (= (get got 1) #((WsBinary :link "ws-2" :data b"\x00") (WsLinkClosed :link "ws-2" :code 4004 :reason "end of script"))) got))


(defk send-and-drain [url text]
  {:pre [(: url str) (: text str)]
   :post [(: % (get tuple #(WsSendOutcome (get tuple #(WsFrame ...)) WsLinkClosed (get tuple #(WsSentRecord ...)))))]
   :tags {:context "ws-client-test" :role "program"}}
  "Connect to url, send text, receive frames until close, issue the close, and read the record (answer = #(send's answer frame sequence, close's answer, record))."
  (<- link str (connected url))
  (<- sent WsSendOutcome (WsSend :link link :text text))
  (<- frames (get tuple #(WsFrame ...)) (drain link))
  (<- closed WsLinkClosed (WsDisconnect :link link :code 1000 :reason "done"))
  (<- records (get tuple #(WsSentRecord ...)) (ReadWsSent))
  #(sent frames closed records))


(deftest test-the-scripted-client-appends-the-replies-of-every-contained-word-in-script-order
  (val script (WsScript :endpoints #((ScriptedWsEndpoint :url "ws://side.test"
                                                         :replies #((ScriptedWsReply :contains "append" :frames #((ScriptedWsText :text "ack:append")))
                                                                    (ScriptedWsReply :contains "commit" :frames #((ScriptedWsText :text "ack:commit")
                                                                                                                  (ScriptedWsText :text "done"))))))))
  ;; One message containing 2 words chains both replies in script order.
  (<- got (get tuple #(WsSendOutcome (get tuple #(WsFrame ...)) WsLinkClosed (get tuple #(WsSentRecord ...))))
      (with-handler [(state) (scripted-ws-client script)] (send-and-drain "ws://side.test/sessions/1/attach" SIDEBAND-APPEND)))
  (assert (= (get got 0) (WsSent :link "ws-1")) got)
  (assert (= (get got 1) #((WsText :link "ws-1" :text "ack:append") (WsText :link "ws-1" :text "ack:commit") (WsText :link "ws-1" :text "done")
                           (WsLinkClosed :link "ws-1" :code None :reason SCRIPT-EXHAUSTED-REASON)))
          got)
  ;; A WsDisconnect after exhaustion does not close again -- the answer is the same WsLinkClosed as at exhaustion, the record has no close, and only the sent text remains.
  (assert (= (get got 2) (WsLinkClosed :link "ws-1" :code None :reason SCRIPT-EXHAUSTED-REASON)) got)
  (assert (= (get got 3) #((WsSentText :link "ws-1" :text SIDEBAND-APPEND))) got))


(defk first-frames-and-a-refusal [first-url second-url refused-url]
  {:pre [(: first-url str) (: second-url str) (: refused-url str)] :post [(: % (get tuple #(WsFrame WsFrame WsConnectOutcome)))] :tags {:context "ws-client-test" :role "program"}}
  "Connect to 2 urls and receive the first frame of each, and attach the answer of the connect to a 3rd url."
  (<- first str (connected first-url))
  (<- second str (connected second-url))
  (<- refused WsConnectOutcome (WsConnect :url refused-url))
  (<- first-frame WsFrame (WsReceive :link first))
  (<- second-frame WsFrame (WsReceive :link second))
  #(first-frame second-frame refused))


(deftest test-the-scripted-client-picks-the-longest-matching-endpoint-and-refuses-the-rest
  (val script (WsScript :endpoints #((ScriptedWsEndpoint :url "wss://api.test" :frames #((ScriptedWsText :text "root")))
                                     (ScriptedWsEndpoint :url "wss://api.test/v1/live" :frames #((ScriptedWsText :text "live"))))))
  (<- got (get tuple #(WsFrame WsFrame WsConnectOutcome)) (with-handler [(state) (scripted-ws-client script)]
                  (first-frames-and-a-refusal "wss://api.test/v1/live/sessions/s1/attach" "wss://api.test/other" "wss://elsewhere.test/v1/live")))
  (assert (= (get got 0) (WsText :link "ws-1" :text "live")) got)
  (assert (= (get got 1) (WsText :link "ws-2" :text "root")) got)
  (assert (= (get got 2) (WsConnectFailed :url "wss://elsewhere.test/v1/live" :reason NOT-IN-SCRIPT-REASON :status None)) got))


(deftest test-the-aiohttp-client-names-the-status-of-a-refused-handshake
  {:interpreters ["aiohttp-ws-client"]}
  ;; The peer listener upgrades only /ws to ws -- other paths are refused with 404 (a property of the real one only: the refusal status rides along).
  (<- world WsWorld (ContractWsWorld))
  (val elsewhere (+ world.url "/nope"))
  (<- refused WsConnectOutcome (WsConnect :url elsewhere))
  (assert (isinstance refused WsConnectFailed) refused)
  (assert (= [refused.url refused.status] [elsewhere 404]) refused))


(defk fast-link-after-abandoning-the-slow-one [slow url]
  {:pre [(: slow str) (: url str)] :post [(: % WsText)] :tags {:context "ws-client-test" :role "program"}}
  "Start a slow connect, finish a fast connect, then Cancel the slow one (answer = the fast one's greeting -- proof that the remaining one works)."
  (<- slow-task (Spawn (connected slow)))
  (<- fast WsConnectOutcome (WsConnect :url url))
  (assert (isinstance fast WsLink) fast)
  (<- (Cancel slow-task))
  (<- hello WsFrame (WsReceive :link fast.link))
  (assert (isinstance hello WsText) hello)
  hello)


(defn :async #^ bool all-closed [#^ (get tuple #("aiohttp.ClientSession" ...)) sessions]  ; cannot be a defk: a coroutine that waits for the client closes on the shared event loop
  "Cancel returns without waiting for the coroutine to unwind, so wait on the shared loop, for up to PEER-SECONDS, for all the created clients to close
   (answer = whether all closed)."
  (setv loop (asyncio.get-running-loop))
  (setv deadline (+ (.time loop) PEER-SECONDS))
  (while (and (not (all (gfor s sessions s.closed))) (< (.time loop) deadline))
    (await (asyncio.sleep 0.02)))
  (all (gfor s sessions s.closed)))


(defk drained [box]
  {:pre [(: box (get Queue aiohttp.ClientSession))] :post [(: % (get tuple #(aiohttp.ClientSession ...)))] :tags {:context "ws-client-test" :role "judgment"}}
  "Take out everything placed in the box (in the order placed)."
  (var items #())
  (while (not (.empty box))
    (:= items (+ items #((.get-nowait box)))))
  items)


(deftest test-a-cancelled-connect-closes-its-client-and-the-other-connect-proceeds
  {:interpreters ["aiohttp-ws-client"]}
  ;; Cancel one of 2 connects progressing at the same time under the same handler: the remaining one works, and the Cancelled one's client closes in finally.
  (import doeff_core_effects._aiohttp_ws_client [aiohttp-ws-client])
  (<- world WsWorld (ContractWsWorld))
  (val box ((get Queue "aiohttp.ClientSession")))
  (<- hello WsText (with-handler [(aiohttp-ws-client :client-factory (partial client-into box))]
                     (fast-link-after-abandoning-the-slow-one world.slow world.url)))
  ;; The slow one takes ws-1, and the fast one gets ws-2 (a connect that carries no header, so the greeting is "hello -").
  (assert (= hello (WsText :link "ws-2" :text "hello -")) hello)
  (<- sessions (get tuple #(aiohttp.ClientSession ...)) (drained box))
  ;; One client each is created for the slow and the fast ones; the Cancelled one closes in finally, and the fast one at the cleanup at the end of the scope.
  (assert (= (len sessions) 2) sessions)
  (<- closed bool (Await (all-closed sessions)))
  (assert closed (lfor s sessions s.closed)))


(deftest test-failure-reasons-carry-neither-header-values-nor-the-query
  {:interpreters ["aiohttp-ws-client"]}
  ;; Boundary of the protocol record: WsConnectFailed.reason does not carry the request header values (the Authorization Bearer) or the url query --
  ;; the reason is only the exception type name and the status.
  (<- world WsWorld (ContractWsWorld))
  (val secret "bearer-secret-value")
  (val bearer #((HttpHeader :name "Authorization" :value (+ "Bearer " secret))))
  (val refused-url (+ world.url "/nope?token=" secret))
  (<- refused WsConnectOutcome (WsConnect :url refused-url :headers bearer))
  (assert (isinstance refused WsConnectFailed) refused)
  (assert (= [refused.status refused.reason] [404 "WSServerHandshakeError: status 404"]) refused)
  (<- dead WsConnectOutcome (WsConnect :url (+ world.dead "?token=" secret) :headers bearer))
  (assert (isinstance dead WsConnectFailed) dead)
  (assert (= dead.reason "ClientConnectorError") dead)
  (assert (and (not-in secret dead.reason) (not-in "?" dead.reason) (not-in "token" dead.reason)) dead))


(deftest test-failure-detail-drops-the-exception-text-that-carries-the-url
  {:interpreters ["aiohttp-ws-client"]}
  ;; The text of aiohttp's exception carries the request url (including the query) -- the boundary is held by failure-detail (only the type name and the status).
  (import aiohttp)
  (import yarl [URL])
  (import multidict [CIMultiDict CIMultiDictProxy])
  (import doeff_core_effects._aiohttp_ws_client [failure-detail])
  (val url (URL "wss://api.test/v1/live/sessions/s1/attach?token=secret-in-query"))
  (val info (aiohttp.RequestInfo :url url :method "GET"
                                 :headers (CIMultiDictProxy (CIMultiDict [#("Authorization" "Bearer secret-in-header")])) :real-url url))
  (val error (aiohttp.WSServerHandshakeError :request-info info :history #() :status 401 :message "Unauthorized"))
  (assert (in "secret-in-query" (str error)) (str error))
  (val detail (failure-detail error))
  (assert (= detail "WSServerHandshakeError: status 401") detail)
  (assert (and (not-in "secret" detail) (not-in "?" detail)) detail))
