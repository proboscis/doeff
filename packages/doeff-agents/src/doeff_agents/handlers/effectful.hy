;;; Effectful doeff-agent handlers.
;;;
;;; The handler boundary is Hy `defhandler`; backend-specific execution stays in
;;; AgentHandler implementations.

(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [<-])

(import doeff [Ask GetHandlers handler :as _program-handler run])
(import doeff_core_effects.handlers [state])
(import doeff_agents.effects [
  AgentEffect
  AttachAgentSessionEffect
  AwaitResultEffect
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


(defn _make-run-tool [handlers]
  (defn run-tool [tool arguments]
    (setv args (lfor name (.param-names tool) (.get arguments name)))
    (setv program (tool.handler #* args))
    (for [h handlers]
      (setv program ((_program-handler h) program)))
    ;; Captured handlers can own lazy-val/lazy-var state. Their own effects flow
    ;; outside the handler, while LaunchSession's outer state handler is not
    ;; reliably part of GetHandlers(k), so the callback VM must provide one.
    (setv program ((_program-handler (state)) program))
    (run program))
  run-tool)


(defn _cached-tmux-handler [handler-ref active-backend session-repository
                            claude-runtime-policy]
  (setv handler (.get handler-ref "handler"))
  (when (is handler None)
    (setv handler
      (TmuxAgentHandler
        :backend active-backend
        :session-repository session-repository
        :claude-runtime-policy claude-runtime-policy))
    (setv (get handler-ref "handler") handler))
  handler)


(defhandler agent-handler-defhandler [agent-handler]
  "Define the Hy defhandler boundary for an AgentHandler object."
  (AgentEffect [task]
    (resume (.handle-agent agent-handler effect)))

  (LaunchSessionEffect [spec]
    (if spec.mcp-tools
        (do
          (<- handlers (GetHandlers k))
          (resume (.handle-launch-session agent-handler effect
                    :run-tool (_make-run-tool handlers))))
        (resume (.handle-launch-session agent-handler effect :run-tool None))))

  (AwaitResultEffect [handle timeout-seconds]
    (resume (.handle-await-result agent-handler effect)))

  (FollowUpEffect [handle message]
    (resume (.handle-follow-up agent-handler effect)))

  (StopSessionEffect [handle reason]
    (.handle-stop-session agent-handler effect)
    (resume None))

  (ReleaseSessionEffect [handle]
    (.handle-release-session agent-handler effect)
    (resume None))

  (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
    (if mcp-tools
        (do
          (<- handlers (GetHandlers k))
          (resume (.handle-launch agent-handler effect
                    :run-tool (_make-run-tool handlers))))
        (resume (.handle-launch agent-handler effect))))

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
    (resume (.handle-cleanup-session agent-handler effect))))


(defhandler tmux-agent-defhandler [* [session-repository None] [backend None]
                                   [claude-runtime-policy None]]
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
                            claude-runtime-policy))
    (resume (.handle-agent agent-handler effect)))

  (LaunchSessionEffect [spec]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (if spec.mcp-tools
        (do
          (<- handlers (GetHandlers k))
          (resume (.handle-launch-session agent-handler effect
                    :run-tool (_make-run-tool handlers))))
        (resume (.handle-launch-session agent-handler effect :run-tool None))))

  (AwaitResultEffect [handle timeout-seconds]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-await-result agent-handler effect)))

  (FollowUpEffect [handle message]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-follow-up agent-handler effect)))

  (StopSessionEffect [handle reason]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (.handle-stop-session agent-handler effect)
    (resume None))

  (ReleaseSessionEffect [handle]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (.handle-release-session agent-handler effect)
    (resume None))

  (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (if mcp-tools
        (do
          (<- handlers (GetHandlers k))
          (resume (.handle-launch agent-handler effect
                    :run-tool (_make-run-tool handlers))))
        (resume (.handle-launch agent-handler effect))))

  (ClaudeLaunchEffect [session-name work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-claude-launch agent-handler effect)))

  (MonitorEffect [handle]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-monitor agent-handler effect)))

  (CaptureEffect [handle lines]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-capture agent-handler effect)))

  (SendEffect [handle message enter literal]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (.handle-send agent-handler effect)
    (resume None))

  (StopEffect [handle]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (.handle-stop agent-handler effect)
    (resume None))

  (GetAgentSessionEffect [session-id]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-get-session agent-handler effect)))

  (ListAgentSessionsEffect [query]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-list-sessions agent-handler effect)))

  (ObserveAgentSessionEffect [session-id lines]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-observe-session agent-handler effect)))

  (AttachAgentSessionEffect [session-id]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (.handle-attach-session agent-handler effect)
    (resume None))

  (CancelAgentSessionEffect [session-id]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-cancel-session agent-handler effect)))

  (CleanupAgentSessionEffect [session-id]
    (setv active-backend backend)
    (when (is active-backend None)
      (<- active-backend (Ask SessionBackend)))
    (setv agent-handler
      (_cached-tmux-handler handler-ref active-backend session-repository
                            claude-runtime-policy))
    (resume (.handle-cleanup-session agent-handler effect))))
