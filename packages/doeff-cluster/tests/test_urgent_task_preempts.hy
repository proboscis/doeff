;;; 場所の譲り(coordinator/core/measured_room の頭の註の場所の譲り・worker/core/memory_rules の頭の註の急ぎの task・2026-11-11)の検:
;;; 急ぎの優先の task は memory の空きを待たず、場所を譲る名乗り(preemptible)の優先の低い task を止めて先に動く。見張りは急ぎの task を後に止める。
;;;   (1) memory の空きに入らない急ぎの task は 1 拍で assigned になり、同じ worker の譲る task は id の替わった次の試行の待ちの行へ戻る
;;;       (消失の上限に数えない・detail と preempted-by に訳と相手)
;;;   (2) 失敗ケース: 譲る名乗りが無ければ(直す前の形)、急ぎの task は queued のまま待ち、優先の低い task は走り続ける
;;;   (3) 同じ優先の task と、全部止めても入らない worker では止めない
;;;   (4) 門の外の急ぎの task も、選んだ worker で要りが入らなければ譲らせて置く
;;;   (5) 受け口と構築関数: 走らせ直しを名乗らない preemptible は断る・保存の綴りの往復で欄が残る・欄の無い旧い行は名乗らない
;;;   (6) worker: 返事の行の priority を読み、DANGER の止めと要りの越えの止めは急ぎの task を、ほかの候補が残る間は選ばない
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import pytest)
(import doeff_core_effects [ProcRow])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo TaskLimit GroupLimit submit-detached-task]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad TaskBody])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed])
(import doeff_cluster._coordinator.core.detached_policy [submit-detached])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB KEEP-NOW-PRIORITY])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._shared.core.detached_rules [task-preemptible-refusal task-preemptible-json])
(import doeff_cluster._worker.core.memory_rules [KillUnit JobUse MemoryLimits MIB UnitKind URGENT-PRIORITY victim-of])
(import doeff_cluster._worker.core.eviction_rules [TaskNeed task-needs-of over-need-tasks urgent-jobs])
(import doeff_hy._table [TableWrite table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed task-body-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
(val GROUP "swap")
(val GROUPS (table-of #((TableWrite GROUP (GroupLimit :group GROUP :scope "cluster" :limit 1)))))
;; 機体の memory の空き(床の上 1000 MiB)・譲る task の要りと今の使い・急ぎの task の要り。譲る task の予約(要り − 今の使い = 548)を引いた
;; 空き 452 MiB に急ぎの要り 2048 は入らず、譲る task を止めると戻る 2048(max(要り, 今の使い))を足すと入る。
(val ROOM-MIB 1000)
(val LOW-NEED-MIB 2048)
(val LOW-USE-MIB 1500)
(val URGENT-NEED-MIB 2048)


(defk worker-with [uses]
  {:pre [(: uses dict)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "memory の空きが床の上 ROOM-MIB の node-a を作るため(CPU は空き・PSI は低い)。uses = 測りの名乗る task ごとの今の使い(MiB)。"
  (WorkerInfo "node-a" #("host-node-a") NOW :versions PY3
              :measured (MeasuredLoad :at-ms NOW :cpus 8 :cpu-busy 0.1 :memory-total-MiB 16384
                                      :memory-available-MiB (+ MEMORY-FLOOR-MIB ROOM-MIB) :task-memory-MiB uses)))


(defk queued [id priority * [memory-mib None] [ungated False] [limits #()]]
  {:pre [(: id str) (: priority int) (: memory-mib (| int None)) (: ungated bool) (: limits tuple)] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-a を要る、待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-a") 15000 20000 0 :detached True :key id
              :priority priority :memory-mib memory-mib :ungated ungated :limits limits))


(defk running [id priority * [preemptible True]]
  {:pre [(: id str) (: priority int) (: preemptible bool)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-a に置かれて走っている、走らせ直しを名乗る切り離した task(要り LOW-NEED-MIB・譲る名乗り preemptible)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-a") 15000 (+ NOW 15000) 0 :detached True :key id
              :priority priority :memory-mib LOW-NEED-MIB :rerun True :preemptible preemptible
              :phase "assigned" :worker "node-a" :started-ms 1000))


(defk placed-rows [rows uses]
  {:pre [(: rows dict) (: uses dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-a 1 台(測りの今の使い uses)と組の宣言 GROUPS の状態で task rows を置き先に 1 拍回し、拍の後の全部の行(id → 行)を得るため。"
  (<- workers (table-from-rows {"node-a" (! (worker-with uses))}))
  (<- tasks (table-from-rows rows))
  (val state (replace (ClusterState #() workers (table-of #()) tasks) :group-limits GROUPS))
  (val placed (! (tasks-placed NOW state state.placements T)))
  (dfor t (.rows placed) t.id t))


(deftest test-an-urgent-task-stops-a-preemptible-lower-task-and-runs-at-once
  (<- low TaskRecord (running "a-low" -1))
  (<- urgent TaskRecord (queued "b-urgent" 10 :memory-mib URGENT-NEED-MIB))
  (<- rows dict (placed-rows {"a-low" low "b-urgent" urgent} {"a-low" LOW-USE-MIB}))
  (val placed (get rows "b-urgent"))
  (assert (= #(placed.phase placed.worker) #("assigned" "node-a")) placed)
  (assert (in "a-low" placed.detail) placed.detail)
  ;; 止めた task の前の試行の行は消え(worker は返事から外れた job として止める)、次の試行が待ちの行で戻る。
  (assert (not-in "a-low" rows) rows)
  (val again (get rows "a-low-r2"))
  (assert (= #(again.phase again.worker again.attempt again.losses again.preempted-by) #("queued" None 2 0 "b-urgent")) again)
  (assert (in "場所を譲る" again.detail) again.detail)
  (assert (= again.key "a-low") again))


(deftest test-without-the-preemptible-mark-the-urgent-task-waits
  ;; 失敗ケース(直す前の形): 譲る名乗りの無い task は止めず、急ぎの task は memory の空きを待つ。
  (<- low TaskRecord (running "a-low" -1 :preemptible False))
  (<- urgent TaskRecord (queued "b-urgent" 10 :memory-mib URGENT-NEED-MIB))
  (<- rows dict (placed-rows {"a-low" low "b-urgent" urgent} {"a-low" LOW-USE-MIB}))
  (assert (= (. (get rows "b-urgent") phase) "queued") (get rows "b-urgent"))
  (assert (= (. (get rows "a-low") phase) "assigned") (get rows "a-low")))


(deftest test-a-task-of-the-same-priority-or-a-worker-that-cannot-fit-is-not-stopped
  ;; 同じ優先の task は譲らない。
  (<- peer TaskRecord (running "a-peer" 10))
  (<- urgent TaskRecord (queued "b-urgent" 10 :memory-mib URGENT-NEED-MIB))
  (<- rows dict (placed-rows {"a-peer" peer "b-urgent" urgent} {"a-peer" LOW-USE-MIB}))
  (assert (= (. (get rows "b-urgent") phase) "queued") (get rows "b-urgent"))
  (assert (= (. (get rows "a-peer") phase) "assigned") (get rows "a-peer"))
  ;; 譲る task を全部止めても要りに届かない worker では止めない(仕事を失わせるだけの止めをしない)。
  (<- low TaskRecord (running "a-low" -1))
  (<- huge TaskRecord (queued "b-huge" 10 :memory-mib 8192))
  (<- rows dict (placed-rows {"a-low" low "b-huge" huge} {"a-low" LOW-USE-MIB}))
  (assert (= (. (get rows "b-huge") phase) "queued") (get rows "b-huge"))
  (assert (= (. (get rows "a-low") phase) "assigned") (get rows "a-low")))


(deftest test-an-ungated-urgent-task-also-makes-room-on-the-worker-it-takes
  ;; 門の外の急ぎの task(要りを名乗らない — 要り 1024 MiB と見込む)は空きを判じずに置かれるが、空き 1000 MiB に入らないので、譲る task を
  ;; 止めてから置く(止めないと見張りに止められる形 — 2026-11-11)。
  (<- low TaskRecord (running "a-low" -1))
  (<- swap TaskRecord (queued "b-swap" 10 :ungated True :limits #((TaskLimit :group GROUP))))
  (<- rows dict (placed-rows {"a-low" low "b-swap" swap} {"a-low" LOW-USE-MIB}))
  (assert (= (. (get rows "b-swap") phase) "assigned") (get rows "b-swap"))
  (assert (= (. (get rows "a-low-r2") preempted-by) "b-swap") rows)
  ;; 急ぎでない門の外の task は今までどおり止めずに置く。
  (<- plain TaskRecord (queued "b-swap" 0 :ungated True :limits #((TaskLimit :group GROUP))))
  (<- rows dict (placed-rows {"a-low" low "b-swap" plain} {"a-low" LOW-USE-MIB}))
  (assert (= (. (get rows "b-swap") phase) "assigned") (get rows "b-swap"))
  (assert (= (. (get rows "a-low") phase) "assigned") (get rows "a-low")))


(defk noop []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "構築関数へ渡す Program の代わり(走らせない — 構築関数が送る前に断るかだけを見る)。"
  1)


(deftest test-the-door-and-the-builder-take-preemptible-only-with-rerun
  (assert (is (task-preemptible-refusal True True) None))
  (assert (is (task-preemptible-refusal None False) None))
  (assert (is-not (task-preemptible-refusal True False) None))
  (assert (is-not (task-preemptible-refusal "yes" True) None))
  (assert (= #((task-preemptible-json True) (task-preemptible-json False)) #({"preemptible" True} {})))
  (with [raised (pytest.raises TypeError)]
    (<- (submit-detached-task (noop) "k" :needs (frozenset #("host-node-a")) :memory-mib 1024 :preemptible True)))
  (assert (in "preemptible" (str raised.value)) raised.value)
  ;; 受け口: 走らせ直しを名乗らない本文は 400、名乗る本文は行に名乗りを持つ。
  (<- placed tuple (program-placed (ClusterState) {"python" "3"}))
  (setv #(placed-state sha) placed)
  (<- workers (table-from-rows {"node-a" (! (worker-with {}))}))
  (val state (replace placed-state :workers workers))
  (for [#(body status) [#({"preemptible" True} 400) #({"preemptible" True "rerun" True} 200)]]
    (<- parsed TaskBody (task-body-of (| {"program" sha "revision" "rev" "name" "low" "needs" ["host-node-a"] "memoryMiB" 1024} body)))
    (val reply (submit-detached state "k" parsed 0))
    (assert (= reply.status status) #(body reply.status reply.body))
    (when (= status 200)
      (assert (. (get (list (.rows reply.state.tasks)) 0) preemptible) reply.state.tasks))))


(deftest test-the-stored-row-keeps-preemptible-and-preempted-by
  (<- low TaskRecord (replace (running "a-low" -1) :preempted-by "b-urgent"))
  (<- back TaskRecord (task-record-from-json (task-record-to-json low)))
  (assert (= #(back.preemptible back.preempted-by) #(True "b-urgent")) back)
  (<- plain TaskRecord (running "a-plain" 0 :preemptible False))
  (val spelled (task-record-to-json plain))
  (assert (not-in "preemptible" spelled) spelled)
  (assert (not-in "preempted_by" spelled) spelled)
  (<- old TaskRecord (task-record-from-json spelled))
  (assert (= #(old.preemptible old.preempted-by) #(False "")) old))


(defk unit [job mib]
  {:pre [(: job str) (: mib int)] :post [(: % KillUnit)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の job の止める単位 1 つ(TASK — 頭の process 1 つ)を作るため。"
  (val head (ProcRow :pid (+ 100 mib) :ppid 1 :start-ticks 1 :rss-bytes (* mib MIB) :comm "hy"))
  (KillUnit :job job :kind UnitKind.TASK :head head :bytes (* mib MIB) :members #(head)))


(deftest test-the-memory-watch-stops-an-urgent-task-last
  (assert (= URGENT-PRIORITY KEEP-NOW-PRIORITY))
  ;; 返事の行の priority を読む(無い・真偽の値は 0)。
  (<- needs tuple (task-needs-of #({"id" "u" "memoryMiB" 1024 "priority" 10} {"id" "p" "memoryMiB" 512 "priority" True} {"id" "q"})))
  (assert (= (tuple (gfor n needs n.priority)) #(10 0 0)) needs)
  (<- urgent frozenset (urgent-jobs needs))
  (assert (= urgent (frozenset #("task/u"))) urgent)
  ;; DANGER の止め: 急ぎの task がいちばん大きくても、ほかの候補が残る間は選ばない。ほかに候補が無ければ選ぶ。
  (<- big KillUnit (unit "task/u" 3000))
  (<- small KillUnit (unit "task/p" 800))
  (val uses #((JobUse :job "task/u" :pid 1 :bytes (* 3000 MIB) :processes 1) (JobUse :job "task/p" :pid 2 :bytes (* 800 MIB) :processes 1)))
  (<- chosen KillUnit (victim-of #(big small) uses #() (MemoryLimits) :urgent urgent))
  (assert (= chosen.job "task/p") chosen)
  (<- before KillUnit (victim-of #(big small) uses #() (MemoryLimits)))
  (assert (= before.job "task/u") before)
  (<- alone KillUnit (victim-of #(big) uses #() (MemoryLimits) :urgent urgent))
  (assert (= alone.job "task/u") alone)
  ;; 要りの越えの止め: 越えの大きい急ぎの task より、越えた急ぎでない task を先に並べる。
  (<- over tuple (over-need-tasks needs uses (* 500 MIB)))
  (assert (= (tuple (gfor t over t.id)) #("p")) over))
