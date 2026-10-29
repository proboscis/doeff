;;; Effectful doeff-agent handlers.
;;;
;;; The handler boundary is Hy `defhandler`. The parts every agent handler answers the same way — the in-VM MCP
;;; server a launch carries and the AwaitResult loop over a transport looked at once at a time — are the programs
;;; `_serve-mcp-tools` and `_await-result-by-polling`, which the mock, scenario and daemon handlers share.

(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [<- defk val var])

(import time threading)
(import collections.abc [Callable])
(import doeff [Ask GetHandlers GetOuterHandlers K])
(import doeff_core_effects [CreateExternalPromise Wait Spawn PRIORITY_IDLE])
(import doeff_agents._agentd-client [DEFAULT_AWAIT_BUDGET_SECONDS])
(import doeff_agents._effects [
  AgentCapabilityUnsupportedError
  AgentEffect
  AttachAgentSessionEffect
  AwaitResultEffect
  AwaitStatus
  CancelAgentSessionEffect
  CaptureEffect
  CleanupAgentSessionEffect
  FollowUpEffect
  GetAgentSessionEffect
  LaunchEffect
  LaunchSessionEffect
  ListAgentSessionsEffect
  MonitorEffect
  ObserveAgentSessionEffect
  ReleaseSessionEffect
  SendEffect
  StopEffect
  StopSessionEffect
  WarmSessionEffect])
(import doeff_agents._effects [AwaitOutcome :as _AwaitOutcome SessionHandle :as _SessionHandle])
(import doeff_agents._effects._handler-effects [ClaudeLaunchEffect])
(import doeff_agents._session-backend [SessionBackend])
(import doeff_agents._handlers.production [
  TmuxAgentHandler
  make-report-result-tool
  spec-uses-report-result-transport])
(import doeff_agents._mcp-server [McpToolServer])
(import doeff_agents._handlers.mcp-server-loop [mcp-server-loop])


(setv AWAIT-RESULT-POLL-INTERVAL-SECONDS 0.2)
(setv AWAIT-RESULT-AWAITING-INPUT-STABILITY-SECONDS 15.0)


