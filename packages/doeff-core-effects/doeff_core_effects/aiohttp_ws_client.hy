;;; 汎用の WebSocket の client の effect(ws_client_effects.hy)の本物の答え手 aiohttp-ws-client(agora-redesign #4007 U0)。aiohttp の
;;; ClientSession.ws_connect で繋ぐ・frame を受け渡す・close frame を送る実 I/O で、判断を持たない。aiohttp は extra `http-server` の依存。
;;;
;;;   loop          節は Await で await-handler の共有の event loop に入り、その上で aiohttp の coroutine を走らせる(handler の組み立ての外側に
;;;                 await-handler と state が要る — session の値は state の効果で持つ)。_http_handlers_impl.hy の HttpRequest の答え手と同じ載せ方
;;;   client        接続ごとに aiohttp の ClientSession を 1 つ作る(繋ぐ coroutine の中 = 共有の loop の上で作り、接続を閉じる時に一緒に
;;;                 閉じる)。1 つの client を範囲をまたいで使い回さない(#3415 — 閉じた client を次の範囲が使う形を作らない)。作り手は installer の
;;;                 client-factory(既定 new-client-session — 検は作った client を数える作り手を渡す)
;;;   並行          同じ handler の下で複数の接続が同時に進む(音声の service は session ごとに繋ぐ)。接続の表(open・ended)は session の値の
;;;                 1 つの dict で、節は Await の後も同じ表を参照し、参照してから書くまでに await を挟まない。id の番号は繋ぎを始める前(await の前)に
;;;                 確保し、断られても戻さない — await の後に増やすと、同時に進む 2 つの繋ぎが同じ番号を読む
;;;   WsConnect     ws_connect(headers は handshake の要求のヘッダー)。繋ぐまでの上限は CONNECT-SECONDS・101 の答えを待つ上限は HANDSHAKE-SECONDS。
;;;                 handshake を断られれば WsConnectFailed(status = 断りの status)、届かなければ WsConnectFailed(status None)。答えが決まらずに
;;;                 抜ける時(CancelledError — scheduler の Cancel・Race の負け)も client と ws を閉じ、開いたままにしない。
;;;                 1 通の上限は MAX-MESSAGE-BYTES(超える 1 通は aiohttp が接続を切る → WsLinkClosed)
;;;   WsReceive     次の frame を待つ: TEXT → WsText・BINARY → WsBinary・CLOSE(相手の close frame)→ その状態符と理由の WsLinkClosed・
;;;                 CLOSED / ERROR(close frame なしに切れた)→ 接続の状態符(1006 等・無ければ None)の WsLinkClosed。ping には aiohttp の
;;;                 autoping が pong を返す(ここで待っている間も)。終わった接続はその WsLinkClosed をもう 1 度・知らない id は closed-answer
;;;   WsSend        send_str。相手が閉じた後の送り(書けない transport)は WsLinkClosed
;;;   WsDisconnect  close frame を送り、相手の close を CLOSE-SECONDS まで待って閉じる(aiohttp の ws_close)。答えの状態符と理由は渡した物
;;;   WsDisconnectAll 開いている接続の全部に WsDisconnect と同じ事をする(答え手の範囲の終わりの後始末 — installer の closing-links が出す)
;;; 理由の文(WsConnectFailed.reason・WsLinkClosed.reason)は例外の型の名と status だけ(failure-detail)。例外の文は要求の url(query を含む)や
;;; ヘッダー(Authorization の Bearer の値)を運ぶことがあるので載せない — protocol の記録の境界(tests/test_ws_client.hy が固定する)。
;;; 本物の答え手が heartbeat(自分から ping を送る)を持たない理由: 相手(OpenAI の sideband)が ping を送り、こちらは autoping で答える。
;;; 自分から ping が要る相手が出たら WsConnect の欄として足す(設計の註)。
;;; 本物と台本が同じに決める物は ws_client_effects.hy の判断の defk を呼ぶ(契約テストは tests/test_ws_client_contract.hy)。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "ws-client" :role "foundation"})
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import aiohttp)
(import aiohttp [WSMsgType ClientWSTimeout])
(import doeff [EffectBase Program])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.http_server_effects [HttpHeader])
(import doeff_core_effects.ws_client_effects [WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll WsLink WsConnectFailed WsText WsBinary
                                              WsLinkClosed WsFrame WsSent link-name closed-answer closing-links])

