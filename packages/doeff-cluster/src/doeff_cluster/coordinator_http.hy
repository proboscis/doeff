;;; coordinator へ話す HTTP の client を 1 か所で作る(heartbeat・共有の保存・task の 3 つの口の handler が使う)。
;;; HTTP の client はこの module と、それを持つ handler の中だけに閉じる(業務コードは effect しか知らない)。
;;;
;;; 2026-09-23 の実測(newmac の `turn-runner` が `GET /board` の `ConnectTimeout` で 5 回落ちた件):
;;;
;;; - 落ちた時刻 5 回とも、atlas の tailscaled が newmac への経路を IPv4(LAN)から newmac の IPv6 の番地へ切り替えた
;;;   時刻と 1 秒以内で重なった。21:29:46〜57 の回を atlas の tailscale0 で記録すると、newmac の SYN は届き、atlas は
;;;   SYN-ACK をすぐ返しているのに newmac に届かず、newmac は 6〜13 秒 SYN を送り直し続けた(同じ時間の LAN 経由の
;;;   接続は失われなかった)。途絶は数秒〜十数秒で、tailscaled が IPv4 へ戻すと直る。tailnet の経路の揺れは worker の外。
;;; - worker の側の弱さは 2 つ: coordinator が要求ごとに接続を閉じていた(HTTP/1.0)ので client は要求のたびに
;;;   TCP の接続を張り直し(newmac 1 台で毎秒 3 本前後)、途絶の間の新しい接続は必ず失敗した。そして共有の保存の読みは
;;;   1 回の失敗で例外を業務の Program へ投げ、service の process ごと落ちた。
;;; - 直し: 接続を使い回す(coordinator の側を HTTP/1.1 にした — `coordinator.hy`)。接続の段の上限を短くし、その段の
;;;   失敗だけは書きでも送り直す(要求がまだ相手に届いていない。httpx の transport の retries は ConnectError と
;;;   ConnectTimeout だけを送り直す)。何度送っても同じ意味の読みは、切れ方を問わず期限まで送り直す(`send-idempotent`)。
;;;   自己停止(20 秒)と移し替え(45 秒)の時間は cluster_model の ClusterTiming。
(import time)
(import httpx)
(import .cluster_model [ClusterTiming])

;; 返事を待つ上限(秒)。coordinator は書きを永続化してから返事をする(group commit)ので、返事は fsync の時間だけ遅れる。longhorn の
;; volume の実測(2026-09-24): fsync p50 0.1 秒、ただし 30 分に 1 回ほど 10.4 秒の詰まり(その間の返事は最長 13 秒)。上限はそれより
;; 長く、worker の自己停止(20 秒)より短くする。heartbeat・共有の保存・task・readiness の client は全部この値を使う。
(setv REPLY-SECONDS 15.0)

;; 接続の段の上限(秒)。tailnet の上の往復は数 ms なので、2 秒待って届かない SYN は待つより送り直す方が早い。
(setv CONNECT-SECONDS 2.0)

;; 何度でも送ってよい要求(読み)を、途中で切れても送り直す時間の上限(秒)。worker の自己停止(fence — cluster_model の
;; ClusterTiming・20 秒)より 5 秒長くする: fence より短い途絶は service も worker も越え、それより長い途絶では worker の方が
;; job を止める(読みを先に諦めて service が自分で落ちることはない)。
(setv IDEMPOTENT-DEADLINE-SECONDS (+ (/ (. (ClusterTiming) fence-ms) 1000) 5.0))


(import sys)

