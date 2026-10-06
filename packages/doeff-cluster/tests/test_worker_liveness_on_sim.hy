;; worker の生死の出来事を、模擬の世界(sim-cluster — 本物の coordinator と本物の worker を仮想の時計で回す)で受ける側から確かめる
;; 失敗ケース(#3864)。筋書きの Program が coordinator と同じ知らせの broker(SimParts.broker)を読む受け手になる。
;;   1・3 worker の処理を期限より長く止めると、期限を越えた歩で WorkerGone が 1 度、戻ると WorkerBack が 1 度(重ねて出ない)。
;;   5 静かな区間を飛ばす模擬(skip-idle 真)でも、1 歩ずつ走らせた時(偽)と同じ刻・同じ数の出来事。
;;   6 broker が止まっている間に期限が切れても coordinator は止まらず、broker が戻った後、受け手は追いつきの合図を受けて、
;;     coordinator の状態から worker が居ない事を読める(模擬の broker は全員を切るので、受け手も繋ぎ直す。受け手が繋がったままの形は
;;     doeff-events の GAP_LAWS が持つ)。
;;   8 :timing を渡さない筋書き(比で延ばした世界 — 生死の判断が起きない前提で heartbeat の間を延ばした)で worker の死の判断が出たら、
;;     筋書きを待たずに SimLivenessError で終わる(生死を試す筋書きは本番の値を :timing で明示する — 上の 1〜7 は T を渡す・#3865)。
;;   7 coordinator が起き直すと、沈黙のままの worker の WorkerGone を(同じ boot で)1 度出す(起動の時の今の状態 — その前に
;;     WorkerBack を出さない)。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [EffectBase Program with-handlers])
(import doeff_time [Delay])
(import doeff_events [EventBus MemoryBroker hold-broker release-broker SourceMissed SourceResumed SourceStarted WaitForEvent memory-notice-handler notice-events-handler
                      subscribed-event-handler cut-broker restore-broker])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.core.timing_rules [scaled-timing])
(import pytest)
(import doeff_cluster.coordinator.intent.worker_notices [WorkerBack WorkerGone])
(import doeff_cluster.coordinator.protocol.worker_notices [WORKER-NOTICE-READS])
(import doeff_cluster.sim.local [SIM-TIMING-RATIO SimLivenessError sim-cluster SimParts PartsOf SimWorker StallWorker StopCoordinator ReadCoordinator CoordinatorRuns])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses])

