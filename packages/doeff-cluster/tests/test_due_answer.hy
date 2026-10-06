;; 期限の関数の答えは「刻・今すぐ・無し」の閉じた型(#3865 の単位 1)。
;;
;; 期限の関数は、状態がこのままで判断の答えが変わる最初の刻を返す。今まで「行が在れば次の拍」「まだ落ち着いていない」を now + 1 の
;; 数で返していたので、1 秒の格子に乗らない待ちでは「1 ms 先の期限」と「すぐもう 1 歩」を見分けられなかった。
;; - 置いた切り離していない task: 担い手の生死の窓(reassign-after-ms)が切れる刻(place-tasks が task を失敗にする刻)。
;; - 置ける生きた worker の在る待っている task: その worker の生死の窓(lease-ms)が切れる最初の刻(置き先の候補が変わる刻)。
;; - まだ落ち着いていない(生きていないと数える名の求め直しが違う・期限が過ぎている): 今すぐ。
;; - Rollout の進行中に Kubernetes を読む: 名のある読みの周期の後の刻。
;; どれも、返す刻の 1 ms 前では判断が状態を変えず、その刻で変える。
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming TaskRecord WorkerInfo ComponentVersion BoardRow])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.core.cluster_policy [place-tasks task-due liveness-due sweep-due note-liveness liveness-deadline])
(import doeff_cluster.coordinator.core.api_policy [tick tick-due])


