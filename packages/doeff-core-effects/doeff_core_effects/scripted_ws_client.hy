;;; 汎用の WebSocket の client の effect(ws_client_effects.hy)の I/O なしの答え手 scripted-ws-client(agora-redesign #4007 U0)。本物の
;;; socket を開かず、台本(WsScript — 繋ぎ先ごとの届く frame の列と、送りに応じて届く frame)で答え、送った文と閉じを記録する。業務を
;;; 知らない: 台本の中身は呼び手が渡す。
;;;
;;;   WsConnect        台本に url(url の先頭の最長の一致)が在れば WsLink(id は繋いだ順に ws-1 ・ws-2 …)、無ければ WsConnectFailed(status None)
;;;   WsReceive        台本の次の frame を答える。尽きたら then-close が在ればその状態符と理由の WsLinkClosed、無ければ状態符なしで
;;;                    SCRIPT-EXHAUSTED-REASON の WsLinkClosed(呼び手の Program を永遠に待たせない)。終わった・知らない idは closed-answer
;;;   WsSend           送った文を記録し(WsSentText)、台本の replies のうち送った文が含む語の frame の列を届く列の後ろへ足す(sideband の
;;;                    append → ACK の往復を模擬する)。終わった・知らない idは closed-answer
;;;   WsDisconnect     閉じを記録し(WsSentClose)、接続を終わらせる(以後の WsReceive / WsSend は同じ WsLinkClosed)
;;;   WsDisconnectAll  開いている接続の全部に同じ事をする
;;;   ReadWsSent       送った文と閉じの記録(WsSentText・WsSentClose の tuple)
;;; 本物の答え手と同じに決める物は ws_client_effects.hy の判断の defk を呼ぶ(契約テストは tests/test_ws_client_contract.hy)。
;;; 公開の installer scripted-ws-client は、program を closing-links(範囲の終わりに WsDisconnectAll)で包んでから被せる — 本物と同じ後始末。
;;; 並び: session の値を持つ state の handler(doeff_core_effects の state)はこの handler より外側に要る。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "ws-client" :role "foundation"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import doeff [EffectBase Program])
(import doeff_core_effects.ws_client_effects [WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll ReadWsSent WsLink WsConnectFailed
                                              WsText WsBinary WsLinkClosed WsFrame WsSent WsScript ScriptedWsEndpoint ScriptedWsText
                                              ScriptedWsBinary ScriptedWsClosed ScriptedWsFrame WsSentText WsSentClose
                                              NOT-IN-SCRIPT-REASON SCRIPT-EXHAUSTED-REASON link-name closed-answer closing-links])

