;;; codex_handler — handles LaunchEffect(CODEX) + session lifecycle effects.
;;;
;;; Mirrors claude_handler for the non-MCP Codex CLI path. MCP support is not
;;; implemented for Codex here; callers that need MCP should fail loudly instead
;;; of silently launching a session without tools.

(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [<- set!])
(import doeff_agents.effects.agent [
  LaunchEffect MonitorEffect CaptureEffect
  SendEffect StopEffect SleepEffect SessionHandle Observation])
(import doeff_agents.adapters.base [AgentType LaunchParams])
(import doeff_agents.adapters.codex [CodexAdapter])
(import doeff_agents.monitor [MonitorState SessionStatus
  detect-status hash-content is-waiting-for-input detect-pr-url])
(import doeff_agents [tmux])

(import shlex)
(import time)


(defn codex-handler [* [backend None]]
  "Codex agent handler — catches LaunchEffect(CODEX) directly."
  (setv backend (or backend (tmux.get-default-backend)))
  (setv adapter (CodexAdapter))

  (defhandler _handler
    (lazy-var sessions {})

    (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout]
      :when (= agent-type AgentType.CODEX)
      (when mcp-tools
        (raise (NotImplementedError "Codex MCP tools are not supported by codex-handler")))
      (setv session-info (.new-session backend
        (tmux.SessionConfig :session-name session-name :work-dir work-dir)))
      (setv params (LaunchParams
        :work-dir work-dir
        :prompt prompt
        :model model
        :effort effort
        :bare bare))
      (setv argv (.launch-command adapter params))
      (.send-keys backend session-info.pane-id (shlex.join argv) :literal False)
      (setv handle (SessionHandle
        :session-name session-name
        :pane-id session-info.pane-id
        :agent-type AgentType.CODEX
        :work-dir work-dir))
      (set! sessions (| sessions {session-name
        {"handle" handle "monitor" (MonitorState) "status" SessionStatus.BOOTING "pr-url" None}}))
      (resume handle))

    (MonitorEffect [handle]
      :when (= handle.agent-type AgentType.CODEX)
      (setv sname handle.session-name)
      (when (not (.has-session backend sname))
        (resume (Observation :status SessionStatus.EXITED)))
      (setv output (.capture-pane backend handle.pane-id 100))
      (setv session-data (.get sessions sname {}))
      (setv mon (.get session-data "monitor" (MonitorState)))
      (setv skip-lines 3)
      (setv content-hash (hash-content output skip-lines))
      (setv output-changed (!= content-hash mon.output-hash))
      (setv has-prompt (is-waiting-for-input output))
      (when output-changed
        (setv mon.output-hash content-hash)
        (setv mon.last-output output))
      (setv pr-url None)
      (when (not (.get session-data "pr-url"))
        (setv detected (detect-pr-url output))
        (when detected
          (setv (get session-data "pr-url") detected)
          (setv pr-url detected)))
      (setv new-status (detect-status output mon output-changed has-prompt))
      (when new-status
        (setv (get session-data "status") new-status))
      (resume (Observation
        :status (.get session-data "status" SessionStatus.RUNNING)
        :output-changed output-changed
        :pr-url pr-url
        :output-snippet (when output (cut output -500 None)))))

    (CaptureEffect [handle lines]
      :when (= handle.agent-type AgentType.CODEX)
      (resume (.capture-pane backend handle.pane-id lines)))

    (SendEffect [handle message literal enter]
      :when (= handle.agent-type AgentType.CODEX)
      (.send-keys backend handle.pane-id message :literal literal :enter enter)
      (resume None))

    (StopEffect [handle]
      :when (= handle.agent-type AgentType.CODEX)
      (when (.has-session backend handle.session-name)
        (.kill-session backend handle.session-name))
      (resume None))

    (SleepEffect [seconds]
      (time.sleep seconds)
      (resume None)))

  _handler)
