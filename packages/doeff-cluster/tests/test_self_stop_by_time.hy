;; worker の自己停止を、heartbeat が失敗した時だけでなく処理の周期ごとに時間で判じる(#2806・#2803 の子 H3)。処理が止まって
;; heartbeat を送れなかった worker は、戻った最初の周期で「最後の成功から fence を越えた」と判じ、印の無い(移せる先の在る)job を
;; 止める — 戻って最初の heartbeat の返事を待つ間(本番の返事の上限 15 秒)に coordinator が移し替えの期限を越えて他の worker へ置いても、
;; 2 か所で走らない。印の在る job は今までどおり長い方の柵まで動かし続ける(#2804・tests/test_keep_when_cut_off.hy)。
;;
;; 前半 = 判断(heartbeat_rules.desired-after-silence)を直に呼ぶ検。後半 = 模擬の世界(本物の coordinator と本物の worker を仮想の時計で
;; 回す sim-cluster)の処理の止まり(StallWorker)の筋書き。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import doeff_events [MemoryBroker])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.coordinator.core.coordinator_invariants [one-place-per-job ProcessSpan])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs])
(import doeff_cluster.worker.core.heartbeat_rules [desired-after-silence])
(import doeff_cluster.sim.local [sim-cluster SimWorker ProcessesOf StallWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses])

;; 本番と同じ時間の設定(fence 20 秒)。
(val T (ClusterTiming))
;; 能力 cluster-net を持つ worker 2 台 — pulse は移せる先の在る job になり、印が付かない。
(val TWO-CAPABLE #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)
                   (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :task-reserve 0)))


;; --- 判断 ---------------------------------------------------------------------------------------------------------

(val MOVABLE (JobSpec "a" "m" #() "rev"))
(val KEPT (replace (JobSpec "b" "m" #() "rev") :keep-when-cut-off True))


(deftest test-the-silence-judgment-stops-movable-jobs-only-past-the-fence-while-holding-a-declaration
  ;; 最後に成功した返事の宣言を持っていて、最後の成功から fence を越えた時だけ、印の無い job を止めた宣言(印の在る job は残す)を返す。
  (val past (desired-after-silence (+ T.fence-ms 1) T.fence-ms T.keep-fence-ms True #(MOVABLE KEPT) #()))
  (assert (isinstance past DesiredJobs) past)
  (assert (= past.jobs #(KEPT)) past)
  ;; fence の内は止めない(heartbeat の判断へ進む)。
  (assert (is (desired-after-silence T.fence-ms T.fence-ms T.keep-fence-ms True #(MOVABLE KEPT) #()) None))
  ;; もう止めてある(宣言を持っていない)周期は二度判じない — heartbeat を送って返事で戻す(届かなければ desired-when-unreachable)。
  (assert (is (desired-after-silence (+ T.fence-ms 1) T.fence-ms T.keep-fence-ms False #(MOVABLE KEPT) #()) None)))


;; --- 模擬の世界の筋書き ---------------------------------------------------------------------------------------------

(defrecord StallSeen
  "処理の止まりの筋書きの読み: host = 処理を止めた担い手・resumed-ms = 止まりが明けた時刻・after = 明けて 30 秒後の process の列。"
  (#^ str host)
  (#^ int resumed-ms)
  (#^ tuple after))


(defk stall-then-read [seconds]
  {:pre [(: seconds float)] :post [(: % StallSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って pulse の担い手の処理を seconds 秒止め(heartbeat を送らない・子は動き続ける)、明けて 30 秒後に process の列を読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (val host (. (get before 0) worker))
  (<- stalled-at int (now-epoch-ms))
  (<- (StallWorker host seconds))
  (<- (Delay (+ seconds 30.0)))
  (<- after tuple (ProcessesOf "pulse"))
  (StallSeen :host host :resumed-ms (+ stalled-at (int (* seconds 1000))) :after after))


(defk spans-of [processes]
  {:pre [(: processes tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの process の記録を条 C2 の判断に渡す区間の列にするため。"
  (tuple (gfor p processes (ProcessSpan :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms))))


(deftest test-a-worker-stalled-past-the-fence-stops-its-movable-job-on-the-first-cycle-after-it-resumes
  ;; 受入: 処理が 30 秒(fence 20 秒の後・移し替えの期限の前)止まった担い手は、戻った最初の周期で印の無い job を止める(止めの合図 -15)
  ;; — heartbeat の返事を待たない。coordinator は置き先を保っているので、次の返事で同じ担い手に起き直す。2 か所では走らない。
  ;; 直す前は、戻って最初の heartbeat が通るので止めの判断が走らず、同じ process が動き続けた(列は 1 つ・exit-code None)。
  (<- seen StallSeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (pulses sim-foundation) (stall-then-read 30.0) :workers TWO-CAPABLE))
  (val stopped (get seen.after 0))
  (assert (= #(stopped.worker stopped.exit-code) #(seen.host -15)) seen.after)
  ;; 止めたのは明けた最初の周期(明けた刻から止めの猶予の内 — 止まりの最中ではない)。
  (assert (and (is-not stopped.ended-ms None) (<= seen.resumed-ms stopped.ended-ms (+ seen.resumed-ms 5000))) #(seen.resumed-ms stopped))
  (val again (get seen.after -1))
  (assert (= #(again.worker again.exit-code) #(seen.host None)) seen.after)
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken))


(deftest test-a-worker-stalled-within-the-fence-keeps-its-movable-job
  ;; 止まりが fence の内(10 秒)なら止めない — 戻った周期の判断は「最後の成功から fence を越えたか」だけを見る。
  (<- seen StallSeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (pulses sim-foundation) (stall-then-read 10.0) :workers TWO-CAPABLE))
  (assert (= (len seen.after) 1) seen.after)
  (assert (= #((. (get seen.after 0) worker) (. (get seen.after 0) exit-code)) #(seen.host None)) seen.after))