;; 答える効果と、節が出す効果(installer の宣言 — doeff-effect-analyzer の __doeff_handles__ / __doeff_effects__)。
(val SCRIPTED-WS-HANDLES #(WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll ReadWsSent))
(val SCRIPTED-WS-EFFECTS #())


(defrecord ScriptedLink
  "台本の上で開いている接続 1 本: link = id・url・endpoint = 当たった繋ぎ先の台本・pending = まだ届けていない frame の列。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str url)
  (#^ ScriptedWsEndpoint endpoint)
  (#^ (get tuple #(ScriptedWsFrame ...)) pending))


(defk endpoint-for [script url]
  {:pre [(: script WsScript) (: url str)] :post [(: % (| ScriptedWsEndpoint None))] :tags {:context "ws-client" :role "judgment"}}
  "url への WsConnect を受ける台本の繋ぎ先を選ぶため(url の先頭の最長の一致 — 無ければ None = 台本に無い)。"
  (val hits (sorted (gfor e script.endpoints :if (.startswith url (.rstrip e.url "/")) e) :key (fn [e] (len e.url))))
  (if hits (get hits -1) None))


(defk link-of [links link]
  {:pre [(: links (of tuple ScriptedLink ...)) (: link str)] :post [(: % (| ScriptedLink None))]}
  "開いている接続の列からid の接続を引くため(無ければ None)。"
  (next (gfor open links :if (= open.link link) open) None))


(defk without-link [links link]
  {:pre [(: links (of tuple ScriptedLink ...)) (: link str)] :post [(: % (of tuple ScriptedLink ...))]}
  "開いている接続の列からid の接続を外すため。"
  (tuple (gfor open links :if (!= open.link link) open)))


(defk frame-of [link scripted]
  {:pre [(: link str) (: scripted (| ScriptedWsText ScriptedWsBinary ScriptedWsClosed))] :post [(: % WsFrame)]
   :tags {:context "ws-client" :role "judgment"}}
  "台本の frame 1 つを、id の接続に届いた frame にするため。"
  (match scripted
    (ScriptedWsText :text text) (WsText :link link :text text)
    (ScriptedWsBinary :data data) (WsBinary :link link :data data)
    (ScriptedWsClosed :code code :reason reason) (WsLinkClosed :link link :code code :reason reason)))


(defk next-frame [open]
  {:pre [(: open ScriptedLink)] :post [(: % WsFrame)] :tags {:context "ws-client" :role "judgment"}}
  "id の接続の次の frame を決めるため(先頭の説明): 届けていない frame が在ればその先頭、尽きていれば then-close かSCRIPT-EXHAUSTED-REASON の
   WsLinkClosed。"
  (match open.pending
    #() (match open.endpoint.then-close
          None (WsLinkClosed :link open.link :code None :reason SCRIPT-EXHAUSTED-REASON)
          closing (! (frame-of open.link closing)))
    _ (! (frame-of open.link (get open.pending 0)))))


(defk replies-for [endpoint text]
  {:pre [(: endpoint ScriptedWsEndpoint) (: text str)] :post [(: % (of tuple ScriptedWsFrame ...))]
   :tags {:context "ws-client" :role "judgment"}}
  "送った文に応じて届く frame の列を決めるため(台本の replies のうち、送った文がその語を含む物を台本の順に — 全部つなぐ)。"
  (tuple (gfor reply endpoint.replies :if (in reply.contains text) frame reply.frames frame)))


(defhandler scripted-ws-link-handler [#^ WsScript script]
  ;; 台本の接続(先頭の説明)。links = 開いている接続の列・ended = 終わった接続の WsLinkClosed の列(閉じた後の WsReceive / WsSend が同じ答えを
  ;; 返す)・sent = 送った文と閉じの記録・counter = 振った id の数(どれも session の値)。
  ;; 引数に残す理由: script は呼び手が組んだ凍った台本(繋ぎ先と届く frame)で、組み立ての外で差し替える相手がいない。
  (session var links #())
  (session var ended #())
  (session var sent #())
  (session var counter 0)
  (WsConnect [url headers]
    (<- endpoint (| ScriptedWsEndpoint None) (endpoint-for script url))
    (match endpoint
      None (resume (WsConnectFailed :url url :reason NOT-IN-SCRIPT-REASON :status None))
      found (do (:= counter (+ counter 1))
                (<- link str (link-name counter))
                (:= links (+ links #((ScriptedLink :link link :url url :endpoint found :pending found.frames))))
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


(deff scripted-ws-client [#^ WsScript script]  ; defk にできない: Program の外で呼ぶ、答え手を被せる installer の作り手
  {:pre [(: script WsScript)] :post [(: % (of Callable [(| Program EffectBase)] Program))] :tags {:context "ws-client" :role "foundation"}}
  "台本の答え手の installer を作るため(先頭の説明): 被せた program を closing-links で包み、範囲の終わりに開いたままの接続を閉じる。"
  (setv install (scripted-ws-link-handler script))
  (setv closing (fn [#^ (| Program EffectBase) program] (install (closing-links program))))
  ;; 被せる関数のマーカー(defhandler が付ける物と同じ属性を置く — with_handlers はマーカーの無い Program → Program の関数を断る)と、宣言。
  (setattr closing "_doeff_is_handler_fn" True)
  (setattr closing "__doeff_name__" "scripted-ws-client")
  (setattr closing "__doeff_handler_data__" (getattr install "__doeff_handler_data__"))
  (setattr closing "__doeff_handles__" SCRIPTED-WS-HANDLES)
  (setattr closing "__doeff_effects__" SCRIPTED-WS-EFFECTS)
  closing)


;; 節を静的に読めない installer の作り手なので、答える効果と出す効果を宣言する(本番の土台の閉じ具合の検が「読めない handler」と
;; 数えないため — _http_handlers_impl.hy の http-production-handler と同じ)。
(setattr scripted-ws-client "__doeff_handles__" SCRIPTED-WS-HANDLES)
(setattr scripted-ws-client "__doeff_effects__" SCRIPTED-WS-EFFECTS)
