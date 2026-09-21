(require doeff-hy.macros [defhandler deftest <-])
(import dataclasses [replace])
(import doeff_agents.sessionhost.acp.cache_operation [CacheTarget CacheOperation CacheReply
  MaintenanceRecord MaintenanceState ResidentCache PingRunning PingCompleted PingMissing
  MaintenanceTransportUncertainError MaintenanceNow ReadMaintenance InspectResidentCache
  ClaimMaintenance StartMaintenancePing ProbeMaintenancePing FinishMaintenance])
(import doeff_agents.sessionhost.acp.cache_maintenance [reconcile-cache-operation])
(import doeff_vm [WithHandler])
(import doeff_agents.sessionhost.acp.cache_live [cache-live-handler maintain-node-cache])
(import doeff_agents.sessionhost.acp.cache_operation [SessionCachePing SessionCacheProbe BorrowCacheCredential ReleaseCacheCredential])
(import doeff_agents.sessionhost.cache_host_model [HostCacheRecord])
(import doeff_agents.sessionhost.acp.fake [FakeAcp])
(import doeff_agents.sessionhost.acp.effects [AGORA-KINDS-NAMESPACE AcpRow AgentdSettings SessionView ClockNowMs SessionGet LeaseGrant])

(setv TARGET (CacheTarget "conversation" "session" "node" "profile" "account" "model" 1)
      OP (CacheOperation "cache-ping-1" TARGET "old-response" 3600000)
      REPLY (CacheReply "new-response" 3300000 3300100 "model" 3600 64000 0))

(defn world []
  {"record" (MaintenanceRecord OP 1) "now" 3300000
   "resident" (ResidentCache TARGET True True) "starts" 0 "probes" 0
   "outcome" (PingCompleted REPLY) "lose-reply" False "claim-lost" False})

