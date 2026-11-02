;;; task の置き先を、worker が heartbeat で名乗る機体の測った空き(measured)から数える判断の検(coordinator/core/measured_room と
;;; cluster_policy の task-room-of・place-tasks)。手書きの task の枠(枠の file の taskReserve・backgroundRoom)を外した後の置き方:
;;;   (1) 空き = CPU の空き・上限の残りの小さい方から、測りにまだ出ていない task を引いた数(memory の空き — 置いた task の要りの予約を
;;;       引いた MiB — が 0 以下なら 0。要りの大きさは test_task_memory_need が検める)。PSI の待ちが出ている機体と
;;;       測りの無い worker は 0
;;;   (2) 置き先は測った空きの多い worker(担う数の少ない worker ではない)。1 拍で測った空きを越えて置かない。測りの無い worker には
;;;       置かず、待ちの文で名指す
;;;   (3) 予約は上限の 1/6(CPU 36 の機体で 6 — 手で書いていた zeus の値)で、半分ずつ優先 0 以上の task と背景の仕事に残す
;;;   (4) GET /state の taskRoom は置き方と同じ数えで、worker の行に手書きの枠の欄は無い
;;; 失敗ケース = 混んだ機体へ手の数どおりに置く・置いたばかりの task を数えずに 1 拍で詰め込む・測りの無い worker に置く・
;;; 空きの少ない機体を選ぶ・背景の仕事が大きい機体の最後の空きまで埋める。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import doeff_cluster [JobSpec])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [ClusterJob WorkerInfo ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [Placement TaskRecord ComponentVersion TaskUnplacedKind])
(import dataclasses [replace])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad StatusRow])
(import doeff_cluster._coordinator.intent.cluster_model [WorkerReport ClusterObservations])
(import doeff_cluster._coordinator.core.measured_room [measured-room memory-room-mib reserve-of task-cap-of unseen-since TASK-RAMP-MS])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text state-view load-of])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])
(import tests.program_rows [SAMPLE-TASK-PROGRAM reports-observed])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))


