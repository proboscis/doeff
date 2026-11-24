;;; 区画の持つ clis の層の貸し(ADR-DOE-CLUSTER-005 R13・worker/core/eviction_rules の頭の註 — 2026-11-15)の検: 区画(用途ごとに cgroup の
;;; 上限を固定する宣言)が clis の層を持つ worker でも、clis の層の上限と使いの差を借りの task に貸し、上に載る系のターンが memory を
;;; 求めた時に借りの task から取り返す。ターンは memory の不足で待たない(clis の層の memory.max は区画の上限のまま)。
;;;   (1) worker の模型(模擬の cgroup の file と本物の見張りの回): 区画の持つ clis の層の memory.max は区画の上限のまま、上限 − 使い −
;;;       1 本の要り ÷ CLIS-ROOM-SHARE を貸しとして数え、heartbeat が hostedLendMiB で名乗る。借りの task が置かれると jobs の層の
;;;       memory.max を区画の上限 + 区画を越えた借りの要りに広げ、借りが止まると今の使いより下げずに区画の上限へ戻す
;;;   (2) 置き先: tasks 区画が申告で埋まった時、場所を譲る名乗りの task は貸しの分だけ借りとして置かれ、譲れない task は待つ
;;;   (3) 取り返し: ターンが始まって clis の使いが伸び貸しが縮むと、置き先は貸しを越えた借りの task を止めて待ちの行へ戻す。その間も
;;;       clis の層の上限は区画の値で、ターンの受け手が読む空きは 1 本の要りを越える
;;; 失敗ケース = 直す前の形: 区画の持つ clis の層は貸しを数えず(clis-held-lend が None)、借りの task は 1 本も置かれなかった。
(require doeff-hy.macros [defk deftest <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff [with-handlers])
(import doeff_core_effects [state slog-discard-handler memory-file-handler MemoryFiles])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy._wire [parse])
(import doeff_hy._table [Table TableWrite table-of])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad StatusRow])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed tasks-for adopted-task])
(import doeff_cluster._coordinator.protocol.replies [task-offer-json])
(import doeff_cluster._shared.intent.compartment_model [Compartment MachineCompartments HeldCompartment])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.core.eviction_rules [CLI-TURN-BYTES CLIS-ROOM-SHARE clis-held-lend held-jobs-limit task-needs-of])
(import doeff_cluster._worker.core.load_rules [MachineLoad held-lend-told])
(import doeff_cluster._worker.protocol.eviction_line [EvictionCell EvictionSettings])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])
(import tests.test_eviction_cli_layer [node-a-files node-a-api ticked-files content-at CLIS JOBS OWN STAT])
(import tests.test_lend_clis_turn_room [lend-settings])

(val GIB (* 1024 MIB))
;; 模擬の機体の区画: tasks(jobs の層)4096 MiB・hosted clis 8192 MiB。clis の使い 1 GiB の間の貸しは 8192 − 1024 − 2048 = 5120 MiB。
(val TASKS-MIB 4096)
(val CLIS-MIB 8192)
(val HELD #((HeldCompartment :use "tasks" :layer "jobs" :memory-mib TASKS-MIB :memory-limit-mib TASKS-MIB :cpu-millis 4000)
            (HeldCompartment :use "hosted" :layer "clis" :memory-mib CLIS-MIB :memory-limit-mib CLIS-MIB :cpu-millis 0)))
