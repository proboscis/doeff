;;; 急ぎの task の空き(worker/core/load_rules と coordinator/core/measured_room の頭の註の急ぎの task の空き・2026-11-10)の検:
;;; 会話のターンに残す線を名指す worker は、線が空きを縮めている間、線を外して追い出しの線だけで数えた空きも名乗り、coordinator は
;;; 優先 KEEP-NOW-PRIORITY 以上の task だけをその空きで置く。
;;;   (1) worker は線が空きを縮めた時だけ urgent の空きを名乗る(heartbeat の綴りと受け口を通して読める)。線が無い時は名乗らない
;;;   (2) 線の下でしか空きの無い worker に、急ぎの task は置かれ、同じ要りの優先 0 の task は待つ
;;;   (3) 失敗ケース: worker が urgent の空きを名乗らなければ、急ぎの task も優先 0 の task と同じに待つ(直す前の形 — node-h の 11-10 は
;;;       名乗る空き約 4 GiB で、要り 6144 MiB・その機体だけを要る task が queued のまま待った)
;;;   (4) memory-room-mib の urgent は、jobs の層の空きも urgent の値で数え、追い出しの線の手前の床は同じに引く
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad HeartbeatBody])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB JOBS-ROOM-FLOOR-MIB KEEP-NOW-PRIORITY memory-room-mib
                                                       urgent-priority?])