;; 繋ぐまで(名前の引きと TCP の接続)の上限と、繋いだ後の handshake の答え(101)を待つ上限(秒)— 待ち受けの ws の中継と同じ 2 つの分け方。
(val CONNECT-SECONDS 10.0)
(val HANDSHAKE-SECONDS 15.0)
;; close frame を送ってから相手の close を待つ上限(秒 — aiohttp の ws_close)。
(val CLOSE-SECONDS 10.0)
;; 1 通の上限(byte)— 超える 1 通は aiohttp が接続を切る。
(val MAX-MESSAGE-BYTES (* 4 1024 1024))

;; 答える効果と、節が出す効果(installer の宣言 — doeff-effect-analyzer の __doeff_handles__ / __doeff_effects__)。
(val AIOHTTP-WS-HANDLES #(WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll))
(val AIOHTTP-WS-EFFECTS #(Await))


(defrecord OpenLink
  "繋いだ接続 1 本: link = id・url・session = この接続だけの aiohttp の client・ws = 繋いだ socket。"
  {:tags {:context "ws-client" :role "type"}}
  (#^ str link)
  (#^ str url)
  (#^ aiohttp.ClientSession session)
  (#^ aiohttp.ClientWebSocketResponse ws))


(defn #^ aiohttp.ClientSession new-client-session []  ; defk にできない: 繋ぐ coroutine の中(走っている event loop の上)で呼ぶ client の作り手(client-factory の既定)
  "接続 1 本のための aiohttp の client を作るため: 繋ぐまでの上限と handshake の答えの上限を持ち、total は置かない(繋いだ ws を時間で切らない)。"
  (aiohttp.ClientSession :timeout (aiohttp.ClientTimeout :total None :connect CONNECT-SECONDS :sock-read HANDSHAKE-SECONDS)))


(defn #^ str failure-detail [#^ BaseException error]  ; defk にできない: aiohttp の coroutine の中(Program の外)で呼ぶ
  "繋がらなかった・切れた理由の文を決めるため: 例外の型の名と、答えがあればその status だけ(先頭の説明の記録の境界 — 例外の文は要求の
   url や ヘッダーを運ぶことがあるので載せない。分岐に使わない)。"
  (if (isinstance error aiohttp.ClientResponseError)
      (.format "{}: status {}" (. (type error) __name__) error.status)
      (. (type error) __name__)))


(defn :async #^ (| OpenLink WsConnectFailed) open-link [#^ (get Callable #([] aiohttp.ClientSession)) client-factory #^ str link #^ str url
                                                         #^ (get tuple #(HttpHeader ...)) headers]  ; defk にできない: aiohttp の実 I/O(共有の event loop の coroutine)
  "url へ繋ぐため(先頭の説明の WsConnect): この接続だけの client を作って ws_connect。断られた・届かなかった時は WsConnectFailed。
   答えが OpenLink に決まらずに抜ける時(断り・届かない・CancelledError)は finally で ws と client を閉じ、開いたままにしない。"
  (setv session (client-factory))
  (setv ws None)
  (setv outcome None)
  (try
    (setv ws (await (.ws-connect session url :headers (dfor h headers h.name h.value) :max-msg-size MAX-MESSAGE-BYTES
                                 :timeout (ClientWSTimeout :ws-receive None :ws-close CLOSE-SECONDS))))
    (setv outcome (OpenLink :link link :url url :session session :ws ws))
    (except [error aiohttp.WSServerHandshakeError]
      (setv outcome (WsConnectFailed :url url :reason (failure-detail error) :status error.status)))
    (except [error #(aiohttp.ClientError OSError asyncio.TimeoutError)]
      (setv outcome (WsConnectFailed :url url :reason (failure-detail error) :status None)))
    (finally
      (when (not (isinstance outcome OpenLink))
        (when (is-not ws None)
          (await (.close ws)))
        (await (.close session)))))
  outcome)


(defn :async #^ WsFrame receive-frame [#^ OpenLink open]  ; defk にできない: aiohttp の実 I/O(共有の event loop の coroutine)
  "id の接続の次の frame を待つため(先頭の説明の WsReceive)。ping / pong / 閉じの途中は次を待つ。"
  (while True
    (setv message (await (.receive open.ws)))
    (match message.type
      WSMsgType.TEXT (return (WsText :link open.link :text message.data))
      WSMsgType.BINARY (return (WsBinary :link open.link :data message.data))
      WSMsgType.CLOSE (return (WsLinkClosed :link open.link :code (int message.data) :reason (or message.extra "")))
      WSMsgType.CLOSED (return (WsLinkClosed :link open.link :code (lost-code open.ws) :reason ""))
      WSMsgType.ERROR (return (WsLinkClosed :link open.link :code (lost-code open.ws) :reason (failure-detail message.data)))
      _ None)))


(defn #^ (| int None) lost-code [#^ aiohttp.ClientWebSocketResponse ws]  ; defk にできない: aiohttp の socket の状態を読む実 I/O
  "close frame なしに終わった接続の状態符を読むため(aiohttp が付けた close_code — 無ければ None)。"
  (if (is ws.close-code None) None (int ws.close-code)))


(defn :async #^ (| WsSent WsLinkClosed) send-text [#^ OpenLink open #^ str text]  ; defk にできない: aiohttp の実 I/O(共有の event loop の coroutine)
  "id の接続へ文字の 1 通を送るため(先頭の説明の WsSend)。閉じた・書けない接続は WsLinkClosed。"
  (when open.ws.closed
    (return (WsLinkClosed :link open.link :code (lost-code open.ws) :reason "")))
  (try
    (await (.send-str open.ws text))
    (except [error #(aiohttp.ClientError ConnectionResetError RuntimeError)]
      (return (WsLinkClosed :link open.link :code (lost-code open.ws) :reason (failure-detail error)))))
  (WsSent :link open.link))


(defn :async #^ WsLinkClosed close-link [#^ OpenLink open #^ int code #^ str reason]  ; defk にできない: aiohttp の実 I/O(共有の event loop の coroutine)
  "id の接続を状態符と理由で閉じるため(先頭の説明の WsDisconnect): close frame を送って相手の close を CLOSE-SECONDS まで待ち、client も閉じる。"
  (try
    (await (.close open.ws :code code :message (.encode reason "utf-8")))
    (except [#(aiohttp.ClientError ConnectionResetError RuntimeError asyncio.TimeoutError)] None)
    (finally
      (await (.close open.session))))
  (WsLinkClosed :link open.link :code code :reason reason))


(defn :async #^ None drop-link [#^ OpenLink open]  ; defk にできない: aiohttp の実 I/O(共有の event loop の coroutine)
  "終わった接続の client を閉じるため(相手が閉じた・切れた後の後始末)。"
  (await (.close open.session))
  None)


(defhandler aiohttp-ws-link-handler [#^ (get Callable #([] aiohttp.ClientSession)) client-factory]
  ;; 本物の接続(先頭の説明)。open = 開いている接続の表(id → OpenLink)・ended = 終わった接続の答えの表(id → WsLinkClosed — 閉じた後の
  ;; WsReceive / WsSend が同じ答えを返す)。どちらも session の値の 1 つの dict: 節は Await の後も同じ表を参照し、参照してから書くまでに await を
  ;; 挟まない(session var の tuple にすると節の初めに取った値を await の後に書き戻し、並行の節の書きを消す)。counter = 振った id の数(session var
  ;; — await の前に増やすので、並行の 2 つの WsConnect が同じ番号を読まない)。
  ;; 引数に残す理由: client の作り手は組み立ての側で決まる(検は作った client を数える作り手を渡す)。
  (session val open ((get dict #(str OpenLink))))
  (session val ended ((get dict #(str WsLinkClosed))))
  (session var counter 0)
  (WsConnect [url headers]
    ;; 番号は繋ぎを始める前に確保し、断られても戻さない(await の後に増やすと、同時に進む 2 つの繋ぎが同じ番号を取る — 台本の答え手も同じ並び)。
    (:= counter (+ counter 1))
    (<- link str (link-name counter))
    (<- opened (| OpenLink WsConnectFailed) (Await (open-link client-factory link url headers)))
    (match opened
      (OpenLink) (do (setv (get open link) opened)
                     (resume (WsLink :link link :url url)))
      _ (resume opened)))
  (WsReceive [link]
    (match (.get open link)
      None (do (<- gone WsLinkClosed (closed-answer (tuple (.values ended)) link))
               (resume gone))
      found (do (<- frame WsFrame (Await (receive-frame found)))
                (match frame
                  (WsLinkClosed) (do (<- (Await (drop-link found)))
                                     (.pop open link None)
                                     (setv (get ended link) frame))
                  _ None)
                (resume frame))))
  (WsSend [link text]
    (match (.get open link)
      None (do (<- gone WsLinkClosed (closed-answer (tuple (.values ended)) link))
               (resume gone))
      found (do (<- outcome (| WsSent WsLinkClosed) (Await (send-text found text)))
                (match outcome
                  (WsLinkClosed) (do (<- (Await (drop-link found)))
                                     (.pop open link None)
                                     (setv (get ended link) outcome))
                  _ None)
                (resume outcome))))
  (WsDisconnect [link code reason]
    (match (.get open link)
      None (do (<- gone WsLinkClosed (closed-answer (tuple (.values ended)) link))
               (resume gone))
      found (do (<- closed WsLinkClosed (Await (close-link found code reason)))
                (.pop open link None)
                (setv (get ended link) closed)
                (resume closed))))
  (WsDisconnectAll [code reason]
    (var closing #())
    (for [found (tuple (.values open))]
      (<- closed WsLinkClosed (Await (close-link found code reason)))
      (.pop open found.link None)
      (setv (get ended found.link) closed)
      (:= closing (+ closing #(closed))))
    (resume closing)))


(deff aiohttp-ws-client [* [client-factory new-client-session]]  ; defk にできない: Program の外で呼ぶ、答え手を被せる installer の作り手
  {:pre [(: client-factory (get Callable #([] aiohttp.ClientSession)))] :post [(: % (of Callable [(| Program EffectBase)] Program))] :tags {:context "ws-client" :role "foundation"}}
  "本物の答え手の installer を作るため(先頭の説明): 被せた program を closing-links で包み、範囲の終わりに開いたままの接続を閉じる。
   client-factory = 接続ごとの client の作り手(既定 new-client-session)。"
  (setv install (aiohttp-ws-link-handler client-factory))
  (setv closing (fn [#^ (| Program EffectBase) program] (install (closing-links program))))
  ;; 被せる関数のマーカー(defhandler が付ける物と同じ属性を置く — with_handlers はマーカーの無い Program → Program の関数を断る)と、宣言。
  (setattr closing "_doeff_is_handler_fn" True)
  (setattr closing "__doeff_name__" "aiohttp-ws-client")
  (setattr closing "__doeff_handler_data__" (getattr install "__doeff_handler_data__"))
  (setattr closing "__doeff_handles__" AIOHTTP-WS-HANDLES)
  (setattr closing "__doeff_effects__" AIOHTTP-WS-EFFECTS)
  closing)


;; 節を静的に読めない installer の作り手なので、答える効果と出す効果を宣言する(本番の土台の閉じ具合の検が「読めない handler」と
;; 数えないため — _http_handlers_impl.hy の http-production-handler と同じ)。
(setattr aiohttp-ws-client "__doeff_handles__" AIOHTTP-WS-HANDLES)
(setattr aiohttp-ws-client "__doeff_effects__" AIOHTTP-WS-EFFECTS)
