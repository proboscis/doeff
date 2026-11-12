;;; 置いた task の申告(ADR-DOE-CLUSTER-005 R4・law the-claim-follows-the-measured-peaks-and-stays — coordinator/core/task_claims の頭の註)。
;;;   (1) 決め方: 同じ形に終わりまで走った行が在れば、申告は山 × 余裕 + 刻みで、送り手の見当を読まない(見当 4,096 MiB の task も 512 MiB の
;;;       task も同じ申告)。無ければ見当と山の大きい方、見当の無い task は既定 UNDECLARED-TASK-MIB。
;;;   (2) 固定: 置いた歩に申告が表に書かれ、同じ形の山が後で伸びても、置いている間の申告・予約の数え・heartbeat の返事の memoryMiB は
;;;       動かない。置いた phase を出た歩に申告が外れる。
;;;   (3) 保存: 申告は state file と耐久の鍵を往復し、壊れた保存の値は理由つきで断る。
;;; 失敗ケース = 拍ごとに要りを数え直す形(古い行が保持の外へ出た拍や山が伸びた拍に、走っている task の要りが動く)と、worker が送り手の
;;; 見当を比べ続ける形(置き先が数えた要りと worker の越えの止めが別の量を読む)。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import math [ceil])
(import doeff_cluster [ClusterState WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord TaskClaim ComponentVersion])
(import doeff_cluster._coordinator.core.task_claims [claims-noted shape-peaks-of task-need-of])
(import doeff_cluster._coordinator.core.cluster_policy [load-of tasks-for])
(import doeff_cluster._coordinator.core.measured_room [SHAPE-PEAK-MARGIN PEAK-STEP-MIB UNDECLARED-TASK-MIB TASK-CORES])
(import doeff_cluster._coordinator.protocol.state_json [state-to-json state-from-json task-claim-from-json])
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_hy._table [TableWrite])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(val PY3 #((ComponentVersion "python" "3")))
(val SHAPE "repo-a:image build")
;; 同じ形の終わった回の山 615 MiB(送り手の見当 4,096 MiB の 6 分の 1)と、後で伸びた回の山 2,000 MiB。
(val SETTLED-PEAK-MIB 615)
(val GROWN-PEAK-MIB 2000)
(val CLAIM-MIB (+ (ceil (* SETTLED-PEAK-MIB SHAPE-PEAK-MARGIN)) PEAK-STEP-MIB))


(defk task-of [id memory-mib shape]
  {:pre [(: id str) (: memory-mib (| int None)) (: shape (| str None))] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "見当 memory-mib と形 shape を名乗る、待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-a") 15000 20000 0 :detached True :key id :memory-mib memory-mib :shape shape))


