;; sim の宿が起こした process の記録(SimProcess)に、その process の根の task の id(root-task)が載る(#4194 — #3855 の U7)。
;;
;; 速さの測り(呼び手の側)は job → 根の task → 親の結び(task ごとの表 OpenTaskTally・#4188)で、job ごとの task の木の CPU と
;; doeff-vm の歩数を足す。片方だけ計算する 2 つの job で、計算しない job の木の CPU の和が計算する job の 1/10 未満。
(require doeff-hy.macros [deftest defk deff defsystem <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import time)
(import doeff [with-handlers])
(import doeff_events [MemoryBroker])
(import doeff_core_effects.scheduler_step_tally [step-tally-handler])
(import doeff_core_effects.step_tally_effects [OpenTaskTally CloseTaskTally TaskTally])
(import doeff_time [Delay])
(import doeff_cluster.sim.local [sim-cluster ProcessesOf SimProcess AwaitProcessStarted])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulse-program])

(val BUSY-CPU-MS 4)
(val BEATS 5)


(deff burn-cpu [ms]  ; defk にできない: thread の CPU を使う計算そのもの(効果を出さない素の関数 — 計算する job の本体が拍ごとに呼ぶ)
  {:pre [(: ms int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "thread の CPU 秒を ms だけ使うため(壁の時計ではない — 混んだ機体で縮まない)。"
  (setv end (+ (time.thread-time-ns) (* ms 1000000)))
  (while (< (time.thread-time-ns) end)
    None)
  None)


(defk burn-body []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "計算する job の見本: 拍ごとに CPU を使ってから、準備できたと報告し続ける。"
  (var n 0)
  (while True
    (burn-cpu BUSY-CPU-MS)
    (<- (ReportReady True "計算している"))
    (<- (Delay 1.0))
    (:= n (+ n 1)))
  n)


(defk burn-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: burn-body を土台で包む。"
  (<- n int (foundation (burn-body)))
  n)


(defsystem burn-and-idle [#^ Callable foundation]
  "見本の系: 拍ごとに計算する service と、報告するだけの service"
  (burner (burn-program foundation) :replicas 1 :needs #{"cluster-net"})
  (idler (pulse-program foundation) :replicas 1 :needs #{"cluster-net"}))


(defrecord RootReading
  "検の読み: burner / idler = 各 job の最初の process の記録・table = 窓の間の task ごとの表(OpenTaskTally の答え)。"
  (#^ SimProcess burner)
  (#^ SimProcess idler)
  (#^ tuple table))


(defk read-roots []
  {:pre [] :post [(: % RootReading)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: task の積算の窓を開け、両方の job の process が起きてから BEATS 拍待ち、process の記録と表を読む。"
  (<- (OpenTaskTally "jobs"))
  (<- (AwaitProcessStarted "burner"))
  (<- (AwaitProcessStarted "idler"))
  (<- (Delay (float BEATS)))
  (<- burners tuple (ProcessesOf "burner"))
  (<- idlers tuple (ProcessesOf "idler"))
  (<- table tuple (CloseTaskTally "jobs"))
  (RootReading :burner (get burners 0) :idler (get idlers 0) :table table))


(defk tree-cpu [table root]
  {:pre [(: table tuple) (: root int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "根の task root と、親の結びでその下にある task の CPU の和を返すため。root を持つ run が複数あれば、行の最も多い run(sim の
   scheduler の run — 検の外側の run は task が少ない)を取る。"
  (val runs (sorted (sfor row table :if (= row.tid root) row.run)
                    :key (fn [run] (len (lfor row table :if (= row.run run) row)))))
  (val run (get runs -1))
  (var members #{root})
  (var grew True)
  (while grew
    (:= grew False)
    (for [row table]
      (when (and (= row.run run) (in row.parent members) (not-in row.tid members))
        (.add members row.tid)
        (:= grew True))))
  (sum (gfor row table :if (and (= row.run run) (in row.tid members)) row.cpu-ns)))


(deftest test-each-process-names-its-root-task-and-the-tree-sums-only-its-own-cpu []
  ;; process の記録に根の task の id が在り、U6 の表と組むと、報告するだけの job の木の CPU は計算する job の木の 1/10 未満。
  (<- seen RootReading (sim-cluster :notice-broker (MemoryBroker) (burn-and-idle sim-foundation)
                                    (with-handlers [step-tally-handler] (read-roots))))
  (assert (is-not seen.burner.root-task None) seen.burner)
  (assert (is-not seen.idler.root-task None) seen.idler)
  (assert (!= seen.burner.root-task seen.idler.root-task) (, seen.burner seen.idler))
  (<- busy int (tree-cpu seen.table seen.burner.root-task))
  (<- idle int (tree-cpu seen.table seen.idler.root-task))
  (assert (>= busy (* (- BEATS 1) BUSY-CPU-MS 1000000)) (, busy idle))
  (assert (< idle (/ busy 10)) (, busy idle)))
