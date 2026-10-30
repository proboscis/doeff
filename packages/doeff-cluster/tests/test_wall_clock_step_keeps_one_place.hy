;; worker 1 台の機体の壁の時計が動いても、同じ job が 2 か所で走らない(card ki-069f4a5c07f7)— 模擬の世界(本物の coordinator と本物の
;; worker 2 台を仮想の時計で回す sim-cluster)の筋書き。本番の口そのものの刻は tests/test_silence_wall_clock_step.hy が確かめる。
;;
;; 実例の形: worker の宿は沈黙の長さ(最後に届いた返事からの ms)を壁の時計の差で判じていた。coordinator は自分の時計で、最後の heartbeat
;; から reassign-after-ms(60 秒)沈黙した worker の印の無い job を他へ移す。
;;   - 途絶の最中に担い手の壁の時計が 45 秒戻ると、担い手が自己停止を決めるのは沈黙 65 秒の時 — coordinator が 60 秒で他の worker へ
;;     置いた process と、担い手のまだ止まっていない process が重なる(条 C2 one-place-per-job の破り)。
;;   - 途絶が無くても、担い手の壁の時計が 70 秒戻ると heartbeat の送りが 70 秒止まり、coordinator は届いている担い手の job を他へ移す。
;; 沈黙は壁の時計と単調な時計の長い方で計る(heartbeat_rules.silent-ms)。筋書きは担い手の機体の壁の時計だけを動かす(検の effect
;; StepWorkerWall — 単調な時計・世界の時計は動かない)。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import pytest)
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster._coordinator.core.coordinator_invariants [one-place-per-job ProcessSpan])
(import doeff_cluster [sim-cluster SimWorker ProcessesOf CutWorker StepWorkerWall])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses])

;; 本番と同じ時間の設定(fence 20 秒・移し替え 60 秒)。
(val T (ClusterTiming))
;; 能力 cluster-net を持つ worker 2 台 — pulse は移せる先の在る job になり、印が付かない。どちらも機体の壁の時計を動かせる。
(val TWO-CAPABLE #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0 :steps-wall True)
                   (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :task-reserve 0 :steps-wall True)))
;; 途絶を始めてから壁の時計を動かすまでの秒(途絶の最中 — fence の 20 秒より前)。
(val STEP-AFTER-SECONDS 5.0)
;; 壁の時計を動かしてから process の列を読むまでの秒(移し替えの 60 秒と、戻った分だけ遅れた自己停止の後まで)。
(val READ-AFTER-SECONDS 100.0)


