;;; 汎用の HTTP の待ち受け(受ける側)の effect(agora-redesign #802 便 2・消費者 = #795 webapp の受け口)。送る側の HttpRequest
;;; (http_effects.hy)と対になる土台の語彙で、業務の語を持たない。答え手は仕組みごとに差し替える:
;;;   aiohttp-http-server   本物の待ち受け(aiohttp_http_server.hy — aiohttp は extra `http-server` の依存)
;;;   scripted-http-server  I/O なし — 台本の要求の列を出来事にし、受けた命令を記録する(scripted_http_server.hy)
;;;
;;;   HttpListen       待ち受けを開く(address・ws の 1 通の上限・ws の接続ごとの送りの上限)。答え = 実際に結んだ宛先 HttpAddress
;;;                    (port 0 を渡せば空いている port を結ぶ — 結んだ port はこの答えで知る)
;;;   HttpNextRequest  次の出来事。答え = HttpEvent: HttpRequestArrived(札・method・path・target・頭・ws への Upgrade を求めたか)・
;;;                    ws の出来事(WsOpened・WsTextArrived・WsBinaryArrived・WsClosed — WsAccept で ws に上げた札だけ)・HttpServerClosed
;;;   HttpRespond      札の要求へ status・頭・本文(HttpBodyBytes / HttpBodyFileRange / HttpNoBody)を送る。答え = None
;;;   HttpForward      札の要求を url へ HTTP で中継する(本文は両向き streaming・hop-by-hop の頭を落とし X-Forwarded-Proto / -For を足す・
;;;                    届かなければ 502)。答え = None
;;;   WsForward        札の要求を url へ ws で中継する(frame を両向きに写す・ping / pong と close の状態符を素通し・届かなければ 502)。答え = None
;;;   WsAccept         札の要求を ws に上げて、ここで終端する(handshake の検めと 101 は答え手 — 成らなければ断りの応答を返して 1 行名乗り、
;;;                    出来事は出ない)。上げ終えたら出来事 WsOpened、以後その接続の 1 通が WsTextArrived / WsBinaryArrived、接続が終われば
;;;                    WsClosed(状態符と理由)が HttpNextRequest の同じ流れに並ぶ。ping への pong・分割の組み立て・mask の検め・UTF-8 の検め・
;;;                    1 通の上限(HttpListen の ws-max-bytes)を超えた接続の切りは答え手が持つ。答え = None
;;; 札ごとに命令(HttpRespond・HttpForward・WsForward・WsAccept)はちょうど 1 つ。命令は撃つだけで待たない(長い中継が他の要求を止めない)。
;;; WsAccept で上げた接続へは次の 2 つを何度でも撃てる(どちらも撃つだけで待たない — 答え手が接続ごとの送りの箱に積み、書くのは答え手の側):
;;;   WsSendText       文字の 1 通を送る。閉じた・知らない札への送りは黙って捨てる。箱の溜まりが HttpListen の ws-send-max-bytes を超える
;;;                    接続(読まない相手)はその場で切る(箱の中身を捨て、出来事 WsClosed の理由に名乗る)。答え = None
;;;   WsClose          状態符と理由で閉じる(箱に積んだ 1 通を流し切ってから close を送る)。答え = None
;;; 待ち受けの全体へは:
;;;   HttpShutdown     待ち受けを閉じる — 新しい接続を受けず、開いている ws の全部へ close 1000 を送り、送りの箱を drain-seconds まで流し切って
;;;                    から閉じる。以後の HttpNextRequest は HttpServerClosed(reason)。答え = None
;;;   TakeWsSendReport 送りの箱の勘定(前に読んでから積んだ・流した・捨てた byte と流すまでの所要 — WsSendReport)を読んで 0 に戻す。
;;;                    消費者が自分の計器へ積むための材料で、答え手は消費者の計器を知らない
;;; 出来事の received-at = 答え手がその出来事を受けた拍の単調時計の秒(time.monotonic と同じ物差し — 消費者の待ちの計器の起点)。時計を
;;; 持たない台本の答え手では、台本の書き手が載せた値のまま(載せなければ None)。
;;; 要求の本文を読む effect は今の消費者に要らないので持たない(要る時に契約を足す)。file の状態は file_effects.hy の StatPath で読む(ここに持たない)。
;;;
;;; 台本の語彙(本物の待ち受けには無い): HttpScript・ScriptedUpstream = 台本・HttpServed = 受けた命令と端末が受け取る答え・
;;; WsTextSent / WsCloseSent = ws の接続へ送った 1 通と閉じ・ReadHttpServed = 記録を読む effect(検と筋書きが覗くため)・
;;; AppendHttpScript = 走っている台本の後ろへ出来事を足す effect(筋書きの相手役が時刻の来た拍に届ける — scripted-http-server だけが答える)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase])

