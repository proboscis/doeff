;;; 測った山で数える task の要り(coordinator/core/measured_room の頭の註)の検: 送り手が名乗る memory の要りが実の山より小さくても、
;;; coordinator は worker の測りが名乗る task ごとの今の使いの最大を行の山に持ち、同じ形の task の要りをその山で数える。
;;;   (1) heartbeat の task ごとの今の使いで、その worker に置いた task の山を書き直す(刻み PEAK-STEP-MIB — 伸びるたびには書かない)
;;;   (2) 要り = max(名乗り, min(max(この task の山, 同じ形の山), worker の線 − 床))。形を名乗らない・初めての形は名乗りのまま
;;;   (3) 同じ形の task が実の山まで伸びた worker の空きが山より小さければ、次の同じ形の task をそこに置かない
;;;   (4) 形と山は保存の行に残り、形の綴りの誤りは送る前と受け口で断る
;;; 失敗ケース = node-h の 11-09 16:52 UTC: 名乗る空き約 9,682 MiB の刻に、名乗り 3,584 MiB の task 3 本を置き、8 分後に 2 本が各 14 GiB
;;; を使った(t41112 = 14,238 MiB)。予約は「要り − 今の使い」だけで、名乗りを越えた伸びは次の置きの判断に入らなかった。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text task-peaks-noted shape-peaks-of PEAK-STEP-MIB])
(import doeff_cluster._coordinator.core.measured_room [learned-need MEMORY-FLOOR-MIB])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._shared.core.detached_rules [task-shape-refusal task-placing-json TASK-SHAPE-MAX-LENGTH])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])
(import doeff_hy._table [Table TableWrite])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
;; 本番 node-h の 11-09 16:52 UTC の値: 名乗る空き約 9,682 MiB(memory-room-mib = 名乗る memory の空き − 床)・long-command の名乗り 3,584 MiB・
;; 8 分後の t41112 の使い 14,238 MiB。線の大きさは 100,000 MiB(追い出しの線)。
(val NODE-H-LINE-MIB 100000)
(val NODE-H-ROOM-MIB 9682)
(val DECLARED-MIB 3584)
(val GROWN-MIB 14238)
(val SHAPE "repo-a:cargo test")