;; 次の宛先へ回るのは接続できない時だけ(接続の時間切れ・拒否 = 要求はまだ相手に届いていない)。
(setv CONNECT-FAILURES #(httpx.ConnectError httpx.ConnectTimeout))
;; 先の宛先(LAN)から後ろ(tailnet)へ回った後、先の宛先を試し直すまでの秒。
(setv PREFERRED-RECHECK-SECONDS 60.0)


(defn #^ str default-actor []
  "送り手の既定: worker の子 process なら「job 名@worker 名/pid」、それ以外は「pid@機体」。ASCII に限る(HTTP の header)。"
  (import os socket)
  (setv job (os.environ.get "DOEFF_WORKER_JOB") worker (os.environ.get "DOEFF_WORKER_NAME"))
  (setv actor (if (and job worker)
                  (.format "{}@{}/{}" job worker (os.getpid))
                  (.format "{}@{}" (os.getpid) (socket.gethostname))))
  (.decode (.encode actor "ascii" "replace") "ascii"))


(defn #^ tuple parse-urls [#^ str spec]
  "宛先の指定 = URL を `,` で並べた文字列。前ほど優先(Mac なら LAN の宛先・tailnet の宛先の順)。"
  (setv urls (tuple (gfor u (.split spec ",") :if (.strip u) (.rstrip (.strip u) "/"))))
  (when (not urls) (raise (ValueError f"coordinator の宛先が無い: {spec !r}")))
  urls)


(defclass CoordinatorEndpoint []
  "coordinator へ話す口(heartbeat・共有の保存・task の 3 つが使う)。宛先を複数持ち、前から順に試す。
   - 接続は使い回す(httpx の pool は宛先ごと)。
   - 接続できない時だけ次の宛先へ回る。読みや返事の途中の失敗では回らない(宛先の問題と限らない)。
   - 回った後も PREFERRED-RECHECK-SECONDS ごとに先頭の宛先を先に試し、届けば戻る。
   - 全部の宛先に届かなければ connect-retries 回まで間を置いて一巡し直し、最後の接続の失敗を投げる。
   heartbeat の連続性(自己停止の数え方)は使い手(CoordinatorLink)が宛先と無関係に持つので、宛先を替えても壊れない。"
  (defn __init__ [self #^ str spec #^ float timeout #^ int connect-retries
                  [transport None] [clock time.monotonic] [pause (fn [seconds] (time.sleep seconds))]
                  [recheck-seconds PREFERRED-RECHECK-SECONDS] [actor None]]
    ;; actor = 書きの送り手(header X-Actor)。coordinator は誰が・いつ・何を書いたかを出来事の記録に残す。
    (setv self.urls (parse-urls spec) self.active 0 self.switched-at (clock)
          self.connect-retries connect-retries self.clock clock self.pause pause self.recheck-seconds recheck-seconds
          self.actor (or actor (default-actor))
          self.client (httpx.Client :timeout (httpx.Timeout timeout :connect (min timeout CONNECT-SECONDS))
                                    :transport (or transport (httpx.HTTPTransport :retries 0))
                                    :headers {"X-Actor" self.actor}
                                    :trust-env False)))

  (defn [property] #^ str url [self]
    "いま使っている宛先。"
    (get self.urls self.active))

  (defn #^ list order [self]
    "試す順。先頭以外にいる間は、最後に先頭を試した時(回った時・試し直した時)から recheck-seconds ごとに先頭から試す。"
    (setv rest (lfor i (range (len self.urls)) :if (!= i self.active) i))
    (if (and (!= self.active 0) (>= (- (self.clock) self.switched-at) self.recheck-seconds))
        (do (setv self.switched-at (self.clock))
            (list (range (len self.urls))))
        (+ [self.active] rest)))

  (defn use [self #^ int index]
    (when (!= index self.active)
      (print (.format "coordinator の宛先を {} から {} へ切り替えました" (get self.urls self.active) (get self.urls index))
             :file sys.stderr :flush True)
      (setv self.active index self.switched-at (self.clock))))

  (defn #^ httpx.Response request [self #^ str method #^ str path #** kwargs]
    (setv last None)
    (for [round (range (+ self.connect-retries 1))]
      (when (> round 0) (self.pause (* 0.25 (** 2 (- round 1)))))
      (for [index (.order self)]
        (try
          (setv response (.request self.client method (+ (get self.urls index) path) #** kwargs))
          (.use self index)
          (return response)
          (except [error CONNECT-FAILURES]
            (setv last error)))))
    (raise last)))


(defn #^ httpx.Response send-idempotent [send [deadline-seconds IDEMPOTENT-DEADLINE-SECONDS] [pause-seconds 0.5]]
  "何度送っても同じ意味の要求(GET)を、通信の失敗(接続・読み・切断)なら期限まで送り直す。
   書きの要求には使わない: 返事を読む前に切れた書きは、相手に届いたかどうかが分からない。書きの送り直しは
   CoordinatorEndpoint の接続の段(要求がまだ届いていない段)だけに限る。"
  (setv started (time.monotonic))
  (while True
    (try
      (return (send))
      (except [httpx.TransportError]
        (when (> (+ (- (time.monotonic) started) pause-seconds) deadline-seconds)
          (raise))
        (time.sleep pause-seconds)))))
