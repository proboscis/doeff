;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy)の本物の答え手 aiohttp-http-server(agora-redesign #802 便 2 — agora-controllers の
;;; webapp の受け口の土台 #767 / #795 から移した)。aiohttp の待ち受け・応答の送出・HTTP と ws の中継の実 I/O で、判断を持たない
;;; (何をどう送るかは呼び手の Program が HttpRespond の値で決める)。aiohttp は extra `http-server` の依存。
;;;
;;;   待ち受け      aiohttp の server を await-handler の共有の event loop の上に立てる(HttpListen)。要求ごとに札を振り、出来事
;;;                 HttpRequestArrived(頭を含む)を列へ並べ、命令(札つき)を待ってから実 I/O を撃つ — 呼び手は撃つだけで待たないので、
;;;                 長い中継が他の要求を止めない
;;;   本文の読み    HttpReadBody で札の要求の本文を request.content から塊で流しながら読む(aiohttp の request.read の既定の上限 1 MiB は
;;;                 通らない — 上限は effect の max-bytes だけ)。宣言の Content-Length が上限を超えれば読まずに断り、宣言が無い(chunked)・
;;;                 偽る要求は読んだ量が上限を 1 byte でも超えた拍に止めて断る。断った札は、答えを送った後に接続を閉じる(残りの本文を
;;;                 aiohttp の lingering で読み捨てさせない)
;;;   応答の送出    HttpRespond の status と頭をそのまま・本文は byte 列か file の範囲(start から length byte を塊で読んで書く)
;;;   HTTP の中継   本文を両向きとも streaming で通す。hop-by-hop の頭を落とし、X-Forwarded-Proto / X-Forwarded-For を足す。Host は要求の
;;;                 値のまま。中継先に届かなければ 502
;;;   ws の中継     先に中継先へ ws で繋いでから(届かなければ 502)、要求を ws に上げて frame を両向きに写す。1 frame の上限は HttpListen の
;;;                 ws-max-bytes。中継の側は ping を撃たず(autoping を切る)、端末の間の ping / pong と close の状態符をそのまま写す
;;;   ws の終端     WsAccept で要求を ws に上げる(handshake の形が合わなければ断りの応答 — GET でなければ 405・Upgrade: websocket が無ければ
;;;                 426・他は 400 — を返して 1 行名乗る)。上げたら接続ごとに読みの loop(aiohttp が ping への pong・分割の組み立て・UTF-8 の検め・
;;;                 1 通の上限 ws-max-bytes を持つ)と、送りの箱 + 書き手の task(WsSendText / WsClose は箱へ積むだけで待たない — 遅い 1 接続が
;;;                 他の接続と本体を塞がない)を立てる。箱の溜まりが ws-send-max-bytes を超える 1 通が来たら、その接続をその場で切る(箱を捨てる・
;;;                 transport を落とす)。閉じた接続への送りは黙って捨てる。出来事(WsOpened・WsTextArrived・WsBinaryArrived・WsClosed)は要求と
;;;                 同じ列へ並べ、受けた拍の単調時計を received-at に載せる
;;;   閉じ          HttpShutdown で新しい要求を受けず、開いている ws の全部へ close 1000 を積み、書き手が流し切るのを drain-seconds まで待ち、
;;;                 残った接続は落として待ち受けを畳む。以後の HttpNextRequest は HttpServerClosed
;;;   送りの勘定    積んだ・流した・捨てた byte と流すまでの所要を数え、TakeWsSendReport で渡して 0 に戻す(消費者の計器の材料)
;;; 台本の答え手と同じに決める物は http_server_effects.hy の判断の defk を呼ぶ(本文を運ぶ答えか carries-content・ws の断りの status
;;; ws-refusal-status・送りの上限で切るか send-overflows・WsClosed で名乗る状態符と理由 closing-of — こちらの WsClose ならその状態符と理由、
;;; 相手の close なら相手の状態符と理由)。契約テストは tests/test_http_server_contract.hy。
;;; 並び: 組の外側に await-handler が要る。
(require doeff-hy.macros [defhandler <- val])
(import asyncio)
(import collections [deque])
(import sys)
(import time)
(import pathlib [Path])
(import aiohttp)
(import aiohttp [web WSMsgType])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.http_server_effects [HttpAddress HttpServerClosed HttpCommand HttpEvent HttpHeader HttpRequestArrived HttpListen
                                                HttpNextRequest HttpRespond HttpForward WsForward WsAccept WsSendText WsClose HttpShutdown
                                                TakeWsSendReport WsSendReport WsOpened WsTextArrived WsBinaryArrived WsClosed
                                                HttpBodyBytes HttpBodyFileRange HttpNoBody FLUSH-SAMPLES-LIMIT DEFAULT-DRAIN-SECONDS WS-CLOSE-NORMAL
                                                WS-CLOSE-ABNORMAL HttpReadBody HttpBodyRead HttpBodyTooLarge HttpBodyFailed HttpBodyOutcome
                                                WS-REFUSAL-TEXT WS-CUT-REASON WsCloseFrame carries-content ws-refusal-status
                                                send-overflows closing-of])
