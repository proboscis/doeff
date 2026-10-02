;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy)の I/O なしの答え手 scripted-http-server(agora-redesign #802 便 2・ws の終端は
;;; #811 変更 3a)。本物の socket を開かず、台本(HttpScript — 届く出来事の列と中継先ごとの答えと読まない相手の札)で答える。業務を知らない:
;;; 台本の中身は呼び手が渡す。
;;;
;;;   HttpListen       何もしない(開いた扱い)。答え = 渡された宛先のまま(port 0 は 0 のまま — 台本は port を結ばない)。送りの上限と
;;;                    probe の口を控える
;;;   HttpNextRequest  台本の出来事を順に渡し、尽きたら HttpServerClosed。WsAccept で上げた札の WsOpened と、WsClose・送りの上限の切りで
;;;                    終わった札の WsClosed は、その拍に列の頭へ差す(本物の待ち受けが直ぐに出す出来事と同じ並び)。HttpShutdown の後は
;;;                    列に何が残っていても HttpServerClosed(その理由)。probe の口に当たる要求(probe-for)は渡さず、ここで本物と同じ
;;;                    probe-answer(answer を自分の run で — 本体の run の handler と状態に触れない)で答えて記録し、次の出来事へ進む。
;;;                    台本に thread は無いので、本体の流れが probe を待たせない形は「本体へ届かず、本体の命令を待たずに答える」で表す
;;;   HttpReadBody     台本の本文(ScriptedBody)で答える: 宣言の長さ(要求の頭の Content-Length)が上限を超えれば読まずに HttpBodyTooLarge、
;;;                    本文が上限を超えれば HttpBodyTooLarge(宣言が無ければ None)、failed が在れば HttpBodyFailed、他は HttpBodyRead(台本に
;;;                    本文の無い札は b"")。札ごとに 1 度だけ — 読み終えた札・命令を受けた札・知らない札は HttpBodyFailed(本物と同じ)
;;;   HttpRespond      受けた命令を記録する。端末が受け取る本文 = byte 列は UTF-8 で読み(読めない byte は置き換え)、file の範囲は
;;;                    file system の effect(ReadBytes — 外側の file の答え手、多くは memory-file-handler)で読んで切り出す。本文を運ばない
;;;                    答え(carries-content — HEAD・1xx・204・304)の本文は空
;;;   HttpForward      台本の中継先(url の頭の最長の一致)の http-status で答えた扱いにする。当たらなければ 502
;;;   WsForward        台本の中継先が ws を受ければ 101、受けなければ・当たらなければ 502
;;;   WsAccept         101 で上げた扱いにして記録する。ws に上げられない形の要求(ws-refusal-status — GET でない 405・Upgrade の無い 426)は
;;;                    断りの答えを記録し、上げない(ws の出来事は出ない)
;;;   WsSendText       上げた札なら 1 通を記録する(WsTextSent — 相手は直ぐに読む: 積んだ byte を流した勘定・所要 0 秒)。読まない相手の札
;;;                    (台本の stalled)は箱に溜め続ける。箱の溜まりに足すと送りの上限を超える 1 通(send-overflows)は積まずに接続を切る
;;;                    (溜まりを捨てた勘定・WsClosed 1006 と切りの理由)。閉じた・知らない札は捨てる
;;;   WsClose          上げた札なら閉じを記録し(WsCloseSent)、箱の溜まりを捨て、WsClosed(同じ状態符と理由 — closing-of)を列の頭へ差す
;;;   HttpShutdown     開いている札の全部へ close 1000 を記録し、以後は HttpServerClosed
;;;   TakeWsSendReport 送りの勘定(WsSendReport)を読んで 0 に戻す
;;;   ReadHttpServed   受けた命令と送った ws の記録(HttpServed・WsTextSent・WsCloseSent の tuple)
;;;   AppendHttpScript 台本の出来事の列の後ろへ足す(筋書きの相手役が時刻の来た拍に届ける)。足す要求の本文の台本も足す
;;; 本物の答え手と同じに決める物は http_server_effects.hy の判断の defk を呼ぶ(契約テストは tests/test_http_server_contract.hy)。
;;; 命令は届けた要求の札へ撃つ(届けていない札への命令は KeyError — 本物の答え手と同じ)。
;;; 並び: file の答え手をこの handler より外側に置く。session の値の置き場(doeff_core_effects の state)はさらに外側に要る。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "http-server" :role "foundation"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import doeff_core_effects.http_server_effects [HttpListen HttpNextRequest HttpRespond HttpForward WsForward WsAccept WsSendText WsClose
                                                HttpShutdown TakeWsSendReport WsSendReport ReadHttpServed AppendHttpScript HttpServed
                                                WsTextSent WsCloseSent WsOpened WsClosed HttpServerClosed HttpScript ScriptedUpstream
                                                HttpBodyBytes HttpBodyFileRange HttpNoBody DEFAULT-WS-SEND-MAX-BYTES FLUSH-SAMPLES-LIMIT
                                                WS-CLOSE-NORMAL HttpReadBody HttpBodyRead HttpBodyTooLarge HttpBodyFailed
                                                HttpBodyOutcome HttpRequestArrived ScriptedBody WS-CUT-REASON WS-REFUSAL-TEXT WsCloseFrame
                                                carries-content ws-refusal-status send-overflows closing-of HttpProbe HttpProbeAnswer
                                                probe-for probe-answer WsTextArrived WsBinaryArrived HttpHeader])