(defk finished-of [id peak]
  {:pre [(: id str) (: peak int)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "形 SHAPE の、山 peak で終わりまで走った行を作るため。"
  (<- row TaskRecord (task-of id 4096 SHAPE))
  (replace row :phase "finished" :worker "a" :started-ms 0 :finished-ms 10 :peak-mib peak))


(defk world-of [rows]
  {:pre [(: rows dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "測りの無い worker a 1 台と、task の行 rows(id → 行)の状態を作るため。"
  (<- workers (table-from-rows {"a" (WorkerInfo "a" #("host-a") 0 :versions PY3)}))
  (<- tasks (table-from-rows rows))
  (<- none (table-from-rows {}))
  (ClusterState #() workers none tasks))


(deftest test-the-declaration-follows-the-settled-peak-of-the-shape-not-the-senders-number
  ;; (1) 同じ形に終わりまで走った行が在る: 見当 4,096 MiB の task も 512 MiB の task も、申告は山 × 余裕 + 刻み。
  (<- done TaskRecord (finished-of "done" SETTLED-PEAK-MIB))
  (<- big TaskRecord (task-of "big" 4096 SHAPE))
  (<- small TaskRecord (task-of "small" 512 SHAPE))
  (<- rows (table-from-rows {"done" done "big" big "small" small}))
  (<- peaks dict (shape-peaks-of rows))
  (<- big-need int (task-need-of big None peaks))
  (<- small-need int (task-need-of small None peaks))
  (assert (= #(big-need small-need) #(CLAIM-MIB CLAIM-MIB)) #(big-need small-need))
  ;; 終わりまで走った行の無い形: 見当と山の大きい方。形も見当も無い task: 既定。
  (<- fresh TaskRecord (task-of "fresh" 3584 "repo-b:first"))
  (<- fresh-need int (task-need-of fresh None peaks))
  (assert (= fresh-need 3584) fresh-need)
  (<- bare TaskRecord (task-of "bare" None None))
  (<- bare-need int (task-need-of bare None peaks))
  (assert (= bare-need UNDECLARED-TASK-MIB) bare-need))


(deftest test-the-claim-is-fixed-when-placed-and-dropped-when-the-task-leaves
  (<- done TaskRecord (finished-of "done" SETTLED-PEAK-MIB))
  (<- queued TaskRecord (task-of "run" 4096 SHAPE))
  (<- before ClusterState (world-of {"done" done "run" queued}))
  ;; (2) 置いた歩: 申告が表に書かれる(memory = 同じ形の山からの値・CPU = TASK-CORES core)。
  (val placed (replace queued :phase "assigned" :worker "a" :started-ms 100 :lease-until-ms 20000))
  (val after (replace before :tasks (.with-writes before.tasks #((TableWrite "run" placed)))))
  (<- claimed ClusterState (claims-noted before after))
  (assert (= (.row claimed.claims "run") (TaskClaim :task "run" :memory-mib CLAIM-MIB :cpu-millis (* TASK-CORES 1000))) claimed.claims)
  ;; 同じ形の次の回が大きく伸びて終わった: 走っている task の申告・予約の数え・返事の memoryMiB は動かない(次に置く task だけが伸びた山で数える)。
  (<- grown TaskRecord (finished-of "grown" GROWN-PEAK-MIB))
  (val later (replace claimed :tasks (.with-writes claimed.tasks #((TableWrite "grown" grown)))))
  (<- kept ClusterState (claims-noted claimed later))
  (assert (= (. (.row kept.claims "run") memory-mib) CLAIM-MIB) kept.claims)
  (val load (load-of kept kept.placements))
  (assert (= (. (get load "a") reserved-mib) CLAIM-MIB) load)
  (val offers (tasks-for kept "a"))
  (assert (= (tuple (gfor o offers #(o.id o.memory-mib))) #(#("run" CLAIM-MIB))) offers)
  (<- next-need int (task-need-of (! (task-of "next" 4096 SHAPE)) None (! (shape-peaks-of kept.tasks))))
  (assert (= next-need (+ (ceil (* GROWN-PEAK-MIB SHAPE-PEAK-MARGIN)) PEAK-STEP-MIB)) next-need)
  ;; 置いた phase を出た歩: 申告が外れる。
  (val ended (replace placed :phase "finished" :finished-ms 500 :peak-mib 700))
  (val finished (replace kept :tasks (.with-writes kept.tasks #((TableWrite "run" ended)))))
  (<- dropped ClusterState (claims-noted kept finished))
  (assert (is (.row dropped.claims "run") None) dropped.claims))


(deftest test-a-claim-survives-the-state-file-and-the-durable-keys
  ;; (3) 申告は保存先(state file・耐久の鍵)を往復する — coordinator を作り直しても置いている task の申告は動かない。
  (<- base ClusterState (world-of {}))
  (val claim (TaskClaim :task "run" :memory-mib CLAIM-MIB :cpu-millis 1000))
  (val state (replace base :claims (.with-writes base.claims #((TableWrite "run" claim)))))
  (<- data dict (state-to-json state))
  (<- from-file ClusterState (state-from-json data 0))
  (assert (= (.row from-file.claims "run") claim) from-file.claims)
  (<- kv dict (full-kv state))
  (<- from-keys ClusterState (state-from-kv kv 0))
  (assert (= (.row from-keys.claims "run") claim) from-keys.claims)
  ;; 申告の欄の無い旧い file は空の表で読む。壊れた保存の値は理由つきで断る。
  (<- old ClusterState (state-from-json (dfor #(k v) (.items data) :if (!= k "claims") k v) 0))
  (assert (= (.size old.claims) 0) old.claims)
  (for [#(spec word) [#({"cpuMillis" 1000} "memoryMiB") #({"memoryMiB" -1 "cpuMillis" 1000} "memoryMiB") #({"memoryMiB" 1 "cpuMillis" True} "cpuMillis")]]
    (try
      (task-claim-from-json "run" spec)
      (raise (AssertionError (.format "壊れた保存の値を読んだ: {}" spec)))
      (except [error ValueError]
        (assert (in word (str error)) #(word (str error)))))))
