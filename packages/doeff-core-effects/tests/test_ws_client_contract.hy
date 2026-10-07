;;; WebSocket の client の契約テスト — 同じ効果(WsConnect・WsReceive・WsSend・WsDisconnect・WsDisconnectAll)に答える本物(aiohttp-ws-client)と
;;; fake(scripted-ws-client)が、同じ deftest を通る(agora-redesign #4007 U0)。解釈器の組み立てと契約の世界は ws_client_contract_handlers.hy。
;;;
;;;   * WsConnect は繋がれば WsLink(id は繋いだ順に ws-1・ws-2 … — 断られた繋ぎは番号を進めない)、届かなければ WsConnectFailed
;;;   * WsReceive は文字の 1 通を WsText・byte の 1 通を WsBinary で届け、相手の close はその状態符と理由の WsLinkClosed
;;;   * 相手が閉じた後の WsReceive・WsSend・WsDisconnect は同じ WsLinkClosed(閉じ直さない)
;;;   * WsDisconnect の状態符と理由は、答え(WsLinkClosed)にも相手の受ける close にも載り、以後の受信も同じ
;;;   * 知らない idへの WsReceive は状態符なしの WsLinkClosed(UNKNOWN-LINK-REASON)
;;;   * WsDisconnectAll は開いている接続の全部を閉じ、閉じた順の WsLinkClosed を答える
;;;   * 答え手の範囲が終わると、開いたままの接続は WS-LINK-CLOSE-GOING-AWAY と SCOPE-ENDED-REASON で閉じられる(相手にも届く)
;;; 答え手だけの性質は test_ws_client.hy(fake: 台本が尽きた時・then-close・replies の重なり・繋ぎ先の最長の一致。本物: handshake を断った status)。
(require doeff-hy.macros [defk deftest <- val var with-handler])
(import doeff_core_effects.http_server_effects [HttpHeader])
(import doeff_core_effects.ws_client_effects [WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll WsLink WsConnectFailed WsConnectOutcome
                                              WsText WsBinary WsLinkClosed WsFrame WsSent WsSendOutcome UNKNOWN-LINK-REASON
                                              WS-LINK-CLOSE-GOING-AWAY SCOPE-ENDED-REASON])
(import ws_client_contract_handlers [ContractWsWorld PeerClosures PeerClose WsWorld HEADER-NAME GREETING PEER-BINARY PEER-BYE-CODE
                                     PEER-BYE-REASON])


(defk connect [url]
  {:pre [(: url str)] :post [(: % WsLink)] :tags {:context "ws-client-test" :role "program"}}
  "ヘッダー X-Contract: a を載せて url へ繋ぎ、繋がった事を確かめる。"
  (<- outcome WsConnectOutcome (WsConnect :url url :headers #((HttpHeader :name HEADER-NAME :value "a"))))
  (assert (isinstance outcome WsLink) outcome)
  outcome)


(defk greeted [url]
  {:pre [(: url str)] :post [(: % WsLink)] :tags {:context "ws-client-test" :role "program"}}
  "繋いで、相手の greeting(hello a — ヘッダーが相手に届いた証)を受ける。"
  (<- link WsLink (connect url))
  (<- hello WsFrame (WsReceive :link link.link))
  (assert (= hello (WsText :link link.link :text GREETING)) hello)
  link)


(defk sent-then-received [link text]
  {:pre [(: link str) (: text str)] :post [(: % WsFrame)] :tags {:context "ws-client-test" :role "program"}}
  "文を送り(送れた事を確かめ)、次の frame を受ける。"
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
  ;; 相手の close はその状態符と理由で届き、以後の受信・送信・閉じは同じ答え。
  (val closed (WsLinkClosed :link link.link :code PEER-BYE-CODE :reason PEER-BYE-REASON))
  (<- bye WsFrame (sent-then-received link.link "bye"))
  (assert (= bye closed) bye)
  (<- again WsFrame (WsReceive :link link.link))
  (assert (= again closed) again)
  (<- late-send WsSendOutcome (WsSend :link link.link :text "x"))
  (assert (= late-send closed) late-send)
  (<- late-close WsLinkClosed (WsDisconnect :link link.link :code 4000 :reason "遅い"))
  (assert (= late-close closed) late-close))


(deftest test-our-close-is-named-and-later-reads-answer-the-same
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- world WsWorld (ContractWsWorld))
  (<- link WsLink (greeted world.url))
  (val closed (WsLinkClosed :link link.link :code 4002 :reason "済んだ"))
  (<- answer WsLinkClosed (WsDisconnect :link link.link :code 4002 :reason "済んだ"))
  (assert (= answer closed) answer)
  (<- after WsFrame (WsReceive :link link.link))
  (assert (= after closed) after)
  (<- late-send WsSendOutcome (WsSend :link link.link :text "x"))
  (assert (= late-send closed) late-send)
  ;; 相手の受けた close にも同じ状態符と理由。
  (<- seen tuple (PeerClosures :count 1))
  (assert (= seen #((PeerClose :code 4002 :reason "済んだ"))) seen))


(deftest test-an-unreachable-url-is-a-connect-failure-and-does-not-use-a-link-number
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- world WsWorld (ContractWsWorld))
  (<- failed WsConnectOutcome (WsConnect :url world.dead))
  (assert (isinstance failed WsConnectFailed) failed)
  (assert (= [failed.url failed.status] [world.dead None]) failed)
  ;; 断られた繋ぎは番号を進めない。
  (<- first WsLink (connect world.url))
  (<- second WsLink (connect world.url))
  (assert (= [first.link second.link] ["ws-1" "ws-2"]) [first second]))


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
  (<- closed tuple (WsDisconnectAll :code 4003 :reason "全部"))
  (assert (= closed #((WsLinkClosed :link first.link :code 4003 :reason "全部") (WsLinkClosed :link second.link :code 4003 :reason "全部")))
          closed)
  (<- seen tuple (PeerClosures :count 2))
  (assert (= seen #((PeerClose :code 4003 :reason "全部") (PeerClose :code 4003 :reason "全部"))) seen)
  ;; 何も開いていなければ空。
  (<- nothing tuple (WsDisconnectAll))
  (assert (= nothing #()) nothing))


(deftest test-the-end-of-the-handlers-scope-closes-open-links-as-going-away
  {:interpreters ["aiohttp-ws-client" "scripted-ws-client"]}
  (<- world WsWorld (ContractWsWorld))
  ;; 内側の範囲で繋いだまま出る — 範囲の終わりの後始末が閉じる(相手に届き、外側の WsReceive も閉じた idとして答える)。
  (<- kept WsLink (with-handler [(world.install)] (greeted world.url)))
  (<- seen tuple (PeerClosures :count 1))
  (assert (= seen #((PeerClose :code WS-LINK-CLOSE-GOING-AWAY :reason SCOPE-ENDED-REASON))) seen)
  (<- after WsFrame (WsReceive :link kept.link))
  (assert (= after (WsLinkClosed :link kept.link :code WS-LINK-CLOSE-GOING-AWAY :reason SCOPE-ENDED-REASON)) after))
