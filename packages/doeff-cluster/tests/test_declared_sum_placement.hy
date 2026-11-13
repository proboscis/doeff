;;; 置き方の切り替え(ADR-DOE-CLUSTER-005 R4〜R6・R8 — 2026-11-12)の検: coordinator は task を、worker の tasks 区画に置いた task の申告の和で
;;; 置き、測った負荷(CPU の使い・memory の空き・PSI)・床・予約を読まない。
;;;   (1) law placement-reads-claims-not-measurements: 同じ区画と申告なら、測った負荷が空いていても埋まっていても同じ置き先になり、
;;;       どの区画も申告の和が宣言を越えない
;;;   (2) 門の内の task の待ちの理由は TASK-NO-COMPARTMENT(区画を持つ合う worker が無い)と TASK-COMPARTMENT-FULL(和が越える)
;;;   (3) law ungated-work-never-waits-for-room: 配備の形(ungated・優先 1)の task は、区画が全部埋まり区画の無い worker が在っても最初の拍に
;;;       置かれる(入る区画が無ければ優先の低い譲る task を止め、止める先も無ければ和を越えて置く)
;;;   (4) 急ぎの門の内の task(会話を開く処理の今の形 — 優先 1)は、入らなければ優先の低い譲る task を止めて同じ拍に置かれる
;;;   (5) 積む時の断りは tasks 区画を読む(門の外の task と、区画を持つ合う worker の無い間は断らない)・申告の上限は合う区画の最大
;;; 失敗ケース = 直す前の形(測った空きで置く — 機体の memory の空きが 0 や CPU の待ちの率が 40 % を越えると、配備も会話を開く処理も
;;; 待った)。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo TaskLimit GroupLimit]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text memory-need-refusal])
(import doeff_cluster._coordinator.core.task_claims [tasks-compartments-of compartment-clamp task-need-of TASK-CPU-MILLIS])
(import doeff_cluster._coordinator.core.measured_room [STARVE-WAIT-MS])
(import doeff_cluster._coordinator.intent.cluster_model [TaskUnplacedKind])
(import doeff_cluster._shared.intent.compartment_model [Compartment MachineCompartments])
(import doeff_hy._table [Table TableWrite table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
(val NEEDS #("net"))
(val DEPLOY "deploy")
(val GROUPS (table-of #((TableWrite DEPLOY (GroupLimit :group DEPLOY :scope "cluster" :limit 10)))))
;; 空いた機体の読みと、埋まった機体の読み(memory の空き 0・CPU の使い 100 %・PSI の待ち — 直す前の形では 0 本しか置けない)。
(val IDLE (MeasuredLoad :at-ms 0 :cpus 8 :cpu-busy 0.0 :memory-total-MiB 16384 :memory-available-MiB 16384
                        :cpu-some-avg10 0.0 :memory-some-avg10 0.0))
(val CROWDED (MeasuredLoad :at-ms 0 :cpus 8 :cpu-busy 1.0 :memory-total-MiB 16384 :memory-available-MiB 0
                           :cpu-some-avg10 90.0 :memory-some-avg10 50.0))


(defk worker-of [name measured]
  {:pre [(: name str) (: measured (| MeasuredLoad None))] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 net を名乗る生きた worker name(測った負荷 measured)を作るため。"
  (WorkerInfo name NEEDS NOW :versions PY3 :measured measured))


(defk declared [sizes]
  {:pre [(: sizes dict)] :post [(: % Table)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker の名 → #(memoryMiB cpuMillis) から、worker ごとに tasks 区画 1 つ(層 jobs)の machine の行を持つ表を作るため。"
  (table-of (tuple (gfor #(name #(memory cpu)) (sorted (.items sizes))
                         (TableWrite (+ "m-" name)
                                     (MachineCompartments :machine (+ "m-" name)
                                                          :compartments #((Compartment :use "tasks" :worker name :layer "jobs"
                                                                                       :memory-mib memory :memory-limit-mib memory
                                                                                       :cpu-millis cpu))))))))


(defk queued [id * [priority 0] [memory-mib 1024] [ungated False]]
  {:pre [(: id str) (: priority int) (: memory-mib (| int None)) (: ungated bool)] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 net を要る、待っている切り離した task を作るため(門の外の task は要りを名乗らず組 deploy を名乗る)。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 (+ NOW 15000) 0 :detached True :key id :priority priority
              :memory-mib (if ungated None memory-mib) :ungated ungated
              :limits (if ungated #((TaskLimit :group DEPLOY)) #())))


(defk running [id worker * [priority -1] [memory-mib 1024] [preemptible True]]
  {:pre [(: id str) (: worker str) (: priority int) (: memory-mib int) (: preemptible bool)] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker に置かれて走っている、走らせ直しを名乗る切り離した task(場所を譲る名乗り preemptible)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 (+ NOW 15000) 0 :detached True :key id :priority priority
              :memory-mib memory-mib :rerun True :preemptible preemptible :phase "assigned" :worker worker :started-ms 1000))


(defk state-of [workers sizes rows]
  {:pre [(: workers dict) (: sizes dict) (: rows dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker の名 → WorkerInfo・区画の大きさ sizes(declared)・task の行 rows と組の宣言 GROUPS を持つ状態を作るため。"
  (<- worker-rows (table-from-rows workers))
  (<- tasks (table-from-rows rows))
  (<- compartments Table (declared sizes))
  (replace (ClusterState :workers worker-rows :tasks tasks :machine-compartments compartments) :group-limits GROUPS))


(defk placed-rows [state]
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "状態 state を置き先に 1 拍回し、拍の後の全部の行(id → 行)を得るため。"
  (<- placed (tasks-placed NOW state state.placements T))
  (dfor t (.rows placed) t.id t))


(defk workers-with [measured]
  {:pre [(: measured MeasuredLoad)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "w-a と w-b の 2 台(どちらも測った負荷 measured)を作るため。"
  (<- a WorkerInfo (worker-of "w-a" measured))
  (<- b WorkerInfo (worker-of "w-b" measured))
  {"w-a" a "w-b" b})


(deftest test-placement-reads-claims-not-measurements
  ;; w-a は 2 本分(2048 MiB・2 core)、w-b は 1 本分の区画。置いてある w-b の 1 本で w-b は埋まっている。待っている 3 本のうち 2 本が
  ;; w-a に入り、1 本は待つ — 測った負荷が空きでも埋まりでも、同じ置き先と同じ待ちの理由。
  (val sizes {"w-a" #(2048 2000) "w-b" #(1024 1000)})
  (<- held TaskRecord (running "held" "w-b" :priority 0 :preemptible False))
  (var answers [])
  (for [load #(IDLE CROWDED)]
    (<- workers dict (workers-with load))
    (<- q1 TaskRecord (queued "q1"))
    (<- q2 TaskRecord (queued "q2"))
    (<- q3 TaskRecord (queued "q3"))
    (<- state ClusterState (state-of workers sizes {"held" held "q1" q1 "q2" q2 "q3" q3}))
    (<- rows dict (placed-rows state))
    (:= answers [#* answers (dfor #(id t) (.items rows) id #(t.phase t.worker t.detail))]))
  (val idle (get answers 0))
  (val crowded (get answers 1))
  (assert (= idle crowded) answers)
  (assert (= #((get (get idle "q1") 0) (get (get idle "q1") 1)) #("assigned" "w-a")) idle)
  (assert (= #((get (get idle "q2") 0) (get (get idle "q2") 1)) #("assigned" "w-a")) idle)
  (assert (= (get (get idle "q3") 0) "queued") idle)
  (<- full str (task-unplaced-text TaskUnplacedKind.TASK-COMPARTMENT-FULL))
  (assert (in full (get (get idle "q3") 2)) idle)
  ;; どの区画も申告の和(1024 MiB・1000 millicore の task の数)が宣言を越えない。
  (for [#(name #(memory cpu)) (.items sizes)]
    (val count (len (lfor #(id answer) (.items idle) :if (and (= (get answer 1) name) (!= (get answer 0) "queued")) id)))
    (assert (<= (* count 1024) memory))
    (assert (<= (* count TASK-CPU-MILLIS) cpu))))


(deftest test-a-gated-task-waits-for-a-compartment-and-names-the-reason
  ;; 区画を持つ合う worker が無い: TASK-NO-COMPARTMENT で待つ(測った空きが広くても置かない — R2)。
  (<- workers dict (workers-with IDLE))
  (<- q TaskRecord (queued "q"))
  (<- bare ClusterState (state-of workers {} {"q" q}))
  (<- rows dict (placed-rows bare))
  (<- none-text str (task-unplaced-text TaskUnplacedKind.TASK-NO-COMPARTMENT))
  (assert (= (. (get rows "q") phase) "queued"))
  (assert (in none-text (. (get rows "q") detail)) (. (get rows "q") detail))
  ;; 区画の memory に入らない申告(区画 1024 MiB に 2048 MiB): TASK-COMPARTMENT-FULL。
  (<- big TaskRecord (queued "big" :memory-mib 2048))
  (<- small ClusterState (state-of workers {"w-a" #(1024 4000)} {"big" big}))
  (<- small-rows dict (placed-rows small))
  (<- full-text str (task-unplaced-text TaskUnplacedKind.TASK-COMPARTMENT-FULL))
  (assert (= (. (get small-rows "big") phase) "queued"))
  (assert (in full-text (. (get small-rows "big") detail))))


(deftest test-ungated-work-never-waits-for-room
  ;; 区画は全部埋まっている(w-a は置いた譲らない task で満杯)・w-b は区画を持たない・測った負荷は埋まっている。配備の形の task
  ;; (ungated・優先 1)は最初の拍に置かれる — 入る区画も譲らせる先も無いので、足りなさの最も小さい worker に和を越えて置く。
  (<- workers dict (workers-with CROWDED))
  (<- held TaskRecord (running "held" "w-a" :priority 0 :preemptible False))
  (<- deploy TaskRecord (queued "deploy" :priority 1 :ungated True))
  (<- state ClusterState (state-of workers {"w-a" #(1024 1000)} {"held" held "deploy" deploy}))
  (<- rows dict (placed-rows state))
  (val placed (get rows "deploy"))
  (assert (= placed.phase "assigned") placed.detail)
  ;; w-a の足りなさ(申告 1024 MiB・1 core を越える分)は、区画の無い w-b の足りなさ(申告の全部)と同じ — memory、CPU、名の順で w-a。
  (assert (= placed.worker "w-a"))
  (assert (in "和を越えて置いた" placed.detail) placed.detail)
  ;; 区画がどこにも無くても置く(R6 — 門の外の task は区画を要らない)。
  (<- nowhere ClusterState (state-of workers {} {"deploy" deploy}))
  (<- bare-rows dict (placed-rows nowhere))
  (assert (= (. (get bare-rows "deploy") phase) "assigned"))
  ;; 入る区画が無く、優先の低い譲る task が在れば、その task を止めて置く(止めた task は次の試行の待ちの行へ戻る)。
  (<- low TaskRecord (running "low" "w-a" :priority -1))
  (<- yielding ClusterState (state-of workers {"w-a" #(1024 1000)} {"low" low "deploy" deploy}))
  (<- yield-rows dict (placed-rows yielding))
  (assert (= (. (get yield-rows "deploy") phase) "assigned"))
  (assert (= (. (get yield-rows "deploy") worker) "w-a"))
  (assert (not-in "low" yield-rows))
  (val retried (lfor t (.values yield-rows) :if (= t.preempted-by "deploy") t))
  (assert (= (len retried) 1) yield-rows)
  (assert (= (. (get retried 0) phase) "queued")))


(deftest test-an-urgent-gated-task-stops-preemptible-lower-tasks-and-otherwise-keeps-its-claim
  ;; 会話を開く処理の今の形(門の内・優先 1): 区画に入らなければ、優先の低い譲る task を止めて同じ拍に置く(R6)。
  (<- workers dict (workers-with IDLE))
  (<- low TaskRecord (running "low" "w-a" :priority -1))
  (<- opening TaskRecord (queued "opening" :priority 1))
  (<- state ClusterState (state-of workers {"w-a" #(1024 1000)} {"low" low "opening" opening}))
  (<- rows dict (placed-rows state))
  (assert (= (. (get rows "opening") phase) "assigned"))
  (assert (in "場所を譲らせて置いた" (. (get rows "opening") detail)))
  ;; 譲る task が無ければ待つが、区画に申告を取っておき、後ろの小さい task はその分を使わない。
  (<- held TaskRecord (running "held" "w-a" :priority 0 :preemptible False))
  (<- later TaskRecord (queued "z-later" :priority 0 :memory-mib 512))
  (<- kept ClusterState (state-of workers {"w-a" #(2048 4000)} {"held" held "opening" (replace opening :memory-mib 2048) "z-later" later}))
  (<- kept-rows dict (placed-rows kept))
  (assert (= (. (get kept-rows "opening") phase) "queued"))
  (assert (in "取っておく" (. (get kept-rows "opening") detail)) (. (get kept-rows "opening") detail))
  (assert (= (. (get kept-rows "z-later") phase) "queued")))


(deftest test-a-long-waiting-task-keeps-its-claim-so-later-smaller-tasks-do-not-pass-it
  ;; R6 の長く待った task の取っておき(区画の申告の和で数える): w-a の区画 4096 MiB に譲らない task 2048 MiB が走っている。先に積んだ
  ;; 3072 MiB の task は入らず、後に積んだ 1024 MiB の task は入る。失敗ケース: 取っておきが無いと、走る task が終わって返る区画を後ろの
  ;; 小さい task が先に取り続け、先に積んだ task は何時間も置かれない。
  (<- workers dict (workers-with IDLE))
  (<- held TaskRecord (running "held" "w-a" :priority 0 :memory-mib 2048 :preemptible False))
  (<- old TaskRecord (queued "a-old" :memory-mib 3072))
  (<- new TaskRecord (queued "z-new" :memory-mib 1024))
  ;; 先の task が STARVE-WAIT-MS より短く待った間は取っておかず、後ろの小さい task は空いた区画に置かれる。
  (<- fresh ClusterState (state-of workers {"w-a" #(4096 4000)} {"held" held "a-old" old "z-new" new}))
  (<- fresh-rows dict (placed-rows fresh))
  (assert (= (. (get fresh-rows "a-old") phase) "queued"))
  (assert (= (. (get fresh-rows "z-new") phase) "assigned"))
  ;; STARVE-WAIT-MS を越えて待った先の task は、置いた task が終われば入る w-a に申告を取っておき、後ろの小さい task はその分を使わない。
  (val starved (replace old :submitted-ms (- NOW STARVE-WAIT-MS 1)))
  (<- kept ClusterState (state-of workers {"w-a" #(4096 4000)} {"held" held "a-old" starved "z-new" new}))
  (<- kept-rows dict (placed-rows kept))
  (assert (= (. (get kept-rows "a-old") phase) "queued"))
  (assert (in "w-a の tasks 区画にこの task の申告を取っておく" (. (get kept-rows "a-old") detail)) (. (get kept-rows "a-old") detail))
  (assert (= (. (get kept-rows "z-new") phase) "queued") (. (get kept-rows "z-new") detail))
  ;; 走る task が終わって区画が返ると、先の task がその拍に置かれる(後ろの task は残りの 1024 MiB に入る)。
  (<- freed ClusterState (state-of workers {"w-a" #(4096 4000)} {"a-old" starved "z-new" new}))
  (<- freed-rows dict (placed-rows freed))
  (assert (= (. (get freed-rows "a-old") phase) "assigned"))
  (assert (= (. (get freed-rows "a-old") worker) "w-a")))


(deftest test-the-submit-refusal-and-the-claim-clamp-read-the-tasks-compartments
  (<- workers dict (workers-with IDLE))
  (<- state ClusterState (state-of workers {"w-a" #(4096 2000) "w-b" #(2048 1000)} {}))
  ;; 申告の上限 = 合う worker の tasks 区画の memoryMiB の最大。
  (val compartments (tasks-compartments-of state))
  (assert (= (compartment-clamp state compartments NEEDS) 4096))
  (assert (is (compartment-clamp state compartments #("gpu")) None))
  (<- huge TaskRecord (queued "huge" :memory-mib 9000))
  (<- clamped int (task-need-of huge 4096 {}))
  (assert (= clamped 9000))
  ;; 積む時: 門の内の task の見当がどの区画よりも大きい → 断る。入る大きさ・門の外の task・区画を持つ合う worker の無い能力は断らない。
  (<- too-big (| str None) (memory-need-refusal state NEEDS 9000 "" False))
  (assert (is-not too-big None))
  (assert (in "tasks 区画" too-big))
  (<- fits (| str None) (memory-need-refusal state NEEDS 4096 "" False))
  (assert (is fits None))
  (<- ungated (| str None) (memory-need-refusal state NEEDS None "" True))
  (assert (is ungated None))
  (<- elsewhere (| str None) (memory-need-refusal state #("gpu") 9000 "" False))
  (assert (is elsewhere None)))
