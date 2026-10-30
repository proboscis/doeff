;; 切り離した task の終わりの出口(card ki-e7945a2b2d00 — detached_model の頭の註の出口・detached_policy の出口の節)の純粋な判断の検。
;; 筋書き(3 つの組で送って終わらせて読む)は test_detached.hy の test-outbox-*。ここは 1 歩の前後の差から積む判断(ended-of・outbox-noted)・
;; 読みと受領の口(GET /outbox・DELETE /outbox/<id>)・受け口の notify の検め・保存の往復を確かめる。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff [run])
(import doeff_hy._table [TableWrite table-of])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [DetachedEnded DetachedOutbox DetachedOutboxAcked OUTBOX-ENDED OUTBOX-QUEUED-OVERDUE])
(import doeff_cluster [ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord])
(import doeff_cluster._shared.core.detached_rules [task-notify-refusal])
(import doeff_cluster._coordinator.core.detached_policy [ended-of outbox-noted outbox-read outbox-ack queued-noticed OUTBOX-RETAIN-MS])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._coordinator.protocol.durable_kv [durable-kv state-from-kv])
(import doeff_cluster._coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_cluster [responded])
(import doeff_cluster [http-request])


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
                               "finishedMs" 7 "kind" "ended"}]}) body)
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


;; --- queued の期限超えの知らせ(card ki-4b6613f39c80 — detached_policy の queued-noticed)---------------------------------------

(val NOTICE-MS (. (ClusterTiming) queued-notice-ms))


(deftest test-queued-noticed-adds-one-overdue-row-past-the-deadline []
  (val timing (ClusterTiming))
  (val waiting (replace (! (task-row "t1" "queued")) :detail "負の優先の task は予約を越える空きができるまで待つ"))
  (val state (! (state-of #(waiting (! (task-row "t2" "queued" :notify "")) (! (task-row "t3" "queued" :detached False))
                           (! (task-row "t4" "assigned"))))))
  ;; 期限の前の歩は何も積まない(同じ状態を返す — 保存の差分を作らない)。
  (assert (is (! (queued-noticed state (- NOTICE-MS 1) timing)) state))
  ;; 期限を越えた歩: 知らせ先つきの切り離した queued の task だけに、置かれない理由を持つ待ちの行を 1 つ積み、task に印を置く。
  (val noted (! (queued-noticed state NOTICE-MS timing)))
  (assert (= (.keys noted.outbox) #("t1/queued-overdue")) (.keys noted.outbox))
  (assert (= (.row noted.outbox "t1/queued-overdue")
             (DetachedEnded :id "t1/queued-overdue" :key "k-t1" :notify "n:c-a" :phase "queued"
                            :detail "負の優先の task は予約を越える空きができるまで待つ" :failure-kind "" :finished-ms NOTICE-MS
                            :kind OUTBOX-QUEUED-OVERDUE)))
  (assert (= (. (.row noted.tasks "t1") queued-noticed-ms) NOTICE-MS))
  (assert (is (. (.row noted.tasks "t2") queued-noticed-ms) None)))


(deftest test-queued-noticed-never-adds-a-second-row []
  (val timing (ClusterTiming))
  (val state (! (state-of #((! (task-row "t1" "queued"))))))
  (val noted (! (queued-noticed state NOTICE-MS timing)))
  ;; 待ち続けても 2 行目を積まない(印が残る)。
  (val later (! (queued-noticed noted (* 8 NOTICE-MS) timing)))
  (assert (is later noted))
  ;; 読み手が受領して行を外しても積み直さない。
  (val acked (. (outbox-ack noted "t1/queued-overdue") state))
  (assert (= (.keys acked.outbox) #()))
  (assert (is (! (queued-noticed acked (* 8 NOTICE-MS) timing)) acked))
  ;; 印は保存の往復で残る(起き直した coordinator も 2 度目を積まない)。
  (val back (run (state-from-kv (run (durable-kv acked)) 0)))
  (assert (= (. (.row back.tasks "t1") queued-noticed-ms) NOTICE-MS))
  (assert (is (! (queued-noticed back (* 8 NOTICE-MS) timing)) back)))


(deftest test-ending-task-drops-its-unacked-overdue-row []
  (val timing (ClusterTiming))
  (val noted (! (queued-noticed (! (state-of #((! (task-row "t1" "queued"))))) NOTICE-MS timing)))
  ;; 受領の前に task が終わった: 終わりの行を積み、古い待ちの行を外す。
  (val ended (replace noted :tasks (.with-writes noted.tasks #((TableWrite "t1" (! (task-row "t1" "cancelled")))))))
  (val after (outbox-noted noted ended (+ NOTICE-MS 10)))
  (assert (= (.keys after.outbox) #("t1")) (.keys after.outbox)))


(deftest test-outbox-read-shows-overdue-rows-only-to-readers-naming-the-kind []
  (val ended (DetachedEnded :id "t1" :key "k-t1" :notify "n:c-a" :phase "finished" :detail "" :failure-kind "" :finished-ms 10))
  (val overdue (DetachedEnded :id "t2/queued-overdue" :key "k-t2" :notify "n:c-a" :phase "queued" :detail "待つ" :failure-kind ""
                              :finished-ms 5 :kind OUTBOX-QUEUED-OVERDUE))
  (val state (! (state-of #() :outbox #(ended overdue))))
  ;; 種類を名乗らない読み手(この欄の前の版)には終わりの行だけ — 待ちを終わりと読み違えない。
  (assert (= (lfor e (. (outbox-read state "n:") body entries) e.id) ["t1"]))
  (assert (= (lfor e (. (outbox-read state "n:" (frozenset #(OUTBOX-ENDED OUTBOX-QUEUED-OVERDUE))) body entries) e.id)
             ["t2/queued-overdue" "t1"]))
  (val timing (ClusterTiming))
  (val plain (get (responded state (run (http-request "GET" "/outbox" {"prefix" "n:"} None)) 20 timing) 2))
  (assert (= (lfor e (get plain "entries") (get e "id")) ["t1"]) plain)
  (val both (get (responded state (run (http-request "GET" "/outbox" {"prefix" "n:" "kinds" "ended,queued-overdue"} None)) 20 timing) 2))
  (assert (= (lfor e (get both "entries") #((get e "id") (get e "kind"))) [#("t2/queued-overdue" "queued-overdue") #("t1" "ended")]) both)
  ;; 保存の往復で種類が残る。
  (val back (run (state-from-kv (run (durable-kv state)) 0)))
  (assert (= (.row back.outbox "t2/queued-overdue") overdue)))


(deftest test-queued-notice-mark-is-stored-only-when-set []
  ;; 印の無い行は欄を置かない(欄を知らない前の版へ戻しても、待っている行を旧い形と読んで failed にしない)。
  (val plain (task-record-to-json (! (task-row "t1" "queued"))))
  (assert (not-in "queued_noticed_ms" plain) plain)
  (val marked (task-record-to-json (replace (! (task-row "t1" "queued")) :queued-noticed-ms 42)))
  (assert (= (get marked "queued_noticed_ms") 42) marked)
  (assert (= (. (run (task-record-from-json marked)) queued-noticed-ms) 42))
  (assert (is (. (run (task-record-from-json plain)) queued-noticed-ms) None)))
