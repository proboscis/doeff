(require doeff-hy.macros [defk <-])

(import dataclasses [replace])
(import .cache_operation [CacheReply MaintenanceRecord MaintenanceState ResidentCache
  PingOutcome PingRunning PingCompleted PingFailed PingMissing MaintenanceTransportUncertainError
  MaintenanceNow ReadMaintenance InspectResidentCache ClaimMaintenance StartMaintenancePing
  ProbeMaintenancePing FinishMaintenance])

(defk finish-maintenance [record state now reason reply]
  {:pre [(: record MaintenanceRecord) (: state MaintenanceState) (: now int)
         (: reason (| str None)) (: reply (| CacheReply None))]
   :post [(: % MaintenanceRecord)]}
  (setv updated (replace record :state state :finished-at now :reason reason :reply reply))
  (<- saved MaintenanceRecord (FinishMaintenance record updated))
  saved)

(defk settle-maintenance [record outcome now]
  {:pre [(: record MaintenanceRecord) (: outcome PingOutcome) (: now int)]
   :post [(: % MaintenanceRecord)]}
  (cond
    (isinstance outcome PingCompleted)
      (if (!= outcome.reply.model record.operation.target.model)
        (<- (finish-maintenance record MaintenanceState.FAILED now "model-changed" None))
        (<- (finish-maintenance record MaintenanceState.SUCCEEDED now None outcome.reply)))
    (isinstance outcome PingFailed)
      (<- (finish-maintenance record MaintenanceState.FAILED now outcome.reason None))
    (>= now record.operation.expires-at)
      ;; 証拠のない実行を成功・未送信のどちらにも推測しない。
      (<- (finish-maintenance record MaintenanceState.UNKNOWN now "no-result-before-deadline" None))
    True record))

(defk reconcile-cache-operation [key worker-node]
  {:pre [(: key str) (: worker-node str)] :post [(: % (| MaintenanceRecord None))]}
  (<- record (| MaintenanceRecord None) (ReadMaintenance key))
  (when (is record None) (return None))
  (when (!= record.operation.target.node worker-node) (return record))
  (when (not-in record.state #(MaintenanceState.REQUESTED MaintenanceState.RUNNING)) (return record))
  (<- now int (MaintenanceNow))
  (when (= record.state MaintenanceState.RUNNING)
    ;; 再起動後も送信し直さず、対象sessionhostの専用操作を照会する。
    (try
      (<- outcome PingOutcome (ProbeMaintenancePing record.operation))
      (except [MaintenanceTransportUncertainError] (setv outcome (PingMissing))))
    (return (<- (settle-maintenance record outcome now))))
  (when (>= now record.operation.expires-at)
    (return (<- (finish-maintenance record MaintenanceState.EXPIRED now "expired-before-send" None))))
  (<- resident (| ResidentCache None) (InspectResidentCache record.operation.target))
  (when (or (is resident None) (not resident.available))
    (return (<- (finish-maintenance record MaintenanceState.FAILED now "resident-unavailable" None))))
  (when (!= resident.target record.operation.target)
    (return (<- (finish-maintenance record MaintenanceState.FAILED now "identity-changed" None))))
  (when (not resident.boundary-allowed)
    (return (<- (finish-maintenance record MaintenanceState.FAILED now "company-boundary-forbidden" None))))
  (<- claimed (| MaintenanceRecord None) (ClaimMaintenance record now))
  (when (is claimed None) (return record))
  ;; 認証・実行元の確認後に専用操作を渡す。通常仕事の枠・優先度は入力にすら持たない。
  (try
    (<- outcome PingOutcome (StartMaintenancePing claimed.operation))
    (except [MaintenanceTransportUncertainError] (setv outcome (PingMissing))))
  (<- after int (MaintenanceNow))
  (<- result MaintenanceRecord (settle-maintenance claimed outcome after))
  result)
