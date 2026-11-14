;;; 置いた task の申告(ADR-DOE-CLUSTER-005 R4 — 2026-11-12): task が置いた phase へ移った歩で、送り手の見当(task の行の memory-mib)と
;;; 測った山(task の行の peak-mib と同じ形の行の山 — shape-peaks-of)から memory の申告を 1 度だけ決め、task の id で引く表
;;; (ClusterState.claims — TaskClaim)に固定する。決め方は measured_room.learned-need(頭の註の測った山で数える要り):
;;;   同じ形に終わりまで走った行が在る   申告 = min(⌈max(この task の山, 同じ形の山の最大) × SHAPE-PEAK-MARGIN⌉ + PEAK-STEP-MIB, 上限)
;;;   無い(形を名乗らない・初めての形)  申告 = max(送り手の見当〔無ければ UNDECLARED-TASK-MIB〕, min(max(この task の山, 同じ形の山), 上限))
;;; 形の山は、まだ終わっていない行と近い回の終わった行(task_table.RECENT-PEAK-RUNS 本)の山の最大(2026-11-15 — 保持の間の全部の最大は、
;;; 外れ値の 1 本が保持の間ずっと申告を縛った)。形に近い回の finished の行が無ければ、送り手が名乗った族(ShapeFamily — 形 → 族の別の表)
;;; の近い回の山で補う(task_table.family-peaks-of — 引く順は 形の山 → 族の近い回の山 → 送り手の見当)。命令の全体の digest を含む形は
;;; ほとんどの回が初めての形で、最も大きい共有の機体の 48 時間の 309 本のうち 278 本の申告が送り手の見当(中央の値 3,584 MiB — 測った山の
;;; 中央の値は 873 MiB)のままだった。
;;; CPU の申告は全部の task で TASK-CORES core(task ごとの CPU の使いは記録していない)。
;;;
;;; 固定する訳: 以前は置いた task の要りを拍ごとに数え直した(load-of)。同じ形の古い行が保持の外へ出た拍や、ほかの task の山が伸びた拍に、
;;; 走っている task の要りが動き、予約と worker が比べる量(heartbeat の返事の memoryMiB)が置いた時と違う値になる。worker が比べる量は
;;; 送り手の見当のままだったので、置き先が数えた要りと worker の要りの越えの止め(worker/core/eviction_rules の over-need-tasks)が別の
;;; 量を読んでいた — 同じ形の山が 615 MiB の task を、置き先は 1,025 MiB で数え、worker は名乗りの 4,096 MiB を越えるまで越えと見なかった。
;;; 今はどちらも申告を読む。
;;;
;;; 上限は、task の要る能力の合う worker の持つ tasks 区画の memoryMiB の最大(compartment-clamp — R4。区画を持つ合う worker が無ければ
;;; 切らない)。以前は置いた worker の測った線 − 床で切った — 測った空きは置き方から外したので(R5)、上限も宣言から読む。置く判断
;;; (cluster_policy.place-tasks)と申告の書き(claims-noted)は同じ歩の状態から同じ上限を読む。申告は置いた時に決めるので、区画が後で
;;; 変わっても置いた task の申告は動かない。
;;;
;;; 書きと外しは 1 歩の前後の差の 1 点(claims-noted — program.coordinator-step が保存の直前に呼ぶ): 置いた phase へ移った行(置き直しで
;;; worker か置いた刻が替わった行を含む)に申告を書き、置いた phase を出た行・消えた行の申告を外す。置く判断(cluster_policy.place-tasks)、
;;; 状態を失った coordinator の引き取り(adopted-task)、走らせ直し(行の id が替わる)のどの道も、この歩の前後の差に現れる。
;;; 申告の無い置いた task(表の前の版で置いた行)は、その拍の決め方の値で数える(claim-mib-of)— 次に置き直すまで。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import doeff_cluster._coordinator.intent.cluster_model [ClusterState TaskClaim TaskRecord PLACED-PHASES])
(import doeff_cluster._coordinator.core.measured_room [learned-need ShapePeak TASK-CORES])
(import doeff_cluster._coordinator.core.cluster_rules [placeable])
(import doeff_cluster._shared.core.compartment_rules [held-compartments])
(import doeff_hy._table [Table TableWrite])
(import doeff_cluster._coordinator.intent.task_table [task-index shape-family-peaks])

