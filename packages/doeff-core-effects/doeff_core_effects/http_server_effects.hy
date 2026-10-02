;;; 汎用の HTTP の待ち受け(受ける側)の effect(agora-redesign #802 便 2・消費者 = #795 webapp の受け口)。送る側の HttpRequest
;;; (http_effects.hy)と対になる土台の語彙で、業務の語を持たない。答え手は仕組みごとに差し替える:
;;;   aiohttp-http-server   本物の待ち受け(aiohttp_http_server.hy — aiohttp は extra `http-server` の依存)
;;;   scripted-http-server  I/O なし — 台本の要求の列を出来事にし、受けた命令を記録する(scripted_http_server.hy)
;;;
;;;   HttpListen       待ち受けを開く(address・ws の 1 通の上限・ws の接続ごとの送りの上限)。答え = 実際に結んだ宛先 HttpAddress
;;;                    (port 0 を渡せば空いている port を結ぶ — 結んだ port はこの答えで知る)
;;;   HttpNextRequest  次の出来事。答え = HttpEvent: HttpRequestArrived(札・method・path・target・頭・ws への Upgrade を求めたか・送り元の address)・
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
;;; 要求の remote = 送り元の address(IP の綴り)。本物の答え手は接続の相手(aiohttp の request.remote)を載せ、名乗れない時は None。
;;; 台本の答え手では、台本の書き手が HttpRequestArrived に載せた値のまま(載せなければ None)。
;;; 要求の本文を読む effect(agora-redesign #880 U1 — 記録の service は POST の本文が本体):
;;;   HttpReadBody     札の要求の本文を max-bytes まで読む。答え = HttpBodyOutcome:
;;;                      HttpBodyRead(data)          本文の全部(本文なしは b"")
;;;                      HttpBodyTooLarge(declared)  本文が max-bytes を超える — declared = 要求が宣言した Content-Length(chunked 等で
;;;                                                  宣言が無ければ None)。宣言が上限を超えていれば 1 byte も読まずに断る。宣言が無い・
;;;                                                  宣言を偽る要求は、流しながら読んで上限を 1 byte でも超えた拍に読むのを止めて断る
;;;                                                  (Content-Length の有無を問わず、memory に載せるのは高々 max-bytes + 1 byte)
;;;                      HttpBodyFailed(reason)      読めなかった(相手が途中で切った・本文の形が壊れている・知らない札・命令を
;;;                                                  撃った後の札)
;;;                    命令(HttpRespond 等)より前に、札ごとに高々 1 度撃つ。断った後の答え(413 等)は呼び手が HttpRespond で送り、
;;;                    答え手はその答えを送った後に接続を閉じる(残りの本文を読み捨てない)。HttpForward に渡す札では撃たない(中継は本文を
;;;                    streaming で写すので、先に読むと写す本文が無くなる)。
;;; file の状態は file_effects.hy の StatPath で読む(ここに持たない)。
;;; 答え手が自分で答える probe の口(agora-redesign #2776 — 本体の流れが止まっても生存の問いに答える):
;;;   HttpListen の probes = HttpProbe の列。GET か HEAD で path(query を除く)が probe の path と同じ要求は、出来事にせず(HttpNextRequest に
;;;   届かない)、答え手がその HttpProbe の answer(答えが HttpProbeAnswer の Program)を自分の run で走らせて答える。札は他の要求と同じ
;;;   数えで 1 つ使う(届いた順の札の並びを本物と台本でそろえる)。
;;;   answer は閉じた Program にする: 答え手は handler を足さずに走らせるので、要る handler(state・最新の値の置き場・時計 …)は answer の
;;;   中に被せる。本体の run の状態(session の値)も Await の共有の event loop も使わない。answer が例外で落ちれば 500 と理由の文
;;;   (probe-failure)。
;;;   本物の答え手は、待ち受けを await-handler の共有の event loop とは別の thread の loop に立て、answer をさらに別の thread で走らせる —
;;;   共有の loop と協調型の scheduler が止まっても probe は答える。台本の答え手は、出来事の列でその要求に来た HttpNextRequest の中で answer を
;;;   走らせて答え、記録(HttpServed)に残す。
;;;   何を答えるか(生存 = process が居る・仕事ができる = 本体の流れが動いている、の分け方や閾値)は呼び手が answer に書く — ここは業務の語を
;;;   持たない。
;;; 2 つの答え手が同じに決める物は、ここの判断の defk を両方が呼ぶ: carries-content(本文を運ぶ答えか — HEAD・1xx・204・304 は送らない)・
;;; ws-refusal-status(WsAccept の断りの status)・send-overflows(送りの上限で切るか)・closing-of(WsClosed で名乗る状態符と理由)・
;;; probe-for(要求が probe の口に当たるか)・probe-failure(answer が落ちた probe の答え)。probe の answer を走らせるのも両方が同じ
;;; probe-answer(Program の外から呼ぶ入口)。
;;; 2 つが同じ性質を持つことは tests/test_http_server_contract.hy の契約テストが両方の答え手で確かめる。
;;;
;;; 台本の語彙(本物の待ち受けには無い): HttpScript・ScriptedUpstream・ScriptedBody = 台本・HttpServed = 受けた命令と端末が受け取る答え・
;;; WsTextSent / WsCloseSent = ws の接続へ送った 1 通と閉じ・ReadHttpServed = 記録を読む effect(検と筋書きが覗くため)・
;;; AppendHttpScript = 走っている台本の後ろへ出来事を足す effect(筋書きの相手役が時刻の来た拍に届ける — scripted-http-server だけが答える)。
(require doeff-hy.macros [defk deff val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase Program run])

;; ws の 1 通(中継では 1 frame)の上限の既定(byte)。
(val DEFAULT-WS-MAX-BYTES (* 1024 1024))
;; ws の接続ごとの送りの箱の上限の既定(byte)— 読まない相手の箱をここまで溜めて切る。
(val DEFAULT-WS-SEND-MAX-BYTES (* 16 1024 1024))
;; 待ち受けを閉じる時に送りの箱を流し切るのを待つ上限の既定(秒)。
(val DEFAULT-DRAIN-SECONDS 1.0)
;; ws の閉じの状態符(RFC 6455 7.4.1)— 答え手が名乗る物。
(val WS-CLOSE-NORMAL 1000)
(val WS-CLOSE-ABNORMAL 1006)
;; ws に上げられない要求への断りの答えの本文(WsAccept の断り — 答え手が名乗る物)。
(val WS-REFUSAL-TEXT "WebSocket の Upgrade(GET・Upgrade: websocket・Sec-WebSocket-Key)が要る")
;; 送りの上限で切った接続の WsClosed の理由(答え手が名乗る物)。
(val WS-CUT-REASON "送りの箱が上限を超えた(読まない相手)")


(defrecord HttpAddress
  "待ち受けの宛先。"
  (#^ str host)
  (#^ int port))


(defrecord HttpHeader
  "HTTP の頭 1 つ(同じ名が複数あっても落とさないよう列で持つ)。"
  (#^ str name)
  (#^ str value))


(defrecord HttpRequestArrived
  "届いた要求 1 つ: 札・method・path(query なし)・target(query つき)・頭の列・ws への Upgrade を求めたか・受けた拍(頭の註)・
   remote = 送り元の address(IP の綴り — 答え手が名乗れない時は None)。"
  (#^ str ticket)
  (#^ str method)
  (#^ str path)
  (#^ str target)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ bool upgrade)
  (setv #^ (| float None) received-at None
        #^ (| str None) remote None))


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


(defrecord HttpBodyRead
  "HttpReadBody の答え: 要求の本文の全部(本文なしは b\"\")。"
  (#^ bytes data))


(defrecord HttpBodyTooLarge
  "HttpReadBody の答え: 本文が上限を超えた(declared = 要求が宣言した Content-Length・宣言が無ければ None — 頭の註)。"
  (#^ (| int None) declared))


(defrecord HttpBodyFailed
  "HttpReadBody の答え: 本文を読めなかった(reason = 理由の文 — 頭の註)。"
  (#^ str reason))


;; HttpReadBody の答えの union。
(val HttpBodyOutcome (| HttpBodyRead HttpBodyTooLarge HttpBodyFailed))


(defrecord HttpProbeAnswer
  "probe の口の答え(頭の註): status・頭・本文の byte 列(HEAD の答えは本文を送らない — carries-content)。"
  (#^ int status)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ bytes body))


(defrecord HttpProbe
  "答え手が自分で答える probe の口 1 つ(頭の註): path = 当たる path(query を除く — GET と HEAD だけ当たる)・answer = 答えが
   HttpProbeAnswer の閉じた Program(要求ごとに答え手が自分の run で走らせる)。"
  (#^ str path)
  (#^ Program answer))


(defclass [(dataclass :frozen True)] HttpListen [EffectBase]
  "待ち受けを開く(頭の註)。probes = 答え手が自分で答える probe の口の列。答え = 結んだ宛先 HttpAddress。"
  (#^ HttpAddress address)
  (setv #^ int ws-max-bytes DEFAULT-WS-MAX-BYTES
        #^ int ws-send-max-bytes DEFAULT-WS-SEND-MAX-BYTES
        #^ (get tuple #(HttpProbe ...)) probes #()))


(defclass [(dataclass :frozen True)] HttpNextRequest [EffectBase]
  "次の出来事を 1 つ受ける(頭の註)。")


(defclass [(dataclass :frozen True)] HttpRespond [EffectBase]
  "札の要求へ status・頭・本文を送る(頭の註)。"
  (#^ str ticket)
  (#^ int status)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ HttpBody body))


(defclass [(dataclass :frozen True)] HttpReadBody [EffectBase]
  "札の要求の本文を max-bytes まで読む(頭の註)。答え = HttpBodyOutcome。"
  (#^ str ticket)
  (#^ int max-bytes))


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


;; --- 答え手が共に呼ぶ判断(本物の aiohttp-http-server と台本の scripted-http-server が同じ関数で決める)--------------------------------

(defk carries-content [method status]
  {:pre [(: method str) (: status int)] :post [(: % bool)] :tags {:context "http-server" :role "judgment"}}
  "method の要求への status の答えが本文を運ぶか(RFC 9110 6.4.1 — HEAD の答えと 1xx・204・304 は本文を送らない)。運ばない答えは
   HttpRespond に本文を渡されても送らない(送ると同じ接続の次の答えの頭として読まれる)。"
  (not (or (= method "HEAD") (< status 200) (in status #(204 304)))))


(defk ws-refusal-status [method upgrade]
  {:pre [(: method str) (: upgrade bool)] :post [(: % (| int None))] :tags {:context "http-server" :role "judgment"}}
  "WsAccept で ws に上げられない要求の形への断りの status(GET でない 405・Upgrade: websocket が無い 426)。None = 形は上げられる
   (本物の答え手は handshake の残りの検め — Sec-WebSocket-Key 等 — で断れば 400)。"
  (cond
    (!= method "GET") 405
    (not upgrade) 426
    True None))


(defk send-overflows [held size limit]
  {:pre [(: held int) (: size int) (: limit int)] :post [(: % bool)] :tags {:context "http-server" :role "judgment"}}
  "送りの箱に held byte が溜まった接続へ size byte の 1 通を積むと上限 limit を超えるか(超えるならその 1 通を積まずに接続を切る —
   WsSendText の頭の註)。"
  (> (+ held size) limit))


(defrecord WsCloseFrame
  "ws の閉じの状態符と理由(こちらが送った close・相手から受けた close・接続が終わった時に名乗る物)。"
  (#^ int code)
  (#^ str reason))


(defk closing-of [cut sent received lost]
  {:pre [(: cut (| str None)) (: sent (| WsCloseFrame None)) (: received (| WsCloseFrame None)) (: lost (| int None))]
   :post [(: % WsCloseFrame)] :tags {:context "http-server" :role "judgment"}}
  "ws の接続が終わった時に WsClosed で名乗る状態符と理由を決めるため: 送りの上限で切った(cut = 切りの理由)なら 1006・こちらが WsClose で
   閉じた(sent)ならその状態符と理由・相手が閉じた(received)なら相手の状態符と理由・どれでもなければ切れた時の状態符(lost — 無ければ
   1006)。"
  (cond
    (is-not cut None) (WsCloseFrame :code WS-CLOSE-ABNORMAL :reason cut)
    (is-not sent None) sent
    (is-not received None) received
    True (WsCloseFrame :code (if (is lost None) WS-CLOSE-ABNORMAL lost) :reason "")))


(defk probe-for [probes method path]
  {:pre [(: probes tuple) (: method str) (: path str)] :post [(: % (| HttpProbe None))] :tags {:context "http-server" :role "judgment"}}
  "要求を答え手が自分で答えるか決めるため(頭の註の probe の口): GET か HEAD で、path(query を除く)が probes のどれかの path と同じなら
   その HttpProbe、他は None(出来事として本体へ渡す)。"
  (if (in method #("GET" "HEAD"))
      (next (gfor probe probes :if (= probe.path path) probe) None)
      None))


(defk probe-failure [path reason]
  {:pre [(: path str) (: reason str)] :post [(: % HttpProbeAnswer)] :tags {:context "http-server" :role "judgment"}}
  "probe の answer が落ちた時の答えを決めるため(500 と理由の文 — 答えを出せない probe を健康と読ませない)。"
  (HttpProbeAnswer :status 500 :headers #((HttpHeader :name "Content-Type" :value "text/plain; charset=utf-8"))
                   :body (.encode (.format "{} の probe は答えを出せなかった: {}" path reason) "utf-8")))


(deff probe-answer [probe]  ; defk にできない: 答え手が本体の Program の外(本物 = 待ち受けの loop の外の thread・台本 = handler の節)で呼び、閉じた answer を自分の run で走らせる入口
  {:pre [(: probe HttpProbe)] :post [(: % HttpProbeAnswer)] :tags {:context "http-server" :role "foundation"}}
  "probe の口の要求 1 つの答えを、本体の run(scheduler・session の値・Await の共有の event loop)に触れずに出すため(頭の註): answer を
   自分の run で走らせ、落ちた・答えの型が違う時は probe-failure の 500。2 つの答え手が同じこの関数で答える。"
  (try
    (setv answer (run probe.answer))
    (except [error Exception]
      (setv answer error)))
  (if (isinstance answer HttpProbeAnswer)
      answer
      (run (probe-failure probe.path (repr answer)))))


;; --- 台本の語彙(scripted-http-server) -----------------------------------------------------------------------------------

(defrecord ScriptedUpstream
  "中継先 1 つの台本の答え: base = この頭で始まる URL へ届いた中継がこの答えを受ける・http-status = HTTP の中継の答えの status・
   accepts-ws = ws の中継を受ける(受けなければ 502)。"
  (#^ str base)
  (#^ int http-status)
  (#^ bool accepts-ws))


(defrecord ScriptedBody
  "札の要求の本文 1 つの台本: data = 相手が送る本文の byte 列・failed = 読みの途中で相手が切った等の理由(None = 読める)。宣言の長さ
   (Content-Length)は要求の頭(HttpRequestArrived の headers)に台本の書き手が載せる — 載せなければ chunked と同じ宣言なし。台本に本文の
   無い札は本文なし(b\"\")。"
  (#^ str ticket)
  (#^ bytes data)
  (setv #^ (| str None) failed None))


(defrecord HttpScript
  "scripted-http-server の台本: 届く出来事の列(要求と、WsAccept で上げた札の ws の出来事)と、中継先ごとの答えと、読まない相手の札
   (stalled — その札の送りは箱に溜まり続け、送りの上限を超えれば切られる)と、要求の本文(bodies — HttpReadBody の答えの元)。"
  (#^ (get tuple #((| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed) ...)) arrivals)
  (setv #^ (get tuple #(ScriptedUpstream ...)) upstreams #()
        #^ (get frozenset str) stalled (frozenset)
        #^ (get tuple #(ScriptedBody ...)) bodies #()))


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
  "走っている台本の出来事の列の後ろへ arrivals を足す(頭の註)。bodies = 足す要求の本文の台本。答え = None。"
  (#^ (get tuple #((| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed) ...)) arrivals)
  (setv #^ (get tuple #(ScriptedBody ...)) bodies #()))
