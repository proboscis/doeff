;;; 待っている切り離した task の置き先を替える口(POST /detached/<key>/retarget・effect RetargetDetached — 2026-11-17)の検。
;;; 送った後に作業場の書き手が別の機体へ移る呼び手(上に載る系の、送った時の機体の印で列に待つ間に作業場の書き手が
;;; 別の機体へ移った)は、まだ走り出していない task の needs の機体の印を新しい機体の印へ替え、task を新しい機体で走り出させる。
;;;   1  待っている task の needs の host-node-h を host-node-z へ替えると、次の置きで node-z に置かれる。試行の番号と積んだ刻は変わらない。
;;;      失敗ケース: 替えなければ同じ置きで node-h に置かれる(送った時の機体に固まる — 直す前の上に載る系の形)
;;;   2  置いた後の task は替えない(moved 偽・phase と needs はそのまま — 走り出した試行の置き先はその試行の物)
;;;   3  同じ頼みの 2 度目は替えない(needs が away を持たない)
;;;   4  新しい needs を満たす生きた worker が無い替えは替えない(替えると place-tasks が置き先の無い task として終える)
;;;   5  知らない key は moved 偽・phase unknown。away か toward が空の本文は 400
;;;   6  sim の client(RetargetDetached)は本番と同じ口で替え、待っていた task は新しい機体の worker で走り出す。ReadDetachedTasks は担い手と組の名を読む
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_hy._table [Table table-of])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [ClusterTiming WorkerInfo ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [ComponentVersion DetachedRetargeted ErrorReply])
(import doeff_cluster._coordinator.intent.request_bodies [TaskBody RetargetBody BodyMalformed])
(import doeff_cluster._coordinator.protocol.request_bodies [body-of])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed])
(import doeff_cluster._coordinator.core.detached_policy [submit-detached retarget-detached task-by-key])
(import doeff_cluster [http-request]
        doeff_cluster._shared.protocol.detached [detached-path])
(import doeff_cluster [TaskLimit GroupLimit AwaitDetached DetachedPending RetargetDetached DetachedRetarget ReadDetachedTasks DetachedTasks
                       DetachedTaskPhase])
(import doeff_cluster [submit-detached-task system-of sim-cluster SimWorker])
(import tests.room_rows [ROOM-LOADS compartments-like-rooms])
(import tests.program_rows [program-placed task-body-of])
(import tests.state_rows [table-from-rows])
(import tests.detached_rig [slow-add RIG-PROVIDES])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val KEY "run/caller-a/guard-sim")
;; 置きの刻(worker の最後の連絡 0 から lease の窓 10 秒の内 — どちらの worker も生きている)。
(val NOW 1000)


