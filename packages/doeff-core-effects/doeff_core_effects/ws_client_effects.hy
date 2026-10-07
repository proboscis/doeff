;;; 汎用の WebSocket の client(繋ぐ側)の effect(agora-redesign #4007 U0・消費者 = 音声の service が OpenAI の sideband の WebSocket に
;;; 付くため)。待ち受け側(http_server_effects.hy の WsOpened / WsTextArrived / WsClosed …)と対になる土台の語彙で、業務の語を持たない。
;;; 名は待ち受け側と衝突しない(WsConnect / WsReceive / WsSend / WsDisconnect と WsLink / WsText / WsBinary / WsLinkClosed)。
;;; 答え手は仕組みごとに差し替える:
;;;   aiohttp-ws-client   本物(aiohttp_ws_client.hy — aiohttp は extra `http-server` の依存)
;;;   scripted-ws-client  I/O なし — 台本の frame の列で答え、送った文を記録する(scripted_ws_client.hy)
;;;
;;;   WsConnect        url へ繋ぐ(headers = handshake の要求に載せるヘッダーの列 — Authorization 等)。答え = WsConnectOutcome:
;;;                      WsLink(link・url)                  繋がった。link = 答え手が振る接続の id(以後の effect はこの id で接続を指す)。番号は繋ぎを始めた順(ws-1・ws-2 …)で、
;;;                                                          断られた繋ぎの番号も戻さない — 同時に進む繋ぎが同じ番号を取らないため(番号は繋ぎを始める前に確保する)
;;;                      WsConnectFailed(url・reason・status) 繋がらなかった。status = handshake を断った status(届かなければ None)。reason は
;;;                                                          人と log のための文で、要求のヘッダーの値や url の query を含まない(記録の境界)
;;;   WsReceive        id の接続の次の frame を待つ。答え = WsFrame:
;;;                      WsText(link・text)                  文字の 1 通
;;;                      WsBinary(link・data)                byte の 1 通
;;;                      WsLinkClosed(link・code・reason)    接続が終わった(相手の close ならその状態符と理由・こちらの WsDisconnect なら
;;;                                                          渡した状態符と理由・切れた時は 1006 等と空の理由・状態符が無ければ None)。
;;;                                                          終わった後の WsReceive も同じ WsLinkClosed。知らない idは reason = UNKNOWN-LINK-REASON
;;;   WsSend           id の接続へ文字の 1 通を送る。答え = WsSendOutcome: WsSent(link)か、終わった接続なら WsLinkClosed
;;;   WsDisconnect     id の接続を状態符と理由で閉じる(close frame を送る)。答え = WsLinkClosed(渡した状態符と理由)。終わった接続・知らない id
;;;                    なら、その接続の WsLinkClosed(閉じ直さない)
;;;   WsDisconnectAll  開いている接続の全部を状態符と理由で閉じる。答え = 閉じた接続の WsLinkClosed の列(開いた順)。答え手の範囲の終わりの
;;;                    後始末(closing-links — 答え手の installer が program の finally で実行する)が使う。呼び手が出してもよい
;;; 答えは例外ではなく値 — 呼び手は match で網羅する。
;;; 2 つの答え手が同じに決める物は、ここの判断の defk を両方が呼ぶ: link-name(id の綴り)・closed-answer(終わった・知らない idへの答え)。
;;; 2 つが同じ性質を持つことは tests/test_ws_client_contract.hy の契約テストが両方の答え手で確かめる。
;;;
;;; 台本の語彙(本物の答え手には無い): WsScript・ScriptedWsEndpoint・ScriptedWsReply・ScriptedWsText / ScriptedWsBinary / ScriptedWsClosed =
;;; 台本・WsSentText / WsSentClose = 送った文と閉じの記録・ReadWsSent = 記録を読む effect(検と筋書きが覗くため)。
(require doeff-hy.macros [defeffect defk val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import doeff [EffectBase Program])
(import doeff_core_effects.http_server_effects [HttpHeader])

;; ws の閉じの状態符(RFC 6455 7.4.1)— 答え手が決める値。
(val WS-LINK-CLOSE-NORMAL 1000)
(val WS-LINK-CLOSE-GOING-AWAY 1001)
(val WS-LINK-CLOSE-ABNORMAL 1006)
;; 知らない idへの答えの理由(答え手が決める値)。
(val UNKNOWN-LINK-REASON "unknown-link")
;; 台本の frame が尽きた接続への答えの理由(台本の答え手が決める値)。
(val SCRIPT-EXHAUSTED-REASON "script-exhausted")
;; 台本に無い url への答えの理由(台本の答え手が決める値)。
(val NOT-IN-SCRIPT-REASON "台本に無い url")
;; 答え手の範囲の終わりに開いたままの接続を閉じる時の理由(closing-links が付ける物)。
(val SCOPE-ENDED-REASON "答え手の範囲が終わった")


(defrecord WsLink
  "WsConnect の答え: 繋がった。link = 答え手が振った接続の id・url = 繋いだ url。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str url))


(defrecord WsConnectFailed
  "WsConnect の答え: 繋がらなかった。reason = 理由の文(人と log のため — 分岐に使わない)・status = handshake を断った status(届かなければ
   None)。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str url)
  (#^ str reason)
  (#^ (| int None) status))


(val WsConnectOutcome (| WsLink WsConnectFailed))


(defrecord WsText
  "WsReceive の答え: id の接続に文字の 1 通が届いた。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str text))


(defrecord WsBinary
  "WsReceive の答え: id の接続に byte の 1 通が届いた。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ bytes data))


(defrecord WsLinkClosed
  "id の接続が終わった(先頭の説明): code = 閉じの状態符(無ければ None)・reason = 理由の文。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ (| int None) code)
  (#^ str reason))


(val WsFrame (| WsText WsBinary WsLinkClosed))


(defrecord WsSent
  "WsSend の答え: id の接続へ送った。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link))


(val WsSendOutcome (| WsSent WsLinkClosed))


(defeffect WsConnect
  "url へ繋ぐ(先頭の説明)。headers = handshake の要求に載せるヘッダーの列。答え = WsLink | WsConnectFailed。"
  {:fields [(: url str) (: headers (get tuple #(HttpHeader ...)) #())]
   :answer (| WsLink WsConnectFailed)
   :tags {:context "ws-client" :role "foundation"}})


(defeffect WsReceive
  "id の接続の次の frame を待つ(先頭の説明)。答え = WsText | WsBinary | WsLinkClosed。"
  {:fields [(: link str)]
   :answer (| WsText WsBinary WsLinkClosed)
   :tags {:context "ws-client" :role "foundation"}})


(defeffect WsSend
  "id の接続へ文字の 1 通を送る(先頭の説明)。答え = WsSent | WsLinkClosed。"
  {:fields [(: link str) (: text str)]
   :answer (| WsSent WsLinkClosed)
   :tags {:context "ws-client" :role "foundation"}})


(defeffect WsDisconnect
  "id の接続を状態符と理由で閉じる(先頭の説明)。答え = WsLinkClosed。"
  {:fields [(: link str) (: code int WS-LINK-CLOSE-NORMAL) (: reason str "")]
   :answer WsLinkClosed
   :tags {:context "ws-client" :role "foundation"}})


(defeffect WsDisconnectAll
  "開いている接続の全部を状態符と理由で閉じる(先頭の説明)。答え = 閉じた接続の WsLinkClosed の列(開いた順)。"
  {:fields [(: code int WS-LINK-CLOSE-GOING-AWAY) (: reason str SCOPE-ENDED-REASON)]
   :answer (get tuple #(WsLinkClosed ...))
   :tags {:context "ws-client" :role "foundation"}})


;; --- 答え手が共に呼ぶ判断(本物の aiohttp-ws-client と台本の scripted-ws-client が同じ関数で決める)----------------------------------

(defk link-name [n]
  {:pre [(: n int)] :post [(: % str)] :tags {:context "ws-client" :role "judgment"}}
  "n 本目の接続のid の綴りを決めるため(繋いだ順に 1 から)。"
  (.format "ws-{}" n))


(defk closed-answer [ended link]
  {:pre [(: ended (of tuple WsLinkClosed ...)) (: link str)] :post [(: % WsLinkClosed)] :tags {:context "ws-client" :role "judgment"}}
  "開いていない idへの答えを決めるため(先頭の説明): 終わった接続(ended)ならその時の WsLinkClosed をもう 1 度、知らない idなら状態符なしで
   UNKNOWN-LINK-REASON。"
  (val known (next (gfor closed ended :if (= closed.link link) closed) None))
  (if (is known None)
      (WsLinkClosed :link link :code None :reason UNKNOWN-LINK-REASON)
      known))


(defk closing-links [program]
  {:pre [(: program (| Program EffectBase))] :post [(: % "program の答え(型は program ごと)")]
   :tags {:context "ws-client" :role "foundation"}}
  "program を走らせ、終わり(答えでも例外でも)に開いたままの接続の全部を閉じるため(先頭の説明の答え手の範囲の後始末 — 答え手の installer が
   program をこれで包んでから被せる)。"
  (try
    (<- answer program)
    answer
    (finally
      (<- (WsDisconnectAll)))))


;; --- 台本の語彙(scripted-ws-client) ----------------------------------------------------------------------------------------------

(defrecord ScriptedWsText
  "台本の frame: 文字の 1 通が届く。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str text))


(defrecord ScriptedWsBinary
  "台本の frame: byte の 1 通が届く。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ bytes data))


(defrecord ScriptedWsClosed
  "台本の frame: 相手が状態符と理由で閉じる。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ int code)
  (#^ str reason))


(val ScriptedWsFrame (| ScriptedWsText ScriptedWsBinary ScriptedWsClosed))


(defrecord ScriptedWsReply
  "送りに応じて届く frame の台本 1 つ: contains = 送った文がこの語を含めば・frames = その後に届く frame の列(届く列の後ろへ足す)。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str contains)
  (#^ (get tuple #(ScriptedWsFrame ...)) frames))


(defrecord ScriptedWsEndpoint
  "台本の繋ぎ先 1 つ: url = この文字列で始まる url への WsConnect がこの台本を受ける(最長の一致)・frames = 繋いだ接続に届く frame の列・
   replies = 送りに応じて届く frame(送った文に含まれる語 → frame の列)・then-close = frame が尽きた後の相手の閉じ(None なら
   SCRIPT-EXHAUSTED-REASON で状態符なしの WsLinkClosed — 呼び手の Program を待たせない)。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str url)
  (setv #^ (get tuple #(ScriptedWsFrame ...)) frames #()
        #^ (get tuple #(ScriptedWsReply ...)) replies #()
        #^ (| ScriptedWsClosed None) then-close None))


(defrecord WsScript
  "scripted-ws-client の台本: 繋ぎ先の列。無い url への WsConnect は WsConnectFailed。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ (get tuple #(ScriptedWsEndpoint ...)) endpoints))


(defrecord WsSentText
  "id の接続へ送った文字の 1 通(台本の記録)。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str text))


(defrecord WsSentClose
  "id の接続へ送った閉じ(台本の記録 — WsDisconnect・WsDisconnectAll)。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ int code)
  (#^ str reason))


(val WsSentRecord (| WsSentText WsSentClose))


(defeffect ReadWsSent
  "台本の答え手が送った文と閉じの記録(WsSentText・WsSentClose の送った順の tuple)を読む(先頭の説明)。"
  {:answer (get tuple #((| WsSentText WsSentClose) ...))
   :tags {:context "ws-client" :role "foundation"}})
