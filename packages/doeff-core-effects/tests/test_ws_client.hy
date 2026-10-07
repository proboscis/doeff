;;; 汎用の WebSocket の client の effect(ws_client_effects.hy)の、答え手の片方だけが持つ性質の検(agora-redesign #4007 U0)。両方に共通の
;;; 性質(繋ぎ・frame の往復・相手の閉じとこちらの閉じ・閉じた後・知らない id・範囲の終わりの後始末)は test_ws_client_contract.hy の契約
;;; テストが両方の答え手で回す。
;;;   - 台本の答え手(scripted-ws-client): 台本の frame が尽きた時(then-close の有無)・送った文が含む語ごとの replies が台本の順に全部
;;;     つながる事・繋ぎ先の最長の一致・送った文と閉じの記録(ReadWsSent)。
;;;   - 本物の答え手(aiohttp-ws-client): handshake を断った status が WsConnectFailed に載る(aiohttp の無い venv では skip)。
(require doeff-hy.macros [defk deftest <- val var with-handler])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.ws_client_effects [WsConnect WsReceive WsSend WsDisconnect ReadWsSent WsLink WsConnectFailed WsConnectOutcome WsText
                                              WsBinary WsLinkClosed WsFrame WsSent WsSendOutcome WsScript ScriptedWsEndpoint ScriptedWsReply
                                              ScriptedWsText ScriptedWsBinary ScriptedWsClosed WsSentText WsSentClose SCRIPT-EXHAUSTED-REASON
                                              NOT-IN-SCRIPT-REASON])
(import doeff_core_effects.scripted_ws_client [scripted-ws-client])
(import ws_client_contract_handlers [ContractWsWorld WsWorld])

(val SIDEBAND-APPEND "{\"commit\": true, \"append\": \"x\"}")


(defk connected [url]
  {:pre [(: url str)] :post [(: % str)] :tags {:context "ws-client-test" :role "program"}}
  "url へ繋ぎ、id を返す。"
  (<- outcome WsConnectOutcome (WsConnect :url url))
  (assert (isinstance outcome WsLink) outcome)
  outcome.link)


