;;; Executable ADR for agentd's explicit agent-auth-profile launch gate.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-003
  :title "agent auth profile must be explicit at launch — agentd rejects the implicit ~/.codex fallback"
  :status "accepted"
  :scope ["doeff-agentd" "agentd.session-launch" "agent-auth-profile"]
  :problem
    [(fact
       "When CODEX_HOME is not set, codex implicitly falls back to ~/.codex. On a shared machine the personal account lives there, so which account an agent runs under (a billing and permission decision) was being decided by a missing setting."
       :evidence "~/.codex/auth.json vs ~/.codex/profiles/company/auth.json (measured 2026-07-04)")
     (fact
       "On 2026-07-04, the ACP hypha runtime's wire registration catalog registered a bare codex command; an unattended attend session ran under the personal account, died at the rate-limit menu, and the personal weekly quota reached 100%."
       :evidence "agent-control-plane ADR 0039 problem facts; ~/.codex/sessions rollout telemetry")
     (fact
       "Fixing the declaration on the caller side (ACP) alone cannot close the gap for callers outside ACP or for future missing declarations. Every agent session goes through agentd's session.launch — that is the single enforcement point."
       :evidence "agentd-rust-final:src/main.rs session_launch")]
  :context
    [(interpretation
       "An auth profile is an attribute of a project/namespace, and there is no default. A missing profile must mean 'stop with a clear error', not 'run with some convenient value'.")
     (interpretation
       "The same principle (CLAUDE_CONFIG_DIR) applies to claude, but until existing claude callers (including trading operations) migrate, launch rejection applies only to codex and claude gets only a warning (staged enforcement).")]
  :decision
    [(rule R1 "A session.launch that starts codex (agent_type=codex or a command containing codex) requires CODEX_HOME in session_env or CODEX_HOME= in the command. If absent, it is rejected with a clear error before any tmux work.")
     (rule R2 "The error message names the missing declaration (CODEX_HOME), the remedy (declare it explicitly in session_env / command), and the reason (there is no default).")
     (rule R3 "A claude session warns when CLAUDE_CONFIG_DIR is not set. After callers finish migrating, this is promoted to the same rejection as R1 (the promotion is recorded as a revision of this ADR).")
     (rule R4 "agentd does not verify the contents of a profile (which account it is) — that is the responsibility of the caller (the project/namespace declaration; ADR 0039 in ACP). agentd enforces only that it is declared explicitly. [2026-09 revision (metered-billing lane B): **the account is not checked, but agreement between the billing-tier declaration and the binding kind is checked.** Under the old clause, putting metered-billing credentials into a home opened a path that passes silently (because the check looked only at env names — measured in the 2026-09-15 design). A silently passing path means the maintainer's invariant 'do not bring metered billing into the system' is protected only by human attention, and since the billing tier does not appear in the type, the audit point disappears too. Therefore, if a flat-rate kind's home (claude-code / codex) contains a metered-billing declaration (claude = apiKeyHelper in settings.json / env.CLAUDE_CODE_USE_VERTEX / env.ANTHROPIC_API_KEY / env.ANTHROPIC_AUTH_TOKEN; codex = a **non-empty** OPENAI_API_KEY in auth.json), PreLaunchSetup issues a typed reject (two ways to fix: make the kind *-metered, or remove the declaration from the home). ⚠ The test is 'the field is present and its content is non-empty' — the codex CLI writes OPENAI_API_KEY as null even for a flat-rate login, so judging by the field's presence would reject every flat-rate home (measured 2026-09-15: all three of the maintainer's homes have the field, with empty content; before landing, this measurement was used to confirm that the maintainer's deployment had 0 declarations). An absent or corrupted settings.json / auth.json is **allowed through** (same as today; on corruption the CLI itself fails loudly) — corruption gets one line in a warning. Only the billing-tier declaration itself is checked; which account the login belongs to is still not examined. law no-default-auth-profile is unchanged. The contract on the metered-billing side is ADR-DOE-AGENTS-004 R9 and law metered-billing-is-declared-by-kind-and-allowed-by-host-policy. deftest: sessionhost_impls_deftests.hy test-claude-pre-launch-rejects-metered-declaration-in-subscription-home, test-codex-pre-launch-rejects-api-key-in-subscription-auth-file.]")]
  :laws
    [(law codex-launch-requires-explicit-auth
       :statement "codex_launch => explicit_CODEX_HOME (session_env or command) else reject_before_tmux"
       :counterexamples
         [(counterexample "a codex launch without CODEX_HOME starts running on ~/.codex")
          (counterexample "instead of rejecting, only a warning log is emitted and launch continues")])
     (law no-default-auth-profile
       :statement "missing_auth_declaration => clear_error not_fallback"
       :counterexamples
         [(counterexample "agentd guesses a 'plausible' profile and fills it in")
          (counterexample "the company profile path is hard-coded into agentd as the default")])]
  :enforcement
    [(defsemgrep no-default-codex-home-in-agentd
       :languages ["generic"]
       :pattern "DEFAULT_CODEX_HOME"
       :message "agentd must not hold a default for CODEX_HOME (ADR-DOE-AGENTS-003 R4: agentd only enforces explicitness; the caller decides the value)."
       :bad ["const DEFAULT_CODEX_HOME: &str = \"/Users/x/.codex/profiles/agent\";"]
       :good ["// auth profile must arrive explicitly via session_env or the launch command"])]
  :plans ["docs/adr/defadr_doeff_agents_003_explicit_agent_auth.hy"
          "agentd-rust-final:src/main.rs (session_launch auth gate; cargo test)"])
