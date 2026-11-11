;;; 名乗りを下限にしない要り(coordinator/core/measured_room の頭の註の測った山で数える要り・2026-11-11)の安全の側の検: 要りを同じ形の
;;; 終わりまで測った山 + 余裕まで下げた後に、task がその要りも名乗りも越えて伸びても、追い出しの線を越えず、同じ機体の要りの内の task は
;;; 止まらず、次の同じ形の task は伸びた山で数えられる。
;;;   (1) coordinator: 同じ形の山 882 MiB の task(名乗り 3,584 MiB)の要りは 1,359 MiB。9,000 MiB まで伸びた後の予約は 0 で、伸びた分は
;;;       測った空きから引かれている(空きの読みに入る)
;;;   (2) worker: jobs の層の memory.max は、要りの大きさに依らず「jobs の今の使い + 追い出しまでの空き − 宣言の余裕」を越えない — kernel は
;;;       jobs の層の中で止め、Pod の working set は追い出しの線の手前に残る
;;;   (3) worker: 上に載る系の残す線を割った回に見張りが名指すのは、名乗った要りを越えた task だけ(要りの内の task は止めない)
;;;   (4) coordinator: 伸びた task の山を heartbeat が行に書き、次の同じ形の task の要りはその山 + 余裕になる(同じ機体に詰めない)
;;; 失敗ケース = 要りを下げた分だけ置き先が同じ機体に task を足し、伸びた 1 本が kubelet の追い出しで Pod ごと全部の task を止める形。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import math [ceil])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [task-need-of task-peaks-noted shape-peaks-of])
(import doeff_cluster._coordinator.core.measured_room [learned-need reserved-mib-of ShapePeak SHAPE-PEAK-MARGIN PEAK-STEP-MIB])
(import doeff_cluster._worker.core.memory_rules [JobUse MIB TASK-PREFIX])
(import doeff_cluster._worker.core.eviction_rules [EvictionLine LineKind TaskNeed jobs-limit over-need-tasks placed-pending])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(val PY3 #((ComponentVersion "python" "3")))
(val GIB (* 1024 MIB))
(val SHAPE "repo-a:pytest tests/x.py")
;; 名乗り 3,584 MiB・同じ形の前の回の山 882 MiB。置いた後に 9,000 MiB まで伸びる(名乗りも山も越える)。
(val DECLARED-MIB 3584)
(val SETTLED-PEAK-MIB 882)
(val GROWN-MIB 9000)
;; 同じ機体の要りの内の task(要り 1,024 MiB・使い 600 MiB)。
(val STEADY-MIB 600)
;; 追い出しの線: 線の大きさ 20 GiB・線に数える working set 16 GiB(空き 4 GiB)。宣言の余裕 1 GiB。
(val LINE (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes (* 20 GIB) :used-bytes (* 16 GIB) :room-bytes (* 4 GIB)))
(val FLOOR-MARGIN GIB)


(defk task-of [id phase peak]
  {:pre [(: id str) (: phase str) (: peak (| int None))] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "形 SHAPE と要り DECLARED-MIB を名乗り、node-h に置いた段 phase・山 peak の切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-h") 15000 20000 0 :detached True :key id :memory-mib DECLARED-MIB
              :shape SHAPE :phase phase :worker "node-h" :started-ms 0 :peak-mib peak))


(defk uses-of [grown-mib]
  {:pre [(: grown-mib int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "見張りの読みの job ごとの memory を作るため: 伸びる task grown が grown-mib、要りの内の task steady が STEADY-MIB。"
  #((JobUse :job (+ TASK-PREFIX "grown") :pid 10 :bytes (* grown-mib MIB) :processes 3)
    (JobUse :job (+ TASK-PREFIX "steady") :pid 11 :bytes (* STEADY-MIB MIB) :processes 1)))


(deftest test-a-task-grown-past-its-learned-need-is-held-below-the-eviction-line
  ;; (1) 同じ形の前の回が 882 MiB で終わった: 名乗り 3,584 MiB の task の要りは 882 × 余裕 + 刻み。
  (<- need int (learned-need DECLARED-MIB None (ShapePeak :mib SETTLED-PEAK-MIB :settled True) None))
  (assert (= need (+ (ceil (* SETTLED-PEAK-MIB SHAPE-PEAK-MARGIN)) PEAK-STEP-MIB)) need)
  ;; 要りを越えて伸びた task の予約は 0(伸びた使いは測った空きから既に引かれている — 空きを 2 度引かない)。
  (<- reserved int (reserved-mib-of need GROWN-MIB))
  (assert (= reserved 0) reserved)
  ;; (2) worker の jobs の層の上限は、追い出しまでの空きから宣言の余裕を引いた所まで — 名乗りと山を越えた伸びも kernel がこの層で止める。
  ;; 置いた task の取り置き(worker は名乗った要りで数える)がどれだけ大きくても、上限は線の手前を越えない。
  (val needs #((TaskNeed :id "grown" :bytes (* DECLARED-MIB MIB)) (TaskNeed :id "steady" :bytes GIB)))
  (<- uses tuple (uses-of GROWN-MIB))
  (val jobs-current (sum (gfor u uses u.bytes)))
  (<- pending int (placed-pending needs uses))
  (<- limit int (jobs-limit jobs-current LINE FLOOR-MARGIN FLOOR-MARGIN None 0 pending))
  (<- swollen int (jobs-limit jobs-current LINE FLOOR-MARGIN FLOOR-MARGIN None 0 (* 64 GIB)))
  (val headroom (- LINE.room-bytes FLOOR-MARGIN))
  (assert (and (<= (- limit jobs-current) headroom) (<= (- swollen jobs-current) headroom)) #(limit swollen))
  ;; (3) 線を割った回に見張りが止めるのは、名乗った要りを越えた task だけ — 要りの内の task(steady)は止めない。
  (<- over tuple (over-need-tasks needs uses (* 2 GIB)))
  (assert (= (tuple (gfor t over t.id)) #("grown")) over))


(deftest test-the-grown-peak-raises-the-need-of-the-next-task-of-the-shape
  ;; (4) heartbeat の測りが伸びた task の今の使いを名乗ると、行の山が伸び、次の同じ形の task の要りはその山 + 余裕になる。
  (<- done TaskRecord (task-of "done" "finished" SETTLED-PEAK-MIB))
  (<- grown TaskRecord (task-of "grown" "assigned" SETTLED-PEAK-MIB))
  (<- next TaskRecord (task-of "next" "queued" None))
  (<- rows (table-from-rows {"done" done "grown" grown "next" (replace next :worker None :started-ms None)}))
  (val measured (MeasuredLoad :at-ms 0 :cpus 8 :memory-total-MiB 20480 :memory-available-MiB 4096 :cpu-busy 0.1 :cpu-nice 0.0
                              :cpu-some-avg10 0.5 :memory-some-avg10 0.0 :task-memory-MiB {"grown" GROWN-MIB}))
  (<- before int (task-need-of (.row rows "next") None (! (shape-peaks-of rows))))
  (<- noted (task-peaks-noted rows "node-h" measured))
  (<- after int (task-need-of (.row noted "next") None (! (shape-peaks-of noted))))
  (assert (= #(before after)
             #((+ (ceil (* SETTLED-PEAK-MIB SHAPE-PEAK-MARGIN)) PEAK-STEP-MIB) (+ (ceil (* GROWN-MIB SHAPE-PEAK-MARGIN)) PEAK-STEP-MIB)))
          #(before after)))
