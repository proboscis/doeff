;;; Executable ADR for the in-process (caller-VM) agent result transport.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-005
  :title "in-process result transport: report_result data channel only — terminal-scrape result paths fully removed"
  :status "accepted"
  :scope ["doeff-agents" "doeff-agents._handlers.production" "doeff-agents._handlers.effectful"
          "doeff-agents.result-contract" "doeff-agents.inprocess-face"]
  :problem
    [(fact
       "On 2026-07-10, in nakagawa SBI recon readiness, the agent had already output a correct 585-character single-line JSON blocker with markers (DOEFF_AGENT_RESULT_BEGIN/END) at 16:35:45, but AwaitResult, which parsed verbatim the output the TUI had wrapped in an 80-column pane, failed definitively with 'Invalid control character', and readiness FAILed after 121 no-result polls."
       :evidence "zeus claude-home PVC projects/-tmp-readiness-sbi-recon-ui-mismatch-c92a96e2…/b86fb07b….jsonl; nak-pytest-agent-bfba24c7ab pod log; wrap reproduction script (wrap the same payload at width 78 → json.loads fails)")
     (fact
       "ADR 0035 R5 (e5f2ef5c) removed the wrap-repair heuristic as 'a lossy clone that always breaks one class of values' and declared report_result the single transport owner, but the same commit explicitly stated 'raw pane extraction is kept because doeff-agentic still depends on the in-process tmux handler' and kept shipping a non-conforming surface. There were zero machine checks (deftest / semgrep / conformance row) detecting this exemption."
       :evidence "git show e5f2ef5c (commit message); packages/doeff-agents/conformance/README.md (no in-process row)")
     (fact
       "The user (proboscis-ema) unknowingly received the repair removal via a pin bump (edfac2a1, 6e5d2128→8526c105); nothing failed in review, and the failure appeared only at runtime. The caller's failure message did not include outcome.validation-error, so the root cause, a parse failure, was invisible in the pytest output."
       :evidence "proboscis-ema edfac2a1; nakagawa sbi_recon.hy _recon-retry-message (before the fix)")
     (fact
       "On the same day, Claude Code 2.1.206 changed the screen-reader banner wording from '[Accessible screen reader mode: on]' to '[Screen Reader Mode: on via flag]'; the trust-dialog auto-responder that required the old wording went silent, and the agent stopped forever at 'Enter y/n:'. Keying on terminal-rendered text is version-fragile as a class, not as an isolated instance."
       :evidence "git show c1219b0e; tmux pane capture 2026-07-10 17:2x")
     (fact
       "The terminal grid projection is non-injective: soft-wrap injects and loses whitespace, and repairing a word-boundary wrap always loses whitespace that was really there. No repair heuristic can be correct for all payloads."
       :evidence "git show 5d404aee (agentd byte-faithful data channel, ADR 0035 R1)")]
  :context
    [(interpretation
       "The law of ADR 0035 extends to every handler surface doeff-agents ships: results ride a typed data channel, and the terminal is for observation only (status, onboarding, watchdog). The in-process surface is the only path that runs MCP tools inside the caller's VM and cannot be retired until session-host hosts caller tools — which is exactly why this surface is given the same channel, rather than being kept as an exemption.")
     (interpretation
       "Keeping scraping as a 'fallback' is worse than no fallback: it succeeds for short payloads and fails for long ones, making a nondeterministic system whose transport correctness depends on payload length. The law (ADR), the mechanism (code), and the users (pin consumers) migrate as one set.")]
  :decision
    [(rule R1 "An L2 session with result_schema launched via the in-process defhandler (tmux-agent-defhandler — agent-handler-defhandler, which wrapped an AgentHandler object, was removed by ADR-DOE-API-001 R4) always has an in-VM MCP server: domain tools + the report_result tool. Even if spec.mcp_tools is empty, a server with only report_result is started. The sink is registered before launch, and the result-contract prompt directs the agent only to the report_result tool.")
     (rule R2 "The only source from which in-process AwaitResult resolves a result is the reported sink (result-first; it wins regardless of whether the session is alive). Parsing terminal bytes as a result payload is forbidden. Pane capture remains for observation only (status, dialogs, watchdog).")
     (rule R3 "The marker vocabulary (DOEFF_AGENT_RESULT_BEGIN/END) and its extraction/repair helpers are removed from doeff-agents. Reintroducing marker-printing instructions or marker parsing violates this ADR (detected by semgrep).")
     (rule R4 "A session observed as terminal without a report returns a typed no-result outcome (EXITED, result=None, validation_error naming report_result, continuable=False). If it is alive and has not reported, it keeps returning heartbeat/awaiting-input observations — the solicitation policy is owned by the caller (the in-process counterpart of ADR-DOE-AGENTS-002 R1).")
     (rule R5 "tmux-agent-defhandler's answer to LaunchSession (doeff_agents._handlers.production._launch_session — formerly TmuxAgentHandler.handle_launch_session) fails fast for a schema spec with no registered sink (typed AgentLaunchError). The in-process surface's L1 agent() task path cannot host the data channel because its synchronous await starves the server loop — schema sessions must use the L2 LaunchSession effect path. Mock/Scenario/Daemon handlers own their own transports and are outside R5.")
     (rule R6 "Migration: consumers that pin doeff-agents (doeff-agentic, proboscis-ema) move schema sessions to the L2 path. Breaking marker-dependent tests and prompt assertions is an intentional breaking change of this ADR.")]
  :laws
    [(law results-never-ride-rendered-terminal
       :statement "result_payload => typed_data_channel(report_result) never terminal_scrape"
       :counterexamples
         [(counterexample "a result path is added that runs json.loads on JSON from a pane capture / pipe-pane transcript")
          (counterexample "the marker path is partially revived on the grounds that 'short results do not wrap, so it is safe'")
          (counterexample "the wrap-repair heuristic is reintroduced (a rollback of ADR 0035 R5)")])
     (law schema-session-always-has-channel
       :statement "result_schema_set and inprocess_launch => in_vm_mcp_server_with_report_result registered_before_launch"
       :counterexamples
         [(counterexample "since mcp_tools is empty no server is started, yet the prompt still directs report_result (the tool is absent and the agent is lost)")
          (counterexample "launch proceeds without a registered sink, and await spins on no-result until the budget expires")])
     (law capture-is-observation-only
       :statement "capture_pane_or_transcript => status_dialog_watchdog_observation only"
       :counterexamples
         [(counterexample "a fast-path is added that looks for result markers in the monitor's last_output")])
     (law tui-text-keys-on-stable-wording
       :statement "tui_text_detection => keyed_on_dialog_own_wording never version_dependent_banners"
       :counterexamples
         [(counterexample "trust-dialog detection is made dependent on the '[Accessible screen reader mode: on]' banner wording (it actually went silent in 2.1.206)")])]
  :enforcement
    [(defsemgrep no-terminal-result-marker-vocabulary
       :languages ["generic"]
       :pattern "DOEFF_AGENT_RESULT_BEGIN"
       :message "The result marker vocabulary was removed by ADR-DOE-AGENTS-005 R3. Results are carried by the in-VM report_result data channel (make_report_result_tool + sink). Reintroducing a terminal-scrape result path requires superseding this ADR first."
       :bad ["RESULT_BLOCK_BEGIN = \"DOEFF_AGENT_RESULT_BEGIN\""]
       :good ["sink = handler.create_result_sink(session_id)  # ADR-DOE-AGENTS-005 R1"])]
  :plans ["docs/adr/defadr_doeff_agents_005_inprocess_result_transport.hy"
          "packages/doeff-agents/src/doeff_agents/_handlers/production.py (marker vocabulary and extraction removed, sink-first await, launch fail-fast)"
          "packages/doeff-agents/src/doeff_agents/_handlers/effectful.hy (schema sessions always get in-VM server + report_result)"
          "packages/doeff-agents/tests/test_inprocess_report_result.py (machine checks for R1-R5)"])
