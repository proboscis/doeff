;; coordinator の機体ごとの止まりを、模擬の世界(sim-cluster — 本物の coordinator と本物の worker を仮想の時計で回す)で受ける側から
;; 確かめる失敗ケース(card ki-d033e5f969cf・親 card ki-b4d94c618094)。2026-10-13 02:22〜02:24 JST に atlas が 3 分止まり(containerd と
;; kubelet が答えず、host の記録にも空白)、起きた coordinator が最初の歩で全 worker を lease 切れに数え、配る担当へ消えと戻りが 33 回
;; 届いた。筋書きの Program が coordinator と同じ知らせの broker を読む受け手になり、coordinator を StallCoordinator で 3 分止める。
;;   1 誰も死なない止まり: 止まりの間と明けた後に WorkerGone が 0 回(終わりの印として、明けた後に w1 の処理を止めた時の WorkerGone・
;;     WorkerBack だけを受ける)・service の置き先は同じ worker の同じ世代のまま(移し替えの期限 60 秒を越えて止まっても移さない)。
;;   2 止まりの間に死んだ worker: 明けた直後の歩では WorkerGone を出さず、止まる前の沈黙の残り(lease の内)の後に、死んだ w2 だけの
;;     WorkerGone が 1 度届く。生きている w1 は生きたまま。
;; 直す前は 1・2 とも、明けた最初の歩で w1・w2 の WorkerGone が出る。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [EffectBase Program with-handlers])
(import doeff_time [Delay])
(import doeff_events [EventBus MemoryBroker WaitForEvent memory-notice-handler notice-events-handler subscribed-event-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.worker_notices [WorkerBack WorkerGone])
(import doeff_cluster.coordinator.protocol.worker_notices [WORKER-NOTICE-READS])
(import doeff_cluster.shared.intent.cluster_control [KillWorker])
(import doeff_cluster.sim.local [sim-cluster SimParts PartsOf SimWorker StallWorker StallCoordinator ReadCoordinator CoordinatorRuns CoordinatorSteps CutWorker])
(import doeff_cluster.shared.intent.cluster_control [StopCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses])

(val T (ClusterTiming))
(val WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)
               (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :task-reserve 0)))
;; coordinator を止める長さ(2026-10-13 の atlas と同じ 3 分 — 移し替えの期限 reassign-after-ms の 60 秒も越える)。
(val STALL-SECONDS 180.0)
;; 明けた後に見る長さ: 移し替えの期限を越える(誤って沈黙と数えていれば job が移る)。
(val AFTER-SECONDS (+ (/ T.reassign-after-ms 1000) 30.0))
;; 終わりの印に w1 の処理を止める長さ: 生死の期限を越え、移し替えの期限には届かない。
(val MARK-STALL-SECONDS (+ (/ T.lease-ms 1000) 10.0))
;; 止まりの間に w2 を殺す刻(止まりの始めから)。
(val KILL-AFTER-SECONDS 60.0)
(val READER "stall-reader")