(val PYTHON #((ComponentVersion "python" "3")))


(defk worker-at [name last-seen capacity]
  {:pre [(: name str) (: last-seen int) (: capacity int)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "筋書きの worker(能力 cpu・python 3・最後の連絡 last-seen)を作るため。"
  (replace (WorkerInfo name #("cpu") capacity last-seen :task-reserve 0) :versions PYTHON))


(defk task-on [id phase worker]
  {:pre [(: id str) (: phase str) (: worker (| str None))] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "筋書きの切り離していない task(能力 cpu・lease の期限は遠い 1000 秒目 — 呼び手は問い合わせを続けている)を作るため。"
  (TaskRecord id "digest" None "rev" PYTHON #("cpu") 15000 1000000 0 :phase phase :worker worker))


(defk settled-state [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "now の place-tasks を当てた後の状態(待つ task は待ちの理由を書いた後)を求めるため — 期限の関数の前提の、落ち着いた状態。"
  (<- tasks dict (place-tasks now state {} timing))
  (replace state :tasks tasks))


(deftest test-a-placed-task-is-due-when-its-worker-falls-out-of-the-reassign-window
  ;; 置いた task の担い手 w(最後の連絡 0): place-tasks が task を失敗にするのは reassign-after-ms の窓が切れる刻。直す前は now + 1(数)。
  (val timing (ClusterTiming))
  (<- w WorkerInfo (worker-at "w" 0 2))
  (<- run TaskRecord (task-on "run" "assigned" "w"))
  (<- state ClusterState (settled-state (ClusterState :workers {"w" w} :tasks {"run" run}) 5000 timing))
  (<- due (| DueAt DueNow DueNever) (task-due state 5000 timing))
  (val expected (+ (liveness-deadline w timing.reassign-after-ms) 1))
  (assert (= due (DueAt :at expected)) due)
  (<- before dict (place-tasks (- expected 1) state {} timing))
  (<- at dict (place-tasks expected state {} timing))
  (assert (= before state.tasks) "期限の 1 ms 前に変わった")
  (assert (= (. (get at "run") phase) "failed") at))


(deftest test-a-queued-task-with-a-live-capable-worker-is-due-when-that-worker-falls-silent
  ;; 置ける生きた worker w(最後の連絡 0・空き 1 を run が使う)に、待っている task wait: 空きが無いので待つ。時刻で置き先の候補が変わるのは
  ;; w の lease-ms の窓が切れる刻(待ちの理由が「連絡していない」に変わる)。直す前は now + 1(数)。
  (val timing (ClusterTiming))
  (<- w WorkerInfo (worker-at "w" 0 1))
  (<- run TaskRecord (task-on "run" "assigned" "w"))
  (<- wait TaskRecord (task-on "wait" "queued" None))
  (<- state ClusterState (settled-state (ClusterState :workers {"w" w} :tasks {"run" run "wait" wait}) 5000 timing))
  (<- due (| DueAt DueNow DueNever) (task-due state 5000 timing))
  (val expected (+ (liveness-deadline w timing.lease-ms) 1))
  (assert (= due (DueAt :at expected)) due)
  (<- before dict (place-tasks (- expected 1) state {} timing))
  (<- at dict (place-tasks expected state {} timing))
  (assert (= before state.tasks) "期限の 1 ms 前に変わった")
  (assert (!= (. (get at "wait") detail) (. (get state.tasks "wait") detail)) at))


(deftest test-an-unsettled-state-is-due-now-not-one-millisecond-later
  ;; まだ落ち着いていない状態は「今すぐ」。数の now + 1 ではない。
  (val timing (ClusterTiming))
  ;; 生きていないと数える名の求め直しが、状態の欄と違う(note-liveness をまだ当てていない)。
  (<- w WorkerInfo (worker-at "w" 0 1))
  (val unsettled (ClusterState :workers {"w" w}))
  (<- live (| DueAt DueNow DueNever) (liveness-due unsettled (+ timing.lease-ms 1) timing))
  (assert (= live (DueNow)) live)
  ;; 掃除の期限が過ぎている(掃く前の状態)。
  (val expired (ClusterState :board {"k" (BoardRow :value 1 :version 1 :expires-ms 100 :size 1)}))
  (<- swept (| DueAt DueNow DueNever) (sweep-due expired 200 timing))
  (assert (= swept (DueNow)) swept)
  ;; task の期限が過ぎている(place-tasks をまだ当てていない)。
  (val lapsed (replace (! (task-on "call" "finished" "w")) :lease-until-ms 100 :finished-ms 50))
  (<- tasked (| DueAt DueNow DueNever) (task-due (ClusterState :tasks {"call" lapsed}) 200 timing))
  (assert (= tasked (DueNow)) tasked))


(deftest test-a-state-without-deadlines-is-never-due
  ;; 期限の無い状態は「無し」(数の None ではない)。
  (val timing (ClusterTiming))
  (<- empty-live (| DueAt DueNow DueNever) (liveness-due (ClusterState) 0 timing))
  (<- empty-task (| DueAt DueNow DueNever) (task-due (ClusterState) 0 timing))
  (<- empty-sweep (| DueAt DueNow DueNever) (sweep-due (ClusterState) 0 timing))
  (assert (= #(empty-live empty-task empty-sweep) #((DueNever) (DueNever) (DueNever))) #(empty-live empty-task empty-sweep)))


(defk settled-scenarios []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "落ち着いた状態の筋書き #(名 状態) を並べるため(どれも、要求の無い歩を進めても状態が変わらない — 長く続きうる): 置ける worker が全部
   埋まっていて待つ task・能力の合う worker が黙っていて待つ task・置いた task と生きた担い手・何も無い状態。"
  (<- busy WorkerInfo (worker-at "w" 14000 1))
  (<- silent WorkerInfo (worker-at "s" 0 1))
  (<- run TaskRecord (task-on "run" "assigned" "w"))
  (<- wait TaskRecord (task-on "wait" "queued" None))
  #(#("置ける worker が埋まっていて待つ task" (ClusterState :workers {"w" busy} :tasks {"run" run "wait" wait}))
    #("能力の合う worker が黙っていて待つ task" (ClusterState :workers {"s" silent} :tasks {"wait" wait}))
    #("置いた task と生きた担い手" (ClusterState :workers {"w" busy} :tasks {"run" run}))
    #("何も無い状態" (ClusterState))))


(deftest test-a-settled-state-is-never-due-now
  ;; 要求の無い歩(tick)を 1 つ進めて落ち着いた状態(もう 1 歩進めても変わらない)では、期限の答えは今すぐ(DueNow)ではない。
  ;; 待ち方を期限まで待つ形に替えた時に、待たずの歩が回り続けないための守り(#3865 の単位 2 の前提)。
  (val timing (ClusterTiming))
  (val now 15000)
  (<- scenarios tuple (settled-scenarios))
  (for [#(name state) scenarios]
    (<- settled ClusterState (tick state now timing))
    (<- again ClusterState (tick settled now timing))
    (assert (= again settled) #(name "もう 1 歩で状態が変わった — 筋書きが落ち着いていない"))
    (<- due (| DueAt DueNow DueNever) (tick-due settled now timing))
    (assert (!= due (DueNow)) #(name due))))