(defk measured [available-mib uses]
  {:pre [(: available-mib int) (: uses (| dict None))] :post [(: % MeasuredLoad)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "CPU の空いた 8 core の機体が、memory の空き available-mib と task ごとの今の使い uses を名乗る測りを作るため。"
  (MeasuredLoad :at-ms 0 :cpus 8 :memory-total-MiB NODE-H-LINE-MIB :memory-available-MiB available-mib :cpu-busy 0.1 :cpu-nice 0.0
                :cpu-some-avg10 0.5 :memory-some-avg10 0.0 :task-memory-MiB uses))


(defk task-of [id needs shape]
  {:pre [(: id str) (: needs tuple) (: shape str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 needs を要り、要り DECLARED-MIB と形 shape を名乗る待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 needs 15000 20000 0 :detached True :key id :memory-mib DECLARED-MIB :shape shape))


(defk placed-with [grown queued-shape]
  {:pre [(: grown (| TaskRecord None)) (: queued-shape str)] :post [(: % (get Table TaskRecord))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-h に伸びた task grown(None = 無い)が居て、node-h の名乗る空きが NODE-H-ROOM-MIB の時に、node-h でしか走れない形 queued-shape の task next を
   1 拍で置いた task の表を知るため。"
  (val uses (if (is grown None) {} {grown.id GROWN-MIB}))
  (val node-h (WorkerInfo "node-h" #("host-node-h") 0 :versions PY3 :measured (! (measured (+ NODE-H-ROOM-MIB MEMORY-FLOOR-MIB) uses))))
  (<- workers (table-from-rows {"node-h" node-h}))
  (<- next TaskRecord (task-of "next" #("host-node-h") queued-shape))
  (<- rows (table-from-rows (| {"next" next} (if (is grown None) {} {grown.id grown}))))
  (<- none (table-from-rows {}))
  (val state (ClusterState #() workers none rows))
  (! (tasks-placed 1000 state state.placements T)))


(deftest test-the-heartbeat-raises-the-peak-of-the-tasks-placed-on-the-worker
  (<- base TaskRecord (task-of "a" #("host-node-h") SHAPE))
  (val running (replace base :phase "assigned" :worker "node-h" :started-ms 0))
  (val elsewhere (replace running :id "b" :key "b" :worker "node-c"))
  (val ended (replace running :id "c" :key "c" :phase "finished" :finished-ms 10))
  (<- rows (table-from-rows {"a" running "b" elsewhere "c" ended}))
  ;; 通る例: 山の無い行は今の使い。ほかの worker に置いた行・終わった行は書かない。
  (<- first (get Table TaskRecord) (task-peaks-noted rows "node-h" (! (measured 50000 {"a" 5000 "b" 9000 "c" 9000}))))
  (assert (= #((. (.row first "a") peak-mib) (. (.row first "b") peak-mib) (. (.row first "c") peak-mib)) #(5000 None None)))
  ;; 刻みより小さい伸びは書かない(行を heartbeat ごとに書かない)。刻み以上の伸びは書く。縮んでも山は下げない。
  (<- small (get Table TaskRecord) (task-peaks-noted first "node-h" (! (measured 50000 {"a" (+ 5000 PEAK-STEP-MIB -1)}))))
  (assert (is small first))
  (<- grown (get Table TaskRecord) (task-peaks-noted first "node-h" (! (measured 50000 {"a" GROWN-MIB}))))
  (<- shrunk (get Table TaskRecord) (task-peaks-noted grown "node-h" (! (measured 50000 {"a" 2000}))))
  (assert (= #((. (.row grown "a") peak-mib) (. (.row shrunk "a") peak-mib)) #(GROWN-MIB GROWN-MIB)))
  ;; 使いを名乗らない測り(古い読み・前の版の worker)と測りの無い heartbeat は表のまま。
  (<- silent (get Table TaskRecord) (task-peaks-noted grown "node-h" (! (measured 50000 None))))
  (<- unmeasured (get Table TaskRecord) (task-peaks-noted grown "node-h" None))
  (assert (and (is silent grown) (is unmeasured grown)))
  ;; 形ごとの山は、形を名乗り山を測った行(終わった行を含む)の最大。
  (<- peaks dict (shape-peaks-of (.with-writes grown #((TableWrite "c" (replace ended :peak-mib 20000))))))
  (assert (= peaks {SHAPE 20000}) peaks))


(deftest test-the-need-is-the-larger-of-the-declaration-and-the-measured-peak-cut-at-the-line
  ;; 通る例: 名乗りより大きい山(この task か同じ形)は山。線 − 床より大きい山は線 − 床で切る(どの worker にも置けなくならない)。
  (<- own int (learned-need DECLARED-MIB GROWN-MIB None 98464))
  (<- shaped int (learned-need DECLARED-MIB None GROWN-MIB 98464))
  (<- cut int (learned-need DECLARED-MIB None 200000 98464))
  (<- unlined int (learned-need DECLARED-MIB None GROWN-MIB None))
  (assert (= #(own shaped cut unlined) #(GROWN-MIB GROWN-MIB 98464 GROWN-MIB)))
  ;; 山が名乗りより小さい・山の無い task は名乗りのまま。名乗らない task は見込みの要り。
  (<- small int (learned-need DECLARED-MIB 1000 2000 98464))
  (<- unmeasured int (learned-need DECLARED-MIB None None 98464))
  (<- undeclared int (learned-need None None None 98464))
  (assert (= #(small unmeasured undeclared) #(DECLARED-MIB DECLARED-MIB 1024))))


(deftest test-a-task-of-a-shape-measured-past-the-room-waits-instead-of-crossing-the-line
  ;; node-h の 11-09 の形: 同じ形の task が 14,238 MiB まで伸びて走っている(その山は heartbeat が行に書いた)。
  (<- base TaskRecord (task-of "t41112" #("host-node-h") SHAPE))
  (val grown (replace base :phase "assigned" :worker "node-h" :started-ms 0 :peak-mib GROWN-MIB))
  ;; 通る例: 次の同じ形の task の要りは測った山 14,238 MiB で、名乗る空き 9,682 MiB の node-h には置かず、空きの無い文で待つ。
  (<- placed (get Table TaskRecord) (placed-with grown SHAPE))
  (<- no-memory str (task-unplaced-text TaskUnplacedKind.TASK-NO-MEMORY))
  (val next (.row placed "next"))
  (assert (= #(next.phase next.detail) #("queued" no-memory)) next)
  ;; 形を名乗らない task と、まだ測っていない初めての形の task は名乗り 3,584 MiB のまま置く(山を知らない仕事は名乗りを信じる —
  ;; 名乗りより伸びた分は worker が jobs の層の上限で止める)。
  (<- unshaped (get Table TaskRecord) (placed-with grown ""))
  (<- first (get Table TaskRecord) (placed-with None SHAPE))
  (assert (= #((. (.row unshaped "next") phase) (. (.row first "next") phase)) #("assigned" "assigned"))))


(deftest test-the-shape-and-the-peak-survive-the-stored-row-and-a-bad-shape-is-refused
  (<- base TaskRecord (task-of "a" #("host-node-h") SHAPE))
  (val row (replace base :phase "assigned" :worker "node-h" :started-ms 0 :peak-mib GROWN-MIB))
  (<- read TaskRecord (task-record-from-json (task-record-to-json row)))
  (assert (= #(read.shape read.peak-mib) #(SHAPE GROWN-MIB)) read)
  ;; 形と山の無い行は欄を置かず(前の版の coordinator へ戻しても読める)、欄の無い行は名乗らない・測っていないで読む。
  (<- plain TaskRecord (task-of "p" #("host-node-h") ""))
  (val spelled (task-record-to-json plain))
  (assert (not (& #{"shape" "peak_mib"} (set spelled))) spelled)
  (<- back TaskRecord (task-record-from-json spelled))
  (assert (= #(back.shape back.peak-mib) #("" None)) back)
  ;; 本文の欄: 形を名乗る時だけ欄 shape を置く。
  (assert (= #((task-placing-json 0 #() DECLARED-MIB SHAPE) (task-placing-json 0 #() DECLARED-MIB))
             #({"memoryMiB" DECLARED-MIB "shape" SHAPE} {"memoryMiB" DECLARED-MIB})))
  ;; 失敗ケース: 文字列でない・長すぎる・表示できない字を持つ形は断る(名乗らない None と空は受ける)。
  (for [bad [7 (* "x" (+ TASK-SHAPE-MAX-LENGTH 1)) "cargo\ntest"]]
    (assert (is-not (task-shape-refusal bad) None) bad))
  (for [good [None "" SHAPE]]
    (assert (is (task-shape-refusal good) None) good)))