(defk as-reader [broker body]
  {:pre [(: broker MemoryBroker) (: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "program"}}
  "body を、coordinator と同じ broker の worker の生死の出来事の受け手として走らせるため(購読は body の最初の effect より前に成る)。"
  (<- answer (with-handlers [(subscribed-event-handler (EventBus) READER #(WorkerGone WorkerBack))
                             (memory-notice-handler broker)
                             (notice-events-handler READER WORKER-NOTICE-READS 3600.0)]
               body))
  answer)


(defk heard-until [last]
  {:pre [(: last type)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受けた生死の出来事を、受けた刻(epoch ms)と組にして、型 last の出来事まで並べるため(購読の後に出た物は取りこぼさない)。"
  (var heard #())
  (while True
    (<- came (WaitForEvent WorkerGone WorkerBack))
    (<- at int (now-epoch-ms))
    (:= heard (+ heard #(#(came at))))
    (when (isinstance came last)
      (return heard))))


(defk pulse-placement []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の状態から、service pulse の置き先の worker と世代を読むため。"
  (<- state dict (ReadCoordinator "/state"))
  (val placed (get state "placements" "pulse"))
  #((get placed "worker") (get placed "generation")))


(defk stall-without-deaths []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受け手として: coordinator を止めて明けた後まで待ち、終わりの印に w1 の処理を止めて戻るまでに受けた出来事と、止める前後の pulse の
   置き先と、印を付けた刻と、止まりが明ける刻を返すため。"
  (<- before tuple (pulse-placement))
  (<- stalled int (now-epoch-ms))
  (<- (StallCoordinator STALL-SECONDS))
  (<- (Delay (+ STALL-SECONDS AFTER-SECONDS)))
  (<- after tuple (pulse-placement))
  (<- marked int (now-epoch-ms))
  (<- (StallWorker "w1" MARK-STALL-SECONDS))
  (<- heard tuple (heard-until WorkerBack))
  #(heard before after marked (+ stalled (int (* 1000 STALL-SECONDS)))))


(defk stalled-without-deaths []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker が名乗って pulse が置かれた後に受け手に成り、誰も死なない止まりの筋書きを回し、coordinator の Pod の一生の数も添えて返すため。"
  (<- (Delay 5.0))
  (<- parts SimParts (PartsOf))
  (<- seen tuple (as-reader parts.broker (stall-without-deaths)))
  (<- runs tuple (CoordinatorRuns))
  (<- steps tuple (CoordinatorSteps))
  #(#* seen (len runs) steps))


(deftest test-a-stalled-coordinator-tells-no-worker-gone-and-moves-no-job
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (stalled-without-deaths) :timing T :workers WORKERS))
  (val heard (get seen 0))
  (val before (get seen 1))
  (val after (get seen 2))
  (val marked (get seen 3))
  (val thawed (get seen 4))
  (assert (= (get seen 5) 1) "止まりは coordinator を作り直さない(同じ一生のまま)")
  ;; 受けたのは終わりの印の WorkerGone・WorkerBack だけ(止まりの間と明けた後の WorkerGone は 0 回)。
  (assert (= (lfor #(event _at) heard #((type event) event.worker)) [#(WorkerGone "w1") #(WorkerBack "w1")]) heard)
  (assert (>= (get heard 0 1) marked) #("WorkerGone は印の後" heard marked))
  (assert (= after before) #("pulse の置き先は同じ worker の同じ世代のまま" before after))
  ;; 止まりが明けた歩(要求の無い歩)の保存に、ずらした worker の最後の連絡の印と、その歩の刻の生存の印が載る(設計検証 S2 — 保存は歩の
  ;; 前の状態との差なので、ずらしを差に入れ損ねると、止まりの後の印が置き場に残らない)。
  (val thaw-step (next (gfor step (get seen 6) :if (>= step.at thawed) step)))
  (val written (dfor writes thaw-step.writes w writes w.key w.value))
  (assert (= (.get (.get written "counter" {}) "aliveMs") thaw-step.at) #("明けた歩の生存の印" (sorted written)))
  (assert (<= #{"worker/w1" "worker/w2"} (set written)) #("明けた歩にずらした worker の印" (sorted written))))


(defk stall-with-a-death []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受け手として: coordinator を止め、止まりの間に w2 を殺し、明けた後の最初の WorkerGone までに受けた出来事と、止まりが明ける刻と、
   その時の w1 の生死を返すため。"
  (<- started int (now-epoch-ms))
  (<- (StallCoordinator STALL-SECONDS))
  (<- (Delay KILL-AFTER-SECONDS))
  (<- (KillWorker "w2"))
  (<- heard tuple (heard-until WorkerGone))
  (<- state dict (ReadCoordinator "/state"))
  #(heard (+ started (int (* 1000 STALL-SECONDS))) (get state "workers" "w1" "live")))


(defk stalled-with-a-death []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker が名乗った後に受け手に成り、止まりの間に worker が死ぬ筋書きを回して返すため。"
  (<- (Delay 5.0))
  (<- parts SimParts (PartsOf))
  (<- seen tuple (as-reader parts.broker (stall-with-a-death)))
  seen)


(deftest test-a-worker-that-died-in-the-stall-is-told-gone-after-the-grace
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (stalled-with-a-death) :timing T :workers WORKERS))
  (val heard (get seen 0))
  (val thawed (get seen 1))
  (val w1-live (get seen 2))
  (assert (= (lfor #(event _at) heard #((type event) event.worker)) [#(WorkerGone "w2")]) heard)
  ;; 明けた直後の歩では出さず、止まる前の沈黙の残り(lease の内)の後に出す。
  (val gone-at (get heard 0 1))
  (assert (< thawed gone-at (+ thawed T.lease-ms 1001)) #("WorkerGone は明けた後の lease の内" thawed gone-at))
  (assert (is w1-live True) "生きている w1 は生きたまま"))


;; --- 起き直しの後、Service に載るまで(card ki-439a73dc075c)---------------------------------------------------------------
;; 配備で coordinator の Pod が作り直されると、読み直して受付を始めてから Pod が Ready になり Service に載るまで(readinessProbe の周期 —
;; 最大 10 秒)worker の heartbeat は届かない(rev 47 2026-10-17 15:25 の実測: 全 worker が WorkerGone になり、Service に載った直後に
;; WorkerBack で戻った)。筋書きは両 worker の網を、止めてから作り直した coordinator の Service に載るまでの長さだけ切って、その形を作る。
;;   3 作り直しの後に Service に載るまでの間も、明けた後も WorkerGone は 0 回(起動の時の生死の 1 度の知らせの WorkerBack だけ)・pulse の
;;     置き先は同じ worker の同じ世代のまま。直す前は、作り直しから lease の内に w1・w2 の WorkerGone が出る。
;; 止めている秒(配備の Pod の入れ替えの間)と、作り直してから Service に載るまでの秒(lease の 10 秒を越える — 実測は 8 秒 + 戻した最後の
;; 連絡の 2〜4 秒前)。網を切る長さは足して 15 秒 — worker の fence(20 秒)の内なので、worker は job を止めない。
(val DOWN-SECONDS 3.0)
(val UNREACHED-SECONDS 12.0)


(defk heard-until-back-of [name after]
  {:pre [(: name str) (: after int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受けた生死の出来事を、受けた刻と組にして、刻 after より後の name の WorkerBack まで並べるため。"
  (var heard #())
  (while True
    (<- came (WaitForEvent WorkerGone WorkerBack))
    (<- at int (now-epoch-ms))
    (:= heard (+ heard #(#(came at))))
    (when (and (isinstance came WorkerBack) (= came.worker name) (> at after))
      (return heard))))


(defk restart-unreached []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受け手として: 両 worker の網を切って coordinator を止め、作り直して Service に載り、移し替えの期限を越えるまで待ち、終わりの印に
   w1 の処理を止めて戻るまでに受けた出来事と、止める前後の pulse の置き先と、印を付けた刻を返すため。"
  (<- before tuple (pulse-placement))
  (for [w WORKERS]
    (<- (CutWorker w.name (+ DOWN-SECONDS UNREACHED-SECONDS))))
  (<- (StopCoordinator DOWN-SECONDS))
  (<- (Delay (+ DOWN-SECONDS UNREACHED-SECONDS AFTER-SECONDS)))
  (<- after tuple (pulse-placement))
  (<- marked int (now-epoch-ms))
  (<- (StallWorker "w1" MARK-STALL-SECONDS))
  (<- heard tuple (heard-until-back-of "w1" marked))
  #(heard before after marked))


(defk restarted-unreached []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker が名乗って pulse が置かれた後に受け手に成り、作り直しの後に Service に載るまで届かない筋書きを回し、coordinator の Pod の
   一生の数も添えて返すため。"
  (<- (Delay 5.0))
  (<- parts SimParts (PartsOf))
  (<- seen tuple (as-reader parts.broker (restart-unreached)))
  (<- runs tuple (CoordinatorRuns))
  #(#* seen (len runs)))


(deftest test-a-restarted-coordinator-tells-no-worker-gone-before-the-service-takes-it
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (restarted-unreached) :timing T :workers WORKERS))
  (val heard (get seen 0))
  (val before (get seen 1))
  (val after (get seen 2))
  (val marked (get seen 3))
  (assert (= (get seen 4) 2) "止めて作り直した(一生 2 つ)")
  ;; 受け手は筋書きの待ちが明けてから読むので、受けた刻ではなく並びと期限で分ける: 作り直した coordinator の起動の時の生死の 1 度の
  ;; 知らせ(どちらも生きている)の後は、終わりの印の WorkerGone・WorkerBack だけ。
  (val names (lfor #(event _at) heard #((type event) event.worker)))
  (assert (= (sorted (cut names 0 2) :key str) (sorted [#(WorkerBack "w1") #(WorkerBack "w2")] :key str)) heard)
  (assert (= (cut names 2 None) [#(WorkerGone "w1") #(WorkerBack "w1")]) heard)
  (assert (> (. (get heard 2 0) deadline-ms) marked) #("WorkerGone は印の後の期限" heard marked))
  (assert (= after before) #("pulse の置き先は同じ worker の同じ世代のまま" before after)))