(defk load [cpus busy available * [total 65536] [at-ms 0] [cpu-psi 0.0] [memory-psi 0.0]]
  {:pre [(: cpus int) (: busy (| float None)) (: available int) (: total int) (: at-ms int) (: cpu-psi float) (: memory-psi float)]
   :post [(: % MeasuredLoad)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "CPU cpus 個・CPU の使い busy・memory の空き available MiB の機体の測った空き(heartbeat の measured の形)を作るため。"
  (MeasuredLoad :at-ms at-ms :cpus cpus :cpu-busy busy :memory-total-MiB total :memory-available-MiB available
                :cpu-some-avg10 cpu-psi :memory-some-avg10 memory-psi))


(defk worker [name measured]
  {:pre [(: name str) (: measured (| MeasuredLoad None))] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-any の worker name(最後の連絡 0・task と同じ版 python 3)が measured を名乗った形を作るため。"
  (WorkerInfo name #("host-any") 0 :versions PY3 :measured measured))


(defk queued [id * [priority 0]]
  {:pre [(: id str) (: priority int)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-any を要る待っている切り離した task(優先 priority)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-any") 15000 20000 0 :detached True :key id :priority priority))


(defk queued-all [ids * [priority 0]]
  {:pre [(: ids list) (: priority int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "待っている task の表(id → 行 — 優先 priority)を ids から作るため。"
  (var rows {})
  (for [id ids]
    (<- row TaskRecord (queued id :priority priority))
    (:= rows (| rows {id row})))
  rows)


(defk state-of [workers tasks * [jobs #()] [placements {}]]
  {:pre [(: workers tuple) (: tasks dict) (: jobs tuple) (: placements dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker の列 workers と task の表 tasks(id → 行)と常駐の job の宣言 jobs・置き先 placements(job → worker)の状態を作るため。"
  (<- table (table-from-rows (dfor w workers w.name w)))
  (<- placed (table-from-rows (dfor #(j w) (.items placements) j (Placement j w 1 0))))
  (<- rows (table-from-rows tasks))
  (ClusterState jobs table placed rows))


(deftest test-the-room-is-the-smallest-of-the-measured-cpu-memory-slots-and-cap
  ;; CPU 16 個・使い 0.25 → 空いた CPU 12 − 残す 1 = 11・memory の空き 7168 − 1536 − 予約 0 = 5632 MiB → 枠 5・上限 16 − 置いた 3 = 13。
  ;; 測りにまだ出ていない 1 本は CPU の空きからだけ引く(memory はその task の要りを予約が引いている)→ 5(memory が決める)。
  (<- by-memory-slots int (measured-room (! (load 16 0.25 7168)) 3 1 0))
  (<- left int (memory-room-mib (! (load 16 0.25 7168)) 0))
  (assert (= #(by-memory-slots left) #(5 5632)))
  ;; memory が広ければ CPU が決める: 11 − 1 = 10。
  (<- by-cpu int (measured-room (! (load 16 0.25 60000)) 3 1 0))
  (assert (= by-cpu 10))
  ;; 置いた task の予約(まだ伸びる分)が memory の空きを食い尽くした機体は、CPU が空いていても 0。
  (<- by-memory int (measured-room (! (load 16 0.25 7168)) 3 1 5632))
  (assert (= by-memory 0))
  ;; 空いた機体でも CPU の数より多くは置かない: CPU 4 個・使い 0 → CPU の空き 3 だが、上限 4 − 置いた 3 = 1。
  (<- by-cap int (measured-room (! (load 4 0.0 60000)) 3 0 0))
  (assert (= by-cap 1))
  ;; 失敗ケース: 混んだ機体(CPU 36 個・使い 0.98 — 空いた CPU 0.72)は、手で書いた数(zeus の 6 + 4)が残っていても 0。
  (<- crowded int (measured-room (! (load 36 0.98 50000)) 6 0 0))
  (assert (= crowded 0)))


(deftest test-pressure-and-missing-readings-give-no-room
  ;; PSI の待ちが出ている機体(CPU の some avg10 40% 以上・memory の some avg10 10% 以上)は、CPU と memory が空いて見えても 0。
  (<- cpu-waits int (measured-room (! (load 8 0.1 60000 :cpu-psi 55.0)) 0 0 0))
  (<- memory-waits int (measured-room (! (load 8 0.1 60000 :memory-psi 12.5)) 0 0 0))
  (assert (= #(cpu-waits memory-waits) #(0 0)))
  ;; 閾値の手前は待ちと読まない(PSI の無い機体 None も同じ)。
  (<- calm int (measured-room (! (load 8 0.1 60000 :cpu-psi 39.9 :memory-psi 9.9)) 0 0 0))
  (<- no-psi int (measured-room (MeasuredLoad :at-ms 0 :cpus 8 :cpu-busy 0.1 :memory-total-MiB 65536 :memory-available-MiB 60000) 0 0 0))
  (assert (= #(calm no-psi) #(6 6)))
  ;; 測りを名乗らない worker と、CPU の使いをまだ数えられない初めの読みは 0(空いていると決めつけない)。
  (<- none int (measured-room None 0 0 0))
  (<- first-reading int (measured-room (! (load 8 None 60000)) 0 0 0))
  (assert (= #(none first-reading) #(0 0))))


(deftest test-the-reserve-is-a-sixth-of-the-cap-and-unseen-tasks-count-from-the-reading
  ;; 予約 = 上限(CPU の数)の 1/6: CPU 36 の機体は 6(手で書いていた zeus の taskReserve と同じ)・16 は 2・4 は 0・測り無しは 0。
  (<- big int (reserve-of (! (load 36 0.5 1000))))
  (<- middle int (reserve-of (! (load 16 0.5 1000))))
  (<- small int (reserve-of (! (load 4 0.5 1000))))
  (<- blind int (reserve-of None))
  (assert (= #(big middle small blind) #(6 2 0 0)))
  (<- cap int (task-cap-of 36))
  (assert (= cap 36))
  ;; 測りにまだ出ていないと数える起点 = 読んだ刻 − TASK-RAMP-MS(測り無しは数えない)。
  (<- since (| int None) (unseen-since (! (load 4 0.5 1000 :at-ms 100000))))
  (<- none (| int None) (unseen-since None))
  (assert (= #(since none) #((- 100000 TASK-RAMP-MS) None))))


(deftest test-a-task-goes-to-the-worker-with-the-most-measured-room
  ;; 混んだ機体 a-crowded(CPU 36 個・使い 0.9 → 空き 2・担う物 0)と空いた機体 b-idle(CPU 8 個・使い 0 → 空き 7・常駐の job 2 本)。
  ;; 失敗ケース(直す前の順 = 担っている数の少ない順)なら a-crowded に置く。測った空きの多い b-idle に置く。
  (val jobs (tuple (gfor n #("s1" "s2") (ClusterJob (JobSpec n "m" #() "rev") :peak-mib 200 :settle-mib 100))))
  (<- crowded WorkerInfo (worker "a-crowded" (! (load 36 0.9 60000))))
  (<- idle WorkerInfo (worker "b-idle" (! (load 8 0.0 60000))))
  (<- state ClusterState (state-of #(crowded idle) {"t1" (! (queued "t1"))} :jobs jobs :placements {"s1" "b-idle" "s2" "b-idle"}))
  (val placed (! (tasks-placed 1000 state state.placements T)))
  (assert (= #((. (.row placed "t1") phase) (. (.row placed "t1") worker)) #("assigned" "b-idle")) (.row placed "t1")))


(deftest test-one-tick-places-no-more-than-the-measured-room
  ;; CPU 4 個・使い 0 → CPU の空き 3・memory の空き 3600 − 1536 = 2064 MiB(枠 2)。要りを名乗らない task(見込み 1024 MiB)5 本のうち
  ;; 1 拍で置くのは 2 本(置くたびに要りの全部を予約する — 測りにまだ出ていない)。残る 3 本は空きの無い固定の文で待つ。
  ;; 失敗ケース: 置いたばかりの task を数えなければ、測りが変わらない同じ拍で 5 本とも置く。
  (<- small WorkerInfo (worker "small" (! (load 4 0.0 3600 :at-ms 1000))))
  (val ids ["t1" "t2" "t3" "t4" "t5"])
  (<- state ClusterState (state-of #(small) (! (queued-all ids))))
  (val placed (! (tasks-placed 1000 state state.placements T)))
  (val phases (lfor id ids (. (.row placed id) phase)))
  (assert (= (.count phases "assigned") 2) phases)
  (<- no-room str (task-unplaced-text TaskUnplacedKind.TASK-NO-ROOM))
  (assert (all (gfor id ids :setv row (.row placed id) :if (= row.phase "queued") (= row.detail no-room))) placed)
  ;; 次の測りで機体が空いて見えても、置いた 2 本はまだ動いていると報告されていない(測りに出ていない)ので空きから引く。
  (<- later WorkerInfo (worker "small" (! (load 4 0.0 3600 :at-ms 9000))))
  (<- again ClusterState (state-of #(later) (dfor id ids id (.row placed id))))
  (val replaced (! (tasks-placed 9000 again again.placements T)))
  (assert (= (.count (lfor id ids (. (.row replaced id) phase)) "assigned") 2) replaced))


(deftest test-a-placed-task-counts-as-unseen-until-the-measuring-heartbeat-reports-it-running-for-a-while
  ;; 測りを載せた heartbeat(刻 20000)の状態の報告: t1 と t3 が動いている・t2 は無い。t1 は読みの 10 秒より前に置いた → 測りに出ている。
  ;; t2 は動いていると報告されていない・t3 は置いて 5 秒(走り出したばかり)→ どちらもまだ出ていない(unseen 2)。失敗ケース: 置いた刻
  ;; だけで数えると t2 を見落とし、報告だけで数えると t3 を見落とす。
  (<- w WorkerInfo (worker "w" (! (load 8 0.5 60000 :at-ms 20000))))
  (val rows {"t1" (replace (! (queued "t1")) :phase "assigned" :worker "w" :started-ms 1000)
             "t2" (replace (! (queued "t2")) :phase "assigned" :worker "w" :started-ms 1000)
             "t3" (replace (! (queued "t3")) :phase "assigned" :worker "w" :started-ms 15000)})
  (<- base ClusterState (state-of #(w) rows))
  (<- seen ClusterObservations (reports-observed {"w" (WorkerReport :at 20000 :endpoint None
                                                                   :jobs #((StatusRow :name "task/t1" :phase "running")
                                                                           (StatusRow :name "task/t3" :phase "running")))}))
  (val counted (load-of (replace base :observations seen) base.placements))
  (assert (= #((. (get counted "w") tasks) (. (get counted "w") unseen)) #(3 2)) (get counted "w")))


(deftest test-an-unmeasured-worker-gets-no-task-and-the-wait-names-it
  ;; 測った空きを名乗らない worker(/proc の無い機体・測りの前の版)だけなら置かずに待ち、待ちの文が測りの無さを名指す。
  (<- blind WorkerInfo (worker "blind" None))
  (<- state ClusterState (state-of #(blind) {"t1" (! (queued "t1"))}))
  (val placed (! (tasks-placed 1000 state state.placements T)))
  (<- unmeasured str (task-unplaced-text TaskUnplacedKind.TASK-UNMEASURED))
  (assert (= #((. (.row placed "t1") phase) (. (.row placed "t1") detail)) #("queued" unmeasured)) (.row placed "t1"))
  ;; 測りを名乗る worker が混んでいる時は、空きの無さを名乗る(測りの無い worker が並んでいても)。
  (<- busy WorkerInfo (worker "busy" (! (load 8 1.0 60000))))
  (<- both ClusterState (state-of #(blind busy) {"t1" (! (queued "t1"))}))
  (val waited (! (tasks-placed 1000 both both.placements T)))
  (<- no-room str (task-unplaced-text TaskUnplacedKind.TASK-NO-ROOM))
  (assert (= (. (.row waited "t1") detail) no-room) (.row waited "t1")))


(deftest test-background-tasks-leave-the-reserve-of-a-big-machine-to-the-build
  ;; CPU 36 個・使い 0.8 → 空き 6・予約 6(優先 0 以上に 3・背景の仕事に 3)。背景の仕事(優先 -1)10 本は空きが予約の半分 3 に
  ;; 下がるまでの 3 本だけ置かれ、残りは予約の文で待つ。失敗ケース: 予約を見なければ 6 本置かれ、後の組み立ての task が空くまで待つ。
  (<- zeus WorkerInfo (worker "zeus" (! (load 36 0.8 60000 :at-ms 1000))))
  (val ids (lfor i (range 10) (.format "bg{}" i)))
  (<- state ClusterState (state-of #(zeus) (! (queued-all ids :priority -1))))
  (val placed (! (tasks-placed 1000 state state.placements T)))
  (val phases (lfor id ids (. (.row placed id) phase)))
  (assert (= (.count phases "assigned") 3) phases)
  (<- reserved str (task-unplaced-text TaskUnplacedKind.TASK-RESERVED))
  (assert (all (gfor id ids :setv row (.row placed id) :if (= row.phase "queued") (= row.detail reserved))) placed)
  ;; 後の拍で積んだ組み立ての task(優先 0)は、背景の仕事が待っていても残した予約へ置かれる。
  (<- later ClusterState (state-of #(zeus) (| (dfor id ids id (.row placed id)) {"build" (! (queued "build"))})))
  (val built (! (tasks-placed 2000 later later.placements T)))
  (assert (= (. (.row built "build") phase) "assigned") (.row built "build")))


(deftest test-the-state-shows-the-placement-room-and-no-task-slots
  ;; GET /state の worker の taskRoom は置き方と同じ数え(CPU 16 個・使い 0.25・memory 広い → 11)で、測りの無い worker は 0。
  ;; 手書きの task の枠の欄(taskReserve・backgroundRoom・capacity)は worker の行に無い。
  (<- roomy WorkerInfo (worker "roomy" (! (load 16 0.25 60000))))
  (<- blind WorkerInfo (worker "blind" None))
  (<- state ClusterState (state-of #(roomy blind) {}))
  (val workers (get (! (state-view-json (! (state-view state 20 T)))) "workers"))
  (assert (= #((get workers "roomy" "taskRoom") (get workers "blind" "taskRoom")) #(11 0)) workers)
  (assert (not (& #{"taskReserve" "backgroundRoom" "capacity"} (set (get workers "roomy")))) (get workers "roomy")))