(defk two-machines []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "機体の印の違う 2 台の worker(node-h・node-z — どちらも tasks 区画に空きが在る)と、詰めた Program を置き場に置いた状態の組 #(状態 sha) を
   作るため。"
  (<- placed (get tuple #(ClusterState str)) (program-placed (ClusterState) {"python" "3"}))
  (setv #(state sha) placed)
  (val workers {"node-h" (WorkerInfo "node-h" #("host-node-h") 0 :measured (get ROOM-LOADS 30) :versions PY3)
                "node-z" (WorkerInfo "node-z" #("host-node-z") 0 :measured (get ROOM-LOADS 30) :versions PY3)})
  (<- compartments Table (compartments-like-rooms workers))
  #((replace state :workers (! (table-from-rows workers)) :machine-compartments compartments) sha))


(defk queued-on-h [state sha]
  {:pre [(: state ClusterState) (: sha str)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "機体 node-h の印を needs に持つ切り離した task 1 本を、受け口と同じ解きで PUT /detached/<KEY> として送った状態を作るため(まだ置かない —
   待っている)。走らせ直しを名乗る(試行の番号を読むため)。"
  (<- body TaskBody (task-body-of {"program" sha "revision" "rev" "name" "guard-sim" "needs" ["host-node-h"] "memoryMiB" 1024 "rerun" True}))
  (val reply (submit-detached state KEY body 0))
  (assert (= reply.status 200) reply)
  reply.state)


(defk retarget-request [state away toward]
  {:pre [(: state ClusterState) (: away list) (: toward list)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "置き先の替えの本文を受け口と同じ解きで POST /detached/<KEY>/retarget として送った #(status 答え 状態) を得るため。"
  (<- parsed (body-of (! (http-request "POST" (detached-path KEY "/retarget") {}
                                       {"away" away "toward" toward "reason" "作業場の書き手が node-z へ移った"}))))
  (assert (isinstance parsed RetargetBody) parsed)
  (val reply (retarget-detached state KEY parsed NOW T))
  #(reply.status reply.body reply.state))


(defk placed-worker [state]
  {:pre [(: state ClusterState)] :post [(: % (| str None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "1 回の置き(tasks-placed)の後に KEY の task を置いた worker の名を読むため(置かなければ None)。"
  (val placed (! (tasks-placed NOW state (table-of #()) T)))
  (. (task-by-key (replace state :tasks placed) KEY) worker))


(deftest test-a-queued-task-retargeted-to-the-new-machine-is-placed-there
  ;; 1: 待っている task の needs の host-node-h を host-node-z へ替えると、次の置きで node-z に置かれる。試行の番号と積んだ刻は替えの前のまま。
  (setv #(state sha) (! (two-machines)))
  (val queued (! (queued-on-h state sha)))
  (val before (task-by-key queued KEY))
  (setv #(status answer after) (! (retarget-request queued ["host-node-h"] ["host-node-z"])))
  (assert (= status 200) #(status answer))
  (assert (= answer (DetachedRetargeted :key KEY :moved True :phase "queued" :needs #("host-node-z") :detail "")) answer)
  (val moved (task-by-key after KEY))
  (assert (= #(moved.needs moved.attempt moved.submitted-ms moved.id) #(#("host-node-z") before.attempt before.submitted-ms before.id)) moved)
  (assert (in "作業場の書き手が node-z へ移った" moved.detail) moved.detail)
  (assert (= (! (placed-worker after)) "node-z") after))


(deftest test-without-the-retarget-the-queued-task-runs-on-the-machine-it-was-sent-for
  ;; 1 の失敗ケース: 替えなければ、同じ置きで送った時の機体 node-h に置かれる(作業場の書き手が node-z へ移っていても — 上に載る系の直す前の形)。
  (setv #(state sha) (! (two-machines)))
  (val queued (! (queued-on-h state sha)))
  (assert (= (! (placed-worker queued)) "node-h") queued))


(deftest test-a-task-already-placed-keeps-its-placement
  ;; 2: 置いた後(assigned)の task は替えない — 答えは moved 偽と訳、行は変わらない。
  (setv #(state sha) (! (two-machines)))
  (val queued (! (queued-on-h state sha)))
  (val running (replace queued :tasks (! (tasks-placed NOW queued (table-of #()) T))))
  (assert (= (. (task-by-key running KEY) phase) "assigned") running)
  (setv #(status answer after) (! (retarget-request running ["host-node-h"] ["host-node-z"])))
  (assert (and (= status 200) (not answer.moved) (= answer.phase "assigned") (= answer.needs #("host-node-h"))) answer)
  (assert (in "走り出した試行" answer.detail) answer.detail)
  (assert (is after running) after))


(deftest test-the-same-retarget-twice-moves-once
  ;; 3: 同じ頼みの 2 度目は替えない(needs がもう away を持たない)— 送り直してよい。
  (setv #(state sha) (! (two-machines)))
  (val queued (! (queued-on-h state sha)))
  (setv #(_ first after-first) (! (retarget-request queued ["host-node-h"] ["host-node-z"])))
  (setv #(status second after-second) (! (retarget-request after-first ["host-node-h"] ["host-node-z"])))
  (assert first.moved first)
  (assert (and (= status 200) (not second.moved) (= second.needs #("host-node-z"))) second)
  (assert (is after-second after-first) after-second))


(deftest test-a-retarget-no-live-worker-can-take-is-not-made
  ;; 4: 新しい needs を満たす生きた worker が無い替え(node-x の印)は替えない — 替えると置き先の無い task として終わる。行はそのまま待つ。
  (setv #(state sha) (! (two-machines)))
  (val queued (! (queued-on-h state sha)))
  (setv #(status answer after) (! (retarget-request queued ["host-node-h"] ["host-node-x"])))
  (assert (and (= status 200) (not answer.moved) (= answer.needs #("host-node-h"))) answer)
  (assert (in "生きた worker が無い" answer.detail) answer.detail)
  (assert (is after queued) after))


(deftest test-an-unknown-key-and-an-empty-mark-list-are-answered
  ;; 5: 知らない key は moved 偽・phase unknown・needs 空。away か toward が空の本文は 400。
  (setv #(state _) (! (two-machines)))
  (setv #(status answer _) (! (retarget-request state ["host-node-h"] ["host-node-z"])))
  (assert (= #(status answer) #(200 (DetachedRetargeted :key KEY :moved False :phase "unknown" :needs #() :detail "その key の task は無い")))
          answer)
  (setv #(empty-status empty-answer _) (! (retarget-request state [] ["host-node-z"])))
  (assert (and (= empty-status 400) (isinstance empty-answer ErrorReply)) #(empty-status empty-answer)))


;; --- sim の client(RetargetDetached)------------------------------------------------------------------

;; 機体ごとに 1 本の組 probe(1 台目に置いた長い task の後ろで、2 本目が待つ)。worker 2 台は機体の印だけが違う。
(val SYSTEM (system-of "retarget-scenarios" #() :task-limits #((GroupLimit :group "probe" :scope "worker" :limit 1))))
(val ON-H (frozenset #(#* RIG-PROVIDES "host-h")))
(val ON-Z (frozenset #(#* RIG-PROVIDES "host-z")))


(defk retarget-scenario []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "機体 h の印を持つ task 2 本(組 probe)を出し、1 本目が w-h に置かれて 2 本目が待つ間に 2 本目の置き先を h → z へ替え、2 本目が走り出した後の
   一覧の読みと、替えの答え 3 つ(待つ task・同じ頼みの 2 度目・置いた task)を返すため。"
  (<- (submit-detached-task (slow-add 30.0 1) :needs ON-H :key "busy" :lease-seconds 5.0 :limits #((TaskLimit :group "probe")) :memory-mib 1024))
  (<- (submit-detached-task (slow-add 1.0 2) :needs ON-H :key "guard" :lease-seconds 5.0 :limits #((TaskLimit :group "probe")) :memory-mib 1024))
  (var waited 0.0)
  (while True
    (assert (< waited 20.0) "1 本目が 20 秒の内に置かれない")
    (<- busy (AwaitDetached "busy" :timeout-seconds 0.0))
    (when (and (isinstance busy DetachedPending) (= busy.phase "assigned"))
      (break))
    (<- (Delay 0.5))
    (:= waited (+ waited 0.5)))
  (<- queued DetachedTasks (ReadDetachedTasks ""))
  (<- moved DetachedRetarget (RetargetDetached "guard" (frozenset #("host-h")) (frozenset #("host-z")) :reason "作業場の書き手が z へ移った"))
  (<- again DetachedRetarget (RetargetDetached "guard" (frozenset #("host-h")) (frozenset #("host-z")) :reason "作業場の書き手が z へ移った"))
  (<- kept DetachedRetarget (RetargetDetached "busy" (frozenset #("host-h")) (frozenset #("host-z")) :reason "作業場の書き手が z へ移った"))
  (:= waited 0.0)
  (while True
    (assert (< waited 20.0) "替えた 2 本目が 20 秒の内に置かれない")
    (<- guard (AwaitDetached "guard" :timeout-seconds 0.0))
    (when (not (and (isinstance guard DetachedPending) (= guard.phase "queued")))
      (break))
    (<- (Delay 0.5))
    (:= waited (+ waited 0.5)))
  (<- listed DetachedTasks (ReadDetachedTasks ""))
  {"queued" queued "moved" moved "again" again "kept" kept "listed" listed})


(deftest test-the-sim-client-retargets-a-queued-task-to-the-other-machine
  ;; 6: 1 本目が w-h の組の枠を持つ間、2 本目は待つ。置き先を h → z へ替えると w-z で走り出す(w-h の枠が空くのを待たない)。同じ頼みの 2 度目と、
  ;; 置いた 1 本目の替えは替えない。一覧の読みは担い手(待つ行は None)と名乗った組 probe を持つ。
  (<- state dict (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) SYSTEM (retarget-scenario)
                              :workers #((SimWorker :name "w-h" :provides ON-H) (SimWorker :name "w-z" :provides ON-Z))))
  (assert (= (get state "queued") (DetachedTasks :tasks #((DetachedTaskPhase :key "busy" :phase "assigned" :worker "w-h" :groups #("probe"))
                                                          (DetachedTaskPhase :key "guard" :phase "queued" :groups #("probe")))))
          (get state "queued"))
  (val moved (get state "moved"))
  (assert (and moved.moved (= moved.needs (tuple (sorted ON-Z)))) moved)
  (assert (not (. (get state "again") moved)) (get state "again"))
  (val kept (get state "kept"))
  (assert (and (not kept.moved) (= kept.phase "assigned")) kept)
  (val guard (next (gfor row (. (get state "listed") tasks) :if (= row.key "guard") row)))
  (assert (= guard.worker "w-z") (get state "listed")))
