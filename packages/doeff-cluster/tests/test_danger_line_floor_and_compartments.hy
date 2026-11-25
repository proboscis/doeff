;;; 見張りの DANGER の線(danger-available)の単一の持ち主・coordinator の memory の床・模擬の世界の検。
;;;   (1) coordinator の memory の床(memory-floor-mib)と区画の和の上限(compartments-ceiling-mib)が、worker の
;;;       MemoryLimits.danger-available の 1 か所から導かれ、固定の数 1536 や 0.10 の写しを持たない
;;;   (2) 空きが「1536 MiB 以上・線 × danger-available 未満」の worker では memory-room-mib が負・measured-room が 0 になる
;;;   (3) 模擬の世界(sim-cluster + 本物の memory-watch-step・guard True)で、宣言の和が線 × (1 − danger-available)の内の
;;;       区画に置いた task は見張りの DANGER で 1 本も止められずに終わり、和を越える宣言では見張りの DANGER で止められる
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import math [ceil])
(import doeff [with-handlers])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [ClusterTiming sim-cluster SimWorker SimMachine SimMemoryWatch SimMemoryRead
                       ReadCoordinator MemoryReadsOf ClientLink coordinator-answers
                       SubmitDetached AwaitDetached DetachedSucceeded system-of])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.measured_room [memory-floor-mib memory-room-mib memory-line-mib measured-room])
(import doeff_cluster._worker.core.memory_rules [MemoryLimits danger-line-mib compartments-ceiling-mib])
(import doeff_cluster._sim.local [SimLink send-request])

