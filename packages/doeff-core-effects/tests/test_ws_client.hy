;;; 汎用の WebSocket の client の effect(ws_client_effects.hy)の、答え手の片方だけが持つ性質の検(agora-redesign #4007 U0)。両方に共通の
;;; 性質(繋ぎ・frame の往復・相手の閉じとこちらの閉じ・閉じた後・知らない id・範囲の終わりの後始末)は test_ws_client_contract.hy の契約
;;; テストが両方の答え手で回す。
;;;   - 台本の答え手(scripted-ws-client): 台本の frame が尽きた時(then-close の有無)・送った文が含む語ごとの replies が台本の順に全部
;;;     つながる事・繋ぎ先の最長の一致・送った文と閉じの記録(ReadWsSent)。
;;;   - 本物の答え手(aiohttp-ws-client): handshake を断った status が WsConnectFailed に載る・Cancel された繋ぎの client が閉じて残った繋ぎが
;;;     動く・理由の文(reason)に要求のヘッダーの値や url の query が載らない(aiohttp の無い venv では skip)。
(require doeff-hy.macros [defk deftest <- val var with-handler])
(import asyncio)
(import typing [TYPE_CHECKING])
(import functools [partial])
(import queue [Queue])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [Spawn Cancel])
(import doeff_core_effects.http_server_effects [HttpHeader])
(import doeff_core_effects.ws_client_effects [WsConnect WsReceive WsSend WsDisconnect ReadWsSent WsLink WsConnectFailed WsConnectOutcome WsText
                                              WsBinary WsLinkClosed WsFrame WsSent WsSendOutcome WsScript ScriptedWsEndpoint ScriptedWsReply
                                              ScriptedWsText ScriptedWsBinary ScriptedWsClosed WsSentText WsSentRecord SCRIPT-EXHAUSTED-REASON
                                              NOT-IN-SCRIPT-REASON])
(import doeff_core_effects.scripted_ws_client [scripted-ws-client])
(import ws_client_contract_handlers [ContractWsWorld WsWorld PEER-SECONDS client-into])
;; aiohttp は extra `http-server` の依存(無い環境では本物の答え手のテストを skip する)— 型の注記にだけ使い、実行時は import しない。
(when TYPE_CHECKING
  (import aiohttp))

(val SIDEBAND-APPEND "{\"commit\": true, \"append\": \"x\"}")


(defk connected [url]
  {:pre [(: url str)] :post [(: % str)] :tags {:context "ws-client-test" :role "program"}}
  "url へ繋ぎ、id を返す。"
  (<- outcome WsConnectOutcome (WsConnect :url url))
  (assert (isinstance outcome WsLink) outcome)
  outcome.link)


