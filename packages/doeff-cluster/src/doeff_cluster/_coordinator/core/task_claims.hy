;;; 置いた task の申告(ADR-DOE-CLUSTER-005 R4 — 2026-11-12): task が置いた phase へ移った歩で、送り手の見当(task の行の memory-mib)と
;;; 測った山(task の行の peak-mib と同じ形の行の山 — shape-peaks-of)から memory の申告を 1 度だけ決め、task の id で引く表
;;; (ClusterState.claims — TaskClaim)に固定する。決め方は measured_room.learned-need(頭の註の測った山で数える要り):
;;;   同じ形に終わりまで走った行が在る   申告 = min(⌈max(この task の山, 同じ形の山の最大) × SHAPE-PEAK-MARGIN⌉ + PEAK-STEP-MIB, 線 − 床)
;;;   無い(形を名乗らない・初めての形)  申告 = max(送り手の見当〔無ければ UNDECLARED-TASK-MIB〕, min(max(この task の山, 同じ形の山), 線 − 床))
;;; CPU の申告は全部の task で TASK-CORES core(task ごとの CPU の使いは記録していない)。
;;;
;;; 固定する訳: 以前は置いた task の要りを拍ごとに数え直した(load-of)。同じ形の古い行が保持の外へ出た拍や、ほかの task の山が伸びた拍に、
;;; 走っている task の要りが動き、予約と worker が比べる量(heartbeat の返事の memoryMiB)が置いた時と違う値になる。worker が比べる量は
;;; 送り手の見当のままだったので、置き先が数えた要りと worker の要りの越えの止め(worker/core/eviction_rules の over-need-tasks)が別の
;;; 量を読んでいた — 同じ形の山が 615 MiB の task を、置き先は 1,025 MiB で数え、worker は名乗りの 4,096 MiB を越えるまで越えと見なかった。
;;; 今はどちらも申告を読む。
;;;
;;; 線 − 床で切るのは置いた worker の測った線(measured_room.memory-line-mib)で、区画の宣言を置き先が読むまでの間の上限。
;;; 申告は置いた時に決めるので、線が後で動いても置いた task の申告は動かない。
;;;
;;; 書きと外しは 1 歩の前後の差の 1 点(claims-noted — program.coordinator-step が保存の直前に呼ぶ): 置いた phase へ移った行(置き直しで
;;; worker か置いた刻が替わった行を含む)に申告を書き、置いた phase を出た行・消えた行の申告を外す。置く判断(cluster_policy.place-tasks)、
;;; 状態を失った coordinator の引き取り(adopted-task)、走らせ直し(行の id が替わる)のどの道も、この歩の前後の差に現れる。
;;; 申告の無い置いた task(表の前の版で置いた行)は、その拍の決め方の値で数える(claim-mib-of)— 次に置き直すまで。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import doeff_cluster._coordinator.intent.cluster_model [ClusterState TaskClaim TaskRecord WorkerInfo PLACED-PHASES])
(import doeff_cluster._coordinator.core.measured_room [learned-need memory-line-mib ShapePeak TASK-CORES])
(import doeff_hy._table [Table TableWrite])
(import doeff_cluster._coordinator.intent.task_table [task-index])

;; 1 core の millicore。
(val MILLIS-PER-CORE 1000)


(defk shape-peaks-of [tasks]
  {:pre [(: tasks (get Table TaskRecord))] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "形ごとの測った山を知るため(measured_room の頭の註の測った山で数える要り): 形を名乗り山を測った行(保持の間の終わった行を含む)の形 →
   ShapePeak(山の最大と、終わり〔finished〕まで走った行が在るか)。保持の間の終わった行(scan-rows が除く行)も読む — 終わった task の山が
   同じ形の次の task の申告の材料なので。値は task の表の索引が書きのたびに持ち続ける(task_table — 行を回らない)。読み手は書き換えない。"
  (. (task-index tasks) peaks))


(defk task-need-of [task worker peaks]
  {:pre [(: task TaskRecord) (: worker (| WorkerInfo None)) (: peaks dict)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "task を worker に置く時の memory の申告(MiB)を知るため(頭の註の決め方): 送り手の見当と、この task の山と、同じ形の山(peaks —
   shape-peaks-of)を、worker の線 − 床で切って数えた値(measured_room.learned-need — 同じ形を終わりまで測っていれば見当を見ない)。
   worker の無い・測りの無い時は山を線で切らない。置いた後の task の申告は表の行(claim-mib-of)で読み、この値を数え直さない。"
  (<- line (| int None) (memory-line-mib (if (is worker None) None worker.measured)))
  (<- need int (learned-need task.memory-mib task.peak-mib (if task.shape (.get peaks task.shape) None) line))
  need)


(defk claim-mib-of [task claims worker peaks]
  {:pre [(: task TaskRecord) (: claims (get Table TaskClaim)) (: worker (| WorkerInfo None)) (: peaks dict)] :post [(: % int)]
   :tags {:context "coordinator" :role "judgment"}}
  "置いた task の memory の申告(MiB)を知るため: 表 claims に行が在ればその memory-mib(置いた時に固定した値)、無ければ(表の前の版で
   置いた行)task-need-of の今の値。"
  (val claim (.row claims task.id))
  (when (is-not claim None)
    (return claim.memory-mib))
  (<- need int (task-need-of task worker peaks))
  need)


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
   外すため(頭の註の書きと外しの 1 点)。申告の決め方は task-need-of を、置いた worker と after の形の山で読む。変わらなければ after を
   そのまま返す。"
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
    (<- peaks dict (shape-peaks-of after.tasks))
    (for [t placed]
      (<- need int (task-need-of t (.row after.workers t.worker) peaks))
      (.append writes (TableWrite t.id (TaskClaim :task t.id :memory-mib need :cpu-millis (* TASK-CORES MILLIS-PER-CORE))))))
  (if writes
      (replace after :claims (.with-writes after.claims (tuple writes)))
      after))
