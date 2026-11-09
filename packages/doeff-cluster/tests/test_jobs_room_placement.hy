;;; task の置き先が、worker の名乗る task の層(jobs)の今の空きを越える要りの task を置かない事の検(2026-11-09)。実例: atlas の
;;; jobs の層の memory.max は、追い出しまでの空きから余裕・会話の CLI の層への貸しを引いた値で、11-08 14:04 は上限 3471 MiB・使い
;;; 1959 MiB(層の空き 1512 MiB)だった。置き先は追い出しまでの空き 7186 MiB − 床 1536 MiB だけを見て要り 3584 MiB の task を置き、
;;; kernel が jobs の層の OOM で task の中の最大の process を止めた(同じ形で 3 回続いた)。
;;;   (1) 反例の筋書き: 11-08 14:04 の atlas の測りで、層の空きを名乗らない数え(直す前)は要り 3584 MiB の task を置き、層の空きを名乗る
;;;       数えは置かずに queued で待たせ、待ちの文は memory の空きの無さを名指す。層が空けば置く
;;;   (2) 空き(memory-room-mib)は 追い出しまでの空き − 床 と 層の空き − 層の床 の小さい方から、置いた task の予約を引いた値
;;;   (3) worker は新しい線と、上限と使いの両方を読めた回だけ層の空きを名乗り、heartbeat と GET /state に載る
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [WorkerInfo ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB JOBS-ROOM-FLOOR-MIB memory-room-mib])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text register-heartbeat state-view])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])
(import doeff_cluster._worker.core.eviction_rules [EvictionLine LineKind])
(import doeff_cluster._worker.core.load_rules [MachineLoad jobs-room-of])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import doeff_hy._table [table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM heartbeat-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val LONG-LEASE-MS (* 3600 1000))
(val PY3 #((ComponentVersion "python" "3")))
;; 11-08 14:04〜14:09 の atlas の worker の log の値(MiB)。
(val LINE-MIB 20263)
(val EVICTION-ROOM-MIB 7186)
(val JOBS-LIMIT-MIB 3471)
(val JOBS-CURRENT-MIB 1959)
;; 着地の宣言の task の要り。
(val DECLARE-NEED 3584)


(defk declare-task [id]
  {:pre [(: id str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-atlas を要る、要り DECLARE-NEED MiB の待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-atlas") LONG-LEASE-MS LONG-LEASE-MS 0 :detached True :key id
              :memory-mib DECLARE-NEED))


(defk atlas-measured [jobs-room]
  {:pre [(: jobs-room (| int None))] :post [(: % MeasuredLoad)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "11-08 14:04 の atlas の測り(追い出しまでの空き EVICTION-ROOM-MIB・CPU は空いている)に、jobs の層の空き jobs-room(None = 名乗らない
   — 直す前の worker)を添えた形を作るため。"
  (MeasuredLoad :at-ms 0 :cpus 16 :cpu-busy 0.1 :memory-total-MiB LINE-MIB :memory-available-MiB EVICTION-ROOM-MIB
                :cpu-some-avg10 0.0 :memory-some-avg10 0.0 :task-memory-MiB {} :jobs-room-MiB jobs-room))


(defk placed-on-atlas [jobs-room]
  {:pre [(: jobs-room (| int None))] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "atlas だけが jobs の層の空き jobs-room を名乗る世界で、宣言の task 1 本を置き先に 1 拍回した後の task の行を得るため。"
  (val atlas-row (WorkerInfo "atlas" #("host-atlas") 0 :versions PY3 :measured (! (atlas-measured jobs-room))))
  (<- workers (table-from-rows {"atlas" atlas-row}))
  (<- task TaskRecord (declare-task "declare"))
  (<- tasks (table-from-rows {"declare" task}))
  (val state (ClusterState #() workers (table-of #()) tasks))
  (val placed (! (tasks-placed 0 state state.placements T)))
  (.row placed "declare"))


(deftest test-a-task-larger-than-the-jobs-layer-room-waits-instead-of-being-killed
  ;; 直す前の数え(層の空きを名乗らない worker と同じ)は、追い出しまでの空き 7186 − 床 1536 = 5650 MiB を見て要り 3584 MiB を置く。
  (<- before TaskRecord (placed-on-atlas None))
  (assert (= #(before.phase before.worker) #("assigned" "atlas")) before)
  ;; 層の空き 3471 − 1959 = 1512 MiB を名乗れば置かず、queued で待たせ、待ちの文は空きの無さを名指す(層の空き − 層の床 1000 MiB は
  ;; memory の枠 1 つに満たないので、数の空きも 0 — 待ちの文は数の空きの無さ。どちらも失敗にしない)。
  (<- after TaskRecord (placed-on-atlas (- JOBS-LIMIT-MIB JOBS-CURRENT-MIB)))
  (<- no-room str (task-unplaced-text TaskUnplacedKind.TASK-NO-ROOM))
  (<- no-memory str (task-unplaced-text TaskUnplacedKind.TASK-NO-MEMORY))
  (assert (= after.phase "queued") after)
  (assert (in after.detail #(no-room no-memory)) after)
  ;; 枠が残る程度の層の空き(要りには足りない)では、待ちの文は memory の空きの無さを名指す。
  (<- short TaskRecord (placed-on-atlas (+ 2048 JOBS-ROOM-FLOOR-MIB)))
  (assert (= #(short.phase short.detail) #("queued" no-memory)) short)
  ;; 層が要り + 層の床まで空けば置く。
  (<- roomy TaskRecord (placed-on-atlas (+ DECLARE-NEED JOBS-ROOM-FLOOR-MIB)))
  (assert (= #(roomy.phase roomy.worker) #("assigned" "atlas")) roomy))


(deftest test-the-memory-room-is-the-smaller-of-the-line-and-the-jobs-layer-less-the-reservations
  (val line-room (- EVICTION-ROOM-MIB MEMORY-FLOOR-MIB))
  ;; 層の空きを名乗らない worker は追い出しまでの空きだけで数える(前の版の worker と同じ)。
  (<- unnamed int (memory-room-mib (! (atlas-measured None)) 700))
  (assert (= unnamed (- line-room 700)) unnamed)
  ;; 層の空きが小さければ層の空き − 層の床 − 予約。負は負のまま(GET /state で詰めすぎを読める)。
  (<- tight int (memory-room-mib (! (atlas-measured 1512)) 700))
  (assert (= tight (- 1512 JOBS-ROOM-FLOOR-MIB 700)) tight)
  (<- squeezed int (memory-room-mib (! (atlas-measured -188)) 0))
  (assert (= squeezed (- -188 JOBS-ROOM-FLOOR-MIB)) squeezed)
  ;; 層の空きが大きければ追い出しまでの空きが決める(層の上限が線より緩い機体)。
  (<- wide int (memory-room-mib (! (atlas-measured 20000)) 700))
  (assert (= wide (- line-room 700)) wide)
  ;; 測りの無い worker は 0。
  (<- unmeasured int (memory-room-mib None 0))
  (assert (= unmeasured 0)))


(deftest test-the-worker-names-the-jobs-layer-room-in-the-heartbeat-and-state-shows-it
  (val line (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes (* LINE-MIB MIB) :used-bytes (* (- LINE-MIB EVICTION-ROOM-MIB) MIB)
                          :room-bytes (* EVICTION-ROOM-MIB MIB)))
  ;; 新しい線と、上限と使いの両方を読めた回だけ名乗る。上限を使いより下げた続きの中は負。
  (<- named (| int None) (jobs-room-of line (* JOBS-LIMIT-MIB MIB) (* JOBS-CURRENT-MIB MIB)))
  (<- negative (| int None) (jobs-room-of line (* 3173 MIB) (* 3361 MIB)))
  (<- no-line (| int None) (jobs-room-of None (* JOBS-LIMIT-MIB MIB) (* JOBS-CURRENT-MIB MIB)))
  (<- no-limit (| int None) (jobs-room-of line None (* JOBS-CURRENT-MIB MIB)))
  (<- no-current (| int None) (jobs-room-of line (* JOBS-LIMIT-MIB MIB) None))
  (assert (= #(named negative no-line no-limit no-current) #(1512 -188 None None None)))
  ;; heartbeat の measured に欄 jobsRoomMiB で載り(名乗らない時は欄が無い)、coordinator は受けて置き先と GET /state に使う。
  (val load (MachineLoad :at-ms 0 :cpus 16 :cpu-busy 0.1 :memory-total-mib LINE-MIB :memory-available-mib EVICTION-ROOM-MIB
                         :cpu-some-avg10 0.0 :cpu-full-avg10 None :memory-some-avg10 0.0 :memory-full-avg10 None))
  (<- bare dict (measured-json load))
  (assert (not-in "jobsRoomMiB" bare) bare)
  (<- spelled dict (measured-json (replace load :jobs-room-mib named)))
  (assert (= (get spelled "jobsRoomMiB") 1512) spelled)
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "atlas" "provides" ["host-atlas"] "measured" spelled})) 10)))
  (assert (= (. (.row heard.workers "atlas") measured jobs-room-MiB) 1512))
  (val view (get (! (state-view-json (! (state-view heard 20 T)))) "workers" "atlas"))
  (assert (= (get view "measured" "jobsRoomMiB") 1512) view)
  (assert (= (get view "taskMemoryRoomMiB") (- 1512 JOBS-ROOM-FLOOR-MIB)) view)
  ;; 欄の無い heartbeat(前の版の worker)は名乗らない。
  (val old (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "atlas" "provides" ["host-atlas"] "measured" bare})) 10)))
  (assert (is (. (.row old.workers "atlas") measured jobs-room-MiB) None)))
