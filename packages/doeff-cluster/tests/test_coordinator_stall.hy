;; coordinator が process の中で止まった時の判断の失敗ケース(card ki-d033e5f969cf — 2026-10-13 02:22〜02:24 JST に atlas が 3 分止まり、
;; 起きた coordinator が最初の歩で全 worker を lease 切れに数え、配る担当へ消えと戻りが 33 回届いた・親 card ki-b4d94c618094)。
;;   1 予定の外の長さ(前の歩の長さ + 予定の起きる刻からの寝過ごし)が lease-ms を越えた時だけ止まりと数え、前の歩の判断の刻から今まで
;;     を止まりとする(寝過ごし・保存の詰まりのどちらでも)。
;;   2 予定どおりの眠り(worker の期限の刻に起きる)は止まりに数えない — 前の歩から lease を越えて起きても、期限ちょうどに起きた歩は 0
;;     (数えると、期限の刻に起きるたびに期限をずらし、本当に黙った worker を沈黙と判じない)。
;;   3 起動の最初の歩(刻み無し)と、期限なしの待ちの長い眠りは止まりでない。
;;   4 止まりの長さだけ時計をずらすと、止まる前に生きていた worker は止まりの直後の歩で沈黙にならず(WorkerGone 無し)、その後
;;     heartbeat が無ければ、止まる前の沈黙の残りの後に従来どおり WorkerGone になる。
;;   5 起き直し(card ki-439a73dc075c — rev 47 2026-10-17 15:25 の配備で全 worker が WorkerGone になり、Service に載った後に WorkerBack
;;     で戻った): 読み直しから Pod が Service に載るまで(readinessProbe の周期 — lease と同じ 10 秒)worker の heartbeat は届かない。その
;;     間も経過に数えず、Service に載った後の歩で WorkerGone を出さない。heartbeat が待ちの上限(REACH-WAIT-MS)まで 1 つも来なければ、
;;     その後は従来どおり数える。
(require doeff-hy.macros [defk deftest <- val])
(import doeff_hy.table [TableWrite table-of])
(import doeff [run])
(import dataclasses [fields replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState BoardRow Drain WarmEntry SupersededRun])
(import doeff_cluster.coordinator.intent.due_model [StepPace Stall])
(import doeff_cluster.coordinator.intent.worker_notices [WorkerGone])
(import doeff_cluster.coordinator.core.api_policy [tick stall-of clocks-shifted resume-after-downtime unreached-shifted REACH-WAIT-MS
                                                   STALL-SHIFTED-FIELDS STALL-UNSHIFTED-FIELDS])
(import doeff_cluster.coordinator.core.cluster_policy [liveness-moves])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])

(val T (ClusterTiming))
(val V {"python" "3.14.0" "doeff" "1"})
;; worker w が最後に heartbeat を送った刻と、止まる前の最後の歩の判断の刻(その時の w の沈黙は 2 秒)。
(val SEEN 1000)
(val JUDGED (+ SEEN 2000))
;; 止まっていた長さ(2026-10-13 の atlas と同じ 3 分)。
(val STALL-MS 180000)


(defk named []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w(起動の印 b1)が刻 SEEN に名乗っただけの状態(置き先も task も無い)を、検の出発点として組むため。"
  (val body {"name" "w" "boot" "b1" "provides" ["net"] "capacity" 10 "taskReserve" 0 "versions" V "statuses" []})
  (get (responded (ClusterState) (! (http-request "POST" "/heartbeat" {} body)) SEEN T) 0))


(deftest test-a-wake-late-past-the-lease-is-a-stall
  ;; 期限の刻 5000 に起きる予定の待ちが 3 分寝過ごした: 前の歩の判断の刻 1000 から今までが止まり(内訳は寝過ごし)。
  (val pace (StepPace :judged-ms 1000 :waited-ms 1010 :planned-ms 5000))
  (assert (= (! (stall-of pace (+ 5000 STALL-MS) T)) (Stall :busy-ms 10 :overslept-ms STALL-MS :gap-ms (+ 4000 STALL-MS)))))


(deftest test-a-step-held-past-the-lease-is-a-stall
  ;; 保存(fsync)が 15 秒詰まった歩の後、すぐ起きた歩(予定は今すぐ): 前の歩の長さが lease を越えたので止まり(内訳は前の歩の長さ)。
  (val pace (StepPace :judged-ms 1000 :waited-ms 16000 :planned-ms 16000))
  (assert (= (! (stall-of pace 16005 T)) (Stall :busy-ms 15000 :overslept-ms 5 :gap-ms 15005))))


(deftest test-a-wake-on-the-planned-deadline-is-not-a-stall
  ;; worker の期限の刻の次の ms に起きる予定どおりに起きた歩: 前の歩の判断の刻から lease + 2 ms 経っても止まりではない。
  (val pace (StepPace :judged-ms 1000 :waited-ms 1003 :planned-ms (+ 1000 T.lease-ms 1)))
  (assert (is (! (stall-of pace (+ 1000 T.lease-ms 2) T)) None)))


