;;; Executable ADR: self-check the existence of defadr files and their wiring into pytest collection.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-ADR-001
  :title "Fully reconcile the existing executable ADRs with the actual pytest collection"
  :status "accepted"
  :scope ["doeff-adr" "pytest collection" "CI wiring"]
  :problem
    [(fact
       "In the 3 repositories doeff, proboscis-ema and agent-control-plane, defadr_*.hy files existed but sat outside testpaths or the CI arguments, and their checks were silently deactivated."
       :evidence "docs/crystallization/erosion-audit-2026-07-02.md")
     (fact
       "pytest_collect_file receives only the files pytest scanned, so the plugin alone could not previously discover defadr files outside the collection root."
       :evidence "packages/doeff-adr/src/doeff_adr/_pytest_plugin.py")
     (fact
       "doeff's own wiring into its default gate (tests/test_adr_wiring_gate.py) fired a strict `pytest --collect-only` in a child process from inside a test, collecting the whole suite a second time. In the daily full verification (pod), for 3 consecutive days from 2026-09-15, this one test hit pytest-timeout's 60 seconds, and since the method is thread, the whole process died and the run went red with no summary line (at about 28 % progress, 0 failure names extracted). Pod measurement (2026-09-17): collecting all 1,549 tests takes 135 seconds with a cold bytecode cache and 3.7 seconds with a warm one (Mac: 19 seconds cold, 4 seconds warm). The landing mechanism runs the child with PYTHONDONTWRITEBYTECODE=1, so the daily run writes no cache and the nested collection is cold every time. Most of the cold collection is compiling one Hy ADR file (docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy — 13 of 19 seconds on Mac), and this file grew from 165 KB on 09-14 (this check passed) to 285 KB on 09-15 and 400 KB on 09-17, crossing the deadline."
       :evidence "agora-1-0 land-runs/doeff-verify-20260917-033009.log; _hygiene_env in dotfiles agentcli land.py")]
  :context
    [(interpretation
       "Rather than statically guessing from config files, comparing the repository-wide candidates with the measured session.items handles testpaths, explicit arguments and ignore hooks by the same rule.")
     (interpretation
       "To avoid false failures when introducing this to existing users, plain pytest only warns, and only the CI command is strict.")
     (interpretation
       "A session started the default way (no path arguments) has already finished collecting the default scope. That collection result itself is the measurement, and there is no reason to start a second collection from inside a test. Collection time is proportional to the suite size and the machine's slowness, so placed under a single test's deadline it will eventually exceed it.")
     (interpretation
       "Wiring is a property of the collection scope, not of selection (-k / -m / --deselect). doeff's canonical gate itself runs with -m 'not e2e' — an ADR dropped by selection has still reached the scope.")]
  :decision
    [(rule R1 "When pytest collection finishes, reconcile the executable ADR candidates in the repository with the set of files of the items actually collected. The set of collected files is measured before selection (-k / -m / --deselect) takes effect (addendum 2026-09-17).")
     (rule R2 "The default mode is warn; strict lists the uncollected ADRs and exits non-zero. An explicit off remains for local runs.")
     (rule R3 "doeff-adr verify-wiring starts a strict collect-only pytest. External repositories can use it as a one-line CI gate. doeff itself, following the pytest canon (ADR-DOE-ENFORCE-001 R1), always wires it into the default gate via tests/test_adr_wiring_gate.py.")
     (rule R4 "defsemgrep does not turn a missing semgrep executable into a skip; it stays fail-closed. It is treated as a runtime-dependency check separate from wiring strict.")
     (rule R5 "A test that checks wiring from inside the session (doeff's tests/test_adr_wiring_gate.py) reads the running session's own collection result through the plugin entry point doeff_adr._pytest_plugin.default_scope_wiring. It does not start a second collection (nested pytest) from inside the test (added 2026-09-17). Reconciliation happens once per session, and the report at collection finish (R1 / R2) and this entry point read the same measurement. A session that names paths can say nothing about the default scope (narrower gives a false red, wider a false green), so the entry point returns NotDefaultScope and the test states the reason as a skip, not a pass. To check locally, use `uv run pytest -k <test name>` (default scope, one collection) or R3's `doeff-adr verify-wiring`.")]
  :laws
    [(law every-executable-adr-is-collected
       :statement "exists(defadr_file) => collected_by_configured_pytest_scope(defadr_file)"
       :counterexamples
         [(counterexample "docs/adr/defadr_*.hy exists but testpaths is only tests")
          (counterexample "CI names only tests explicitly and does not scan docs/adr")])
     (law strict-wiring-fails-closed
       :statement "uncollected_defadr and wiring_mode(strict) => nonzero_exit_with_paths"
       :counterexamples
         [(counterexample "CI goes green on the success of collected tests alone even though there are uncollected ADRs")])
     (law gate-test-reads-the-sessions-own-collection
       :statement "in_session_gate_test(t) => reads(t, measured_collection(current_session)) AND NOT spawns(t, nested_collection); collected(session) measured_before selection(-k, -m, --deselect); explicit_path_args(session) => verdict(t) == NotDefaultScope, never green"
       :counterexamples
         [(counterexample
             "real incident 2026-09-15 to 17: the shape that fires a whole-suite `pytest --collect-only` in a child process from inside a test — in the daily full verification (pod, no bytecode cache) collection took 135 seconds and hit the 60-second per-test deadline 3 days in a row. pytest-timeout's thread method kills the whole process, so the results of the roughly 430 tests that had passed and the summary line were lost, and the daily verdict could not be reached for 3 days")
          (counterexample
             "only extending the deadline — collection time is proportional to the suite size, so the extended deadline is eventually exceeded too. The shape that pays for whole-suite collection twice per run remains")
          (counterexample
             "reading the collection result of a session that names `pytest tests docs/adr`, while testpaths lacks docs/adr, as the answer for the default scope — naming collects every ADR so it is green, but in the default `uv run pytest` the ADRs stay silent")
          (counterexample
             "reconciling against session.items after selection — the canonical gate runs with -m 'not e2e', and a focused `-k` run also collects the default scope. Counting ADRs dropped by selection as uncollected goes red even though the wiring is correct")
          (counterexample
             "passing the test in a session that names paths — a run that checked nothing claims green")])]
  :enforcement
    [(defsemgrep no-silent-off-wiring-default
       :languages ["generic"]
       :pattern "default=\"off\""
       :message "doeff-adr wiring must not silently default to off."
       :bad ["parser.addini(\"doeff_adr_wiring\", default=\"off\")"]
       :good ["parser.addini(\"doeff_adr_wiring\", default=\"warn\")"])
     (deftest test-adr-doe-adr-001-gate-test-spawns-no-nested-collection
       ;; Existence pin for R5 + law gate-test-reads-the-sessions-own-collection.
       ;; The behavior itself is tested in packages/doeff-adr/tests/test_wiring.py (4 tests: red and green for the default scope,
       ;; NotDefaultScope for a session that names paths, measured before selection).
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv gate (.read-text (/ root "tests/test_adr_wiring_gate.py") :encoding "utf-8"))
       (assert (in "default_scope_wiring" gate)
               "tests/test_adr_wiring_gate.py does not read the session's own collection result (the plugin entry point) — ADR-DOE-ADR-001 R5")
       (assert (not (in "import subprocess" gate))
               "tests/test_adr_wiring_gate.py starts a child process — regression to nested full collection (ADR-DOE-ADR-001 R5)")
       (setv plugin (.read-text (/ root "packages/doeff-adr/src/doeff_adr/_pytest_plugin.py")
                                :encoding "utf-8"))
       (assert (in "def default_scope_wiring(" plugin)
               "the plugin entry point default_scope_wiring is gone — ADR-DOE-ADR-001 R5"))]
  :plans ["packages/doeff-adr/tests/test_wiring.py"
          "packages/doeff-adr/README.md"
          "tests/test_adr_wiring_gate.py"
          "docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
