;;; Executable ADR for doeff-agents' public result boundary.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-001
  :title "doeff-agents public result is the AwaitResult schema channel only"
  :status "accepted"
  :scope ["doeff-agents" "doeff-agents.public-api" "agent-result-schema"]
  :problem
    [(fact
       "The old doeff-agents had a transport that made the agent write .agentd-result.json, but an agent-created JSON result file easily slips in as a runtime result contract even when users do not read it."
       :evidence "packages/doeff-agents/src/doeff_agents/_handlers/production.py")
     (fact
       "doeff-agents exposes AgentSpec.result_schema and AwaitResultEffect / AwaitOutcome.result as its public API."
       :evidence "packages/doeff-agents/src/doeff_agents/_effects/agent.py")
     (fact
       "Schema validation and the retry prompt are owned by the doeff-agents handler."
       :evidence "packages/doeff-agents/src/doeff_agents/_handlers/production.py")
     (fact
       "In the 2026-07-02 Nakagawa SBI L2 readiness run, a schema-valid DOEFF_AGENT_RESULT_BEGIN/END block appeared in the agent pane, but it could not be collected as AwaitResult.result because of a race between the normal monitoring capture width and the awaiting-input decision."
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_uses_extended_capture_before_awaiting_input")
     (fact
       "In the same failure mode, AwaitOutcome can carry a schema-valid result even when AwaitOutcome.status is AWAITING_INPUT, so the effectful loop must also prioritize the result value over the status."
       :evidence "packages/doeff-agents/tests/test_agent_launch_invariants.py::test_await_result_result_wins_over_awaiting_input_status")
     (fact
       "Claude Code's `bypass permissions on` / `shift+tab to cycle` is a permanent footer that stays visible during long MCP tool runs, so using it alone as evidence of awaiting input in AwaitResult triggers a premature FollowUp just before the schema result appears."
       :evidence "packages/doeff-agents/tests/test_monitor_claude.py::test_stable_claude_permission_footer_alone_does_not_block")
     (fact
       "Unless a Claude Code session once judged BLOCKED is returned to RUNNING when the current pane has no evidence of awaiting input, the stable screen during a long MCP tool run keeps the stale BLOCKED state and is returned as AwaitResult.AWAITING_INPUT."
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_clears_stale_blocked_state_when_current_footer_is_not_input")
     (fact
       "A Claude Code tmux pane may contain shell-prompt-like lines right after launch or after a long MCP tool. Returning EXITED + result None on that alone while the tmux session still exists makes the caller consume an absent-result retry just before the schema result appears."
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_does_not_finalize_absent_result_on_live_tmux_shell_prompt")
     (fact
       "In the 2026-07-02 Nakagawa SBI L2 readiness run, Claude emitted a long readiness_blocker JSON, so only DOEFF_AGENT_RESULT_END remained in the visible tmux pane; DOEFF_AGENT_RESULT_BEGIN scrolled away and AwaitResult could not collect the result."
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_uses_transcript_when_result_begin_scrolled_off_screen")
     (fact
       "In the 2026-07-02 Nakagawa SBI L2 readiness run, while Claude was still rendering an account-state JSON, AwaitResult treated DOEFF_AGENT_RESULT_BEGIN alone as a result block, sent a validation follow-up, and corrupted the JSON being printed. Because the tmux pipe-pane transcript contains ANSI/cursor control sequences, JSON may not be extractable from it even when BEGIN/END are present."
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_waits_until_result_end_marker")
     (fact
       "In the 2026-07-02 Nakagawa SBI L2 readiness run, even though compact single-line JSON was requested, Claude Code's terminal UI inserted physical line breaks around column 80 in the middle of ticker codes and quantities, breaking `5020.T` and `1000000000` as JSON tokens."
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_result_payload_extract_repairs_terminal_wraps_inside_json_tokens")]
  :context
    [(interpretation
       "An agent-generated JSON file is never a runtime contract, whether for input, result, evidence, or checkpoint. Results are collected through the structured result channel managed by doeff-agents.")
     (interpretation
       "Users obtain the values they need by performing AwaitResult after LaunchSession and receiving the schema-validated AwaitOutcome.result. If diagnostics / evidence are needed, include them as fields of the result schema.")]
  :decision
    [(rule R1 "doeff-agents users must not obtain results by reading files created by the agent.")
     (rule R2 "The only agent result doeff-agents users can receive is the AwaitOutcome.result returned by AwaitResultEffect.")
     (rule R3 "Result schema validation, invalid-result retry, and missing-result retry are the responsibility of the doeff-agents handler.")
     (rule R4 "doeff-agents does not make the agent create JSON result files such as .agentd-result.json / result.json.")
     (rule R5 "A caller that needs diagnostics / evidence / checkpoints declares them in AgentSpec.result_schema and receives them from AwaitOutcome.result. Side-channel files are not used.")
     (rule R6 "When a schema-valid result block exists in the pane, AwaitResult returns that result in preference to the awaiting-input decision, even if the agent is still starting up or shows an awaiting-input display.")
     (rule R7 "When AwaitOutcome.result is present and there is no validation_error, the AwaitResult effectful loop stops waiting and returns to the caller even if AwaitOutcome.status is AWAITING_INPUT.")
     (rule R8 "Claude Code's permanent footer alone must not be the basis for AWAITING_INPUT in AwaitResult. Only an explicit input request or a permission prompt is treated as awaiting input.")
     (rule R9 "doeff-agents must not retain a BLOCKED state when the current pane has no evidence of awaiting input. A stale BLOCKED is returned to RUNNING and AwaitResult continues.")
     (rule R10 "For a Claude Code L2 session whose tmux session still exists, a shell-prompt-like display alone must not settle the result as absent. If there is no result, it is re-monitored as a heartbeat timeout.")
     (rule R11 "AwaitResult on the tmux backend does not depend only on the visible pane; it also collects DOEFF_AGENT_RESULT_BEGIN/END blocks from the transcript tail since session start. However, the transcript fallback is adopted only when a JSON payload can be extracted, and a block corrupted by raw terminal control sequences is not treated as a result.")
     (rule R12 "AwaitResult does not treat DOEFF_AGENT_RESULT_BEGIN alone as a result block; it waits until the matching DOEFF_AGENT_RESULT_END appears.")
     (rule R13 "AwaitResult repairs physical line breaks that Claude Code's terminal wrap inserted in the middle of a JSON string / number / property name, treating them as transport noise. Falling back to a workspace JSON file must not be used as the remedy.")]
  :laws
    [(law await-result-only-public-boundary
       :statement "doeff_agents_user_result => AwaitResult(handle).result and not agent_created_file"
       :counterexamples
         [(counterexample "caller waits for .agentd-result.json and parses it")
          (counterexample "caller tells agent to write result.json, then reads that file")
          (counterexample "caller validates schema after reading an agent-created JSON file instead of letting doeff-agents retry")])
     (law schema-retry-owned-by-doeff-agents
       :statement "invalid_or_absent_agent_result => doeff_agents_handler validates and retries before returning AwaitOutcome"
       :counterexamples
         [(counterexample "application code polls file existence and asks the agent to fix JSON itself")
          (counterexample "application code accepts a JSON file without AwaitResult schema validation")])
     (law agent-result-file-is-not-transport
       :statement "agent_terminal_result => structured_result_channel and not workspace_json_file"
       :counterexamples
         [(counterexample "doeff-agents prompt tells the agent to write .agentd-result.json")
          (counterexample "doeff-agents retry prompt asks the agent to create a JSON result file")])
     (law visible-result-block-wins-over-awaiting-input
       :statement "pane_contains_valid_result_block => AwaitResult.result regardless_of_prompt_status"
       :counterexamples
         [(counterexample "handler sees Claude prompt after the block and returns AWAITING_INPUT")
          (counterexample "handler captures only the latest status area and misses an older result block")])
     (law result-value-wins-over-await-status
       :statement "AwaitOutcome.result and no validation_error => caller receives outcome regardless_of_AwaitStatus"
       :counterexamples
         [(counterexample "effectful loop keeps waiting because status is AWAITING_INPUT even though result is present")
          (counterexample "application code requires AwaitStatus.EXITED after doeff-agents has returned a schema-valid result")])
     (law claude-footer-is-not-input-request
       :statement "claude_status_footer_only => not AwaitStatus.AWAITING_INPUT"
       :counterexamples
         [(counterexample "bypass permissions footer alone triggers FollowUp during a long MCP tool call")
          (counterexample "shift+tab footer alone is treated as a user prompt")])
     (law stale-blocked-state-must-clear
       :statement "previous_status_BLOCKED and not current_input_prompt => current_status_RUNNING"
       :counterexamples
         [(counterexample "old idle prompt keeps AwaitResult returning AWAITING_INPUT during an MCP call")
          (counterexample "stable Claude footer with no input request inherits a previous BLOCKED state")])
     (law live-tmux-prompt-is-not-absent-result
       :statement "tmux_session_exists and no_result_block => AwaitStatus.TIMED_OUT not EXITED_absent"
       :counterexamples
         [(counterexample "Claude launch echo with a shell prompt returns EXITED + result None")
          (counterexample "a post-MCP idle-looking pane consumes absent-result retries before the schema block appears")])
     (law result-block-may-outlive-visible-pane
       :statement "transcript_contains_valid_result_block => AwaitResult.result even_if_visible_pane_lost_BEGIN"
       :counterexamples
         [(counterexample "visible pane contains DOEFF_AGENT_RESULT_END but not BEGIN, so AwaitResult ignores a valid transcript result")
          (counterexample "long readiness blocker JSON scrolls BEGIN out of tmux visible screen and the caller waits until timeout")])
     (law partial-or-unparseable-result-block-is-not-result
       :statement "partial_or_unparseable_result_block => keep_waiting_or_use_other_valid_source"
       :counterexamples
         [(counterexample "AwaitResult sends a FollowUp while the agent is still printing JSON between BEGIN and END")
          (counterexample "AwaitResult validates a raw pipe-pane transcript containing cursor-control fragments instead of waiting for a parseable pane block")])
     (law terminal-wrap-is-transport-noise-not-file-contract
       :statement "terminal_wrapped_result_json => AwaitResult reconstructs payload and not agent_workspace_json_file"
       :counterexamples
         [(counterexample "Claude wraps 1000000000 into two pane lines and caller asks the agent to write result.json instead")
          (counterexample "doeff-agents treats a wrapped but reconstructable result block as absent and burns result retries")])]
  :enforcement
    [(defsemgrep no-public-agentd-result-file-read
       :languages ["generic"]
       :pattern "agentd-result.json"
       :message "doeff-agents users must not read .agentd-result.json; use AwaitResult.result."
       :bad ["payload = Path(work_dir, \".agentd-result.json\").read_text()"]
       :good ["outcome = AwaitResult(handle, timeout_seconds=60)\npayload = outcome.result"])
     (defsemgrep no-public-agent-result-json-boundary
       :languages ["generic"]
       :pattern "result.json"
       :message "agent-created result.json is not a public result boundary; use AgentSpec.result_schema + AwaitResult."
       :bad ["wait_for(Path(work_dir, \"result.json\"))\npayload = json.loads(Path(work_dir, \"result.json\").read_text())"]
       :good ["spec = AgentSpec(result_schema=schema)\noutcome = AwaitResult(handle, timeout_seconds=60)\npayload = outcome.result"])
     (defsemgrep no-public-result-poller
       :languages ["generic"]
       :pattern "start-result-poller"
       :message "file polling for agent results belongs inside doeff-agents, not in users."
       :bad ["start-result-poller(work_dir / \"result.json\")"]
       :good ["outcome = AwaitResult(handle, timeout_seconds=60)"])
     (defsemgrep no-agentd-result-file-write-prompt
       :languages ["generic"]
       :pattern "write a JSON object to `.agentd-result.json`"
       :message "doeff-agents must not instruct agents to create JSON result files."
       :bad ["For this terminal backend, write a JSON object to `.agentd-result.json`."]
       :good ["Do not create JSON result files in the workspace. Return a structured result block."])]
  :plans ["docs/adr/defadr_doeff_agents_001_await_result_public_boundary.hy"])
