;; heartbeat の task の 3 周(absorb-task-reports・renew-detached・promote-prepared)は、変わる行が無ければ入力の dict を同じ object で返し、
;; 変わる行が有る時は変わった行だけ新しい object にする(他の行は元の object — durable-delta の tasks の組を飛ばすため・Q3)。
(require doeff-hy.macros [deftest defk val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_hy.table [Table])
(import tests.state_rows [table-from-rows])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ComponentVersion TaskRecord WorkerInfo])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow])
(import doeff_cluster.coordinator.core.cluster_policy [absorb-task-reports renew-detached promote-prepared])
(import unittest.mock [Mock])
(import doeff_cluster.coordinator.core.cluster_policy [register-heartbeat])
(import doeff_cluster.coordinator.protocol.durable_kv :as dk)
(import tests.program_rows [SAMPLE-TASK-PROGRAM heartbeat-of])

(defk task [id phase worker detached boot]
  {:pre [(: id str) (: phase str) (: worker (| str None)) (: detached bool) (: boot (| str None))] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "phase・worker・世代 boot で置いた task の記録(lease の期限 20000・延長の幅 15000)を作るため。"
  (replace (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" #((ComponentVersion "python" "3")) #() 15000 20000 0)
           :phase phase :worker worker :detached detached :boot boot))

(defk rows []
  {:pre [] :post [(: % Table)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker w の切り離した task(世代 b1)・別の世代 b0 の切り離した task・別の worker の切り離した task・待っている task の 4 行。"
  (! (table-from-rows {"mine" (! (task "mine" "assigned" "w" True "b1"))
                       "old-gen" (! (task "old-gen" "assigned" "w" True "b0"))
                       "other" (! (task "other" "assigned" "x" True "b1"))
                       "waiting" (! (task "waiting" "queued" None False None))})))

(deftest test-renew-detached-returns-the-same-dict-when-nothing-needs-extending
  (val tasks (! (rows)))
  (assert (is (renew-detached tasks "nobody" "b1" 1000) tasks))
  ;; 別の世代の heartbeat は延ばさない(置いた世代の process にしか延ばさない)
  (assert (is (renew-detached tasks "w" "b9" 1000) tasks)))

(deftest test-renew-detached-renews-only-the-row-held-by-the-same-worker-and-boot
  (val tasks (! (rows)))
  (val renewed (renew-detached tasks "w" "b1" 1000))
  (assert (is-not renewed tasks))
  (assert (= (.row renewed "mine") (replace (.row tasks "mine") :lease-until-ms 16000)))
  (for [id ["old-gen" "other" "waiting"]]
    (assert (is (.row renewed id) (.row tasks id)) id))
  (assert (= (list (.keys renewed)) (list (.keys tasks)))))

(deftest test-promote-prepared-returns-the-same-dict-when-no-row-is-ready
  (val tasks (! (table-from-rows {"p" (! (task "p" "preparing" "w" False None)) "q" (! (task "q" "queued" None False None))})))
  ;; env-ready-on は runtime-env の無い task を準備済みとしない
  (val info (WorkerInfo "w" #() 10 0 :task-reserve 0))
  (assert (is (promote-prepared tasks info) tasks)))

(deftest test-absorb-task-reports-returns-the-same-dict-when-nothing-is-reported
  (val state (ClusterState #() (! (table-from-rows {})) {} (! (rows))))
  (assert (is (absorb-task-reports state "w" #() 1000 "b1") state.tasks))
  ;; 知らない task・置いていない worker の行・走っている(running)報告だけでは task は変わらない
  (assert (is (absorb-task-reports state "w" #((StatusRow :name "task/zzz" :phase "finished")) 1000 "b1") state.tasks))
  (assert (is (absorb-task-reports state "w" #((StatusRow :name "job-a" :phase "running")) 1000 "b1") state.tasks))
  (assert (is (absorb-task-reports state "x" #((StatusRow :name "task/mine" :phase "finished")) 1000 "b1") state.tasks)))

(deftest test-absorb-task-reports-rewrites-only-the-reported-row
  (val state (ClusterState #() (! (table-from-rows {})) {} (! (rows))))
  (val out (absorb-task-reports state "w" #((StatusRow :name "task/mine" :phase "code-failed" :detail "x")) 1000 "b1"))
  (assert (is-not out state.tasks))
  (assert (= (. (.row out "mine") phase) "code-failed"))
  (for [id ["old-gen" "other" "waiting"]]
    (assert (is (.row out id) (.row state.tasks id)) id))
  ;; 別の世代の報告は写さない
  (assert (is (absorb-task-reports state "w" #((StatusRow :name "task/mine" :phase "code-failed" :detail "x")) 1000 "b9") state.tasks)))


;; --- heartbeat の全体(register-heartbeat)と durable-delta ---------------------------------------------------------

(defk beat [state name now]
  {:pre [(: state ClusterState) (: name str) (: now int)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task を担わない worker name の heartbeat(statuses 無し)を 1 つ受けた後の状態。"
  (! (register-heartbeat state (! (heartbeat-of {"name" name "provides" ["net"] "capacity" 2 "taskReserve" 0 "statuses" []})) now)))

(deftest test-a-heartbeat-of-a-worker-holding-no-task-keeps-the-tasks-dict
  ;; 受入: 切り離した task を担わない worker の heartbeat の後、tasks は前の歩と同じ object(2 度目の heartbeat で名乗りの変化も無い)。
  (val state (ClusterState #() (! (table-from-rows {})) {} (! (rows))))
  (val first (! (beat state "atlas" 1000)))
  (val second (! (beat first "atlas" 2000)))
  (assert (is first.tasks state.tasks))
  (assert (is second.tasks first.tasks)))

(deftest test-a-heartbeat-of-a-worker-holding-no-task-builds-no-task-keys [monkeypatch]
  ;; 受入: その歩の durable-delta は tasks の組の鍵を作らず(呼ばれた回数 0)、task/ の鍵を 1 本も書かない。
  (val state (ClusterState #() (! (table-from-rows {})) {} (! (rows))))
  (val first (! (beat state "atlas" 1000)))
  (val second (! (beat first "atlas" 2000)))
  (val spies (tuple (gfor g dk.SOURCE-GROUPS #(g.fields (Mock :wraps g.build)))))
  (.setattr monkeypatch dk "SOURCE_GROUPS" (tuple (gfor #(fields spy) spies (dk.SourceGroup :fields fields :build spy))))
  (val got (! (dk.durable-delta first second)))
  (assert (= (dfor #(fields spy) spies fields spy.call-count) (dfor #(fields spy) spies fields (if (= fields #("workers")) 2 0))))
  (assert (not (any (gfor k got (.startswith k "task/")))) got))

(deftest test-a-heartbeat-of-the-holding-worker-renews-only-its-detached-task
  ;; 担う worker の heartbeat は延ばした行だけ新しい物(組は飛ばない — 仕様どおり)。
  (val state (ClusterState #() (! (table-from-rows {})) {} (! (rows))))
  (val first (! (beat state "w" 1000)))
  (val second (! (beat first "w" 2000)))
  (assert (is-not second.tasks first.tasks))
  (assert (= (. (.row second.tasks "mine") lease-until-ms) (+ 2000 15000)))
  (assert (is (.row second.tasks "other") (.row first.tasks "other"))))
