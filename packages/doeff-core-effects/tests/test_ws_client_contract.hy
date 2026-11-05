;;; WebSocket client contract tests - the real implementation (aiohttp-ws-client) and the fake (scripted-ws-client), both answering the same effects (WsConnect, WsReceive, WsSend, WsDisconnect, WsDisconnectAll),
;;; go through the same deftests. The interpreter assembly and the contract world are in ws_client_contract_handlers.hy.
;;;
;;;   * WsConnect gives WsLink when connected (ids are ws-1, ws-2 ... in the order connections were started - the number of a refused connection is not given back), and WsConnectFailed when unreachable
;;;   * Even if 2 connections proceed concurrently under the same handler, the ids do not overlap and both connections can be used separately
;;;   * WsReceive delivers a text message as WsText and a byte message as WsBinary, and the peer's close arrives as WsLinkClosed with its status code and reason
;;;   * WsReceive, WsSend and WsDisconnect after the peer has closed give the same WsLinkClosed (it is not closed again)
;;;   * The status code and reason of WsDisconnect appear in both the answer (WsLinkClosed) and the close the peer receives, and later receives are the same
;;;   * WsReceive on an unknown id gives a WsLinkClosed without a status code (UNKNOWN-LINK-REASON)
;;;   * WsDisconnectAll closes all open connections and answers WsLinkClosed in the order they were closed
;;;   * When the responder's scope ends, connections still open are closed with WS-LINK-CLOSE-GOING-AWAY and SCOPE-ENDED-REASON (this reaches the peer too)
;;; Properties of the responder alone are in test_ws_client.hy (fake: when the script runs out, then-close, overlapping replies, longest match of the connection target. real: the status when the handshake is refused).
(require doeff-hy.macros [defk deftest <- val var with-handler])
(import doeff_core_effects [Spawn Wait])
(import doeff_core_effects [HttpHeader])
(import doeff_core_effects [WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll WsLink WsConnectFailed WsConnectOutcome WsText WsBinary WsLinkClosed WsFrame WsSent WsSendOutcome]
        doeff_core_effects._ws_client_effects [UNKNOWN-LINK-REASON WS-LINK-CLOSE-GOING-AWAY SCOPE-ENDED-REASON])
(import ws_client_contract_handlers [ContractWsWorld PeerClosures PeerClose WsWorld HEADER-NAME GREETING PEER-BINARY PEER-BYE-CODE
                                     PEER-BYE-REASON])


