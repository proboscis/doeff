;; heartbeat の task の 3 周(absorb-task-reports・renew-detached・promote-prepared)は、変わる行が無ければ入力の dict を同じ object で返し、
;; 変わる行が有る時は変わった行だけ新しい object にする(他の行は元の object — durable-delta の tasks の組を飛ばすため・Q3)。
(require doeff-hy.macros [deftest defk val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ComponentVersion TaskRecord WorkerInfo])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow])
(import doeff_cluster.coordinator.core.cluster_policy [absorb-task-reports renew-detached promote-prepared])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(defk task [id phase worker detached boot]
  {:pre [(: id str) (: phase str) (: worker (| str None)) (: detached bool) (: boot (| str None))] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "phase・worker・世代 boot で置いた task の記録(lease の期限 20000・延長の幅 15000)を作るため。"
  (replace (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" #((ComponentVersion "python" "3")) #() 15000 20000 0)
           :phase phase :worker worker :detached detached :boot boot))

(defk rows []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker w の切り離した task(世代 b1)・別の世代 b0 の切り離した task・別の worker の切り離した task・待っている task の 4 行。"
  {"mine" (! (task "mine" "assigned" "w" True "b1"))
   "old-gen" (! (task "old-gen" "assigned" "w" True "b0"))
   "other" (! (task "other" "assigned" "x" True "b1"))
   "waiting" (! (task "waiting" "queued" None False None))})

(deftest test-renew-detached-returns-the-same-dict-when-nothing-needs-extending
  (val tasks (! (rows)))
  (assert (is (renew-detached tasks "nobody" "b1" 1000) tasks))
  ;; 別の世代の heartbeat は延ばさない(置いた世代の process にしか延ばさない)
  (assert (is (renew-detached tasks "w" "b9" 1000) tasks)))

(deftest test-renew-detached-renews-only-the-row-held-by-the-same-worker-and-boot
  (val tasks (! (rows)))
  (val renewed (renew-detached tasks "w" "b1" 1000))
  (assert (is-not renewed tasks))
  (assert (= renewed.mine.lease-until-ms (+ 1000 15000)) renewed)
  (assert (= (get renewed "mine") (replace (get tasks "mine") :lease-until-ms 16000)))
  (for [id ["old-gen" "other" "waiting"]]
    (assert (is (get renewed id) (get tasks id)) id))
  (assert (= (list renewed) (list tasks))))

(deftest test-promote-prepared-returns-the-same-dict-when-no-row-is-ready
  (val tasks {"p" (! (task "p" "preparing" "w" False None)) "q" (! (task "q" "queued" None False None))})
  ;; env-ready-on は runtime-env の無い task を準備済みとしない
  (val info (WorkerInfo "w" #() 10 0 :task-reserve 0))
  (assert (is (promote-prepared tasks info) tasks)))

(deftest test-absorb-task-reports-returns-the-same-dict-when-nothing-is-reported
  (val state (ClusterState #() {} {} (! (rows))))
  (assert (is (absorb-task-reports state "w" #() 1000 "b1") state.tasks))
  ;; 知らない task・置いていない worker の行・走っている(running)報告だけでは task は変わらない
  (assert (is (absorb-task-reports state "w" #((StatusRow :name "task/zzz" :phase "finished")) 1000 "b1") state.tasks))
  (assert (is (absorb-task-reports state "w" #((StatusRow :name "job-a" :phase "running")) 1000 "b1") state.tasks))
  (assert (is (absorb-task-reports state "x" #((StatusRow :name "task/mine" :phase "finished")) 1000 "b1") state.tasks)))

(deftest test-absorb-task-reports-rewrites-only-the-reported-row
  (val state (ClusterState #() {} {} (! (rows))))
  (val out (absorb-task-reports state "w" #((StatusRow :name "task/mine" :phase "code-failed" :detail "x")) 1000 "b1"))
  (assert (is-not out state.tasks))
  (assert (= (. (get out "mine") phase) "code-failed"))
  (for [id ["old-gen" "other" "waiting"]]
    (assert (is (get out id) (get state.tasks id)) id))
  ;; 別の世代の報告は写さない
  (assert (is (absorb-task-reports state "w" #((StatusRow :name "task/mine" :phase "code-failed" :detail "x")) 1000 "b9") state.tasks)))
