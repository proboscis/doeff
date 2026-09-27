;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy)の本物の答え手 aiohttp-http-server(agora-redesign #802 便 2 — agora-controllers の
;;; webapp の受け口の土台 #767 / #795 から移した)。aiohttp の待ち受け・応答の送出・HTTP と ws の中継の実 I/O で、判断を持たない
;;; (何をどう送るかは呼び手の Program が HttpRespond の値で決める)。aiohttp は extra `http-server` の依存。
;;;
;;;   待ち受け      aiohttp の server を await-handler の共有の event loop の上に立てる(HttpListen)。要求ごとに札を振り、出来事
;;;                 HttpRequestArrived(頭を含む)を列へ並べ、命令(札つき)を待ってから実 I/O を撃つ — 呼び手は撃つだけで待たないので、
;;;                 長い中継が他の要求を止めない
;;;   応答の送出    HttpRespond の status と頭をそのまま・本文は byte 列か file の範囲(start から length byte を塊で読んで書く)
;;;   HTTP の中継   本文を両向きとも streaming で通す。hop-by-hop の頭を落とし、X-Forwarded-Proto / X-Forwarded-For を足す。Host は要求の
;;;                 値のまま。中継先に届かなければ 502
;;;   ws の中継     先に中継先へ ws で繋いでから(届かなければ 502)、要求を ws に上げて frame を両向きに写す。1 frame の上限は HttpListen の
;;;                 ws-max-bytes。中継の側は ping を撃たず(autoping を切る)、端末の間の ping / pong と close の状態符をそのまま写す
;;; 並び: 組の外側に await-handler が要る。
(require doeff-hy.macros [defhandler <- val])
(import asyncio)
(import sys)
(import pathlib [Path])
(import aiohttp)
(import aiohttp [web WSMsgType])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.http_server_effects [HttpAddress HttpServerClosed HttpCommand HttpHeader HttpRequestArrived HttpListen
                                                HttpNextRequest HttpRespond HttpForward WsForward HttpBodyBytes HttpBodyFileRange HttpNoBody])

;; file の範囲を送る塊の byte 数。
(val FILE-CHUNK-BYTES 262144)

;; 中継先への接続の上限と、HTTP の中継の読みの間の上限(秒 — 旧い nginx の proxy_read_timeout 300s と同じ)。
(val CONNECT-SECONDS 10.0)
(val HTTP-READ-SECONDS 300.0)
;; 中継で写さない頭(hop-by-hop — RFC 9110 7.6.1)。ws の中継は handshake の頭も写さない(aiohttp が中継先への handshake を組む)。
(val HOP-BY-HOP (frozenset #("connection" "keep-alive" "proxy-authenticate" "proxy-authorization" "te" "trailer" "trailers"
                            "transfer-encoding" "upgrade")))