(val T (ClusterTiming))
(val WORKER "w1")
(val WORKERS #((SimWorker :name WORKER :provides #{"net"} :task-reserve 0)))
;; 処理を止める長さ: 生死の期限(lease-ms)を十分に越える。
(val STALL-SECONDS (+ (/ T.lease-ms 1000) 20.0))
(val READER "liveness-reader")


(defk as-reader [broker body]
  {:pre [(: broker MemoryBroker) (: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "program"}}
  "body を、coordinator と同じ broker の worker の生死の出来事の受け手として走らせるため(購読は body の最初の effect より前に成る)。"
  (<- answer (with-handlers [(subscribed-event-handler (EventBus) READER #(WorkerGone WorkerBack SourceStarted SourceMissed SourceResumed))
                             (memory-notice-handler broker)
                             (notice-events-handler READER WORKER-NOTICE-READS 3600.0)]
               body))
  answer)


(defk heard-until [last]
  {:pre [(: last type)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受けた生死の出来事を、受けた刻(epoch ms)と組にして、型 last の出来事まで並べるため。"
  (var heard #())
  (while True
    (<- came (WaitForEvent WorkerGone WorkerBack))
    (<- at int (now-epoch-ms))
    (:= heard (+ heard #(#(came at))))
    (when (isinstance came last)
      (return heard))))


(defk stalls-and-hears []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受け手として: worker の処理を STALL-SECONDS 止め、WorkerBack までに受けた出来事を返すため。"
  (<- (StallWorker WORKER STALL-SECONDS))
  (<- heard tuple (heard-until WorkerBack))
  heard)


(defk stall-and-hear []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker が名乗った後に受け手に成り、worker の処理を止めて戻るまでに受けた出来事を返すため。"
  (<- (Delay 3.0))
  (<- parts SimParts (PartsOf))
  (<- heard tuple (as-reader parts.broker (stalls-and-hears)))
  heard)


(defk gone-then-back [heard]
  {:pre [(: heard tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "受けた列が WorkerGone 1 つ・WorkerBack 1 つの順で、WorkerGone は期限を越えた歩(期限の刻の後、1 秒の内)に届き、2 つが同じ worker で
   あることを確かめるため。"
  (assert (= (lfor #(event _at) heard (type event)) [WorkerGone WorkerBack]) heard)
  (val gone (get heard 0 0))
  (val gone-at (get heard 0 1))
  (val back (get heard 1 0))
  (assert (= #(gone.worker back.worker) #(WORKER WORKER)) heard)
  (assert (< gone.deadline-ms gone-at (+ gone.deadline-ms 1001)) #(gone gone-at))
  None)


(deftest test-a-stalled-worker-is-told-gone-once-and-back-once
  (<- heard tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (stall-and-hear) :timing (ClusterTiming) :workers WORKERS))
  (<- (gone-then-back heard)))


(defk stall-and-hear-on [broker]
  {:pre [(: broker MemoryBroker)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker が名乗った後に、呼び手が作って sim-cluster に渡した broker の受け手に成り(PartsOf を読まない)、worker の処理を止めて戻るまでに
   受けた出来事を返すため。"
  (<- (Delay 3.0))
  (<- heard tuple (as-reader broker (stalls-and-hears)))
  heard)


(deftest test-a-broker-given-by-the-caller-hears-the-coordinator
  ;; 失敗ケース(#3850 — 世界の基盤を呼び手が作って渡す形): 呼び手が作った broker を :notice-broker で渡すと、coordinator は
  ;; その broker へ生死の出来事を出し、同じ broker の受け手(呼び手の世界の job の形)が WorkerGone・WorkerBack を受ける。以前は coordinator が
  ;; 走りごとに自分の broker を作ったので、外で作った broker の受け手には何も届かなかった。
  (val broker (MemoryBroker))
  (<- heard tuple (sim-cluster (pulses sim-foundation) (stall-and-hear-on broker) :timing (ClusterTiming) :workers WORKERS
                               :notice-broker broker))
  (<- (gone-then-back heard)))


(deftest test-skipping-quiet-steps-tells-the-same-events-at-the-same-times
  (<- skipping tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (stall-and-hear) :timing (ClusterTiming) :workers WORKERS :skip-idle True))
  (<- stepping tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (stall-and-hear) :timing (ClusterTiming) :workers WORKERS :skip-idle False))
  (assert (= skipping stepping) #(skipping stepping)))


(defk catches-up-after-an-outage [broker]
  {:pre [(: broker MemoryBroker)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受け手として: broker を止め、その間に worker の期限を切らせ、戻した後に受けた追いつきの合図と、coordinator の状態の worker の生死を
   返すため。"
  (<- (WaitForEvent SourceStarted))
  (<- (cut-broker broker "止めた(検)"))
  (<- (StallWorker WORKER STALL-SECONDS))
  (<- (Delay (+ (/ T.lease-ms 1000) 5.0)))
  (<- (restore-broker broker))
  (<- told (WaitForEvent SourceResumed SourceMissed))
  (<- state dict (ReadCoordinator "/state"))
  #(told (get state "workers" WORKER "live")))


(defk gone-during-an-outage []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "broker を止めている間に worker の期限を切らせ、戻した後に受け手が受けた追いつきの合図・coordinator の状態の worker の生死・
   coordinator の Pod の一生の数を返す。"
  (<- (Delay 3.0))
  (<- parts SimParts (PartsOf))
  (<- answer tuple (as-reader parts.broker (catches-up-after-an-outage parts.broker)))
  (<- runs tuple (CoordinatorRuns))
  #(#* answer (len runs)))


(deftest test-a-deadline-passed-while-the-broker-was-away-is-caught-up-after-it-returns
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (gone-during-an-outage) :timing (ClusterTiming) :workers WORKERS))
  (val told (get seen 0))
  (val alive (get seen 1))
  (val runs (get seen 2))
  (assert (isinstance told #(SourceResumed SourceMissed)) told)
  (assert (is alive False) alive)
  (assert (= runs 1) "coordinator は broker が止まっても止まらない"))


(defk hears-across-a-restart []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受け手として: worker を沈黙させて WorkerGone を受け、coordinator を起こし直し、その後の WorkerGone までを返すため。"
  (<- (StallWorker WORKER 600.0))
  (<- first tuple (heard-until WorkerGone))
  ;; 読み直しは止まっていた長さだけ worker の沈黙をずらす(api_policy.resume-after-downtime)ので、ずらした後も期限を越える長さを待つ。
  (<- (Delay 30.0))
  (<- (StopCoordinator 2.0))
  (<- again tuple (heard-until WorkerGone))
  #(first again))


(defk gone-across-a-restart []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker を沈黙させて WorkerGone を受けた後に coordinator を起こし直し、起き直しの後に受けた出来事を返すため。"
  (<- (Delay 3.0))
  (<- parts SimParts (PartsOf))
  (<- heard tuple (as-reader parts.broker (hears-across-a-restart)))
  heard)


(deftest test-a-restarted-coordinator-tells-a-still-silent-worker-gone-once
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (gone-across-a-restart) :timing (ClusterTiming) :workers WORKERS))
  (val first (get seen 0))
  (val again (get seen 1))
  (assert (= (len first) 1) first)
  (assert (= (len again) 1) again)
  (val before (get first 0 0))
  (val after (get again 0 0))
  (assert (= #(after.worker after.boot) #(before.worker before.boot)) #(before after)))


(defk answers-while-the-broker-hangs []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "知らせの broker が繋がったまま答えなく成った間に worker の期限を切らせ(WorkerGone の送りが答えを待ったまま)、その間も coordinator が
   要求に答えるかを読むため。答え = #(その間に読めた worker の生死 coordinator の Pod の一生の数)。"
  (<- (Delay 3.0))
  (<- parts SimParts (PartsOf))
  (<- (hold-broker parts.broker))
  (<- (StallWorker WORKER STALL-SECONDS))
  (<- (Delay (+ (/ T.lease-ms 1000) 5.0)))
  (<- state dict (ReadCoordinator "/state"))
  (<- (release-broker parts.broker))
  (<- runs tuple (CoordinatorRuns))
  #((get state "workers" WORKER "live") (len runs)))


(deftest test-a-broker-that-stops-answering-does-not-hold-the-coordinator
  ;; 直す点 A(見直し): 出来事の送りは調停の歩の外の task で出す — 答えない broker が、歩と要求への返事を止めない。
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (answers-while-the-broker-hangs) :timing (ClusterTiming) :workers WORKERS))
  (assert (is (get seen 0) False) seen)
  (assert (= (get seen 1) 1) seen))


(defk stalls-past-the-scaled-lease []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "比で延ばした世界の生死の期限より長く worker の処理を止め、その後も待つため(見張りが先に終わらせる)。"
  (<- scaled ClusterTiming (scaled-timing SIM-TIMING-RATIO))
  (<- (Delay 3.0))
  (<- (StallWorker WORKER (+ (/ scaled.lease-ms 1000) 20.0)))
  (<- (Delay (+ (/ scaled.lease-ms 1000) 20.0)))
  None)


(deftest test-a-worker-death-in-the-scaled-world-ends-the-run
  (with [caught (pytest.raises SimLivenessError)]
    (<- (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (stalls-past-the-scaled-lease) :workers WORKERS)))
  (assert (in WORKER (str caught.value)) caught.value))
