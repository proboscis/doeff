;;; 汎用の HTTP の待ち受け(受ける側)の effect(agora-redesign #802 便 2・消費者 = #795 webapp の受け口)。送る側の HttpRequest
;;; (http_effects.hy)と対になる土台の語彙で、業務の語を持たない。答え手は仕組みごとに差し替える:
;;;   aiohttp-http-server   本物の待ち受け(aiohttp_http_server.hy — aiohttp は extra `http-server` の依存)
;;;   scripted-http-server  I/O なし — 台本の要求の列を出来事にし、受けた命令を記録する(scripted_http_server.hy)
;;;
;;;   HttpListen       待ち受けを開く(address・ws の中継の 1 frame の上限)。答え = None
;;;   HttpNextRequest  次の出来事。答え = HttpRequestArrived(札・method・path・target・頭・ws への Upgrade を求めたか)か HttpServerClosed
;;;   HttpRespond      札の要求へ status・頭・本文(HttpBodyBytes / HttpBodyFileRange / HttpNoBody)を送る。答え = None
;;;   HttpForward      札の要求を url へ HTTP で中継する(本文は両向き streaming・hop-by-hop の頭を落とし X-Forwarded-Proto / -For を足す・
;;;                    届かなければ 502)。答え = None
;;;   WsForward        札の要求を url へ ws で中継する(frame を両向きに写す・ping / pong と close の状態符を素通し・届かなければ 502)。答え = None
;;; 札ごとに命令はちょうど 1 つ。命令は撃つだけで待たない(長い中継が他の要求を止めない)。要求の本文を読む effect は今の消費者に
;;; 要らないので持たない(要る時に契約を足す)。file の状態は file_effects.hy の StatPath で読む(ここに持たない)。
;;;
;;; 台本の語彙(本物の待ち受けには無い): HttpScript・ScriptedUpstream = 台本・HttpServed = 受けた命令と端末が受け取る答え・
;;; ReadHttpServed = 記録を読む effect(検と筋書きが覗くため — scripted-http-server だけが答える)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase])

;; ws の中継の 1 frame の上限の既定(byte)。
(val DEFAULT-WS-MAX-BYTES (* 1024 1024))


(defrecord HttpAddress
  "待ち受けの宛先。"
  (#^ str host)
  (#^ int port))


(defrecord HttpHeader
  "HTTP の頭 1 つ(同じ名が複数あっても落とさないよう列で持つ)。"
  (#^ str name)
  (#^ str value))


(defrecord HttpRequestArrived
  "届いた要求 1 つ: 札・method・path(query なし)・target(query つき)・頭の列・ws への Upgrade を求めたか。"
  (#^ str ticket)
  (#^ str method)
  (#^ str path)
  (#^ str target)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ bool upgrade))


(defrecord HttpServerClosed
  "待ち受けが閉じた(台本が尽きた等)。"
  (#^ str reason))


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
  "待ち受けを開く(頭の註)。"
  (#^ HttpAddress address)
  (setv #^ int ws-max-bytes DEFAULT-WS-MAX-BYTES))


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


;; 待ち受けの命令の union(答え手と台本の記録が命令 1 つを受ける型)。
(val HttpCommand (| HttpRespond HttpForward WsForward))


;; --- 台本の語彙(scripted-http-server) -----------------------------------------------------------------------------------

(defrecord ScriptedUpstream
  "中継先 1 つの台本の答え: base = この頭で始まる URL へ届いた中継がこの答えを受ける・http-status = HTTP の中継の答えの status・
   accepts-ws = ws の中継を受ける(受けなければ 502)。"
  (#^ str base)
  (#^ int http-status)
  (#^ bool accepts-ws))


(defrecord HttpScript
  "scripted-http-server の台本: 届く要求の列と、中継先ごとの答え。"
  (#^ (get tuple #(HttpRequestArrived ...)) arrivals)
  (setv #^ (get tuple #(ScriptedUpstream ...)) upstreams #()))


(defrecord HttpServed
  "受けた命令 1 つと、端末が受け取る答え(status・本文の文字列 — file の範囲は中身・中継は台本の答え)。"
  (#^ str ticket)
  (#^ HttpCommand command)
  (#^ int status)
  (#^ str body))


(defclass [(dataclass :frozen True)] ReadHttpServed [EffectBase]
  "台本の待ち受けが受けた命令の記録(HttpServed の tuple)を読む(頭の註)。")