(deftest test-the-stall-threshold-is-the-lease
  ;; 予定の外の長さが lease-ms ちょうどなら止まりでなく、1 ms 越えたら止まり。
  (val pace (StepPace :judged-ms 1000 :waited-ms 1000 :planned-ms 1000))
  (assert (is (! (stall-of pace (+ 1000 T.lease-ms) T)) None) "lease ちょうどは止まりでない")
  (val over (! (stall-of pace (+ 1000 T.lease-ms 1) T)))
  (assert (is-not over None) "lease を越えたら止まり")
  (assert (= over.gap-ms (+ T.lease-ms 1)) over))


(deftest test-the-first-step-and-an-open-wait-are-not-stalls
  ;; 起動の最初の歩(止まっていた長さは読み直しが数える)と、期限なしの待ち(要求でだけ起きる — 寝過ごしは無い)。
  (assert (is (! (stall-of None 999999 T)) None) "刻みの無い最初の歩")
  (val pace (StepPace :judged-ms 1000 :waited-ms 1010 :planned-ms None))
  (assert (is (! (stall-of pace 3600000 T)) None) "期限なしの待ちの 1 時間の眠り"))


(deftest test-a-live-worker-outlasts-the-stall-and-a-silent-one-is-told-gone-after-the-grace
  ;; w は刻 SEEN に名乗り、刻 JUDGED の歩の後に coordinator が 3 分止まった(予定の起きる刻は w の期限の次の ms)。
  (val before (! (tick (! (named)) JUDGED T)))
  (val now (+ JUDGED STALL-MS))
  (val stall (! (stall-of (StepPace :judged-ms JUDGED :waited-ms (+ JUDGED 5) :planned-ms (+ SEEN T.lease-ms 1)) now T)))
  (assert (= stall.gap-ms STALL-MS) "前の歩の判断の刻から今までが止まり")
  (assert (= (len (! (liveness-moves before (! (tick before now T)) T))) 1)
          "ずらさなければ止まりの直後の歩で w を沈黙に数える(直す前の形)")
  (val first (! (tick (! (clocks-shifted before now stall.gap-ms)) now T)))
  (assert (= (! (liveness-moves before first T)) #()) "止まりの直後の歩で WorkerGone を出さない")
  ;; その後 heartbeat が無ければ、止まる前の沈黙(JUDGED − SEEN)の残りの後に従来どおり WorkerGone。
  (val deadline (+ now (- T.lease-ms (- JUDGED SEEN))))
  (val at-deadline (! (tick first deadline T)))
  (val past (! (tick at-deadline (+ deadline 1) T)))
  (assert (= (! (liveness-moves first at-deadline T)) #()) "猶予の期限の刻ちょうどまでは生きている")
  (assert (= (! (liveness-moves at-deadline past T)) #((WorkerGone :worker "w" :boot "b1" :deadline-ms deadline)))
          "猶予が明けたら WorkerGone を 1 つ"))


;; --- 起き直しの後、Service に載るまで(card ki-439a73dc075c)---------------------------------------------------------------
;; 刻 JUDGED の印の後に coordinator が止まり、刻 BOOT に読み直した。受付は BOOT に始まるが、Pod が Service に載る刻 READY まで worker の
;; heartbeat は届かない(rev 47 の実測: 受付の 15:25:04 から Service の 15:25:12 まで 8 秒・戻した最後の連絡は 15:25:00〜02)。
(val BOOT (+ JUDGED 60000))
(val READY (+ BOOT T.lease-ms 2000))


(defk restarted []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "w が刻 SEEN に名乗り、刻 JUDGED に生きていた印を付けて止まった coordinator を、刻 BOOT に読み直した状態を組むため(読み直しが
   状態を読んだ刻 started-ms は durable_kv.state-from-kv と同じく BOOT)。"
  (val stopped (replace (! (tick (! (named)) JUDGED T)) :alive-ms JUDGED :started-ms BOOT))
  (get (resume-after-downtime stopped BOOT) 0))


(deftest test-the-wait-for-the-service-after-a-restart-tells-no-worker-gone
  (val resumed (! (restarted)))
  (assert (= resumed.unreached-ms BOOT) "読み直しが届かない間の起点を置く")
  (assert (= (. (.row resumed.workers "w") last-seen-ms) (- BOOT (- JUDGED SEEN))) "止まっていた長さは読み直しがずらす")
  (assert (= (len (! (liveness-moves resumed (! (tick resumed READY T)) T))) 1)
          "Service に載るまでを沈黙に数えれば w を WorkerGone にする(直す前の形)")
  (val reached (! (unreached-shifted resumed READY)))
  (assert (= reached.unreached-ms READY) "まだ heartbeat を受けていない — 起点を今へ進める")
  (val first (! (tick reached READY T)))
  (assert (= (! (liveness-moves resumed first T)) #()) "Service に載った歩で WorkerGone を出さない")
  (assert (= (. (.row first.workers "w") last-seen-ms) (- READY (- JUDGED SEEN))) "止まる前の沈黙のまま"))


(deftest test-the-wait-for-the-service-is-counted-again-after-the-limit
  ;; heartbeat が 1 つも来ないまま待ちの上限を越えた: 上限の刻までをずらし、以後は従来どおり数える(止まる前の沈黙の残りの後に WorkerGone)。
  (val resumed (! (restarted)))
  (val limit (+ BOOT REACH-WAIT-MS))
  (val midway (! (unreached-shifted resumed (- limit 5000))))
  (val past (! (unreached-shifted midway (+ limit 5000))))
  (assert (is past.unreached-ms None) "上限を越えたら起点を外す")
  (val deadline (+ limit (- T.lease-ms (- JUDGED SEEN))))
  (assert (= (. (.row past.workers "w") last-seen-ms) (- deadline T.lease-ms)) "ずらすのは上限の刻まで")
  (assert (is (! (unreached-shifted past (+ deadline 1))) past) "外した後はずらさない")
  (val at-deadline (! (tick past deadline T)))
  (assert (= (! (liveness-moves past at-deadline T)) #()) "沈黙の残りの期限の刻ちょうどまでは生きている")
  (assert (= (! (liveness-moves at-deadline (! (tick at-deadline (+ deadline 1) T)) T))
             #((WorkerGone :worker "w" :boot "b1" :deadline-ms deadline)))
          "上限の後は従来どおり WorkerGone を 1 つ"))


;; 盲検 A(card ki-d033e5f969cf の設計検証): 止まりの間にずらす欄の一覧が api_policy.clocks-shifted に在るので、期限を持つ欄を足す人が
;; 一覧を忘れると、止まりの直後にその期限だけが切れる(drain・盤の TTL の行で実測した)。欄ごとに必ず分けさせ、分けた通りにずらす。

(deftest test-every-state-field-is-classified-for-the-stall
  ;; ClusterState の欄は、ずらす(STALL-SHIFTED-FIELDS)かずらさない(STALL-UNSHIFTED-FIELDS)のちょうど一方に在る。
  (val names (frozenset (gfor f (fields ClusterState) f.name)))
  (assert (= (& STALL-SHIFTED-FIELDS STALL-UNSHIFTED-FIELDS) (frozenset)) (sorted (& STALL-SHIFTED-FIELDS STALL-UNSHIFTED-FIELDS)))
  (assert (= (| STALL-SHIFTED-FIELDS STALL-UNSHIFTED-FIELDS) names)
          (.format "分けていない欄 {}・無い欄 {}" (sorted (- names STALL-SHIFTED-FIELDS STALL-UNSHIFTED-FIELDS))
                   (sorted (- (| STALL-SHIFTED-FIELDS STALL-UNSHIFTED-FIELDS) names)))))


(deftest test-a-stall-keeps-the-renewable-deadlines
  ;; w は刻 SEEN に名乗り、drain・温める表の行・盤の TTL の行の期限は SEEN + 60 秒、退いた世代の最後の報告は SEEN。刻 JUDGED の歩の後に
  ;; 3 分止まった。ずらした欄はずらすと言った欄だけで、止まりの直後の歩でどの期限も切れない(直す前は drain と盤の行が消えた)。
  (val base (! (tick (! (named)) JUDGED T)))
  (val before (replace base
                       :drains (table-of #((TableWrite "w" (Drain :worker "w" :since-ms SEEN :until-ms (+ SEEN 60000)))))
                       :warms (table-of #((TableWrite "k" (WarmEntry :key "k" :runtime-env {} :needs #() :until-ms (+ SEEN 60000) :holder "h"))))
                       :board (table-of #((TableWrite "presence-x" (BoardRow :value 1 :version 1 :expires-ms (+ SEEN 60000) :size 1))))
                       :superseded-runs #((SupersededRun :worker "w" :boot "b0" :jobs #("j") :seen-ms SEEN :overlapped False :leaving False))))
  (val now (+ JUDGED STALL-MS))
  (val shifted (! (clocks-shifted before now STALL-MS)))
  (val moved (frozenset (gfor f (fields ClusterState) :if (!= (getattr before f.name) (getattr shifted f.name)) f.name)))
  (assert (<= moved STALL-SHIFTED-FIELDS) (sorted (- moved STALL-SHIFTED-FIELDS)))
  (assert (<= #{"workers" "board" "drains" "warms" "superseded_runs" "alive_ms"} moved) (sorted moved))
  (assert (= (. (.row shifted.drains "w") until-ms) (+ SEEN 60000 STALL-MS)))
  (assert (= (. (.row shifted.board "presence-x") version) 1) "TTL の行は値が同じなので版を進めない")
  (val after (! (tick shifted now T)))
  (assert (is-not (.row after.drains "w") None) "止まりの直後の歩で drain が切れない")
  (assert (is-not (.row after.warms "k") None) "止まりの直後の歩で温める表の行が切れない")
  (assert (is-not (.row after.board "presence-x") None) "止まりの直後の歩で盤の TTL の行が切れない")
  (assert (= (. (get after.superseded-runs 0) seen-ms) (+ SEEN STALL-MS)) "退いた世代の沈黙は止まる前の時点のまま"))
