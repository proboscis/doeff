;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy)の本物の答え手 aiohttp-http-server(agora-redesign #802 便 2 — agora-controllers の
;;; webapp の受け口の土台 #767 / #795 から移した)。aiohttp の待ち受け・応答の送出・HTTP と ws の中継の実 I/O で、判断を持たない
;;; (何をどう送るかは呼び手の Program が HttpRespond の値で決める)。aiohttp は extra `http-server` の依存。
;;;
;;;   待ち受けの loop aiohttp の server とこの module の実 I/O の全部は、process に 1 つの待ち受けの loop(edge-loop — await-handler の
;;;                 共有の event loop とは別の daemon thread で回る)の上で走る。答え手の節は Await で共有の loop に入り、そこから待ち受けの
;;;                 loop の coroutine の完了を待つ(across)。共有の loop か協調型の scheduler が止まっても、待ち受けの loop は接続を受け続ける
;;;                 (agora-redesign #2776 — 2026-10-02 の record の job は共有の loop が 30 秒止まり、/healthz も答えなかった)
;;;   probe の口    HttpListen の probes に当たる要求(probe-for — GET・HEAD と path)は列にも scheduler にも渡さず、待ち受けの loop が
;;;                 answer の Program を別の thread(asyncio.to_thread)の自分の run で走らせて答える(probe-answer)。札は 1 つ数える
;;;   待ち受け      aiohttp の server を待ち受けの loop の上に立てる(HttpListen)。要求ごとに札を振り、出来事
;;;                 HttpRequestArrived(頭と、送り元の address = request.remote を含む)を列へ並べ、命令(札つき)を待ってから実 I/O を撃つ — 呼び手は撃つだけで待たないので、
;;;                 長い中継が他の要求を止めない。列(Arrivals)に既に在る出来事は、HttpNextRequest の節が答え手の節の thread で待たずに
;;;                 取る(共有の loop へ入らない — 書き 1 回で起きた待ちの読みが列に溜まった時に 1 つずつ往復しない・agora-redesign #3688
;;;                 の案 1)。列が空の時だけ共有の loop から待ち受けの loop で積まれるまで待つ
;;;   本文の読み    HttpReadBody で札の要求の本文を request.content から塊で流しながら読む(aiohttp の request.read の既定の上限 1 MiB は
;;;                 通らない — 上限は effect の max-bytes だけ)。宣言の Content-Length が上限を超えれば読まずに断り、宣言が無い(chunked)・
;;;                 偽る要求は読んだ量が上限を 1 byte でも超えた拍に止めて断る。断った札は、答えを送った後に接続を閉じる(残りの本文を
;;;                 aiohttp の lingering で読み捨てさせない)。宣言の長さが PREFETCH-BYTES 以下の本文は、待ち受けの loop が出来事を並べる
;;;                 前に読み切り、HttpReadBody は共有の loop へ入らずに答え手の節の側で上限を判じて答える(agora-redesign #3688 の子 (3) の
;;;                 3c — 超えれば同じ HttpBodyTooLarge で、答えの後に接続を閉じる)。先に読んだ本文は、読まずに HttpForward した札では
;;;                 中継先へそのまま送る
;;;   応答の送出    HttpRespond の status と頭をそのまま・本文は byte 列か file の範囲(start から length byte を塊で読んで書く)。
;;;                 答え手の節は命令を待ち受けの loop へ積むだけで戻る(共有の loop を経ず、往復を待たない — agora-redesign #3688 の子 (3)
;;;                 の 3b)。積んだ順に渡るので、後から撃った読み・閉じより先に渡る。札が待ち受けに無い(2 度目・知らない札)命令は、積んだ
;;;                 側がもう戻っているので、待ち受けの loop で 1 行名乗って捨てる。
;;;                 相手が先に切った後の書き込みの失敗(答えが遅れ、相手の上限が先に来た — ConnectionError)は aiohttp へ上げず、1 行で
;;;                 名乗って待ち受けごとに数える(上げると aiohttp が 1 件ごとに traceback を書く — 2026-10-02 の record の止まりで約 2.2KB ×
;;;                 265 本・agora-redesign #2757)。待ち受けを閉じる時に、届かなかった答えの合計を 1 行名乗る
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
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "http-server" :role "foundation"})
(import asyncio)
(import collections [deque])
(import collections.abc [Coroutine])
(import sys)
(import threading)
(import time)
(import typing [TypeVar])
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
                                                send-overflows closing-of HttpProbe probe-for probe-answer])
