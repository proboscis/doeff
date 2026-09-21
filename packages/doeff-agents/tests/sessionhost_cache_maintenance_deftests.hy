(require doeff-hy.macros [defhandler deftest <-])
(import dataclasses [replace])
(import doeff_agents.sessionhost.acp.cache_operation [CacheTarget CacheOperation CacheReply
  MaintenanceRecord MaintenanceState ResidentCache PingRunning PingCompleted PingMissing
  MaintenanceTransportUncertainError MaintenanceNow ReadMaintenance InspectResidentCache
  ClaimMaintenance StartMaintenancePing ProbeMaintenancePing FinishMaintenance])
(import doeff_agents.sessionhost.acp.cache_maintenance [reconcile-cache-operation])

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
