;;; 待ちの期限(SubmitDetached の queue-limit-seconds の註・cluster_policy.settle-detached・2026-11-12)の検: 送り手が待ちの期限を名乗った
;;; 切り離した task は、queued のまま期限を越えると phase cancelled で終わり、detail に期限・待った長さ・最後の置かれない理由が載り、
;;; 知らせ先へ出口の終わりの行が 1 つ積まれる。
;;;   (1) 期限の刻(task-lapse-at)は積んだ刻 + 期限の次の ms。期限なし・置いた task・終わった task は待ちの期限を持たない
;;;   (2) 測った空きの無い worker の前で待つ task: 期限の前は queued のまま、期限を越えた拍に cancelled(理由と待った長さつき)
;;;   (3) 失敗ケース: 期限を名乗らない task は同じ長さ待っても queued のまま(直す前の形 — 何十時間も黙って待つ)
;;;   (4) 置いた task は期限を越えても止めない
;;;   (5) 期限で終わった歩は出口へ終わりの行を 1 つ積み、detail を写す
;;;   (6) 受け口: 1 以上の整数でない queueLimitSeconds は 400・受けた本文の行は ms で持つ。POST /tasks は受けない
;;;   (7) 構築関数も同じ規則で送る前に断り、本文の欄は名乗る時だけ置く
;;;   (8) 保存の綴りの往復で期限が残り、欄の無い旧い行は期限なしで読む
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import pytest)
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo submit-detached-task]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad TaskBody])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-lapse-at submit-task])
(import doeff_cluster._coordinator.core.detached_policy [submit-detached outbox-noted])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB CPU-PRESSURE-STOP])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._shared.core.detached_rules [task-queue-limit-refusal task-queue-limit-json])
(import doeff_hy._table [table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed task-body-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
;; 期限(2 時間)と、積んだ刻(0)から期限の前・後の刻。
(val LIMIT-MS (* 2 3600 1000))
(val BEFORE (- LIMIT-MS 1000))
(val AFTER (+ LIMIT-MS (* 5 60 1000)))
(val NEED-MIB 1024)


(defk noop []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "構築関数へ渡す Program の代わり(走らせない — 構築関数が送る前に断るかだけを見る)。"
  1)


(defk crowded-worker [now]
  {:pre [(: now int)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "刻 now に生きていて、測った空きが 0 の node-a を作るため(門の内の task はここで queued のまま待つ)。"
  (WorkerInfo "node-a" #("host-node-a") now :versions PY3
              :measured (MeasuredLoad :at-ms now :cpus 8 :cpu-busy 1.0 :memory-total-MiB 16384
                                      :memory-available-MiB (- MEMORY-FLOOR-MIB 512)
                                      :cpu-some-avg10 (+ CPU-PRESSURE-STOP 50.0) :memory-some-avg10 60.0)))


(defk waiting [id * [limit-ms LIMIT-MS] [phase "queued"]]
  {:pre [(: id str) (: limit-ms (| int None)) (: phase str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "刻 0 に積んだ、能力 host-node-a を要る切り離した task(待ちの期限 limit-ms・知らせ先つき)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-a") 15000 15000 0 :detached True :key id
              :memory-mib NEED-MIB :notify "n:c-a" :phase phase :queue-limit-ms limit-ms
              :worker (if (= phase "queued") None "node-a")))


(defk placed-at [now rows]
  {:pre [(: now int) (: rows dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "測った空きが 0 の node-a 1 台の状態で、task rows(id → 行)を刻 now の置き先に 1 拍回し、id → 行を得るため。"
  (<- workers (table-from-rows {"node-a" (! (crowded-worker now))}))
  (<- tasks (table-from-rows rows))
  (val state (ClusterState #() workers (table-of #()) tasks))
  (val placed (! (tasks-placed now state state.placements T)))
  (dfor id rows id (.row placed id)))


(deftest test-the-queue-limit-is-the-due-instant-of-a-waiting-task
  (<- limited TaskRecord (waiting "a"))
  (assert (= (! (task-lapse-at limited 0)) (+ LIMIT-MS 1)))
  ;; 期限なしの待ちは自分の期限を持たない(今までどおり)。
  (<- open TaskRecord (waiting "b" :limit-ms None))
  (assert (is (! (task-lapse-at open 0)) None))
  ;; 置いた task の期限は lease の期限のまま(待ちの期限を読まない)。
  (<- running TaskRecord (waiting "c" :phase "assigned"))
  (assert (= (! (task-lapse-at running 0)) (+ running.lease-until-ms 1))))


(deftest test-a-task-past-its-queue-limit-is-cancelled-with-the-reason
  (<- task TaskRecord (waiting "a"))
  ;; 期限の前: 測った空きの無さで queued のまま待ち、置かれない理由が detail に在る。
  (<- before dict (placed-at BEFORE {"a" task}))
  (val waited (get before "a"))
  (assert (= waited.phase "queued") waited)
  (assert waited.detail waited)
  ;; 期限の後の拍: cancelled で終わり、期限・待った長さ・最後の置かれない理由を書く。
  (<- after dict (placed-at AFTER {"a" waited}))
  (val ended (get after "a"))
  (assert (= ended.phase "cancelled") ended)
  (assert (= ended.finished-ms AFTER) ended)
  (assert (in "待ちの期限 2 時間 0 分" ended.detail) ended.detail)
  (assert (in "待った長さ 2 時間 5 分" ended.detail) ended.detail)
  (assert (in waited.detail ended.detail) #(waited.detail ended.detail)))


(deftest test-without-a-queue-limit-the-task-keeps-waiting
  ;; 失敗ケース(直す前の形): 期限を名乗らない task は、期限の何倍待っても queued のまま。
  (<- task TaskRecord (waiting "a" :limit-ms None))
  (<- rows dict (placed-at (* 30 LIMIT-MS) {"a" task}))
  (assert (= (. (get rows "a") phase) "queued") (get rows "a")))


(deftest test-a-placed-task-is-not-stopped-by-its-queue-limit
  (val task (replace (! (waiting "a" :phase "assigned")) :lease-until-ms (* 10 LIMIT-MS)))
  (<- rows dict (placed-at AFTER {"a" task}))
  (assert (= (. (get rows "a") phase) "assigned") (get rows "a")))


(deftest test-the-expiry-puts-one-ended-row-on-the-outbox
  (<- task TaskRecord (waiting "a"))
  (<- workers (table-from-rows {"node-a" (! (crowded-worker AFTER))}))
  (<- tasks (table-from-rows {"a" task}))
  (val before (ClusterState #() workers (table-of #()) tasks))
  (val after (replace before :tasks (! (tasks-placed AFTER before before.placements T))))
  (val noted (outbox-noted before after AFTER))
  (assert (= (.keys noted.outbox) #("a")) (.keys noted.outbox))
  (val row (.row noted.outbox "a"))
  (assert (= row.phase "cancelled") row)
  (assert (in "待ちの期限" row.detail) row.detail))


(defk submitted [body]
  {:pre [(: body dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "詰めた Program を置き場に置いた状態へ、本文 body を受け口と同じ解きで PUT /detached/k として送った #(status 答え 状態) を得るため。"
  (<- placed tuple (program-placed (ClusterState) {"python" "3"}))
  (setv #(placed-state sha) placed)
  (<- workers (table-from-rows {"node-a" (! (crowded-worker 0))}))
  (val state (replace placed-state :workers workers))
  (<- parsed TaskBody (task-body-of (| {"program" sha "revision" "rev" "name" "build" "needs" ["host-node-a"] "memoryMiB" NEED-MIB} body)))
  (val reply (submit-detached state "k" parsed 0))
  #(reply.status reply.body reply.state))


(deftest test-the-door-takes-a-whole-number-of-seconds
  (for [bad [0 -5 1.5 True "3600" (+ (* 30 24 3600) 1)]]
    (setv #(status answer state) (! (submitted {"queueLimitSeconds" bad})))
    (assert (= status 400) #(bad status answer))
    (assert (in "queueLimitSeconds" answer.message) #(bad answer.message))
    (assert (= (len (list (.rows state.tasks))) 0) state.tasks))
  (setv #(status answer state) (! (submitted {"queueLimitSeconds" 7200})))
  (assert (= status 200) #(status answer))
  (assert (= (. (get (list (.rows state.tasks)) 0) queue-limit-ms) LIMIT-MS) state.tasks)
  ;; 名乗らない本文は期限なし。
  (setv #(status answer state) (! (submitted {})))
  (assert (= status 200) #(status answer))
  (assert (is (. (get (list (.rows state.tasks)) 0) queue-limit-ms) None) state.tasks))


(deftest test-post-tasks-does-not-take-a-queue-limit
  (<- placed tuple (program-placed (ClusterState) {"python" "3"}))
  (setv #(placed-state sha) placed)
  (<- parsed TaskBody (task-body-of {"program" sha "revision" "rev" "name" "build" "needs" ["host-node-a"] "queueLimitSeconds" 60}))
  (setv #(_ status answer) (cut (! (submit-task placed-state parsed 0)) 0 3))
  (assert (= status 400) #(status answer))
  (assert (in "queueLimitSeconds" answer.message) answer.message))


(deftest test-the-builder-refuses-the-same-limits
  (assert (is (task-queue-limit-refusal None) None))
  (assert (is (task-queue-limit-refusal 86400) None))
  (assert (is-not (task-queue-limit-refusal 0) None))
  (with [raised (pytest.raises TypeError)]
    (<- (submit-detached-task (noop) "k" :needs (frozenset #("host-node-a")) :memory-mib NEED-MIB :queue-limit-seconds 0)))
  (assert (in "queueLimitSeconds" (str raised.value)) raised.value)
  ;; 送り手の本文の欄: 名乗る時だけ置く(旧い coordinator は知らない欄を捨てる)。
  (assert (= #((task-queue-limit-json 86400) (task-queue-limit-json None)) #({"queueLimitSeconds" 86400} {}))))


(deftest test-the-stored-row-keeps-the-queue-limit
  (<- task TaskRecord (waiting "a"))
  (<- back TaskRecord (task-record-from-json (task-record-to-json task)))
  (assert (= back.queue-limit-ms LIMIT-MS) back)
  (<- open TaskRecord (waiting "b" :limit-ms None))
  (val spelled (task-record-to-json open))
  (assert (not-in "queue_limit_ms" spelled) spelled)
  (<- old TaskRecord (task-record-from-json spelled))
  (assert (is old.queue-limit-ms None) old))
