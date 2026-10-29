;; 切り離した task の終わりの出口(card ki-e7945a2b2d00 — detached_model の頭の註の出口・detached_policy の出口の節)の純粋な判断の検。
;; 筋書き(3 つの組で送って終わらせて読む)は test_detached.hy の test-outbox-*。ここは 1 歩の前後の差から積む判断(ended-of・outbox-noted)・
;; 読みと受領の口(GET /outbox・DELETE /outbox/<id>)・受け口の notify の検め・保存の往復を確かめる。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff [run])
(import doeff_hy._table [TableWrite table-of])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.detached_model [DetachedEnded DetachedOutbox DetachedOutboxAcked])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord])
(import doeff_cluster.shared.core.detached_rules [task-notify-refusal])
(import doeff_cluster.coordinator.core.detached_policy [ended-of outbox-noted outbox-read outbox-ack OUTBOX-RETAIN-MS])
(import doeff_cluster.coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster.coordinator.protocol.durable_kv [durable-kv state-from-kv])
(import doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.shared.protocol.inbox [http-request])


(defk task-row [id phase [notify "n:c-a"] [detached True]]
  {:pre [(: id str) (: phase str) (: notify str) (: detached bool)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検の task の行 1 つ(切り離した task・知らせ先 notify・phase)を作るため。"
  (TaskRecord id "job" None "rev" #() #() 1000 2000 0 :phase phase :detached detached :key (+ "k-" id) :notify notify
              :finished-ms (if (in phase #("queued" "preparing" "assigned")) None 5000) :detail (+ "訳 " phase)))


(defk state-of [rows [outbox #()]]
  {:pre [(: rows tuple) (: outbox tuple)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "task の行 rows と出口の行 outbox を持つ状態を作るため。"
  (ClusterState :tasks (table-of (tuple (gfor r rows (TableWrite r.id r))))
                :outbox (table-of (tuple (gfor e outbox (TableWrite e.id e))))))


(deftest test-ended-of-names-only-the-step-that-ends-a-notified-task []
  (val now 9000)
  ;; 開いていた → 終わり: 積む(終わった刻と phase と理由を写す)。
  (assert (= (ended-of (! (task-row "t1" "assigned")) (! (task-row "t1" "cancelled")) now)
             (DetachedEnded :id "t1" :key "k-t1" :notify "n:c-a" :phase "cancelled" :detail "訳 cancelled" :failure-kind ""
                            :finished-ms 5000)))
  ;; 同じ歩で積まれて終わった(置き先が無い — 前の行が無い)も積む。
  (assert (= (. (ended-of None (! (task-row "t2" "failed")) now) phase) "failed"))
  ;; 開いたまま行が消えた(DELETE /tasks/<id>)は lost として積む(知らせずに失わない)。
  (val gone (ended-of (! (task-row "t3" "queued")) None now))
  (assert (= #(gone.phase gone.finished-ms) #("lost" now)) gone)
  ;; 積まない: 知らせ先が無い・切り離していない・前から終わっていた・まだ開いている・どちらの行も無い。
  (assert (is (ended-of (! (task-row "t4" "assigned" :notify "")) (! (task-row "t4" "finished" :notify "")) now) None))
  (assert (is (ended-of (! (task-row "t5" "assigned" :detached False)) (! (task-row "t5" "finished" :detached False)) now) None))
  (assert (is (ended-of (! (task-row "t6" "finished")) (! (task-row "t6" "finished")) now) None))
  (assert (is (ended-of (! (task-row "t7" "queued")) (! (task-row "t7" "assigned")) now) None))
  (assert (is (ended-of None None now) None)))


(deftest test-outbox-noted-adds-one-row-per-end-and-never-again []
  (val before (! (state-of #((! (task-row "t1" "assigned")) (! (task-row "t2" "assigned" :notify ""))))))
  (val after (! (state-of #((! (task-row "t1" "lost")) (! (task-row "t2" "lost" :notify ""))))))
  (val noted (outbox-noted before after 9000))
  (assert (= (.keys noted.outbox) #("t1")) (.keys noted.outbox))
  ;; 同じ移りをもう 1 度通しても積み直さない(結末の id が出口に在る)。
  (val again (outbox-noted before noted 9000))
  (assert (= (.keys again.outbox) #("t1")))
  ;; 受領で外した後、移りの無い歩は積み直さない(終わりへの移りは 1 度だけ)。
  (val acked (. (outbox-ack noted "t1") state))
  (assert (= (.keys acked.outbox) #()))
  (val later (outbox-noted acked (replace acked :next-task 9) 9500))
  (assert (= (.keys later.outbox) #()))
  ;; 変わらない歩は同じ状態を返す(保存の差分を作らない)。
  (assert (is (outbox-noted before before 9000) before)))


(deftest test-outbox-noted-drops-rows-past-the-retention []
  (val row (DetachedEnded :id "t1" :key "k" :notify "n:c-a" :phase "finished" :detail "" :failure-kind "" :finished-ms 1000))
  (val state (! (state-of #() :outbox #(row))))
  (assert (is (outbox-noted state state (+ 1000 OUTBOX-RETAIN-MS)) state))
  (assert (= (. (outbox-noted state state (+ 1001 OUTBOX-RETAIN-MS)) outbox (keys)) #())))


(deftest test-outbox-read-selects-by-prefix-and-ack-removes-once []
  (val rows (tuple (gfor #(id notify at) [#("t2" "n:c-a" 20) #("t1" "n:c-b" 10) #("t3" "m:c-a" 5)]
                         (DetachedEnded :id id :key (+ "k-" id) :notify notify :phase "finished" :detail "" :failure-kind ""
                                        :finished-ms at))))
  (val state (! (state-of #() :outbox rows)))
  (val read (outbox-read state "n:"))
  (assert (is read.state state))
  (assert (= (lfor e read.body.entries e.id) ["t1" "t2"]) read.body)
  (assert (= (. (outbox-read state "") body entries) (tuple (sorted rows :key (fn [e] #(e.finished-ms e.id))))))
  (val acked (outbox-ack state "t1"))
  (assert (= acked.body (DetachedOutboxAcked :id "t1" :acked True)))
  (val twice (outbox-ack acked.state "t1"))
  (assert (= twice.body (DetachedOutboxAcked :id "t1" :acked False)))
  (assert (is twice.state acked.state)))


(deftest test-outbox-routes-answer-json []
  (val row (DetachedEnded :id "t1" :key "k-t1" :notify "n:c-a" :phase "env-failed" :detail "d" :failure-kind "lock-mismatch" :finished-ms 7))
  (val state (! (state-of #() :outbox #(row))))
  (val timing (ClusterTiming))
  (val read (responded state (run (http-request "GET" "/outbox" {"prefix" "n:"} None)) 10 timing))
  (val status (get read 1))
  (val body (get read 2))
  (assert (= status 200) body)
  (assert (= body {"entries" [{"id" "t1" "key" "k-t1" "notify" "n:c-a" "phase" "env-failed" "detail" "d" "failureKind" "lock-mismatch"
                               "finishedMs" 7}]}) body)
  (val acked (responded state (run (http-request "DELETE" "/outbox/t1" {} None)) 10 timing))
  (val after (get acked 0))
  (val code (get acked 1))
  (val answer (get acked 2))
  (assert (= #(code answer) #(200 {"id" "t1" "acked" True})) answer)
  (assert (= (.keys after.outbox) #())))


(deftest test-notify-refusal-takes-one-short-word []
  ;; 受け口(submit-detached)と構築関数(submit-detached-task)が同じ規則で断る — 読み手は語の頭で選ぶので空白で割らせない。
  (assert (is (task-notify-refusal None) None))
  (assert (is (task-notify-refusal "n:c-a") None))
  (assert (in "notify" (task-notify-refusal "two words")))
  (assert (in "notify" (task-notify-refusal (* "x" 201))))
  (assert (in "notify" (task-notify-refusal 3))))


(deftest test-notify-and-outbox-survive-the-store []
  (val row (DetachedEnded :id "t9" :key "k-t9" :notify "n:c-a" :phase "lost" :detail "x" :failure-kind "" :finished-ms 3))
  (val state (! (state-of #((! (task-row "t1" "assigned")) (! (task-row "t2" "assigned" :notify ""))) :outbox #(row))))
  ;; 知らせ先の無い行は欄 notify を置かない(欄を知らない前の版へ戻しても、開いた行を旧い形と読まない)。
  (val plain (task-record-to-json (! (task-row "t2" "assigned" :notify ""))))
  (assert (not-in "notify" plain) plain)
  (val named (task-record-to-json (! (task-row "t1" "assigned"))))
  (assert (= (get named "notify") "n:c-a") named)
  (assert (= (. (run (task-record-from-json named)) notify) "n:c-a"))
  (assert (= (. (run (task-record-from-json plain)) notify) ""))
  ;; 耐久の KV と state file の往復で、出口の行と知らせ先が残る。
  (val kv (run (durable-kv state)))
  (assert (in "outbox/t9" kv) (sorted kv))
  (val back (run (state-from-kv kv 0)))
  (assert (= (.row back.outbox "t9") row))
  (assert (= (. (.row back.tasks "t1") notify) "n:c-a"))
  (val restored (run (state-from-json (run (state-to-json state)) 0)))
  (assert (= (.row restored.outbox "t9") row)))
