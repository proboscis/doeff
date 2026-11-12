;;; 線の内の貸し(coordinator/core/measured_room の頭の註の線の内の貸し・worker/core/load_rules の頭の註の線の内の貸し・2026-11-12)の検:
;;; 上に載る系が残す線の内を、場所を譲る名乗りの task に借りとして貸し、上に載る系が memory の不足で仕事を待たせる間だけ取り返す。
;;;   (1) 線の上の空きで置けない譲れる task は、worker が名乗る貸しの空きへ借り(borrowed)として置かれる
;;;   (2) 上に載る系が待つ間(worker が取り返す量を名乗る)は、借りの task を優先の低い順に、取り返す量に届くまで止めて待ちの行へ戻す
;;;   (3) 上に載る系が待っていなければ、空きが線を割っていても借りを止めない
;;;   (4) 譲れない task(preemptible の無い task)は借りに置かない — 線の上の空きを待つ
;;;   (5) worker: 線が名乗りを縮める間だけ下の線までの空きを名乗り、取り返しの印が新しい間は貸さずに線を割った量を名乗る
;;;   (6) worker の枠の file: lendLineFile と reclaimFile は組で、keepLineFile の在る行だけが書ける
;;;   (7) 保存の綴りの往復で借りの印と取り返した印が残る
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._worker.core.load_rules [machine-load-of RECLAIM-FRESH-MS])
(import doeff_cluster._worker.core.room_rules [room-of-text RoomState])
(import doeff_hy._table [table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
;; 線の上の空き(床の上 1000 MiB)と、貸しの空き(床の上 9000 MiB)。要り 6144 MiB の task は線の上に入らず、貸しには入る。
(val ROOM-MIB 1000)
(val LEND-ROOM-MIB 9000)
(val NEED-MIB 6144)


(defk worker-with [* [lend True] [reclaim None] [uses {}]]
  {:pre [(: lend bool) (: reclaim (| int None)) (: uses dict)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "線の上の空きが床の上 ROOM-MIB の node-h を作るため(CPU は空き・PSI は低い)。lend = 貸しの空き(床の上 LEND-ROOM-MIB)を名乗るか・
   reclaim = 取り返す量(MiB — None = 上に載る系が待っていない)・uses = 測りの名乗る task ごとの今の使い(MiB)。"
  (WorkerInfo "node-h" #("host-node-h") NOW :versions PY3
              :measured (MeasuredLoad :at-ms NOW :cpus 32 :cpu-busy 0.1 :memory-total-MiB 128000
                                      :memory-available-MiB (if (is reclaim None) (+ MEMORY-FLOOR-MIB ROOM-MIB) 0)
                                      ;; 前の版の worker が両方を名乗っても、取り返す量を名乗る worker には貸さない(置き先の守り)。
                                      :lend-memory-available-MiB (if lend (+ MEMORY-FLOOR-MIB LEND-ROOM-MIB) None)
                                      :reclaim-MiB reclaim :task-memory-MiB uses)))


(defk queued [id * [preemptible True]]
  {:pre [(: id str) (: preemptible bool)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-h を要る、待っている切り離した task(要り NEED-MIB・走らせ直しの名乗り・場所を譲る名乗り preemptible)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-h") 15000 20000 0 :detached True :key id
              :memory-mib NEED-MIB :rerun True :preemptible preemptible))


(defk borrowed [id priority]
  {:pre [(: id str) (: priority int)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-h の線の内に借りとして置かれて走っている、場所を譲る名乗りの切り離した task(要り NEED-MIB)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-h") 15000 (+ NOW 15000) 0 :detached True :key id
              :priority priority :memory-mib NEED-MIB :rerun True :preemptible True :borrowed True
              :phase "assigned" :worker "node-h" :started-ms 1000))


(defk placed-rows [rows worker]
  {:pre [(: rows dict) (: worker WorkerInfo)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker 1 台の状態で task rows を置き先に 1 拍回し、拍の後の全部の行(id → 行)を得るため。"
  (<- workers (table-from-rows {worker.name worker}))
  (<- tasks (table-from-rows rows))
  (val state (ClusterState #() workers (table-of #()) tasks))
  (val placed (! (tasks-placed NOW state state.placements T)))
  (dfor t (.rows placed) t.id t))


(val STAT "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 100 0 100 800 0 0 0 0 0 0\n")
(val MEMINFO (.format "MemTotal: {} kB\nMemAvailable: {} kB\n" (* 128 1024 1024) (* 50 1024 1024)))
(val KEEP "37984\n")
(val LEND "29792\n")


(deftest test-the-worker-names-the-loan-room-and-the-reclaim-from-the-two-files
  ;; 待っていない: 線の上の空き 51200 − 37984 = 13216・貸しの空き 51200 − 29792 = 21408。取り返す量は名乗らない。
  (<- quiet object (machine-load-of STAT MEMINFO None None None NOW :keep-line KEEP :lend-line LEND :reclaim-mark None))
  (assert (= #(quiet.load.memory-available-mib quiet.load.lend-available-mib quiet.load.reclaim-mib) #(13216 21408 None)) quiet.load)
  (assert (= quiet.lend-room-bytes (* 21408 1024 1024)) quiet)
  ;; 古い印は待っていないと読む。
  (<- stale object (machine-load-of STAT MEMINFO None None None NOW :keep-line KEEP :lend-line LEND
                                    :reclaim-mark (str (- NOW RECLAIM-FRESH-MS 1))))
  (assert (= stale.load.lend-available-mib 21408) stale.load)
  ;; 待っている(新しい印)・線の上: 貸さず、割っていないので取り返す量も名乗らない。
  (<- waiting object (machine-load-of STAT MEMINFO None None None NOW :keep-line KEEP :lend-line LEND :reclaim-mark (str NOW)))
  (assert (= #(waiting.load.lend-available-mib waiting.load.reclaim-mib waiting.lend-room-bytes) #(None None None)) waiting.load)
  ;; 待っている・線を割った(線 60000 MiB): 割った量 60000 − 51200 = 8800 MiB を名乗る。
  (<- short object (machine-load-of STAT MEMINFO None None None NOW :keep-line "60000" :lend-line LEND :reclaim-mark (str NOW)))
  (assert (= #(short.load.lend-available-mib short.load.reclaim-mib) #(None 8800)) short.load)
  ;; 下の線の file が無い・線より低くない機体は貸さない(今までの名乗り)。
  (<- none object (machine-load-of STAT MEMINFO None None None NOW :keep-line KEEP))
  (assert (is none.load.lend-available-mib None) none.load)
  (<- above object (machine-load-of STAT MEMINFO None None None NOW :keep-line KEEP :lend-line "40000"))
  (assert (is above.load.lend-available-mib None) above.load))


(deftest test-the-frame-file-takes-the-lend-line-and-the-reclaim-mark-as-a-pair-under-a-keep-line
  (val row {"memoryMiB" 63488 "keepLineFile" "/k" "lendLineFile" "/l" "reclaimFile" "/r"})
  (<- ok object (room-of-text (.format "{{\"node-h\": {}}}" (.replace (str row) "'" "\"")) "node-h"))
  (assert (= #(ok.state ok.room.lend-line-file ok.room.reclaim-file) #(RoomState.OK "/l" "/r")) ok)
  (<- half object (room-of-text "{\"node-h\": {\"memoryMiB\": 1, \"keepLineFile\": \"/k\", \"lendLineFile\": \"/l\"}}" "node-h"))
  (assert (= half.state RoomState.MALFORMED) half)
  (<- bare object (room-of-text "{\"node-h\": {\"memoryMiB\": 1, \"lendLineFile\": \"/l\", \"reclaimFile\": \"/r\"}}" "node-h"))
  (assert (= bare.state RoomState.MALFORMED) bare))


(deftest test-the-stored-row-keeps-the-loan-marks
  (<- low TaskRecord (borrowed "a-low" -1))
  (<- back TaskRecord (task-record-from-json (task-record-to-json (replace low :reclaimed True))))
  (assert (= #(back.borrowed back.reclaimed) #(True True)) back)
  (<- plain TaskRecord (task-record-from-json (task-record-to-json (replace low :borrowed False))))
  (assert (= #(plain.borrowed plain.reclaimed) #(False False)) plain)
  (assert (not-in "borrowed" (task-record-to-json (replace low :borrowed False)))))
