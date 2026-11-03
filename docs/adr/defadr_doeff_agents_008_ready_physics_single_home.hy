;;; Executable ADR: a single home for readiness/idle detection physics — the concretization of ADR-004's
;;; protocol-physics-has-one-home law for readiness, and its enforcement supply.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-008
  :title "The physical facts of readiness/idle detection (pattern strings, trust-prompt predicate, repl-idle budget default) are defined in exactly one place — the gate form in the doeff-free leaf doeff_agents/_ready_physics.hy, the observation form in impls/markers.hy, the budget default in the effects.hy MonitorKnobs vocabulary. Consumers (adapters / _session.py / launch.hy / host.hy) only import and reference them"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/_adapters"
          "packages/doeff-agents/src/doeff_agents/_session.py"
          "packages/doeff-agents/src/doeff_agents/_ready_physics.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost"]
  :problem
    [(fact
       "claude's readiness had forked into 2 live homes: CLAUDE_READY_PATTERN in adapters/claude.py (the screen-reader mode footer 'shift+tab to cycle') and has-idle-prompt in markers.hy (the line-leading `❯` of normal TUI mode). The two stacks also fork the startup argv itself (legacy = with --ax-screen-reader / sessionhost impls/claude_code.hy = without) — the physics of the same kind scattered across 2 implementations, so 'only the one being looked at gets fixed', which is exactly the counterexample of ADR-004 (the structure of the 2026-07-05 trust-dialog permanent hang)."
       :evidence "adapters/claude.py CLAUDE_READY_PATTERN (before the move :20) / sessionhost/impls/markers.hy has-idle-prompt / impls/claude_code.hy build-claude-argv")
     (fact
       "codex's readiness had the physics of the same TUI mode defined twice in 2 forms: CODEX_READY_PATTERN in adapters/codex.py (a regex for a one-shot gate — including the MCP-boot exclusion \\A(?!...) and the menu selection marker exclusion (?!\\d+\\.)) and the predicate group in markers.hy (has-idle-prompt + is-starting-mcp-servers + detect-dialog precedence). A fix to only one side drifts silently."
       :evidence "adapters/codex.py CODEX_READY_PATTERN (before the move :23) / markers.hy has-idle-prompt, is-starting-mcp-servers, detect-dialog")
     (fact
       "The literal for the 120s repl-idle budget was defined in 2 places: launch.hy (whose comment called the retired Rust's Duration::from_secs(120) an 'oracle constant' to justify the copy — a linguistic structure that contradicts ADR-004 R7/U1 'the retired Rust is not the standard of correctness') and the effects.hy MonitorKnobs default. The boot watchdog budget (launch timeout + repl-idle budget) depends on the two being equal, and drift turns into a wrong verdict by the watchdog."
       :evidence "sessionhost/launch.hy (before the move :79-81) / sessionhost/effects.hy MonitorKnobs repl-idle-max-wait-seconds / sessionhost/host.hy boot watchdog budget inputs")
     (fact
       "The physics of the screen-reader trust prompt (a 3-point match on the version-stable y/n line) was isolated in session.py — a sibling of markers.hy's R9 trust dialog (normal mode) living in a different home. While drafting the unification check, a second divergence was also found (recorded only; the fix is a follow-up): the verbatim first-paint capture (claude_screen_reader_trust_dialog.txt) does not contain 'Please answer y or n.', so this predicate does not match on the first paint — in practice the first dismissal is picked up by the generic onboarding pattern ('Yes, I trust this folder'), and this predicate only takes effect on the re-prompt after input is rejected."
       :evidence "session.py _screen_reader_trust_prompt_visible (before the move :467) / tests/data/ready_screens/claude_screen_reader_trust_dialog.txt ('Please answer y or n.' absent from the first paint)")
     (fact
       "A real divergence was found while drafting the unification (only recorded in this ADR; the fix is a separate issue): the gate regex rejects the codex trust-dialog frame (`› 1. Yes, continue`) via the menu lookahead, but has-idle-prompt alone mistakes it for the composer and accepts it. markers.hy's detect-dialog has no codex trust detection (sessionhost normally never reaches this frame because the per-kind pre-launch pre-trusts the workspace), but it is a latent hole where, on the skip_trust_setup path and on trust failure, wait-for-repl-idle could paste the prompt into the trust menu. No historical pin that fixes the fail-open behavior as expected is added (same judgment as ADR-004 R8)."
       :evidence "tests/data/ready_screens/codex_trust_dialog.txt(verbatim: `› 1. Yes, continue`)/ launch.hy wait-for-repl-idle / impls/codex.hy codex-pre-launch")]
  :context
    [(interpretation
       "This ADR is not a new law but the concretization, for readiness physics, of law protocol-physics-has-one-home of the accepted ADR-DOE-AGENTS-004, plus its enforcement supply. 'One home' means each physical fact is defined in one place; the multiplicity of forms itself (regex for gates and predicates for observation) is not forbidden — agreement between the forms is mechanically enforced by a parity check.")
     (interpretation
       "The home of the gate form is split into a doeff-free leaf (ready_physics.hy) rather than markers.hy because of the property the package root explicitly maintains, 'the imperative session transport API works without importing the doeff VM': markers.hy pulls in effects.hy (→ doeff), so importing it directly from adapters would bring the VM into the transport's import graph. The leaf has zero imports (only the hy language runtime).")
     (interpretation
       "claude's 2-mode fork (screen-reader vs normal TUI) is not 'two physics' but 'the physics of 2 rendering modes of the same kind' — with one home, both facts are documented side by side and drift becomes structurally impossible. Unifying the mode (= retiring the legacy imperative stack and merging into ADR-004 R2's in-process defhandler binding) is a follow-up in a separate issue. gemini is a legacy-only kind with no sessionhost impl, and its physics are already defined in only one place (gemini.py) — there is no duplication, so it is not moved (no fake home is created in sessionhost).")]
  :decision
    [(rule R1 "The text physics of readiness are defined in one place: the gate form (ready pattern strings, the screen-reader trust prompt predicate) = doeff_agents/_ready_physics.hy (doeff-free leaf); the observation form (has-idle-prompt / dialog detection, etc.) = sessionhost/impls/markers.hy. adapters / _session.py only import and reference them, and redefining a pattern literal is banned by the installed semgrep rule doeff-agents-ready-pattern-literal-outside-physics-home.")
     (rule R2 "The literal for the repl-idle budget default (120s) lives only in sessionhost/effects.hy (the MonitorKnobs knob vocabulary). launch.hy (the ready gate budget fallback) and host.hy (boot watchdog budget inputs) import and reference it — the installed semgrep rule doeff-agents-repl-idle-budget-literal-single-home bans redefinition within sessionhost.")
     (rule R3 "codex's gate regex and observation predicates are 2 forms of the same physics, and their verdicts must agree (parity) on every fixture frame where both gates are defined (ready / mcp-boot / login / update-dialog). The agreement check is enforced by tests/test_ready_physics_single_home.py on verbatim fixtures. The known divergence (trust-dialog frame) is excluded from parity until it is resolved in the separate issue of problem fact 5 — it is not fixed as expected.")
     (rule R4 "Wording that calls the retired Rust (preserved at rollback tag agentd-rust-final) the 'oracle' of readiness physics has been removed from the physics band this ADR touched (ready_physics.hy / markers.hy header / launch.hy budget comment / adapters). The provenance is written as 'ported from the retired Rust (rollback only; not the standard of correctness — ADR-004 R7/U1)', and the basis for the physics' correctness is placed in conformance fixtures (verbatim captures in tests/data/ready_screens/) and the conformance suite. Sweeping the same kind of wording out of the remaining modules (policy.hy / host.hy / store.hy / substrate.hy, etc.) is a follow-up.")]
  :laws
    [(law readiness-facts-have-one-defining-module
       :statement "readiness_fact(kind, mode) => exactly_one_defining_module; consumers_import_never_redefine"
       :counterexamples
         [(counterexample "redefine a ready pattern literal in an adapter, and only the markers/ready_physics side gets fixed, drifting silently (a replay of the structure of the claude trust-hang of 2026-07-05)")
          (counterexample "copy the repl-idle budget 120 as a literal into a new module and justify it with a comment 'because it is the same value as the retired Rust constant'")
          (counterexample "duplicate into sessionhost the physics of a kind that has only one home, such as gemini, and call it 'unification' (a fake home — creating new duplication)")])
     (law gate-and-observation-formalisms-must-agree
       :statement "same_fact_in_two_formalisms => machine_checked_parity_on_shared_frames; divergence_is_red_not_documentation"
       :counterexamples
         [(counterexample "fix only the codex gate regex and leave the markers predicate alone (or vice versa) — a change that the parity check should turn red")
          (counterexample "fix a discovered divergence in a test as the expected value and file no fix issue (a fail-open historical pin — violates ADR-004 R8)")])]
  :enforcement
    [(deftest test-adr-doe-agents-008-adapters-import-canonical-patterns
       ;; Identity of the home: what the adapter property returns is the leaf's constant object
       ;; itself (is-identical). A redefined literal would not be the same object.
       (import doeff_agents._adapters.claude [ClaudeAdapter])
       (import doeff_agents._adapters.codex [CodexAdapter])
       (import doeff_agents._ready-physics :as ready-physics)
       (assert (is (. (ClaudeAdapter) ready-pattern)
                   ready-physics.CLAUDE-SCREEN-READER-READY-PATTERN))
       (assert (is (. (CodexAdapter) ready-pattern)
                   ready-physics.CODEX-READY-PATTERN)))
     (deftest test-adr-doe-agents-008-budget-single-home
       ;; Budget consistency: effects (the home), launch (the importer), and the MonitorKnobs default are
       ;; equal. The absence of the literal is covered by the semgrep rule + the source check in
       ;; test_ready_physics_single_home.
       (import doeff_agents._sessionhost.effects :as fx)
       (import doeff_agents._sessionhost.launch :as launch)
       (assert (= fx.REPL-IDLE-MAX-WAIT-SECONDS 120))
       (assert (= launch.REPL-IDLE-MAX-WAIT-SECONDS fx.REPL-IDLE-MAX-WAIT-SECONDS))
       (assert (= (. (fx.MonitorKnobs) repl-idle-max-wait-seconds)
                  fx.REPL-IDLE-MAX-WAIT-SECONDS)))
     (defsemgrep ready-pattern-literal-outside-home
       "doeff-agents-ready-pattern-literal-outside-physics-home"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_adapters/claude.py"
         "source" "CLAUDE_READY_PATTERN = r\"shift\\+tab to cycle\"\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/_adapters/codex.py"
         "source" "CODEX_READY_PATTERN = r\"(?ims)composer\"\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_adapters/claude.py"
         "source" "from doeff_agents._ready_physics import CLAUDE_SCREEN_READER_READY_PATTERN\n"}])
     (defsemgrep repl-idle-budget-literal-outside-home
       "doeff-agents-repl-idle-budget-literal-single-home"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/launch.hy"
         "source" ";; budget redefinition (old form)\n(setv REPL-IDLE-MAX-WAIT-SECONDS 120)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/launch.hy"
         "source" "(import doeff_agents._sessionhost.effects [REPL-IDLE-MAX-WAIT-SECONDS])\n"}])]
  :plans ["packages/doeff-agents/src/doeff_agents/_ready_physics.hy (home of the gate form — doeff-free leaf)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/markers.hy (home of the observation form)"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/effects.hy (home of the budget default)"
          "packages/doeff-agents/tests/test_ready_physics_single_home.py (identity + parity + source check)"
          "follow-up issue: make the codex trust frame fail-closed (problem fact 5)"
          "follow-up issue: retire the legacy imperative stack / unify the claude startup mode / sweep the remaining oracle wording"])
