;; 模擬の worker の機体の memory の見張り(SimWorker の memory-watch)を、模擬の世界(sim-cluster — 本物の
;; coordinator と本物の worker を仮想の時計で回す)で確かめる失敗ケース。
;;   - 子 process の溢れ(2026-10-30 の node-h で、job の shim の直下に孤児が約 8.2 万本溜まり、木の辿りが process の
;;     数の 2 乗の見張りの 1 回が main の scheduler を何十分も塞ぎ、heartbeat が止まって Pod が NotReady になった): 常駐の job の根の直下に
;;     子 8 万本を持つ worker で見張りを回しても、1 回の読みは CPU の上限の内で終わり、生死の期限を越えて待った後も coordinator は worker を
;;     生きていると読む。直す前の見張り(doeff 762359834 の親)では読みが上限を越えて切られ、終わらない読みの止まりで heartbeat が止まり、
;;     worker は生死の期限で居ないと読まれる。
;;   - 見張りの無い既定の worker(memory-watch が None)は読みの記録を持たない(本番の入口の外の模擬の既定を変えない)。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_time [Delay])
(import doeff_events [MemoryBroker])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [sim-cluster SimWorker ReadCoordinator MemoryReadsOf SimMemoryWatch SimProcFlood SimMemoryRead])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses])

(val T (ClusterTiming))
(val WORKER "w1")
;; 群れの大きさ: 10-30 の node-h の約 8.2 万本に揃えた 8 万本(pulses の常駐の job pulse の根の直下)。
(val CHILDREN 80000)
;; 読みの間隔: 筋書きの間に群れの読みが 2 回入る長さ(短いと 8 万本の読みの数だけ壁の時間が延びる)。
(val SAMPLE-SECONDS 30.0)
;; 群れを置いた後に待つ長さ: 生死の期限(lease-ms)を十分に越える。
(val AFTER-SECONDS (+ (/ T.lease-ms 1000) 40.0))


(defk flood-watch [children]
  {:pre [(: children int)] :post [(: % SimMemoryWatch)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "pulse の根の直下に子 children 本を持つ模擬の見張りの設定を作るため。"
  (SimMemoryWatch :floods #((SimProcFlood :job "pulse" :children children)) :sample-seconds SAMPLE-SECONDS))


(defk watched-then-live []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "群れの読みが入るまで回し、生死の期限を越えて待った後の coordinator の worker の生死と、見張りの読みの記録を返すため。"
  (<- (Delay (+ SAMPLE-SECONDS 5.0)))
  (<- (Delay AFTER-SECONDS))
  (<- state dict (ReadCoordinator "/state"))
  (<- reads tuple (MemoryReadsOf WORKER))
  #((get state "workers" WORKER "live") reads))


(deftest test-a-worker-with-eighty-thousand-children-keeps-its-heartbeat-through-a-watch-read
  (<- watch SimMemoryWatch (flood-watch CHILDREN))
  (val workers #((SimWorker :name WORKER :provides #{"cluster-net"} :memory-watch watch)))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (watched-then-live) :timing T :workers workers))
  (val live (get seen 0))
  (val reads (get seen 1))
  (val flooded (tuple (gfor r reads :if (> r.rows CHILDREN) r)))
  (assert flooded reads)
  (assert (all (gfor r flooded r.finished)) flooded)
  (assert (is live True) #(live flooded)))


(defk read-records []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "少し回した後の見張りの読みの記録を返すため。"
  (<- (Delay 12.0))
  (<- reads tuple (MemoryReadsOf WORKER))
  reads)


(deftest test-a-default-worker-runs-no-memory-watch
  (<- reads tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (read-records) :timing T
                               :workers #((SimWorker :name WORKER :provides #{"cluster-net"}))))
  (assert (= reads #()) reads))


(deftest test-a-small-tree-is-read-each-sample-without-a-stall
  ;; 群れの小さい worker は読みの間隔ごとに読み、手間は止まりの下限より小さいので止まりを載せない(仮想の時計を揺らさない)。
  (val workers #((SimWorker :name WORKER :provides #{"cluster-net"}
                            :memory-watch (SimMemoryWatch :floods #((SimProcFlood :job "pulse" :children 10)) :sample-seconds 5.0))))
  (<- reads tuple (sim-cluster :notice-broker (MemoryBroker) (pulses sim-foundation) (read-records) :timing T :workers workers))
  (assert (>= (len reads) 2) reads)
  (assert (all (gfor r reads (and r.finished (= r.stalled-ms 0)))) reads))
