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
(import doeff [do :as _doeff-do Ask GetHandlers GetOuterHandlers])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait Spawn])

(import doeff_agents.effects.agent [
  LaunchEffect MonitorEffect CaptureEffect
  SendEffect StopEffect SessionHandle Observation])
(import doeff_agents.adapters.base [AgentType LaunchParams])
(import doeff_agents.adapters.claude [ClaudeAdapter])
(import doeff_agents.session-backend [SessionBackend])
(import doeff_agents.mcp-server [McpToolServer])
(import doeff_agents.handlers.mcp-server-loop [mcp-server-loop])
(import doeff_agents.monitor [MonitorState SessionStatus
  detect-status hash-content is-waiting-for-input])
(import doeff_agents.shell [wrap-with-shell-exports])
(import doeff_agents [tmux])

(import json)
(import shlex)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; Trust setup — write hasTrustDialogAccepted to ~/.claude.json
;; ---------------------------------------------------------------------------

(defn _trust-workdir [work-dir]
  "Mark work_dir as trusted in BOTH ~/.claude.json (legacy) and
   ~/.claude/.claude.json (Claude Code 2.1+ — the path the CLI actually reads).
   Without the 2.1+ path the agent hits the workspace-trust dialog and hangs
   even though `--dangerously-skip-permissions` is set."
  (setv home (Path.home))
  (setv claude-dir (/ home ".claude"))
  (.mkdir claude-dir :parents True :exist-ok True)
  (setv workdir-str (str (.resolve work-dir)))
  (for [claude-json [(/ home ".claude.json") (/ claude-dir ".claude.json")]]
    (setv data (if (.exists claude-json)
                   (json.loads (.read-text claude-json))
                   {}))
    (setv projects (.setdefault data "projects" {}))
    (setv entry (.setdefault projects workdir-str {"allowedTools" []}))
    ;; Always overwrite trust + onboarding flags. A stale entry from a previous
    ;; launch where the dialog was declined would still show the prompt
    ;; otherwise.
    (setv (get entry "hasTrustDialogAccepted") True)
    (setv (get entry "hasCompletedProjectOnboarding") True)
    (.write-text claude-json (json.dumps data)))
  ;; Ensure onboarding config exists
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
  (setv adapter (ClaudeAdapter))

  (defhandler _handler
    (lazy-var sessions {})
    (lazy-var mcp-servers {})

    (LaunchEffect [session-name agent-type work-dir prompt model mcp-tools mcp-server-name effort bare ready-timeout session-env]
      :when (= agent-type AgentType.CLAUDE)
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
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
      (setv session-info (.new-session active-backend
        (tmux.SessionConfig :session-name session-name :work-dir work-dir :env session-env)))
      ;; 4. Launch command
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
      ;; 5. Store + resume
      (setv handle (SessionHandle :session-id session-name))
      (set! sessions (| sessions {session-name
        {"handle" handle
         "pane-id" session-info.pane-id
         "agent-type" AgentType.CLAUDE
         "monitor" (MonitorState)
         "status" SessionStatus.BOOTING}}))
      (resume handle))

    (MonitorEffect [handle]
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
      (setv sname handle.session-name)
      (setv session-data (.get sessions sname))
      (when (is session-data None)
        (reperform effect))
      (when (not (.has-session active-backend sname))
        (resume (Observation :status SessionStatus.EXITED)))
      (setv output (.capture-pane active-backend (.get session-data "pane-id") 100))
      (setv mon (.get session-data "monitor" (MonitorState)))
      (setv skip-lines 5)
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
      (setv session-data (.get sessions handle.session-name))
      (when (is session-data None)
        (reperform effect))
      (resume (.capture-pane active-backend (.get session-data "pane-id") lines)))

    (SendEffect [handle message literal enter]
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
      (setv session-data (.get sessions handle.session-name))
      (when (is session-data None)
        (reperform effect))
      (.send-keys active-backend (.get session-data "pane-id") message :literal literal :enter enter)
      (resume None))

    (StopEffect [handle]
      (setv active-backend backend)
      (when (is active-backend None)
        (<- active-backend (Ask SessionBackend)))
      (setv session-data (.get sessions handle.session-name))
      (when (is session-data None)
        (reperform effect))
      (setv server (.pop mcp-servers handle.session-name None))
      (when server (.shutdown server))
      (set! mcp-servers mcp-servers)
      (when (.has-session active-backend handle.session-name)
        (.kill-session active-backend handle.session-name))
      (resume None)))

  _handler)