(defk drain [link]
  {:pre [(: link str)] :post [(: % tuple)] :tags {:context "ws-client-test" :role "program"}}
  "id の接続の frame を WsLinkClosed が来るまで受ける(答え = 受けた frame の列・閉じを含む)。"
  (var frames #())
  (var reading True)
  (while reading
    (<- frame WsFrame (WsReceive :link link))
    (:= frames (+ frames #(frame)))
    (:= reading (not (isinstance frame WsLinkClosed))))
  frames)


(defk drain-two [first-url second-url]
  {:pre [(: first-url str) (: second-url str)] :post [(: % tuple)] :tags {:context "ws-client-test" :role "program"}}
  "2 つの url へ繋ぎ、それぞれの frame を閉じまで受ける(答え = #(1 つ目の frame の列 2 つ目の frame の列))。"
  (<- first str (connected first-url))
  (<- second str (connected second-url))
  (<- first-frames tuple (drain first))
  (<- second-frames tuple (drain second))
  #(first-frames second-frames))


(deftest test-the-scripted-client-ends-an-exhausted-script-without-a-code-or-with-the-scripted-close
  (val script (WsScript :endpoints #((ScriptedWsEndpoint :url "ws://open.test" :frames #((ScriptedWsText :text "one")))
                                     (ScriptedWsEndpoint :url "ws://closing.test" :frames #((ScriptedWsBinary :data b"\x00"))
                                                         :then-close (ScriptedWsClosed :code 4004 :reason "台本の終わり")))))
  (<- got tuple (with-handler [(state) (scripted-ws-client script)] (drain-two "ws://open.test/a" "ws://closing.test/b")))
  ;; then-close の無い台本は、尽きたら状態符なしで script-exhausted(呼び手を待たせない)。
  (assert (= (get got 0) #((WsText :link "ws-1" :text "one") (WsLinkClosed :link "ws-1" :code None :reason SCRIPT-EXHAUSTED-REASON))) got)
  (assert (= (get got 1) #((WsBinary :link "ws-2" :data b"\x00") (WsLinkClosed :link "ws-2" :code 4004 :reason "台本の終わり"))) got))


(defk send-and-drain [url text]
  {:pre [(: url str) (: text str)] :post [(: % tuple)] :tags {:context "ws-client-test" :role "program"}}
  "url へ繋ぎ、text を送り、frame を閉じまで受け、閉じを出し、記録を読む(答え = #(送りの答え frame の列 記録))。"
  (<- link str (connected url))
  (<- sent WsSendOutcome (WsSend :link link :text text))
  (<- frames tuple (drain link))
  (<- closed WsLinkClosed (WsDisconnect :link link :code 1000 :reason "終わり"))
  (<- records tuple (ReadWsSent))
  #(sent frames records))


(deftest test-the-scripted-client-appends-the-replies-of-every-contained-word-in-script-order
  (val script (WsScript :endpoints #((ScriptedWsEndpoint :url "ws://side.test"
                                                         :replies #((ScriptedWsReply :contains "append" :frames #((ScriptedWsText :text "ack:append")))
                                                                    (ScriptedWsReply :contains "commit" :frames #((ScriptedWsText :text "ack:commit")
                                                                                                                  (ScriptedWsText :text "done"))))))))
  ;; 2 つの語を含む 1 通は、両方の replies を台本の順につなぐ。
  (<- got tuple (with-handler [(state) (scripted-ws-client script)] (send-and-drain "ws://side.test/sessions/1/attach" SIDEBAND-APPEND)))
  (assert (= (get got 0) (WsSent :link "ws-1")) got)
  (assert (= (get got 1) #((WsText :link "ws-1" :text "ack:append") (WsText :link "ws-1" :text "ack:commit") (WsText :link "ws-1" :text "done")
                           (WsLinkClosed :link "ws-1" :code None :reason SCRIPT-EXHAUSTED-REASON)))
          got)
  ;; 尽きた後の WsDisconnect は閉じ直さない(記録に閉じは無い)— 送った文だけが記録に残る。
  (assert (= (get got 2) #((WsSentText :link "ws-1" :text SIDEBAND-APPEND))) got))


(defk first-frames-and-a-refusal [first-url second-url refused-url]
  {:pre [(: first-url str) (: second-url str) (: refused-url str)] :post [(: % tuple)] :tags {:context "ws-client-test" :role "program"}}
  "2 つの url へ繋いでそれぞれ最初の frame を受け、3 つ目の url への繋ぎの答えを添える。"
  (<- first str (connected first-url))
  (<- second str (connected second-url))
  (<- refused WsConnectOutcome (WsConnect :url refused-url))
  (<- first-frame WsFrame (WsReceive :link first))
  (<- second-frame WsFrame (WsReceive :link second))
  #(first-frame second-frame refused))


(deftest test-the-scripted-client-picks-the-longest-matching-endpoint-and-refuses-the-rest
  (val script (WsScript :endpoints #((ScriptedWsEndpoint :url "wss://api.test" :frames #((ScriptedWsText :text "root")))
                                     (ScriptedWsEndpoint :url "wss://api.test/v1/live" :frames #((ScriptedWsText :text "live"))))))
  (<- got tuple (with-handler [(state) (scripted-ws-client script)]
                  (first-frames-and-a-refusal "wss://api.test/v1/live/sessions/s1/attach" "wss://api.test/other" "wss://elsewhere.test/v1/live")))
  (assert (= (get got 0) (WsText :link "ws-1" :text "live")) got)
  (assert (= (get got 1) (WsText :link "ws-2" :text "root")) got)
  (assert (= (get got 2) (WsConnectFailed :url "wss://elsewhere.test/v1/live" :reason NOT-IN-SCRIPT-REASON :status None)) got))


(deftest test-the-aiohttp-client-names-the-status-of-a-refused-handshake
  {:interpreters ["aiohttp-ws-client"]}
  ;; 相手の待ち受けは /ws だけを ws に上げる — 他の path は 404 で断る(本物だけの性質: 断りの status が載る)。
  (<- world WsWorld (ContractWsWorld))
  (val elsewhere (+ world.url "/nope"))
  (<- refused WsConnectOutcome (WsConnect :url elsewhere))
  (assert (isinstance refused WsConnectFailed) refused)
  (assert (= [refused.url refused.status] [elsewhere 404]) refused))
