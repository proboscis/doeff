;;; 測った空きの門の外の task(coordinator/core/measured_room の頭の註の測った空きの門の外の task・2026-11-11)の検: 送り手が ungated を
;;; 名乗った切り離した task は、機体の測った空き(CPU・memory・PSI)と予約を判じずに置かれ、同時数は系の宣言の組の上限だけが限る。
;;;   (1) 測った空きが 0 の worker(memory の空きが床の下・CPU が埋まる・PSI が止めの線の上)に、門の外の task は 1 拍で assigned になり、
;;;       同じ形の門の内の task は queued のまま待つ
;;;   (2) 失敗ケース: ungated を名乗らなければ(直す前の形)、同じ task も queued のまま待つ
;;;   (3) 門の外の task も、名乗った組の上限(scope cluster・limit 1)に達していれば queued で待つ(cluster で 1 本の錠)
;;;   (4) 受け口: 門の外で memoryMiB を名乗る本文・組の無い本文・真偽でない ungated は 400、門の外の本文は 200 で行が ungated を持つ。
;;;       POST /tasks は ungated を受けない
;;;   (5) 構築関数 submit-detached-task も同じ規則で送る前に断り、本文の欄は名乗る時だけ置く
;;;   (6) 保存の綴りの往復で ungated が残り、欄の無い旧い行は門の内で読む
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import pytest)
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo TaskLimit GroupLimit submit-detached-task]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad TaskBody])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text submit-task])
(import doeff_cluster._coordinator.core.detached_policy [submit-detached])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB CPU-PRESSURE-STOP])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._shared.core.detached_rules [task-room-claim-refusal task-ungated-json])
(import doeff_hy._table [TableWrite table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed task-body-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
;; 系が宣言する門の外の task の組(cluster で 1 本)。
(val GROUP "swap")
(val GROUPS (table-of #((TableWrite GROUP (GroupLimit :group GROUP :scope "cluster" :limit 1)))))
(val SWAP-LIMITS #((TaskLimit :group GROUP)))
;; 門の内の task の要り(門の外の task は要りを名乗らない)。
(val NEED-MIB 1024)


(defk noop []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "構築関数へ渡す Program の代わり(走らせない — 構築関数が送る前に断るかだけを見る)。"
  1)


(defk crowded-worker []
  {:pre [] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "測った空きが 0 の node-a を作るため: memory の空きは床の下・CPU は埋まり・CPU と memory の PSI は止めの線の上。"
  (WorkerInfo "node-a" #("host-node-a") NOW :versions PY3
              :measured (MeasuredLoad :at-ms NOW :cpus 8 :cpu-busy 1.0 :memory-total-MiB 16384
                                      :memory-available-MiB (- MEMORY-FLOOR-MIB 512)
                                      :cpu-some-avg10 (+ CPU-PRESSURE-STOP 50.0) :memory-some-avg10 60.0)))


(defk queued [id * [ungated False] [limits #()] [memory-mib None]]
  {:pre [(: id str) (: ungated bool) (: limits tuple) (: memory-mib (| int None))] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-a を要る、待っている切り離した task(門の外の名乗り ungated・組 limits・要り memory-mib)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-a") 15000 20000 0 :detached True :key id
              :limits limits :memory-mib memory-mib :ungated ungated))


(defk placed-rows [rows]
  {:pre [(: rows dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "測った空きが 0 の node-a 1 台と組の宣言 GROUPS の状態で、待っている task rows(id → 行)を置き先に 1 拍回し、id → 行を得るため。"
  (<- workers (table-from-rows {"node-a" (! (crowded-worker))}))
  (<- tasks (table-from-rows rows))
  (val state (replace (ClusterState #() workers (table-of #()) tasks) :group-limits GROUPS))
  (val placed (! (tasks-placed NOW state state.placements T)))
  (dfor id rows id (.row placed id)))


(deftest test-an-ungated-task-is-placed-on-a-worker-with-no-measured-room
  (<- swap TaskRecord (queued "a-swap" :ungated True :limits SWAP-LIMITS))
  (<- plain TaskRecord (queued "b-plain" :memory-mib NEED-MIB))
  (<- rows dict (placed-rows {"a-swap" swap "b-plain" plain}))
  (assert (= (. (get rows "a-swap") phase) "assigned") (get rows "a-swap"))
  (assert (= (. (get rows "a-swap") worker) "node-a") (get rows "a-swap"))
  ;; 門の内の task は同じ worker の空きの無さで待つ(失敗にしない)。
  (assert (= (. (get rows "b-plain") phase) "queued") (get rows "b-plain")))


(deftest test-without-ungated-the-same-task-waits-for-room
  ;; 失敗ケース(直す前の形): 門の外を名乗らない task は、組と要りが同じでも測った空きの無さで queued のまま待つ。
  (<- swap TaskRecord (queued "a-swap" :limits SWAP-LIMITS :memory-mib NEED-MIB))
  (<- rows dict (placed-rows {"a-swap" swap}))
  (assert (= (. (get rows "a-swap") phase) "queued") (get rows "a-swap")))


(deftest test-an-ungated-task-waits-behind-its-group-limit
  ;; cluster で 1 本の錠: 同じ組の門の外の task 2 本は、1 本だけが置かれ、もう 1 本は組の上限で待つ。
  (<- first TaskRecord (queued "a-swap" :ungated True :limits SWAP-LIMITS))
  (<- second TaskRecord (queued "b-swap" :ungated True :limits SWAP-LIMITS))
  (<- rows dict (placed-rows {"a-swap" first "b-swap" second}))
  (assert (= (. (get rows "a-swap") phase) "assigned") (get rows "a-swap"))
  (assert (= (. (get rows "b-swap") phase) "queued") (get rows "b-swap"))
  (<- limited str (task-unplaced-text TaskUnplacedKind.TASK-LIMITED))
  (assert (in limited (. (get rows "b-swap") detail)) (get rows "b-swap")))


(defk submitted [key body]
  {:pre [(: key str) (: body dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "詰めた Program を置き場に置き、測った空きが 0 の node-a と組の宣言 GROUPS を持つ状態へ、本文 body を受け口と同じ解きで
   PUT /detached/<key> として送った #(status 答え 状態) を得るため。"
  (<- placed tuple (program-placed (ClusterState) {"python" "3"}))
  (setv #(placed-state sha) placed)
  (<- workers (table-from-rows {"node-a" (! (crowded-worker))}))
  (val state (replace placed-state :workers workers :group-limits GROUPS))
  (<- parsed TaskBody (task-body-of (| {"program" sha "revision" "rev" "name" "swap" "needs" ["host-node-a"]} body)))
  (val reply (submit-detached state key parsed 0))
  #(reply.status reply.body reply.state))


(deftest test-the-door-refuses-an-ungated-body-with-a-memory-need-or-without-a-group
  (for [bad [{"ungated" True "memoryMiB" NEED-MIB "limits" [{"group" GROUP}]}
             {"ungated" True}
             {"ungated" "yes" "memoryMiB" NEED-MIB}]]
    (setv #(status answer state) (! (submitted "k" bad)))
    (assert (= status 400) #(bad status answer))
    (assert (in "ungated" answer.message) #(bad answer.message))
    (assert (= (len (list (.rows state.tasks))) 0) state.tasks))
  ;; 門の外の本文(要りを名乗らず組を名乗る)は受けて、行が門の外を持つ。
  (setv #(status answer state) (! (submitted "k" {"ungated" True "limits" [{"group" GROUP}]})))
  (assert (= status 200) #(status answer))
  (val rows (list (.rows state.tasks)))
  (assert (= (len rows) 1) rows)
  (assert (. (get rows 0) ungated) rows)
  (assert (is (. (get rows 0) memory-mib) None) rows)
  ;; 門の内の本文は今までどおり要りが必須で、行は門の内。
  (setv #(status answer state) (! (submitted "k" {"memoryMiB" NEED-MIB})))
  (assert (= status 200) #(status answer))
  (assert (not (. (get (list (.rows state.tasks)) 0) ungated)) state.tasks))


(deftest test-post-tasks-does-not-take-ungated
  (<- placed tuple (program-placed (ClusterState) {"python" "3"}))
  (setv #(placed-state sha) placed)
  (<- parsed TaskBody (task-body-of {"program" sha "revision" "rev" "name" "swap" "needs" ["host-node-a"] "ungated" True}))
  (setv #(_ status answer) (cut (! (submit-task placed-state parsed 0)) 0 3))
  (assert (= status 400) #(status answer))
  (assert (in "ungated" answer.message) answer.message))


(deftest test-the-builder-refuses-the-same-ungated-sends
  (assert (is (task-room-claim-refusal True None 1) None))
  (assert (is-not (task-room-claim-refusal True NEED-MIB 1) None))
  (assert (is-not (task-room-claim-refusal True None 0) None))
  (assert (is (task-room-claim-refusal False NEED-MIB 0) None))
  (assert (is-not (task-room-claim-refusal False None 1) None))
  (with [raised (pytest.raises TypeError)]
    (<- (submit-detached-task (noop) "k" :needs (frozenset #("host-node-a")) :memory-mib NEED-MIB :ungated True :limits SWAP-LIMITS)))
  (assert (in "ungated" (str raised.value)) raised.value)
  (with [raised (pytest.raises TypeError)]
    (<- (submit-detached-task (noop) "k" :needs (frozenset #("host-node-a")) :memory-mib None :ungated True)))
  (assert (in "limits" (str raised.value)) raised.value)
  ;; 送り手の本文の欄: 名乗る時だけ ungated を置く(旧い coordinator は知らない欄を捨てる)。
  (assert (= #((task-ungated-json True) (task-ungated-json False)) #({"ungated" True} {}))))


(deftest test-the-stored-row-keeps-ungated
  (<- swap TaskRecord (queued "a-swap" :ungated True :limits SWAP-LIMITS))
  (<- back TaskRecord (task-record-from-json (task-record-to-json swap)))
  (assert back.ungated back)
  (<- plain TaskRecord (queued "b-plain" :memory-mib NEED-MIB))
  (val spelled (task-record-to-json plain))
  (assert (not-in "ungated" spelled) spelled)
  (<- old TaskRecord (task-record-from-json spelled))
  (assert (not old.ungated) old))
