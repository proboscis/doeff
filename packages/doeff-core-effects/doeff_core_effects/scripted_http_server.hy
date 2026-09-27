;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy)の I/O なしの答え手 scripted-http-server(agora-redesign #802 便 2)。本物の socket を
;;; 開かず、台本(HttpScript — 届く要求の列と中継先ごとの答え)で答える。業務を知らない: 台本の中身は呼び手が渡す。
;;;
;;;   HttpListen       何もしない(開いた扱い)
;;;   HttpNextRequest  台本の要求を順に出来事にし、尽きたら HttpServerClosed
;;;   HttpRespond      受けた命令を記録する。端末が受け取る本文 = byte 列は UTF-8 で読み(読めない byte は置き換え)、file の範囲は
;;;                    file system の effect(ReadBytes — 外側の file の答え手、多くは memory-file-handler)で読んで切り出す
;;;   HttpForward      台本の中継先(url の頭の最長の一致)の http-status で答えた扱いにする。当たらなければ 502
;;;   WsForward        台本の中継先が ws を受ければ 101、受けなければ・当たらなければ 502
;;;   ReadHttpServed   受けた命令と答えの記録(HttpServed の tuple)
;;; 並び: file の答え手をこの handler より外側に置く。session の値の置き場(doeff_core_effects の state)はさらに外側に要る。
(require doeff-hy.macros [defhandler defk <- val var])
(import doeff_core_effects.http_server_effects [HttpListen HttpNextRequest HttpRespond HttpForward WsForward ReadHttpServed HttpServed
                                                HttpServerClosed HttpScript ScriptedUpstream HttpBodyBytes HttpBodyFileRange HttpNoBody])
(import doeff_core_effects.file_effects [ReadBytes FileFailed])

(val CLOSED-REASON "台本の要求の列が尽きた")


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
  {:pre [(: script HttpScript) (: command (| HttpRespond HttpForward WsForward))] :post [(: % HttpServed)]}
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
              (HttpServed :ticket ticket :command command :status 502 :body (+ "中継先に ws で届かない: " url))))))


(defhandler scripted-http-server [#^ HttpScript script]
  ;; 台本の待ち受け(頭の註)。pending = まだ届けていない要求・served = 受けた命令と答えの列(session の値)。
  ;; 引数に残す理由: script は呼び手が組んだ凍った台本(要求の列と中継先の答え)で、組の外で差し替える相手がいない。
  (session var pending script.arrivals)
  (session var served #())
  (HttpListen [address ws-max-bytes]
    (resume None))
  (HttpNextRequest []
    (if pending
        (do (val head (get pending 0))
            (:= pending (cut pending 1 None))
            (resume head))
        (resume (HttpServerClosed :reason CLOSED-REASON))))
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
  (ReadHttpServed []
    (resume served)))
