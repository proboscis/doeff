;;; 置ける組の印を枠の file で worker に持たせ、Pod を作り直さずに置き先を替える事の失敗ケース(card ki-ced3c45980a5)。
;;;
;;; 本物の coordinator と worker を模擬の cluster(仮想の時計)で回す。worker w1 は起動の引数の能力 cluster-net だけを持ち、枠の file を読む
;;; (本番の --room-file — SimWorker の reads-room)。job pooled は cluster-net と組の印 pool-a を要る:
;;;
;;;   (a) 枠の file の w1 の行に pools が無い間、pooled はどこにも置かれない(置ける worker が無い)。memoryMiB は GET /state の w1 の行に出る。
;;;   (b) 行に pools ["pool-a"] を足すと、w1 の同じ世代(Pod を作り直さない — boot が変わらない)が組の印を名乗り、pooled が w1 に置かれて
;;;       走る(直す前は組の印を名乗る道が起動の引数しか無く、Pod を作り直さないと置けないので赤)。
;;;   (c) 行から pools を外すと、w1 は組の印を名乗らなくなり、pooled は置かれない(置き先が外れる)。
(require doeff-hy.macros [deftest defk defsystem <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.sim.local [sim-cluster SimWorker ProcessesOf ReadCoordinator WriteRoomFile])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulse-program])

(val WORKERS #((SimWorker :name "w1" :provides (frozenset #{"cluster-net"}) :task-reserve 0 :capacity 3 :reads-room True)))
;; 本番の時間の設定を明示して渡す: 枠の file の値は heartbeat で名乗るので、heartbeat の間(本番は生存の窓 10 秒の 4 分の 1)がこの筋書きの
;; 物差し。比で延ばした既定の世界(SIM-TIMING-RATIO)では heartbeat が 875 秒に 1 度になり、読み直しの間 10 秒と比べられない。
(val TIMING (ClusterTiming))
;; 枠の file の読み直しの間(10 秒)と heartbeat の間(2.5 秒)を越えて、置き方が落ち着くまでの秒。
(val SETTLE-SECONDS 20.0)
(val ROOM {"capacity" 3 "taskReserve" 0 "memoryMiB" 2048})


(defsystem pooled-system [#^ Callable foundation]
  "見本の系: 組の印 pool-a を要る service 1 つ"
  (pooled (pulse-program foundation) :replicas 1 :needs #{"cluster-net" "pool-a"}))


(defrecord PoolSight
  "GET /state と GET /workers/w1 の 1 回の読み: provides = w1 の名乗った能力・memory = w1 の memoryMiB(None = 出ていない)・placed = pooled の
   置き先の worker(None = 置かれていない)・unplaced = pooled が置かれない理由(None = 置かれている)・boot = w1 の今の世代。"
  (#^ tuple provides)
  (#^ (| int None) memory)
  (#^ (| str None) placed)
  (#^ (| str None) unplaced)
  (#^ (| str None) boot))


(defrecord PoolRun
  "筋書きの結果: without / with-pool / removed = 組の印の無い間・足した後・外した後の読み・ran = 組の印を足した後に w1 で起きた pooled の
   process の数。"
  (#^ PoolSight without)
  (#^ PoolSight with-pool)
  (#^ PoolSight removed)
  (#^ int ran))


(defk sight []
  {:pre [] :post [(: % PoolSight)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の状態の画面と w1 の画面から、w1 の名乗りと pooled の置き先を読むため。"
  (<- view dict (ReadCoordinator "/state"))
  (<- worker dict (ReadCoordinator "/workers/w1"))
  (val w1 (get view "workers" "w1"))
  (val placement (.get (get view "placements") "pooled"))
  (PoolSight :provides (tuple (get w1 "provides")) :memory (.get w1 "memoryMiB")
             :placed (if (is placement None) None (get placement "worker"))
             :unplaced (.get (get view "unplaced") "pooled")
             :boot (.get worker "boot")))


(defk adding-and-removing-the-pool []
  {:pre [] :post [(: % PoolRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "枠の file の w1 の行に組の印を足して外し、そのたびに置き方が落ち着いてから読むため。"
  (<- (WriteRoomFile (json.dumps {"w1" ROOM})))
  (<- (Delay SETTLE-SECONDS))
  (<- without PoolSight (sight))
  (<- (WriteRoomFile (json.dumps {"w1" (| ROOM {"pools" ["pool-a"]})})))
  (<- (Delay SETTLE-SECONDS))
  (<- with-pool PoolSight (sight))
  (<- processes tuple (ProcessesOf "pooled"))
  (<- (WriteRoomFile (json.dumps {"w1" ROOM})))
  (<- (Delay SETTLE-SECONDS))
  (<- removed PoolSight (sight))
  (PoolRun :without without :with-pool with-pool :removed removed :ran (len (lfor p processes :if (= p.worker "w1") p))))


(deftest test-a-pool-in-the-room-file-places-the-job-without-recreating-the-worker
  (<- seen PoolRun (sim-cluster :notice-broker (MemoryBroker) (pooled-system sim-foundation) (adding-and-removing-the-pool)
                                :workers WORKERS :timing TIMING))
  ;; (a) 組の印の無い間は置かれない。予算は名乗る。
  (assert (= #(seen.without.provides seen.without.placed seen.without.memory) #(#("cluster-net") None 2048)) seen.without)
  (assert (is-not seen.without.unplaced None) seen.without)
  ;; (b) 足すと同じ世代のまま組の印を名乗り、w1 に置かれて走る。
  (assert (= #(seen.with-pool.provides seen.with-pool.placed seen.with-pool.unplaced) #(#("cluster-net" "pool-a") "w1" None)) seen.with-pool)
  (assert (>= seen.ran 1) seen)
  (assert (= seen.with-pool.boot seen.without.boot) seen)
  ;; (c) 外すと名乗らなくなり、置かれない。世代は変わらない。
  (assert (= #(seen.removed.provides seen.removed.placed) #(#("cluster-net") None)) seen.removed)
  (assert (is-not seen.removed.unplaced None) seen.removed)
  (assert (= seen.removed.boot seen.without.boot) seen))