(defk connect [url]
  {:pre [(: url str)] :post [(: % WsLink)] :tags {:context "ws-client-test" :role "program"}}
  "Connect to url with the header X-Contract: a, and confirm it connected."
  (<- outcome WsConnectOutcome (WsConnect :url url :headers #((HttpHeader :name HEADER-NAME :value "a"))))
  (assert (isinstance outcome WsLink) outcome)
  outcome)


(defk greeted [url]
  {:pre [(: url str)] :post [(: % WsLink)] :tags {:context "ws-client-test" :role "program"}}
  "Connect, and receive the peer's greeting (hello a - proof that the header reached the peer)."
  (<- link WsLink (connect url))
  (<- hello WsFrame (WsReceive :link link.link))
  (assert (= hello (WsText :link link.link :text GREETING)) hello)
  link)


(defk sent-then-received [link text]
  {:pre [(: link str) (: text str)] :post [(: % WsFrame)] :tags {:context "ws-client-test" :role "program"}}
  "Send text (confirming it was sent), and receive the next frame."
  (<- sent WsSendOutcome (WsSend :link link :text text))
  (assert (= sent (WsSent :link link)) sent)
  (<- frame WsFrame (WsReceive :link link))
  frame)


(deftest test-a-link-exchanges-text-and-binary-and-the-peers-close-is-named
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- world WsWorld (ContractWsWorld))
  (<- link WsLink (greeted world.url))
  (assert (= link.url world.url) link)
  (<- echoed WsFrame (sent-then-received link.link "hi"))
  (assert (= echoed (WsText :link link.link :text "echo:hi")) echoed)
  (<- binary WsFrame (sent-then-received link.link "bin"))
  (assert (= binary (WsBinary :link link.link :data PEER-BINARY)) binary)
  ;; The peer's close arrives with its status code and reason, and later receives, sends and closes give the same answer.
  (val closed (WsLinkClosed :link link.link :code PEER-BYE-CODE :reason PEER-BYE-REASON))
  (<- bye WsFrame (sent-then-received link.link "bye"))
  (assert (= bye closed) bye)
  (<- again WsFrame (WsReceive :link link.link))
  (assert (= again closed) again)
  (<- late-send WsSendOutcome (WsSend :link link.link :text "x"))
  (assert (= late-send closed) late-send)
  (<- late-close WsLinkClosed (WsDisconnect :link link.link :code 4000 :reason "late"))
  (assert (= late-close closed) late-close))


(deftest test-our-close-is-named-and-later-reads-answer-the-same
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- world WsWorld (ContractWsWorld))
  (<- link WsLink (greeted world.url))
  (val closed (WsLinkClosed :link link.link :code 4002 :reason "done"))
  (<- answer WsLinkClosed (WsDisconnect :link link.link :code 4002 :reason "done"))
  (assert (= answer closed) answer)
  (<- after WsFrame (WsReceive :link link.link))
  (assert (= after closed) after)
  (<- late-send WsSendOutcome (WsSend :link link.link :text "x"))
  (assert (= late-send closed) late-send)
  ;; The same status code and reason appear in the close the peer received.
  (<- seen (get tuple #(PeerClose ...)) (PeerClosures :count 1))
  (assert (= seen #((PeerClose :code 4002 :reason "done"))) seen))


(deftest test-an-unreachable-url-is-a-connect-failure-and-its-number-is-not-reused
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- world WsWorld (ContractWsWorld))
  (<- failed WsConnectOutcome (WsConnect :url world.dead))
  (assert (isinstance failed WsConnectFailed) failed)
  (assert (= [failed.url failed.status] [world.dead None]) failed)
  ;; The number is reserved before starting a connection and is not given back even if refused (so concurrent connections do not take the same number) - the refused connection used ws-1, and the next is ws-2.
  (<- first WsLink (connect world.url))
  (<- second WsLink (connect world.url))
  (assert (= [first.link second.link] ["ws-2" "ws-3"]) [first second]))


(defk connect-both-at-once [slow url]
  {:pre [(: slow str) (: url str)] :post [(: % (get tuple #(WsLink WsLink)))] :tags {:context "ws-client-test" :role "program"}}
  "Connect concurrently to a slow target and a fast target (the slow one is started first, and the fast one connects first). Answer = #(WsLink of the slow one, WsLink of the fast one)."
  (<- slow-task (Spawn (connect slow)))
  (<- fast-task (Spawn (connect url)))
  (<- fast WsLink (Wait fast-task))
  (<- slow-link WsLink (Wait slow-task))
  #(slow-link fast))


(deftest test-two-connects-at-once-get-distinct-ids-and-both-links-work
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  ;; Even if 2 connections proceed concurrently under the same handler, the ids do not overlap (the number is reserved before starting a connection - the slow one is ws-1 and the fast one is ws-2).
  (<- world WsWorld (ContractWsWorld))
  (<- both (get tuple #(WsLink WsLink)) (connect-both-at-once world.slow world.url))
  (val slow-link (get both 0))
  (val fast-link (get both 1))
  (assert (= [slow-link.link fast-link.link] ["ws-1" "ws-2"]) both)
  ;; Both connections can be used separately (each one's greeting and echo).
  (<- hello-slow WsFrame (WsReceive :link slow-link.link))
  (<- hello-fast WsFrame (WsReceive :link fast-link.link))
  (assert (= [hello-slow hello-fast] [(WsText :link "ws-1" :text GREETING) (WsText :link "ws-2" :text GREETING)]) [hello-slow hello-fast])
  (<- echoed WsFrame (sent-then-received fast-link.link "hi"))
  (assert (= echoed (WsText :link "ws-2" :text "echo:hi")) echoed)
  (<- closed WsLinkClosed (WsDisconnect :link slow-link.link :code 4005 :reason "close the slow one"))
  (assert (= closed (WsLinkClosed :link "ws-1" :code 4005 :reason "close the slow one")) closed)
  (<- still WsFrame (sent-then-received fast-link.link "hi"))
  (assert (= still (WsText :link "ws-2" :text "echo:hi")) still))


(deftest test-an-unknown-link-is-answered-closed-without-a-code
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- gone WsFrame (WsReceive :link "ws-99"))
  (assert (= gone (WsLinkClosed :link "ws-99" :code None :reason UNKNOWN-LINK-REASON)) gone)
  (<- gone-send WsSendOutcome (WsSend :link "ws-99" :text "x"))
  (assert (= gone-send gone) gone-send)
  (<- gone-close WsLinkClosed (WsDisconnect :link "ws-99"))
  (assert (= gone-close gone) gone-close))


(deftest test-disconnect-all-closes-every-open-link-in-order
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- world WsWorld (ContractWsWorld))
  (<- first WsLink (greeted world.url))
  (<- second WsLink (greeted world.url))
  (<- closed (get tuple #(WsLinkClosed ...)) (WsDisconnectAll :code 4003 :reason "all"))
  (assert (= closed #((WsLinkClosed :link first.link :code 4003 :reason "all") (WsLinkClosed :link second.link :code 4003 :reason "all")))
          closed)
  (<- seen (get tuple #(PeerClose ...)) (PeerClosures :count 2))
  (assert (= seen #((PeerClose :code 4003 :reason "all") (PeerClose :code 4003 :reason "all"))) seen)
  ;; Empty if nothing is open.
  (<- nothing (get tuple #(WsLinkClosed ...)) (WsDisconnectAll))
  (assert (= nothing #()) nothing))


(deftest test-the-end-of-the-handlers-scope-closes-open-links-as-going-away
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- world WsWorld (ContractWsWorld))
  ;; Leave while still connected in the inner scope - the cleanup at the end of the scope closes it (it reaches the peer, and the outer WsReceive also answers as a closed id).
  (<- kept WsLink (with-handler [(world.install)] (greeted world.url)))
  (<- seen (get tuple #(PeerClose ...)) (PeerClosures :count 1))
  (assert (= seen #((PeerClose :code WS-LINK-CLOSE-GOING-AWAY :reason SCOPE-ENDED-REASON))) seen)
  (<- after WsFrame (WsReceive :link kept.link))
  (assert (= after (WsLinkClosed :link kept.link :code WS-LINK-CLOSE-GOING-AWAY :reason SCOPE-ENDED-REASON)) after))
