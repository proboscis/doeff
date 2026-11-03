;;; Executable ADR: the deftest :interpreters / :env vocabulary must be able to express "partial stack composition +
;;; handler replacement + env injection" without a consumer-side shim.
;;; Agents being unable to express verification faithfully in deftest is equivalent to
;;; the verification report to the (human) reviewer being distorted.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest <-])
(import doeff-adr.macros [fact interpretation counterexample])
(import doeff [Ask])


(defadr ADR-DOE-HY-002
  :title "deftest expressiveness contract: doeff-hy's deftest passes :interpreters / :env / :marks faithfully to the fixture, and the contract is that 'partial stack composition + single handler replacement + env injection' can be expressed with that vocabulary alone. A consuming repository bypassing deftest and reinventing its own run_test shim is a violation signal of this contract"
  :status "proposed"
  :scope ["packages/doeff-hy/src/doeff_hy"
          "packages/doeff-adr/src/doeff_adr/_pytest_plugin.py"
          "docs/adr/conftest.py"
          "docs/adr/defadr_doeff_hy_002_deftest_expressiveness.hy"
          "packages/doeff-agents/tests"]
  :problem
    [(fact
       "mediagen, one of the largest Python consumers, has the doeff_interpreter fixture wired in conftest, yet 0 of its 260 test functions use deftest. Instead, tests/support/run_test.py reimplements part of the interpreter stack and does manual with_handlers replacement. The fixture's :env path is still NotImplementedError ('deftest :env override is not wired to mediagen_interpreter yet')."
       :evidence "mediagen conftest.py / tests/support/run_test.py / mediagen ADR-008 §5 residual (2026-07-14 investigation)")
     (fact
       "From the viewpoint of the verification contract for agent authors: if an agent cannot faithfully express an issue's Verification items in deftest, it becomes a recurrence surface for silent weakening of verification (the 2026-06-12 PR #472 / #474 incident — mock substitution and display-only checks shipped while green). deftest's expressiveness is not a quality improvement but a precondition for agent-driven development."
       :evidence "doeff CLAUDE.md 'Verification Contract (CRITICAL)'; 2026-07-14 investment plan discussion")]
  :context
    [(interpretation
       "A consumer wiring up the framework's flagship test mechanism and then not using it signals a lack of expressiveness, not a lack of demand (mediagen even writes a shim to do the same thing). Absorbing the requirements the shim expresses — partial stack + replacement + env — into deftest's vocabulary is the owning layer's job.")
     (interpretation
       "Responsibility boundaries: doeff-hy owns faithful passing of params, doeff-adr owns collection, and the consuming repository owns the doeff_interpreter fixture (including the obligation to apply env). doeff's own docs/adr conftest fixture also serves as the reference implementation of this fixture contract.")]
  :decision
    [(rule R1 "With deftest's :interpreters / :env vocabulary, 'partial stack composition + single handler replacement + env injection' can be expressed without a consumer-side shim of its own. The acceptance criterion is that mediagen's tests/support/run_test.py becomes replaceable with the deftest vocabulary.")
     (rule R2 "doeff-hy passes deftest params (:env / :interpreters / :marks) faithfully to the fixture. Document the fixture-side contract (the obligation to apply :env, hard fail on unsupported params).")
     (rule R3 "The doeff_interpreter fixture in doeff's own docs/adr conftest is the reference implementation that applies :env. A fixture silently ignoring :env is forbidden (failing with NotImplementedError is allowed — better than silence).")]
  :laws
    [(law deftest-params-are-honored
       :statement "for_all deftest t: env_declared(t, k, v) => Ask(k) == v under_fixture; params_silently_dropped == 0"
       :counterexamples
         [(counterexample "The fixture receives :env but does not apply it, and the test runs against the production env and happens to be green")
          (counterexample "A homegrown shim that bypasses deftest reimplements part of the interpreter stack and fails to track changes in stack composition (handler order, etc.)")
          (counterexample "The consumer exposes a function that re-wraps deftest to pytest, and pytestmark (skipif / marks / parametrize) is lost along with __dict__ — written but ineffective, going red on machines without tmux (2026-09-21, 25 files and 12 :skip-if cases in doeff-agents. Check = packages/doeff-agents/tests/test_deftest_declaration_band.py and test_deftest_skip_canary.py)")])]
  :enforcement
    [(deftest test-adr-doe-hy-002-env-roundtrip
       {:env {"adr.doe.hy.002.probe" 42}}
       ;; Pin for R2/R3: deftest's :env reaches Ask via the fixture.
       ;; If red, either params passing or fixture application is unwired (both are in scope for this ADR).
       (<- v (Ask "adr.doe.hy.002.probe"))
       (assert (= v 42) f"deftest :env did not reach Ask: {v} — ADR-DOE-HY-002 R2/R3"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
