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

;; 時間・receiptだけを差し替え、実際の応答処理とidle回収を組み合わせる。
;; 出来事の読み(HeadlessEventsSince — 094424fc)は package の headless-substrate に memory の置き場
;; (MemoryEventStore — 検・fake の handler)を持たせた登記簿で受ける。provider の応答は本番の書き手
;; (子 process の読み手 = headless_process)と同じく置き場へ直接積む。
(import datetime [datetime timezone])
(import json)
(import doeff_agents.sessionhost.cache_host [cache-last-success-at cache-host-probe])
(import doeff_agents.sessionhost.cache_host_model [HostCacheLastSuccessAt HostCacheWrite])
(import doeff_agents.sessionhost.effects [ClockNow HeadlessKill])
(import doeff_agents.sessionhost.headless_events [HeadlessEventAppend MemoryEventStore])
(import doeff_agents.sessionhost.headless_process [HeadlessRegistry])
(import doeff_agents.sessionhost.substrate_headless [headless-substrate])
(import doeff_agents.sessionhost.acp.handlers [session-view-of])
(import doeff_agents.sessionhost.acp.judgment [sessions-to-retire cache-resident-retention-of])

(defhandler residency-world [#^ dict state]
  (ClockNow [] (resume (datetime.fromtimestamp (/ (get state "now") 1000) timezone.utc)))
  (HeadlessKill [session-name] (resume True))
  (HostCacheWrite [record]
    (.append (get state "receipts") record)
    (resume None))
  ;; card acp:kanban-issue:ki-567f2dd6140f §3.1e: host が名乗るのは**観測した事実**ちょうど
  ;; (最後に成功した専用操作の完了時刻)。保持の予算を足すのは ACP 側の判断。
  (HostCacheLastSuccessAt [session-id]
    (setv completions (lfor r (get state "receipts")
      :if (and (= r.session-id session-id) (= r.state MaintenanceState.SUCCEEDED)
               (is-not r.reply None) (> (+ r.reply.cache-read r.reply.cache-write) 0))
      r.reply.completed-at))
    (resume (if completions (max completions) None))))

(deftest test-clock-swapped-idle-cleanup-ping-and-next-cycle
  (setv store (MemoryEventStore)
        hosted (headless-substrate (HeadlessRegistry store))
        state {"now" 0 "receipts" []}
        wire {"session_id" "resident" "agent_type" "claude" "backend_kind" "headless"
              "status" "running" "work_dir" "/same" "lifecycle" "multi_turn"
              "conversation" {"session_id" "provider-session"}
              "turn_ended_at" "1970-01-01T00:00:00+00:00"})
  (for [now #(600000 3300000 3600000 6600000 6900000)]
    (setv (get state "now") now)
    (<- observed (| int None) ((residency-world state) (cache-last-success-at (get wire "session_id"))))
    (setv snapshot (| wire {"cache_last_success_at_ms" observed})
          view (session-view-of snapshot))
    (<- retired tuple (sessions-to-retire #(view) now 600))
    (assert (= retired #()))
    (when (in now #(3300000 6600000))
      ;; provider応答は本番の書き手(子processの読み手)と同じく置き場へ積む。成功判定・保持の判断は実Program。
      ;; locatorは本番の綴り(行のevents-path + ".cache-" + 操作id — cache-host-ping)。
      (setv at (.isoformat (datetime.fromtimestamp (/ now 1000) timezone.utc))
            locator f"/state/resident.events.jsonl.cache-ping-{now}")
      (for [line #((json.dumps {"type" "assistant" "timestamp" at
                                "message" {"id" f"response-{now}" "model" "model" "usage"
                                  {"cache_read_input_tokens" 64000 "cache_creation_input_tokens" 42
                                   "cache_creation" {"ephemeral_1h_input_tokens" 42}}}})
                   (json.dumps {"type" "result" "is_error" False}))]
        (.append store (HeadlessEventAppend locator "stdout" line at)))
      (setv pending (HostCacheRecord f"ping-{now}" "resident" (+ now 300000)
        "process" locator :state MaintenanceState.RUNNING :started-at now))
      (<- completed HostCacheRecord (hosted ((residency-world state) (cache-host-probe pending))))
      (assert (= completed.state MaintenanceState.SUCCEEDED))))
  ;; handlerを作り直しても永続receiptが同じなら同じ期限。新しい応答なしでは有限で回収。
  (<- observed (| int None) ((residency-world state) (cache-last-success-at (get wire "session_id"))))
  (assert (= observed 6600000))
  (setv view (session-view-of (| wire {"cache_last_success_at_ms" observed})))
  ;; 期限の判断は ACP 側の 1 点(host は予算を 1 度も足さない)。
  (<- retained (| int None) (cache-resident-retention-of view))
  (assert (= retained 10200000))
  (<- expired tuple (sessions-to-retire #(view) 10200000 600))
  (assert (= expired #("resident")))
  (assert (= view.turn-ended-at-ms 0)))
