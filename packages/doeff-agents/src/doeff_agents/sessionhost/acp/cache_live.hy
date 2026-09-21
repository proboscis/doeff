;;; 専用操作のhandlerを既存のACP/RPC/資格effectsへ翻訳する。直接I/Oは持たない。
(require doeff-hy.macros [defk defhandler <-])
(import dataclasses [replace])
(import .cache_operation [CacheTarget CacheOperation MaintenanceRecord MaintenanceState ResidentCache
  PingOutcome PingRunning PingCompleted PingFailed PingMissing MaintenanceNow ReadMaintenance
  InspectResidentCache ClaimMaintenance StartMaintenancePing ProbeMaintenancePing FinishMaintenance
  AcpCacheOperations SessionCachePing SessionCacheProbe BorrowCacheCredential ReleaseCacheCredential])
(import .cache_wire [maintenance-of-row maintenance-status])
(import .cache_maintenance [reconcile-cache-operation])
(import ..cache_host_model [HostCacheRecord])
(import .effects [AgentdSettings AcpRow AcpGetRow AcpPutStatus ClockNowMs SessionGet SessionView
  Written Conflict Refused LeaseGrant LeaseRefused LogLine])
(import .judgment [profile-key-of conversation-key-of attribution-of-view turn-session-env-of])

(defk inspect-cache-resident [settings node-row target]
  {:pre [(: settings AgentdSettings) (: node-row str) (: target CacheTarget)]
   :post [(: % (| ResidentCache None))]}
  (when (!= target.node node-row) (return None))
  (<- view (| SessionView None) (SessionGet :session-id target.session))
  (when (or (is view None) (!= view.agent-type "claude") (!= view.backend-kind "headless")
            (!= view.status "running")) (return None))
  (<- attribution (| dict None) (attribution-of-view view))
  (when (or (is attribution None) (!= (.get attribution "account") target.account)
            (!= (.get attribution "conversationId") target.conversation)) (return None))
  (<- profile-key str (profile-key-of target.profile))
  (<- profile (| AcpRow None) (AcpGetRow :key profile-key))
  (when (or (is profile None) (!= (.get profile.spec "account") target.account)) (return None))
  (<- conv-key str (conversation-key-of target.conversation))
  (<- conversation (| AcpRow None) (AcpGetRow :key conv-key))
  (when (is conversation None) (return None))
  (setv status (or conversation.status {}) agent (.get status "agent"))
  (when (or (!= (.get status "state") "open") (not (isinstance agent dict))
            (!= (.get agent "generation") target.generation)) (return None))
  ;; 不明な所有境界は許可に推測しない。残量・capacityは読まない。
  (setv boundary (.get profile.spec "boundary"))
  (ResidentCache target True (and (in boundary #("personal" "company")) (in boundary settings.places))))

(defk cache-host-outcome [receipt]
  {:pre [(: receipt (| HostCacheRecord None))] :post [(: % PingOutcome)]}
  (cond
    (is receipt None) (PingMissing)
    (in receipt.state #(MaintenanceState.REQUESTED MaintenanceState.RUNNING)) (PingRunning)
    (= receipt.state MaintenanceState.SUCCEEDED)
      (do (assert (is-not receipt.reply None)) (PingCompleted receipt.reply))
    True (PingFailed (or receipt.reason receipt.state.value))))

(defk send-cache-operation [settings node-row operation]
  {:pre [(: settings AgentdSettings) (: node-row str) (: operation CacheOperation)]
   :post [(: % PingOutcome)]}
  (<- resident (| ResidentCache None) (inspect-cache-resident settings node-row operation.target))
  (when (or (is resident None) (not resident.boundary-allowed))
    (return (PingFailed "resident-or-boundary-changed")))
  (<- grant (| LeaseGrant LeaseRefused) (BorrowCacheCredential operation))
  (when (isinstance grant LeaseRefused) (return (PingFailed "credential-unavailable")))
  (<- env dict (turn-session-env-of grant))
  (<- receipt HostCacheRecord (SessionCachePing operation env))
  (<- (cache-host-outcome receipt)))

(defk write-cache-state [previous updated]
  {:pre [(: previous MaintenanceRecord) (: updated MaintenanceRecord)]
   :post [(: % (| MaintenanceRecord None))]}
  (<- row (| AcpRow None) (AcpGetRow :key previous.operation.key))
  (when (or (is row None) (!= row.generation previous.revision)) (return None))
  (<- outcome (| Written Conflict Refused) (AcpPutStatus :row row :status (maintenance-status updated)))
  (when (isinstance outcome Refused) (raise (RuntimeError f"cache-operation status refused: {outcome.status}")))
  (when (isinstance outcome Conflict) (return None))
  (<- fresh (| AcpRow None) (AcpGetRow :key row.key))
  (when (is fresh None) (raise (RuntimeError "cache-operation vanished after write")))
  (maintenance-of-row fresh))

(defhandler cache-live-handler [#^ AgentdSettings settings #^ str node-row]
  (MaintenanceNow [] (<- now int (ClockNowMs)) (resume now))
  (ReadMaintenance [key]
    (<- row (| AcpRow None) (AcpGetRow :key key))
    (resume (if row (maintenance-of-row row) None)))
  (InspectResidentCache [target]
    (<- resident (| ResidentCache None) (inspect-cache-resident settings node-row target))
    (resume resident))
  (ClaimMaintenance [record now]
    (<- claimed (| MaintenanceRecord None)
      (write-cache-state record (replace record :state MaintenanceState.RUNNING :started-at now)))
    (resume claimed))
  (StartMaintenancePing [operation]
    (<- outcome PingOutcome (send-cache-operation settings node-row operation))
    (resume outcome))
  (ProbeMaintenancePing [operation]
    (<- receipt (| HostCacheRecord None) (SessionCacheProbe operation))
    (if (and receipt (= receipt.state MaintenanceState.REQUESTED))
      (do
        ;; durable receiptが未送信を証明している場合だけ開始を試す。観測なしなら再送しない。
        (<- outcome PingOutcome (send-cache-operation settings node-row operation))
        (resume outcome))
      (do (<- outcome PingOutcome (cache-host-outcome receipt)) (resume outcome))))
  (FinishMaintenance [previous updated]
    (<- (ReleaseCacheCredential updated.operation))
    (<- saved (| MaintenanceRecord None) (write-cache-state previous updated))
    (resume (or saved previous))))

(defk maintain-node-cache [node-row]
  {:pre [(: node-row str)] :post [(: % int)]}
  (<- rows tuple (AcpCacheOperations node-row))
  (setv count 0)
  (for [row rows]
    (<- result (| MaintenanceRecord None) (reconcile-cache-operation row.key node-row))
    (when result
      (+= count 1)
      (when (not-in result.state #(MaintenanceState.REQUESTED MaintenanceState.RUNNING))
        (<- (ReleaseCacheCredential result.operation)))))
  count)
