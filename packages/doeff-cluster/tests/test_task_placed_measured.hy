;;; 置いた刻の測った空き(TaskRecord の placed-memory-available-mib・placed-measured-at-ms・2026-11-12)の検:
;;; worker の measured は保存しない拍ごとの観測なので、置き先が task を置いた拍に数えた空きを task の行に写し、終わった後も読めるようにする。
;;;   (1) 置いた行は、置いた worker の measured の memory-available-MiB と at-ms を持つ(冷たい起動の preparing も同じ)
;;;   (2) GET /state の task の行(task-summary)に placedMemoryAvailableMiB と placedMeasuredAtMs が載る・置いていない行には載らない
;;;   (3) 待ちの行へ戻した次の試行は、前の試行の置いた刻の空きを持たない
;;;   (4) 保存の綴りの往復で残り、欄の無い行は None で読む
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed preempted-detached])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._coordinator.protocol.replies [task-summary])
(import doeff_hy._table [Table table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])
(import tests.room_rows [compartments-like-rooms])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
;; 測りの刻は置く拍より前(heartbeat の後に置き先が回る)。
(val MEASURED-AT 98500)
(val AVAILABLE-MIB (+ MEMORY-FLOOR-MIB 20000))


;; 測った空きが床の上 20000 MiB の node-a(CPU は空き)。
(val NODE-A (WorkerInfo "node-a" #("host-node-a") NOW :versions PY3
                        :measured (MeasuredLoad :at-ms MEASURED-AT :cpus 32 :cpu-busy 0.1 :memory-total-MiB 128000
                                                :memory-available-MiB AVAILABLE-MIB :task-memory-MiB {})))


(defk queued [id]
  {:pre [(: id str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-a を要る、待っている切り離した task(要り 2048 MiB・走らせ直しと場所を譲る名乗り)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-a") 15000 20000 0 :detached True :key id
              :memory-mib 2048 :rerun True :preemptible True))


(defk placed-rows [rows worker]
  {:pre [(: rows dict) (: worker WorkerInfo)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker 1 台の状態で task rows を置き先に 1 拍回し、拍の後の全部の行(id → 行)を得るため。"
  (<- workers (table-from-rows {worker.name worker}))
  (<- tasks (table-from-rows rows))
  (<- compartments Table (compartments-like-rooms {worker.name worker}))
  (val state (ClusterState #() workers (table-of #()) tasks :machine-compartments compartments))
  (val placed (! (tasks-placed NOW state state.placements T)))
  (dfor t (.rows placed) t.id t))


(deftest test-a-placed-task-keeps-the-measured-room-of-its-worker-at-the-placing-tick
  (val worker NODE-A)
  (<- task TaskRecord (queued "t-roll"))
  (<- rows dict (placed-rows {"t-roll" task} worker))
  (val placed (get rows "t-roll"))
  (assert (= #(placed.phase placed.worker placed.started-ms) #("assigned" "node-a" NOW)) placed)
  (assert (= #(placed.placed-memory-available-mib placed.placed-measured-at-ms) #(AVAILABLE-MIB MEASURED-AT)) placed)
  ;; 冷たい起動(preparing)も置いた拍の空きを持つ。
  (<- cold TaskRecord (queued "t-cold"))
  (<- cold-rows dict (placed-rows {"t-cold" (replace cold :runtime-env {"python" "3.12"})} worker))
  (val prepared (get cold-rows "t-cold"))
  (assert (= #(prepared.phase prepared.placed-memory-available-mib prepared.placed-measured-at-ms)
             #("preparing" AVAILABLE-MIB MEASURED-AT))
          prepared))


(deftest test-the-state-row-of-a-placed-task-names-the-room-and-its-measure-time
  (val worker NODE-A)
  (<- task TaskRecord (queued "t-roll"))
  (<- rows dict (placed-rows {"t-roll" task} worker))
  ;; 終わった後も行に残る(終わりは phase と finished-ms だけを替える)。
  (val ended (replace (get rows "t-roll") :phase "finished" :finished-ms (+ NOW 60000)))
  (<- row dict (task-summary ended))
  (assert (= #((get row "placedMemoryAvailableMiB") (get row "placedMeasuredAtMs")) #(AVAILABLE-MIB MEASURED-AT)) row)
  ;; 失敗ケース(直す前の形): 置いていない行は欄を持たない。
  (<- waiting dict (task-summary task))
  (assert (not-in "placedMemoryAvailableMiB" waiting) waiting)
  (assert (not-in "placedMeasuredAtMs" waiting) waiting))


(deftest test-the-next-attempt-does-not-carry-the-room-of-the-previous-placement
  (val worker NODE-A)
  (<- task TaskRecord (queued "t-roll"))
  (<- rows dict (placed-rows {"t-roll" task} worker))
  (<- by TaskRecord (queued "t-urgent"))
  (<- again TaskRecord (preempted-detached (get rows "t-roll") (+ NOW 1000) (replace by :priority 100) "node-a"))
  (assert (= #(again.phase again.placed-memory-available-mib again.placed-measured-at-ms) #("queued" None None)) again))


(deftest test-the-stored-row-keeps-the-room-at-the-placing-tick
  (val worker NODE-A)
  (<- task TaskRecord (queued "t-roll"))
  (<- rows dict (placed-rows {"t-roll" task} worker))
  (val placed (get rows "t-roll"))
  (<- back TaskRecord (task-record-from-json (task-record-to-json placed)))
  (assert (= #(back.placed-memory-available-mib back.placed-measured-at-ms) #(AVAILABLE-MIB MEASURED-AT)) back)
  ;; 置いていない行は欄を置かず、欄の無い行は None で読む。
  (val stored (task-record-to-json task))
  (assert (not-in "placed_memory_available_mib" stored) stored)
  (<- plain TaskRecord (task-record-from-json stored))
  (assert (= #(plain.placed-memory-available-mib plain.placed-measured-at-ms) #(None None)) plain))
