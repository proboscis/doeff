;;; 測った空きの門の外の task の置き先の順(coordinator/core/measured_room の頭の註の測った空きの門の外の task の置き先の順・2026-11-11)の検:
;;; 門の外の task は待たずに置かれるが、置ける worker の中では要りが memory の空きに入る worker を先に選ぶ。
;;;   (1) 同じ形の山 2,800 MiB の task は、memory の空き 500 MiB の node-a ではなく、memory の空いた node-h に置かれる。2 台とも測った
;;;       空き(数)は 0 で、前の順(env・数の空き・名)では名の順で node-a に置かれた — 2026-11-11 の置き方(下の失敗ケース)
;;;   (2) 要りがどちらにも入る task(形を名乗らない — 要り UNDECLARED-TASK-MIB)は、今までどおり数の空きの順で選ぶ
;;;       (memory の空きが多い機体へ寄せるのではなく、入るかどうかで分ける)
;;;   (3) どの worker にも入らない時も待たず(queued にしない)、足りなさの最も小さい worker に置かれる
;;;   (4) 空きは走っている task の伸びの見込み(予約)を引かずに数える: 実の空き 12,000 MiB で予約を引くと 600 MiB の node-h は、
;;;       実の空き 1,200 MiB の node-a より先に選ばれる(予約で数えると node-a の方が足りなさが小さく、node-a に置かれる)
;;; 失敗ケース = 2026-11-11 09:27〜09:52 JST: 置き先が空きを判じない順だけで選び、空き 1.3〜1.6 GiB の機体に山 1.0〜3.0 GiB の task が
;;; 置かれ、jobs の層の追い出しで止まって 7 度置き直した(同じ刻のもう 1 台は空いていて、そこに置かれた同じ task は通った)。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo TaskLimit GroupLimit]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB CPU-PRESSURE-STOP])
(import doeff_hy._table [TableWrite table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
(val NEEDS #("pool"))
(val GROUP "declare")
(val GROUPS (table-of #((TableWrite GROUP (GroupLimit :group GROUP :scope "cluster" :limit 4)))))
(val LIMITS #((TaskLimit :group GROUP)))
(val SHAPE "declare:repo-a")
;; 2026-11-11 の値: 同じ形の task の山(最大 3.0 GiB の手前)と、混んだ機体の memory の空き(測った空き − 床)。
(val SHAPE-PEAK-MIB 2800)
(val SHORT-ROOM-MIB 500)


(defk worker [name room-mib * [pressured False] [uses None]]
  {:pre [(: name str) (: room-mib int) (: pressured bool) (: uses (| dict None))] :post [(: % WorkerInfo)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 pool を持ち、CPU の空いた 8 core で、memory の空き(測った空き − 床)が room-mib の worker を作るため。pressured = CPU の PSI が
   止めの線の上(測った空きの数が 0 — memory の空きは変わらない)。uses = 測りが名乗る task ごとの今の使い。"
  (WorkerInfo name NEEDS NOW :versions PY3
              :measured (MeasuredLoad :at-ms NOW :cpus 8 :cpu-busy 0.1 :cpu-nice 0.0 :memory-total-MiB 65536
                                      :memory-available-MiB (+ MEMORY-FLOOR-MIB room-mib)
                                      :cpu-some-avg10 (if pressured (+ CPU-PRESSURE-STOP 50.0) 0.5) :memory-some-avg10 0.0
                                      :task-memory-MiB uses)))


(defk ungated-task [id shape]
  {:pre [(: id str) (: shape (| str None))] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 pool を要る、門の外を名乗る(要りを名乗らず組 GROUP を名乗る)待っている切り離した task(形 shape)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 20000 0 :detached True :key id
              :limits LIMITS :ungated True :shape shape))


(defk placed-on [task workers * [others {}]]
  {:pre [(: task TaskRecord) (: workers dict) (: others dict)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker の組 workers(名 → 行)と、同じ形で山 SHAPE-PEAK-MIB まで伸びて終わった task 1 本とほかの task の行 others(id → 行)の在る
   状態で、待っている task を 1 拍置いた行を知るため。"
  (<- ended TaskRecord (ungated-task "ended" SHAPE))
  (val done (replace ended :phase "finished" :worker "node-h" :started-ms 1000 :finished-ms (- NOW 1000)
                :retain-ms 60000 :peak-mib SHAPE-PEAK-MIB))
  (<- worker-rows (table-from-rows workers))
  (<- task-rows (table-from-rows (| others {"ended" done task.id task})))
  (val state (replace (ClusterState #() worker-rows (table-of #()) task-rows) :group-limits GROUPS))
  (.row (! (tasks-placed NOW state state.placements T)) task.id))


(deftest test-an-ungated-task-goes-to-the-worker-its-shape-peak-fits
  ;; 2 台とも測った空き(数)は 0(node-a は memory の空きが床の上 500 MiB・node-h は CPU の PSI が止めの線の上)。node-h の memory は空いている。
  (<- node-a WorkerInfo (worker "node-a" SHORT-ROOM-MIB))
  (<- node-h WorkerInfo (worker "node-h" 20000 :pressured True))
  (<- task TaskRecord (ungated-task "next" SHAPE))
  (<- row TaskRecord (placed-on task {"node-a" node-a "node-h" node-h}))
  (assert (= #(row.phase row.worker) #("assigned" "node-h")) row))


(deftest test-a-need-that-fits-everywhere-keeps-the-room-order
  ;; 形を名乗らない task の要りは UNDECLARED-TASK-MIB(1,024 MiB)で、どちらの memory の空きにも入る — memory の多さでなく、今までの
  ;; 数の空きの順で選ぶ(node-a は 1 枠・node-h は PSI で 0)。
  (<- node-a WorkerInfo (worker "node-a" 1500))
  (<- node-h WorkerInfo (worker "node-h" 20000 :pressured True))
  (<- task TaskRecord (ungated-task "next" None))
  (<- row TaskRecord (placed-on task {"node-a" node-a "node-h" node-h}))
  (assert (= #(row.phase row.worker) #("assigned" "node-a")) row))


(deftest test-an-ungated-task-that-fits-nowhere-is-placed-where-it-is-least-short
  ;; どちらの空きも山より小さい: 待たずに、足りなさ(山 − 空き)の小さい node-h(2,000 MiB)に置く。
  (<- node-a WorkerInfo (worker "node-a" SHORT-ROOM-MIB))
  (<- node-h WorkerInfo (worker "node-h" 2000 :pressured True))
  (<- task TaskRecord (ungated-task "next" SHAPE))
  (<- row TaskRecord (placed-on task {"node-a" node-a "node-h" node-h}))
  (assert (= #(row.phase row.worker) #("assigned" "node-h")) row))


(deftest test-the-room-is-counted-without-the-growth-of-running-tasks
  ;; node-h の実の空きは 12,000 MiB。走っている task 1 本(今の使い 100 MiB・自分の山 11,500 MiB)の伸びの見込み 11,400 MiB を予約に
  ;; 数えると空きは 600 MiB で、実の空き 1,200 MiB の node-a より狭く見える。追い出しを決めるのは今の実の空きなので node-h を選ぶ。
  (<- running TaskRecord (ungated-task "busy" "other:long"))
  (val busy (replace running :phase "assigned" :worker "node-h" :started-ms 1000 :peak-mib 11500))
  (<- node-a WorkerInfo (worker "node-a" 1200))
  (<- node-h WorkerInfo (worker "node-h" 12000 :uses {"busy" 100}))
  (<- task TaskRecord (ungated-task "next" SHAPE))
  (<- row TaskRecord (placed-on task {"node-a" node-a "node-h" node-h} :others {"busy" busy}))
  (assert (= #(row.phase row.worker) #("assigned" "node-h")) row))
