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
(import doeff [do :as _doeff-do GetHandlers GetOuterHandlers])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait Spawn])

(import doeff_agents.effects.agent [
  LaunchEffect MonitorEffect CaptureEffect
  SendEffect StopEffect SleepEffect SessionHandle Observation])
(import doeff_agents.adapters.base [AgentType LaunchParams])
(import doeff_agents.adapters.claude [ClaudeAdapter])
(import doeff_agents.mcp-server [McpToolServer])
(import doeff_agents.handlers.mcp-server-loop [mcp-server-loop])
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

(defn _write-mcp-json [work-dir server mcp-server-name]
  "Write .mcp.json for Claude Code to discover MCP tools.

   `mcp-server-name` becomes the key under mcpServers — Claude Code prefixes
   tool invocations with it (e.g. @<name>:submit-order), so callers can
   pick a namespace that matches their agent's domain."
  (setv mcp-config
    {"mcpServers"
     {mcp-server-name
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

    (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout]
      :when (= agent-type AgentType.CLAUDE)
      ;; 1. Trust
      (_trust-workdir work-dir)
      ;; 2. MCP server — tools run INSIDE the main VM as spawned tasks, not
      ;;    in a separate run() from the HTTP thread. This way the scheduler,
      ;;    sim_time clock, and state handler are shared between the pipeline
      ;;    and every tool invocation, so WaitUntil / GetTime behave correctly.
      ;;
      ;;    Capture the full handler stack at the Launch site:
      ;;      - inner: handlers below claude_handler (caught by GetHandlers(k))
      ;;      - outer: handlers above claude_handler (scheduled, state, lazy_ask),
      ;;        captured by GetOuterHandlers since claude_handler's segment is
      ;;        detached from its parent after catching.
      ;;    Tool programs are re-installed with this stack per call.
      (when mcp-tools
        (<- inner-handlers (GetHandlers k))
        (<- outer-handlers (GetOuterHandlers))
        (setv captured-handlers (+ (list inner-handlers) (list outer-handlers)))
        (setv server (McpToolServer :tools mcp-tools))
        ;; Wait for the HTTP server thread to enter its accept loop before
        ;; launching the agent, so the CLI never hits a closed SSE endpoint.
        (<- ready-ep (CreateExternalPromise))
        (.start server :ready-promise ready-ep)
        (<- _ (Wait ready-ep.future))
        (_write-mcp-json work-dir server mcp-server-name)
        ;; Spawn the dispatch loop as a scheduler child task — inherits the
        ;; parent's scheduler so sim_time / state are shared.
        (<- _ (Spawn (mcp-server-loop server captured-handlers)))
        (set! mcp-servers (| mcp-servers {session-name server})))
      ;; 3. Tmux session
      (setv session-info (.new-session backend
        (tmux.SessionConfig :session-name session-name :work-dir work-dir)))
      ;; 4. Launch command
      (setv params (LaunchParams
        :work-dir work-dir
        :prompt prompt
        :model model
        :effort effort
        :bare bare))
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
