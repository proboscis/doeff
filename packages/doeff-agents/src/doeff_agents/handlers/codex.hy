;;; codex_handler — handles LaunchEffect(CODEX) + session lifecycle effects.
;;;
;;; Mirrors claude_handler for the non-MCP Codex CLI path. MCP support is not
;;; implemented for Codex here; callers that need MCP should fail loudly instead
;;; of silently launching a session without tools.

(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [<- set!])
(import doeff [Ask])
(import doeff_agents.effects.agent [
  LaunchEffect MonitorEffect CaptureEffect
  SendEffect StopEffect SessionHandle Observation])
(import doeff_agents.adapters.base [AgentType LaunchParams])
(import doeff_agents.adapters.codex [CodexAdapter])
(import doeff_agents.session-backend [SessionBackend])
(import doeff_agents.monitor [MonitorState SessionStatus
  detect-status hash-content is-waiting-for-input])
(import doeff_agents.shell [wrap-with-shell-exports])
(import doeff_agents [tmux])

(import shlex)


(defn codex-handler [* [backend None]]
  "Codex agent handler — catches LaunchEffect(CODEX) directly."
  (setv adapter (CodexAdapter))

  (defhandler _handler
    (lazy-var sessions {})

    (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
      :when (= agent-type AgentType.CODEX)
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
      (when mcp-tools
        (raise (NotImplementedError "Codex MCP tools are not supported by codex-handler")))
      (setv session-info (.new-session active-backend
        (tmux.SessionConfig :session-name session-name :work-dir work-dir :env session-env)))
      (setv params (LaunchParams
        :work-dir work-dir
        :prompt prompt
        :model model
        :effort effort
        :bare bare))
      (setv argv (.launch-command adapter params))
      (.send-keys active-backend session-info.pane-id
        (wrap-with-shell-exports (shlex.join argv) session-env)
        :literal False)
      (setv handle (SessionHandle :session-id session-name))
      (set! sessions (| sessions {session-name
        {"handle" handle
         "pane-id" session-info.pane-id
         "agent-type" AgentType.CODEX
         "monitor" (MonitorState)
         "status" SessionStatus.BOOTING}}))
      (resume handle))

    (MonitorEffect [handle]
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
      (setv sname handle.session-id)
      (setv session-data (.get sessions sname))
      (when (is session-data None)
        (reperform effect))
      (when (not (.has-session active-backend sname))
        (resume (Observation :status SessionStatus.EXITED)))
      (setv output (.capture-pane active-backend (.get session-data "pane-id") 100))
      (setv mon (.get session-data "monitor" (MonitorState)))
      (setv skip-lines 3)
      (setv content-hash (hash-content output skip-lines))
      (setv output-changed (!= content-hash mon.output-hash))
      (setv has-prompt (is-waiting-for-input output))
      (when output-changed
        (setv mon.output-hash content-hash)
        (setv mon.last-output output))
      (setv new-status (detect-status output mon output-changed has-prompt))
      (when new-status
        (setv (get session-data "status") new-status))
      (resume (Observation
        :status (.get session-data "status" SessionStatus.RUNNING)
        :output-changed output-changed
        :output-snippet (when output (cut output -500 None)))))

    (CaptureEffect [handle lines]
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
      (setv session-data (.get sessions handle.session-id))
      (when (is session-data None)
        (reperform effect))
      (resume (.capture-pane active-backend (.get session-data "pane-id") lines)))

    (SendEffect [handle message literal enter]
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
      (setv session-data (.get sessions handle.session-id))
      (when (is session-data None)
        (reperform effect))
      (.send-keys active-backend (.get session-data "pane-id") message :literal literal :enter enter)
      (resume None))

    (StopEffect [handle]
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
      (setv session-data (.get sessions handle.session-id))
      (when (is session-data None)
        (reperform effect))
      (when (.has-session active-backend handle.session-id)
        (.kill-session active-backend handle.session-id))
      (resume None)))

  _handler)