(import aiohttp.web_protocol [PayloadAccessError])
(import doeff [run])

;; file の範囲を送る塊の byte 数。
(val FILE-CHUNK-BYTES 262144)
;; 要求の本文を読む塊の byte 数(上限の手前では残りの分だけ読む)。
(val BODY-CHUNK-BYTES 262144)

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


(defn #^ int refusal-status [#^ web.Request request]  ; defk にできない: aiohttp の要求の頭を読む受け口の実 I/O
  "ws に上げられない要求へ返す断りの status を決めるため(形の断りは台本の答え手と同じ ws-refusal-status — GET でない 405・
   Upgrade: websocket が無い 426。形が合っても handshake の残りの検めで断れば 400)。"
  (or (run (ws-refusal-status request.method (upgrade-asked request))) 400))


(defclass WsPeer []
  "ws に上げた接続 1 本と、その送りの箱(await-handler の共有の event loop の上だけで触る)。outbox = 積んだ物の列(#(\"text\" 文 積んだ拍)
   か #(\"close\" #(状態符 理由) 積んだ拍))・pending = 箱の文の byte の合計・closed = 以後は積まない(閉じを積んだ・切った・終わった)・
   cut = 送りの上限で切った理由(None = 切っていない)・sent = こちらが積んだ閉じ(WsCloseFrame)・received = 相手から受けた閉じ・
   wake = 書き手を起こす印・writer = 書き手の task。"

  (defn #^ None __init__ [self #^ str ticket #^ web.Request request #^ web.WebSocketResponse ws]
    (setv self.ticket ticket
          self.request request
          self.ws ws
          self.outbox (deque)
          self.pending 0
          self.closed False
          self.cut None
          #^ (| WsCloseFrame None) self.sent None
          #^ (| WsCloseFrame None) self.received None
          self.wake (asyncio.Event)
          self.writer None)
    None))


