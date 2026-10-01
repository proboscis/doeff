;;; worker が heartbeat をいつ送るか(純粋な判断・I/O はしない — #1933)。本番の coordinator への口(worker/protocol/coordinator_link)と手元の sim の宿
;;; (local.hy)が同じ関数を使う。
;;;
;;; worker の拍(WorkerPolicy.tick-seconds・0.5 秒)ごとに ReadDesired が来るが、heartbeat を送るのは次のどれかの時だけ:
;;;   - 待ちの口を使えていない(返事に版が無い旧い coordinator・GET /watch が 404・待ちがまだ 1 度も答えていない)— 今までどおり毎拍
;;;   - 前の heartbeat が届いていない(届かない間は毎拍送り直す — 途絶と fence の数え方は今までどおり)
;;;   - 名指しの待ち(GET /watch?worker=<名>)が「変わった」と答えた(desired の変化を待ちで受ける)
;;;   - 状態の報告が前に送った物から変わった(process の起動・終わり・task の結果を遅らせない)
;;;   - 前の heartbeat から間隔が過ぎた。間隔 = 生存の窓(timing の lease_ms)の 1/4 と、自分の切り離した task の最短の lease の 1/3 の
;;;     小さい方 — coordinator の生存の判断(lease-ms より新しい heartbeat)と task の lease を、届く heartbeat が必ず延ばす。
;;; それ以外の拍は、前の heartbeat の返事の desired を使い続ける。lease と fence の判断(coordinator の lease-ms・reassign-after-ms、
;;; worker の fence-ms)は変えない — どれも最後に届いた heartbeat から数える。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(require doeff-hy.record [defenum defrecord])
(import enum [StrEnum])
(import dataclasses [dataclass])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.protocol [WATCH-MAX-SECONDS])

(val BEAT-LEASE-DIVISOR 4)          ; 生存の窓を何等分した間隔で送るか(10 秒 → 2.5 秒)
(val BEAT-TASK-LEASE-DIVISOR 3)     ; 切り離した task の lease を何等分した間隔で送るか
(val DEFAULT-LEASE-MS (. (ClusterTiming) lease-ms))  ; 返事に timing の無い旧い coordinator の生存の窓
(val WATCH-RETRY-SECONDS 1.0)       ; 届かない・断られた待ちを送り直すまでの間
(val WAKE-HOLD-SECONDS 5.0)         ; 待ちが「変わった」と答えた後、heartbeat が版を進めるのを待つ上限(同じ版で待ち直して空回りしない)

(defenum WatchKind CHANGED UNCHANGED UNSUPPORTED FAILED)
;; 待ち 1 回の答えの種類。CHANGED = 名指しの worker の見え方が変わった・UNCHANGED = 期限まで変わらなかった(revision = 次の after)・
;; UNSUPPORTED = 待つ口の無い coordinator(404)・FAILED = 届かない・断られた・読めない(detail = 理由)。


(defrecord WatchReading
  "GET /watch の返事 1 つの読み(watch-reading)。"
  (#^ WatchKind kind)
  (setv #^ (| int None) revision None)
  (setv #^ str detail ""))


(deff beat-interval-ms [#^ (| dict None) timing #^ dict task-echo]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ判断を使う
  {:pre [(: timing (| dict None)) (: task-echo dict)] :post [(: % int)] :tags {:context "doeff-cluster" :role "judgment"}}
  "heartbeat の返事の timing(lease_ms)と、この worker に置かれた切り離した task の返事の行(leaseMs)から、heartbeat を送る間隔を決める
   ため — coordinator が生存を数える窓と task の lease の中に、届く heartbeat が何度か入る長さ。"
  (let [lease (.get (or timing {}) "lease_ms")
        window (if (isinstance lease int) lease DEFAULT-LEASE-MS)
        task-leases (lfor row (.values task-echo) :setv ms (.get row "leaseMs") :if (isinstance ms int) ms)]
    (max 1 (min (+ [(// window BEAT-LEASE-DIVISOR)] (lfor ms task-leases (// ms BEAT-TASK-LEASE-DIVISOR)))))))


(deff heartbeat-due [#^ bool watching #^ bool fresh #^ bool woken #^ bool statuses-changed #^ int silent-ms #^ int interval-ms]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ判断を使う
  {:pre [(: watching bool) (: fresh bool) (: woken bool) (: statuses-changed bool) (: silent-ms int) (: interval-ms int)]
   :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "この拍で heartbeat を送るかを決めるため。watching = 待ちの口を使えている・fresh = 前の heartbeat が届いた・woken = 待ちが「変わった」
   と答えた・statuses-changed = 状態の報告が前に送った物と違う・silent-ms = 最後に届いた heartbeat からの時間。"
  (or (not watching) (not fresh) woken statuses-changed (>= silent-ms interval-ms)))


(deff watch-params [#^ int after #^ str worker #^ str boot #^ bool confirmed]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ問いを作る
  {:pre [(: after int) (: worker str) (: boot str) (: confirmed bool)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "名指しの待ちの問い(GET /watch の query)を作るため。まだ口を確かめていない最初の待ちは 0 秒(すぐ答える — 待つ口の有無を確かめ、
   確かめるまで毎拍の heartbeat を続ける)、その後は上限まで待つ。"
  {"after" (str after) "timeoutSeconds" (str (if confirmed WATCH-MAX-SECONDS 0.0)) "worker" worker "boot" boot})


(deff watch-reading [#^ (| int None) status #^ (| dict list str int float bool None) body]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ読みを使う
  {:pre [(: status (| int None)) (: body (| dict list str int float bool None))] :post [(: % WatchReading)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "GET /watch の返事(status = None は届かない)を待ちの答えの種類に読むため。"
  (cond
    (is status None) (WatchReading :kind WatchKind.FAILED :detail (str body))
    (= status 404) (WatchReading :kind WatchKind.UNSUPPORTED)
    (and (= status 200) (isinstance body dict) (isinstance (.get body "revision") int) (isinstance (.get body "changed") bool))
      (WatchReading :kind (if (get body "changed") WatchKind.CHANGED WatchKind.UNCHANGED) :revision (get body "revision"))
    True (WatchReading :kind WatchKind.FAILED :detail (.format "{}: {}" status (cut (str body) 0 200)))))


(deff reply-revision [#^ dict reply]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ読みを使う
  {:pre [(: reply dict)] :post [(: % (| int None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "heartbeat の返事の版(次の待ちの after)を読むため。欄の無い返事は待つ口の無い旧い coordinator の物(None)。"
  (let [revision (.get reply "revision")]
    (if (and (isinstance revision int) (not (isinstance revision bool))) revision None)))