(defrecord StepSeen
  "壁の時計を動かす筋書きの読み: host = pulse の担い手(壁の時計を動かした worker)・began-ms = 途絶を始めた(途絶の無い筋書きは壁の時計を
   動かす 5 秒前の)世界の時計の刻・after = 読んだ時の pulse の process の列。"
  (#^ str host)
  (#^ int began-ms)
  (#^ tuple after))


(defk step-then-read [cut back-seconds]
  {:pre [(: cut bool) (: back-seconds float)] :post [(: % StepSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って pulse の担い手を見つけ、cut なら担い手の網を切り(読むまで切ったまま)、5 秒後に担い手の機体の壁の時計を
   back-seconds 戻し(負 = 進める・0 = 動かさない)、100 秒後に process の列を読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (val host (. (get before 0) worker))
  (<- began int (now-epoch-ms))
  (when cut
    (<- (CutWorker host (+ STEP-AFTER-SECONDS READ-AFTER-SECONDS 60.0))))
  (<- (Delay STEP-AFTER-SECONDS))
  (when (!= back-seconds 0.0)
    (<- (StepWorkerWall host back-seconds)))
  (<- (Delay READ-AFTER-SECONDS))
  (<- after tuple (ProcessesOf "pulse"))
  (StepSeen :host host :began-ms began :after after))


(defk seen-on [cut back-seconds]
  {:pre [(: cut bool) (: back-seconds float)] :post [(: % StepSeen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書き step-then-read を、本番と同じ時間の設定の模擬の世界(worker 2 台)で回すため。"
  (<- seen StepSeen (sim-cluster :notice-broker (MemoryBroker) :timing T (pulses sim-foundation) (step-then-read cut back-seconds)
                                 :workers TWO-CAPABLE))
  seen)


(defk overlaps-of [seen]
  {:pre [(: seen StepSeen)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの process の記録を条 C2 の判断(one-place-per-job)に渡し、重なりの列を返すため(空 = 2 か所で走っていない)。"
  (<- broken tuple (one-place-per-job (tuple (gfor p seen.after (ProcessSpan :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms)))))
  broken)


(defk self-stop-after-ms [seen]
  {:pre [(: seen StepSeen)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "担い手の最初の process が止めの合図(-15)で終わった刻の、途絶の始まりからの ms(世界の時計)。"
  (val first-process (get seen.after 0))
  (assert (= #(first-process.worker first-process.exit-code) #(seen.host -15)) seen.after)
  (- first-process.ended-ms seen.began-ms))


(deftest test-a-cut-off-worker-whose-wall-clock-stepped-back-45-seconds-does-not-run-its-job-beside-the-reassigned-one
  ;; 失敗ケース(2 か所で走る時間帯): 途絶の最中に壁の時計が 45 秒戻った担い手も、戻らない担い手(対照)と同じ刻に pulse を止め、
  ;; coordinator が 60 秒で他の worker へ置いた process と重ならない。壁の時計の差で判じると、止めるのは途絶の約 65 秒後で、重なる。
  (<- control StepSeen (seen-on True 0.0))
  (<- seen StepSeen (seen-on True 45.0))
  (<- stopped int (self-stop-after-ms seen))
  (<- control-stopped int (self-stop-after-ms control))
  (assert (= stopped control-stopped) #(stopped control-stopped seen.after))
  (assert (< stopped T.reassign-after-ms) #(stopped seen.after))
  ;; 移し替えは起きている(もう 1 台が pulse を走らせている)— 重なりが無いのは、移っていないからではない。
  (val moved (get seen.after -1))
  (assert (and (!= moved.worker seen.host) (is moved.exit-code None)) seen.after)
  (<- broken tuple (overlaps-of seen))
  (assert (= broken #()) broken))


(deftest test-a-cut-off-worker-whose-wall-clock-stepped-back-past-the-c4-margin-stops-its-job-at-the-same-instant
  ;; 失敗ケース(余白 8 秒を越える 9 秒の戻り): 自己停止の刻は戻らない担い手と同じ(fence の 20 秒の所)。壁の時計の差で判じると 9 秒遅れ、
  ;; 止め切りの最悪(返事の上限・接続の上限・停止の猶予 = 32 秒)を足すと移し替えの 60 秒を越える(条 C4)。
  (<- control StepSeen (seen-on True 0.0))
  (<- seen StepSeen (seen-on True 9.0))
  (<- stopped int (self-stop-after-ms seen))
  (<- control-stopped int (self-stop-after-ms control))
  (assert (= stopped control-stopped) #(stopped control-stopped seen.after))
  (<- broken tuple (overlaps-of seen))
  (assert (= broken #()) broken))


(deftest test-a-worker-in-touch-whose-wall-clock-stepped-back-70-seconds-keeps-its-job
  ;; 失敗ケース(途絶なし): 届いている担い手の壁の時計が 70 秒戻っても、heartbeat は間隔どおり届き続け、pulse は担い手で走り続ける
  ;; (process は 1 つのまま)。壁の時計の差で送る間隔を待つと送りが 70 秒止まり、coordinator が 60 秒の沈黙で pulse を他へ移す。
  (<- seen StepSeen (seen-on False 70.0))
  (assert (= (len seen.after) 1) seen.after)
  (val only (get seen.after 0))
  (assert (= #(only.worker only.exit-code) #(seen.host None)) seen.after))


(deftest test-a-worker-whose-wall-clock-stepped-forward-past-the-fence-stops-its-job-and-never-runs-it-in-two-places
  ;; 壁が進む向き(機体の suspend と同じ読み — 単調な時計は進まず壁の時計だけ進む・直す前も後も緑): 壁の時計が 30 秒(fence の後)
  ;; 進んだ担い手は、次の周で pulse を止める(止めの合図 -15)。coordinator から見た沈黙は無いので置き先は変わらず、同じ担い手に起き直す。
  ;; 2 か所では走らない(代償は偽の自己停止だけ)。単調な時計だけで計ると止めず、本物の suspend の後に返事を待つ間も動かし続ける。
  (<- seen StepSeen (seen-on False -30.0))
  (<- stopped int (self-stop-after-ms seen))
  (assert (<= (int (* 1000 STEP-AFTER-SECONDS)) stopped (+ (int (* 1000 STEP-AFTER-SECONDS)) (// T.lease-ms 2))) #(stopped seen.after))
  (val again (get seen.after -1))
  (assert (= #(again.worker again.exit-code) #(seen.host None)) seen.after)
  (<- broken tuple (overlaps-of seen))
  (assert (= broken #()) broken))


(defk step-a-plain-worker []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 機体の壁の時計の代役を並べていない worker の壁の時計を動かそうとする。"
  (<- (Delay 1.0))
  (<- (StepWorkerWall "w1" 5.0))
  None)


(deftest test-stepping-the-wall-clock-of-a-worker-without-its-own-wall-clock-is-refused
  ;; 守り: steps-wall でない worker の壁の時計は動かせない — 黙って何も起きない筋書きにしない。
  (with [raised (pytest.raises ValueError)]
    (<- (sim-cluster :notice-broker (MemoryBroker) :timing T (pulses sim-foundation) (step-a-plain-worker)
                     :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)))))
  (assert (in "steps-wall" (str raised.value)) raised.value))
