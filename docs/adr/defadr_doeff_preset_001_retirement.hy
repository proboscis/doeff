;;; Executable ADR: retire doeff-preset — slog_handler is the canonical default for "visible logs",
;;; and config defaults are the entrypoint env's responsibility. The layer of bundled presets is no longer needed.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-PRESET-001
  :title "Retire the doeff-preset package — remove it from the workspace, exclude it from the publish matrix, and ban doeff_preset imports entirely. The display defaults preset provided have moved to slog_handler (ADR-DOE-CORE-EFFECTS-001 R2), and config Ask defaults to the entrypoint's reader/lazy_ask env"
  :status "proposed"
  :scope ["packages/doeff-preset"
          "packages/doeff-agents/examples"
          "packages/doeff-agentic/examples"
          ".github/workflows/publish.yml"
          "docs/release-publish-runbook.md"]
  :problem
    [(fact
       "doeff-preset is already broken against the current API: its own docstring and tests assume the removed default_handlers (_Removed at doeff/__init__.py:183), and 17 failing tests were inventoried as pre-existing debt during the 2026-07-13 slog integration. log_display was repaired to .msg/.kwargs by ADR-DOE-CORE-EFFECTS-001 R6, but that is life support, not a restored reason to exist."
       :evidence "packages/doeff-preset/src/doeff_preset/__init__.py:9; the pre-existing debt section of docs/doeff-2026-07-13-slog-semantics-architecture-plan.md; doeff/__init__.py:183")
     (fact
       "Zero users: no src code in the core or any package imports doeff_preset; the only references are 14 example files in doeff-agents/doeff-agentic (themselves bit-rotted demos that also use the removed run_program(scoped_handlers=...) / default_handlers) and dependency declarations in pyproject. No other local repository imports it either."
       :evidence "rg 'doeff_preset' packages/*/src → 0 hits (measured 2026-07-13); run_program at packages/doeff-agents/examples/01_basic_session.py:131 does not exist in doeff-agents src")
     (fact
       "preset's main job, 'display that just works', became the core default in the slog integration design: slog_handler contracts 'install it and logs are visible' as the stderr terminal sink, and the CLI default_interpreter includes it as standard. Config defaults are covered by env injection into reader/lazy_ask. So no responsibility remains for a third layer of 'bundled presets'."
       :evidence "docs/adr/defadr_doeff_core_effects_001_slog_observability.hy R2/R4; slog_handler in packages/doeff-core-effects/doeff_core_effects/_handlers.py")]
  :context
    [(interpretation
       "preset was a buffer layer for 'keeping core thin while improving the experience', but its motivation was compensating for broken default sink semantics (slog going silent). Now that the root cause is fixed by ADR-DOE-CORE-EFFECTS-001, preset is a relic of a compensator, and the longer it is maintained the more it creates a second entry point competing with the canonical handler-composition idiom (stacking installers explicitly).")
     (interpretation
       "Retirement is 'removal of public API', so the fact of removal itself must be recorded with enforcement — not repeating the erosion in which the .log attribute disappeared informally without enforcement and came back (ADR-DOE-CORE-EFFECTS-001 problem).")]
  :decision
    [(rule R1 "Delete packages/doeff-preset and remove the dependency, workspace source, and examples extra from the pyproject of root/doeff-agents/doeff-agentic/doeff-conductor. Regenerate uv.lock; doeff_preset is no longer importable.")
     (rule R2 "doeff_preset imports are banned entirely (the formal record of a removed API). Where display defaults are wanted, compose slog_handler(program); give config Ask defaults with reader(env=...) / lazy_ask(env=...). If a bulk composition equivalent to preset_handlers is needed, the user stacks installers explicitly.")
     (rule R3 "Retire it from the publish surface too: exclude doeff-preset from the build matrix in .github/workflows/publish.yml and from the publishing procedure in docs/release-publish-runbook.md. Already-published versions on PyPI remain, but no new releases are made.")
     (rule R4 "Migration of examples is limited to removing preset references: preset_handlers()(prog) → slog_handler(prog); scoped_handlers=(preset_handlers(),) is removed and the program side is wrapped with slog_handler. Repairing the other removed APIs the examples also use (run_program / default_handlers) is pre-existing debt outside this ADR's scope, handled in a separate issue.")]
  :laws
    [(law preset-stays-retired
       :statement "not importable(doeff_preset) and not in_publish_matrix(doeff-preset) and no_tracked_code_reference(doeff_preset)"
       :counterexamples
         [(counterexample "Partially reviving doeff_preset for convenience and importing it from examples (a second composition entry point reborn)")
          (counterexample "doeff-preset remains in the publish.yml matrix and new versions still go to PyPI after retirement")])
     (law display-default-owned-by-slog-handler
       :statement "wants_visible_logs(entrypoint) => composes(slog_handler); no_bundle_package_reintroduced"
       :counterexamples
         [(counterexample "Creating a new 'all-in-one handler for beginners' package that externalizes the default sink semantics again")])]
  :enforcement
    ;; The real pytest is tests/test_doeff_preset_retired.py (3 tests: not importable /
    ;; package dir absent / zero tracked references — the public runbook is an explicit denylist).
    ;; The installed semgrep rule is no-doeff-preset-import in .semgrep.yaml.
    ;; This is the inline self-check of the static guard.
    [(defsemgrep no-doeff-preset-import
       :languages ["python"]
       :pattern "from doeff_preset import $X"
       :message "doeff-preset was retired by ADR-DOE-PRESET-001. Display defaults are slog_handler; config defaults are the reader/lazy_ask env."
       :bad ["from doeff_preset import preset_handlers"]
       :good ["from doeff_core_effects._handlers import slog_handler"])]
  :plans ["docs/doeff-2026-07-13-slog-semantics-architecture-plan.md"])
