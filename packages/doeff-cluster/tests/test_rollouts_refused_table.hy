;;; rollouts と refused の欄が表(Table)で、書き手が「動いた行だけの書き」であることの検(ki-099b9b525351 — T6a)。
;;;
;;; 時間では測らない。見る物は 3 つ: 残る行(鍵)、書きの無い拍で表・状態が元と同じ物(is)であること、保存の鍵と値。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState RefusedJob RolloutRow RolloutStatus TaskRecord WorkerInfo])
(import doeff_cluster.coordinator.core.api_policy [plan-rollouts record-action])
(import doeff_cluster.coordinator.core.resource_policy [delete-resource])
(import doeff_cluster.coordinator.core.rollout_policy [validate-rollout-spec])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json read-service-rows])
(import doeff_hy.table [Table TableWrite table-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming))
;; 相手が Service だけの Rollout の spec(Deployment の相手を持たないので、持ち主の判断や観測を使わない)。
(val SPEC (validate-rollout-spec {"from" {"kind" "Service" "name" "old"}
                                  "to" {"kind" "Service" "name" "new"}
                                  "readyTimeoutSeconds" 300 "stopTimeoutSeconds" 180 "observeSeconds" 60 "failAfterSeconds" 30
                                  "rollbackTimeoutSeconds" 600 "markDeployment" False "abort" False}))
(val DONE (RolloutRow :spec SPEC :status (RolloutStatus :phase "Complete" :completed-ms 500)))
(val LIVE (RolloutRow :spec SPEC :status (RolloutStatus :created-ms 0)))
(val OLD-ROW {"name" "old" "revision" "r9"})


(defk state-with-rollouts [rows]
  {:pre [(: rows dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "rollouts だけを持つ状態を、鍵 → RolloutRow の dict から作るため。"
  (<- table Table (table-from-rows rows))
  (ClusterState :rollouts table))


(deftest test-plan-rollouts-keeps-the-state-when-every-rollout-is-over
  (<- state ClusterState (state-with-rollouts {"a" DONE "b" (replace DONE :status (RolloutStatus :phase "RolledBack"))}))
  (<- planned tuple (plan-rollouts state 1000 T))
  (assert (is (get planned 0) state) "終わった Rollout だけの拍で状態が変わった")
  (assert (= (get planned 1) []) planned))


(deftest test-plan-rollouts-writes-only-the-rollout-that-moved
  (<- state ClusterState (state-with-rollouts {"done" DONE "live" LIVE}))
  (<- planned tuple (plan-rollouts state 1000 T))
  (val next (get planned 0))
  (assert (isinstance next.rollouts Table) next.rollouts)
  (assert (is (.row next.rollouts "done") DONE) "終わった Rollout の行が作り直された")
  (assert (!= (.row next.rollouts "live") LIVE) "動く Rollout の段が進まなかった")
  (assert (= (.keys next.rollouts) (.keys state.rollouts))))


(deftest test-record-action-rewrites-one-row-and-an-unknown-rollout-keeps-the-state
  (<- state ClusterState (state-with-rollouts {"done" DONE "live" LIVE}))
  (val action {"rollout" "live" "op" "scale" "target" SPEC.from-target "replicas" 0})
  (assert (is (record-action state {"rollout" "zz" "op" "scale"} True None 1000) state))
  (val recorded (record-action state action True None 1000))
  (assert (is (.row recorded.rollouts "done") DONE))
  (assert (!= (.row recorded.rollouts "live") LIVE)))


(deftest test-delete-resource-drops-the-row-of-each-table-and-keeps-the-others
  (val worker (WorkerInfo "w1" #("net") 10 0 :task-reserve 0))
  (val task (TaskRecord "t1" "n" "b" "r" #() #() 15000 16000 1000))
  (<- rollouts Table (table-from-rows {"done" DONE "keep" DONE}))
  (<- refused Table (table-from-rows {"old" (RefusedJob :name "old" :row OLD-ROW :reason "旧い形の行")}))
  (<- tasks Table (table-from-rows {"t1" task}))
  (<- workers Table (table-from-rows {"w1" worker}))
  (val state (ClusterState :rollouts rollouts :refused refused :tasks tasks :workers workers))
  (val gone-rollout (delete-resource state "Rollout" "done" {} 1000000 T))
  (assert (= (.keys gone-rollout.rollouts) #("keep")) (.keys gone-rollout.rollouts))
  (assert (is gone-rollout.refused state.refused))
  (val gone-refused (delete-resource state "Service" "old" {} 1000000 T))
  (assert (= (.size gone-refused.refused) 0))
  (assert (is gone-refused.rollouts state.rollouts))
  (assert (= (.size (. (delete-resource state "Task" "t1" {} 1000000 T) tasks)) 0))
  (assert (= (.size (. (delete-resource state "Worker" "w1" {} 1000000 T) workers)) 0)))


(deftest test-rollouts-and-refused-keep-their-stored-keys-and-values
  (<- rollouts Table (table-from-rows {"r1" DONE}))
  (<- refused Table (table-from-rows {"old" (RefusedJob :name "old" :row OLD-ROW :reason "旧い形の行")}))
  (val state (ClusterState :rollouts rollouts :refused refused))
  (<- kv dict (full-kv state))
  (assert (in "rollout/r1" kv) (sorted kv))
  ;; 受け付けない行は jobs と同じ接頭辞 service/<名> に、元の行のまま保つ。
  (assert (= (get kv "service/old") OLD-ROW) (get kv "service/old"))
  (<- again ClusterState (state-from-kv kv 0))
  (assert (= again.rollouts state.rollouts))
  (assert (= (.keys again.refused) #("old")))
  (assert (= (. (.row again.refused "old") row) OLD-ROW))
  (<- data dict (state-to-json state))
  (assert (= (get data "jobs") [OLD-ROW]) (get data "jobs"))
  (assert (in "r1" (get data "rollouts")))
  (<- from-file ClusterState (state-from-json data 0))
  (assert (= from-file.rollouts state.rollouts))
  (assert (= (.keys from-file.refused) #("old"))))


(deftest test-read-service-rows-reads-an-unreadable-row-into-the-refused-table
  (val rows (read-service-rows [OLD-ROW]))
  (val jobs (get rows 0))
  (val refused (get rows 1))
  (assert (= jobs #()) jobs)
  (assert (isinstance refused Table) refused)
  (assert (= (.keys refused) #("old")))
  (assert (in "受け付けない" (. (.row refused "old") reason)) (.row refused "old")))
