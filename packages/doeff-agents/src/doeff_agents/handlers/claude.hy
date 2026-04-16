;;; claude_handler — handles LaunchEffect(CLAUDE) + session lifecycle effects.
;;;
;;; Single handler design: catches LaunchEffect directly (with :when agent_type=CLAUDE),
;;; not via a resolver indirection. This is important because GetHandlers(k) must
;;; capture the full handler stack from the program's context — if we route through
;;; a resolver that yields ClaudeLaunchEffect, GetHandlers only captures the resolver
;;; itself, not the domain handlers.
;;;
;;; State: lazy-var sessions / mcp-servers backed by doeff Get/Put.

(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [<- set!])
(import doeff [do :as _doeff-do GetHandlers WithHandler run])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_agents.effects.agent [
  LaunchEffect MonitorEffect CaptureEffect
  SendEffect StopEffect SleepEffect SessionHandle Observation])
(import doeff_agents.adapters.base [AgentType LaunchParams])
(import doeff_agents.adapters.claude [ClaudeAdapter])
(import doeff_agents.mcp-server [McpToolServer])
(import doeff_agents.monitor [MonitorState SessionStatus
  detect-status hash-content is-waiting-for-input detect-pr-url])
(import doeff_agents [tmux])

(import json)
(import shlex)
(import time)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; Trust setup — write hasTrustDialogAccepted to ~/.claude.json
;; ---------------------------------------------------------------------------

(defn _trust-workdir [work-dir]
  "Mark work_dir as trusted in ~/.claude.json."
  (setv home (Path.home))
  (setv claude-json (/ home ".claude.json"))
  (setv data (if (.exists claude-json)
                 (json.loads (.read-text claude-json))
                 {}))
  (setv projects (.setdefault data "projects" {}))
  (setv workdir-str (str (.resolve work-dir)))
  (.setdefault projects workdir-str
    {"allowedTools" []
     "hasTrustDialogAccepted" True
     "hasCompletedProjectOnboarding" True})
  (.write-text claude-json (json.dumps data))
  ;; Ensure onboarding config exists
  (setv claude-dir (/ home ".claude"))
  (.mkdir claude-dir :parents True :exist-ok True)
  (setv config-path (/ claude-dir "config.json"))
  (when (not (.exists config-path))
    (.write-text config-path (json.dumps {"hasCompletedOnboarding" True}))))


;; ---------------------------------------------------------------------------
;; MCP server helpers
;; ---------------------------------------------------------------------------

(defn _make-run-tool [handlers mcp-tools]
  "Create run_tool closure for MCP server — executes tool programs with captured handlers.
   Wraps with scheduled() so tools using WaitUntil / Spawn / CreatePromise work."
  (defn run-tool [tool arguments]
    (setv args (lfor name (.param-names tool) (.get arguments name)))
    (setv program (tool.handler #* args))
    (for [h handlers]
      (setv program (WithHandler h program)))
    (run (scheduled program)))
  run-tool)


(defn _write-mcp-json [work-dir server]
  "Write .mcp.json for Claude Code to discover MCP tools."
  (setv mcp-config
    {"mcpServers"
     {"doeff"
      {"type" "sse"
       "url" server.url}}})
  (.write-text (/ work-dir ".mcp.json") (json.dumps mcp-config :indent 2)))


;; ---------------------------------------------------------------------------
;; claude_handler factory
;; ---------------------------------------------------------------------------

(defn claude-handler [* [backend None]]
  "Claude agent handler — catches LaunchEffect(CLAUDE) directly.

   Handles: trust, MCP, tmux, onboarding, session lifecycle.
   Uses lazy-var for mutable state (sessions, mcp-servers) backed by Get/Put.

   Non-CLAUDE LaunchEffects are Passed to outer handlers (so other agent handlers
   can pick them up)."
  (setv backend (or backend (tmux.get-default-backend)))
  (setv adapter (ClaudeAdapter))

  (defhandler _handler
    (lazy-var sessions {})
    (lazy-var mcp-servers {})

    (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools ready-timeout]
      :when (= agent-type AgentType.CLAUDE)
      ;; 1. Trust
      (_trust-workdir work-dir)
      ;; 2. MCP server (capture handlers BEFORE any resolver layer)
      (when mcp-tools
        (<- captured-handlers (GetHandlers k))
        (setv run-tool (_make-run-tool captured-handlers mcp-tools))
        (setv server (McpToolServer :tools mcp-tools :run-tool run-tool))
        (.start server)
        (_write-mcp-json work-dir server)
        (set! mcp-servers (| mcp-servers {session-name server})))
      ;; 3. Tmux session
      (setv session-info (.new-session backend
        (tmux.SessionConfig :session-name session-name :work-dir work-dir)))
      ;; 4. Launch command
      (setv params (LaunchParams :work-dir work-dir :prompt prompt :model model))
      (setv argv (.launch-command adapter params))
      (.send-keys backend session-info.pane-id (shlex.join argv) :literal False)
      ;; 5. Store + resume
      (setv handle (SessionHandle
        :session-name session-name
        :pane-id session-info.pane-id
        :agent-type AgentType.CLAUDE
        :work-dir work-dir))
      (set! sessions (| sessions {session-name
        {"handle" handle "monitor" (MonitorState) "status" SessionStatus.BOOTING "pr-url" None}}))
      (resume handle))

    (MonitorEffect [handle]
      (setv sname handle.session-name)
      (when (not (.has-session backend sname))
        (resume (Observation :status SessionStatus.EXITED)))
      (setv output (.capture-pane backend handle.pane-id 100))
      (setv session-data (.get sessions sname {}))
      (setv mon (.get session-data "monitor" (MonitorState)))
      (setv skip-lines 5)
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
      (resume (.capture-pane backend handle.pane-id lines)))

    (SendEffect [handle message literal enter]
      (.send-keys backend handle.pane-id message :literal literal :enter enter)
      (resume None))

    (StopEffect [handle]
      (setv server (.pop mcp-servers handle.session-name None))
      (when server (.shutdown server))
      (set! mcp-servers mcp-servers)
      (when (.has-session backend handle.session-name)
        (.kill-session backend handle.session-name))
      (resume None))

    (SleepEffect [seconds]
      (time.sleep seconds)
      (resume None)))

  _handler)
