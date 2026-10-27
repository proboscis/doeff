;;; The handlers the doeff-agents CLI runs its programs under (internal to doeff-agents).
;;;
;;; A CLI command is a program of effects: the public session effects
;;; (`doeff_agents.effects`) and the host-protocol asks of
;;; `doeff_agents.effects._session_host_effects`. `session-host-handlers` builds ONE
;;; connection to the session host and answers both over it — the session effects
;;; through `daemon-agent-handler`, the host asks through `session-host-defhandler` —
;;; so the command never builds a client itself. Whether a host may be started is
;;; decided here and only here, by `start-host`: an observation verb passes False
;;; (ADR-DOE-AGENTS-004 law reads-never-start-a-host), so a host that does not answer
;;; is an error the program sees, never a new host.
;;;
;;; `terminal-session-handlers` answers the `run` verb, which launches an agent in a
;;; tmux session of this machine without a host.

(require doeff-hy.handle [defhandler])

(import doeff.program [ProgramHandler])
(import doeff_time [sync-time-handler])
(import doeff_agents._agentd-client [LazyAgentdClient agentd-socket-is-supervised])
(import doeff_agents.effects._session-host-effects [
  HostKindsReading
  HostStatusReading
  ListSessionRowsEffect
  SessionHostCallEffect
  SessionHostKindsEffect
  SessionHostStatusEffect])
(import doeff_agents.handlers [daemon-agent-handler])
(import doeff_agents.handlers.effectful [tmux-agent-defhandler])
(import doeff_agents.tmux [get-default-backend])


;; 引数に残す理由: CLIENT は daemon-agent-handler と同じ 1 本の接続でなければならない
;; (host を起こすかの決めを 1 度だけにするため)ので、Ask で読まずに組の作り手が渡す。
(defhandler session-host-defhandler [#^ LazyAgentdClient client]
  "Answer the CLI's host-protocol asks over CLIENT, the connection its session effects share."

  (SessionHostStatusEffect []
    (setv status (.status client))
    (setv socket-path (.resolved-socket-path client))
    (resume (HostStatusReading
              :socket-path socket-path
              :status status
              :supervised (agentd-socket-is-supervised socket-path))))

  (SessionHostKindsEffect []
    (setv kinds (tuple (.kinds client)))
    (resume (HostKindsReading :socket-path (.resolved-socket-path client) :kinds kinds)))

  (SessionHostCallEffect [method params]
    (resume (.request client method params)))

  (ListSessionRowsEffect [query]
    (resume (.list-sessions-with-warnings client query))))


(defn #^ (get tuple #(ProgramHandler ...)) session-host-handlers
  [* #^ bool start-host #^ (| LazyAgentdClient None) [client None]
   #^ (| ProgramHandler None) [time-handler None]]
  "The handlers (outer first) a CLI command that talks to the session host runs under.

   START-HOST says whether reaching no host may start one (True only for the verbs that
   change a session, `agentd ensure` and the explicit `--ensure`). CLIENT replaces the
   connection (a test's fake host); TIME-HANDLER replaces the wall clock `Delay` runs on."
  (setv connection (if (is client None) (LazyAgentdClient :start-host start-host) client))
  #((if (is time-handler None) (sync-time-handler) time-handler)
    (session-host-defhandler connection)
    (daemon-agent-handler :client connection)))


(defn #^ (get tuple #(ProgramHandler ...)) terminal-session-handlers
  [* #^ (| ProgramHandler None) [time-handler None]]
  "The handlers (outer first) the `run` verb launches and watches a tmux session of this machine under."
  #((if (is time-handler None) (sync-time-handler) time-handler)
    (tmux-agent-defhandler :backend (get-default-backend))))
