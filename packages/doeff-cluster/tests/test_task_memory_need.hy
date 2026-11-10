;;; task の memory の要り(SubmitDetached の memory-mib — 1 本の山の MiB)を、置き先が測った空きから「要り − 今の使い」で予約して数える
;;; 事の検(2026-11-02)。実例: 日次の doeff の回は置いてから 15〜20 分かけて 0.9 → 5.2 GiB 超まで伸びる。置き先は置いた時の空きを
;;; 1 本 1 GiB の見込みで割り、10 秒だけ測りに出ていない task を引いていたので、伸びる前の空きを見て軽い task を同じ worker に詰め、
;;; 重い回が伸びた時に見張りに止められた(11-02 12:05 の t27866 — 5232 MiB で止められた)。
;;;   (1) 反例の筋書き: 重い 1 本が伸びる前に軽い task を詰める世界を、heartbeat ごとに機体の真実(task ごとの今の使い)から測りを作って
;;;       置き先を回し、重い 1 本が伸びきった後も機体の空きが床を割らない
;;;   (2) 要りの形の誤りは構築関数と受け口が断り、needs の合うどの worker の線よりも大きい要りは積む時に断り、今だけ足りない要りは
;;;       queued で待って待ちの文が memory の空きを名指す
;;;   (3) 要りは保存の形と GET /state に出て、worker の taskMemoryRoomMiB は置き先と同じ数え
;;;   (4) worker は見張りの新しい読みの時だけ task ごとの今の使いを名乗り、置き先は名乗った使いを予約から引く
(require doeff-hy.macros [deftest val var <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import pytest)
(import doeff_cluster [ClusterTiming submit-detached-task])
(import doeff_cluster [WorkerInfo ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind WorkerReport])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad StatusRow TaskBody])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB memory-room-mib])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text state-view load-of])
(import doeff_cluster._coordinator.core.detached_policy [submit-detached])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])
(import doeff_cluster._shared.core.detached_rules [task-memory-refusal task-memory-required-refusal task-placing-json])
(import doeff_cluster._worker.core.memory_rules [JobUse])
(import doeff_cluster._worker.core.load_rules [MachineLoad TaskMemory task-uses-of TASK-USES-FRESH-MS])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import doeff_hy._table [table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed task-body-of reports-observed])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
;; task の lease は筋書きの長さより長く(heartbeat の延ばしを模さない — 置いた task が筋書きの途中で lost にならない)。
(val LONG-LEASE-MS (* 3600 1000))
(val PY3 #((ComponentVersion "python" "3")))
;; node-a の形の機体: 追い出しの線の大きさ 16 GiB・常駐の job と他の Pod が使う 4000 MiB・CPU 32 個(CPU では縛らない)。
(val LINE-MIB 16384)
(val BASE-MIB 4000)
;; 重い回: 要り 6000 MiB・置いてから 600 秒は 900 MiB、その後 6000 MiB へ伸びる(日次の doeff の回の 930 → 5232 MiB の形)。
(val HEAVY-NEED 6000)
(val HEAVY-EARLY 900)
(val GROW-AFTER-MS 600000)
;; 軽い task: 要り 700 MiB・使い 600 MiB(会話の test の 1 本の形)。
(val LIGHT-NEED 700)
(val LIGHT-USE 600)


(defk noop []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "構築関数へ渡す Program の代わり(走らせない — 構築関数が送る前に断るかだけを見る)。"
  1)


(defk queued [id need]
  {:pre [(: id str) (: need (| int None))] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-a を要る待っている切り離した task(memory の要り need — None = 名乗らない)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-a") LONG-LEASE-MS LONG-LEASE-MS 0 :detached True :key id :memory-mib need))


(defk truth-use [task now]
  {:pre [(: task TaskRecord) (: now int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "機体の真実: 置いた task の今の memory の使い(MiB)を知るため。重い回は置いてから GROW-AFTER-MS で要りまで伸び、軽い task は一定。"
  (cond
    (!= task.memory-mib HEAVY-NEED) LIGHT-USE
    (< (- now task.started-ms) GROW-AFTER-MS) HEAVY-EARLY
    True HEAVY-NEED))


(defk node-a-at [rows now]
  {:pre [(: rows dict) (: now int)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "刻 now の heartbeat で node-a が名乗る測り(機体の真実から — 空き = 線 − 常駐 − 置いた task の今の使い・task ごとの今の使い)を作るため。"
  (var uses {})
  (for [t (.values rows) :if (= t.phase "assigned")]
    (<- use int (truth-use t now))
    (:= uses (| uses {t.id use})))
  (WorkerInfo "node-a" #("host-node-a") now :versions PY3
              :measured (MeasuredLoad :at-ms now :cpus 32 :cpu-busy 0.1 :memory-total-MiB LINE-MIB
                                      :memory-available-MiB (max 0 (- LINE-MIB BASE-MIB (sum (.values uses))))
                                      :cpu-some-avg10 0.0 :memory-some-avg10 0.0 :task-memory-MiB uses)))


(defk world-at [rows now]
  {:pre [(: rows dict) (: now int)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "刻 now の coordinator の状態(node-a の測りと、置いた task が動いていると報告した同じ heartbeat の状態の報告)を作るため。"
  (<- node-a WorkerInfo (node-a-at rows now))
  (<- workers (table-from-rows {"node-a" node-a}))
  (<- tasks (table-from-rows rows))
  (<- seen (reports-observed {"node-a" (WorkerReport :at now :endpoint None
                                                    :jobs (tuple (gfor t (.values rows) :if (= t.phase "assigned")
                                                                       (StatusRow :name (+ "task/" t.id) :phase "running"))))}))
  (replace (ClusterState #() workers (table-of #()) tasks) :observations seen))


(deftest test-light-tasks-do-not-take-the-room-a-heavy-task-grows-into
  ;; 反例の筋書き(赤 → 緑): 刻 0 に重い回(要り 6000)を置き、刻 60 秒に軽い task 12 本(要り 700)を積む。heartbeat ごと(60 秒)に機体の
  ;; 真実から測りを作って置き先を回し、重い回が 600 秒で伸びきるまで続ける。直す前の数え(空き ⌊(空き − 床) / 1024⌋ − 10 秒だけの
  ;; 測りに出ていない task)は、伸びる前の空き 11484 MiB を見て 9 本、次の拍で残りの 3 本も置き、伸びた後の空きは
  ;; 16384 − 4000 − 6000 − 12 × 600 = −816 MiB(kubelet の追い出し・見張りの止め)。予約で数えると、重い回のまだ伸びる 5100 MiB を
  ;; 引いた空きにだけ軽い task を置き、伸びきった後も機体の空きは床(MEMORY-FLOOR-MIB)を割らない。
  (var rows {"heavy" (! (queued "heavy" HEAVY-NEED))})
  (var lowest LINE-MIB)
  (for [now (range 0 (+ GROW-AFTER-MS 120001) 60000)]
    (when (= now 60000)
      (for [i (range 12)]
        (<- light TaskRecord (queued (.format "light{:02d}" i) LIGHT-NEED))
        (:= rows (| rows {light.id light}))))
    (<- state ClusterState (world-at rows now))
    (val placed (! (tasks-placed now state state.placements T)))
    (:= rows (dict (.items placed)))
    (<- after WorkerInfo (node-a-at rows now))
    (:= lowest (min lowest after.measured.memory-available-MiB)))
  (val running (lfor t (.values rows) :if (= t.phase "assigned") t.id))
  (assert (in "heavy" running) running)
  ;; 重い回が伸びきった後も空きは床を割らない(直す前は −816)。
  (assert (>= lowest MEMORY-FLOOR-MIB) #(lowest running))
  ;; 置けなかった軽い task は queued のまま待ち、待ちの文は空きの無さを名指す(失敗にしない)。
  (val waiting (lfor t (.values rows) :if (= t.phase "queued") t))
  (assert waiting running)
  (<- no-room str (task-unplaced-text TaskUnplacedKind.TASK-NO-ROOM))
  (<- no-memory str (task-unplaced-text TaskUnplacedKind.TASK-NO-MEMORY))
  (assert (all (gfor t waiting (in t.detail #(no-room no-memory)))) waiting))


(defk submitted [key body]
  {:pre [(: key str) (: body dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "詰めた Program を置き場に置き、刻 0 の node-a(何も置いていない)の測りを持つ状態へ、本文 body を受け口と同じ解きで PUT /detached/<key>
   として送った #(status 答え 状態) を得るため。"
  (<- placed tuple (program-placed (ClusterState) {"python" "3"}))
  (setv #(placed-state sha) placed)
  (<- node-a WorkerInfo (node-a-at {} 0))
  (<- workers (table-from-rows {"node-a" node-a}))
  (val state (replace placed-state :workers workers))
  (<- parsed TaskBody (task-body-of (| {"program" sha "revision" "rev" "name" "verify" "needs" ["host-node-a"]} body)))
  (val reply (submit-detached state key parsed 0))
  #(reply.status reply.body reply.state))


(deftest test-a-malformed-memory-need-is-refused-by-the-builder-and-the-door
  ;; 要りの形の誤り(0・負・真偽値・文字列の量・上限を越える数)は、構築関数が送る前に TypeError で、受け口が 400 で断る。
  (for [bad [0 -5 True "6Gi" (* 4096 1024 1024)]]
    (assert (is-not (task-memory-refusal bad) None) bad))
  (assert (is (task-memory-refusal 6144) None))
  (with [raised (pytest.raises TypeError)]
    (<- (submit-detached-task (noop) "k" :needs (frozenset #("host-node-a")) :memory-mib 0)))
  (assert (in "memoryMiB" (str raised.value)) raised.value)
  (setv #(status answer _) (! (submitted "k" {"memoryMiB" "6Gi"})))
  (assert (= status 400) #(status answer))
  (assert (in "memoryMiB" answer.message) answer.message)
  ;; 送り手の本文の欄: 名乗る時だけ memoryMiB を置く(旧い coordinator は知らない欄を捨てる)。
  (assert (= #((task-placing-json 0 #() 6144) (task-placing-json 0 #())) #({"memoryMiB" 6144} {}))))


(deftest test-a-send-without-a-memory-need-is-refused-by-the-builder-and-the-door
  ;; 切り離した task の要りは必須(2026-11-02): 要りを渡さない呼びも None も構築関数が送る前に TypeError で、本文に memoryMiB の無い
  ;; PUT /detached を受け口が 400 で断り、行を積まない(名乗らない task を一律の見込みで数える道を閉じる)。
  (assert (is-not (task-memory-required-refusal None) None))
  (assert (is (task-memory-required-refusal 6144) None))
  (assert (is-not (task-memory-required-refusal 0) None))
  (with [raised (pytest.raises TypeError)]
    (<- (submit-detached-task (noop) "k" :needs (frozenset #("host-node-a")))))
  (assert (in "memory_mib" (str raised.value)) raised.value)
  (with [raised (pytest.raises TypeError)]
    (<- (submit-detached-task (noop) "k" :needs (frozenset #("host-node-a")) :memory-mib None)))
  (assert (in "memoryMiB" (str raised.value)) raised.value)
  (setv #(status answer state) (! (submitted "k" {})))
  (assert (= status 400) #(status answer))
  (assert (in "memoryMiB" answer.message) answer.message)
  (assert (= (len (list (.rows state.tasks))) 0) state.tasks)
  ;; 欄の前に保存した行(memory_mib の無い行)の読みは、今も名乗らない行として受ける(置き先は見込みの要りで数える)。
  (assert (is (task-memory-refusal None) None)))


(deftest test-a-need-larger-than-every-line-is-refused-and-a-short-room-waits
  ;; node-a の線 − 床 = 16384 − 1536 = 14848 MiB。それより大きい要りは待っても置けないので、積む時に 400 で断る(queued のまま待たせない)。
  (setv #(status answer state) (! (submitted "too-big" {"memoryMiB" 20000})))
  (assert (= status 400) #(status answer))
  (assert (and (in "14848" answer.message) (in "node-a" answer.message)) answer.message)
  (assert (= (len (list (.rows state.tasks))) 0) state.tasks)
  ;; 線には入るが、今の空き(12384 − 1536 = 10848 MiB)には入らない要りは積んで queued で待ち、待ちの文が memory の空きを名指す。
  (setv #(status answer state) (! (submitted "later" {"memoryMiB" 12000})))
  (assert (= status 200) #(status answer))
  (val placed (! (tasks-placed 0 state state.placements T)))
  (val row (get (list (.rows placed)) 0))
  (<- no-memory str (task-unplaced-text TaskUnplacedKind.TASK-NO-MEMORY))
  (assert (= #(row.phase row.detail row.memory-mib) #("queued" no-memory 12000)) row))


(deftest test-the-need-is-stored-and-shown-with-the-memory-room
  ;; 要りは保存の形を往復し、欄の無い行は名乗らない。GET /state の task に memoryMiB、worker に taskMemoryRoomMiB(置き先と同じ数え —
  ;; 空き 11484 − 床 1536 − 置いた重い回のまだ伸びる 6000 − 900 = 5100 → 4848)が出る。
  (<- heavy TaskRecord (queued "heavy" HEAVY-NEED))
  (<- back TaskRecord (task-record-from-json (task-record-to-json heavy)))
  (assert (= back heavy) back)
  (<- plain TaskRecord (queued "plain" None))
  (assert (not-in "memory_mib" (task-record-to-json plain)))
  (<- state ClusterState (world-at {"heavy" (replace heavy :phase "assigned" :worker "node-a" :started-ms 0)} 60000))
  (val view (! (state-view-json (! (state-view state 60000 T)))))
  (assert (= (get (get view "tasks") 0 "memoryMiB") HEAVY-NEED) (get view "tasks"))
  (assert (= (get view "workers" "node-a" "taskMemoryRoomMiB") (- LINE-MIB BASE-MIB HEAVY-EARLY MEMORY-FLOOR-MIB (- HEAVY-NEED HEAVY-EARLY)))
          (get view "workers" "node-a")))


(deftest test-the-worker-names-task-memory-only-from-a-fresh-watch-reading
  ;; worker は見張りの最後の読みの job ごとの memory のうち task の物を、読みが新しい間だけ名乗る(古い小さな使いで予約を減らさない)。
  (val uses #((JobUse :job "task/t1" :pid 10 :bytes (* 900 1024 1024) :processes 3) (JobUse :job "svc" :pid 11 :bytes 1 :processes 1)))
  (<- fresh (| tuple None) (task-uses-of uses 1000 (+ 1000 TASK-USES-FRESH-MS)))
  (<- stale (| tuple None) (task-uses-of uses 1000 (+ 1001 TASK-USES-FRESH-MS)))
  (<- never (| tuple None) (task-uses-of uses None 1000))
  (assert (= #(fresh stale never) #(#((TaskMemory :id "t1" :mib 900)) None None)))
  (<- row dict (measured-json (MachineLoad :at-ms 0 :cpus 4 :cpu-busy 0.0 :memory-total-mib 100 :memory-available-mib 50
                                            :cpu-some-avg10 None :cpu-full-avg10 None :memory-some-avg10 None :memory-full-avg10 None
                                            :task-uses fresh)))
  (assert (= (get row "taskMemoryMiB") {"t1" 900}) row)
  ;; 置き先は名乗った今の使いを要りから引いた分だけ予約し、名乗られない task は要りの全部を予約する。
  (val rows {"seen" (replace (! (queued "seen" HEAVY-NEED)) :phase "assigned" :worker "node-a" :started-ms 0)
             "fresh" (replace (! (queued "fresh" LIGHT-NEED)) :phase "assigned" :worker "node-a" :started-ms 0)})
  (<- state ClusterState (world-at rows 1000))
  (val told (replace state :workers (table-of #())))
  (<- node-a WorkerInfo (node-a-at {"seen" (get rows "seen")} 1000))
  (<- only-seen (table-from-rows {"node-a" node-a}))
  (val counted (load-of (replace told :workers only-seen) state.placements))
  (assert (= (. (get counted "node-a") reserved-mib) (+ (- HEAVY-NEED HEAVY-EARLY) LIGHT-NEED)) (get counted "node-a"))
  (<- left int (memory-room-mib node-a.measured (. (get counted "node-a") reserved-mib)))
  (assert (= left (- LINE-MIB BASE-MIB HEAVY-EARLY MEMORY-FLOOR-MIB (- HEAVY-NEED HEAVY-EARLY) LIGHT-NEED)) left))