(val KEEP-MIB (int (// (- (// (- CLI-TURN-BYTES) CLIS-ROOM-SHARE)) MIB)))
(val LEND-MIB (- CLIS-MIB 1024 KEEP-MIB))
(val BORROW-MIB 3072)


;; --- worker の模型 -------------------------------------------------------------------------------------------------------------------

(defk held-settings []
  {:pre [] :post [(: % EvictionSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の機体の線の設定に、区画を層へ書く dir(container の cgroup)を足すため(1 本の要りを持つ clis の層 — lend-settings)。"
  (<- base EvictionSettings (lend-settings))
  (replace base :layers-dir OWN))


(defk worker-round [files cell]
  {:pre [(: files MemoryFiles) (: cell EvictionCell)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の cgroup の file files の上で、本物の見張りの回の頭を 2 回まわし、その後の file を読むため。"
  (<- settings EvictionSettings (held-settings))
  (<- after MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (node-a-api)
                                        slog-discard-handler]
                          (ticked-files settings cell 2)))
  after)


(defk limits-mib [files]
  {:pre [(: files MemoryFiles)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の file の clis と jobs の層の memory.max(MiB)を読むため。"
  (<- clis str (content-at files (+ CLIS "/memory.max")))
  (<- jobs str (content-at files (+ JOBS "/memory.max")))
  #((// (int clis) MIB) (// (int jobs) MIB)))


(defk machine [* [clis GIB] [jobs (// GIB 2)]]
  {:pre [(: clis int) (: jobs int)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の機体の cgroup の file を作るため: Pod の和 12 GiB と、clis と jobs の層の今の使い(container はその和 + 1.5 GiB)。"
  (<- files MemoryFiles (node-a-files (* 12 GIB) jobs clis (+ clis jobs (* 3 (// GIB 2)))))
  files)


(defk used [files * [jobs (// GIB 2)]]
  {:pre [(: files MemoryFiles) (: jobs int)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の file の jobs の層の今の使いだけを替えた file を作るため(見張りが書いた上限は残す — 同じ機体の次の読み)。"
  (val texts {(+ JOBS "/memory.current") (str jobs) (+ JOBS "/memory.stat") (.format STAT jobs)})
  (replace files :files (tuple (gfor f files.files
                                     (if (in f.path texts) (replace f :content (.encode (get texts f.path))) f)))))


(deftest test-a-held-clis-compartment-lends-its-unused-room-to-borrowed-tasks
  ;; (1) 区画が clis の層を持つ worker: clis の memory.max は区画の 8192 MiB のまま、貸しは上限 − 使い 1024 − 1 本 ÷ 0.5 = 5120 MiB。
  (val cell (EvictionCell))
  (setv cell.compartments HELD)
  (<- start MemoryFiles (machine))
  (<- files MemoryFiles (worker-round start cell))
  (<- before tuple (limits-mib files))
  (assert (= before #(CLIS-MIB TASKS-MIB)) before)
  (assert (= cell.clis-held-lend (* LEND-MIB MIB)) cell.clis-held-lend)
  ;; heartbeat は貸しを hostedLendMiB で名乗り、coordinator の受け口が同じ値を読む。上に載る系が待つ間(取り返しの印が新しい)は 0。
  (<- told (| int None) (held-lend-told False cell.clis-held-lend))
  (<- waiting (| int None) (held-lend-told True cell.clis-held-lend))
  (assert (= #(told waiting) #(LEND-MIB 0)) #(told waiting))
  (<- spelled dict (measured-json (MachineLoad :at-ms 6000 :cpus 4 :cpu-busy 0.0 :memory-total-mib 16384 :memory-available-mib 8192
                                                :cpu-some-avg10 None :cpu-full-avg10 None :memory-some-avg10 None :memory-full-avg10 None
                                                :hosted-lend-mib told)))
  (<- read MeasuredLoad (parse MeasuredLoad spelled))
  (assert (= read.hosted-lend-MiB LEND-MIB) spelled)
  ;; 置き先が区画を埋める 4096 MiB の task と借りの 3072 MiB の task を置いた返事: coordinator の tasks-for → task-offer-json で組んだ行に
  ;; borrowed が載り、worker の task-needs-of がその行から borrowed を読んで jobs の層を 4096 + 3072 MiB に広げ、clis は区画のまま。
  (<- worker WorkerInfo (node-c LEND-MIB))
  (<- rows dict (placed-rows worker {"t-plain" (running "t-plain") "t-borrow" (queued "t-borrow")}))
  (<- offered tuple (offered-rows worker rows))
  (val by-id (dfor row offered (get row "id") row))
  (assert (not-in "borrowed" (get by-id "t-plain")) (get by-id "t-plain"))
  (assert (is (.get (get by-id "t-borrow") "borrowed") True) (get by-id "t-borrow"))
  (<- needs tuple (task-needs-of offered))
  (val need-by-id (dfor n needs n.id n))
  (assert (= #((. (get need-by-id "t-plain") borrowed) (. (get need-by-id "t-borrow") borrowed)) #(False True)) need-by-id)
  (setv cell.needs needs)
  (<- lent-files MemoryFiles (worker-round files cell))
  (<- lent tuple (limits-mib lent-files))
  (assert (= lent #(CLIS-MIB (+ TASKS-MIB BORROW-MIB))) lent)
  ;; 状態を失った coordinator が、worker が報告へ写した task-offer-json の行から走っている task を引き取る時も borrowed を保つ。
  (val adopted-borrow (adopted-task (ClusterState) "node-c" None (StatusRow :name "task/t-borrow" :phase "running" :task (get by-id "t-borrow")) NOW))
  (val adopted-plain (adopted-task (ClusterState) "node-c" None (StatusRow :name "task/t-plain" :phase "running" :task (get by-id "t-plain")) NOW))
  (assert (and (is-not adopted-borrow None) (is adopted-borrow.borrowed True)) adopted-borrow)
  (assert (and (is-not adopted-plain None) (is adopted-plain.borrowed False)) adopted-plain)
  ;; 失敗ケース(直す前の形): 区画の持つ clis の層の貸しは数えず、jobs の層は区画の値のまま(借りの task が来ても広げない)。
  (val bare (EvictionCell))
  (setv bare.compartments #((get HELD 0)))
  (<- bare-files MemoryFiles (worker-round start bare))
  (assert (is bare.clis-held-lend None) bare.clis-held-lend))


(deftest test-the-jobs-layer-returns-to-the-compartment-only-after-the-borrowed-task-leaves
  ;; 借りが止まった返事の後も、jobs の層の今の使いが残る間は使いより下げない(止めた process が退き終えるまで残る task を OOM で止めない)。
  (<- staying int (held-jobs-limit (* TASKS-MIB MIB) 0 (* 7168 MIB) (* 6 GIB)))
  (<- gone int (held-jobs-limit (* TASKS-MIB MIB) 0 (* 7168 MIB) (* 3 GIB)))
  (<- unread int (held-jobs-limit (* TASKS-MIB MIB) 0 (* 7168 MIB) None))
  (<- raised int (held-jobs-limit (* TASKS-MIB MIB) (* BORROW-MIB MIB) None None))
  (assert (= (tuple (gfor v #(staying gone unread raised) (// v MIB))) #(6144 TASKS-MIB 7168 7168)))
  ;; 区画の上限を越える使いは 0 を貸す(clis の層の区画が上限)。
  (<- none int (clis-held-lend (* CLIS-MIB MIB) (* 7 GIB) CLI-TURN-BYTES))
  (assert (= none 0) none))


;; --- 置き先と取り返し ----------------------------------------------------------------------------------------------------------------

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
(val NEEDS #("host-node-c"))
(val MACHINES (table-of #((TableWrite "m-node-c"
                                      (MachineCompartments :machine "m-node-c"
                                                           :compartments #((Compartment :use "tasks" :worker "node-c" :layer "jobs"
                                                                                        :memory-mib TASKS-MIB :memory-limit-mib TASKS-MIB
                                                                                        :cpu-millis 4000)
                                                                           (Compartment :use "hosted" :worker "node-c" :layer "clis"
                                                                                        :memory-mib CLIS-MIB :memory-limit-mib CLIS-MIB
                                                                                        :cpu-millis 0)))))))


(defk node-c [lend]
  {:pre [(: lend (| int None))] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "区画の持つ clis の層の貸し lend(MiB — None = 名乗らない)を名乗る生きた worker node-c を作るため。"
  (WorkerInfo "node-c" NEEDS NOW :versions PY3
              :measured (MeasuredLoad :at-ms NOW :cpus 8 :cpu-busy 0.1 :memory-total-MiB 16384 :memory-available-MiB 8192
                                      :hosted-lend-MiB lend)))


(defk queued [id * [preemptible True] [memory-mib BORROW-MIB]]
  {:pre [(: id str) (: preemptible bool) (: memory-mib int)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-c を要る、待っている走らせ直しを名乗る切り離した task(場所を譲る名乗り preemptible)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 (+ NOW 15000) 0 :detached True :key id
              :memory-mib memory-mib :rerun True :preemptible preemptible))


(defk running [id * [borrowed False] [memory-mib TASKS-MIB] [priority 0]]
  {:pre [(: id str) (: borrowed bool) (: memory-mib int) (: priority int)] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-c に置かれて走っている、場所を譲る名乗りの切り離した task(borrowed = 貸しへ借りとして置かれた)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 (+ NOW 15000) 0 :detached True :key id :priority priority
              :memory-mib memory-mib :rerun True :preemptible True :borrowed borrowed :phase "assigned" :worker "node-c" :started-ms 1000))


(defk offered-rows [worker rows]
  {:pre [(: worker WorkerInfo) (: rows dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の tasks-for と task-offer-json で組んだ heartbeat の返事の task の行(JSON の dict の tuple)を得るため。"
  (<- workers (table-from-rows {worker.name worker}))
  (<- tasks (table-from-rows rows))
  (val cluster (ClusterState :workers workers :tasks tasks :machine-compartments MACHINES))
  (var out #())
  (for [offer (tasks-for cluster worker.name)]
    (<- spelled dict (task-offer-json offer))
    (:= out (+ out #(spelled))))
  out)


(defk placed-rows [worker rows]
  {:pre [(: worker WorkerInfo) (: rows dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker 1 台と区画の宣言の状態で task の行 rows(id → 行を作る Program)を置き先に 1 拍回し、拍の後の全部の行(id → 行)を得るため。"
  (<- workers (table-from-rows {worker.name worker}))
  (var made {})
  (for [#(k v) (.items rows)]
    (<- row TaskRecord v)
    (:= made (| made {k row})))
  (<- tasks (table-from-rows made))
  (val cluster (ClusterState :workers workers :tasks tasks :machine-compartments MACHINES))
  (val placed (! (tasks-placed NOW cluster cluster.placements T)))
  (dfor t (.rows placed) t.id t))


(deftest test-a-preemptible-task-borrows-the-held-clis-room-and-a-plain-task-waits
  ;; (2) tasks 区画は走っている 4096 MiB の task で埋まっている。貸し 5120 MiB の node-c に、譲る task 1 本(3072 MiB)が借りとして置かれ、
  ;; 2 本目は貸しを越えるので待ち、譲れない task は貸しを使わずに区画の和で待つ。
  (<- worker WorkerInfo (node-c LEND-MIB))
  (<- rows dict (placed-rows worker {"t-plain" (running "t-plain") "t-b1" (queued "t-b1") "t-b2" (queued "t-b2")
                                     "t-solid" (queued "t-solid" :preemptible False :memory-mib 1024)}))
  (val b1 (get rows "t-b1"))
  (assert (= #(b1.phase b1.worker b1.borrowed) #("assigned" "node-c" True)) b1)
  (assert (in "貸しへ借りとして置いた" b1.detail) b1.detail)
  (val b2 (get rows "t-b2"))
  (val solid (get rows "t-solid"))
  (assert (= #(b2.phase b2.unplaced-kind) #("queued" TaskUnplacedKind.TASK-COMPARTMENT-FULL)) b2)
  (assert (= #(solid.phase solid.borrowed solid.unplaced-kind) #("queued" False TaskUnplacedKind.TASK-COMPARTMENT-FULL)) solid)
  ;; 失敗ケース(直す前の形): 貸しを名乗らない worker には借りも置かれず、譲る task も区画の和で待つ。
  (<- silent WorkerInfo (node-c None))
  (<- before dict (placed-rows silent {"t-plain" (running "t-plain") "t-b1" (queued "t-b1")}))
  (assert (= (. (get before "t-b1") phase) "queued") (get before "t-b1")))


(deftest test-a-turn-takes-the-room-back-from-the-borrowed-task-without-waiting
  ;; (3) 貸した後にターンが始まり、clis の使いが 1 GiB から 4 GiB に伸びた。worker の模型の次の回: clis の memory.max は区画の 8192 MiB の
  ;; まま(受け手の読む空き 4 GiB は 1 本の要りを越える — ターンは待たない)、貸しは 8192 − 4096 − 2048 = 2048 MiB に縮む。
  (val cell (EvictionCell))
  (setv cell.compartments HELD)
  (<- before-worker WorkerInfo (node-c LEND-MIB))
  (<- plain-row TaskRecord (running "t-plain"))
  (<- borrow-row TaskRecord (running "t-borrow" :borrowed True :memory-mib BORROW-MIB))
  (<- before-offered tuple (offered-rows before-worker {"t-plain" plain-row "t-borrow" borrow-row}))
  (<- needs tuple (task-needs-of before-offered))
  (setv cell.needs needs)
  (<- turned MemoryFiles (machine :clis (* 4 GIB) :jobs (* 6 GIB)))
  (<- files MemoryFiles (worker-round turned cell))
  (<- turn-limits tuple (limits-mib files))
  (val clis-room (- (get turn-limits 0) 4096))
  (assert (>= (* clis-room MIB) CLI-TURN-BYTES) turn-limits)
  (<- told (| int None) (held-lend-told False cell.clis-held-lend))
  (assert (= told (- CLIS-MIB 4096 KEEP-MIB)) told)
  ;; 置き先: 区画 4096 + 貸し 2048 に 4096 + 3072 が入らない — 借りの task を止めて次の試行の待ちの行へ戻す(取り返しの印を持つ)。
  ;; 区画の内の task は止めない。積み直した行は貸しに入らないので待つ。
  (<- worker WorkerInfo (node-c told))
  (<- rows dict (placed-rows worker {"t-plain" (running "t-plain") "t-borrow" (running "t-borrow" :borrowed True :memory-mib BORROW-MIB)}))
  (assert (not-in "t-borrow" rows) (sorted rows))
  (val again (lfor t (.values rows) :if (!= t.id "t-plain") t))
  (assert (= (len again) 1) again)
  (val next-row (get again 0))
  (assert (= #(next-row.phase next-row.reclaimed next-row.borrowed next-row.attempt) #("queued" True False 2)) next-row)
  (assert (= (. (get rows "t-plain") phase) "assigned"))
  ;; 上に載る系が memory の不足で仕事を待たせる間(印が新しい)は貸し 0: 区画を越えた借りを全部返す。
  (<- waiting (| int None) (held-lend-told True cell.clis-held-lend))
  (<- wait-worker WorkerInfo (node-c waiting))
  (<- wait-rows dict (placed-rows wait-worker {"t-plain" (running "t-plain" :memory-mib 2048)
                                               "t-borrow" (running "t-borrow" :borrowed True :memory-mib BORROW-MIB)}))
  (assert (not-in "t-borrow" wait-rows) (sorted wait-rows))
  ;; 貸しが借りを覆う間・貸しを名乗らない間は止めない(測りの欠けた拍に借りを止めない)。
  (for [lend #(LEND-MIB None)]
    (<- kept-worker WorkerInfo (node-c lend))
    (<- kept dict (placed-rows kept-worker {"t-plain" (running "t-plain")
                                            "t-borrow" (running "t-borrow" :borrowed True :memory-mib BORROW-MIB)}))
    (assert (= (. (get kept "t-borrow") phase) "assigned") lend))
  ;; worker の模型の次の回: 借りの行が返事から外れた後、jobs の層の使いが 6 GiB 残る間は使いまで、退いた後は区画の 4096 MiB へ戻す。
  (<- plain-offered tuple (offered-rows worker rows))
  (<- plain-needs tuple (task-needs-of plain-offered))
  (setv cell.needs plain-needs)
  (<- leaving MemoryFiles (worker-round files cell))
  (<- leaving-limits tuple (limits-mib leaving))
  (<- left MemoryFiles (used leaving :jobs (* 3 GIB)))
  (<- left-files MemoryFiles (worker-round left cell))
  (<- left-limits tuple (limits-mib left-files))
  (assert (= #(leaving-limits left-limits) #(#(CLIS-MIB 6144) #(CLIS-MIB TASKS-MIB))) #(leaving-limits left-limits)))