(import doeff_core_effects.file_effects [ReadBytes FileFailed])

(val CLOSED-REASON "台本の要求の列が尽きた")
(val EMPTY-REPORT (WsSendReport :queued-frames 0 :queued-bytes 0 :flushed-bytes 0 :flush-seconds #() :dropped-bytes 0 :cuts 0))


(defk upstream-for [script url]
  {:pre [(: script HttpScript) (: url str)] :post [(: % (| ScriptedUpstream None))]}
  "中継の url を受ける台本の中継先を選ぶため(頭の最長の一致 — 無ければ None = 届かない)。"
  (val hits (sorted (gfor u script.upstreams :if (.startswith url (.rstrip u.base "/")) u) :key (fn [u] (len u.base))))
  (if hits (get hits -1) None))


(defk body-text [body]
  {:pre [(: body (| HttpBodyBytes HttpBodyFileRange HttpNoBody))] :post [(: % str)]}
  "送った本文を端末が受け取る文字列にするため(file の範囲は file system の effect で読む — 読めなければ理由の文)。"
  (match body
    (HttpBodyBytes :data data) (.decode data "utf-8" :errors "replace")
    (HttpBodyFileRange :path path :start start :length length)
      (do (<- read (| bytes FileFailed) (ReadBytes path))
          (if (isinstance read FileFailed)
              (+ "file を読めない: " read.detail)
              (.decode (cut read start (+ start length)) "utf-8" :errors "replace")))
    (HttpNoBody) ""))


(defk served-of [script command arrival]
  {:pre [(: script HttpScript) (: command (| HttpRespond HttpForward WsForward WsAccept)) (: arrival HttpRequestArrived)]
   :post [(: % HttpServed)]}
  "受けた命令を、端末が受け取る答えにするため(本物の待ち受けと同じ振り分け — arrival = 命令の札の届いた要求)。本文を運ばない答え
   (carries-content — HEAD・1xx・204・304)の本文は空・ws に上げられない形の要求への WsAccept は断りの答え(ws-refusal-status)。"
  (match command
    (HttpRespond :ticket ticket :status status :body body)
      (do (<- carried bool (carries-content arrival.method status))
          (<- text str (if carried (body-text body) (body-text (HttpNoBody))))
          (HttpServed :ticket ticket :command command :status status :body text))
    (HttpForward :ticket ticket :url url)
      (do (<- upstream (| ScriptedUpstream None) (upstream-for script url))
          (if (is upstream None)
              (HttpServed :ticket ticket :command command :status 502 :body (+ "中継先に届かない: " url))
              (HttpServed :ticket ticket :command command :status upstream.http-status :body (+ "台本の中継先 " upstream.base))))
    (WsForward :ticket ticket :url url)
      (do (<- upstream (| ScriptedUpstream None) (upstream-for script url))
          (if (and (is-not upstream None) upstream.accepts-ws)
              (HttpServed :ticket ticket :command command :status 101 :body (+ "ws の中継 " upstream.base))
              (HttpServed :ticket ticket :command command :status 502 :body (+ "中継先に ws で届かない: " url))))
    (WsAccept :ticket ticket)
      (do (<- refusal (| int None) (ws-refusal-status arrival.method arrival.upgrade))
          (if (is refusal None)
              (HttpServed :ticket ticket :command command :status 101 :body "ws をここで終端した")
              (HttpServed :ticket ticket :command command :status refusal :body WS-REFUSAL-TEXT)))))


(defk tally-queued [tally size]
  {:pre [(: tally WsSendReport) (: size int)] :post [(: % WsSendReport)]}
  "箱へ 1 通 size byte を積んだ勘定を足すため。"
  (WsSendReport :queued-frames (+ tally.queued-frames 1) :queued-bytes (+ tally.queued-bytes size) :flushed-bytes tally.flushed-bytes
                :flush-seconds tally.flush-seconds :dropped-bytes tally.dropped-bytes :cuts tally.cuts))


(defk tally-flushed [tally size]
  {:pre [(: tally WsSendReport) (: size int)] :post [(: % WsSendReport)]}
  "箱の 1 通 size byte を相手へ流した勘定を足すため(台本の相手は直ぐに読む — 所要 0 秒。標本は新しい方から上限まで)。"
  (WsSendReport :queued-frames tally.queued-frames :queued-bytes tally.queued-bytes :flushed-bytes (+ tally.flushed-bytes size)
                :flush-seconds (cut (+ tally.flush-seconds #(0.0)) (- FLUSH-SAMPLES-LIMIT) None)
                :dropped-bytes tally.dropped-bytes :cuts tally.cuts))


(defk tally-dropped [tally size cut]
  {:pre [(: tally WsSendReport) (: size int) (: cut bool)] :post [(: % WsSendReport)]}
  "箱の size byte を捨てた勘定を足すため(cut = 送りの上限で切った接続なら切りの数も)。"
  (WsSendReport :queued-frames tally.queued-frames :queued-bytes tally.queued-bytes :flushed-bytes tally.flushed-bytes
                :flush-seconds tally.flush-seconds :dropped-bytes (+ tally.dropped-bytes size) :cuts (+ tally.cuts (if cut 1 0))))


(defk without-ticket [backlog ticket]
  {:tp [V] :pre [(: backlog (of dict str V)) (: ticket str)] :post [(: % (of dict str V))]}
  "札の箱の溜まりを表から外すため。"
  (dfor [t n] (.items backlog) :if (!= t ticket) t n))


(defk declared-length [headers]
  {:pre [(: headers (of tuple HttpHeader ...))] :post [(: % (| int None))]}
  "要求の頭から宣言された本文の長さ(Content-Length)を読むため(無い・数でなければ None — chunked と同じ宣言なし)。"
  (val said (next (gfor h headers :if (= (.lower h.name) "content-length") (.strip h.value)) None))
  (if (and (is-not said None) (.isdigit said)) (int said) None))


(defk scripted-body-outcome [declared body max-bytes]
  {:pre [(: declared (| int None)) (: body (| ScriptedBody None)) (: max-bytes int)] :post [(: % HttpBodyOutcome)]}
  "台本の本文 1 つを HttpReadBody の答えにするため(本物と同じ順: 宣言が上限を超えれば読まずに断る → 読みの途中の失敗 → 読んだ量が上限を
   超えれば断る → 読めた)。"
  (val data (if (is body None) b"" body.data))
  (cond
    (and (is-not declared None) (> declared max-bytes)) (HttpBodyTooLarge :declared declared)
    (and (is-not body None) (is-not body.failed None)) (HttpBodyFailed :reason body.failed)
    (> (len data) max-bytes) (HttpBodyTooLarge :declared declared)
    True (HttpBodyRead :data data)))


(defk bodies-by-ticket [bodies]
  {:pre [(: bodies (of tuple ScriptedBody ...))] :post [(: % (of dict str ScriptedBody))]}
  "本文の台本を札で引ける表にするため。"
  (dfor b bodies b.ticket b))


(defrecord ProbeHit
  "台本の次の出来事のうち、待ち受けが自分で答える probe の口に当たった要求と、当たった口。"
  (#^ HttpRequestArrived arrival)
  (#^ HttpProbe probe))


(defk probe-hit [probes event]
  {:pre [(: probes (of tuple HttpProbe ...)) (: event (| HttpRequestArrived WsTextArrived WsBinaryArrived WsClosed WsOpened))] :post [(: % (| ProbeHit None))]
   :tags {:context "http-server" :role "judgment"}}
  "台本の次の出来事が、待ち受けが自分で答える probe の口の要求か決めるため(当たらなければ None — ws の出来事も本体へ渡す)。"
  (if (isinstance event HttpRequestArrived)
      (do (<- probe (| HttpProbe None) (probe-for probes event.method event.path))
          (if (is probe None) None (ProbeHit :arrival event :probe probe)))
      None))


(defk probe-served [arrival answer]
  {:pre [(: arrival HttpRequestArrived) (: answer HttpProbeAnswer)] :post [(: % HttpServed)] :tags {:context "http-server" :role "judgment"}}
  "probe の口の答えを、端末が受け取る答えの記録にするため(本物と同じく HEAD の答えの本文は空 — carries-content)。"
  (<- carried bool (carries-content arrival.method answer.status))
  (HttpServed :ticket arrival.ticket
              :command (HttpRespond :ticket arrival.ticket :status answer.status :headers answer.headers
                                    :body (HttpBodyBytes :data answer.body))
              :status answer.status
              :body (if carried (.decode answer.body "utf-8" :errors "replace") "")))


(defhandler scripted-http-server [#^ HttpScript script]
  ;; 台本の待ち受け(頭の註)。pending = まだ届けていない出来事・served = 受けた命令と送った ws の記録の列・opened = ws に上げて開いている札・
  ;; backlog = 読まない相手の札の箱の溜まり(byte)・limit = 送りの上限(HttpListen が控える)・tally = 送りの勘定・closed = HttpShutdown の理由
  ;; (None = 開いている)・body-table = 札 → 本文の台本・readable = 本文をまだ読める札 → 宣言の長さ(届けた要求の札を入れ、読んだ・命令を
  ;; 受けた札を外す)・arrived = 届けた要求(札 → HttpRequestArrived — 命令の答えの形を要求の method と Upgrade で決める)・mouths = 自分で
  ;; 答える probe の口(HttpListen が控える)(どれも session の値)。
  ;; 引数に残す理由: script は呼び手が組んだ凍った台本(出来事の列と中継先の答えと読まない相手)で、組の外で差し替える相手がいない。
  (session var pending script.arrivals)
  (session var body-table (dfor b script.bodies b.ticket b))
  (session var readable {})
  (session var arrived {})
  (session var served #())
  (session var opened (frozenset))
  (session var backlog {})
  (session var limit DEFAULT-WS-SEND-MAX-BYTES)
  (session var tally EMPTY-REPORT)
  (session var closed None)
  (session var mouths #())
  (HttpListen [address ws-max-bytes ws-send-max-bytes probes]
    (:= limit ws-send-max-bytes)
    (:= mouths probes)
    (resume address))
  (HttpNextRequest []
    ;; probe の口に当たる要求は本体へ渡さず、ここで答えて記録する(頭の註)— 列の頭が probe でなくなるまで。
    (var answering True)
    (while (and answering (is closed None) pending)
      (<- hit (| ProbeHit None) (probe-hit mouths (get pending 0)))
      (if (is hit None)
          (:= answering False)
          (do (<- answer HttpServed (probe-served hit.arrival (probe-answer hit.probe)))
              (:= served (+ served #(answer)))
              (:= pending (cut pending 1 None)))))
    (cond
      (is-not closed None) (resume (HttpServerClosed :reason closed))
      pending
        (do (val head (get pending 0))
            (:= pending (cut pending 1 None))
            (when (isinstance head WsClosed)
              ;; 相手が閉じた札: 以後の送りは捨てる・箱の溜まりは捨てた勘定へ。
              (:= opened (- opened #{head.ticket}))
              (<- dropped WsSendReport (tally-dropped tally (.get backlog head.ticket 0) False))
              (:= tally dropped)
              (<- rest dict (without-ticket backlog head.ticket))
              (:= backlog rest))
            (when (isinstance head HttpRequestArrived)
              (<- declared (| int None) (declared-length head.headers))
              (:= readable (| readable {head.ticket declared}))
              (:= arrived (| arrived {head.ticket head})))
            (resume head))
      True (resume (HttpServerClosed :reason CLOSED-REASON))))
  (HttpReadBody [ticket max-bytes]
    (if (not-in ticket readable)
        (resume (HttpBodyFailed :reason (.format "札 {} の要求の本文は読めない(知らない札・読み終えた札・命令を撃った後の札)" ticket)))
        (do (<- outcome HttpBodyOutcome (scripted-body-outcome (get readable ticket) (.get body-table ticket) max-bytes))
            (<- rest dict (without-ticket readable ticket))
            (:= readable rest)
            (resume outcome))))
  (HttpRespond [ticket status headers body]
    (<- answer HttpServed (served-of script effect (get arrived ticket)))
    (:= served (+ served #(answer)))
    (<- rest dict (without-ticket readable ticket))
    (:= readable rest)
    (resume None))
  (HttpForward [ticket url]
    (<- answer HttpServed (served-of script effect (get arrived ticket)))
    (:= served (+ served #(answer)))
    (<- rest dict (without-ticket readable ticket))
    (:= readable rest)
    (resume None))
  (WsForward [ticket url]
    (<- answer HttpServed (served-of script effect (get arrived ticket)))
    (:= served (+ served #(answer)))
    (<- rest dict (without-ticket readable ticket))
    (:= readable rest)
    (resume None))
  (WsAccept [ticket]
    (<- answer HttpServed (served-of script effect (get arrived ticket)))
    (:= served (+ served #(answer)))
    (<- rest dict (without-ticket readable ticket))
    (:= readable rest)
    ;; 断った札(101 でない答え)は上げない — ws の出来事は出ない。
    (when (= answer.status 101)
      (:= opened (| opened #{ticket}))
      (:= pending (+ #((WsOpened :ticket ticket)) pending)))
    (resume None))
  (WsSendText [ticket text]
    (val size (len (.encode text "utf-8")))
    (val held (.get backlog ticket 0))
    (<- overflows bool (send-overflows held size limit))
    (cond
      (not-in ticket opened) (resume None)
      ;; 上限を超える 1 通は積まずに切る(本物と同じ send-overflows)— 箱の溜まりを捨て、WsClosed(closing-of — 1006 と切りの理由)。
      overflows
        (do (<- cut-tally WsSendReport (tally-dropped tally held True))
            (:= tally cut-tally)
            (<- rest dict (without-ticket backlog ticket))
            (:= backlog rest)
            (:= opened (- opened #{ticket}))
            (<- frame WsCloseFrame (closing-of WS-CUT-REASON None None None))
            (:= pending (+ #((WsClosed :ticket ticket :code frame.code :reason frame.reason)) pending))
            (resume None))
      (in ticket script.stalled)
        (do (<- queued WsSendReport (tally-queued tally size))
            (:= tally queued)
            (:= backlog (| backlog {ticket (+ held size)}))
            (resume None))
      True
        (do (<- queued WsSendReport (tally-queued tally size))
            (<- flushed WsSendReport (tally-flushed queued size))
            (:= tally flushed)
            (:= served (+ served #((WsTextSent :ticket ticket :text text))))
            (resume None))))
  (WsClose [ticket code reason]
    (when (in ticket opened)
      (:= served (+ served #((WsCloseSent :ticket ticket :code code :reason reason))))
      (<- dropped WsSendReport (tally-dropped tally (.get backlog ticket 0) False))
      (:= tally dropped)
      (<- rest dict (without-ticket backlog ticket))
      (:= backlog rest)
      (:= opened (- opened #{ticket}))
      ;; 名乗る状態符と理由は本物と同じ closing-of(こちらの閉じ)。
      (<- frame WsCloseFrame (closing-of None (WsCloseFrame :code code :reason reason) None None))
      (:= pending (+ #((WsClosed :ticket ticket :code frame.code :reason frame.reason)) pending)))
    (resume None))
  (HttpShutdown [reason drain-seconds]
    (for [ticket (sorted opened)]
      (:= served (+ served #((WsCloseSent :ticket ticket :code WS-CLOSE-NORMAL :reason reason))))
      (<- dropped WsSendReport (tally-dropped tally (.get backlog ticket 0) False))
      (:= tally dropped))
    (:= backlog {})
    (:= opened (frozenset))
    (:= closed reason)
    (resume None))
  (TakeWsSendReport []
    (val report tally)
    (:= tally EMPTY-REPORT)
    (resume report))
  (ReadHttpServed []
    (resume served))
  (AppendHttpScript [arrivals bodies]
    (:= pending (+ pending arrivals))
    (<- added dict (bodies-by-ticket bodies))
    (:= body-table (| body-table added))
    (resume None)))
