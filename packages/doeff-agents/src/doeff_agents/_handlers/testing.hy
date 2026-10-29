"""Testing handlers for deterministic agent tests: the mock terminal handler and the scenario handler.

Both are doeff handlers (defhandler) over a state value the factory builds or the caller keeps: the mock answers
the terminal-session effects from a MockAgentState; the scenario handler answers the schema-result (L2) effects
from a ScenarioWorld, stacked outside a mock handler over the world's terminal sessions."""

(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [<- defk val var])

(import dataclasses [dataclass field])
(import datetime [datetime timezone])
(import functools [partial])
(import json)
(import pathlib [Path PurePath])
(import time)

(import doeff_agents._adapters.base [AgentType])
(import doeff_agents._effects._handler-effects [ClaudeLaunchEffect refuse-turn-capabilities])
(import doeff_agents._effects [AgentCapabilityUnsupportedError :as _AgentCapabilityUnsupportedError])
(import doeff_agents._effects [
  AgentEffect
  AgentSessionLifecycle
  AgentSessionSnapshot
  AttachAgentSessionEffect
  AwaitOutcome
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
  Observation
  ObserveAgentSessionEffect
  ReleaseSessionEffect
  SendEffect
  SessionAlreadyExistsError
  SessionHandle
  SessionNotFoundError
  StopEffect
  StopSessionEffect
  WarmSessionEffect])
(import doeff_agents._effects.agent [SessionStatus])
(import doeff_agents._handlers.effectful [_await-result-by-polling _serve-mcp-tools _stop-mcp-server])
(import doeff_agents._handlers.production [_run-agent-task])
(import doeff_agents._session-store [
  AgentSessionRepository
  InMemoryAgentSessionRepository])


(defclass MockSessionScript []
  "Script for deterministic session observations.

   session_name names the session the mock handler answers with this script
   (mock_agent_handlers(sessions=…) reads it); initial_output is the screen text
   the session shows before its first observation."
  (defn __init__ [self * [session-name None] [observations None] [initial-output ""] [_index 0]]
    (setv self.session-name session-name)
    (setv self.observations (if (is observations None) [] (list observations)))
    (setv self.initial-output initial-output)
    (setv self._index _index))

  (defn next-observation [self]
    (if (>= self._index (len self.observations))
        #(SessionStatus.DONE "")
        (do
          (setv obs (get self.observations self._index))
          (setv self._index (+ self._index 1))
          obs))))