;; ws の 1 通(中継では 1 frame)の上限の既定(byte)。
(val DEFAULT-WS-MAX-BYTES (* 1024 1024))
;; ws の接続ごとの送りの箱の上限の既定(byte)— 読まない相手の箱をここまで溜めて切る。
(val DEFAULT-WS-SEND-MAX-BYTES (* 16 1024 1024))
;; 待ち受けを閉じる時に送りの箱を流し切るのを待つ上限の既定(秒)。
(val DEFAULT-DRAIN-SECONDS 1.0)
;; ws の閉じの状態符(RFC 6455 7.4.1)— 答え手が名乗る物。
(val WS-CLOSE-NORMAL 1000)
(val WS-CLOSE-ABNORMAL 1006)


(defrecord HttpAddress
  "待ち受けの宛先。"
  (#^ str host)
  (#^ int port))


(defrecord HttpHeader
  "HTTP の頭 1 つ(同じ名が複数あっても落とさないよう列で持つ)。"
  (#^ str name)
  (#^ str value))


(defrecord HttpRequestArrived
  "届いた要求 1 つ: 札・method・path(query なし)・target(query つき)・頭の列・ws への Upgrade を求めたか・受けた拍(頭の註)。"
  (#^ str ticket)
  (#^ str method)
  (#^ str path)
  (#^ str target)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ bool upgrade)
  (setv #^ (| float None) received-at None))


(defrecord WsOpened
  "WsAccept で札の要求を ws に上げ終えた(以後この札の 1 通が届く)。"
  (#^ str ticket)
  (setv #^ (| float None) received-at None))


(defrecord WsTextArrived
  "ws に上げた札の接続に文字の 1 通が届いた(分割は組み立て済み・UTF-8 は検め済み)。"
  (#^ str ticket)
  (#^ str text)
  (setv #^ (| float None) received-at None))


(defrecord WsBinaryArrived
  "ws に上げた札の接続に byte の 1 通が届いた(受けるかは消費者が決める — 受けないなら WsClose で閉じる)。"
  (#^ str ticket)
  (#^ bytes data)
  (setv #^ (| float None) received-at None))


(defrecord WsClosed
  "ws に上げた札の接続が終わった: code = 閉じの状態符(相手の close・こちらの WsClose・切れた時は 1006)・reason = 理由の文
   (送りの上限で切った時はそう名乗る)。以後この札への送りは捨てられる。"
  (#^ str ticket)
  (#^ int code)
  (#^ str reason)
  (setv #^ (| float None) received-at None))


(defrecord HttpServerClosed
  "待ち受けが閉じた(HttpShutdown・台本が尽きた等)。"
  (#^ str reason))


;; HttpNextRequest の答えの union。
(val WsEvent (| WsOpened WsTextArrived WsBinaryArrived WsClosed))
(val HttpEvent (| HttpRequestArrived WsOpened WsTextArrived WsBinaryArrived WsClosed HttpServerClosed))


(defrecord WsSendReport
  "送りの箱の勘定(前に TakeWsSendReport で読んでから): queued-frames / queued-bytes = 箱へ積んだ 1 通の数と byte・flushed-bytes = 相手へ
   流した byte・flush-seconds = 流した 1 通ごとの積んでから流すまでの秒(新しい方から FLUSH-SAMPLES-LIMIT まで — byte の勘定は全数)・
   dropped-bytes = 捨てた byte(送りの上限で切った・接続が終わった時に箱に残った)・cuts = 送りの上限で切った接続の数。"
  (#^ int queued-frames)
  (#^ int queued-bytes)
  (#^ int flushed-bytes)
  (#^ (get tuple #(float ...)) flush-seconds)
  (#^ int dropped-bytes)
  (#^ int cuts))


;; 1 回の WsSendReport が持つ所要の標本の上限(読まれない間に際限なく溜めない)。
(val FLUSH-SAMPLES-LIMIT 4096)


(defrecord HttpBodyBytes
  "本文 = 決まった byte 列。"
  (#^ bytes data))


(defrecord HttpBodyFileRange
  "本文 = file の path の start から length byte(答え手が読んで送る — 大きい file を memory に載せない)。"
  (#^ str path)
  (#^ int start)
  (#^ int length))


(defrecord HttpNoBody
  "本文なし(304・HEAD・302)。")


(val HttpBody (| HttpBodyBytes HttpBodyFileRange HttpNoBody))


(defclass [(dataclass :frozen True)] HttpListen [EffectBase]
  "待ち受けを開く(頭の註)。答え = 結んだ宛先 HttpAddress。"
  (#^ HttpAddress address)
  (setv #^ int ws-max-bytes DEFAULT-WS-MAX-BYTES
        #^ int ws-send-max-bytes DEFAULT-WS-SEND-MAX-BYTES))


(defclass [(dataclass :frozen True)] HttpNextRequest [EffectBase]
  "次の出来事を 1 つ受ける(頭の註)。")


(defclass [(dataclass :frozen True)] HttpRespond [EffectBase]
  "札の要求へ status・頭・本文を送る(頭の註)。"
  (#^ str ticket)
  (#^ int status)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ HttpBody body))


(defclass [(dataclass :frozen True)] HttpForward [EffectBase]
  "札の要求を url へ HTTP で中継する(頭の註)。"
  (#^ str ticket)
  (#^ str url))


(defclass [(dataclass :frozen True)] WsForward [EffectBase]
  "札の要求を url へ ws で中継する(頭の註)。"
  (#^ str ticket)
  (#^ str url))


(defclass [(dataclass :frozen True)] WsAccept [EffectBase]
  "札の要求を ws に上げて、ここで終端する(頭の註)。"
  (#^ str ticket))


(defclass [(dataclass :frozen True)] WsSendText [EffectBase]
  "ws に上げた札の接続へ文字の 1 通を送る(頭の註)。"
  (#^ str ticket)
  (#^ str text))


(defclass [(dataclass :frozen True)] WsClose [EffectBase]
  "ws に上げた札の接続を状態符と理由で閉じる(頭の註)。"
  (#^ str ticket)
  (#^ int code)
  (#^ str reason))


(defclass [(dataclass :frozen True)] HttpShutdown [EffectBase]
  "待ち受けを閉じる(頭の註)。reason = 以後の HttpServerClosed の理由・drain-seconds = 送りの箱を流し切るのを待つ上限の秒。"
  (#^ str reason)
  (setv #^ float drain-seconds DEFAULT-DRAIN-SECONDS))


(defclass [(dataclass :frozen True)] TakeWsSendReport [EffectBase]
  "送りの箱の勘定を読んで 0 に戻す(頭の註)。答え = WsSendReport。")


;; 札の要求への命令の union(答え手と台本の記録が命令 1 つを受ける型)。
(val HttpCommand (| HttpRespond HttpForward WsForward WsAccept))


;; --- 台本の語彙(scripted-http-server) -----------------------------------------------------------------------------------

(defrecord ScriptedUpstream
  "中継先 1 つの台本の答え: base = この頭で始まる URL へ届いた中継がこの答えを受ける・http-status = HTTP の中継の答えの status・
   accepts-ws = ws の中継を受ける(受けなければ 502)。"
  (#^ str base)
  (#^ int http-status)
  (#^ bool accepts-ws))


(defrecord HttpScript
  "scripted-http-server の台本: 届く出来事の列(要求と、WsAccept で上げた札の ws の出来事)と、中継先ごとの答えと、読まない相手の札
   (stalled — その札の送りは箱に溜まり続け、送りの上限を超えれば切られる)。"
  (#^ (get tuple #((| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed) ...)) arrivals)
  (setv #^ (get tuple #(ScriptedUpstream ...)) upstreams #()
        #^ (get frozenset str) stalled (frozenset)))


(defrecord HttpServed
  "受けた命令 1 つと、端末が受け取る答え(status・本文の文字列 — file の範囲は中身・中継は台本の答え)。"
  (#^ str ticket)
  (#^ HttpCommand command)
  (#^ int status)
  (#^ str body))


(defrecord WsTextSent
  "ws に上げた札の接続へ送った文字の 1 通(台本の記録)。"
  (#^ str ticket)
  (#^ str text))


(defrecord WsCloseSent
  "ws に上げた札の接続へ送った閉じ(台本の記録 — WsClose・HttpShutdown の close 1000)。"
  (#^ str ticket)
  (#^ int code)
  (#^ str reason))


(defclass [(dataclass :frozen True)] ReadHttpServed [EffectBase]
  "台本の待ち受けが受けた命令と送った ws の記録(HttpServed・WsTextSent・WsCloseSent の受けた順の tuple)を読む(頭の註)。")


(defclass [(dataclass :frozen True)] AppendHttpScript [EffectBase]
  "走っている台本の出来事の列の後ろへ arrivals を足す(頭の註)。答え = None。"
  (#^ (get tuple #((| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed) ...)) arrivals))
