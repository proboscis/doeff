;;; Executable ADR: detection and bounded compensation of an unsubmitted prompt + a single sweep of terminal sessions
;;; (root fix of issue #568 — codifies, in the same change, the 2 remaining holes of #582/#583 [rescue-key sending and
;;; report reminders without destination verification] and the accompanying ruling [making cleanup a single sweep]).

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-010
  :title "An unsubmitted prompt is detected over the whole composer region, and compensation is a bounded budget + a loud typed terminal on exhaustion (infinite blocked is structurally forbidden). The awaiting_response latch has a deadline (the window restarts on an observation cut). The monitor sends only to a destination whose pane-to-session ownership is verified every cycle. Substrate cleanup (tmux kill + cleaned_at) is owned by a single sweep that picks up all terminal rows, and per-terminal-path cleanup commands are forbidden"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/markers.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/substrate.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/effects.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/store.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/host.hy"
          "packages/doeff-agents/src/doeff_agents/_tmux.py"
          "packages/doeff-agents/conformance/README.md"]
  :problem
    [(fact
       "On 2026-07-28, the launch prompt of ACP invocation inv_wi_dbaf5df9a812dba_a1 stayed unsubmitted in the composer as an [Image #150] attachment chip, and the session sat silent in blocked for 2 hours without a turn ever starting. has-unsubmitted-paste (markers.hy) and _output_has_unsubmitted_paste_input (tmux.py) looked only at the single last prompt line and were blind to the attachment chip drawn outside the prompt line (on the line directly below) — all 17 Enter resends were ineffective, and once the pane morphed into a shape outside the vocabulary, the compensator went completely silent."
       :evidence "proboscis/doeff issue #568 body (agentd DB agent_session_events session_id agent_inv_wi_dbaf5df9a812dba_a1, pane capture /tmp/wedged-attend-pane-full.txt)")
     (fact
       "On 2026-08-06, seats with the same kind of unsubmitted prompt fully saturated all 7 ACP runner seats (14.7 to 44.7h occupied, silent after 32 / 13 resends, zero conversation records on all 7 seats), and the following 66 items were stalled for up to 7 days. The 3 termination mechanisms (launch timeout, turn-end detection, idle timeout) are all disabled at once by the same look as a healthy seat, 'the input box is displayed' — time structurally cannot resolve it."
       :evidence "agora artifact analysis-acp-responsibility-sessionhost-steward-2026-08-06.html (full measurement of all 7 seats + population of 6,771 rows), ACP decision row sandbox:responsibility:sessionhost-steward gen14")
     (fact
       "awaiting_response=1 disables the stall watchdog and turn-end detection wholesale (#582 hole c). The latch is released only by observing positive work evidence and has no deadline, so on the path 'prompted but never answers' the declaration 'infinite waiting is forbidden' (policy.hy R5/R7) lapses."
       :evidence "proboscis/doeff issue #582/#583 (2026-08-01 ep9: permanently idle with status=blocked, awaiting_response=1, budget exhausted) — merged into #568 (merge comment 2026-08-06)")
     (fact
       "The monitor's rescue-key sending (send-unblock-keys) and report-reminder delivery (deliver-message) send blindly to row.pane_id, and nobody verifies the destination pane's session ownership (#582 holes a/b). Measured: a rescue key pushed the conversation to the background screen (loss of the reporting tool), and a reminder text spawned a new bg session (misdelivery 1/240). Pane numbers are reused (1,483 collisions measured in the ledger)."
       :evidence "proboscis/doeff issue #582 (analysis-acp-invariant-attention-verdict-stale-2026-08-01-ep9.html appendix F, second-level match at 2026-08-01 11:12Z)")
     (fact
       "Substrate cleanup (tmux-kill-session + cleaned_at) was attached only to finalize's done/failed observation path; 4 of the 5 terminal paths (launch timeout, boot leftover, former observation cut, bare-shell return) make the row terminal and exit with (return row), so cleanup was never called. Measured: 26 leftovers, 6.155 GiB (stamping rate done 100% / failed 83.5% / exited 2.9% / stopped 1.8%)."
       :evidence "analysis-acp-responsibility-sessionhost-steward-2026-08-06.html appendix 'Why cleanup is not called' (1:1 matching of the 26 leftovers against how they ended)")]
  :context
    [(interpretation
       "Type of detection: the evidence of non-submission is drawn not on 'the prompt line' but in 'the composer region' (the last prompt line and the lines after it). The attachment chip [Image #N] is drawn directly below the prompt line with leading whitespace, so scanning the single prompt line is structurally blind. Conversely, everything above the last prompt line is submitted history and out of scope (history chips are not misdetected as unsubmitted).")
     (interpretation
       "Type of compensation (fail-fast): a compensator (Enter resend) always declares a bounded budget, and exhaustion is a loud typed terminal failure (retryable=true — an unsubmitted prompt is a transport-level transient, and retrying the attempt does not produce a duplicate implementation). Silent blocked is forbidden. [2026-08-12 relation entry (ADR-DOE-AGENTS-011)] Since the launch-stage ready gate now has 'pasteability (probe consumed and cleared)' as a necessary condition, this arm takes the position of a backstop for shapes that got through the pre-delivery gate — the main rule (do not extend the launch-side synchronous confirm) is unchanged; what 011 added is a stage before delivery, not an extension of confirm. Behavioral terminals look at provider-limit observation first (the same distillation as ACP ADR 0049 R9 — rate_limited if blocked_api has been latched during the attempt).")
     (interpretation
       "Type of the awaiting latch: 'a prompt is owed to the agent' is a proposition with a deadline. If positive work evidence (active marker / turn activity) does not arrive within the deadline, it is a typed terminal — this is a level-triggered implementation of issue #568 expected behavior (c) (submission confirmation requires positive evidence of a turn start), and deliberately rejects the shape that extends the launch-side synchronous confirm (widening the blind window of hazard 4). The deadline's base restarts on an observation cut (isomorphic to ADR-DOE-AGENTS-009 R2 — a window that could not be observed voids the premise 'observed continuously and found no evidence').")
     (interpretation
       "Type of destination: pane_id is a reused number, and sending keys or messages without verifying session ownership structurally allows 'sending into someone else's seat'. Ownership verification is done not as a guard per sending site but in a single arm at the observation entrance (every cycle) — all downstream sends of that cycle (Enter resend, dialog dismiss, rescue keys, reminder delivery) are bound to the same verified destination. An ownership mismatch is active evidence obtained while tmux answers = vanished (the second-evidence class of ADR-DOE-AGENTS-009).")
     (interpretation
       "Type of cleanup (mechanism vs policy): adding a cleanup command per terminal path leaks again on the next path (measured: 4 of 5 paths leaked). Cleanup is owned by 'a single sweep that picks up every row that reached a terminal state' = a declarative reconciler that observes the end-state of being terminal. The inline cleanup of the launch ready gate (launch.hy) remains as an early optimization owned by the launch pipeline — its failures (leftover cleaned_at NULL) are also repaired by the sweep. The sweep also collects the existing leftovers (26) by the same mechanism — no manual bulk cleanup is needed.")
     (interpretation
       "Safety clauses of the sweep: (a) it inherits the reaping exemption (ADR-DOE-AGENTS-007 safety clause 1) — targets are only terminal rows that are run_to_completion and not adopted. (b) Session names are numbered by the caller and may be reused over time — when an active row claims the same name, do not kill, only stamp cleaned_at (do not kill a live new seat by the name of an old leftover). (c) cleaned_at means 'the sweep confirmed there is no leftover' — it is stamped even on rows whose tmux session is already gone (the target set converges boundedly, allowing a rescan every cycle).")]
  :decision
    [(rule R1 "Unsubmitted detection looks at the whole composer region: has-unsubmitted-paste (markers.hy), unsubmitted-paste-input? (substrate.hy), and _output_has_unsubmitted_paste_input (tmux.py) scan the last prompt line (❯ / › / input:) in the tail window and the lines after it, and detect [Pasted text / [Pasted Content / [Image #N chips / Press up to edit queued messages as unsubmitted markers. Everything above the last prompt line (submitted history) stays out of scope.")
     (rule R2 "The paste-resend compensator is bounded: it has SessionRow.paste_resubmit_attempts (durable counter) and the knob paste-resubmit-limit (frozen default 5, env DOEFF_AGENTD_PASTE_RESUBMIT_LIMIT); within budget it resends Enter + a session_unsubmitted_paste_resubmitted event, and on exhaustion it is a typed terminal failure (status=failed, reason prefix `unsubmitted-prompt:`, cause prompt_undelivered retryable=true, rate_limited if the api-limit latch is set). [2026-08-12 revision (ADR-DOE-AGENTS-011 R-undelivered-first-class-b5e8)] The cause category on exhaustion is changed from timed_out to prompt_undelivered — the rows this arm terminates never had a turn start (turn-activity marker absent in all 25 measured cases), so it is the same proposition as non-delivery at the launch-stage gate, and splitting it across 2 categories splits the non-delivery counts. retryable=true and the api-limit look-ahead distillation are unchanged. A revision that changes this category ships together with ADR-DOE-AGENTS-011.")
     (rule R3 "The awaiting_response latch has a deadline: it has SessionRow.awaiting_response_since (updated on solicitation re-arm, cleared together with the latch on positive work evidence) and the knob awaiting-response-timeout-seconds (frozen default 600, env DOEFF_AGENTD_AWAITING_RESPONSE_TIMEOUT_SECS). Deadline base = max(awaiting_response_since | started_at, observation_gap_at). Exceeding it is a typed terminal failure (status=failed, reason prefix `awaiting-response timeout:`, cause timed_out retryable=true, rate_limited if the api-limit latch is set). This makes 'infinite blocked' structurally impossible (root fix of #582 hole c). [2026-08-09 relation entry (ACP ADR 3d0ff3 wait-bound-declared-ownership)] This timeout is the first layer (owner) of the two-layer 'waiting for a reply' structure — the blocked-input-dwell bound on the ACP ledger side (default 1800s) is a backstop for when the sessionhost verdict does not arrive over the observation path, and the ordering invariant = the effective value of this knob < the ACP backstop bound. A change to this knob's frozen default (600) ships together with a revision of the value registered in ACP ADR 3d0ff3 (SESSIONHOST-FIRST-LAYER-TIMEOUT-SECONDS).")
     (rule R4 "Ownership verification of the destination pane: every cycle, right after the tmux liveness check, the monitor observes the set of panes owned by row.session_name with TmuxSessionPaneIds (tmux list-panes -s / for herdr, the agent's pane resolution — both substrates implement the same contract), and if row.pane_id does not belong to it, terminates immediately as exited + cause vanished (the reason names the pane and the session). All subsequent sends (Enter resend, dialog dismiss, rescue keys, reminder delivery) go only to a destination that passed this verification (root fix of #582 holes a/b). Accompanying contract revision: the shape 'pane killed out of band while the session is alive' (which the former conformance S19c used to simulate a supply cut) is promoted to loss of ownership observed while the substrate answers = death with second evidence — a supply cut (the hold + gap stamp of ADR-DOE-AGENTS-009 R1) is limited to the shape where probe/capture fails without an answer, and the home of its verification moves to the hy deftest gate.")
     (rule R-blocked-needs-no-work-evidence-4f1c "The waiting arm of the observed status is a conjunction with work evidence: the frozen classification order of observed-status-from-markers (policy.hy) is failure → api-limit → (waiting ∧ ¬active) → running, and an observation in which a live active marker (claude spinner / codex working line) is visible must not be classified as blocked. Reason = of the 7 words that has-waiting-marker judges on, accept edits / bypass permissions / shift+tab to cycle are the permission-mode indicator of the current claude TUI, i.e. a permanent footer drawn at all times even while working (and while other markers use a tail 30-line window, only waiting matches the whole capture), and alone does not mean 'waiting for input'. has-turn-activity (⏺ / ⎿) is not included in the conjunction — it is a trace that remains even on an idle screen, not live work evidence (markers.hy states it is limited to latch clearing and lifting the startup watchdog), and including it would mean a claude seat that worked even once never becomes blocked again, making a truly frozen seat invisible. Marker detection (owned by impl) is unchanged — only classification (owned by policy) changes, and PaneObservation carries both facts side by side as before. The source of truth for the behavior contract (the F-waiting row of the F-* table in the conformance README) is revised in the same change. [Relation to R3] The first layer of 600s measures 'prompted but no positive work evidence arrives', and this rule forbids 'calling itself blocked while work evidence is arriving' — two uses of the same work evidence; this rule guarantees the meaning of the status that the second layer (ACP backstop) reads.")
     (rule R5 "Substrate cleanup is owned by a single sweep: after the per-session loop, monitor-cycle sweeps all rows that are terminal status and cleaned_at IS NULL and run_to_completion and not adopted (SessionStoreListCleanupPending) — if the tmux session is alive it kills it (session_cleaned event); if an active row claims the same name it does not kill; in both cases it stamps cleaned_at. The inline cleanup in finalize is removed, and adding a cleanup command to the monitor's terminal arms is forbidden (semgrep doeff-agents-terminal-cleanup-single-sweep). The inline cleanup of the launch ready gate (launch.hy) remains, owned by the launch pipeline, and its failures are repaired by the sweep.")]
  :laws
    [(law composer-region-owns-unsubmitted-vocabulary
       :statement "unsubmitted_detection(output) scans composer_region(last_prompt_line ..); attachment_chips_below_prompt_are_unsubmitted; history_above_last_prompt_is_not"
       :counterexamples
         [(counterexample "2026-07-28 real wedge: an empty ❯ prompt + the `  [Image #150]` chip directly below it misanswered as 'submitted' — all 17 resends ineffective, silent for 2h (the old implementation looked only at the single prompt line)")
          (counterexample "Read even history chips above the last prompt line (a submitted `❯ [Image #3]` line) as unsubmitted — the reverse misdetection that keeps pouring Enter into a healthy seat")])
     (law unsubmitted-prompt-compensation-is-bounded
       :statement "resubmit_compensation(row) => budgeted(paste_resubmit_limit) and exhaustion_is_loud_typed_terminal(retryable=true); silent_blocked_is_forbidden"
       :counterexamples
         [(counterexample "2026-08-06 full runner saturation: silent after 32 / 13 resends, then blocked without a sound for 2 days — the compensator had neither a count limit nor a terminal on failure, and 7 seats stalled 66 items for 7 days")])
     (law awaiting-latch-has-deadline
       :statement "awaiting_response(row) => bounded_wait(base=max(awaiting_response_since|started_at, observation_gap_at), awaiting_response_timeout) ; no_work_evidence_within_deadline => typed_terminal"
       :counterexamples
         [(counterexample "#582 ep9 (2026-08-01): awaiting_response=1 disabled the stall watchdog and turn-end detection, and a dead seat with an exhausted budget stayed permanently idle with finished_at=None — the supervisor kept observing every second, yet there were zero paths leading it to a terminal")
          (counterexample "Count an observation-cut window toward the deadline — wrongly terminating as timed_out a row whose evidence structurally cannot arrive due to a supply cut (a violation of the ADR-DOE-AGENTS-009 type axiom; the window restarts on a gap)")])
     (law blocked-status-requires-absence-of-work-evidence
       :statement "observed_status(obs) = blocked => waiting_marker(obs) ∧ ¬active_marker(obs) — a permanent UI string is not evidence of 'waiting for input'. And ¬(turn_activity ∈ conjunction): counting a residual marker as work evidence makes blocked unreachable"
       :counterexamples
         [(counterexample "Measurement for the 2026-08-12 refinement (the population is taken by the cutoff reason text — 'dwelled blocked (awaiting input)' in the failure-reports of both roots matched against agentd, 186 seats, 186/186 matched): **175 seats (94%) had a changing screen up to 60 seconds before death** — seats in the middle of running 80-90k tokens were cleaned up as 'seats whose instructions never arrived'. **Seats that had been idle beyond the bound: 0**. ⚠ Cutting by the lifetime band (1860..2000s) captures only 168 of 186, and narrowing by status to failed/exited drops them because dwell cutoffs land as stopped. The actual text = the capture at death of session agent_inv_wi_5f3fa0e22242b74d_a1, '✶ Whatchamacalliting… (31m 5s · ↓ 91.4k tokens)' + the permanent footer '⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt'")
          (counterexample "Put has-turn-activity (⏺ / ⎿) into the conjunction: a claude seat that worked even once keeps the trace on an idle screen and never becomes blocked again, so the 7 seats frozen on 2026-08-05..06 (22h27m of zero production, empty composer) become invisible as 'running' in status — over-correction breaks the very premise of the upstream backstop")])
     (law delivery-target-must-be-owned
       :statement "monitor_send(keys|message, pane) requires verified(pane belongs_to row.session_name, this cycle); ownership_mismatch_is_evidenced_vanish"
       :counterexamples
         [(counterexample "#582 hole a (2026-08-01 11:12:17Z): rescue keys sent to a frozen screen pushed the conversation to the background screen (matching to the second the start of claude daemon run --origin transient), and lost the reporting tool (doeff_result of --strict-mcp-config)")
          (counterexample "#582 hole b: a reminder text landed on the 'describe a task for a new session' entrance after backgrounding and spawned a new bg session (misdelivery 1/240) — contributing to the accident where a dead seat emitted a 'live heartbeat' for 60 minutes")])
     (law terminal-cleanup-single-sweep
       :statement "substrate_cleanup(tmux_kill + cleaned_at) is owned by one sweep over all terminal ∧ uncleaned ∧ run_to_completion ∧ ¬adopted rows; per-arm cleanup commands are banned; sweep_must_not_kill(name claimed by active row)"
       :counterexamples
         [(counterexample "Build cleanup only into the done/failed observation path (the old finalize) — 4 of 5 terminal paths leaked, and 26 leftovers / 6.155 GiB accumulated (measured: the stamping rate splits into 100% and 0% by path)")
          (counterexample "Add one cleanup command to the leaking path (launch timeout) — it leaks again on the next path (bare-shell return, a newly added arm). An explicitly rejected shape")
          (counterexample "When a caller-numbered session name is reused, fire kill by the name of an old leftover row — killing a live new seat with the same name (while an active row claims the same name, only stamp cleaned_at)")])]
  :enforcement
    [(deftest test-adr-doe-agents-010-knobs-and-fields-frozen
       ;; The mechanical face of R2/R3: the budget knobs and durable fields exist with their frozen defaults.
       (import doeff_agents [SessionRow]
               doeff_agents._sessionhost.effects [MonitorKnobs])
       (setv knobs (MonitorKnobs))
       (assert (= knobs.paste-resubmit-limit 5))
       (assert (= knobs.awaiting-response-timeout-seconds 600))
       (setv row (SessionRow :session-id "adr10" :session-name "adr10" :pane-id "%0"
                             :agent-type "claude" :lifecycle "run_to_completion"
                             :status "running"
                             :started-at "2026-08-06T00:00:00+00:00"))
       (assert (= row.paste-resubmit-attempts 0))
       (assert (is row.awaiting-response-since None)))
     (deftest test-adr-doe-agents-010-cleanup-single-home
       ;; The mechanical face of R5: policy.hy has exactly one tmux-kill-session call site, inside the sweep
       ;; function — regression into finalize / terminal arms is forbidden.
       (import pathlib [Path])
       (import re)
       (import doeff_agents._sessionhost.policy :as policy-mod)
       (setv source (.read-text (Path (. policy-mod __file__)) :encoding "UTF-8"))
       (setv call-sites (.count source "(tmux-kill-session"))
       (assert (= call-sites 1)
               f"policy.hy must contain exactly one (tmux-kill-session call site (the sweep), got {call-sites}")
       ;; Split into top-level definition blocks and confirm the kill site is in the sweep
       ;; function's block (fail if it is inside finalize).
       (setv blocks (re.split r"(?m)^\(" source))
       (setv owner-blocks (lfor block blocks
                                :if (in "(tmux-kill-session" block)
                                block))
       (assert (= (len owner-blocks) 1))
       (setv owner (get owner-blocks 0))
       (assert (.startswith owner "defk cleanup-terminal-session-once")
               "tmux-kill-session must live inside the sweep (cleanup-terminal-session-once)")
       (assert (not-in "(tmux-kill-session" (get (re.split r"(?m)^\(defk cleanup" source) 0))
               "no tmux-kill-session call before the sweep definition (e.g. inside finalize)"))
     (deftest test-adr-doe-agents-010-composer-region-vocabulary
       ;; The mechanical face of R1: the real wedge's pane shape (the [Image #N] chip outside the prompt line)
       ;; is read as unsubmitted by all 3 implementations (markers.hy / substrate.hy / tmux.py).
       (import doeff_agents._sessionhost.impls.markers [has-unsubmitted-paste])
       (import doeff_agents._sessionhost.substrate [unsubmitted-paste-input?])
       (import doeff_agents._tmux [_output-has-unsubmitted-paste-input])
       (setv wedge "❯\n  [Image #150]\n\n  ⏵⏵ bypass permissions on (shift+tab to cycle)")
       (assert (has-unsubmitted-paste wedge))
       (assert (unsubmitted-paste-input? wedge None))
       (assert (_output-has-unsubmitted-paste-input wedge))
       ;; Chips in submitted history (above the last prompt line) stay out of scope.
       (setv history "❯ [Image #3]\n⏺ done\n❯")
       (assert (not (has-unsubmitted-paste history)))
       (assert (not (unsubmitted-paste-input? history None)))
       (assert (not (_output-has-unsubmitted-paste-input history))))
     (deftest test-adr-doe-agents-010-blocked-needs-no-work-evidence
       ;; The mechanical face of R-blocked-needs-no-work-evidence-4f1c (the law's conjunction itself).
       ;; Classification is a pure function, so PaneObservation is built directly and pinned — marker
       ;; detection from a real pane is pinned literally by the impls-side deftest
       ;; (test-classify-claude-working-pane-carries-both-marker-facts), and the behavior
       ;; including the cycle is pinned by the policy-side deftest
       ;; (test-working-pane-with-waiting-footer-stays-running).
       (import doeff_agents [PaneObservation])
       (import doeff_agents._sessionhost.policy [observed-status-from-markers])
       ;; Only the permanent footer (waiting) = blocked as before.
       (assert (= "blocked"
                  (! (observed-status-from-markers
                       (PaneObservation :has-waiting-marker True)))))
       ;; A working pane (waiting ∧ active) is not made blocked — 175 of the 186 cut-off seats.
       (assert (= "running"
                  (! (observed-status-from-markers
                       (PaneObservation :has-waiting-marker True
                                        :has-active-marker True)))))
       ;; The residual marker (turn-activity) is not in the conjunction — over-correction is forbidden.
       (assert (= "blocked"
                  (! (observed-status-from-markers
                       (PaneObservation :has-waiting-marker True
                                        :has-turn-activity True)))))
       ;; The higher classification order is unchanged (failure / api-limit win even with active).
       (assert (= "failed"
                  (! (observed-status-from-markers
                       (PaneObservation :has-failure-marker True
                                        :has-active-marker True)))))
       (assert (= "blocked_api"
                  (! (observed-status-from-markers
                       (PaneObservation :has-api-limit-marker True
                                        :has-active-marker True))))))
     (defsemgrep per-arm-cleanup-is-banned
       "doeff-agents-terminal-cleanup-single-sweep"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
         "source" "(when (and (is-run-to-completion row.lifecycle)\n           (in observed-status #{\"done\" \"failed\"}))\n  (<- still-alive (tmux-has-session row.session-name))\n  (when still-alive\n    (<- _ (tmux-kill-session row.session-name))\n    (setv row (replace row :cleaned-at (or row.cleaned-at observed-at)))))\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
         "source" "(defk cleanup-terminal-session-once [row active-names]\n  (<- alive (tmux-has-session row.session-name))\n  (when (and alive (not-in row.session-name active-names))\n    (<- _ (tmux-kill-session row.session-name)))\n  (setv row (replace row :cleaned-at (or row.cleaned-at observed-at)))\n  row)\n"}])]
  :plans ["docs/adr/defadr_doeff_agents_010_unsubmitted_prompt_terminal_sweep.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/markers.hy (R1 composer region)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/substrate.hy (R1 confirm-loop face + TmuxPaneSessionName real IO)"
          "packages/doeff-agents/src/doeff_agents/_tmux.py (R1 legacy transport face)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/effects.hy (R2/R3 knobs and fields + TmuxPaneSessionName / SessionStoreListCleanupPending)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy (R2 bounded resend + R3 deadline arm + R4 ownership-verification arm + R5 sweep, removal of finalize inline cleanup)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/store.hy (2 additive columns + cleanup-pending listing + clearing since together with the latch)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/host.hy (2 env knobs)"
          ".semgrep.yaml (doeff-agents-terminal-cleanup-single-sweep)"
          "packages/doeff-agents/conformance/README.md (knob table, cleanup contract revision)"])
