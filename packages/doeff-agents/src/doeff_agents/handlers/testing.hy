"""Testing AgentHandler implementations for deterministic agent tests."""

(import datetime [datetime timezone])
(import json)

(import doeff_agents.adapters.base [AgentType])
(import doeff_agents.effects [
  AgentSessionLifecycle
  AgentSessionSnapshot
  AttachAgentSessionEffect
  AwaitOutcome
  AwaitResultEffect
  AwaitStatus
  CancelAgentSessionEffect
  CaptureEffect
  ClaudeLaunchEffect
  CleanupAgentSessionEffect
  FollowUpEffect
  GetAgentSessionEffect
  L2SessionHandle
  LaunchEffect
  LaunchSessionEffect
  LaunchTaskEffect
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
  StopSessionEffect])
(import doeff_agents.handlers.production [AgentHandler])
(import doeff_agents.monitor [SessionStatus])
(import doeff_agents.session-store [
  AgentSessionRepository
  InMemoryAgentSessionRepository])


(defclass MockSessionScript []
  "Script for deterministic session observations."
  (defn __init__ [self * [observations None] [_index 0]]
    (setv self.observations (if (is observations None) [] (list observations)))
    (setv self._index _index))

  (defn next-observation [self]
    (if (>= self._index (len self.observations))
        #(SessionStatus.DONE "")
        (do
          (setv obs (get self.observations self._index))
          (setv self._index (+ self._index 1))
          obs))))


(defclass MockAgentState []
  "Serializable snapshot of testing handler state."
  (defn __init__ [self * [scripts None] [handles None] [statuses None]
                  [outputs None] [sends None] [next_pane_id 0]]
    (setv self.scripts (or scripts {}))
    (setv self.handles (or handles {}))
    (setv self.statuses (or statuses {}))
    (setv self.outputs (or outputs {}))
    (setv self.sends (or sends []))
    (setv self.next_pane_id next_pane_id)))