(defk _serve-mcp-tools [continuation tools]
  {:pre [(: continuation K) (: tools tuple)] :post [(: % McpToolServer)]
   :tags {:context "agent-handler" :role "foundation"}}
  "Start the in-VM MCP server a launch carries and give it back once it listens. Its tools run under the
   handlers around the launching program (read from the launch's continuation) and the handlers outside the
   answering handler, so a tool's effects reach the program's domain handlers."
  (<- inner-handlers (GetHandlers continuation))
  (<- outer-handlers (GetOuterHandlers))
  (val server (McpToolServer :tools tools))
  (<- ready-ep (CreateExternalPromise))
  (.start server :ready-promise ready-ep)
  (<- (Wait ready-ep.future))
  (<- (Spawn (mcp-server-loop server (+ (list inner-handlers) (list outer-handlers))) :daemon True))
  server)


(defk _stop-mcp-server [servers session-id]
  {:pre [(: servers dict) (: session-id str)] :post [(: % (type None))]
   :tags {:context "agent-handler" :role "foundation"}}
  "A session that stops, is released or is cleaned up no longer needs its in-VM MCP server: shut down the one
   `servers` (session id → server) keeps for it, if any."
  (val server (.pop servers session-id None))
  (when (is-not server None)
    (.shutdown server))
  None)


(defk _await-result-by-polling [await-once handle timeout-seconds]
  {:pre [(: await-once Callable) (: handle _SessionHandle) (: timeout-seconds (| int float None))]
   :post [(: % _AwaitOutcome)]
   :tags {:context "agent-handler" :role "protocol"}}
  "AwaitResult over a transport looked at once at a time: `await-once` answers an AwaitResultEffect with a zero
   timeout (a look that does not wait). Between looks the program waits on the scheduler at idle priority, so
   other tasks run. It stops at a result, at a final outcome, when AWAITING_INPUT has held for the stability
   window (or is not continuable), or when the budget (`timeout-seconds`, the transport heartbeat when None) runs out."
  (val deadline (+ (time.monotonic)
                   (if (is timeout-seconds None) DEFAULT_AWAIT_BUDGET_SECONDS timeout-seconds)))
  (var awaiting-input-deadline None)
  (var outcome None)
  (while True
    (:= outcome (await-once (AwaitResultEffect :handle handle :timeout-seconds 0.0)))
    (when (and (is-not outcome.result None)
               (is outcome.validation-error None))
      (break))
    (cond
      (= outcome.status AwaitStatus.TIMED_OUT)
      (:= awaiting-input-deadline None)

      (= outcome.status AwaitStatus.AWAITING_INPUT)
      (do
        (when (is awaiting-input-deadline None)
          (:= awaiting-input-deadline
              (+ (time.monotonic) AWAIT-RESULT-AWAITING-INPUT-STABILITY-SECONDS)))
        (when (or (not (getattr outcome "continuable" True))
                  (>= (time.monotonic) awaiting-input-deadline))
          (break)))

      True
      (break))
    (when (>= (time.monotonic) deadline)
      (break))
    (val delay-seconds
         (max 0.0 (min AWAIT-RESULT-POLL-INTERVAL-SECONDS (- deadline (time.monotonic)))))
    (when (> delay-seconds 0.0)
      (<- delay-ep (CreateExternalPromise))
      (.start (threading.Timer delay-seconds delay-ep.complete [None]))
      (<- (Wait delay-ep.future :priority PRIORITY_IDLE))))
  outcome)


(defn _mcp-server-map [agent-handler]
  (when (not (hasattr agent-handler "_doeff_mcp_servers"))
    (setattr agent-handler "_doeff_mcp_servers" {}))
  (getattr agent-handler "_doeff_mcp_servers"))


(defn _remember-mcp-server [agent-handler session-id server]
  (setv servers (_mcp-server-map agent-handler))
  (setv (get servers session-id) server)
  None)


(defn _shutdown-mcp-server-for-session [agent-handler session-id]
  (when (hasattr agent-handler "_doeff_mcp_servers")
    (setv servers (getattr agent-handler "_doeff_mcp_servers"))
    (setv server (.pop servers session-id None))
    (when (is-not server None)
      (.shutdown server)))
  None)


(defn _cached-tmux-handler [handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy]
  (setv handler (.get handler-ref "handler"))
  (when (is handler None)
    (setv handler
      (TmuxAgentHandler
        :backend active-backend
        :session-repository session-repository
        :claude-runtime-policy claude-runtime-policy
        :codex-runtime-policy codex-runtime-policy))
    (setv (get handler-ref "handler") handler))
  handler)


(defhandler agent-handler-defhandler [agent-handler]
  "Define the Hy defhandler boundary for an AgentHandler object."

  (AgentEffect [task]
    (resume (.handle-agent agent-handler effect)))

  (LaunchSessionEffect [spec]
    (setv mcp-server-urls None)
    ;; ADR-DOE-AGENTS-005 R1: every schema session gets the report_result
    ;; data channel — start the in-VM server even when the spec carries no
    ;; domain tools. Terminal bytes are never a result transport.
    (setv report-transport?
          (and agent-handler.supports-inprocess-report-result
               (spec-uses-report-result-transport spec)))
    (setv wants-server? (or (bool spec.mcp-tools) report-transport?))
    (when wants-server?
      (setv server-tools (tuple spec.mcp-tools))
      (when report-transport?
        (setv sink (.create-result-sink agent-handler spec.session-id))
        (setv server-tools
              (+ server-tools
                 #((make-report-result-tool sink spec.result-schema)))))
      (<- server (_serve-mcp-tools k server-tools))
      (_remember-mcp-server agent-handler spec.session-id server)
      (setv mcp-server-urls {spec.mcp-server-name server.url}))
    (setv launch-result None)
    (try
      (setv launch-result
        (.handle-launch-session agent-handler effect :mcp-servers mcp-server-urls))
      (except [e Exception]
        (when wants-server?
          (_shutdown-mcp-server-for-session agent-handler spec.session-id))
        (when agent-handler.supports-inprocess-report-result
          (.discard-result-sink agent-handler spec.session-id))
        (raise e)))
    (resume launch-result))

  (AwaitResultEffect [handle timeout-seconds]
    (<- outcome (_await-result-by-polling agent-handler.handle-await-result handle timeout-seconds))
    (resume outcome))

  (FollowUpEffect [handle message]
    (resume (.handle-follow-up agent-handler effect)))

  ;; 入力の前に runtime を事前起動する WarmSessionEffect は、AgentHandler の会話では LaunchEffect が既に CLI を起動して入力を待たせて
  ;; いる — 黙って何もせずに応答せず、型で拒否する。
  (WarmSessionEffect [handle]
    (raise (AgentCapabilityUnsupportedError :capability "WarmSessionEffect" :handler "agent-handler-defhandler")))

  (StopSessionEffect [handle reason]
    (.handle-stop-session agent-handler effect)
    (_shutdown-mcp-server-for-session agent-handler handle.session-id)
    (resume None))

  (ReleaseSessionEffect [handle]
    (.handle-release-session agent-handler effect)
    (_shutdown-mcp-server-for-session agent-handler handle.session-id)
    (resume None))

  (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
    (setv mcp-server-urls None)
    (when mcp-tools
      (<- server (_serve-mcp-tools k (tuple mcp-tools)))
      (_remember-mcp-server agent-handler session-name server)
      (setv mcp-server-urls {mcp-server-name server.url}))
    (setv launch-result None)
    (try
      (setv launch-result
        (.handle-launch agent-handler effect :mcp-servers mcp-server-urls))
      (except [e Exception]
        (when mcp-tools
          (_shutdown-mcp-server-for-session agent-handler session-name))
        (raise e)))
    (resume launch-result))

  (ClaudeLaunchEffect [session-name work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
    (resume (.handle-claude-launch agent-handler effect)))

  (MonitorEffect [handle]
    (resume (.handle-monitor agent-handler effect)))

  (CaptureEffect [handle lines]
    (resume (.handle-capture agent-handler effect)))

  (SendEffect [handle message enter literal]
    (.handle-send agent-handler effect)
    (resume None))

  (StopEffect [handle]
    (.handle-stop agent-handler effect)
    (_shutdown-mcp-server-for-session agent-handler handle.session-id)
    (resume None))

  (GetAgentSessionEffect [session-id]
    (resume (.handle-get-session agent-handler effect)))

  (ListAgentSessionsEffect [query]
    (resume (.handle-list-sessions agent-handler effect)))

  (ObserveAgentSessionEffect [session-id lines]
    (resume (.handle-observe-session agent-handler effect)))

  (AttachAgentSessionEffect [session-id]
    (.handle-attach-session agent-handler effect)
    (resume None))

  (CancelAgentSessionEffect [session-id]
    (resume (.handle-cancel-session agent-handler effect)))

  (CleanupAgentSessionEffect [session-id]
    (_shutdown-mcp-server-for-session agent-handler session-id)
    (resume (.handle-cleanup-session agent-handler effect))))


(defhandler tmux-agent-defhandler [* [session-repository None] [backend None]
                                   [claude-runtime-policy None]
                                   [codex-runtime-policy None]]
  "Create the production tmux-backed agent defhandler.

  If backend is omitted, it is read through Ask(SessionBackend) when the first
  handled effect arrives.
  "
  (lazy-var handler-ref {})
  (AgentEffect [task]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-agent agent-handler effect)))

  (LaunchSessionEffect [spec]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    ;; ADR-DOE-AGENTS-005 R1 — same registration as the
    ;; agent-handler-defhandler branch above: every schema session gets the
    ;; report_result data channel, with or without domain tools.
    (setv report-transport?
          (and agent-handler.supports-inprocess-report-result
               (spec-uses-report-result-transport spec)))
    (if (or (bool spec.mcp-tools) report-transport?)
        (do
          (<- inner-handlers (GetHandlers k))
          (<- outer-handlers (GetOuterHandlers))
          (setv captured-handlers (+ (list inner-handlers) (list outer-handlers)))
          (setv server-tools (tuple spec.mcp-tools))
          (when report-transport?
            (setv sink (.create-result-sink agent-handler spec.session-id))
            (setv server-tools
                  (+ server-tools
                     #((make-report-result-tool sink spec.result-schema)))))
          (setv server (McpToolServer :tools server-tools))
          (<- ready-ep (CreateExternalPromise))
          (.start server :ready-promise ready-ep)
          (<- _ (Wait ready-ep.future))
          (<- _ (Spawn (mcp-server-loop server captured-handlers) :daemon True))
          (_remember-mcp-server agent-handler spec.session-id server)
          (setv launch-result None)
          (try
            (setv launch-result
              (.handle-launch-session agent-handler effect
                :mcp-servers {spec.mcp-server-name server.url}))
            (except [e Exception]
              (_shutdown-mcp-server-for-session agent-handler spec.session-id)
              (.discard-result-sink agent-handler spec.session-id)
              (raise e)))
          (resume launch-result))
        (resume (.handle-launch-session agent-handler effect :mcp-servers None))))

  (AwaitResultEffect [handle timeout-seconds]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (setv effective-timeout
      (if (is timeout-seconds None) DEFAULT_AWAIT_BUDGET_SECONDS timeout-seconds))
    (setv deadline (+ (time.monotonic) effective-timeout))
    (setv awaiting-input-deadline None)
    (setv outcome None)
    (while True
      (setv outcome
        (.handle-await-result agent-handler
          (AwaitResultEffect :handle handle :timeout-seconds 0.0)))
      (when (and (is-not outcome.result None)
                 (is outcome.validation-error None))
        (break))
      (cond
        (= outcome.status AwaitStatus.TIMED_OUT)
        (setv awaiting-input-deadline None)

        (= outcome.status AwaitStatus.AWAITING_INPUT)
        (do
          (when (is awaiting-input-deadline None)
            (setv awaiting-input-deadline
                  (+ (time.monotonic)
                     AWAIT-RESULT-AWAITING-INPUT-STABILITY-SECONDS)))
          (when (or (not (getattr outcome "continuable" True))
                    (>= (time.monotonic) awaiting-input-deadline))
            (break)))

        True
        (break))
      (when (>= (time.monotonic) deadline)
        (break))
      (setv delay-seconds
            (max 0.0
                 (min AWAIT-RESULT-POLL-INTERVAL-SECONDS
                      (- deadline (time.monotonic)))))
      (when (> delay-seconds 0.0)
        (<- delay-ep (CreateExternalPromise))
        (.start (threading.Timer delay-seconds delay-ep.complete [None]))
        (<- _ (Wait delay-ep.future :priority PRIORITY_IDLE))))
    (resume outcome))

  (FollowUpEffect [handle message]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-follow-up agent-handler effect)))

  ;; 入力の前に runtime を事前起動する WarmSessionEffect は、tmux の会話では LaunchEffect が既に CLI を起動して入力を待たせている —
  ;; 黙って何もせずに応答せず、型で拒否する。
  (WarmSessionEffect [handle]
    (raise (AgentCapabilityUnsupportedError :capability "WarmSessionEffect" :handler "tmux-agent-defhandler")))

  (StopSessionEffect [handle reason]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (.handle-stop-session agent-handler effect)
    (_shutdown-mcp-server-for-session agent-handler handle.session-id)
    (resume None))

  (ReleaseSessionEffect [handle]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (.handle-release-session agent-handler effect)
    (_shutdown-mcp-server-for-session agent-handler handle.session-id)
    (resume None))

  (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (if mcp-tools
        (do
          (<- inner-handlers (GetHandlers k))
          (<- outer-handlers (GetOuterHandlers))
          (setv captured-handlers (+ (list inner-handlers) (list outer-handlers)))
          (setv server (McpToolServer :tools mcp-tools))
          (<- ready-ep (CreateExternalPromise))
          (.start server :ready-promise ready-ep)
          (<- _ (Wait ready-ep.future))
          (<- _ (Spawn (mcp-server-loop server captured-handlers) :daemon True))
          (_remember-mcp-server agent-handler session-name server)
          (setv launch-result None)
          (try
            (setv launch-result
              (.handle-launch agent-handler effect
                :mcp-servers {mcp-server-name server.url}))
            (except [e Exception]
              (_shutdown-mcp-server-for-session agent-handler session-name)
              (raise e)))
          (resume launch-result))
        (resume (.handle-launch agent-handler effect))))

  (ClaudeLaunchEffect [session-name work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-claude-launch agent-handler effect)))

  (MonitorEffect [handle]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-monitor agent-handler effect)))

  (CaptureEffect [handle lines]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-capture agent-handler effect)))

  (SendEffect [handle message enter literal]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (.handle-send agent-handler effect)
    (resume None))

  (StopEffect [handle]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (.handle-stop agent-handler effect)
    (_shutdown-mcp-server-for-session agent-handler handle.session-id)
    (resume None))

  (GetAgentSessionEffect [session-id]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-get-session agent-handler effect)))

  (ListAgentSessionsEffect [query]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-list-sessions agent-handler effect)))

  (ObserveAgentSessionEffect [session-id lines]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-observe-session agent-handler effect)))

  (AttachAgentSessionEffect [session-id]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (.handle-attach-session agent-handler effect)
    (resume None))

  (CancelAgentSessionEffect [session-id]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (resume (.handle-cancel-session agent-handler effect)))

  (CleanupAgentSessionEffect [session-id]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy codex-runtime-policy))
    (_shutdown-mcp-server-for-session agent-handler session-id)
    (resume (.handle-cleanup-session agent-handler effect))))