(defhandler maintenance-world [#^ dict state]
  (MaintenanceNow [] (resume (get state "now")))
  (ReadMaintenance [key] (resume (get state "record")))
  (InspectResidentCache [target] (resume (get state "resident")))
  (ClaimMaintenance [record now]
    (if (get state "claim-lost")
      (resume None)
      (do
        (assert (= record (get state "record")))
        (setv next (replace record :revision (+ record.revision 1)
                           :state MaintenanceState.RUNNING :started-at now))
        (setv (get state "record") next)
        (resume next))))
  (StartMaintenancePing [operation]
    (assert (= operation OP))
    (setv (get state "starts") (+ (get state "starts") 1))
    (when (get state "lose-reply") (raise (MaintenanceTransportUncertainError "lost")))
    (resume (get state "outcome")))
  (ProbeMaintenancePing [operation]
    (setv (get state "probes") (+ (get state "probes") 1))
    (resume (get state "outcome")))
  (FinishMaintenance [previous updated]
    (assert (= previous (get state "record")))
    (setv (get state "record") (replace updated :revision (+ previous.revision 1)))
    (resume (get state "record"))))

(deftest test-special-operation-does-not-request-a-turn-or-read-queue-capacity
  (setv state (world))
  ;; installed handlerにはagent-job作成、SessionSend、枠取得のhandlerが無い。
  ;; それらを要求したら未処理effectとしてこの実VM試験が失敗する。
  (<- result MaintenanceRecord ((maintenance-world state)
    (reconcile-cache-operation OP.key "node")))
  (assert (= result.state MaintenanceState.SUCCEEDED))
  (assert (= result.reply REPLY))
  (assert (= (get state "starts") 1)))

(defn acp-row [kind key spec status]
  (AcpRow AGORA-KINDS-NAMESPACE (+ AGORA-KINDS-NAMESPACE ":" kind ":" key) kind key "v1" 1 0 {} {} spec status))

(defhandler cache-leaves [#^ dict state]
  (ClockNowMs [] (resume 3300000))
  (SessionGet [session-id]
    (resume (SessionView session-id "claude" "running" "/same-work" "multi_turn"
      {"session_id" "same-provider-session"} {"CLAUDE_CONFIG_DIR" "/same-profile"}
      None None 3200000 :backend-kind "headless"
      :launch-attribution {"agentd" {"conversationId" "conversation" "account" "account"}})))
  (BorrowCacheCredential [operation]
    (assert (= operation.target.account "account"))
    (resume (LeaseGrant "lease" "claude" False 4000000 "test-only-token" None)))
  (ReleaseCacheCredential [operation] (resume True))
  (SessionCachePing [operation session-env]
    (setv (get state "starts") (+ (get state "starts") 1))
    (assert (= operation.target.session "session"))
    (resume (HostCacheRecord operation.key "session" operation.expires-at "special-child" "events"
      :state MaintenanceState.SUCCEEDED :started-at REPLY.started-at :reply REPLY)))
  (SessionCacheProbe [operation] (resume None)))

(deftest test-live-handler-composition-runs-with-zero-normal-capacity
  (setv operation (acp-row "cache-operation" "ping"
    {"conversationId" "conversation" "sessionId" "session" "nodeRow" "node" "profile" "profile"
     "account" "account" "model" "model" "declarationGeneration" 1
     "observedResponseId" "old-response" "expiresAt" 3600000} {"state" "requested"}))
  (setv profile (acp-row "profile" "profile" {"account" "account" "boundary" "personal"} {"state" "exhausted"}))
  (setv conversation (acp-row "conversation" "conversation" {} {"state" "open" "agent" {"generation" 1}}))
  (setv acp (FakeAcp {}) state {"starts" 0})
  (for [row #(operation profile conversation)] (.put-row acp row))
  (setv settings (AgentdSettings "node-name" :node-capacity 0 :places #("personal")))
  (<- count int (WithHandler acp.dispatch
    ((cache-leaves state) ((cache-live-handler settings "node") (maintain-node-cache "node")))))
  (setv saved (get acp.rows operation.key))
  (assert (= count 1) f"count={count}")
  (assert (= (get state "starts") 1) f"state={state}, saved={saved}")
  (assert (= (get saved.status "state") "succeeded") f"saved={saved}")
  (assert (= (get saved.status "cacheObservation" "cacheRead") 64000))
  ;; 使った資源は専用操作1行だけ。通常job・message・turn-recordを作らない。
  (assert (= (len acp.rows) 3)))

(deftest test-response-loss-and-worker-restart-probe-instead-of-resending
  (setv state (world) (get state "lose-reply") True)
  (<- first MaintenanceRecord ((maintenance-world state)
    (reconcile-cache-operation OP.key "node")))
  (assert (= first.state MaintenanceState.RUNNING))
  ;; 新しいhandler/Programでも、永続したrunningから専用操作を照会する。
  (<- recovered MaintenanceRecord ((maintenance-world state)
    (reconcile-cache-operation OP.key "node")))
  (assert (= recovered.state MaintenanceState.SUCCEEDED))
  (assert (= (get state "starts") 1))
  (assert (= (get state "probes") 1)))

(deftest test-claim-loser-and-terminal-replay-never-send
  (setv state (world) (get state "claim-lost") True)
  (<- ((maintenance-world state) (reconcile-cache-operation OP.key "node")))
  (assert (= (get state "starts") 0))
  (setv (get state "claim-lost") False)
  (<- ((maintenance-world state) (reconcile-cache-operation OP.key "node")))
  (<- ((maintenance-world state) (reconcile-cache-operation OP.key "node")))
  (assert (= (get state "starts") 1)))

(deftest test-wrong-node-profile-and-company-boundary-cannot-be-bypassed
  (setv state (world))
  (<- ((maintenance-world state) (reconcile-cache-operation OP.key "other-node")))
  (assert (= (get state "starts") 0))
  (for [resident #((ResidentCache (replace TARGET :profile "other") True True)
                   (ResidentCache TARGET True False))]
    (setv state (world) (get state "resident") resident)
    (<- result MaintenanceRecord ((maintenance-world state)
      (reconcile-cache-operation OP.key "node")))
    (assert (= result.state MaintenanceState.FAILED))
    (assert (= (get state "starts") 0))))

(deftest test-expired-request-never-starts-and-lost-running-result-is-unknown
  (setv state (world) (get state "now") 3600000)
  (<- expired MaintenanceRecord ((maintenance-world state)
    (reconcile-cache-operation OP.key "node")))
  (assert (= expired.state MaintenanceState.EXPIRED))
  (assert (= (get state "starts") 0))
  (setv state (world) (get state "outcome") (PingMissing))
  (<- ((maintenance-world state) (reconcile-cache-operation OP.key "node")))
  (setv (get state "now") 3600000)
  (<- uncertain MaintenanceRecord ((maintenance-world state)
    (reconcile-cache-operation OP.key "node")))
  (assert (= uncertain.state MaintenanceState.UNKNOWN))
  (assert (is uncertain.reply None))
  (assert (= (get state "starts") 1)))