(val WS-HANDSHAKE-PREFIX "sec-websocket-")
;; 送れない close の状態符: 状態符なし(1005)は正常終了(1000)で、異常終了(1006・1015 — close frame の無い切断)と、close frame を
;; 受けずに相手が消えた時は 1011(中継の側の異常)で閉じる — 端末が「意図した切断」と読み違えて再接続を止めないように。
(val NO-STATUS-CLOSE 1005)
(val ABNORMAL-CLOSE-CODES (frozenset #(1006 1015)))
(val NORMAL-CLOSE 1000)
(val RELAY-FAILED-CLOSE 1011)


(defn #^ bool upgrade-asked [#^ web.Request request]  ; defk にできない: aiohttp の要求の頭を読む受け口の実 I/O
  "要求が ws への Upgrade を求めているかを出来事に載せるため。"
  (= (.lower (.get request.headers "Upgrade" "")) "websocket"))


(defn #^ frozenset hop-names [#^ str connection]  ; defk にできない: 中継の実 I/O(aiohttp の頭の組)
  "写さない頭の名(固定の hop-by-hop と、Connection の頭が名指す頭 — RFC 9110 7.6.1)を決めるため。"
  (| HOP-BY-HOP (frozenset (gfor token (.split connection ",") :if (.strip token) (.lower (.strip token))))))


(defn #^ list forwarded-headers [#^ web.Request request #^ bool ws]  ; defk にできない: 中継の実 I/O(aiohttp の頭の組)
  "中継先へ渡す頭(hop-by-hop と、ws なら handshake の頭を除く)に X-Forwarded-Proto / X-Forwarded-For を足すため。値は (名 値) の組の列
   (同じ名の頭が複数あっても落とさない)。"
  (setv hops (hop-names (.get request.headers "Connection" ""))
        kept (lfor [name value] (.items request.headers)
                   :if (not (or (in (.lower name) hops) (and ws (.startswith (.lower name) WS-HANDSHAKE-PREFIX))
                                (in (.lower name) #("x-forwarded-proto" "x-forwarded-for"))))
                   #(name value))
        proto (.get request.headers "X-Forwarded-Proto" request.scheme)
        chain (.get request.headers "X-Forwarded-For" "")
        peer (or request.remote ""))
  (+ kept [#("X-Forwarded-Proto" proto) #("X-Forwarded-For" (if chain (+ chain ", " peer) peer))]))


(defn #^ int sendable-close [#^ (| int None) code]  ; defk にできない: ws の中継の実 I/O(close frame の組)
  "受けた close の状態符を、送り直せる状態符にするため(状態符なしは正常終了・異常終了は 1011 — 頭の註)。"
  (cond
    (or (is code None) (= code NO-STATUS-CLOSE)) NORMAL-CLOSE
    (in code ABNORMAL-CLOSE-CODES) RELAY-FAILED-CLOSE
    True code))


(defn :async #^ None pump [#^ (| web.WebSocketResponse aiohttp.ClientWebSocketResponse) source
                           #^ (| web.WebSocketResponse aiohttp.ClientWebSocketResponse) target]  ; defk にできない: ws の中継の実 I/O(aiohttp の socket を写す)
  "片向きの frame を写すため: 文字・byte・ping・pong をそのまま・close は状態符ごと相手へ写して終わる。相手が先に閉じていれば終わる。"
  (while True
    (setv message (await (.receive source)))
    (try
      (match message.type
        WSMsgType.TEXT (await (.send-str target message.data))
        WSMsgType.BINARY (await (.send-bytes target message.data))
        WSMsgType.PING (await (.ping target message.data))
        WSMsgType.PONG (await (.pong target message.data))
        WSMsgType.CLOSE (do (await (.close target :code (sendable-close message.data)
                                           :message (.encode (or message.extra "") "utf-8")))
                            (return None))
        _ (do (await (.close target :code RELAY-FAILED-CLOSE)) (return None)))
      (except [#(ConnectionResetError RuntimeError aiohttp.ClientError)]
        (await (.close source :code RELAY-FAILED-CLOSE))
        (return None)))))


(defn #^ None relay-failed [#^ str line]  ; defk にできない: 中継の実 I/O(access log の代わりの 1 行)
  "中継の失敗を stderr に 1 行名乗るため(access log を出さないので、どの口が落ちたかを外から見分ける)。"
  (print (+ "http-server: " line) :file sys.stderr :flush True)
  None)


(defclass WebEdge []
  "aiohttp の待ち受けと、札ごとの命令の待ち(await-handler の共有の event loop の上だけで触る)。待ち受けの handler の session の値。"

  (defn #^ None __init__ [self]
    (setv self.address None
          self.ws-max-bytes None
          self.queue None
          self.client None
          self.runner None
          self.waiting {}
          self.count 0)
    None)

  (defn :async #^ None start [self #^ HttpAddress address #^ int ws-max-bytes]
    "待ち受けを開くため(開いた後に届いた要求はすべて列へ並ぶ)。"
    (setv self.address address
          self.ws-max-bytes ws-max-bytes)
    (setv self.queue (asyncio.Queue)
          self.client (aiohttp.ClientSession :auto-decompress False
                                             :timeout (aiohttp.ClientTimeout :total None :sock-connect CONNECT-SECONDS
                                                                             :sock-read HTTP-READ-SECONDS)))
    (setv app (web.Application))
    (.add-route app.router "*" "/{tail:.*}" self.receive)
    (setv self.runner (web.AppRunner app :access-log None))
    (await (.setup self.runner))
    (await (.start (web.TCPSite self.runner self.address.host self.address.port)))
    None)

  (defn :async #^ (| HttpRequestArrived HttpServerClosed) next-arrival [self]
    "受け口の列の次の出来事を本体へ渡すため。"
    (await (.get self.queue)))

  (defn :async #^ None settle [self #^ str ticket #^ HttpCommand command]
    "本体の命令を札の要求へ渡すため(同じ札へ 2 度渡すと KeyError — 判断は要求ごとに 1 つ)。"
    (.set-result (.pop self.waiting ticket) command)
    None)

  (defn :async #^ web.StreamResponse receive [self #^ web.Request request]
    "aiohttp の要求 1 つ: 札を振って出来事を並べ、本体の命令を待って実 I/O を撃つため。"
    (setv self.count (+ self.count 1))
    (setv ticket (str self.count)
          waiting (.create-future (asyncio.get-running-loop)))
    (setv (get self.waiting ticket) waiting)
    (await (.put self.queue (HttpRequestArrived :ticket ticket :method request.method :path request.path :target request.raw-path
                                         :upgrade (upgrade-asked request)
                                         :headers (tuple (gfor [name value] (.items request.headers) (HttpHeader :name name :value value))))))
    (setv command (await waiting))
    (match command
      (HttpRespond :status status :headers headers :body body) (await (self.respond request status headers body))
      (HttpForward :url url) (await (self.relay-http request url))
      (WsForward :url url) (await (self.relay-ws request url))))

  (defn :async #^ web.StreamResponse respond [self #^ web.Request request #^ int status #^ tuple headers
                                              #^ (| HttpBodyBytes HttpBodyFileRange HttpNoBody) body]
    "翻訳の handler が決めた答えをそのまま送るため(file の範囲は塊で読んで書く)。"
    (setv response (web.StreamResponse :status status))
    (for [header headers]
      (if (= (.lower header.name) "content-length")
          (setv response.content-length (int header.value))
          (.add response.headers header.name header.value)))
    (match body
      (HttpBodyBytes :data data)
        (do (setv response.content-length (len data))
            (await (.prepare response request))
            (await (.write response data)))
      (HttpBodyFileRange :path path :start start :length length)
        (do (await (.prepare response request))
            (with [handle (open path "rb")]
              (.seek handle start)
              (setv left length)
              (while (> left 0)
                (setv chunk (.read handle (min left FILE-CHUNK-BYTES)))
                (when (not chunk) (break))
                (setv left (- left (len chunk)))
                (await (.write response chunk)))))
      (HttpNoBody) (await (.prepare response request)))
    (await (.write-eof response))
    response)

  (defn :async #^ web.StreamResponse relay-http [self #^ web.Request request #^ str url]
    "HTTP の要求を中継先へ streaming で写すため(届かなければ 502)。"
    (setv response None)
    (try
      (with [:async upstream (.request self.client request.method url :headers (forwarded-headers request False)
                                       :data (if request.body-exists request.content None) :allow-redirects False)]
        (setv response (web.StreamResponse :status upstream.status :reason upstream.reason))
        (setv hops (hop-names (.get upstream.headers "Connection" "")))
        (for [[name value] (.items upstream.headers)]
          (when (not-in (.lower name) hops)
            (.add response.headers name value)))
        (await (.prepare response request))
        (for [:async chunk (.iter-any upstream.content)]
          (await (.write response chunk)))
        (await (.write-eof response))
        response)
      (except [error #(aiohttp.ClientError asyncio.TimeoutError)]
        (relay-failed (+ "中継先 " url " への HTTP の中継が切れた: " (repr error)))
        (when (is response None)
          (return (web.Response :status 502 :text (+ "中継先に届かない: " url ": " (repr error)))))
        ;; 本文の途中で切れた: 終わりの印(chunked の終端)を送らずに接続を落とす — 途中の本文を完全な答えとして受けさせない。
        (.force-close response)
        (when request.transport
          (.close request.transport))
        response)))

  (defn :async #^ web.StreamResponse relay-ws [self #^ web.Request request #^ str url]
    "ws の要求を中継先へ繋いで frame を両向きに写すため(先に中継先へ繋ぎ、届かなければ Upgrade せずに 502)。"
    (setv protocols (lfor p (.split (.get request.headers "Sec-WebSocket-Protocol" "") ",") :if (.strip p) (.strip p)))
    (try
      (setv upstream (await (.ws-connect self.client url :headers (forwarded-headers request True) :autoping False
                                         :max-msg-size self.ws-max-bytes :protocols protocols)))
      (except [error #(aiohttp.ClientError asyncio.TimeoutError)]
        (relay-failed (+ "中継先 " url " へ ws で届かない: " (repr error)))
        (return (web.Response :status 502 :text (+ "中継先に ws で届かない: " url ": " (repr error))))))
    (setv client (web.WebSocketResponse :autoping False :max-msg-size self.ws-max-bytes
                                        :protocols (if upstream.protocol [upstream.protocol] [])))
    (try
      (await (.prepare client request))
      (await (asyncio.gather (pump client upstream) (pump upstream client)))
      (finally
        ;; 片向きの写しが例外で抜けても、両側を閉じて相方の写しを終わらせる。
        (await (.close upstream :code RELAY-FAILED-CLOSE))
        (await (.close client :code RELAY-FAILED-CLOSE))))
    client))


(defhandler aiohttp-http-server
  ;; 待ち受けの effect の実 I/O(頭の註)。待ち受けの object は session の値に 1 度だけ作る。effect の中の coroutine は await-handler の共有の
  ;; event loop で走る(組の外側に await-handler が要る)。
  (session val edge (WebEdge))
  (HttpListen [address ws-max-bytes]
    (<- (Await (.start edge address ws-max-bytes)))
    (resume None))
  (HttpNextRequest []
    (<- arrival (Await (.next-arrival edge)))
    (resume arrival))
  (HttpRespond [ticket status headers body]
    (<- (Await (.settle edge ticket effect)))
    (resume None))
  (HttpForward [ticket url]
    (<- (Await (.settle edge ticket effect)))
    (resume None))
  (WsForward [ticket url]
    (<- (Await (.settle edge ticket effect)))
    (resume None)))