(val T (ClusterTiming))
(val WORKER "w1")
(val NO-JOBS (system-of "danger-line-sim" #()))
;; 60,000 MiB の機体: 常駐 48,000 MiB・task 1 本 3,000 MiB・見張りの DANGER の線 6,000 MiB(上限 54,000 MiB → tasks 6,000 MiB = 2 本まで)。
(val SIM-TOTAL-MIB 60000)
(val SIM-BASE-MIB 48000)
(val SIM-TASK-MIB 3000)


(defk slow-work [n seconds]
  {:pre [(: n int) (: seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "見張りの読みの拍を跨いで走る模擬の task(seconds 秒待って n を返す)。"
  (<- (Delay seconds))
  n)


(deftest test-memory-floor-is-derived-from-worker-danger-available-without-fixed-copy
  (val limits (MemoryLimits))
  (for [total #(20263 59618 121315)]
    (val want-floor (ceil (* total limits.danger-available)))
    (assert (= (memory-floor-mib total) (danger-line-mib total) want-floor) total)
    (assert (= (compartments-ceiling-mib total) (- total want-floor)) total))
  ;; 空きが 1536 MiB 以上・線 × danger-available 未満(59618 MiB の機体で空き 3000 MiB < 5962 MiB)では置けない。
  (val in-danger-band (MeasuredLoad :at-ms 0 :cpus 16 :cpu-busy 0.1 :memory-total-MiB 59618 :memory-available-MiB 3000
                                    :cpu-some-avg10 0.0 :memory-some-avg10 0.0))
  (<- room-in-band int (memory-room-mib in-danger-band 0))
  (<- slots-in-band int (measured-room in-danger-band 0 0 0))
  (assert (< room-in-band 0) room-in-band)
  (assert (= slots-in-band 0) slots-in-band)
  ;; 線 × danger-available の上に 2048 MiB の空きがあれば 2048 MiB(2 枠)置ける。
  (val above-danger (MeasuredLoad :at-ms 0 :cpus 16 :cpu-busy 0.1 :memory-total-MiB 59618
                                  :memory-available-MiB (+ (danger-line-mib 59618) 2048)
                                  :cpu-some-avg10 0.0 :memory-some-avg10 0.0))
  (<- room-above int (memory-room-mib above-danger 0))
  (<- slots-above int (measured-room above-danger 0 0 0))
  (assert (= #(room-above slots-above) #(2048 2)))
  (assert (= (! (memory-line-mib above-danger)) (compartments-ceiling-mib 59618))))


(defk submit-and-await-three [keys seconds]
  {:pre [(: keys tuple) (: seconds float)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "keys の 3 本を切り離した task として積み、全部の結末を dict で返すため。"
  (for [#(idx key) (enumerate keys)]
    (<- (SubmitDetached :program (slow-work (+ idx 1) seconds) :key key :needs (frozenset #("local"))
                        :name "work" :lease-seconds 60.0 :retain-seconds 60.0 :memory-mib SIM-TASK-MIB)))
  (var outcomes {})
  (for [key keys]
    (<- outcome (AwaitDetached key :timeout-seconds 90.0))
    (:= outcomes (| outcomes {key outcome})))
  outcomes)


(defk run-three-tasks [seconds * [override-tasks-mib None]]
  {:pre [(: seconds float) (: override-tasks-mib (| int None))] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "3 本の切り離した task(各 SIM-TASK-MIB)を積み、見張りの読みを跨いで終わるのを待って #(結末の dict 読みの記録) を返すため。
   override-tasks-mib が在れば、区画の宣言をその値へ書き替えてから積む(和が線を越える失敗ケースの再現)。"
  (<- link SimLink (ClientLink))
  (when (is-not override-tasks-mib None)
    (<- state dict (ReadCoordinator "/state"))
    (val ver (get state "machineCompartments" WORKER "resourceVersion"))
    (<- #(code body) tuple (send-request link "PUT" (+ "/resources/MachineCompartments/" WORKER) {}
                                         {"resourceVersion" ver
                                          "spec" {"compartments" [{"use" "tasks" "worker" WORKER "layer" "jobs"
                                                                   "memoryMiB" override-tasks-mib "cpuMillis" 6000}]}}))
    (assert (= code 200) #(code body)))
  (val keys #("k1" "k2" "k3"))
  (<- outcomes dict (with-handlers [(coordinator-answers link)] (submit-and-await-three keys seconds)))
  (<- reads tuple (MemoryReadsOf WORKER))
  #(outcomes reads))


(deftest test-tasks-placed-within-compartment-ceiling-are-never-killed-by-memory-watch-danger
  ;; sim-compartments は tasks 区画を 60000 − 48000 − danger-line(60000) = 6000 MiB に置く(和 54000 = 線 × 0.9)。
  ;; 3000 MiB の task 3 本は最大 2 本ずつ置かれ、空きは 6000 MiB(DANGER の線上)を下回らず、見張りの DANGER で 1 本も止められない。
  (val worker (SimWorker :name WORKER :provides #{"local"}
                         :machine (SimMachine :cpus 8 :memory-mib SIM-TOTAL-MIB :base-mib SIM-BASE-MIB :task-mib SIM-TASK-MIB)
                         :memory-watch (SimMemoryWatch :sample-seconds 2.0 :guard True)))
  (<- #(outcomes reads) tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (run-three-tasks 6.0) :timing T :workers #(worker)))
  (assert (all (gfor o (.values outcomes) (isinstance o DetachedSucceeded))) outcomes)
  (assert (>= (len reads) 2) reads)
  (val killed (tuple (gfor r reads name r.killed name)))
  (assert (= killed #()) #(killed reads)))


(deftest test-tasks-placed-past-compartment-ceiling-are-killed-by-memory-watch-danger
  ;; 反例: 区画の和が線 × (1 − danger-available)を越える tasks 9000 MiB(和 57000 > 54000)を宣言すると、
  ;; 3000 MiB の task 3 本が同時に置かれて空きが 3000 MiB < 6000 MiB(DANGER)になり、本物の memory-watch-step が task を止める。
  (val worker (SimWorker :name WORKER :provides #{"local"}
                         :machine (SimMachine :cpus 8 :memory-mib SIM-TOTAL-MIB :base-mib SIM-BASE-MIB :task-mib SIM-TASK-MIB)
                         :memory-watch (SimMemoryWatch :sample-seconds 2.0 :guard True)))
  (<- #(outcomes reads) tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (run-three-tasks 10.0 :override-tasks-mib 9000)
                                           :timing T :workers #(worker)))
  (val killed (tuple (gfor r reads name r.killed name)))
  (assert (bool killed) #(outcomes reads))
  (assert (any (gfor name killed (.startswith name "task/"))) killed))