(defclass [dataclass] MockAgentState []
  "The mock handler's sessions: what each launched session shows and what it was sent.

   One value per handler — mock_agent_handler builds a fresh one from its scripts; a ScenarioWorld keeps one,
   so a session LaunchSession opened answers Monitor and Capture too. scripts maps a session name to its
   MockSessionScript, whose observations Monitor consumes in order; mcp_servers holds the in-VM MCP server
   each launch with tools carries."
  (setv #^ dict scripts (field :default-factory dict))
  (setv #^ dict handles (field :default-factory dict))
  (setv #^ dict statuses (field :default-factory dict))
  (setv #^ dict outputs (field :default-factory dict))
  (setv #^ dict agent-types (field :default-factory dict))
  (setv #^ dict work-dirs (field :default-factory dict))
  (setv #^ dict lifecycles (field :default-factory dict))
  (setv #^ list sends (field :default-factory list))
  (setv #^ dict mcp-servers (field :default-factory dict))
  (setv #^ AgentSessionRepository session-repository
        (field :default-factory InMemoryAgentSessionRepository))

  (defn __post_init__ [self]
    ;; A scripted session shows its initial output and is BOOTING before its first observation.
    (for [#(session-name script) (.items self.scripts)]
      (.setdefault self.outputs session-name script.initial-output)
      (.setdefault self.statuses session-name SessionStatus.BOOTING))))


(defk _record-snapshot [state event-type handle status output-snippet]
  {:pre [(: state MockAgentState) (: event-type str) (: handle SessionHandle) (: status SessionStatus)
         (: output-snippet (| str None))]
   :post [(: % (type None))]
   :tags {:context "mock-agent" :role "foundation"}}
  "Record the session's state in the mock's session repository, so the session-id effects read what the
   terminal effects did (finished_at is set once, at the first final status; a None snippet keeps the last one)."
  (val now (datetime.now timezone.utc))
  (val previous (.get-session state.session-repository handle.session-id))
  (val previous-finished-at (if (is-not previous None) previous.finished-at None))
  (val final? (in status #(SessionStatus.DONE SessionStatus.FAILED SessionStatus.EXITED SessionStatus.STOPPED)))
  (.record-snapshot
    state.session-repository
    event-type
    (.from-handle
      AgentSessionSnapshot
      handle
      :status status
      :backend-ref {"session_name" handle.session-id
                    "agent_type" (. (.get state.agent-types handle.session-id AgentType.CUSTOM) value)
                    "work_dir" (str (.get state.work-dirs handle.session-id "."))}
      :lifecycle (.get state.lifecycles handle.session-id)
      :last-observed-at now
      :finished-at (if (and (is previous-finished-at None) final?) now previous-finished-at)
      :cleaned-at (if (is-not previous None) previous.cleaned-at None)
      :output-snippet (cond
                        (is-not output-snippet None) output-snippet
                        (is-not previous None) previous.output-snippet
                        True None)))
  None)


(defk _register-session [state handle agent-type work-dir lifecycle]
  {:pre [(: state MockAgentState) (: handle SessionHandle) (: agent-type AgentType) (: work-dir (| str PurePath))
         (: lifecycle AgentSessionLifecycle)]
   :post [(: % SessionHandle)]
   :tags {:context "mock-agent" :role "foundation"}}
  "Open the session in the mock's state — BOOTING, with its scripted screen or an empty one — and record it started."
  (setv (get state.handles handle.session-id) handle)
  (setv (get state.statuses handle.session-id) SessionStatus.BOOTING)
  (.setdefault state.outputs handle.session-id "")
  (setv (get state.agent-types handle.session-id) agent-type)
  (setv (get state.work-dirs handle.session-id) work-dir)
  (setv (get state.lifecycles handle.session-id) lifecycle)
  (<- (_record-snapshot state "session_started" handle SessionStatus.BOOTING None))
  handle)


(defk _refuse-existing-session [state session-name]
  {:pre [(: state MockAgentState) (: session-name str)] :post [(: % (type None))]
   :tags {:context "mock-agent" :role "judgment"}}
  "A launch names a new session: refuse a name the mock already has open."
  (when (in session-name state.handles)
    (raise (SessionAlreadyExistsError f"Session {session-name} already exists")))
  None)


(defk _launch [state launch mcp-server-urls]
  {:pre [(: state MockAgentState) (: launch LaunchEffect) (: mcp-server-urls (| dict None))]
   :post [(: % SessionHandle)]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer Launch with a testing session; with MCP tools, write the .mcp.json that points the agent at the in-VM
   servers the handler started (server name → URL in mcp-server-urls)."
  (refuse-turn-capabilities launch :handler "mock_agent_handler")
  (<- (_refuse-existing-session state launch.session-name))
  (when launch.mcp-tools
    (when (or (is mcp-server-urls None)
              (not-in launch.mcp-server-name mcp-server-urls))
      (raise (ValueError "MCP tools require an in-VM MCP server URL")))
    (.write-text
      (/ (Path launch.work-dir) ".mcp.json")
      (json.dumps
        {"mcpServers"
         (dfor [name url] (.items mcp-server-urls)
           name {"type" "sse" "url" url})}
        :indent 2)))
  (<- handle (_register-session state (SessionHandle :session-id launch.session-name)
                                launch.agent-type launch.work-dir launch.lifecycle))
  handle)


(defk _claude-launch [state launch]
  {:pre [(: state MockAgentState) (: launch ClaudeLaunchEffect)] :post [(: % SessionHandle)]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer the Claude-specific launch with a testing Claude session."
  (<- (_refuse-existing-session state launch.session-name))
  (<- handle (_register-session state (SessionHandle :session-id launch.session-name)
                                AgentType.CLAUDE launch.work-dir launch.lifecycle))
  handle)


(defk _monitor [state handle]
  {:pre [(: state MockAgentState) (: handle SessionHandle)] :post [(: % Observation)]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer Monitor: the session's next scripted observation, or — unscripted — its status, unchanged.
   A session the mock does not have open has exited."
  (val session-name handle.session-id)
  (var observation (Observation :status SessionStatus.EXITED))
  (when (in session-name state.handles)
    (val script (.get state.scripts session-name))
    (if script
        (do
          (val observed (.next-observation script))
          (setv (get state.statuses session-name) (get observed 0))
          (setv (get state.outputs session-name) (get observed 1))
          (:= observation (Observation :status (get observed 0)
                                       :output-changed True
                                       :output-snippet (if (get observed 1) (cut (get observed 1) -500 None) None)))
          (<- (_record-snapshot state "session_observed" (get state.handles session-name)
                                observation.status observation.output-snippet)))
        (do
          (:= observation (Observation :status (.get state.statuses session-name SessionStatus.RUNNING)
                                       :output-changed False))
          (<- (_record-snapshot state "session_observed" (get state.handles session-name)
                                observation.status None)))))
  observation)


(defk _capture [state handle]
  {:pre [(: state MockAgentState) (: handle SessionHandle)] :post [(: % str)]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer Capture: the session's current screen text."
  (when (not-in handle.session-id state.handles)
    (raise (SessionNotFoundError f"Session {handle.session-id} does not exist")))
  (val output (.get state.outputs handle.session-id ""))
  (<- (_record-snapshot state "session_captured" handle
                        (.get state.statuses handle.session-id SessionStatus.RUNNING)
                        (if output (cut output -500 None) None)))
  output)


(defk _send [state handle message]
  {:pre [(: state MockAgentState) (: handle SessionHandle) (: message str)] :post [(: % (type None))]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer Send: remember the message the session was sent."
  (when (not-in handle.session-id state.handles)
    (raise (SessionNotFoundError f"Session {handle.session-id} does not exist")))
  (.append state.sends #(handle.session-id message))
  None)


(defk _stop [state handle]
  {:pre [(: state MockAgentState) (: handle SessionHandle)] :post [(: % (type None))]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer Stop: mark the session stopped and shut down its MCP server."
  (<- (_stop-mcp-server state.mcp-servers handle.session-id))
  (when (in handle.session-id state.handles)
    (setv (get state.statuses handle.session-id) SessionStatus.STOPPED)
    (<- (_record-snapshot state "session_stopped" handle SessionStatus.STOPPED None)))
  None)


(defk _registered-snapshot [state session-id]
  {:pre [(: state MockAgentState) (: session-id str)] :post [(: % AgentSessionSnapshot)]
   :tags {:context "mock-agent" :role "foundation"}}
  "The recorded state of a session the mock registered; an id it never registered is an error."
  (val snapshot (.get-session state.session-repository session-id))
  (when (is snapshot None)
    (raise (SessionNotFoundError f"Session {session-id} is not registered")))
  snapshot)


(defk _observe-session [state session-id]
  {:pre [(: state MockAgentState) (: session-id str)] :post [(: % AgentSessionSnapshot)]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer ObserveAgentSession: one Monitor of the session, then its recorded state."
  (<- snapshot (_registered-snapshot state session-id))
  (<- (_monitor state (.to-handle snapshot)))
  (<- observed (_registered-snapshot state session-id))
  observed)


(defk _cancel-session [state session-id]
  {:pre [(: state MockAgentState) (: session-id str)] :post [(: % AgentSessionSnapshot)]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer CancelAgentSession: stop the session, then its recorded state."
  (<- snapshot (_registered-snapshot state session-id))
  (<- (_stop state (.to-handle snapshot)))
  (<- cancelled (_registered-snapshot state session-id))
  cancelled)


(defk _cleanup-session [state session-id]
  {:pre [(: state MockAgentState) (: session-id str)] :post [(: % AgentSessionSnapshot)]
   :tags {:context "mock-agent" :role "foundation"}}
  "Answer CleanupAgentSession: shut down the session's MCP server, forget the session and record it cleaned."
  (<- (_stop-mcp-server state.mcp-servers session-id))
  (<- snapshot (_registered-snapshot state session-id))
  (.pop state.handles snapshot.session-name None)
  (setv (get state.statuses snapshot.session-name) SessionStatus.STOPPED)
  (val now (datetime.now timezone.utc))
  (.record-snapshot
    state.session-repository
    "session_cleaned"
    (.with-update snapshot :status SessionStatus.STOPPED :cleaned-at now :last-observed-at now)))


(defhandler _mock-agent-defhandler [state]
  "The mock terminal handler: answers Launch, Monitor, Capture, Send, Stop and the session-id effects from
   `state` (a MockAgentState). It owns no terminal transport; the schema-result (L2) effects pass outward."

  (LaunchEffect [session-name mcp-tools mcp-server-name]
    (var mcp-server-urls None)
    (when mcp-tools
      (<- server (_serve-mcp-tools k (tuple mcp-tools)))
      (setv (get state.mcp-servers session-name) server)
      (:= mcp-server-urls {mcp-server-name server.url}))
    (var handle None)
    (try
      (<- launched (_launch state effect mcp-server-urls))
      (:= handle launched)
      (except [e Exception]
        (when mcp-tools
          (<- (_stop-mcp-server state.mcp-servers session-name)))
        (raise e)))
    (resume handle))

  (ClaudeLaunchEffect []
    (<- handle (_claude-launch state effect))
    (resume handle))

  (MonitorEffect [handle]
    (<- observation (_monitor state handle))
    (resume observation))

  (CaptureEffect [handle]
    (<- output (_capture state handle))
    (resume output))

  (SendEffect [handle message]
    (<- (_send state handle message))
    (resume None))

  (StopEffect [handle]
    (<- (_stop state handle))
    (resume None))

  (GetAgentSessionEffect [session-id]
    (resume (.get-session state.session-repository session-id)))

  (ListAgentSessionsEffect [query]
    (resume (.list-sessions state.session-repository query)))

  (ObserveAgentSessionEffect [session-id]
    (<- snapshot (_observe-session state session-id))
    (resume snapshot))

  (AttachAgentSessionEffect [session-id]
    (<- (_registered-snapshot state session-id))
    (resume None))

  (CancelAgentSessionEffect [session-id]
    (<- snapshot (_cancel-session state session-id))
    (resume snapshot))

  (CleanupAgentSessionEffect [session-id]
    (<- snapshot (_cleanup-session state session-id))
    (resume snapshot))

  ;; 入力の前に runtime を事前起動する WarmSessionEffect は、mock の会話では LaunchEffect が既に session を開いている —
  ;; 黙って何もせずに応答せず、型で拒否する。
  (WarmSessionEffect []
    (raise (_AgentCapabilityUnsupportedError :capability "WarmSessionEffect" :handler "mock_agent_handler"))))


(defclass ScenarioStep []
  "One scripted L2 await outcome for the scenario handler.

   latency_seconds is how long the transport takes to deliver it: the await answering this step first waits
   that much real time (a slow transport — e.g. an outcome that arrives after the node deadline passed)."
  (defn __init__ [self * status [payload None] [validation-error None]
                  [continuable True] [latency-seconds 0.0]]
    (setv self.status status)
    (setv self.payload payload)
    (setv self.validation-error validation-error)
    (setv self.continuable continuable)
    (setv self.latency-seconds latency-seconds))

  (setv success
        (classmethod
          (fn [cls payload]
            (cls :status AwaitStatus.EXITED :payload payload))))

  (setv invalid
        (classmethod
          (fn [cls * payload validation-error]
            (cls :status AwaitStatus.EXITED
                 :payload payload
                 :validation-error validation-error))))

  (setv terminal-invalid
        (classmethod
          (fn [cls * validation-error]
            (cls :status AwaitStatus.EXITED
                 :validation-error validation-error
                 :continuable False))))

  (setv absent
        (classmethod
          (fn [cls]
            (cls :status AwaitStatus.EXITED))))

  (setv awaiting-input
        (classmethod
          (fn [cls message]
            (cls :status AwaitStatus.AWAITING_INPUT
                 :validation-error message))))

  (setv timeout
        (classmethod
          (fn [cls]
            (cls :status AwaitStatus.TIMED_OUT)))))


(defclass [dataclass] ScenarioWorld []
  "The scenario handler's world: each session's scripted await outcomes and what the handler was asked.

   scripts maps a deterministic session id (AgentSpec.session_id) to the ScenarioStep values its awaits return
   in turn (the last one repeats; a session without a script succeeds with {}), so retries are explicit in the
   test data. Keep the world to read what happened — launch_count, follow_up_messages, stopped_sessions,
   released_sessions, mcp_server_urls — or to replace a session's script between runs (configure_script).
   One world serves several runs: a session launched in one run is re-adopted, not launched again, by the next."
  (setv #^ dict scripts (field :default-factory dict))
  (setv #^ MockAgentState terminal (field :default-factory MockAgentState :init False :repr False))
  (setv #^ list stopped-sessions (field :default-factory list :init False))
  (setv #^ list released-sessions (field :default-factory list :init False))
  (setv #^ dict _indices (field :default-factory dict :init False :repr False))
  (setv #^ dict _launch-counts (field :default-factory dict :init False :repr False))
  (setv #^ dict _follow-ups (field :default-factory dict :init False :repr False))
  (setv #^ dict _mcp-server-urls (field :default-factory dict :init False :repr False))

  (defn __post_init__ [self]
    (setv self.scripts (dfor [session-id steps] (.items self.scripts) session-id (list steps))))

  (defn configure-script [self session-id steps]
    "Replace a session's scripted outcomes, e.g. between park and resume."
    (setv (get self.scripts session-id) (list steps))
    (setv (get self._indices session-id) 0))

  (defn launch-count [self session-id]
    "How many times the session was launched (a launch that re-adopts the live session does not count)."
    (.get self._launch-counts session-id 0))

  (defn follow-up-messages [self session-id]
    "The FollowUp messages the session received, in order."
    (list (.get self._follow-ups session-id [])))

  (defn mcp-server-urls [self session-id]
    "The in-VM MCP server URLs (server name → URL) the session's last launch received; None without tools."
    (.get self._mcp-server-urls session-id)))


;; The four answers below are plain functions: the production attempt loop (_run_agent_task) calls them
;; synchronously for the agent effect, and AwaitResult's polling program calls the await for each look.

(defn _scenario-launch-session [world effect mcp-server-urls]
  "Answer LaunchSession: launch the spec's session, or re-adopt it when it is live (idempotent per session id)."
  (setv session-id effect.spec.session-id)
  (when (not-in session-id world.terminal.handles)
    (setv (get world._launch-counts session-id) (+ (.get world._launch-counts session-id 0) 1))
    (setv (get world.terminal.handles session-id) (SessionHandle :session-id session-id)))
  (setv (get world._mcp-server-urls session-id) mcp-server-urls)
  (SessionHandle :session-id session-id))


(defn _scenario-await [world effect]
  "Answer one look of AwaitResult: the session's next scripted outcome (one step per await; the last repeats)."
  (setv session-id effect.handle.session-id)
  (setv script (.get world.scripts session-id [(ScenarioStep.success {})]))
  (setv index (.get world._indices session-id 0))
  (setv step (get script (min index (- (len script) 1))))
  (when (< index (len script))
    (setv (get world._indices session-id) (+ index 1)))
  (when (> step.latency-seconds 0)
    (time.sleep step.latency-seconds))
  (AwaitOutcome
    :status step.status
    :result step.payload
    :validation-error step.validation-error
    :continuable step.continuable))


(defn _scenario-follow-up [world effect]
  "Answer FollowUp: remember the message the session received; the session stays the same."
  (refuse-turn-capabilities effect :handler "scenario_agent_handler")
  (.append (.setdefault world._follow-ups effect.handle.session-id []) effect.message)
  effect.handle)


(defn _scenario-release [world effect]
  "Answer ReleaseSession: remember the session was released."
  (.append world.released-sessions effect.handle.session-id))


(defhandler _scenario-session-defhandler [world]
  "The scenario handler's schema-result half: answers agent, LaunchSession, AwaitResult, FollowUp, StopSession
   and ReleaseSession from `world` (a ScenarioWorld). scenario_agent_handler stacks it outside the mock handler
   over world.terminal, which answers the terminal effects of the same sessions. The agent effect runs the
   production attempt loop over the scripted outcomes."

  (AgentEffect [task]
    (resume (_run-agent-task task
              :launch-session (fn [launch] (_scenario-launch-session world launch None))
              :await-result (partial _scenario-await world)
              :follow-up (partial _scenario-follow-up world)
              :release-session (partial _scenario-release world))))

  (LaunchSessionEffect [spec]
    (var mcp-server-urls None)
    (when spec.mcp-tools
      (<- server (_serve-mcp-tools k (tuple spec.mcp-tools)))
      (setv (get world.terminal.mcp-servers spec.session-id) server)
      (:= mcp-server-urls {spec.mcp-server-name server.url}))
    (resume (_scenario-launch-session world effect mcp-server-urls)))

  (AwaitResultEffect [handle timeout-seconds]
    (<- outcome (_await-result-by-polling (partial _scenario-await world) handle timeout-seconds))
    (resume outcome))

  (FollowUpEffect []
    (resume (_scenario-follow-up world effect)))

  (StopSessionEffect [handle]
    (.append world.stopped-sessions handle.session-id)
    (<- (_stop-mcp-server world.terminal.mcp-servers handle.session-id))
    (resume None))

  (ReleaseSessionEffect [handle]
    (_scenario-release world effect)
    (<- (_stop-mcp-server world.terminal.mcp-servers handle.session-id))
    (resume None)))
