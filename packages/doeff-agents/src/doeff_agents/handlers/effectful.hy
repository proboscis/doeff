;;; Effectful doeff-agent handlers.
;;;
;;; The handler boundary is Hy `defhandler`; backend-specific execution stays in
;;; AgentHandler implementations.

(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [<-])

(import time threading)
(import doeff [Ask GetHandlers GetOuterHandlers])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait Spawn PRIORITY_IDLE])
(import doeff_agents.agentd-client [DEFAULT_AWAIT_BUDGET_SECONDS])
(import doeff_agents.effects [
  AgentEffect
  AttachAgentSessionEffect
  AwaitResultEffect
  AwaitStatus
  CancelAgentSessionEffect
  CaptureEffect
  ClaudeLaunchEffect
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
  StopSessionEffect])
(import doeff_agents.session-backend [SessionBackend])
(import doeff_agents.handlers.production [TmuxAgentHandler])
(import doeff_agents.mcp-server [McpToolServer])
(import doeff_agents.handlers.mcp-server-loop [mcp-server-loop])


(setv AWAIT-RESULT-POLL-INTERVAL-SECONDS 0.2)
(setv AWAIT-RESULT-AWAITING-INPUT-STABILITY-SECONDS 15.0)


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
    (when spec.mcp-tools
      (<- inner-handlers (GetHandlers k))
      (<- outer-handlers (GetOuterHandlers))
      (setv captured-handlers (+ (list inner-handlers) (list outer-handlers)))
      (setv server (McpToolServer :tools spec.mcp-tools))
      (<- ready-ep (CreateExternalPromise))
      (.start server :ready-promise ready-ep)
      (<- _ (Wait ready-ep.future))
      (<- _ (Spawn (mcp-server-loop server captured-handlers) :daemon True))
      (_remember-mcp-server agent-handler spec.session-id server)
      (setv mcp-server-urls {spec.mcp-server-name server.url}))
    (setv launch-result None)
    (try
      (setv launch-result
        (.handle-launch-session agent-handler effect :mcp-servers mcp-server-urls))
      (except [e Exception]
        (when spec.mcp-tools
          (_shutdown-mcp-server-for-session agent-handler spec.session-id))
        (raise e)))
    (resume launch-result))

  (AwaitResultEffect [handle timeout-seconds]
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
    (resume (.handle-follow-up agent-handler effect)))

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
      (<- inner-handlers (GetHandlers k))
      (<- outer-handlers (GetOuterHandlers))
      (setv captured-handlers (+ (list inner-handlers) (list outer-handlers)))
      (setv server (McpToolServer :tools mcp-tools))
      (<- ready-ep (CreateExternalPromise))
      (.start server :ready-promise ready-ep)
      (<- _ (Wait ready-ep.future))
      (<- _ (Spawn (mcp-server-loop server captured-handlers) :daemon True))
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
    (if spec.mcp-tools
        (do
          (<- inner-handlers (GetHandlers k))
          (<- outer-handlers (GetOuterHandlers))
          (setv captured-handlers (+ (list inner-handlers) (list outer-handlers)))
          (setv server (McpToolServer :tools spec.mcp-tools))
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
