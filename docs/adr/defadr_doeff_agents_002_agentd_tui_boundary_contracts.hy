;;; Executable ADR for doeff-agentd's tmux/TUI boundary correction loops.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-002
  :title "agentd TUI boundary: bounded result solicitation + interactive-prompt watchdog"
  :status "accepted"
  :scope ["doeff-agentd" "agentd.monitor" "agentd.result-contract" "agentd.tui-boundary"]
  :problem
    [(fact
       "At 2026-07-04 01:55 JST, an unattended codex attend session stopped at a rate-limit confirmation menu (`Approaching rate limits / Switch to gpt-5.4-mini …? › 1. Switch… 2. Keep current model … Press enter to confirm`). It was a confirmation menu, not a hard block; the session died only because nobody was there to press Enter."
       :evidence "ACP argus dashboard attend #1 capture; ~/.codex/sessions/2026/07/04/rollout-…019f28e8….jsonl")
     (fact
       "At 2026-07-03 17:06 UTC, the impl agent for acp:issue:argus-git-mergeability-refresh reached turn-end 20 seconds after startup, and the invocation failed with `session reached turn-end without reporting a result via report_result`. The control plane reaped the WorkItem 6 minutes later, the dedup condition remained, and the issue became permanently impossible to re-declare (a real blocker for the dogfood e2e)."
       :evidence "proboscis-agent-control-plane/.acp/event-store.sqlite events sequence 1963559..1965600")
     (fact
       "ACP's investigation-agent independently diagnosed the same class: \"the implementation agent exited without returning an implementation result\" (acp:issue:issue-defect-bef7dcf947e98cdb). Missing-result recurs across namespaces."
       :evidence "acp:issue:issue-defect-bef7dcf947e98cdb resourcePayload.body")
     (fact
       "The codex menu is drawn with the same `› ` glyph as the idle REPL prompt (`output_has_agent_idle_prompt` matches `\\n› `, and the menu line `› 1. Switch…` matches too). The current heuristic cannot distinguish a blocking menu from turn-end."
       :evidence "agentd-rust-final:src/main.rs codex_update_dialog_selected_option / output_has_agent_idle_prompt")
     (fact
       "When there was no result at turn-end, the session failed immediately under 'agentd never re-prompts (hard rule 7 / ADR 0035 R4)'. This conflates a deterministic failure (schema-invalid payload, -32002) with an observation (no result at turn-end). As the history of ADR 0035 removing the retries_used re-prompt path shows, this distinction will be erased again unless it is written down."
       :evidence "agentd-rust-final:src/main.rs monitor_once turn-end branch (old comment)")
     (fact
       "The `awaiting_response` latch is cleared in bulk on daemon restart (the UPDATE in main()). If the bounded loop's count were carried on the latch, it would vanish on restart. The count has to live in a dedicated persistent column."
       :evidence "agentd-rust-final:src/main.rs main() awaiting_response reset")
     (fact
       "A stopped screen that does not match the idle prompt (login prompt, pager, unknown dialog) never reaches turn-end, and last_observed_at is updated every tick even when the pane capture does not change, so none of the existing watchdogs fire. The session occupies its slot forever."
       :evidence "agentd-rust-final:src/main.rs monitor_once (last_observed_at is updated only on capture success/failure)")
     (fact
       "On 2026-08-18 (4 hours after the claude CLI auto-updated to 2.1.234), a real case was observed where a running turn was not drawn in the pane: the snapshot observed 4-10 seconds after prompt submission stayed at 'empty ❯, no spinner, statusline shows waiting for first response…', while the conversation transcript for the same time recorded assistant responses and tool executions. The display-layer-only turn-end decision (idle ∧ ¬active ∧ tail500 stable — with a 1s monitor tick, one second of no redraw makes it stable) burned 22 running attend seats into run_failed / retryable=false in about 20 seconds. The failed snapshot of one of those seats still showed '❯ Press up to edit queued messages' in the composer (the state markers.hy defines as 'clear busy evidence that a turn is running'), yet concluded 'did not respond to the solicitation'."
       :evidence "cross-check of agentd.sqlite agent_session_events (output_snippet of session_result_solicited / session_blocked) × ~/.config/claude-*/projects/*/<sid>.jsonl, 2026-08-18 route-1dbd848847")]
  :context
    [(interpretation
       "agentd is the sole owner of the tmux/TUI boundary. Everything that appears as pixels and keystrokes is observed and operated by agentd; upstream (doeff-agents / ACP) sees only a typed status and the report_result data channel. Leaking the handling of TUI anomalies to upstream or to humans is a boundary violation.")
     (interpretation
       "The classification of ADR 0012 / ACP hard rule 7 also applies inside agentd: deterministic failures (schema-invalid payload) are not retried automatically. Observations (no result at turn-end, a frozen pane) are re-observed and corrected by a bounded loop with a persistent counter. Exceeding the bound ends in a typed terminal failure — an infinite wait is always forbidden.")]
  :decision
    [(rule R1 "A contract session reaching turn-end without a valid reported result is an observation, not a deterministic failure. agentd sends a corrective message saying 'call report_result exactly once, now' and re-observes the next turn-end (solicitation).")
     (rule R2 "Solicitation is bounded (default 2) by a persistent counter that survives restarts (the result_solicitations_used column). When exceeded, it terminates with the existing terminal-without-result failure, and the ACP 4-value discriminator is not changed.")
     (rule R3 "A schema-invalid report_result (-32002) stays deterministic: it is rejected immediately and the same payload is not automatically revalidated. However, if turn-end is subsequently reached without a valid result, the R1 solicitation loop is entered (the agent can fix the payload and report again).")
     (rule R4 "During solicitation the session status stays non-terminal. Whenever report_result lands, it wins under result-first reading. Making the status terminal during solicitation is forbidden (because the report would be rejected with -32003).")
     (rule R5 "For a run_to_completion session after startup has completed, when the pane content is unchanged for T seconds (default 180), with no active-work marker and no idle REPL prompt, the pane capture is passed to a configurable small LLM judge to obtain strict JSON {blocked, keys, reason}. If blocked, the keys are whitelist-validated and then sent via tmux. Attempts are bounded (default 3) by a persistent counter (the prompt_unblock_attempts column).")
     (rule R6 "At the turn-end-without-result decision point, the judge runs before solicitation. Because the codex menu draws the idle prompt glyph, pasting a solicitation into the menu makes Enter confirm an arbitrary option.")
     (rule R7 "At the stall decision point (R5), if the judge bound is exceeded, parsing fails, or the judge is unavailable, it fails loudly and typed (status=failed, last_validation_error starts with 'interactive-prompt-blocked:', TerminalCause is interactive_prompt_blocked). An infinite wait is forbidden. At the turn-end decision point (R6), if the judge is unavailable, it degrades to solicitation (which is bounded and does not hang).")
     (rule R8 "The ACP-side 4-value discriminator (ready / terminal-without-result / not-ready / session-gone) is unchanged. New failure details are carried in last_validation_error and TerminalCause (internal / audit use).")
     (rule R9 "A deterministic fast-path dismisser for known dialogs is allowed, but it must not be the only mechanism (the proposal to suppress prompts only by enumerating known prompts in configuration was rejected). The general path is the judge.")
     (rule R-conversation-evidence-65b8 "Whether a turn is alive is not settled by the display layer (pane rendering) alone — the conversation record (the per-kind transcript artifact: claude = projects jsonl / codex = rollout) is the data-layer evidence surface. (i) While the conversation record has been updated within the freshness window (conversation-quiescence-seconds, default 120), turn-end is not declared. (ii) While a solicitation we delivered sits unconsumed in the composer (queued-messages), 'did not respond to the solicitation' does not hold — it does not reach the budget-exceeded terminal state. (iii) If the conversation record advanced after delivery (mtime beyond the awaiting base time + margin seconds), the awaiting_response latch is cleared as positive work evidence — a turn start that was not drawn is not turned into 'turn never started'. (iv) The probe is a falsification surface, not a gate: rows missing identifying material (identity / conversation / work_dir), a missing artifact, or an unobservable state yield None and fall back to the existing display-layer physics, with zero behavior regression. Pane rendering is a non-contract surface that changes with CLI version updates — for the same reason the verbatim markers broke three times (2026-07-20 / 07-26 / 08-06), the turn liveness decision itself must not be coupled solely to the display layer.")]
  :laws
    [(law missing-result-is-observation-not-determinism
       :statement "turn_end_without_valid_result => bounded_solicitation before terminal_without_result"
       :counterexamples
         [(counterexample "a session with no result at turn-end is failed immediately without solicitation")
          (counterexample "solicitation is called a 'retry' and removed citing hard rule 7 (removing it requires superseding this ADR first)")])
     (law invalid-payload-stays-deterministic
       :statement "schema_invalid_report_result => immediate_-32002 and no_automatic_revalidation_of_same_payload"
       :counterexamples
         [(counterexample "agentd revalidates / retries the same payload on the spot at -32002")
          (counterexample "a validation failure is turned into an automatic re-prompt that does not consume the solicitation counter")])
     (law correction-counters-are-durable-and-bounded
       :statement "solicitation_count and unblock_attempts => dedicated_persistent_columns surviving daemon_restart"
       :counterexamples
         [(counterexample "the counter is carried on a latch that is cleared on restart, like awaiting_response")
          (counterexample "the counter is held only in memory, so solicitation restarts indefinitely on agentd restart")])
     (law solicitation-keeps-session-non-terminal
       :statement "during_solicitation => status_non_terminal so report_result_can_land"
       :counterexamples
         [(counterexample "status is set to failed at the same time the solicitation is sent, and the report_result that arrives is rejected with -32003")])
     (law menu-glyph-is-not-turn-end
       :statement "turn_end_decision_point => judge_before_solicitation"
       :counterexamples
         [(counterexample "solicitation text is pasted into the codex rate-limit menu and Enter confirms the selected option")
          (counterexample "the pane is asserted to be a REPL based only on idle prompt detection")])
     (law frozen-pane-terminates-typed
       :statement "pane_unchanged_beyond_T and not_active and not_idle_prompt => bounded_judge_then_typed_failure never_infinite_wait"
       :counterexamples
         [(counterexample "the judge is unavailable, so nothing is done and the session is left running")
          (counterexample "the judge keeps being called every tick without limit")])
     (law discriminator-stability
       :statement "new_failure_kinds => last_validation_error_and_terminal_cause not_new_discriminator_values"
       :counterexamples
         [(counterexample "interactive-prompt-blocked is added as a fifth value of the ACP discriminator")])
     (law conversation-record-freshness-vetoes-turn-end
       :statement "conversation_record_updated_within_quiescence_window => turn_end_not_declared regardless_of_pane_idle_and_stable"
       :counterexamples
         [(counterexample "based on the pane being idle ∧ stable (the 2026-08-18 real case: a CLI that does not draw the running turn), a solicitation is fired at a seat whose transcript was updated seconds ago, burning the budget")
          (counterexample "the probe is made a gate 'required for every pane' — for rows without identifying material (generic / conversation not yet determined) or with a missing artifact, turn-end detection itself stops, and a truly finished turn is never detected")
          (counterexample "instead of freshness, a 'diff against the previous observation' is stored in a state column and compared — the state is lost on restart / re-adopt, and durable state is added to a comparison that an absolute-clock mtime comparison (zero stored state) already handles")])
     (law unconsumed-solicitation-is-not-nonresponse
       :statement "queued_messages_visible_in_composer => solicitation_exhaustion_terminal_forbidden"
       :counterexamples
         [(counterexample "while observing that a solicitation we delivered is still unconsumed as '❯ Press up to edit queued messages', the seat is killed with run_failed / retryable=false on the grounds of 'did not respond to 2 solicitations' (the 2026-08-18 real snapshot)")])]
  :enforcement
    [(deftest test-adr-doe-agents-002-conversation-evidence-vocabulary
       ;; Machine surface of R-conversation-evidence-65b8: the evidence-surface effect vocabulary and knobs
       ;; exist with frozen default values (property pin — does not depend on predicate spelling).
       (import doeff_agents [ProbeConversationActivity FsFileMtime probe-conversation-activity fs-file-mtime]
               doeff_agents._sessionhost.effects [MonitorKnobs])
       (setv knobs (MonitorKnobs))
       (assert (= knobs.conversation-quiescence-seconds 120))
       (assert (= knobs.conversation-progress-margin-seconds 10))
       ;; The probe is a kind × params interface effect (owned by the per-kind impl).
       (setv eff (probe-conversation-activity
                   "claude" {"work_dir" "/tmp" "effective_identity" {}
                             "conversation" {"session_id" "x"}}))
       (assert (isinstance eff ProbeConversationActivity))
       (assert (isinstance (fs-file-mtime "/tmp/x.jsonl") FsFileMtime))
       ;; The counterexample tests (reproductions of the real incident) exist in the package suite — name-set pin.
       ;; The path is rooted at module __file__ (same editable-install assumption as
       ;; ADR-010 cleanup-single-home — does not depend on cwd).
       (import pathlib [Path])
       (import doeff_agents._sessionhost.policy :as policy-mod)
       (setv pkg-root (. (Path (. policy-mod __file__)) parent parent parent parent))
       (setv suite (.read-text (/ pkg-root "tests" "sessionhost_policy_deftests.hy")
                               :encoding "UTF-8"))
       (for [needed ["test-turn-end-not-declared-while-conversation-fresh"
                     "test-turn-end-not-declared-while-queued-messages"
                     "test-awaiting-cleared-by-conversation-progress"]]
         (assert (in needed suite) f"missing counterexample deftest: {needed}")))
     (defsemgrep no-blanket-never-reprompts-claim
       :languages ["generic"]
       :pattern "agentd never re-prompts"
       :message "The unqualified claim 'agentd never re-prompts' became false with ADR-DOE-AGENTS-002. Write it in qualified form: deterministic failures (-32002) are not revalidated / a missing result at turn-end goes through bounded solicitation."
       :bad ["// Deterministic failure — agentd never re-prompts (hard rule 7 / ADR 0035 R4)."]
       :good ["// Schema rejection is final for that payload (no automatic revalidation, ADR 0035 R4);\n// a missing result at turn-end enters the bounded solicitation loop (ADR-DOE-AGENTS-002 R1)."])]
  :plans ["docs/adr/defadr_doeff_agents_002_agentd_tui_boundary_contracts.hy"
          "agentd-rust-final:src/main.rs (monitor_once solicitation + prompt watchdog; cargo test)"])