(defclass MockAgentHandler [AgentHandler]
  "Testing AgentHandler implementation that never owns terminal transport."
  (defn __init__ [self [session-repository None]]
    (setv self._sessions {})
    (setv self._handles {})
    (setv self._statuses {})
    (setv self._outputs {})
    (setv self._agent-types {})
    (setv self._work-dirs {})
    (setv self._lifecycles {})
    (setv self._sends [])
    (setv self._next-pane-id 0)
    (setv self._mcp-servers {})
    (setv self._session-repository
          (or session-repository (InMemoryAgentSessionRepository))))

  (defn configure-session [self session-name [script None] [initial-output ""]]
    "Pre-configure a session for testing."
    (when script
      (setv (get self._sessions session-name) script))
    (setv (get self._outputs session-name) initial-output)
    (setv (get self._statuses session-name) SessionStatus.BOOTING))

  (defn handle-launch [self effect [mcp-servers None]]
    "Create a testing session, optionally writing MCP server config."
    (when (in effect.session-name self._handles)
      (raise (SessionAlreadyExistsError
               f"Session {effect.session-name} already exists")))

    (when effect.mcp-tools
      (when (or (is mcp-servers None)
                (not (in effect.mcp-server-name mcp-servers)))
        (raise (ValueError "MCP tools require an in-VM MCP server URL")))
      (setv mcp-json-path (/ effect.work-dir ".mcp.json"))
      (.write-text
        mcp-json-path
        (json.dumps
          {"mcpServers"
           (dfor [name url] (.items mcp-servers)
             name {"type" "sse" "url" url})}
          :indent 2)))

    (setv self._next-pane-id (+ self._next-pane-id 1))
    (setv handle (SessionHandle :session-id effect.session-name))
    (setv (get self._handles effect.session-name) handle)
    (setv (get self._statuses effect.session-name) SessionStatus.BOOTING)
    (.setdefault self._outputs effect.session-name "")
    (setv (get self._agent-types effect.session-name) effect.agent-type)
    (setv (get self._work-dirs effect.session-name) effect.work-dir)
    (setv (get self._lifecycles effect.session-name) effect.lifecycle)
    (._record-snapshot self "session_started" handle SessionStatus.BOOTING)
    handle)

  (defn handle-launch-task [self effect]
    "Create testing session for generic task launch."
    (raise (NotImplementedError "LaunchTaskEffect is deprecated; use LaunchEffect directly")))

  (defn handle-claude-launch [self effect]
    "Create a testing Claude session."
    (when (in effect.session-name self._handles)
      (raise (SessionAlreadyExistsError
               f"Session {effect.session-name} already exists")))

    (setv self._next-pane-id (+ self._next-pane-id 1))
    (setv handle (SessionHandle :session-id effect.session-name))
    (setv (get self._handles effect.session-name) handle)
    (setv (get self._statuses effect.session-name) SessionStatus.BOOTING)
    (.setdefault self._outputs effect.session-name "")
    (setv (get self._agent-types effect.session-name) AgentType.CLAUDE)
    (setv (get self._work-dirs effect.session-name) effect.work-dir)
    (setv (get self._lifecycles effect.session-name) effect.lifecycle)
    (._record-snapshot self "session_started" handle SessionStatus.BOOTING)
    handle)

  (defn handle-monitor [self effect]
    "Return next scripted observation."
    (setv session-name effect.handle.session-id)
    (when (not (in session-name self._handles))
      (return (Observation :status SessionStatus.EXITED)))

    (setv script (.get self._sessions session-name))
    (if script
        (do
          (setv #(status output) (.next-observation script))
          (setv (get self._statuses session-name) status)
          (setv (get self._outputs session-name) output)
          (setv observation
                (Observation
                  :status status
                  :output-changed True
                  :output-snippet (if output (cut output -500 None) None)))
          (._record-snapshot
            self "session_observed" (get self._handles session-name) status
            :output-snippet observation.output-snippet)
          observation)
        (do
          (setv observation
                (Observation
                  :status (.get self._statuses session-name SessionStatus.RUNNING)
                  :output-changed False))
          (._record-snapshot
            self "session_observed" (get self._handles session-name)
            observation.status)
          observation)))

  (defn handle-capture [self effect]
    "Return captured output."
    (setv session-name effect.handle.session-id)
    (when (not (in session-name self._handles))
      (raise (SessionNotFoundError f"Session {session-name} does not exist")))
    (setv output (.get self._outputs session-name ""))
    (._record-snapshot
      self "session_captured" effect.handle
      (.get self._statuses session-name SessionStatus.RUNNING)
      :output-snippet (if output (cut output -500 None) None))
    output)

  (defn handle-send [self effect]
    "Record sent message."
    (setv session-name effect.handle.session-id)
    (when (not (in session-name self._handles))
      (raise (SessionNotFoundError f"Session {session-name} does not exist")))
    (.append self._sends #(session-name effect.message)))

  (defn handle-stop [self effect]
    "Mark a session stopped and shut down any MCP server."
    (setv session-name effect.handle.session-id)
    (setv server (.pop self._mcp-servers session-name None))
    (when (is-not server None)
      (.shutdown server))
    (when (in session-name self._handles)
      (setv (get self._statuses session-name) SessionStatus.STOPPED)
      (._record-snapshot self "session_stopped" effect.handle SessionStatus.STOPPED)))

  (defn handle-get-session [self effect]
    "Return persisted testing session state."
    (.get-session self._session-repository effect.session-id))

  (defn handle-list-sessions [self effect]
    "Return persisted testing session states."
    (.list-sessions self._session-repository effect.query))

  (defn handle-observe-session [self effect]
    "Observe a testing session by id."
    (setv snapshot (._require-snapshot self effect.session-id))
    (.handle-monitor self (MonitorEffect :handle (.to-handle snapshot)))
    (setv updated (.get-session self._session-repository effect.session-id))
    (when (is updated None)
      (raise (SessionNotFoundError f"Session {effect.session-id} is not registered")))
    updated)

  (defn handle-attach-session [self effect]
    "Testing attach is a no-op after validating the session exists."
    (._require-snapshot self effect.session-id)
    None)

  (defn handle-cancel-session [self effect]
    "Cancel a testing session by id."
    (setv snapshot (._require-snapshot self effect.session-id))
    (.handle-stop self (StopEffect :handle (.to-handle snapshot)))
    (setv updated (.get-session self._session-repository effect.session-id))
    (when (is updated None)
      (raise (SessionNotFoundError f"Session {effect.session-id} is not registered")))
    updated)

  (defn handle-cleanup-session [self effect]
    "Clean up a testing session by id."
    (setv snapshot (._require-snapshot self effect.session-id))
    (.pop self._handles snapshot.session-name None)
    (setv (get self._statuses snapshot.session-name) SessionStatus.STOPPED)
    (setv now (datetime.now timezone.utc))
    (setv cleaned
          (.with-update
            snapshot
            :status SessionStatus.STOPPED
            :cleaned-at now
            :last-observed-at now))
    (.record-snapshot self._session-repository "session_cleaned" cleaned))

  (defn _require-snapshot [self session-id]
    (setv snapshot (.get-session self._session-repository session-id))
    (when (is snapshot None)
      (raise (SessionNotFoundError f"Session {session-id} is not registered")))
    snapshot)

  (defn _record-snapshot [self event-type handle status * [output-snippet None]]
    (setv now (datetime.now timezone.utc))
    (setv previous (.get-session self._session-repository handle.session-id))
    (setv finished-at (if (is-not previous None) previous.finished-at None))
    (when (and (is finished-at None)
               (in status #(SessionStatus.DONE SessionStatus.FAILED
                             SessionStatus.EXITED SessionStatus.STOPPED)))
      (setv finished-at now))
    (setv snippet
          (if (is-not output-snippet None)
              output-snippet
              (if (is-not previous None) previous.output-snippet None)))
    (setv snapshot
          (.from-handle
            AgentSessionSnapshot
            handle
            :status status
            :backend-ref {"session_name" handle.session-id
                          "agent_type" (. (.get self._agent-types
                                                handle.session-id
                                                AgentType.CUSTOM)
                                          value)
                          "work_dir" (str (.get self._work-dirs
                                                handle.session-id "."))}
            :lifecycle (.get self._lifecycles handle.session-id)
            :last-observed-at now
            :finished-at finished-at
            :cleaned-at (if (is-not previous None) previous.cleaned-at None)
            :output-snippet snippet))
    (.record-snapshot self._session-repository event-type snapshot))

  (setv sent-messages (property (fn [self] (list self._sends))))

  (defn snapshot [self]
    "Return a copyable state snapshot for compatibility/debugging."
    (MockAgentState
      :scripts (dict self._sessions)
      :handles (dict self._handles)
      :statuses (dict self._statuses)
      :outputs (dict self._outputs)
      :sends (list self._sends)
      :next_pane_id self._next-pane-id)))


(defclass ScenarioStep []
  "One scripted L2 await outcome for the scenario handler."
  (defn __init__ [self * status [payload None] [validation-error None]
                  [continuable True]]
    (setv self.status status)
    (setv self.payload payload)
    (setv self.validation-error validation-error)
    (setv self.continuable continuable))

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


(defclass ScenarioAgentHandler [MockAgentHandler]
  "Scenario-driven testing handler.

   Scripts are keyed by deterministic session id. Each await consumes one step,
   so retries are explicit in the test data. This is a handler swap; agents are
   never mocked by replacing application functions."
  (defn __init__ [self * [scripts None]]
    (.__init__ (super))
    (setv self._scenario-scripts
          (dfor [session-id steps] (.items (or scripts {}))
                session-id (list steps)))
    (setv self._scenario-indices {})
    (setv self._launch-counts {})
    (setv self._follow-ups {})
    (setv self.stopped-sessions [])
    (setv self.released-sessions []))

  (defn wrap [self program]
    "Wrap a Program by swapping in the standard Hy defhandler boundary."
    (import doeff_agents.handlers.effectful [agent-handler-defhandler])
    ((agent-handler-defhandler self) program))

  (defn handle-launch-session [self effect [mcp-servers None]]
    (setv session-id effect.spec.session-id)
    (when (not (in session-id self._handles))
      (setv (get self._launch-counts session-id)
            (+ (.get self._launch-counts session-id 0) 1))
      (setv (get self._handles session-id)
            (L2SessionHandle :session-id session-id)))
    (L2SessionHandle :session-id session-id))

  (defn handle-await-result [self effect]
    (setv session-id effect.handle.session-id)
    (setv script (.get self._scenario-scripts
                       session-id
                       [(ScenarioStep.success {})]))
    (setv index (.get self._scenario-indices session-id 0))
    (if (>= index (len script))
        (setv step (get script -1))
        (do
          (setv step (get script index))
          (setv (get self._scenario-indices session-id) (+ index 1))))
    (AwaitOutcome
      :status step.status
      :result step.payload
      :validation-error step.validation-error
      :continuable step.continuable))

  (defn handle-follow-up [self effect]
    (.setdefault self._follow-ups effect.handle.session-id [])
    (.append (get self._follow-ups effect.handle.session-id) effect.message)
    effect.handle)

  (defn handle-stop-session [self effect]
    (.append self.stopped-sessions effect.handle.session-id))

  (defn handle-release-session [self effect]
    (.append self.released-sessions effect.handle.session-id))

  (defn configure-script [self session-id steps]
    "Replace a session's scripted outcomes, e.g. between park and resume."
    (setv (get self._scenario-scripts session-id) (list steps))
    (setv (get self._scenario-indices session-id) 0))

  (defn launch-count [self session-id]
    (.get self._launch-counts session-id 0))

  (defn follow-up-messages [self session-id]
    (list (.get self._follow-ups session-id []))))
