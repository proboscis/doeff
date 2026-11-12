;;; 測った山で数える task の要り(coordinator/core/measured_room の頭の註)の検: 送り手が名乗る memory の要りが実の山より小さくても、
;;; coordinator は worker の測りが名乗る task ごとの今の使いの最大を行の山に持ち、同じ形の task の要りをその山で数える。
;;;   (1) heartbeat の task ごとの今の使いで、その worker に置いた task の山を書き直す(刻み PEAK-STEP-MIB — 伸びるたびには書かない)
;;;   (2) 同じ形に終わりまで走った行が在れば、要り = min(⌈max(この task の山, 同じ形の山) × 余裕⌉ + 刻み, worker の線 − 床)で、名乗りを
;;;       下限にしない。終わりまで走った行が無ければ max(名乗り, min(max(この task の山, 同じ形の山), 線 − 床))。形を名乗らない・初めての形は
;;;       名乗りのまま
;;;   (3) 同じ形の task が実の山まで伸びた worker の空きが山より小さければ、次の同じ形の task をそこに置かない
;;;   (4) 形と山は保存の行に残り、形の綴りの誤りは送る前と受け口で断る
;;; 失敗ケース = node-h の 11-09 16:52 UTC: 名乗る空き約 9,682 MiB の刻に、名乗り 3,584 MiB の task 3 本を置き、8 分後に 2 本が各 14 GiB
;;; を使った(t41112 = 14,238 MiB)。予約は「要り − 今の使い」だけで、名乗りを越えた伸びは次の置きの判断に入らなかった。
;;; 逆の失敗ケース = 11-11 16:4x の機体: 名乗り 8,192 MiB の task の山が 882 MiB(名乗りが実の山の 4〜9 倍の送り手が並ぶ)で、名乗りが要りの
;;; 下限として勝ち、置き済みの予約の和 18,967 MiB に対し実の使いは約 3,800 MiB — 約 26,000 MiB 空いた機体の memory の空きを −3,484 MiB と数えた。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text task-peaks-noted shape-peaks-of PEAK-STEP-MIB])
(import doeff_cluster._coordinator.core.measured_room [learned-need ShapePeak MEMORY-FLOOR-MIB SHAPE-PEAK-MARGIN])
(import math [ceil])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._shared.core.detached_rules [task-shape-refusal task-placing-json TASK-SHAPE-MAX-LENGTH])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])
(import tests.room_rows [compartments-like-rooms])
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
  ;; node-h の tasks 区画は、伸びた task の分と名乗る空きの和(区画の和は伸びた task の申告を数える — ADR-DOE-CLUSTER-005 R5)。
  (<- compartments Table (compartments-like-rooms {"node-h" (replace node-h :measured (! (measured (+ NODE-H-ROOM-MIB GROWN-MIB MEMORY-FLOOR-MIB) {})))}))
  (val state (ClusterState #() workers none rows :machine-compartments compartments))
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
  ;; 形ごとの山は、形を名乗り山を測った行(終わった行を含む)の最大。終わりまで走った行(finished)が在る形だけ settled。
  (<- running-only dict (shape-peaks-of grown))
  (assert (= running-only {SHAPE (ShapePeak :mib GROWN-MIB :settled False)}) running-only)
  (<- peaks dict (shape-peaks-of (.with-writes grown #((TableWrite "c" (replace ended :peak-mib 20000))))))
  (assert (= peaks {SHAPE (ShapePeak :mib 20000 :settled True)}) peaks)
  ;; 終わりの前に止まった行(lost)の山は最大に数えるが、終わりまでの測りにはしない。
  (<- lost dict (shape-peaks-of (.with-writes grown #((TableWrite "c" (replace ended :phase "lost" :peak-mib 20000))))))
  (assert (= lost {SHAPE (ShapePeak :mib 20000 :settled False)}) lost))


(defk learned-of [peak]
  {:pre [(: peak int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "終わりまで測った同じ形の山 peak から数える要り(余裕と刻みを足す — measured_room の頭の註)を知るため。"
  (+ (ceil (* peak SHAPE-PEAK-MARGIN)) PEAK-STEP-MIB))


(deftest test-a-settled-shape-peak-is-the-need-without-the-declaration-as-floor
  ;; 11-11 の機体の形: 名乗り 8,192 MiB・同じ形の終わりまで走った回の山 882 MiB。要りは山 + 余裕(⌈882 × 1.25⌉ + 256 = 1,359 MiB)で、
  ;; 名乗りは下限にならない。
  (val settled (ShapePeak :mib 882 :settled True))
  (<- shaped int (learned-need 8192 None settled 98464))
  (<- expected int (learned-of 882))
  (assert (= shaped expected 1359) shaped)
  ;; この task の山が同じ形の山より大きければ、その山 + 余裕。線 − 床より大きければ線 − 床で切る。名乗らない task も同じ形の山で数える。
  (<- own int (learned-need 8192 3000 settled 98464))
  (<- cut int (learned-need 8192 None (ShapePeak :mib 200000 :settled True) 98464))
  (<- unlined int (learned-need 8192 None settled None))
  (<- undeclared int (learned-need None None settled 98464))
  (<- own-expected int (learned-of 3000))
  (assert (= #(own cut unlined undeclared) #(own-expected 98464 1359 1359)))
  ;; 失敗ケース(前の式 max(名乗り, …)): 名乗り 8,192 MiB が要りに残る — 予約が実の山の 6 倍になる。
  (assert (< shaped 8192)))


(deftest test-the-need-is-the-larger-of-the-declaration-and-the-measured-peak-until-a-run-settles
  ;; 通る例: 終わりまで走った行の無い形は、名乗りより大きい山(この task か同じ形)は山。線 − 床より大きい山は線 − 床で切る。
  (val unsettled (ShapePeak :mib GROWN-MIB :settled False))
  (<- own int (learned-need DECLARED-MIB GROWN-MIB None 98464))
  (<- shaped int (learned-need DECLARED-MIB None unsettled 98464))
  (<- cut int (learned-need DECLARED-MIB None (ShapePeak :mib 200000 :settled False) 98464))
  (<- unlined int (learned-need DECLARED-MIB None unsettled None))
  (assert (= #(own shaped cut unlined) #(GROWN-MIB GROWN-MIB 98464 GROWN-MIB)))
  ;; 山が名乗りより小さい・山の無い task は名乗りのまま(走っている初めての回の山はまだ伸びる途中)。名乗らない task は見込みの要り。
  (<- small int (learned-need DECLARED-MIB 1000 (ShapePeak :mib 2000 :settled False) 98464))
  (<- unmeasured int (learned-need DECLARED-MIB None None 98464))
  (<- undeclared int (learned-need None None None 98464))
  (assert (= #(small unmeasured undeclared) #(DECLARED-MIB DECLARED-MIB 1024))))


(deftest test-a-task-of-a-shape-measured-past-the-room-waits-instead-of-crossing-the-line
  ;; node-h の 11-09 の形: 同じ形の task が 14,238 MiB まで伸びて走っている(その山は heartbeat が行に書いた)。
  (<- base TaskRecord (task-of "t41112" #("host-node-h") SHAPE))
  (val grown (replace base :phase "assigned" :worker "node-h" :started-ms 0 :peak-mib GROWN-MIB))
  ;; 通る例: 次の同じ形の task の申告は測った山 14,238 MiB で、伸びた task の申告の残り 9,682 MiB の node-h の区画には置かず、和の越えの文で待つ。
  (<- placed (get Table TaskRecord) (placed-with grown SHAPE))
  (<- no-memory str (task-unplaced-text TaskUnplacedKind.TASK-COMPARTMENT-FULL))
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


(deftest test-a-declaration-past-the-room-is-placed-by-the-settled-shape-peak
  ;; 11-11 の形: 同じ形の前の回は山 882 MiB で終わった。次の回が名乗る要り 12,000 MiB は node-h の名乗る空き 9,682 MiB より大きいが、
  ;; 要りは山 + 余裕(1,359 MiB)なので置く。前の式(名乗りが下限)では空きの無い文で待った。
  (<- base TaskRecord (task-of "done" #("host-node-h") SHAPE))
  (val done (replace base :phase "finished" :worker "node-h" :started-ms 0 :finished-ms 500 :retain-ms 86400000 :peak-mib 882))
  (<- next TaskRecord (task-of "next" #("host-node-h") SHAPE))
  (val big (replace next :memory-mib 12000))
  (val node-h (WorkerInfo "node-h" #("host-node-h") 0 :versions PY3 :measured (! (measured (+ NODE-H-ROOM-MIB MEMORY-FLOOR-MIB) {}))))
  (<- workers (table-from-rows {"node-h" node-h}))
  (<- rows (table-from-rows {"done" done "next" big}))
  (<- none (table-from-rows {}))
  (<- compartments Table (compartments-like-rooms {"node-h" node-h}))
  (val state (ClusterState #() workers none rows :machine-compartments compartments))
  (<- placed (get Table TaskRecord) (tasks-placed 1000 state state.placements T))
  (assert (= (. (.row placed "next") phase) "assigned") (.row placed "next"))
  ;; 終わりまで走った行の無い形(前の回が走っている途中)は名乗りのまま数え、空きの無い文で待つ。
  (<- running-rows (table-from-rows {"done" (replace done :phase "assigned" :finished-ms None) "next" big}))
  (<- waiting (get Table TaskRecord) (tasks-placed 1000 (ClusterState #() workers none running-rows :machine-compartments compartments) state.placements T))
  (assert (= (. (.row waiting "next") phase) "queued") (.row waiting "next")))