(defclass WebEdge []
  "aiohttp の待ち受けと、札ごとの命令の待ちと、ws に上げた接続(await-handler の共有の event loop の上だけで触る)。待ち受けの handler の
   session の値。"

  (defn #^ None __init__ [self]
    (setv self.address None
          self.ws-max-bytes None
          self.ws-send-max-bytes None
          self.queue None
          self.client None
          self.runner None
          self.waiting {}
          self.unread {}
          self.oversized (set)
          self.peers {}
          self.shut None
          self.ws-send-drain None
          self.count 0)
    (self.reset-report)
    None)

  (defn #^ None reset-report [self]
    "送りの勘定を 0 に戻すため(TakeWsSendReport で渡した後)。"
    (setv self.queued-frames 0
          self.queued-bytes 0
          self.flushed-bytes 0
          self.flush-seconds (deque :maxlen FLUSH-SAMPLES-LIMIT)
          self.dropped-bytes 0
          self.cuts 0)
    None)

  (defn :async #^ HttpAddress start [self #^ HttpAddress address #^ int ws-max-bytes #^ int ws-send-max-bytes]
    "待ち受けを開き、結んだ宛先を答えるため(開いた後に届いた要求はすべて列へ並ぶ)。"
    (setv self.address address
          self.ws-max-bytes ws-max-bytes
          self.ws-send-max-bytes ws-send-max-bytes)
    (setv self.queue (asyncio.Queue)
          self.client (aiohttp.ClientSession :auto-decompress False
                                             :timeout (aiohttp.ClientTimeout :total None :sock-connect CONNECT-SECONDS
                                                                             :sock-read HTTP-READ-SECONDS)))
    (setv app (web.Application))
    (.add-route app.router "*" "/{tail:.*}" self.receive)
    (setv self.runner (web.AppRunner app :access-log None))
    (await (.setup self.runner))
    (await (.start (web.TCPSite self.runner self.address.host self.address.port)))
    (setv bound (get self.runner.addresses 0))
    (HttpAddress :host self.address.host :port (get bound 1)))

  (defn :async #^ HttpEvent next-arrival [self]
    "受け口の列の次の出来事を本体へ渡すため(閉じた後は列に何が残っていても HttpServerClosed)。"
    (when (is-not self.shut None)
      (return (HttpServerClosed :reason self.shut)))
    (setv event (await (.get self.queue)))
    (if (and (is-not self.shut None) (not (isinstance event HttpServerClosed)))
        (HttpServerClosed :reason self.shut)
        event))

  (defn :async #^ None settle [self #^ str ticket #^ HttpCommand command]
    "本体の命令を札の要求へ渡すため(同じ札へ 2 度渡すと KeyError — 判断は要求ごとに 1 つ)。"
    (.set-result (.pop self.waiting ticket) command)
    None)

  (defn :async #^ web.StreamResponse receive [self #^ web.Request request]
    "aiohttp の要求 1 つ: 札を振って出来事を並べ、本体の命令を待って実 I/O を撃つため。"
    (setv self.count (+ self.count 1))
    (setv ticket (str self.count)
          waiting (.create-future (asyncio.get-running-loop)))
    (setv (get self.waiting ticket) waiting
          (get self.unread ticket) request)
    (await (.put self.queue (HttpRequestArrived :ticket ticket :method request.method :path request.path :target request.raw-path
                                         :upgrade (upgrade-asked request)
                                         :headers (tuple (gfor [name value] (.items request.headers) (HttpHeader :name name :value value)))
                                         :received-at (time.monotonic))))
    (setv command (await waiting))
    ;; 命令を受けた札の本文はもう読ませない。本文を上限で断った札は、答えを送った後に接続を閉じる。
    (.pop self.unread ticket None)
    (setv cut-off (in ticket self.oversized))
    (.discard self.oversized ticket)
    (match command
      (HttpRespond :status status :headers headers :body body) (await (self.respond request status headers body cut-off))
      (HttpForward :url url) (await (self.relay-http request url))
      (WsForward :url url) (await (self.relay-ws request url))
      (WsAccept :ticket accepted) (await (self.terminate-ws request accepted))))

  (defn :async #^ web.StreamResponse terminate-ws [self #^ web.Request request #^ str ticket]
    "札の要求を ws に上げて終端するため: 読みの loop で 1 通を出来事へ並べ、送りは書き手の task に任せ、終わったら WsClosed を並べる。"
    (setv ws (web.WebSocketResponse :max-msg-size self.ws-max-bytes))
    (when (not (. (.can-prepare ws request) ok))
      (setv status (refusal-status request))
      (relay-failed (.format "札 {} の ws の handshake が成らなかった: 形が違う(status {})" ticket status))
      (return (web.Response :status status :text WS-REFUSAL-TEXT)))
    (try
      (await (.prepare ws request))
      (except [error #(ConnectionResetError RuntimeError web.HTTPException)]
        (relay-failed (.format "札 {} の ws の handshake が成らなかった: {!r}" ticket error))
        (return ws)))
    (setv peer (WsPeer ticket request ws))
    (setv (get self.peers ticket) peer)
    (setv peer.writer (asyncio.create-task (self.write-loop peer)))
    (await (.put self.queue (WsOpened :ticket ticket :received-at (time.monotonic))))
    (try
      ;; async for では相手の close の理由(message.extra)が読めないので、receive を直に回して相手の閉じを控える。
      (while True
        (setv message (await (.receive ws)))
        (match message.type
          WSMsgType.TEXT (await (.put self.queue (WsTextArrived :ticket ticket :text message.data :received-at (time.monotonic))))
          WSMsgType.BINARY (await (.put self.queue (WsBinaryArrived :ticket ticket :data message.data :received-at (time.monotonic))))
          WSMsgType.CLOSE (do (setv peer.received (WsCloseFrame :code (int message.data) :reason (or message.extra "")))
                              (break))
          _ (break)))
      (except [#(ConnectionResetError RuntimeError)] None)
      (finally
        (await (self.finish-peer peer))))
    ws)

  (defn :async #^ None finish-peer [self #^ WsPeer peer]
    "読みの loop が終わった接続を畳むため: こちらが閉じを積んでいれば書き手が流し切るのを待ち、他は箱を捨てて書き手を止め、WsClosed を並べる。"
    (setv closing (and peer.closed (is peer.cut None)))
    (when (and closing (is-not peer.writer None))
      (await (asyncio.wait #{peer.writer} :timeout (or self.ws-send-drain DEFAULT-DRAIN-SECONDS))))
    (self.seal peer 0)
    (when (is-not peer.writer None)
      (.cancel peer.writer))
    (.pop self.peers peer.ticket None)
    ;; 名乗る状態符と理由は台本の答え手と同じ closing-of で決める(切り・こちらの閉じ・相手の閉じ・切れた時の状態符の順)。
    (setv lost peer.ws.close-code)
    (setv frame (run (closing-of peer.cut peer.sent peer.received (if (is lost None) None (int lost)))))
    (await (.put self.queue (WsClosed :ticket peer.ticket :code frame.code :reason frame.reason :received-at (time.monotonic))))
    None)

  (defn :async #^ None write-loop [self #^ WsPeer peer]
    "接続ごとの書き手: 箱の 1 通を順に相手へ書き(流した勘定と所要を数える)、閉じを積まれたら close を送って終わる。書けなければ箱を捨てる。"
    (while True
      (while (not peer.outbox)
        (when peer.closed
          (return None))
        (.clear peer.wake)
        (await (.wait peer.wake)))
      (setv [kind payload queued-at] (.popleft peer.outbox))
      (match kind
        "text"
          (do (setv size (len (.encode payload "utf-8")))
              (setv peer.pending (- peer.pending size))
              (try
                (await (.send-str peer.ws payload))
                (except [#(ConnectionResetError RuntimeError aiohttp.ClientError)]
                  (self.seal peer size)
                  (return None))
                (except [asyncio.CancelledError]
                  ;; 書きかけのまま接続が終わった(読みの loop が書き手を取り消した): 取り出した 1 通も捨てた勘定へ載せてから取り消しを通す。
                  (self.seal peer size)
                  (raise)))
              (setv self.flushed-bytes (+ self.flushed-bytes size))
              (.append self.flush-seconds (- (time.monotonic) queued-at)))
        "close"
          (do (try
                (await (.close peer.ws :code (get payload 0) :message (.encode (get payload 1) "utf-8")))
                (except [#(ConnectionResetError RuntimeError aiohttp.ClientError)] None))
              (return None)))))

  (defn #^ None seal [self #^ WsPeer peer #^ int unsent]
    "接続の箱を閉じて中身を捨てるため(unsent = 書き手が取り出したが書けなかった byte — 箱の中身と一緒に捨てた勘定へ)。"
    (setv peer.closed True)
    (setv dropped (+ peer.pending unsent))
    (setv peer.outbox (deque) peer.pending 0)
    (setv self.dropped-bytes (+ self.dropped-bytes dropped))
    (.set peer.wake)
    None)

  (defn :async #^ None send-text [self #^ str ticket #^ str text]
    "札の接続の箱へ文字の 1 通を積むため(待たない)。閉じた・知らない札は捨てる。溜まりが上限を超える 1 通なら接続をその場で切る。"
    (setv peer (.get self.peers ticket))
    (when (or (is peer None) peer.closed)
      (return None))
    (setv size (len (.encode text "utf-8"))
          limit self.ws-send-max-bytes)
    (when (is limit None)
      (raise (RuntimeError "HttpListen の前の WsSendText")))
    ;; 切るかは台本の答え手と同じ send-overflows で決める(上限を超える 1 通は積まずに切る)。
    (if (run (send-overflows peer.pending size limit))
        (do (setv self.cuts (+ self.cuts 1)
                  peer.cut WS-CUT-REASON)
            (self.seal peer 0)
            (when peer.request.transport
              (.abort peer.request.transport)))
        (do (.append peer.outbox #("text" text (time.monotonic)))
            (setv peer.pending (+ peer.pending size)
                  self.queued-frames (+ self.queued-frames 1)
                  self.queued-bytes (+ self.queued-bytes size))
            (.set peer.wake)))
    None)

  (defn :async #^ None close-ws [self #^ str ticket #^ int code #^ str reason]
    "札の接続へ閉じを積むため(積んだ 1 通を流し切ってから close を送る — 待たない)。"
    (setv peer (.get self.peers ticket))
    (when (or (is peer None) peer.closed)
      (return None))
    (.append peer.outbox #("close" #(code reason) (time.monotonic)))
    (setv peer.closed True
          peer.sent (WsCloseFrame :code code :reason reason))
    (.set peer.wake)
    None)

  (defn :async #^ None shutdown [self #^ str reason #^ float drain-seconds]
    "待ち受けを閉じるため: 開いている ws の全部へ close 1000 を積み、書き手が流し切るのを drain-seconds まで待ち、残りは落として畳む。"
    (setv self.shut reason
          self.ws-send-drain drain-seconds)
    (setv peers (list (.values self.peers)))
    (for [peer peers]
      (await (self.close-ws peer.ticket WS-CLOSE-NORMAL reason)))
    (setv writers (lfor peer peers :if (is-not peer.writer None) peer.writer))
    (when writers
      (await (asyncio.wait writers :timeout drain-seconds)))
    (for [peer (list (.values self.peers))]
      (when peer.request.transport
        (.abort peer.request.transport)))
    (when (is-not self.runner None)
      (await (.cleanup self.runner)))
    (when (is-not self.client None)
      (await (.close self.client)))
    (when (is-not self.queue None)
      (await (.put self.queue (HttpServerClosed :reason reason))))
    None)

  (defn :async #^ WsSendReport take-report [self]
    "送りの勘定を渡して 0 に戻すため。"
    (setv report (WsSendReport :queued-frames self.queued-frames :queued-bytes self.queued-bytes :flushed-bytes self.flushed-bytes
                               :flush-seconds (tuple self.flush-seconds) :dropped-bytes self.dropped-bytes :cuts self.cuts))
    (self.reset-report)
    report)

  (defn :async #^ HttpBodyOutcome read-body [self #^ str ticket #^ int max-bytes]  ; defk にできない: aiohttp の要求の本文を読む実 I/O(event loop の coroutine)
    "札の要求の本文を上限まで流しながら読むため(頭の註 — 宣言が上限を超えれば読まずに断る・読んだ量が上限を超えた拍に止めて断る)。
     札ごとに 1 度だけ: 読み終えた・命令を受けた・知らない札は HttpBodyFailed。"
    (setv request (.pop self.unread ticket None))
    (when (is request None)
      (return (HttpBodyFailed :reason (.format "札 {} の要求の本文は読めない(知らない札・読み終えた札・命令を撃った後の札)" ticket))))
    (setv declared request.content-length)
    (when (and (is-not declared None) (> declared max-bytes))
      (.add self.oversized ticket)
      (return (HttpBodyTooLarge :declared declared)))
    (setv chunks [] total 0)
    (try
      (while True
        (setv chunk (await (.read request.content (min BODY-CHUNK-BYTES (- (+ max-bytes 1) total)))))
        (when (not chunk)
          (break))
        (.append chunks chunk)
        (setv total (+ total (len chunk)))
        (when (> total max-bytes)
          (.add self.oversized ticket)
          (return (HttpBodyTooLarge :declared declared))))
      (except [error #(ConnectionError web.RequestPayloadError PayloadAccessError)]
        (return (HttpBodyFailed :reason (.format "札 {} の要求の本文を読めなかった: {!r}" ticket error)))))
    (HttpBodyRead :data (.join b"" chunks)))

  (defn :async #^ web.StreamResponse respond [self #^ web.Request request #^ int status #^ tuple headers
                                              #^ (| HttpBodyBytes HttpBodyFileRange HttpNoBody) body #^ bool cut-off]
    "翻訳の handler が決めた答えをそのまま送るため(file の範囲は塊で読んで書く)。cut-off = 本文を上限で断った札 — 送った後に接続を閉じる
     (残りの本文を読み捨てない)。"
    (setv response (web.StreamResponse :status status))
    (for [header headers]
      (if (= (.lower header.name) "content-length")
          (setv response.content-length (int header.value))
          (.add response.headers header.name header.value)))
    ;; 本文を運ばない答え(HEAD・1xx・204・304)は、本文を渡されても送らない — 台本の答え手と同じ carries-content で決める。
    (setv sent-body (if (run (carries-content request.method status)) body (HttpNoBody)))
    (match sent-body
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
    (when cut-off
      ;; 書いた答えは transport が流し切ってから閉じる。protocol の側で閉じるので、aiohttp は残りの本文の lingering をしない。
      (.force-close request.protocol))
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
  (HttpListen [address ws-max-bytes ws-send-max-bytes]
    (<- bound HttpAddress (Await (.start edge address ws-max-bytes ws-send-max-bytes)))
    (resume bound))
  (HttpNextRequest []
    (<- arrival (Await (.next-arrival edge)))
    (resume arrival))
  (HttpReadBody [ticket max-bytes]
    (<- outcome HttpBodyOutcome (Await (.read-body edge ticket max-bytes)))
    (resume outcome))
  (HttpRespond [ticket status headers body]
    (<- (Await (.settle edge ticket effect)))
    (resume None))
  (HttpForward [ticket url]
    (<- (Await (.settle edge ticket effect)))
    (resume None))
  (WsForward [ticket url]
    (<- (Await (.settle edge ticket effect)))
    (resume None))
  (WsAccept [ticket]
    (<- (Await (.settle edge ticket effect)))
    (resume None))
  (WsSendText [ticket text]
    (<- (Await (.send-text edge ticket text)))
    (resume None))
  (WsClose [ticket code reason]
    (<- (Await (.close-ws edge ticket code reason)))
    (resume None))
  (HttpShutdown [reason drain-seconds]
    (<- (Await (.shutdown edge reason drain-seconds)))
    (resume None))
  (TakeWsSendReport []
    (<- report WsSendReport (Await (.take-report edge)))
    (resume report)))
