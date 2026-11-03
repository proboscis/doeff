;;; Executable ADR: koine session surface v0 stage 1 — session.adopt +
;;; turn stamping + reap exemption for interactive/adopted (fulfilling safety clause 1).
;;;
;;; Revision note (2026-08-17): wave 1-S1 / ADR-DOE-AGENTS-007 revision; origin =
;;; design-conversation-liveness-sessionhost-wave1-2026-08-17.html appendix 4-2 S1;
;;; numbering by proxy = integration-lead [provisional; same shape as koine ruling 6f6a36d7].
;;; A revision that makes the conversation ID (conversation_id) a first-class key for registration, stamping, and row lookup —
;;; R1 (conversation index) / R2 (adopt accepting the conversation, with 3 idempotency branches) / R5 (restructured resolution keys —
;;; the name key limited to bare descriptors) / R7 (probe-free row lookup port) + 2 laws
;;; (stamp-never-crosses-identities / conversation-lookup-never-probes).

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-007
  :title "sessionhost participates as the first implementation of koine session surface v0 stage 1 — session.adopt (observation-only, verify existence → register, accepts conversation ID) + turn stamping (descriptor resolution = pane/conversation/bare name, opaque wait) + unconditional reap exemption for interactive/adopted (safety clause 1) + level-triggered liveness derivation + conversation ID row lookup port (wave 1-S1 revision)"
  :status "accepted"
  :scope ["packages/doeff-agents (sessionhost: store / effects / policy / host / adopt / turn)"
          "packages/doeff-agents/conformance (S23-S27)"]
  :problem
    [(fact
       "The contract for a ledger that answers 'who is running in this system right now' in a single view = koine session surface v0 (the interface-first ruling of pavo ADR 0003 v2). sessionhost had already implemented the store, monitor, lifecycle enum, and substrate abstraction (tmux/herdr); the only missing pieces were adopt (after-the-fact registration of an already-live pane) and turn stamping."
       :evidence "~/repos/koine/surfaces/session/semantics-v0.md; ~/repos/pavo/adr/0003-runtime-unification.md R3/R6")
     (fact
       "Safety clause 1 (reaper fail-closed; interactive is never subject to unconditional reaping) was red in the pre-implementation sessionhost: is-run-to-completion was used in only 4 places (finalize's pane kill / result-first / turn-end / stall watchdog), and 5 paths remained that terminalize interactive rows — boot watchdog (policy.hy old 472-488) / stale reap (old 492-505) / launch timeout (old 507-524) / mux liveness check (old 526-550) / zombie reaper (old 552-568)."
       :evidence "packages/doeff-agents/conformance/test_s26_interactive_not_reaped.py (TDD red 2026-07-21: measured that 4 paths terminalize interactive rows)")
     (fact
       "The launch timeout path interferes fatally with adopt: adopt only observes and does not look at the startup marker, so an adopt row is exactly 'status=running, observed_active_at=None' — it always turns failed 60 seconds after registration (default knob), and because the store's terminal guard forbids reactivation, the row dies while only the pane survives, an orphaning. Adding adopt alone means 'registration kills the seat'."
       :evidence "~/repos/koine/docs/turn-stamp-path.md cross-check result (recording of a critical finding 2026-07-21); packages/doeff-agents/src/doeff_agents/_sessionhost/store.hy db-upsert-snapshot terminal guard")
     (fact
       "There was no ownership marker column (agent_sessions had no owner/creator/adopted column; owner_pid is for the daemon lease), so the clause's opt-in marker + fail-closed could not even be expressed."
       :evidence "packages/doeff-agents/tests/sessionhost_store_deftests.hy EXPECTED-COLUMNS (TDD red 2026-07-21)")
     (fact
       "The conversation ID was not on the interactive-seat ledger at all (0 of 32 rows — for work rows, 8,570/12,078 rows had it), so readers on the agora side (all keyed by conversation ID) could not look up the ledger, and the stamp delivery rate was 14.9% (12,245 undelivered vs 2,138 delivered). There was also a live case of name-secondary-key absorption: the dead row integration-lead (old slot; a one-off manual registration on 2026-07-21) absorbed the stamps of an active seat, and turn_since advanced 3 times during observation — a dead conversation's row looked alive and corrupted the reads. Listing took 2.3-4.8 seconds per 32 rows (caused by the per-row synchronous probe of the mirror principle), which does not fit consumers that read every beat."
       :evidence "design-conversation-liveness-sessionhost-wave1-2026-08-17.html appendix 4-3 (4 read-only measurements: direct read of agentd-herdr.sqlite, daemon.status counter, socket measurement)")]
  :context
    [(interpretation
       "The source of truth for the contract is koine (github.com/proboscis/koine surfaces/session/semantics-v0.md), and this ADR records the implementation-side change (cross-repo principle — pavo ADR 0003 R8). sessionhost participates as the first implementation of a subset of surface v0 (session only). The 4 safety clauses are contract clauses that must not regress (R4).")
     (interpretation
       "The source of truth for the turn stamp write path is koine docs/turn-stamp-path.md: the only writer is the seat's own hook (single writer per source); the hook writes one line to the raw socket and closes without waiting for a response (hard timeout ≤200ms, fail-open). The receiving side's obligation = never create a hang. The seat does not know its session id — sessionhost resolves the descriptor {pane_id, agent_name}, and no id distribution mechanism (env/file) is built.")
     (interpretation
       "Liveness is a derivation, not state: stalled = an open turn (holder='agent') past the threshold, re-derived on every wire output. Coverage of turn-open is partial (it may not fire when starting from a harness-internal restart — finding 3), so an edge-triggered implementation that assumes open/close pairs is forbidden. A closed turn (waiting on WAIT) is non-stalled regardless of elapsed time — waiting is agora's normal state.")
     (interpretation
       "Origin of the semantics of the wave 1-S1 revision (2026-08-17): a conversation outlives its incarnations (ADR-DOE-AGENTS-006 conversation-outlives-incarnation), and a row is the record of one incarnation of a conversation. Therefore (1) the conversation ID is the key that carries row identity — stamping and row lookup match only on exact equality of conversation_json.session_id (a partial match across all fields reads a 'mention' as a 'registration'). (2) If, by reuse of the underlying substrate (pane), a different conversation appears at the same substrate.ref, that is a new incarnation of a new conversation = a new row, and the conversation identity of the existing row is never rewritten (the store's COALESCE first-write-wins is the last line of defense). (3) Design S1 ③'s 'name resolution is limited to rows whose substrate exists' is incompatible with the stamping hot path's obligation not to touch the substrate (existing law turn-rpc-is-store-only — a probe on every stamp is explicitly a counterexample), so it is fulfilled by an equivalent store-only mechanism: a descriptor carrying an identifier (pane_id / conversation_id) never falls back to the name key. A stamp that cannot be looked up by an identifier key is an honest no-op (unadopted counter), and periodic registration (the wave 1 S2 batch) supplies the row on the next beat.")]
  :decision
    [(rule R1 "sessionhost participates as the first implementation of koine session surface v0 (subset = session only; the source of truth for the contract is koine). The schema additions are only 4 columns via additive migration: adopted (ownership marker, INTEGER NOT NULL DEFAULT 0) / turn_holder / turn_since / turn_wait_json (opaque). Wave 1-S1 revision: an expression index for conversation ID row lookup (idx_agent_sessions_conversation — json_extract(conversation_json, '$.session_id')) is added additively. No columns are added — conversation_json remains owned by ADR-DOE-AGENTS-006 (no second location for conversation identity is created). The wire is additive to the existing envelope (session.adopt / session.turn_open / session.turn_close / session.by_conversation / daemon.status counters / the adopted filter of session.list). New contracts originating from koine use a typed string error_code (adopt_target_not_found) — the oracle's numeric table (-32000..) is a frozen vocabulary, and new vocabulary is not added to it.")
     (rule R2 "adopt is observation-only + an ordering obligation: it registers only when the substrate existence check (the existing TmuxHasSession effect — a substrate-neutral probe that resolves to agent.get on the herdr backend) succeeds; on failure it creates no row and returns a typed error (preventing a recurrence of phantom turn-open). Mutations of the substrate (sending keys, creating/destroying sessions, FS writes, delivery) are structurally forbidden on the adopt path (installed semgrep rule doeff-agents-adopt-must-not-mutate-substrate). The id is an opaque id minted by sessionhost (uuid4) — the caller's name is not embedded in the id. Wave 1-S1 revision: adopt accepts an optional conversation_id (non-empty string) and writes it to conversation_json as {\"session_id\": <id>} (the identity component of the ADR-006 union). Idempotency has 3 conversation-aware branches — (a) the same conversation is already registered on a non-terminal row with the same substrate.ref → return the existing row as is (no write). (b) A row with no registered conversation (NULL) → fill in the conversation and return it (completing the registration of the same incarnation — the store's COALESCE first-write-wins is the last line of defense; event = session_conversation_discovered). (c) The existing row registered a different conversation → mint a new row (substrate reuse = a new incarnation of a new conversation; the existing row's conversation identity is never rewritten). An adopt that carries no conversation_id behaves as before (returns the newest non-terminal row with the same ref).")
     (rule R3 "The reap exemption is decided at the start of monitor-session-once (before the booting arm): exempt = adopted OR non-run_to_completion (interactive, and unknown lifecycles are fail-closed and not reaped — reaping is opt-in for run_to_completion). The only thing the monitor may do to an exempt row is record observations, writing the update of last_observed_at as the witness that 'the monitor was alive, evaluated, and did not reap' (the observation surface S26 asserts). Terminal status transitions, writing finished_at/terminal_cause, pane kill, and sending solicitations are forbidden. The installed semgrep rule doeff-agents-interactive-must-not-be-terminalized forbids, as the old form, 'writing :status terminal before the exemption decision' (confirmed to fire on the old policy.hy, 2026-07-21).")
     (rule R4 "Liveness is only a level-triggered derivation: stalled = (turn_holder == 'agent') AND (now - turn_since > DOEFF_AGENTD_TURN_STALL_SECS, default 1800). stalled is not written to the store and status is not changed (signal only). The derivation is performed every time on wire output (responses of session.get / session.list / session.adopt). A closed turn (holder = wait.who = user/work, etc.) is non-stalled regardless of elapsed time. The mirror principle (clause 3) is the same read-time derivation surface: for exempt, non-terminal rows, substrate_present / substrate_checked_at are probed every time and put on the wire; a vanished pane is not ruled exited, and the row is not deleted either.")
     (rule R5 "The turn RPC never touches the substrate: only descriptor resolution + a 3-column UPDATE as a single actor op. Because its counterpart is the ≤200ms fire-and-forget hook hot path, not creating a hang is the receiving side's contractual obligation (installed semgrep rule doeff-agents-turn-rpc-must-not-touch-substrate). Resolution keys of the wave 1-S1 revision (descriptor = {pane_id, agent_name, conversation_id} — all optional, at least 1): ① pane_id is the primary key — when the descriptor carries a conversation_id, it does not land on a row that registered a different conversation (rows matching the conversation take priority over rows with no registered conversation [NULL]; rows of a different conversation are excluded). ② conversation_id is the secondary key — exact match on conversation_json.session_id to the newest non-terminal row of the conversation (stamps arrive even in the window right after revival, before the pane is registered). ③ The agent_name key is used only when the descriptor carries neither pane_id nor conversation_id (installed semgrep rule doeff-agents-turn-name-key-requires-bare-descriptor) — a name fallback for a stamp that carries an identifier is exactly the shape of the live absorption case (2026-08-17: a dead row absorbed the stamps of an active seat). With multiple candidates, the first by started_at DESC, session_id ASC (the same total order as session.list). The targets remain, as before, only adopted non-terminal rows. A stamp that is not adopted / has no identifier match is an honest no-op + a visible counter (daemon.status counters.turn_stamp_unadopted — doubling as the gauge of adopt coverage), neither an error nor silently dropped. wait is stored opaquely (the only thing read is who, mapped to holder — the interpretive authority for the kind vocabulary is the seat-side wait_protocol.py). The turn RPC writes no audit event / command (stamping is a high-frequency hot path — no unbounded table growth or latency source is introduced).")
     (rule R6 "Explicit known limits (the flip side of clause 4): stamping is self-reported — a seat that is 'running but not stamped' can be invisible on the turn/stalled surface. The wire honestly represents not-stamped by omitting the fields (turn_* absent) and does not synthesize a fake open turn. The boot watchdog (BOOTING rows) is owned by the launch pipeline, so placing the exemption decision before it also keeps interactive rows left in BOOTING from being reaped (BOOTING interactive rows are left behind when the launcher dies — recorded as a reconciliation display issue for stage 2 onward). counters are in-memory (persistence waits until measured demand appears).")
     (rule R7 "The conversation ID → row lookup port (wave 1-S1 revision; wire method session.by_conversation {conversation_id}) is a fast probe-free read: resolution rule = the newest non-terminal row, else the newest terminal row, else null (total order = started_at DESC, session_id ASC — the store surface of the resolution rule in the draft conversation resource contract [conversationId → at most 1 live incarnation, else the newest terminal incarnation]). The response is the wire snapshot + the stalled derivation (store reads only), and substrate_present / substrate_checked_at are intentionally **absent** (rather than returning stale stored values, the fields are not included at all — the reader combines them with its own substrate observation to derive liveness. The per-row synchronous probe that caused listing to take 2.3-4.8s per 32 rows is not brought into this port). The mirror principle (R4) is unchanged in the responses of session.get / session.list / session.adopt — this port is not a substitute for it but a raw-material read dedicated to consumers that read every beat (supply points). It is not limited to adopted rows (conversation identity spans the whole ledger — rows originating from launch can be looked up too).")]
  :laws
    [(law interactive-rows-are-never-reaped
       :statement "monitor_terminalization => only_run_to_completion_rows; adopted_or_non_rtc_rows_never_transition_to_terminal_by_monitor"
       :counterexamples
         [(counterexample "any of the launch timeout / stale reap / zombie / mux vanished / boot watchdog arms makes a row with lifecycle=interactive or adopted=1 failed/exited")
          (counterexample "place the exemption decision after the booting arm or the stale arm, in an order where a preceding arm can reach interactive rows")])
     (law adopt-verifies-then-registers
       :statement "adopt => substrate_existence_check_precedes_upsert; failed_adopt_leaves_no_row; adopt_never_mutates_substrate"
       :counterexamples
         [(counterexample "upsert the row first, then check existence, and delete the row on failure (a failed delete becomes a phantom turn-open)")
          (counterexample "call TmuxSendKeys / TmuxNewSession / TmuxKillSession / FsWriteTextAtomic / DeliverMessage during adopt")])
     (law ledger-mirrors-reality
       :statement "vanished_substrate_of_exempt_row => derived_divergence_display_only; never_terminalize_never_delete"
       :counterexamples
         [(counterexample "detect a vanished pane of an exempt row and make the row exited / DELETE the row")
          (counterexample "store substrate_present as a column and return the stored value, not reality, on the wire")])
     (law liveness-is-level-triggered-derivation
       :statement "stalled => derived_at_read_time_from_turn_holder_and_turn_since; never_stored_never_mutates_status; closed_turn_never_stalled"
       :counterexamples
         [(counterexample "write stalled to the store as a column or a status value")
          (counterexample "set a stalled timer triggered by receiving a turn-open event (edge-triggered, falling silent forever when an open is missing)")
          (counterexample "turn a closed (waiting on WAIT) row stalled based on elapsed time")])
     (law turn-rpc-is-store-only
       :statement "turn_stamp_path => row_lookup_plus_update_in_single_actor_op; no_substrate_effects_no_subprocess_no_delivery"
       :counterexamples
         [(counterexample "capture the pane on turn_close and re-parse the WAIT string (duplicating interpretive authority = interpreter no. 5)")
          (counterexample "on every stamp, probe the substrate to 'confirm' the row's freshness before writing (a latency source against the ≤200ms contract)")])
     (law stamp-never-crosses-identities
       :statement "identity_bearing_stamp => lands_only_on_identity_compatible_row; name_key_requires_bare_descriptor; miss_is_honest_noop_with_counter"
       :counterexamples
         [(counterexample "a stamp carrying a pane_id lands on a row with a mismatched pane but a matching name (live absorption case 2026-08-17: the dead integration-lead row absorbed an active seat's stamps and turn_since advanced — a dead conversation's row looks alive)")
          (counterexample "a stamp carrying a conversation_id lands on a pane-matching row that registered a different conversation (polluting another conversation's record in the substrate-reuse window)")
          (counterexample "silently drop a miss on an identifier key, or make it an error (the contract is an honest no-op + counter — the gauge = the measure of adopt coverage)")])
     (law conversation-lookup-never-probes
       :statement "session_by_conversation => store_select_plus_derived_stalled_only; substrate_presence_fields_absent_not_stale; resolution_is_newest_active_else_newest_terminal"
       :counterexamples
         [(counterexample "fire a synchronous probe at the substrate to answer a row lookup (re-importing the slowness of 2.3-4.8s per 32 rows of listing into this port)")
          (counterexample "synthesize substrate_present from stored values / old copies and include it (absence is honestly expressed by an absent field — doing otherwise crushes the reader's degraded-vocabulary value unknown)")
          (counterexample "look up rows by partial match on conversation_json or by matching across all fields (reads a 'mention' as a 'registration' — only exact match on the identity field)")])]
  :enforcement
    [(deftest test-adr-doe-agents-007-reap-exemption-covers-interactive-and-adopted
       ;; Machine surface of the exemption predicate: adopted and non-run_to_completion (interactive, unknown
       ;; lifecycle = fail-closed) are exempt, and only plain run_to_completion remains
       ;; opted in to reaping.
       (import doeff_agents [SessionRow])
       (import doeff_agents._sessionhost.policy [reap-exempt])
       (defn mk [lifecycle adopted]
         (SessionRow :session-id "adr7" :session-name "adr7" :pane-id "%0"
                     :agent-type "claude" :lifecycle lifecycle :status "running"
                     :started-at "2026-07-21T00:00:00+00:00" :adopted adopted))
       (assert (is (reap-exempt (mk "interactive" False)) True))
       (assert (is (reap-exempt (mk "run_to_completion" True)) True))
       (assert (is (reap-exempt (mk "interactive" True)) True))
       (assert (is (reap-exempt (mk "future_lifecycle" False)) True))
       (assert (is (reap-exempt (mk "run_to_completion" False)) False)))
     (defsemgrep adopt-observation-only
       "doeff-agents-adopt-must-not-mutate-substrate"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/adopt.hy"
         "source" "(defk adopt-program [params]\n  (<- _ (tmux-send-keys pane-id \"Enter\" False False))\n  row)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/adopt.hy"
         "source" "(defk adopt-program [params]\n  (<- alive (tmux-has-session session-name))\n  (<- _ (session-store-upsert row))\n  row)\n"}])
     (defsemgrep interactive-never-terminalized
       "doeff-agents-interactive-must-not-be-terminalized"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
         "source" "(defk monitor-session-once [row knobs]\n  (when (= row.status \"booting\")\n    (setv row (replace row :status \"failed\"))\n    (return row))\n  row)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
         "source" "(defk monitor-session-once [row knobs]\n  (when (reap-exempt row)\n    (return row))\n  (when stale\n    (setv row (replace row :status \"failed\")))\n  row)\n"}])
     (defsemgrep turn-rpc-store-only
       "doeff-agents-turn-rpc-must-not-touch-substrate"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/turn.hy"
         "source" "(deff db-turn-stamp [conn pane-id]\n  (<- alive (tmux-has-session session-name))\n  None)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/turn.hy"
         "source" "(deff db-turn-stamp [conn pane-id]\n  (.execute conn \"UPDATE agent_sessions SET turn_holder = ? WHERE session_id = ?\")\n  None)\n"}])
     (deftest test-adr-doe-agents-007-stamp-never-crosses-identities
       ;; Machine surface of law stamp-never-crosses-identities (wave 1-S1 revision, real SQLite):
       ;; a stamp carrying an identifier (pane / conversation) never falls back to the name key;
       ;; the pane key never lands on a row claiming a different conversation; only a bare name descriptor
       ;; can use the name key. Regression pin for the live absorption case (2026-08-17).
       (import sqlite3)
       (import doeff_agents._sessionhost.store [db-migrate])
       (import doeff_agents._sessionhost.turn [db-resolve-turn-target])
       (setv conn (sqlite3.connect ":memory:"))
       (db-migrate conn)
       (defn seed [sid name pane conv]
         (.execute conn
           (+ "INSERT INTO agent_sessions (session_id, session_name, pane_id, "
              "agent_type, work_dir, status, backend_kind, backend_ref_json, "
              "started_at, adopted, conversation_json) "
              "VALUES (?, ?, ?, 'claude', '', 'running', 'tmux', '{}', "
              "'2026-08-17T00:00:00+00:00', 1, ?)")
           #(sid name pane conv)))
       ;; The live case's shape: an active seat with the same name as a dead row (old pane) stamps from a different pane
       (seed "dead-row" "integration-lead" "%old-pane" None)
       (assert (is (db-resolve-turn-target conn "%live-pane" "integration-lead" None)
                   None))
       (assert (is (db-resolve-turn-target conn "%live-pane" "integration-lead" "c-new")
                   None))
       ;; Only a bare name descriptor reaches the name key
       (assert (= (db-resolve-turn-target conn None "integration-lead" None)
                  "dead-row"))
       ;; The pane key does not land on a row claiming a different conversation; it does when the conversation matches
       (seed "conv-row" "seat-c" "%pane-c" "{\"session_id\":\"c1\"}")
       (assert (is (db-resolve-turn-target conn "%pane-c" None "c2") None))
       (assert (= (db-resolve-turn-target conn "%pane-c" None "c1") "conv-row"))
       ;; conversation secondary key: reaches the conversation's row even when the pane is not registered
       (assert (= (db-resolve-turn-target conn "%moved" None "c1") "conv-row"))
       (.close conn))
     (defsemgrep turn-name-key-requires-bare-descriptor
       "doeff-agents-turn-name-key-requires-bare-descriptor"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/turn.hy"
         "source" "(deff db-resolve-turn-target [conn pane-id agent-name conversation-id]\n  (when (is-not agent-name None)\n    (setv row (.fetchone (.execute conn base-name-sql)))\n    (when (is-not row None) (return (get row 0))))\n  None)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/turn.hy"
         "source" "(deff db-resolve-turn-target [conn pane-id agent-name conversation-id]\n  (when (and (is pane-id None) (is conversation-id None) (is-not agent-name None))\n    (setv row (.fetchone (.execute conn base-name-sql)))\n    (when (is-not row None) (return (get row 0))))\n  None)\n"}])]
  :plans ["docs/adr/defadr_doeff_agents_007_koine_session_surface.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/store.hy (additive 4 columns + adopted filter + conversation index + db-session-by-conversation [wave 1-S1])"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy (reap-exempt exemption arm + turn-stalled derivation)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/adopt.hy (adopt program — observation-only + conversation acceptance and 3 idempotency branches [wave 1-S1])"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/turn.hy (descriptor resolution = pane/conversation/bare name + 3-column UPDATE [wave 1-S1])"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/host.hy (RPC dispatch + counters + wire derivation + session.by_conversation [wave 1-S1])"
          "packages/doeff-agents/conformance/test_s23..s27 (black-box checks — can be moved to the koine side in the future. S23/S25 pin the wave 1-S1 revision)"])
