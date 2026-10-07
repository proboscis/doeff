;;; WebSocket の client の契約テストの解釈器(composition root)— 同じ契約の Program を、client の handler だけ替えて走らせる
;;; (agora-redesign #4007 U0)。
;;;
;;;   aiohttp-ws-client    本物: aiohttp-ws-client。相手は同じ process の別の thread の loop で聞く aiohttp の ws の待ち受け(127.0.0.1 の
;;;                        空いた port — 外の網へ出ない)
;;;   scripted-ws-client   fake: scripted-ws-client(I/O なし — 台本)。相手の振る舞いは台本の frame と replies で表す
;;;
;;; 契約の世界(ContractWsWorld の答え — WsWorld): url = 相手の繋ぎ先・slow = handshake が SLOW-SECONDS 遅れる繋ぎ先(同時に進む繋ぎの検 —
;;; 台本では url と同じ振る舞い)・dead = 届かない繋ぎ先・install = この解釈器の答え手の installer(検が内側の範囲を切って、範囲の終わりの
;;; 後始末を見るため)。相手の振る舞いは解釈器ごとに同じ形で用意する:
;;;   * 繋ぐと「hello <X-Contract のヘッダーの値>」の 1 通が届く(契約の Program はヘッダー X-Contract: a を載せる — 台本は "hello a" で答える)
;;;   * "bin" に byte の 1 通 PEER-BINARY・"bye" に状態符 PEER-BYE-CODE と理由 PEER-BYE-REASON の閉じ・"hi" に "echo:hi"
;;; 本物の答え手の client を数える作り手 client-into(作った client を箱へ置く — Cancel された繋ぎの client が閉じている事を検が読む)。
;;; 相手が見た閉じ(PeerClosures の答え — PeerClose の列): 本物 = 待ち受けが受けた close frame(届くまで count 件を待つ)・fake = 台本の記録
;;; WsSentClose。使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS・REQUIRES)。
(require doeff-hy.macros [defk deff defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import functools [partial])
(import queue)
(import socket)
(import threading)
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [state await-handler])
(import doeff_core_effects.ws_client_effects [ReadWsSent ScriptedWsBinary ScriptedWsClosed ScriptedWsEndpoint ScriptedWsReply ScriptedWsText
                                              WsScript WsSentClose])
(import doeff_core_effects.scripted_ws_client [scripted-ws-client])

(val AIOHTTP "aiohttp-ws-client")
(val SCRIPTED "scripted-ws-client")
;; 解釈器が要る外の module(無い環境では conftest.py がその解釈器の検を skip する)。
(val REQUIRES {AIOHTTP "aiohttp"})

(val HEADER-NAME "X-Contract")
(val GREETING "hello a")
(val PEER-BINARY b"\x01\x02")
(val PEER-BYE-CODE 4001)
(val PEER-BYE-REASON "さようなら")
(val SCRIPTED-URL "ws://world.test/ws")
(val SCRIPTED-SLOW "ws://world.test/slow")
(val SCRIPTED-DEAD "ws://dead.test/ws")
;; 相手の 1 つの閉じ・待ち受けの立ち上げを待つ上限(秒)。
(val PEER-SECONDS 10.0)
;; 遅い繋ぎ先の handshake の遅れ(秒)— 速い繋ぎがその間に終わり、同時に進む 2 つの繋ぎの形になる。
(val SLOW-SECONDS 1.0)