(import doeff_cluster._worker.core.eviction_rules [EvictionLine LineKind])
(import doeff_cluster._worker.core.load_rules [LoadReading machine-load-of])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import doeff_hy._table [table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM heartbeat-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
;; 本番 node-h の 11-10 17:44 JST の形: 線 37,984 MiB・MemAvailable 約 42 GiB(線の上 約 4 GiB)・追い出しまでの空き 40,000 MiB。
(val NODE-H-TOTAL-MIB 128480)
(val KEEP-LINE-MIB 37984)
(val NODE-H-AVAILABLE-MIB (+ KEEP-LINE-MIB 4000))
(val NODE-H-EVICTION (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes (* 100000 MIB) :used-bytes (* 60000 MIB)
                                 :room-bytes (* 40000 MIB)))
(val STAT-A (+ "cpu  0 0 0 1000 0 0 0 0 0 0\n" (.join "" (gfor i (range 8) (.format "cpu{} 0 0 0 0 0 0 0 0 0 0\n" i)))))
(val STAT-B (+ "cpu  100 0 0 1900 0 0 0 0 0 0\n" (.join "" (gfor i (range 8) (.format "cpu{} 0 0 0 0 0 0 0 0 0 0\n" i)))))
(val CALM-PSI "some avg10=0.50 avg60=0.40 avg300=0.30 total=100\nfull avg10=0.10 avg60=0.10 avg300=0.10 total=10\n")
;; 置く task の要り(webapp の組みの 1 本の形)と、急ぎの優先(送り手の着地の宣言の task と同じ値)。
(val NEED-MIB 6144)
(val URGENT-PRIORITY 10)


(defk meminfo-text [total-mib available-mib]
  {:pre [(: total-mib int) (: available-mib int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "MemTotal total-mib・MemAvailable available-mib(どちらも MiB)の /proc/meminfo の中身を作るため。"
  (.format "MemTotal: {} kB\nMemFree: 1024 kB\nMemAvailable: {} kB\n" (* total-mib 1024) (* available-mib 1024)))


(defk measured-of [keep]
  {:pre [(: keep (| str None))] :post [(: % MeasuredLoad)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-h の /proc の 2 回の読みと線の file の中身 keep を、worker の測り(machine-load-of)・heartbeat の綴り(measured-json)・coordinator の
   受け口(heartbeat-of)に通した測った空きを作るため。"
  (<- meminfo str (meminfo-text NODE-H-TOTAL-MIB NODE-H-AVAILABLE-MIB))
  (<- first LoadReading (machine-load-of STAT-A meminfo CALM-PSI CALM-PSI None 0 NODE-H-EVICTION :keep-line keep))
  (<- second LoadReading (machine-load-of STAT-B meminfo CALM-PSI CALM-PSI first.ticks 5000 NODE-H-EVICTION :keep-line keep))
  (<- spelled dict (measured-json second.load))
  (<- body HeartbeatBody (heartbeat-of {"name" "node-h" "provides" ["host-node-h"] "measured" spelled}))
  body.measured)


(defk queued [id priority]
  {:pre [(: id str) (: priority int)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-h を要る、要り NEED-MIB・優先 priority の待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-h") 15000 20000 0 :detached True :key id :memory-mib NEED-MIB
              :priority priority))


(defk placed-phases [measured]
  {:pre [(: measured MeasuredLoad)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "測り measured を名乗る node-h 1 台に、急ぎの task と優先 0 の task(同じ要り)を置き先に 1 拍回し、#(急ぎの phase 優先 0 の phase) を
   得るため。"
  (val now 100000)
  (val node-h (WorkerInfo "node-h" #("host-node-h") now :versions PY3 :measured measured))
  (<- workers (table-from-rows {"node-h" node-h}))
  (<- urgent TaskRecord (queued "a-urgent" URGENT-PRIORITY))
  (<- plain TaskRecord (queued "b-plain" 0))
  (<- tasks (table-from-rows {"a-urgent" urgent "b-plain" plain}))
  (val state (ClusterState #() workers (table-of #()) tasks))
  (val placed (! (tasks-placed now state state.placements T)))
  #((. (.row placed "a-urgent") phase) (. (.row placed "b-plain") phase)))


(deftest test-the-worker-names-the-urgent-room-only-while-the-keep-line-narrows
  (<- kept MeasuredLoad (measured-of (str KEEP-LINE-MIB)))
  (assert (= kept.memory-available-MiB 4000) kept)
  (assert (= kept.urgent-memory-available-MiB 40000) kept)
  (<- plain MeasuredLoad (measured-of None))
  (assert (= plain.memory-available-MiB 40000) plain)
  (assert (is plain.urgent-memory-available-MiB None) plain))


(deftest test-an-urgent-task-is-placed-under-the-keep-line-and-a-plain-task-waits
  (assert (! (urgent-priority? URGENT-PRIORITY)))
  (assert (not (! (urgent-priority? (- KEEP-NOW-PRIORITY 1)))))
  (<- measured MeasuredLoad (measured-of (str KEEP-LINE-MIB)))
  (<- phases tuple (placed-phases measured))
  (assert (= (get phases 0) "assigned") phases)
  (assert (= (get phases 1) "queued") phases))


(deftest test-without-the-urgent-room-the-urgent-task-waits-behind-the-keep-line
  ;; 失敗ケース: urgent の空きを名乗らない worker(直す前の形)では、急ぎの task も線の上の空き 4000 − 1536 MiB に入らず待つ。
  (<- measured MeasuredLoad (measured-of (str KEEP-LINE-MIB)))
  (<- phases tuple (placed-phases (replace measured :urgent-memory-available-MiB None)))
  (assert (= phases #("queued" "queued")) phases))


(deftest test-the-urgent-memory-room-counts-the-urgent-jobs-room-with-the-same-floors
  (val measured (MeasuredLoad :at-ms 0 :cpus 8 :cpu-busy 0.1 :memory-total-MiB 100000 :memory-available-MiB 4000
                              :jobs-room-MiB 1000 :urgent-memory-available-MiB 40000 :urgent-jobs-room-MiB 20000))
  (<- plain int (memory-room-mib measured 0))
  (<- urgent int (memory-room-mib measured 0 :urgent True))
  (assert (= plain (- 1000 JOBS-ROOM-FLOOR-MIB)) plain)
  (assert (= urgent (- 20000 JOBS-ROOM-FLOOR-MIB)) urgent)
  ;; jobs の層の urgent を名乗らない worker は平常の jobs の層の空きで切る。
  (<- half int (memory-room-mib (replace measured :urgent-jobs-room-MiB None) 0 :urgent True))
  (assert (= half (- 1000 JOBS-ROOM-FLOOR-MIB)) half)
  (assert (= (! (memory-room-mib (replace measured :jobs-room-MiB None :urgent-jobs-room-MiB None) 0 :urgent True))
             (- 40000 MEMORY-FLOOR-MIB))))
