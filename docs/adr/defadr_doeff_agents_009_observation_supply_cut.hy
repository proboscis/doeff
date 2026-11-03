;;; Executable ADR: observation cut != death — remove the sessionhost watchdog's terminal lost +
;;; split the lost→vanished vocabulary + accept a late result after exited (the doeff-side root fix
;;; of ACP ADR 0067 R6).

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-009
  :title "Observation cut != death: a gap in observation is a proposition about the observation path (supply), not a proposition that the observed target died — the stale-observation watchdog does not terminalize but records observation_gap_at and delegates the verdict to the second-evidence arm; evidenced death terminates immediately as vanished (retryable=true), split off from lost; and a late report_result for the death-verdict class (exited) is accepted result-first and overwritten to done"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/host.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/store.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/effects.hy"
          "packages/doeff-agents/conformance (S9 / S19)"]
  :problem
    [(fact
       "2026-07-27 20:10-21:01 JST, during the observation-cut window of the sessionhost store-bloat wedge (root-fixed in doeff PR #564), the stale-observation watchdog stamped a terminal cause=lost{reason=\"no monitor observation for more than 300s\", retryable=true} on every session without checking liveness, and the invocation of an agent still doing real work in tmux (wi_ee07c6a71fa945a5) was treated as dead downstream (ACP engine). The same signature has recurred systematically 9 times since 2026-07-11."
       :evidence "ACP event store sequence 3640543/3640544, 3640713/3640714; ~/.local/state/doeff/sessionhost-wedge-sample-20260727.txt; ACP docs/adr/defadr_0067_observation_supply_cut_is_not_death.hy :problem")
     (fact
       "The lost category folds 'watchdog observation cut' (no evidence of death) and 'tmux session disappeared / pane returned to idle shell' (death with second evidence) into the same vocabulary (cause=lost, retryable=true), indistinguishable by structured fields. Since ACP ADR 0067 made the engine put cause.category=lost uniformly into an AgentUnobserved hold (non-terminal), even evidenced death waits for the deadman gate and recovery is slow — the vocabulary split is an ACP-side tracked gap (defadr_0067 rule R6)."
       :evidence "ACP src/Acp/App/Agent/Handler/Agentd.hs decodeSnapshotStatus(category==\"lost\" → StatusLost); doeff policy.hy, the 3 former make-cause \"lost\" sites")
     (fact
       "When a real agent calls report_result on a session terminalized by a false lost, it is rejected with 'already reached terminal status ... without a result' (-32003), and the implementation result is lost together with the result channel. Measured 2026-07-27: the implementation result of wi_ee07c6a71fa945a5 became unrecoverable."
       :evidence "host.hy report-result-op, the former terminal-rejection arm; ACP exploration log #149")
     (fact
       "Making only the stale-observation arm fall through leaves an isomorphic defect in the launch-timeout watchdog: the premise 'observed continuously for launch-timeout seconds since started_at and never saw active' becomes unverifiable in an observation-cut window (the active marker flows away during the wedge). In the wedge recovery cycle it wrongly terminates live agents with observed_active_at=None as timed_out (retryable=true), releasing ACP's transient auto-retry = duplicate implementation alongside the live agent."
       :evidence "policy.hy launch-timeout arm (the premise depends on continuity of observation); ACP defadr_0067 :context type axiom")]
  :context
    [(interpretation
       "Type axiom (identical to ACP ADR 0067): 'monitor observation stopped' is a proposition about the observation path (supply), not a proposition that the observed target (agent) died. In a supply-cut window all agents appear to vanish at once (systematic misjudgment), so the fail-safe is supply-cut != death — an observation cut is unknown (insufficient material to judge) and is not terminalized. The ACP side is the engine's fail-safe (never read unobservability as death under any observation-path failure); this ADR is the root fix on the doeff side, which owns observation quality; the two hold independently.")
     (interpretation
       "Definition of second evidence: (a) a negative answer from the tmux liveness probe (has-session answers and the session is absent — an absent server also means the pane really died), (b) the zombie reaper (pane foreground returned to an idle shell — an active observation made while tmux answers), (c) result arrival (direct evidence of work completion). A failure of the probe itself (exception, hang) is a supply cut, not evidence — it is held with per-session isolation, and boundedness is closed by the engine-side deadman gate (ACP ADR 0059: invocation age → InvocationStalled → operator gate). doeff does not own a grace-period terminalization of its own (same reason as rejected alternative 2 of ACP ADR 0067: delayed termination of a row whose observation structurally never comes is a delayed version of the same misjudgment).")
     (interpretation
       "Downstream compatibility of the vocabulary split: ACP decodeSnapshotStatus maps only cause.category==\"lost\" to StatusLost (hold), and drops every other exited into StatusExited (terminal). vanished reaches immediate termination through the existing structural branch alone — no ACP-side code change is needed. retryable=true is kept (retrying an attempt whose death is confirmed does not produce a duplicate implementation). lost disappears from doeff's construction vocabulary (the frozen-table lookup in make-cause rejects it) — a supply cut no longer appears on the wire as a terminal cause.")
     (interpretation
       "Scope of late-result acceptance: the rescue applies only to the death-verdict class (status=exited — vanished / former lost). Judged failures (failed: solicitation exceeded, stall, launch timeout) and operator verdicts (cancelled / stopped) are still rejected with -32003 as before, since accepting them would rewrite a verdict. Accepting on exited is the completed form of ADR 0035 (result-first): whenever report_result lands, the result wins — a death verdict is an inference that 'it vanished without producing a result', and the arrival of a schema-conforming result is the very refutation of that inference. The store's terminal-reactivation guard (ADR-DOE-AGENTS-006) forbids only terminal→active, and the terminal→terminal overwrite exited→done is owned by this ADR as the only legal path.")]
  :decision
    [(rule R1 "The stale-observation watchdog does not terminalize: on detecting an observation cut (max(last_observed_at, observation_gap_at) exceeds the threshold) it stamps observation_gap_at (additive column), records a session_observation_gap event once, and falls through to the normal arms (the detection condition re-arms on gap_at itself, so the event rate is bounded by 1/stale-secs — the PR #564 journal bloat does not recur). The death verdict is given by the second-evidence arm right after it (tmux liveness probe → result-first / zombie reaper). If even the probe is impossible, the row stays running (unknown is held).")
     (rule R2 "The launch-timeout watchdog's watch window restarts on an observation cut: base = max(started_at, observation_gap_at). An observation cut voids the premise 'observed continuously and never saw active' — after supply recovers, judge anew by launch-timeout seconds of continuous observation (a genuinely stuck session is reaped after recovery as before = bounded).")
     (rule R3 "Evidenced death is vanished (retryable=true): the 2 sites tmux session disappeared / pane returned to idle shell use category=vanished. lost is removed from the TERMINAL-CAUSE-RETRYABLE frozen table, bringing its construction paths to 0 (make-cause's precondition raises AssertionError on the table lookup). The reason wording is unchanged (do not create material that breaks the downstream ban on reason reclassification — ACP ADR 0042).")
     (rule R4 "Accepting a late result (rescue path): a report_result for status=exited with no persisted result and with a contract is accepted after schema validation; within a single actor op it overwrites status=done, persists result_payload, clears last_validation_error / terminal_cause, sets finished_at=acceptance time, records a session_late_result_accepted event, and returns {\"accepted\": true, \"late_result\": true}. For RunToCompletion rows, after acceptance the RPC layer (outside the actor) performs the substrate cleanup equivalent to finalize (tmux kill + cleaned_at). failed / cancelled / stopped / done (without payload) are -32003 as before. A schema mismatch is -32002 + session_result_rejected as before.")]
  :laws
    [(law unobservability-never-terminalizes
       :statement "stale_observation(session) => no_terminal_write and gap_recorded; death_verdict_requires_second_evidence(tmux_probe_negative or zombie_shell or confirmed_absence)"
       :counterexamples
         [(counterexample "2026-07-27 wedge: on recovery from the observation-cut window, the watchdog stamped a lost terminal on an agent alive in tmux (wi_ee07c6a71fa945a5 — started 19:27, measured alive at 21:3x), and the ACP engine translated it to InvocationFailed — had retry-impl been pressed, it would have been a duplicate implementation alongside the live agent")
          (counterexample "Record the gap event every cycle (without edge-triggering) — a recurrence of the PR #564 incident, where rows sitting still in blocked poured into the journal and bloated agentd.sqlite to 1.5GB")])
     (law launch-premise-voided-by-gap
       :statement "launch_timeout_reap(row) => continuous_observation_window(max(started_at, observation_gap_at), launch_timeout_secs); gap_restarts_window"
       :counterexamples
         [(counterexample "Fire launch-timeout in the wedge recovery cycle with started_at still as the base — wrongly terminating a live agent handed to running just before the wedge (observed_active_at not yet stamped) as timed_out (retryable=true), releasing ACP's transient auto-retry = duplicate implementation (the isomorphic defect exposed by removing lost)")])
     (law death-vocabulary-is-split
       :statement "terminal_cause_category(evidenced_death) = vanished and retryable(vanished) = true; lost is not constructible"
       :counterexamples
         [(counterexample "Ship evidenced death still as lost — the ACP ADR 0067 engine hold makes even attempts with confirmed death wait for the deadman gate, delaying recovery until the invocation deadline")
          (counterexample "Ship an observation cut as a terminal of a new category (lost under a new name) — exactly a violation of the supply-cut != death type axiom")])
     (law result-arrival-overrides-death-verdict
       :statement "report_result(status=exited, valid_payload, has_contract) => accepted and status=done and cause_cleared; judged_failures_and_operator_verdicts_stay_rejected"
       :counterexamples
         [(counterexample "2026-07-27 wi_ee07c6a71fa945a5: the report_result after a false lost was rejected with -32003, and the implementation result was lost together with the result channel")
          (counterexample "Extend acceptance to failed (solicitation exceeded) or cancelled as well — rewriting a judged failure / operator verdict is a different law, and this ADR owns only the death-verdict class")])]
  :enforcement
    [(deftest test-adr-doe-agents-009-lost-is-not-constructible
       ;; The mechanical face of R3: lost is gone from the frozen table, and make-cause rejects it.
       ;; vanished is in the table with retryable=true.
       (import doeff_agents._sessionhost.policy [TERMINAL-CAUSE-RETRYABLE make-cause])
       (import pytest)
       (assert (not-in "lost" TERMINAL-CAUSE-RETRYABLE))
       (assert (= (get TERMINAL-CAUSE-RETRYABLE "vanished") True))
       (with [(pytest.raises AssertionError)]
         (make-cause "lost" "x" "2026-07-28T00:00:00+00:00")))
     (deftest test-adr-doe-agents-009-gap-field-exists
       ;; Wiring pin for R1/R2: observation_gap_at really exists on SessionRow and as a store column.
       (import doeff_agents [SessionRow])
       (setv row (SessionRow :session-id "adr9" :session-name "adr9" :pane-id "%0"
                             :agent-type "claude" :lifecycle "run_to_completion"
                             :status "running"
                             :started-at "2026-07-28T00:00:00+00:00"))
       (assert (is row.observation-gap-at None)))
     (defsemgrep lost-cause-is-banned
       "doeff-agents-no-lost-terminal-cause"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
         "source" "(setv row (cause-if-absent\n            row (make-cause \"lost\" \"tmux session disappeared\" observed-at)))\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
         "source" "(setv row (cause-if-absent\n            row (make-cause \"vanished\" \"tmux session disappeared\" observed-at)))\n"}])]
  :plans ["docs/adr/defadr_doeff_agents_009_observation_supply_cut.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy (stale arm fall-through + launch window restart + vanished)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/effects.hy (SessionRow observation-gap-at)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/store.hy (additive column + COALESCE(excluded, existing))"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/host.hy (report-result-op rescue arm + RPC-layer cleanup)"
          "packages/doeff-agents/conformance/README.md (TerminalCause table, S9/S19 revision)"
          "packages/doeff-agents/conformance/test_s19_watchdogs.py / test_s9_out_of_band_kill.py"])
