;;; Effectful doeff-agent handlers.
;;;
;;; The handler boundary is Hy `defhandler`. The parts every agent handler answers the same way — the in-VM MCP
;;; server a launch carries and the AwaitResult loop over a transport looked at once at a time — are the programs
;;; `_serve-mcp-tools` and `_await-result-by-polling`, which the tmux, mock and scenario handlers share. The
;;; production tmux handler `tmux-agent-defhandler` answers each effect with one function of
;;; doeff_agents._handlers.production over the tmux world it builds on its first effect.

(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [<- defk val var])

(import time threading)
(import functools [partial])
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
(import doeff_agents._runtime [ClaudeRuntimePolicy CodexRuntimePolicy])
(import doeff_agents._handlers.production [
  _TmuxAgentState
  _attach-session
  _await-result
  _cancel-session
  _capture
  _claude-launch
  _cleanup-session
  _create-result-sink
  _discard-result-sink
  _follow-up
  _get-session
  _launch
  _launch-session
  _list-sessions
  _monitor
  _observe-session
  _release-session
  _run-agent
  _send
  _stop
  _stop-session
  _tmux-agent-state
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


(defk _tmux-state [tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy]
  {:pre [(: tmux-ref dict) (: backend "SessionBackend | None") (: session-repository "AgentSessionRepository | None")
         (: claude-runtime-policy (| ClaudeRuntimePolicy None)) (: codex-runtime-policy (| CodexRuntimePolicy None))]
   :post [(: % _TmuxAgentState)]
   :tags {:context "agent-handler" :role "foundation"}}
  "The tmux world the handler answers from (kept in `tmux-ref`), built on its first effect over the handler's
   backend (a SessionBackend; None reads one through Ask), session repository and runtime policies."
  (when (not-in "state" tmux-ref)
    (var active-backend backend)
    (when (is active-backend None)
      (<- asked (Ask SessionBackend))
      (:= active-backend asked))
    (setv (get tmux-ref "state")
          (_tmux-agent-state :backend active-backend
                             :session-repository session-repository
                             :claude-runtime-policy claude-runtime-policy
                             :codex-runtime-policy codex-runtime-policy)))
  (get tmux-ref "state"))


(defhandler tmux-agent-defhandler [* [session-repository None] [backend None]
                                   [claude-runtime-policy None]
                                   [codex-runtime-policy None]]
  "Create the production tmux-backed agent defhandler.

  Every clause answers its effect from one tmux world (the backend, the runtime policies, the session
  repository and the sessions it opened), built when the first handled effect arrives, with one function of
  doeff_agents._handlers.production; the clauses that start or stop a session's in-VM MCP server do that here.
  If backend is omitted, it is read through Ask(SessionBackend) when the first
  handled effect arrives.
  "
  (session val tmux-ref {})

  (AgentEffect [task]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_run-agent tmux effect)))

  (LaunchSessionEffect [spec]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    ;; ADR-DOE-AGENTS-005 R1: every schema session gets the report_result
    ;; data channel — start the in-VM server even when the spec carries no
    ;; domain tools. Terminal bytes are never a result transport.
    (val report-transport? (spec-uses-report-result-transport spec))
    (if (or (bool spec.mcp-tools) report-transport?)
        (do
          (var server-tools (tuple spec.mcp-tools))
          (when report-transport?
            (val sink (_create-result-sink tmux spec.session-id))
            (:= server-tools (+ server-tools #((make-report-result-tool sink spec.result-schema)))))
          (<- server (_serve-mcp-tools k server-tools))
          (setv (get tmux.mcp-servers spec.session-id) server)
          (var handle None)
          (try
            (:= handle (_launch-session tmux effect :mcp-servers {spec.mcp-server-name server.url}))
            (except [e Exception]
              (<- (_stop-mcp-server tmux.mcp-servers spec.session-id))
              (_discard-result-sink tmux spec.session-id)
              (raise e)))
          (resume handle))
        (resume (_launch-session tmux effect))))

  (AwaitResultEffect [handle timeout-seconds]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (<- outcome (_await-result-by-polling (partial _await-result tmux) handle timeout-seconds))
    (resume outcome))

  (FollowUpEffect []
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_follow-up tmux effect)))

  ;; 入力の前に runtime を事前起動する WarmSessionEffect は、tmux の会話では LaunchEffect が既に CLI を起動して入力を待たせている —
  ;; 黙って何もせずに応答せず、型で拒否する。
  (WarmSessionEffect [handle]
    (raise (AgentCapabilityUnsupportedError :capability "WarmSessionEffect" :handler "tmux-agent-defhandler")))

  (StopSessionEffect [handle]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (_stop-session tmux effect)
    (<- (_stop-mcp-server tmux.mcp-servers handle.session-id))
    (resume None))

  (ReleaseSessionEffect [handle]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (_release-session tmux effect)
    (<- (_stop-mcp-server tmux.mcp-servers handle.session-id))
    (resume None))

  (LaunchEffect [session-name mcp-tools mcp-server-name]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (if mcp-tools
        (do
          (<- server (_serve-mcp-tools k (tuple mcp-tools)))
          (setv (get tmux.mcp-servers session-name) server)
          (var handle None)
          (try
            (:= handle (_launch tmux effect :mcp-servers {mcp-server-name server.url}))
            (except [e Exception]
              (<- (_stop-mcp-server tmux.mcp-servers session-name))
              (raise e)))
          (resume handle))
        (resume (_launch tmux effect))))

  (ClaudeLaunchEffect []
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_claude-launch tmux effect)))

  (MonitorEffect [handle]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_monitor tmux effect)))

  (CaptureEffect [handle]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_capture tmux effect)))

  (SendEffect [handle]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (_send tmux effect)
    (resume None))

  (StopEffect [handle]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (_stop tmux effect)
    (<- (_stop-mcp-server tmux.mcp-servers handle.session-id))
    (resume None))

  (GetAgentSessionEffect [session-id]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_get-session tmux effect)))

  (ListAgentSessionsEffect [query]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_list-sessions tmux effect)))

  (ObserveAgentSessionEffect [session-id]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_observe-session tmux effect)))

  (AttachAgentSessionEffect [session-id]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (_attach-session tmux effect)
    (resume None))

  (CancelAgentSessionEffect [session-id]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (resume (_cancel-session tmux effect)))

  (CleanupAgentSessionEffect [session-id]
    (<- tmux (_tmux-state tmux-ref backend session-repository claude-runtime-policy codex-runtime-policy))
    (<- (_stop-mcp-server tmux.mcp-servers session-id))
    (resume (_cleanup-session tmux effect))))