(defrecord WsWorld
  "契約の世界: url = 相手の繋ぎ先・slow = handshake が遅れる繋ぎ先・dead = 届かない繋ぎ先・install = この解釈器の答え手の installer(引数なしで呼ぶ)。"
  {:tags {:context "ws-client-test" :role "type"}}
  (#^ str url)
  (#^ str slow)
  (#^ str dead)
  (#^ Callable install))


(defrecord PeerClose
  "相手が見た閉じ 1 つ: code = close frame の状態符(無ければ None)・reason = 理由。"
  {:tags {:context "ws-client-test" :role "type"}}
  (#^ (| int None) code)
  (#^ str reason))


(defeffect ContractWsWorld
  "契約の世界(WsWorld)を求める効果。"
  {:answer WsWorld
   :tags {:context "ws-client-test" :role "foundation"}})


(defeffect PeerClosures
  "相手が見た閉じを count 件まで待って読む効果(答え = PeerClose の列・見た順)。"
  {:fields [(: count int)]
   :answer (get tuple #(PeerClose ...))
   :tags {:context "ws-client-test" :role "foundation"}})


(defhandler contract-ws-world [#^ WsWorld world]
  ;; 引数に残す理由: 世界は解釈器ごとに組み立ての側で決まる値(本物は走るたびに結ぶ port)。
  (ContractWsWorld []
    (resume world)))


;; --- 本物の相手(別の thread の loop で聞く aiohttp の ws の待ち受け)-----------------------------------------------------------------

(defrecord Listening
  "立てた待ち受け: runner = aiohttp の AppRunner・port = 結んだ port。"
  {:tags {:context "ws-client-test" :role "type"}}
  (#^ object runner)
  (#^ int port))


(defrecord EchoPeer
  "本物の相手: loop = 待ち受けの loop(別の daemon thread)・listening = 立てた待ち受け・closures = 相手が見た閉じの箱。"
  {:tags {:context "ws-client-test" :role "type"}}
  (#^ asyncio.AbstractEventLoop loop)
  (#^ Listening listening)
  (#^ queue.Queue closures))


(defn :async #^ object echo-peer [#^ queue.Queue closures #^ object request]  ; defk にできない: aiohttp が要求ごとに呼ぶ callback(待ち受けの loop の coroutine)
  "本物の相手の 1 接続(先頭の説明の振る舞い): 繋ぐと greeting を送り、\"bin\" に byte・\"bye\" に閉じ・他は echo。相手(client)の close frame を
   closures へ置く。"
  (import aiohttp [web WSMsgType])
  (setv ws (web.WebSocketResponse))
  (try
    (await (.prepare ws request))
    ;; 相手(client)が handshake の途中で去った(Cancel で client を閉じた)— 上げずに返す。
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


(defn :async #^ object slow-peer [#^ queue.Queue closures #^ object request]  ; defk にできない: aiohttp が要求ごとに呼ぶ callback(待ち受けの loop の coroutine)
  "遅い繋ぎ先: handshake の答えを SLOW-SECONDS 遅らせてから echo-peer と同じに振る舞う(その間に別の繋ぎが終わる — 同時に進む繋ぎの検)。"
  (await (asyncio.sleep SLOW-SECONDS))
  (await (echo-peer closures request)))


(defn :async #^ Listening listen [#^ queue.Queue closures]  ; defk にできない: aiohttp の実 I/O(待ち受けの loop の coroutine)
  "待ち受けを 127.0.0.1 の空いた port に立てるため。"
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
  "本物の相手を立てるため: 自分の daemon thread で回る loop の上に待ち受けを立て、結んだ port を読む。"
  (val loop (asyncio.new-event-loop))
  (.start (threading.Thread :target loop.run-forever :name "ws-contract-peer" :daemon True))
  (val closures (queue.Queue))
  (val listening (.result (asyncio.run-coroutine-threadsafe (listen closures) loop) PEER-SECONDS))
  (EchoPeer :loop loop :listening listening :closures closures))


(defk stop-echo-peer [peer]
  {:pre [(: peer EchoPeer)] :post [(: % None)] :tags {:context "ws-client-test" :role "foundation"}}
  "本物の相手を終わらせるため(待ち受けを閉じ、loop を止める)。"
  (.result (asyncio.run-coroutine-threadsafe (.cleanup peer.listening.runner) peer.loop) PEER-SECONDS)
  (.call-soon-threadsafe peer.loop peer.loop.stop)
  None)


(defk vacant-port []
  {:pre [] :post [(: % int)] :tags {:context "ws-client-test" :role "foundation"}}
  "誰も聞いていない 127.0.0.1 の port(結んで直ぐに閉じる)。"
  (with [probe (socket.socket)]
    (.bind probe #("127.0.0.1" 0))
    (val port (get (.getsockname probe) 1)))
  port)


(deff client-into [box]  ; defk にできない: aiohttp-ws-client の client-factory として繋ぐ coroutine の中(Program の外)で呼ばれる callback
  {:pre [(: box queue.Queue)] :post [(: % "aiohttp.ClientSession")] :tags {:context "ws-client-test" :role "foundation"}}
  "本物の答え手が作る client を数えるため: 既定の作り手(new-client-session)と同じ client を作り、箱へ置いてから渡す(検は後で閉じているかを読む)。"
  (import doeff_core_effects.aiohttp_ws_client [new-client-session])
  (setv session (new-client-session))
  (.put box session)
  session)


(defhandler live-closures [#^ queue.Queue closures]
  ;; 本物の相手が見た閉じ(先頭の説明)。引数に残す理由: 箱は解釈器が待ち受けを立てた時に作る値。
  (PeerClosures [count]
    (var seen #())
    (while (< (len seen) count)
      (:= seen (+ seen #((.get closures :timeout PEER-SECONDS)))))
    (resume seen)))


(defk under-aiohttp [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "ws-client-test" :role "foundation"}}
  "本物の aiohttp-ws-client の下で program を走らせる。相手は走る間だけ聞く echo の待ち受け、dead は誰も聞いていない port。"
  (import doeff_core_effects.aiohttp_ws_client [aiohttp-ws-client])
  (<- peer EchoPeer (start-echo-peer))
  (<- dead int (vacant-port))
  (val world (WsWorld :url (.format "ws://127.0.0.1:{}/ws" peer.listening.port) :slow (.format "ws://127.0.0.1:{}/slow" peer.listening.port)
                      :dead (.format "ws://127.0.0.1:{}/ws" dead) :install aiohttp-ws-client))
  (var answer None)
  (try
    (<- ran (with_handlers [(await-handler) (state) (contract-ws-world world) (aiohttp-ws-client) (live-closures peer.closures)] program))
    (:= answer ran)
    (finally
      (<- (stop-echo-peer peer))))
  answer)


;; --- fake の相手(台本)---------------------------------------------------------------------------------------------------------

;; 相手の振る舞いの台本(先頭の説明)— url と slow の繋ぎ先は同じ振る舞い。
(val PEER-REPLIES #((ScriptedWsReply :contains "hi" :frames #((ScriptedWsText :text "echo:hi")))
                    (ScriptedWsReply :contains "bin" :frames #((ScriptedWsBinary :data PEER-BINARY)))
                    (ScriptedWsReply :contains "bye" :frames #((ScriptedWsClosed :code PEER-BYE-CODE :reason PEER-BYE-REASON)))))
(val SCRIPT (WsScript :endpoints #((ScriptedWsEndpoint :url SCRIPTED-URL :frames #((ScriptedWsText :text GREETING)) :replies PEER-REPLIES)
                                   (ScriptedWsEndpoint :url SCRIPTED-SLOW :frames #((ScriptedWsText :text GREETING)) :replies PEER-REPLIES))))


(defhandler scripted-closures
  ;; fake の相手が見た閉じ(先頭の説明)= 台本の答え手の記録のうち閉じ。
  (PeerClosures [count]
    (<- sent tuple (ReadWsSent))
    (resume (tuple (gfor record sent :if (isinstance record WsSentClose) (PeerClose :code record.code :reason record.reason))))))


(defk scripted-install []
  {:pre [] :post [(: % Callable)] :tags {:context "ws-client-test" :role "foundation"}}
  "契約の世界の install(引数なし)— 台本つきの scripted-ws-client の installer。"
  (scripted-ws-client SCRIPT))


(val SCRIPTED-WORLD (WsWorld :url SCRIPTED-URL :slow SCRIPTED-SLOW :dead SCRIPTED-DEAD :install (fn [] (scripted-ws-client SCRIPT))))


(defk under-scripted [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "ws-client-test" :role "foundation"}}
  "fake の scripted-ws-client の下で program を走らせる。外から順に: state・世界・台本・相手が見た閉じ(台本の記録から読む)。"
  (<- answer (with_handlers [(state) (contract-ws-world SCRIPTED-WORLD) (scripted-ws-client SCRIPT) scripted-closures] program))
  answer)


(val INTERPRETERS {AIOHTTP under-aiohttp
                   SCRIPTED under-scripted})