(defk drain [link]
  {:pre [(: link str)] :post [(: % (get tuple #(WsFrame ...)))] :tags {:context "ws-client-test" :role "program"}}
  "id の接続の frame を WsLinkClosed が来るまで受ける(答え = 受けた frame の列・閉じを含む)。"
  (var frames #())
  (var reading True)
  (while reading
    (<- frame WsFrame (WsReceive :link link))
    (:= frames (+ frames #(frame)))
    (:= reading (not (isinstance frame WsLinkClosed))))
  frames)


(defk drain-two [first-url second-url]
  {:pre [(: first-url str) (: second-url str)] :post [(: % (get tuple #((get tuple #(WsFrame ...)) (get tuple #(WsFrame ...)))))] :tags {:context "ws-client-test" :role "program"}}
  "2 つの url へ繋ぎ、それぞれの frame を閉じまで受ける(答え = #(1 つ目の frame の列 2 つ目の frame の列))。"
  (<- first str (connected first-url))
  (<- second str (connected second-url))
  (<- first-frames (get tuple #(WsFrame ...)) (drain first))
  (<- second-frames (get tuple #(WsFrame ...)) (drain second))
  #(first-frames second-frames))


(deftest test-the-scripted-client-ends-an-exhausted-script-without-a-code-or-with-the-scripted-close
  (val script (WsScript :endpoints #((ScriptedWsEndpoint :url "ws://open.test" :frames #((ScriptedWsText :text "one")))
                                     (ScriptedWsEndpoint :url "ws://closing.test" :frames #((ScriptedWsBinary :data b"\x00"))
                                                         :then-close (ScriptedWsClosed :code 4004 :reason "台本の終わり")))))
  (<- got (get tuple #((get tuple #(WsFrame ...)) (get tuple #(WsFrame ...)))) (with-handler [(state) (scripted-ws-client script)] (drain-two "ws://open.test/a" "ws://closing.test/b")))
  ;; then-close の無い台本は、尽きたら状態符なしで script-exhausted(呼び手を待たせない)。
  (assert (= (get got 0) #((WsText :link "ws-1" :text "one") (WsLinkClosed :link "ws-1" :code None :reason SCRIPT-EXHAUSTED-REASON))) got)
  (assert (= (get got 1) #((WsBinary :link "ws-2" :data b"\x00") (WsLinkClosed :link "ws-2" :code 4004 :reason "台本の終わり"))) got))


(defk send-and-drain [url text]
  {:pre [(: url str) (: text str)]
   :post [(: % (get tuple #(WsSendOutcome (get tuple #(WsFrame ...)) WsLinkClosed (get tuple #(WsSentRecord ...)))))]
   :tags {:context "ws-client-test" :role "program"}}
  "url へ繋ぎ、text を送り、frame を閉じまで受け、閉じを出し、記録を読む(答え = #(送りの答え frame の列 閉じの答え 記録))。"
  (<- link str (connected url))
  (<- sent WsSendOutcome (WsSend :link link :text text))
  (<- frames (get tuple #(WsFrame ...)) (drain link))
  (<- closed WsLinkClosed (WsDisconnect :link link :code 1000 :reason "終わり"))
  (<- records (get tuple #(WsSentRecord ...)) (ReadWsSent))
  #(sent frames closed records))


(deftest test-the-scripted-client-appends-the-replies-of-every-contained-word-in-script-order
  (val script (WsScript :endpoints #((ScriptedWsEndpoint :url "ws://side.test"
                                                         :replies #((ScriptedWsReply :contains "append" :frames #((ScriptedWsText :text "ack:append")))
                                                                    (ScriptedWsReply :contains "commit" :frames #((ScriptedWsText :text "ack:commit")
                                                                                                                  (ScriptedWsText :text "done"))))))))
  ;; 2 つの語を含む 1 通は、両方の replies を台本の順につなぐ。
  (<- got (get tuple #(WsSendOutcome (get tuple #(WsFrame ...)) WsLinkClosed (get tuple #(WsSentRecord ...))))
      (with-handler [(state) (scripted-ws-client script)] (send-and-drain "ws://side.test/sessions/1/attach" SIDEBAND-APPEND)))
  (assert (= (get got 0) (WsSent :link "ws-1")) got)
  (assert (= (get got 1) #((WsText :link "ws-1" :text "ack:append") (WsText :link "ws-1" :text "ack:commit") (WsText :link "ws-1" :text "done")
                           (WsLinkClosed :link "ws-1" :code None :reason SCRIPT-EXHAUSTED-REASON)))
          got)
  ;; 尽きた後の WsDisconnect は閉じ直さない — 答えは尽きた時と同じ WsLinkClosed で、記録に閉じは無く、送った文だけが残る。
  (assert (= (get got 2) (WsLinkClosed :link "ws-1" :code None :reason SCRIPT-EXHAUSTED-REASON)) got)
  (assert (= (get got 3) #((WsSentText :link "ws-1" :text SIDEBAND-APPEND))) got))


(defk first-frames-and-a-refusal [first-url second-url refused-url]
  {:pre [(: first-url str) (: second-url str) (: refused-url str)] :post [(: % (get tuple #(WsFrame WsFrame WsConnectOutcome)))] :tags {:context "ws-client-test" :role "program"}}
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
  (<- got (get tuple #(WsFrame WsFrame WsConnectOutcome)) (with-handler [(state) (scripted-ws-client script)]
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


(defk fast-link-after-abandoning-the-slow-one [slow url]
  {:pre [(: slow str) (: url str)] :post [(: % WsText)] :tags {:context "ws-client-test" :role "program"}}
  "遅い繋ぎを始めてから速い繋ぎを終え、遅い方を Cancel する(答え = 速い方の greeting — 残った方が動く証)。"
  (<- slow-task (Spawn (connected slow)))
  (<- fast WsConnectOutcome (WsConnect :url url))
  (assert (isinstance fast WsLink) fast)
  (<- (Cancel slow-task))
  (<- hello WsFrame (WsReceive :link fast.link))
  (assert (isinstance hello WsText) hello)
  hello)


(defn :async #^ bool all-closed [#^ (get tuple #("aiohttp.ClientSession" ...)) sessions]  ; defk にできない: 共有の event loop の上で client の閉じを待つ coroutine
  "Cancel は coroutine の巻き戻りを待たずに返るので、作られた client の全部が閉じるのを PEER-SECONDS まで共有の loop の上で待つため
   (答え = 全部閉じたか)。"
  (setv loop (asyncio.get-running-loop))
  (setv deadline (+ (.time loop) PEER-SECONDS))
  (while (and (not (all (gfor s sessions s.closed))) (< (.time loop) deadline))
    (await (asyncio.sleep 0.02)))
  (all (gfor s sessions s.closed)))


(defk drained [box]
  {:pre [(: box (get Queue aiohttp.ClientSession))] :post [(: % (get tuple #(aiohttp.ClientSession ...)))] :tags {:context "ws-client-test" :role "judgment"}}
  "箱に置かれた物を全部取り出す(置かれた順)。"
  (var items #())
  (while (not (.empty box))
    (:= items (+ items #((.get-nowait box)))))
  items)


(deftest test-a-cancelled-connect-closes-its-client-and-the-other-connect-proceeds
  {:interpreters ["aiohttp-ws-client"]}
  ;; 同じ handler の下で同時に進む 2 つの繋ぎのうち片方を Cancel する: 残った方は動き、Cancel された方の client は finally で閉じる。
  (import doeff_core_effects.aiohttp_ws_client [aiohttp-ws-client])
  (<- world WsWorld (ContractWsWorld))
  (val box ((get Queue "aiohttp.ClientSession")))
  (<- hello WsText (with-handler [(aiohttp-ws-client :client-factory (partial client-into box))]
                     (fast-link-after-abandoning-the-slow-one world.slow world.url)))
  ;; 遅い方が ws-1 を確保し、速い方は ws-2(ヘッダーを載せない繋ぎなので greeting は "hello -")。
  (assert (= hello (WsText :link "ws-2" :text "hello -")) hello)
  (<- sessions (get tuple #(aiohttp.ClientSession ...)) (drained box))
  ;; 遅い方と速い方の client が 1 つずつ作られ、Cancel された方は finally で・速い方は範囲の終わりの後始末で閉じる。
  (assert (= (len sessions) 2) sessions)
  (<- closed bool (Await (all-closed sessions)))
  (assert closed (lfor s sessions s.closed)))


(deftest test-failure-reasons-carry-neither-header-values-nor-the-query
  {:interpreters ["aiohttp-ws-client"]}
  ;; protocol の記録の境界: WsConnectFailed.reason に要求のヘッダーの値(Authorization の Bearer)や url の query を載せない —
  ;; 理由は例外の型の名と status だけ。
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
  ;; aiohttp の例外の文は要求の url(query を含む)を運ぶ — 境界は failure-detail が持つ(型の名と status だけにする)。
  (import aiohttp)
  (import yarl [URL])
  (import multidict [CIMultiDict CIMultiDictProxy])
  (import doeff_core_effects.aiohttp_ws_client [failure-detail])
  (val url (URL "wss://api.test/v1/live/sessions/s1/attach?token=secret-in-query"))
  (val info (aiohttp.RequestInfo :url url :method "GET"
                                 :headers (CIMultiDictProxy (CIMultiDict [#("Authorization" "Bearer secret-in-header")])) :real-url url))
  (val error (aiohttp.WSServerHandshakeError :request-info info :history #() :status 401 :message "Unauthorized"))
  (assert (in "secret-in-query" (str error)) (str error))
  (val detail (failure-detail error))
  (assert (= detail "WSServerHandshakeError: status 401") detail)
  (assert (and (not-in "secret" detail) (not-in "?" detail)) detail))
