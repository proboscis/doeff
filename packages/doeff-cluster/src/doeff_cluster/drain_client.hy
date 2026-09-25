;;; worker の Pod の側から coordinator に drain を頼み、空くのを待つ Program(2026-09-25)。I/O は effect(CoordinatorCall)と
;;; doeff-time(GetMonotonic・Delay)だけ。handler と入口は drain_main.hy(composition root)。
;;;
;;; 使い手は 2 つ(worker の DaemonSet の manifest):
;;; - preStop(await-drained): drain を頼み直しながら(期限を延ばす)、自分の上の job が全部他へ移る(drained)か、上限の時間が
;;;   過ぎるまで待つ。どちらでも終わる(preStop の失敗は Pod の停止を止めない — 上限で諦めても SIGTERM の後の今の振る舞い
;;;   = 子を止めて lease を返す、に落ちるだけ)。
;;; - readinessProbe(worker-ready): 自分が coordinator から見て生きていて drain 中でないか(新しい Pod が heartbeat を送り始め、
;;;   前の Pod の drain が解けた後にだけ Ready — DaemonSet は Ready を待って次の node の Pod を入れ替える)。
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_time [Delay GetMonotonic])

;; preStop の既定(秒)。上限は cluster.yaml の terminationGracePeriodSeconds より、worker の子の停止(stop-grace 10 秒 + KILL の
;; 猶予 5 秒)の分以上短くする。drain の期限は上限より長く取り、諦めた後もしばらく空けたままにする(次の世代の heartbeat で解ける)。
(setv DRAIN-DEADLINE-SECONDS 90.0)
(setv DRAIN-INTERVAL-SECONDS 2.0)
(setv DRAIN-TTL-MARGIN-SECONDS 60.0)


(defclass [(dataclass :frozen True)] CoordinatorCall [EffectBase]
  "coordinator へ要求を 1 つ送る。結果 = {\"status\" int \"body\" dict}、届かなければ {\"error\" 理由の文}。"
  (#^ str method)
  (#^ str path)
  (setv #^ (| dict None) body None))


(defn #^ str worker-path [#^ str name]
  "worker の名は node の名(k8s の DNS の名 — 英小文字・数字・- と .)なので、そのまま path に置ける。"
  (+ "/workers/" name))


(defn #^ (| str None) drain-outcome [#^ dict answer #^ float elapsed #^ float deadline]
  "純粋: 頼んだ答えと経過の秒から、待つのを終えるか。終えるなら結末の名(drained | unknown-worker | refused | timeout)、
   待ち続けるなら None。届かない・coordinator が 5xx の間は上限まで頼み直す(coordinator の作り直しの間も待つ)。"
  (setv status (.get answer "status") body (or (.get answer "body") {}))
  (cond
    (and (= status 200) (.get (or (.get body "drain") {}) "drained")) "drained"
    ;; coordinator が知らない worker(1 度も heartbeat が届いていない)には移す物が無い。
    (= status 404) "unknown-worker"
    ;; 形の誤り(400 等)は頼み直しても変わらない。
    (and (is-not status None) (<= 400 status 499)) "refused"
    (>= elapsed deadline) "timeout"
    True None))


(defk await-drained [name deadline interval]
  {:pre [(: name str) (: deadline float) (: interval float)] :post [(: % dict)]}
  ;; 結果 = {"outcome" 結末 "elapsed" 秒 "last" 最後の答え}。
  (<- started float (GetMonotonic))
  (setv ttl (+ deadline DRAIN-TTL-MARGIN-SECONDS))
  (while True
    (<- answer dict (CoordinatorCall "POST" (+ (worker-path name) "/drain") {"ttlSeconds" ttl}))
    (<- at float (GetMonotonic))
    (setv outcome (drain-outcome answer (- at started) deadline))
    (when (is-not outcome None)
      (return {"outcome" outcome "elapsed" (round (- at started) 1) "last" answer}))
    (<- (Delay interval))))


(defn #^ bool ready-of [#^ dict answer #^ (| str None) own-boot]
  "純粋: GET /workers/<名> の答え → この Pod の worker が Ready か。届かない・知らない worker は Ready でない。
   own-boot = この Pod の worker の process の世代(worker が起動の時に Pod の中の file へ書く)。coordinator の見る世代がそれと違えば
   Ready でない — worker の名は node の名なので、同じ node の前の Pod(preStop で drain 中)の heartbeat と見分けがつかない
   (2026-09-25 の配備の実弾: 新しい Pod の readinessProbe が、前の Pod が drain を頼む前の数秒に前の Pod の生存を読んで Ready と答え、
   DaemonSet がもう 1 台の Pod の入れ替えへ進み、移す先が無くなって書き手が約 11 秒止まった)。"
  (setv body (or (.get answer "body") {}))
  (and (= (.get answer "status") 200) (is (.get body "ready") True)
       (bool own-boot) (= (.get body "boot") own-boot)))


(defk worker-ready [name own-boot]
  {:pre [(: name str) (: own-boot (| str None))] :post [(: % bool)]}
  (<- answer dict (CoordinatorCall "GET" (worker-path name)))
  (ready-of answer own-boot))
