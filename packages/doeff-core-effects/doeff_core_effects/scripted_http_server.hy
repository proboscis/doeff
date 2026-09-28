;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy)の I/O なしの答え手 scripted-http-server(agora-redesign #802 便 2・ws の終端は
;;; #811 変更 3a)。本物の socket を開かず、台本(HttpScript — 届く出来事の列と中継先ごとの答えと読まない相手の札)で答える。業務を知らない:
;;; 台本の中身は呼び手が渡す。
;;;
;;;   HttpListen       何もしない(開いた扱い)。答え = 渡された宛先のまま(port 0 は 0 のまま — 台本は port を結ばない)。送りの上限を控える
;;;   HttpNextRequest  台本の出来事を順に渡し、尽きたら HttpServerClosed。WsAccept で上げた札の WsOpened と、WsClose・送りの上限の切りで
;;;                    終わった札の WsClosed は、その拍に列の頭へ差す(本物の待ち受けが直ぐに出す出来事と同じ並び)。HttpShutdown の後は
;;;                    列に何が残っていても HttpServerClosed(その理由)
;;;   HttpRespond      受けた命令を記録する。端末が受け取る本文 = byte 列は UTF-8 で読み(読めない byte は置き換え)、file の範囲は
;;;                    file system の effect(ReadBytes — 外側の file の答え手、多くは memory-file-handler)で読んで切り出す
;;;   HttpForward      台本の中継先(url の頭の最長の一致)の http-status で答えた扱いにする。当たらなければ 502
;;;   WsForward        台本の中継先が ws を受ければ 101、受けなければ・当たらなければ 502
;;;   WsAccept         101 で上げた扱いにして記録する(handshake の断りは台本に無い — 断りの形は本物の答え手の検が撃つ)
;;;   WsSendText       上げた札なら 1 通を記録する(WsTextSent — 相手は直ぐに読む: 積んだ byte を流した勘定・所要 0 秒)。読まない相手の札
;;;                    (台本の stalled)は箱に溜め続け、送りの上限を超えた拍に切る(溜まりを捨てた勘定・WsClosed 1006)。閉じた・知らない札は捨てる
;;;   WsClose          上げた札なら閉じを記録し(WsCloseSent)、箱の溜まりを捨て、WsClosed(同じ状態符と理由)を列の頭へ差す
;;;   HttpShutdown     開いている札の全部へ close 1000 を記録し、以後は HttpServerClosed
;;;   TakeWsSendReport 送りの勘定(WsSendReport)を読んで 0 に戻す
;;;   ReadHttpServed   受けた命令と送った ws の記録(HttpServed・WsTextSent・WsCloseSent の tuple)
;;;   AppendHttpScript 台本の出来事の列の後ろへ足す(筋書きの相手役が時刻の来た拍に届ける)
;;; 並び: file の答え手をこの handler より外側に置く。session の値の置き場(doeff_core_effects の state)はさらに外側に要る。
(require doeff-hy.macros [defhandler defk <- val var])
(import doeff_core_effects.http_server_effects [HttpListen HttpNextRequest HttpRespond HttpForward WsForward WsAccept WsSendText WsClose
                                                HttpShutdown TakeWsSendReport WsSendReport ReadHttpServed AppendHttpScript HttpServed
                                                WsTextSent WsCloseSent WsOpened WsClosed HttpServerClosed HttpScript ScriptedUpstream
                                                HttpBodyBytes HttpBodyFileRange HttpNoBody DEFAULT-WS-SEND-MAX-BYTES FLUSH-SAMPLES-LIMIT
                                                WS-CLOSE-NORMAL WS-CLOSE-ABNORMAL])
(import doeff_core_effects.file_effects [ReadBytes FileFailed])

(val CLOSED-REASON "台本の要求の列が尽きた")
(val CUT-REASON "送りの箱が上限を超えた(読まない相手)")
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


(defk served-of [script command]
  {:pre [(: script HttpScript) (: command (| HttpRespond HttpForward WsForward WsAccept))] :post [(: % HttpServed)]}
  "受けた命令を、端末が受け取る答えにするため(本物の待ち受けと同じ振り分け)。"
  (match command
    (HttpRespond :ticket ticket :status status :body body)
      (do (<- text str (body-text body))
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
      (HttpServed :ticket ticket :command command :status 101 :body "ws をここで終端した")))


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
  {:pre [(: backlog dict) (: ticket str)] :post [(: % dict)]}
  "札の箱の溜まりを表から外すため。"
  (dfor [t n] (.items backlog) :if (!= t ticket) t n))


(defhandler scripted-http-server [#^ HttpScript script]
  ;; 台本の待ち受け(頭の註)。pending = まだ届けていない出来事・served = 受けた命令と送った ws の記録の列・opened = ws に上げて開いている札・
  ;; backlog = 読まない相手の札の箱の溜まり(byte)・limit = 送りの上限(HttpListen が控える)・tally = 送りの勘定・closed = HttpShutdown の理由
  ;; (None = 開いている)(どれも session の値)。
  ;; 引数に残す理由: script は呼び手が組んだ凍った台本(出来事の列と中継先の答えと読まない相手)で、組の外で差し替える相手がいない。
  (session var pending script.arrivals)
  (session var served #())
  (session var opened (frozenset))
  (session var backlog {})
  (session var limit DEFAULT-WS-SEND-MAX-BYTES)
  (session var tally EMPTY-REPORT)
  (session var closed None)
  (HttpListen [address ws-max-bytes ws-send-max-bytes]
    (:= limit ws-send-max-bytes)
    (resume address))
  (HttpNextRequest []
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
            (resume head))
      True (resume (HttpServerClosed :reason CLOSED-REASON))))
  (HttpRespond [ticket status headers body]
    (<- answer HttpServed (served-of script effect))
    (:= served (+ served #(answer)))
    (resume None))
  (HttpForward [ticket url]
    (<- answer HttpServed (served-of script effect))
    (:= served (+ served #(answer)))
    (resume None))
  (WsForward [ticket url]
    (<- answer HttpServed (served-of script effect))
    (:= served (+ served #(answer)))
    (resume None))
  (WsAccept [ticket]
    (<- answer HttpServed (served-of script effect))
    (:= served (+ served #(answer)))
    (:= opened (| opened #{ticket}))
    (:= pending (+ #((WsOpened :ticket ticket)) pending))
    (resume None))
  (WsSendText [ticket text]
    (val size (len (.encode text "utf-8")))
    (cond
      (not-in ticket opened) (resume None)
      (in ticket script.stalled)
        (do (<- queued WsSendReport (tally-queued tally size))
            (:= tally queued)
            (val held (+ (.get backlog ticket 0) size))
            (if (> held limit)
                (do (<- cut-tally WsSendReport (tally-dropped tally held True))
                    (:= tally cut-tally)
                    (<- rest dict (without-ticket backlog ticket))
                    (:= backlog rest)
                    (:= opened (- opened #{ticket}))
                    (:= pending (+ #((WsClosed :ticket ticket :code WS-CLOSE-ABNORMAL :reason CUT-REASON)) pending)))
                (:= backlog (| backlog {ticket held})))
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
      (:= pending (+ #((WsClosed :ticket ticket :code code :reason reason)) pending)))
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
  (AppendHttpScript [arrivals]
    (:= pending (+ pending arrivals))
    (resume None)))