(import aiohttp.web_protocol [PayloadAccessError])
(import doeff [run])

;; file の範囲を送る塊の byte 数。
(val FILE-CHUNK-BYTES 262144)
;; 要求の本文を読む塊の byte 数(上限の手前では残りの分だけ読む)。
(val BODY-CHUNK-BYTES 262144)
;; 待ち受けの loop が出来事を並べる前に読み切る本文の、宣言の長さの上限(頭の註の本文の読み — 塊 1 つ分)。
(val PREFETCH-BYTES BODY-CHUNK-BYTES)

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

;; process に 1 つの待ち受けの loop の置き場(鍵 EDGE-KEY → loop)と、作る時の lock(頭の註 — 待ち受けの数だけ thread を増やさない)。
(val EDGE-LOOPS {})
(val EDGE-LOOP-LOCK (threading.Lock))
(val EDGE-KEY "edge")
;; across が待ち受けの loop から運ぶ答えの型。
(val Carried (TypeVar "Carried"))


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
  "ws に上げた接続 1 本と、その送りの箱(待ち受けの loop の上だけで触る)。outbox = 積んだ物の列(#(\"text\" 文 積んだ拍)
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


(defclass Arrivals []
  "受け口の列(要求と ws の出来事・閉じの印)。待ち受けの loop だけが積み(put)、受け手はどの thread からでも列に在る出来事を待たずに
   取れ(take-now — 共有の loop へ入らない・agora-redesign #3688 の案 1)、空なら待ち受けの loop の上で待つ(wait)。積んだ順に取れる。"

  (defn #^ None __init__ [self]
    ;; ready = 積まれてまだ取られていない出来事(deque の append と popleft は thread をまたいで安全)。waiters = 空の列を待つ受け手の
    ;; future(待ち受けの loop だけが触る)。
    (setv self.ready (deque)
          self.waiters (deque))
    None)

  (defn #^ None put [self #^ HttpEvent event]
    "出来事を列の末尾へ積み、空の列を待つ受け手を 1 つ起こすため(待ち受けの loop の上で)。"
    (.append self.ready event)
    (self.wake-one)
    None)

  (defn #^ None wake-one [self]
    "空の列を待つ受け手のうち、まだ待っている先頭の 1 つを起こすため(取り消された待ちは飛ばす)。起こされた受け手は列から自分で取る。"
    (while self.waiters
      (setv waiter (.popleft self.waiters))
      (when (not (.done waiter))
        (.set-result waiter None)
        (break)))
    None)

  (defn #^ (| HttpEvent None) take-now [self]
    "列の先頭の出来事を待たずに取るため(どの thread からでも — 空なら None)。"
    (try
      (.popleft self.ready)
      (except [IndexError]
        None)))

  (defn :async #^ HttpEvent wait [self]
    "列の先頭の出来事を取り、空なら積まれるまで待つため(待ち受けの loop の上で)。起こされた時に他の受け手が先に取っていれば待ち直す。
     待ちが取り消された時に列に出来事が残っていれば、次に待つ受け手を起こす(asyncio.Queue と同じく起こしを取りこぼさない)。"
    (while True
      (setv event (self.take-now))
      (when (is-not event None)
        (return event))
      (setv waiter (.create-future (asyncio.get-running-loop)))
      (.append self.waiters waiter)
      (try
        (await waiter)
        (except [asyncio.CancelledError]
          (when self.ready
            (self.wake-one))
          (raise))))))


(defclass WebEdge []
  "aiohttp の待ち受けと、札ごとの命令の待ちと、ws に上げた接続(待ち受けの loop — 頭の註 — の上だけで触る。答え手の節は across で
   渡す)。待ち受けの handler の session の値。"

  (defn #^ None __init__ [self]
    (setv self.address None
          self.ws-max-bytes None
          self.ws-send-max-bytes None
          ;; 受け口の列(待ち受けの loop が積み、答え手の節は在れば待たずに取る — Arrivals)。
          self.arrivals (Arrivals)
          self.client None
          self.runner None
          self.waiting {}
          self.unread {}
          ;; 札 → 待ち受けの loop が先に読んだ本文の答え(頭の註の本文の読み)。待ち受けの loop が出来事を並べる前に置き、答え手の節の
          ;; thread が読みで 1 度だけ取る(dict の pop 1 回)。命令を渡した札からは外す。
          self.prefetched {}
          self.oversized (set)
          self.peers {}
          self.shut None
          self.ws-send-drain None
          ;; 答え手が自分で答える probe の口(HttpListen の probes)。
          self.probes #()
          self.count 0
          ;; 相手が先に切って届かなかった答えの数(この待ち受けの起動から — 0 に戻さない)。
          self.dropped-answers 0)
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

  (defn #^ asyncio.AbstractEventLoop edge-loop [self]
    "待ち受けを共有の event loop と scheduler の止まりから切り離すため、process に 1 つの待ち受けの loop を引く(無い・閉じていれば作り、
     自分の daemon thread で回す — 頭の註)。"
    (with [EDGE-LOOP-LOCK]
      (setv loop (.get EDGE-LOOPS EDGE-KEY))
      (when (or (is loop None) (.is-closed loop))
        (setv loop (asyncio.new-event-loop))
        (.start (threading.Thread :target loop.run-forever :name "doeff-http-edge" :daemon True))
        (setv (get EDGE-LOOPS EDGE-KEY) loop))
      loop))

  (defn :async #^ Carried across [self #^ (get Coroutine #(object object Carried)) coroutine]
    "答え手の節(Await — 共有の event loop)から待ち受けの loop で coroutine を走らせ、その答えを待つため。待ちが取り消されれば待ち受けの
     loop の task も取り消す。"
    (await (asyncio.wrap-future (asyncio.run-coroutine-threadsafe coroutine (self.edge-loop)))))

  (defn :async #^ HttpAddress start [self #^ HttpAddress address #^ int ws-max-bytes #^ int ws-send-max-bytes #^ tuple probes]
    "待ち受けを開き、結んだ宛先を答えるため(開いた後に届いた要求は、probe の口に当たる物を除いてすべて列へ並ぶ)。"
    (setv self.address address
          self.ws-max-bytes ws-max-bytes
          self.ws-send-max-bytes ws-send-max-bytes
          self.probes probes)
    (setv self.client (aiohttp.ClientSession :auto-decompress False
                                             :timeout (aiohttp.ClientTimeout :total None :sock-connect CONNECT-SECONDS
                                                                             :sock-read HTTP-READ-SECONDS)))
    (setv app (web.Application))
    (.add-route app.router "*" "/{tail:.*}" self.dispatch)
    (setv self.runner (web.AppRunner app :access-log None))
    (await (.setup self.runner))
    (await (.start (web.TCPSite self.runner self.address.host self.address.port)))
    (setv bound (get self.runner.addresses 0))
    (HttpAddress :host self.address.host :port (get bound 1)))

  (defn :async #^ HttpEvent next-arrival [self]
    "受け口の列の次の出来事を、積まれるまで待って本体へ渡すため(待ち受けの loop の上で — 閉じた後は列に何が残っていても
     HttpServerClosed)。"
    (when (is-not self.shut None)
      (return (HttpServerClosed :reason self.shut)))
    (self.closed-or (await (.wait self.arrivals))))

  (defn #^ (| HttpEvent None) take-arrival [self]
    "受け口の列に既に在る次の出来事を、答え手の節の thread で待たずに取るため(共有の loop へ入らない・agora-redesign #3688 の案 1 —
     列が空なら None で、答え手の節が next-arrival で待つ)。閉じた後は next-arrival と同じく HttpServerClosed。"
    (when (is-not self.shut None)
      (return (HttpServerClosed :reason self.shut)))
    (setv event (.take-now self.arrivals))
    (if (is event None)
        None
        (self.closed-or event)))

  (defn #^ HttpEvent closed-or [self #^ HttpEvent event]
    "取った出来事を渡すため — 閉じた後に取った閉じの印でない出来事は、閉じた理由の HttpServerClosed に替える。"
    (if (and (is-not self.shut None) (not (isinstance event HttpServerClosed)))
        (HttpServerClosed :reason self.shut)
        event))

  (defn :async #^ web.StreamResponse dispatch [self #^ web.Request request]
    "aiohttp の要求 1 つを、probe の口に当たれば待ち受けの loop の上で自分で答え、他は列へ並べる(receive)ため。"
    (setv probe (run (probe-for self.probes request.method request.path)))
    (if (is probe None)
        (await (self.receive request))
        (await (self.answer-probe probe))))

  (defn :async #^ web.Response answer-probe [self #^ HttpProbe probe]
    "probe の口の要求 1 つに、列も scheduler も通さずに答えるため(頭の註)。answer は別の thread の自分の run で走らせ、待ち受けの loop を
     塞がない。札は他の要求と同じ数えで 1 つ使う。"
    (setv self.count (+ self.count 1))
    (setv answer (await (asyncio.to-thread probe-answer probe)))
    (web.Response :status answer.status :body answer.body :headers (lfor header answer.headers #(header.name header.value))))

  (defn :async #^ None settle [self #^ str ticket #^ HttpCommand command]
    "本体の命令を札の要求へ渡すため(同じ札へ 2 度渡すと KeyError — 判断は要求ごとに 1 つ)。"
    (self.hand-over ticket command)
    None)

  (defn #^ None hand-over [self #^ str ticket #^ HttpCommand command]
    "本体の命令を札の要求へ渡し、その札の本文をもう読ませないため(待ち受けの loop の上で — 受けの coroutine が起きる前に積まれた本文の
     読みも HttpBodyFailed になる)。先に読んで誰も取らなかった本文は命令と一緒に受けの coroutine へ渡す(中継が送る)。同じ札へ 2 度
     渡すと KeyError。"
    (setv waiting (.pop self.waiting ticket))
    (.pop self.unread ticket None)
    (.set-result waiting #(command (.pop self.prefetched ticket None)))
    None)

  (defn #^ None posted [self #^ str ticket #^ HttpCommand command]
    "積まれた命令(post)を待ち受けの loop の上で札へ渡すため。札が待ち受けに無い(2 度目・知らない札)か、相手が先に去って札の待ちが
     取り消されていれば、1 行名乗って捨てる — 積んだ側はもう戻っていて上げる先が無く、待ち受けの loop へ上げると callback の traceback に
     なる(頭の註の応答の送出・#2757)。"
    (try
      (self.hand-over ticket command)
      (except [error #(KeyError asyncio.InvalidStateError)]
        (relay-failed (.format "札 {} への命令 {} は、待ち受けに無い札(2 度目か知らない札)か相手が先に去った札へ積まれたので捨てた: {!r}"
                               ticket (. (type command) __name__) error))))
    None)

  (defn #^ None post [self #^ str ticket #^ HttpCommand command]
    "本体の命令を待ち受けの loop へ積むだけで戻るため(答えの送り — 共有の loop を経ず、往復を待たない・agora-redesign #3688 の子 (3) の
     3b)。待ち受けの loop は積まれた順に回すので、後から撃った across の coroutine より先に渡る。先に読んだ本文は積む前にここで外す —
     答えの後に撃った読みは、命令が渡るより先でも先に読んだ本文を取らない。"
    (.pop self.prefetched ticket None)
    (.call-soon-threadsafe (self.edge-loop) self.posted ticket command)
    None)

  (defn :async #^ web.StreamResponse receive [self #^ web.Request request]
    "aiohttp の要求 1 つ: 札を振り、宣言の小さい本文を先に読んで(頭の註の本文の読み)出来事を並べ、本体の命令を待って実 I/O を撃つため。
     受けた刻は先に読む前に打つ。"
    (setv received-at (time.monotonic))
    (setv self.count (+ self.count 1))
    (setv ticket (str self.count)
          waiting (.create-future (asyncio.get-running-loop)))
    (setv (get self.waiting ticket) waiting)
    (setv early (await (self.prefetch ticket request)))
    (if (is early None)
        (setv (get self.unread ticket) request)
        (setv (get self.prefetched ticket) early))
    (.put self.arrivals (HttpRequestArrived :ticket ticket :method request.method :path request.path :target request.raw-path
                                            :upgrade (upgrade-asked request)
                                            :headers (tuple (gfor [name value] (.items request.headers) (HttpHeader :name name :value value)))
                                            :received-at received-at
                                            :remote request.remote))
    (setv [command unclaimed] (await waiting))
    ;; 命令を受けた札の本文はもう読ませない(渡した拍に hand-over が外した)。本文を上限で断った札は、答えを送った後に接続を閉じる。
    (setv cut-off (in ticket self.oversized))
    (.discard self.oversized ticket)
    (match command
      (HttpRespond :status status :headers headers :body body) (await (self.respond request status headers body cut-off))
      (HttpForward :url url) (await (self.relay-http request url unclaimed))
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
    (.put self.arrivals (WsOpened :ticket ticket :received-at (time.monotonic)))
    (try
      ;; async for では相手の close の理由(message.extra)が読めないので、receive を直に回して相手の閉じを控える。
      (while True
        (setv message (await (.receive ws)))
        (match message.type
          WSMsgType.TEXT (.put self.arrivals (WsTextArrived :ticket ticket :text message.data :received-at (time.monotonic)))
          WSMsgType.BINARY (.put self.arrivals (WsBinaryArrived :ticket ticket :data message.data :received-at (time.monotonic)))
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
    (.put self.arrivals (WsClosed :ticket peer.ticket :code frame.code :reason frame.reason :received-at (time.monotonic)))
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
    (when (> self.dropped-answers 0)
      (relay-failed (.format "待ち受けを閉じる — 相手が先に切って届かなかった答えは合わせて {} 件" self.dropped-answers)))
    (.put self.arrivals (HttpServerClosed :reason reason))
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
    (await (self.drain-body ticket request max-bytes)))

  (defn :async #^ (| HttpBodyOutcome None) prefetch [self #^ str ticket #^ web.Request request]  ; defk にできない: aiohttp の要求の本文を読む実 I/O(event loop の coroutine)
    "宣言の長さが PREFETCH-BYTES 以下の本文を、出来事を並べる前に待ち受けの loop で読み切るため(頭の註の本文の読み)。宣言の無い
     (chunked)・大きい本文と ws の Upgrade は読まない(None — HttpReadBody が待ち受けの loop で上限を見ながら読む)。"
    (setv declared request.content-length)
    (if (or (is declared None) (> declared PREFETCH-BYTES) (upgrade-asked request))
        None
        (await (self.drain-body ticket request declared))))

  (defn #^ (| HttpBodyOutcome None) take-prefetched [self #^ str ticket #^ int max-bytes]
    "待ち受けの loop が先に読んだ札の本文を、答え手の節の thread で 1 度だけ取って上限で判じるため(先に読んでいない・もう取った・命令を
     渡した札は None)。上限を超えれば HttpBodyTooLarge(宣言の長さ)で、札を oversized へ積む — 待ち受けの loop は積まれた順に回すので、
     後から撃った答えより先に入り、答えの後に接続を閉じる(頭の註)。"
    (setv early (.pop self.prefetched ticket None))
    (match early
      (HttpBodyRead :data data) (if (> (len data) max-bytes)
                                    (do (.call-soon-threadsafe (self.edge-loop) self.oversized.add ticket)
                                        (HttpBodyTooLarge :declared (len data)))
                                    early)
      _ early))

  (defn :async #^ HttpBodyOutcome drain-body [self #^ str ticket #^ web.Request request #^ int max-bytes]  ; defk にできない: aiohttp の要求の本文を読む実 I/O(event loop の coroutine)
    "札の要求の本文を max-bytes まで塊で流しながら読むため(読んだ量が上限を超えた拍に止めて断り、札を oversized へ入れる)。"
    (setv declared request.content-length)
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
    (try
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
      (except [error ConnectionError]
        ;; 相手が先に切った(頭の註の応答の送出)— aiohttp へ上げず 1 行で名乗って数える。返した答えは aiohttp が畳む(閉じた transport
        ;; への書きの ConnectionError は aiohttp が黙って受ける)。
        (setv self.dropped-answers (+ self.dropped-answers 1))
        (relay-failed (.format "{} {} への答え(status {})は相手が先に切ったので届かなかった — この待ち受けで {} 件目: {}"
                               request.method request.path status self.dropped-answers error))))
    response)

  (defn :async #^ web.StreamResponse relay-http [self #^ web.Request request #^ str url #^ (| HttpBodyOutcome None) unclaimed]
    "HTTP の要求を中継先へ streaming で写すため(届かなければ 502)。unclaimed = 待ち受けの loop が先に読み、誰も取らなかった本文(頭の註の
     本文の読み — 読み切った本文はそのまま送る)。"
    (setv response None)
    (setv data (match unclaimed
                 (HttpBodyRead :data read) read
                 _ (if request.body-exists request.content None)))
    (try
      (with [:async upstream (.request self.client request.method url :headers (forwarded-headers request False)
                                       :data data :allow-redirects False)]
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


(defk read-on-edge [edge ticket max-bytes]
  {:pre [(: edge WebEdge) (: ticket str) (: max-bytes int)] :post [(: % HttpBodyOutcome)]}
  "札の本文の読みに答えるため: 待ち受けの loop が先に読んだ本文は答え手の節の thread で判じて答え(共有の loop へ入らない — 頭の註の
   本文の読み)、先に読んでいない札は共有の loop から待ち受けの loop で読む(across)。"
  (match (.take-prefetched edge ticket max-bytes)
    None (do (<- outcome HttpBodyOutcome (Await (.across edge (.read-body edge ticket max-bytes))))
             outcome)
    early early))


(defk next-on-edge [edge]
  {:pre [(: edge WebEdge)] :post [(: % HttpEvent)]}
  "受け手へ次の出来事を渡すため: 受け口の列に既に在れば答え手の節の thread で待たずに取り(共有の loop へ入らない — 頭の註の待ち受け)、
   空なら共有の loop から待ち受けの loop で積まれるまで待つ(across)。"
  (match (.take-arrival edge)
    None (do (<- arrival HttpEvent (Await (.across edge (.next-arrival edge))))
             arrival)
    event event))


(defhandler aiohttp-http-server
  ;; 待ち受けの effect の実 I/O(頭の註)。待ち受けの object は session の値に 1 度だけ作る。節は Await で await-handler の共有の event loop に
  ;; 入り、そこから待ち受けの loop の coroutine を across で待つ(組の外側に await-handler が要る)。
  (session val edge (WebEdge))
  (HttpListen [address ws-max-bytes ws-send-max-bytes probes]
    (<- bound HttpAddress (Await (.across edge (.start edge address ws-max-bytes ws-send-max-bytes probes))))
    (resume bound))
  (HttpNextRequest []
    (<- arrival HttpEvent (next-on-edge edge))
    (resume arrival))
  (HttpReadBody [ticket max-bytes]
    (<- outcome HttpBodyOutcome (read-on-edge edge ticket max-bytes))
    (resume outcome))
  (HttpRespond [ticket status headers body]
    ;; 答えは待ち受けの loop へ積むだけ(共有の loop を経ない — 頭の註の応答の送出)。
    (.post edge ticket effect)
    (resume None))
  (HttpForward [ticket url]
    (<- (Await (.across edge (.settle edge ticket effect))))
    (resume None))
  (WsForward [ticket url]
    (<- (Await (.across edge (.settle edge ticket effect))))
    (resume None))
  (WsAccept [ticket]
    (<- (Await (.across edge (.settle edge ticket effect))))
    (resume None))
  (WsSendText [ticket text]
    (<- (Await (.across edge (.send-text edge ticket text))))
    (resume None))
  (WsClose [ticket code reason]
    (<- (Await (.across edge (.close-ws edge ticket code reason))))
    (resume None))
  (HttpShutdown [reason drain-seconds]
    (<- (Await (.across edge (.shutdown edge reason drain-seconds))))
    (resume None))
  (TakeWsSendReport []
    (<- report WsSendReport (Await (.across edge (.take-report edge))))
    (resume report)))