;; 1 core の millicore。
(val MILLIS-PER-CORE 1000)
;; task 1 本の CPU の申告(millicore — 頭の註)。
(val TASK-CPU-MILLIS (* TASK-CORES MILLIS-PER-CORE))


(deff tasks-compartments-of [#^ ClusterState state]  ; cannot be a defk: place-tasks・load-of・申告の上限の内包表記(Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "名簿の worker ごとの tasks 区画(ADR-DOE-CLUSTER-005 R1・R2 — worker の名 → HeldCompartment、持たない worker は None)を知るため。
   置く判断の和(R5)と申告の上限(R4)が同じ写しを読む。"
  (setv rows (tuple (.rows state.machine-compartments)))
  (dfor name (.keys state.workers)
        name (next (gfor h (held-compartments rows name) :if (= h.use "tasks") h) None)))


(deff compartment-clamp [#^ ClusterState state #^ dict compartments #^ (get tuple #(str ...)) needs]  ; cannot be a defk: place-tasks・load-of・claims-noted の loop(Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState) (: compartments dict) (: needs tuple)] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "要る能力 needs の task の申告の上限(MiB — 頭の註の上限・R4)を知るため: 置き場所の規則(placeable)の合う worker の持つ tasks 区画の
   memoryMiB の最大。区画を持つ合う worker が無ければ None(切らない)。compartments = tasks-compartments-of の答え。"
  (max (gfor #(name held) (.items compartments)
             :setv w (.row state.workers name)
             :if (and (is-not held None) (is-not w None) (placeable needs w))
             held.memory-mib)
       :default None))


(defk shape-peaks-of [tasks [families None]]
  {:pre [(: tasks (get Table TaskRecord)) (: families (| Table None))] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "形ごとの測った山を知るため(measured_room の頭の註の測った山で数える要り): 形を名乗り山を測った行の形 → ShapePeak(まだ終わっていない
   行と近い回の終わった行の山の最大と、その近い回に終わり〔finished〕まで走った行が在るか — task_table.RECENT-PEAK-RUNS)。保持の間の
   終わった行(scan-rows が除く行)も読む — 終わった task の山が同じ形の次の task の申告の材料なので。families = 形 → 族の関係
   (ClusterState.shape-families — None = 族で補わない)を渡すと、近い回の finished の行の無い形を族の近い回の山で補う
   (task_table.family-peaks-of)。値は task の表の索引が書きのたびに持ち続ける(task_table — 行を回らない)。読み手は書き換えない。"
  (if (is families None)
      (. (task-index tasks) peaks)
      (shape-family-peaks tasks families)))


(defk task-need-of [task clamp peaks]
  {:pre [(: task TaskRecord) (: clamp (| int None)) (: peaks dict)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "task を置く時の memory の申告(MiB)を知るため(頭の註の決め方): 送り手の見当と、この task の山と、同じ形の山(peaks —
   shape-peaks-of)を、上限 clamp(compartment-clamp — None = 切らない)で切って数えた値(measured_room.learned-need — 同じ形を終わりまで
   測っていれば見当を見ない)。置いた後の task の申告は表の行(claim-mib-of)で読み、この値を数え直さない。"
  (<- need int (learned-need task.memory-mib task.peak-mib (if task.shape (.get peaks task.shape) None) clamp))
  need)


(defk claim-mib-of [task claims clamp peaks]
  {:pre [(: task TaskRecord) (: claims (get Table TaskClaim)) (: clamp (| int None)) (: peaks dict)] :post [(: % int)]
   :tags {:context "coordinator" :role "judgment"}}
  "置いた task の memory の申告(MiB)を知るため: 表 claims に行が在ればその memory-mib(置いた時に固定した値)、無ければ(表の前の版で
   置いた行)上限 clamp での task-need-of の今の値。"
  (val claim (.row claims task.id))
  (when (is-not claim None)
    (return claim.memory-mib))
  (<- need int (task-need-of task clamp peaks))
  need)


(defk claim-cpu-of [task claims]
  {:pre [(: task TaskRecord) (: claims (get Table TaskClaim))] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "置いた task の CPU の申告(millicore)を知るため: 表 claims に行が在ればその cpu-millis、無ければ TASK-CPU-MILLIS。"
  (val claim (.row claims task.id))
  (if (is claim None) TASK-CPU-MILLIS claim.cpu-millis))


(defk offered-mib-of [task claims]
  {:pre [(: task TaskRecord) (: claims (get Table TaskClaim))] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "heartbeat の返事の task の行の memoryMiB(worker が task の使いを比べる量)を知るため: 申告の行が在ればその memory-mib、無ければ
   送り手の見当 memory-mib(None = 名乗らない — worker は自分の既定で数える)。返事は heartbeat ごとに組むので、形の山を読み直さない。"
  (val claim (.row claims task.id))
  (if (is claim None) task.memory-mib claim.memory-mib))


(defk newly-placed? [before after]
  {:pre [(: before (| TaskRecord None)) (: after (| TaskRecord None))] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "1 歩の前後の task の行 before・after で、この歩に task が置かれたか(置いた phase へ移った・置いたまま worker か置いた刻が替わった)を
   知るため。preparing から assigned への移りは同じ置きの続き(置き直しでない)。"
  (and (is-not after None) (in after.phase PLACED-PHASES)
       (or (is before None) (not-in before.phase PLACED-PHASES)
           (!= before.worker after.worker) (!= before.started-ms after.started-ms))))


(defk claims-noted [before after]
  {:pre [(: before ClusterState) (: after ClusterState)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "1 歩の前後の状態 before・after から、この歩に置かれた task の申告を表 claims に書き、置いた phase を出た task・消えた task の申告を
   外すため(頭の註の書きと外しの 1 点)。申告の決め方は task-need-of を、after の区画から求めた上限(compartment-clamp)と after の形の
   山で読む(置く判断と同じ歩の状態)。変わらなければ after をそのまま返す。"
  (val changed (.changed-keys before.tasks after.tasks))
  (when (not changed)
    (return after))
  ;; 置いた phase を出た行・消えた行の申告を外す。
  (setv writes (lfor key changed
                     :setv row (.row after.tasks key)
                     :if (and (or (is row None) (not-in row.phase PLACED-PHASES)) (is-not (.row after.claims key) None))
                     (TableWrite key None)))
  ;; この歩に置かれた行の申告を書く(形の山は置いた行が在る歩でだけ読む — 全部の行を走る)。
  (setv placed [])
  (for [key changed]
    (setv row (.row after.tasks key))
    (<- moved bool (newly-placed? (.row before.tasks key) row))
    (when moved
      (.append placed row)))
  (when placed
    (<- peaks dict (shape-peaks-of after.tasks after.shape-families))
    (val compartments (tasks-compartments-of after))
    (for [t placed]
      (<- need int (task-need-of t (compartment-clamp after compartments t.needs) peaks))
      (.append writes (TableWrite t.id (TaskClaim :task t.id :memory-mib need :cpu-millis TASK-CPU-MILLIS)))))
  (if writes
      (replace after :claims (.with-writes after.claims (tuple writes)))
      after))


(defk families-noted [before after]
  {:pre [(: before ClusterState) (: after ClusterState)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "1 歩の前後の状態 before・after から、この歩でその形の task の行が 1 本も無くなった形の族の行(ClusterState.shape-families — ShapeFamily)を
   外すため(族の行は形の行が在る間だけ要る — 書きは積んだ拍の detached_policy.submit-detached)。変わらなければ after をそのまま返す。"
  (val changed (.changed-keys before.tasks after.tasks))
  (when (or (not changed) (= (.size after.shape-families) 0))
    (return after))
  (val tallies (. (task-index after.tasks) tallies))
  (setv gone (set))
  (for [key changed]
    (for [row #((.row before.tasks key) (.row after.tasks key))]
      (when (and (is-not row None) row.shape (not-in row.shape tallies) (is-not (.row after.shape-families row.shape) None))
        (.add gone row.shape))))
  (if gone
      (replace after :shape-families (.with-writes after.shape-families (tuple (gfor shape (sorted gone) (TableWrite shape None)))))
      after))
