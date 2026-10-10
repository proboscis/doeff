;; worker の作り直し(本番の worker の Pod の作り直し — 値の変更や doeff-cluster の差の配備)で、他の worker へ移せない handoff の job が
;; 走らせている仕事を切らない(条 D2 — 本番の作り替えが走っている仕事を巻き込まない)。
;;
;; 筋書き: worker w1 だけが job host(needs cluster-net・update handoff・30 秒の仕事)を持てる。仕事の 5 秒目に w1 を本番の作り直しと同じ順で
;; 入れ替える(test_upgrade_swaps.hy の swap-a と同じ — drain〔preStop〕→ 止め〔古い Pod が抜けるまで〕→ 新しい世代で起こす)。
;; 求める姿: 最初の世代の仕事は終わりまで回る(盤に done が在る)・最初の世代の process は止めで切られない(exit -15 / -9 でない)。
;; 今の形(赤): drain は host を unmovable として即座に drained を返し(drain_policy の drain-view)、止めの TERM → stop-grace 10 秒 → KILL で
;; 仕事の途中の process が exit -15 で終わる。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimProcess ProcessesOf SharedRows DrainWorker StopWorker StartWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.working_programs [WORK-PREFIX working-hosts])

(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))


(defrecord SwapSeen
  "筋書きが読んだ姿: processes = host の process(起きた順)・rows = 盤の work/ の行。"
  (#^ (get tuple #(SimProcess ...)) processes)
  (#^ dict rows))


(defk swapped-during-the-work []
  {:pre [] :post [(: % SwapSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 仕事の 5 秒目に w1 を本番の作り直しと同じ順で入れ替え、40 秒待って host の process と盤の行を読む。"
  (<- (Delay 5.0))
  (<- asked dict (DrainWorker "w1"))
  (assert (= (get asked "status") 200) asked)
  (<- (Delay 1.0))
  (<- (StopWorker "w1"))
  (<- started bool (StartWorker "w1"))
  (assert started)
  (<- (Delay 40.0))
  (<- processes tuple (ProcessesOf "host"))
  (<- rows dict (SharedRows (+ WORK-PREFIX "/")))
  (SwapSeen :processes (tuple (sorted processes :key (fn [p] p.started-ms))) :rows rows))


(deftest test-replacing-the-worker-of-an-unmovable-handoff-job-lets-its-running-work-finish
  (<- seen SwapSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) (working-hosts sim-foundation) (swapped-during-the-work)))
  (val first (get seen.processes 0))
  (assert (in (.format "{}/{}/begun" WORK-PREFIX first.instance) seen.rows) seen.rows)
  ;; 最初の世代の仕事は終わりまで回り、その process は止めで切られない。
  (assert (in (.format "{}/{}/done" WORK-PREFIX first.instance) seen.rows) #(first seen.rows))
  (assert (not-in first.exit-code #(-15 -9)) first))
