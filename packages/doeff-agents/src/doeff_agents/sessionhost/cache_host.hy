;;; キャッシュ維持は専用操作。通常sessionの行とevent journalは書かない。
(require doeff-hy.macros [defk <-])
(import dataclasses [replace])
(import hashlib)
(import json)
(import datetime [datetime timezone])
(import .cache_host_model [HostCacheRecord HostCacheRead HostCacheWrite HostCacheActive CacheMaintenanceActiveError])
(import .acp.cache_operation [MaintenanceState CacheReply PING-TEXT])
(import .acp.cache_observation [cache-observation-of])
(import .effects [clock-now fs-read-text headless-has-session headless-spawn
                  headless-deliver headless-kill])
(import .headless [require-headless-row headless-launch-args events-path-of-row])
(import .headless_protocol [CLI-OWN-TURN-ORIGINS])
(import .launch [launch-spawn-env])
(import .policy [carry-launch-flags is-terminal-status])

(defk cache-host-now []
  {:pre [True] :post [(: % int)]}
  (<- now (clock-now))
  (int (* (now.timestamp) 1000)))

(defk cache-host-probe [record]
  {:pre [(: record HostCacheRecord)] :post [(: % HostCacheRecord)]}
  (<- now int (cache-host-now))
  (when (= record.state MaintenanceState.REQUESTED)
    (when (>= now record.expires-at)
      (setv record (replace record :state MaintenanceState.EXPIRED :reason "expired-before-send"))
      (<- (HostCacheWrite record)))
    (return record))
  (when (!= record.state MaintenanceState.RUNNING) (return record))
  (<- text (| str None) (fs-read-text record.events-path))
  (setv records [] ended None)
  (for [line (.splitlines (or text ""))]
    (try (setv item (json.loads line))
      ;; 最後のJSON行はwriterがまだ書いている可能性がある。次回同じfileを読み直す。
      (except [json.JSONDecodeError] (continue)))
    (when (not (isinstance item dict)) (continue))
    (when (= (.get item "type") "assistant")
      (setv observed (dict item))
      (when (not (.get observed "timestamp"))
        (setv (get observed "timestamp")
          (.isoformat (datetime.fromtimestamp (/ now 1000) timezone.utc))))
      (.append records observed))
    (when (= (.get item "type") "result")
      (setv origin (.get item "origin"))
      (when (not (and (isinstance origin dict) (in (.get origin "kind") CLI-OWN-TURN-ORIGINS)))
        (setv ended item))))
  (setv updated record)
  (cond
    (is-not ended None)
      (do
        (<- observation (| dict None) (cache-observation-of (tuple records)))
        (if (or (.get ended "is_error") (is observation None))
          (setv updated (replace record :state MaintenanceState.FAILED
                            :reason (if (.get ended "is_error") "provider-error" "cache-observation-missing")))
          (do
            (assert (is-not record.started-at None))
            (setv reply (CacheReply (get observation "responseId") record.started-at
                          (get observation "at") (get observation "model")
                          (get observation "ttlSeconds") (get observation "cacheRead")
                          (get observation "cacheWrite")))
            (setv updated (replace record :state MaintenanceState.SUCCEEDED :reply reply))))
        ;; CLIのresult後の処理も終えてからsessionの排他を解く。
        (<- (headless-kill record.process-name)))
    (>= now record.expires-at)
      (do
        (<- (headless-kill record.process-name))
        (setv updated (replace record :state MaintenanceState.UNKNOWN :reason "deadline-without-result"))))
  (when (!= record updated) (<- (HostCacheWrite updated)))
  updated)

(defk cache-host-ping [session-id operation-id expires-at session-env]
  {:pre [(: session-id str) (: operation-id str) (: expires-at int) (: session-env dict)]
   :post [(: % HostCacheRecord)]}
  (<- existing (| HostCacheRecord None) (HostCacheRead operation-id))
  (when (and existing (or (!= existing.session-id session-id) (!= existing.expires-at expires-at)))
    (raise (ValueError "cache operation ID reused with different request")))
  (when (and existing (!= existing.state MaintenanceState.REQUESTED))
    (return (<- (cache-host-probe existing))))
  (<- row (require-headless-row session-id))
  (when (or (!= row.agent-type "claude") (is-terminal-status row.status) (is row.conversation None))
    (raise (ValueError "cache ping requires a resident Claude conversation")))
  (setv suffix (.hexdigest (hashlib.sha256 (.encode operation-id "utf-8"))))
  (<- events-path (| str None) (events-path-of-row row))
  (when (is events-path None) (raise (ValueError "resident events path is missing")))
  (setv record (or existing (HostCacheRecord operation-id session-id expires-at
                    (+ "cache-ping-" suffix) (+ events-path ".cache-" suffix))))
  (<- now int (cache-host-now))
  (when (>= now expires-at)
    (setv record (replace record :state MaintenanceState.EXPIRED :reason "expired-before-send"))
    (<- (HostCacheWrite record))
    (return record))
  (<- active (| HostCacheRecord None) (HostCacheActive session-id))
  (when (and active (!= active.operation-id operation-id))
    (setv record (replace record :state MaintenanceState.FAILED :reason "another-cache-operation-active"))
    (<- (HostCacheWrite record))
    (return record))
  (<- (HostCacheWrite record))
  ;; 枠や仕事の優先順位は一切読まない。同じ履歴への同時書込みだけを防ぐ。
  (<- alive bool (headless-has-session row.session-name))
  (when (or row.awaiting-response alive) (return record))
  (setv overlay (or row.launch-overlay {}))
  (setv params (carry-launch-flags overlay
    {"agent_type" row.agent-type "work_dir" row.work-dir
     "model" (.get overlay "model") "effort" (.get overlay "effort")
     "mcp_servers" (or (.get overlay "mcp_servers") {})
     "expected_result" row.expected-result "cache_maintenance" True}))
  (<- built (headless-launch-args params row.effective-identity row.conversation "resume"
              (str (.get (or row.backend-ref {}) "socket_path" "")) row.session-id))
  (<- effective-env (launch-spawn-env row.effective-identity
    (| (dict (or (.get overlay "session_env") {})) session-env)))
  ;; 送信前のdurable fence。ここ以降で死んだ場合は再送せずeventsを再観測する。
  (setv record (replace record :state MaintenanceState.RUNNING :started-at now))
  (<- (HostCacheWrite record))
  (<- (headless-spawn record.process-name row.work-dir effective-env
        (get built "argv") record.events-path (get built "dialogue")))
  (<- delivered bool (headless-deliver record.process-name PING-TEXT #()))
  (when (not delivered)
    (<- (headless-kill record.process-name))
    (setv record (replace record :state MaintenanceState.FAILED :reason "ping-not-delivered"))
    (<- (HostCacheWrite record)))
  record)

(defk cache-host-guard-normal-send [session-id]
  {:pre [(: session-id str)] :post [(: % "None")]}
  (<- active (| HostCacheRecord None) (HostCacheActive session-id))
  (when active
    (<- checked HostCacheRecord (cache-host-probe active))
    (when (in checked.state #(MaintenanceState.REQUESTED MaintenanceState.RUNNING))
      (raise (CacheMaintenanceActiveError "cache-maintenance-active: retry after dedicated operation"))))
  None)

(defk cache-host-cancel [session-id]
  {:pre [(: session-id str)] :post [(: % "None")]}
  (<- active (| HostCacheRecord None) (HostCacheActive session-id))
  (when active
    (<- (headless-kill active.process-name))
    (<- (HostCacheWrite (replace active :state MaintenanceState.FAILED :reason "session-cancelled"))))
  None)
