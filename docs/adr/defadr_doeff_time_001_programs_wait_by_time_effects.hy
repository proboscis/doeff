;;; Executable ADR: programs wait for time by yielding doeff-time effects (Delay, WaitUntil, WaitWithin, etc.). Waiting on the wall clock with time.sleep or asyncio.sleep
;;; is only for implementations of time handlers (functions that receive (effect, k)). A semgrep rule stops new bare sleeps with ERROR.
;;; semgrep cannot read Hy programs (defk, defp), so the doeff-linter rule DOEFF210 enforces the same rule (R7).
;;; semgrep cannot read Hy checks either, so the doeff-linter rule DOEFF211 enforces the rule for checks (R3) (R8).
;;;
;;; The decision (2026-10-12) is written in the :problem fact below.
;;;
;;; This ADR file does not hold the rules' content (pattern, paths). The rules are defined in one place, .semgrep.yaml; the file writes the rule ids and check names in the laws' :enforced-by
;;; as `<path from the repo root>::<name>`, and its :enforcement checks verify that each name exists in the current tree, that the rules are ERROR, and that the polarity checks apply those rules
;;; (deleting a rule, lowering it to WARNING, or renaming a check is red). The name is the rule id for the semgrep config, a
;;; module-level function for Python checks, a deftest for Hy checks, and a #[test] function for Rust checks.
;;;
;;; Rollback: revert the commit that added this file (one ADR file and the enforcement-ledger.json rows). To remove the rules themselves,
;;; delete doeff-no-sleep-in-programs and doeff-no-sleep-in-tests in .semgrep.yaml together with the polarity checks, the registry, and this file in the same commit,
;;; and rebuild docs/adr/enforcement-ledger.json (ADR-DOE-ENFORCE-001 R5).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(import doeff-adr [GetAdr])
(require doeff-hy.macros [deftest defk val var <-])
(import doeff [run])
(import ast)
(import re)
(import pathlib [Path])
(import hy)
(import yaml)


;; ---------------------------------------------------------------------------
;; Ledger

(val ADR-ID "ADR-DOE-TIME-001")
(val THIS-FILE "docs/adr/defadr_doeff_time_001_programs_wait_by_time_effects.hy")

;; The single place where the sleep rules are defined.
(val SEMGREP-CONFIG ".semgrep.yaml")

;; Rule id → its polarity check file (a check that applies the rule itself from SEMGREP-CONFIG and verifies that only the sample's `# fires` lines [`; fires` in Hy samples] hit).
(val POLARITY-TESTS
  {"doeff-no-sleep-in-programs" "tests/semgrep/test_no_sleep_in_programs_rule.py"
   "doeff-no-sleep-in-tests" "tests/semgrep/test_no_sleep_in_tests_rule.py"})


;; ---------------------------------------------------------------------------
;; Judgments (pure functions)

(defk rule-severities [config-text]
  {:pre [(: config-text str)] :post [(: % dict)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "From the semgrep config text: rule id → the list of severities of rules with that id (if 2 rules share an id, there are 2 entries)."
  (val rules (.get (or (yaml.safe-load config-text) {}) "rules" []))
  (dfor rule-id (sfor r rules (str (get r "id")))
        rule-id (lfor r rules :if (= (str (get r "id")) rule-id) (.get r "severity"))))


(defk polarity-subject [source]
  {:pre [(: source str)] :post [(: % tuple)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "What a polarity check's source applies: the module-level RULE value (the rule id) and the string inside the CONFIG expression (the config file name). None if not written."
  (val assigned (dfor node (. (ast.parse source) body)
                      :if (and (isinstance node ast.Assign)
                               (= (len node.targets) 1)
                               (isinstance (get node.targets 0) ast.Name))
                      (. (get node.targets 0) id) node.value))
  (val rule-value (.get assigned "RULE"))
  (val config-value (.get assigned "CONFIG"))
  #((if (isinstance rule-value ast.Constant) rule-value.value None)
    (if (is config-value None)
        None
        (next (gfor n (ast.walk config-value)
                    :if (and (isinstance n ast.Constant) (isinstance n.value str))
                    n.value)
              None))))


(defk deftest-names [text]
  {:pre [(: text str)] :post [(: % frozenset)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "Collect the names of (deftest name …) forms at any depth of Hy source (spellings inside strings and comments are not forms and are not counted)."
  (var pending (list (hy.read-many text)))
  (var names #{})
  (while pending
    (val form (get pending -1))
    (:= pending (+ (cut pending 0 -1) (if (isinstance form hy.models.Sequence) (list form) [])))
    (when (and (isinstance form hy.models.Expression)
               (> (len form) 1)
               (= (str (get form 0)) "deftest"))
      (:= names (| names #{(str (get form 1))}))))
  (frozenset names))


;; Rust checks: the fn name on the line after a #[test] at the start of a line (indentation only). Commented-out checks (// #[test], // fn …) start with // and do not match.
(val RUST-TEST (re.compile r"^[ \t]*#\[test\][ \t]*\n[ \t]*fn[ \t]+([A-Za-z_][A-Za-z0-9_]*)" re.MULTILINE))


(defk rust-test-names [text]
  {:pre [(: text str)] :post [(: % frozenset)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "The names Rust source defines as checks (functions with #[test]). Functions without #[test] and commented-out checks are not counted."
  (frozenset (.findall RUST-TEST text)))


(defk check-names [rel text]
  {:pre [(: rel str) (: text str)] :post [(: % frozenset)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "The names the file content text defines as checks: rule ids for the semgrep config, module-level function names for Python checks, deftest names for Hy checks,
   and #[test] function names for Rust checks."
  (<- severities dict (rule-severities (if (= rel SEMGREP-CONFIG) text "")))
  (<- deftests frozenset (deftest-names (if (.endswith rel ".hy") text "")))
  (<- rust-tests frozenset (rust-test-names (if (.endswith rel ".rs") text "")))
  (cond
    (= rel SEMGREP-CONFIG) (frozenset severities)
    (.endswith rel ".py") (frozenset (gfor node (. (ast.parse text) body)
                                           :if (isinstance node #(ast.FunctionDef ast.AsyncFunctionDef))
                                           node.name))
    (.endswith rel ".rs") rust-tests
    True deftests))


;; ---------------------------------------------------------------------------
;; Foundation (reads files)

(defk dead-checks [repo-root entries]
  {:pre [(: repo-root Path) (: entries list)] :post [(: % list)] :tags {:context "doeff-time-adr" :role "foundation"}}
  "Among the laws' :enforced-by entries (`<path from the repo root>::<name>`), list with reasons those that do not point to a rule or check in the current tree."
  (var dead [])
  (for [entry entries]
    (val parts (.partition entry "::"))
    (val rel (get parts 0))
    (val name (get parts 2))
    (val path (/ repo-root rel))
    (val text (if (.is-file path) (.read-text path :encoding "utf-8") ""))
    (<- names frozenset (check-names rel text))
    (when (not-in name names)
      (:= dead (+ dead [f"{entry}: {rel} has no rule or check named {(or name "<no name>")}"]))))
  dead)


(defadr ADR-DOE-TIME-001
  :title "Programs wait for time by yielding doeff-time effects (Delay, WaitUntil, WaitWithin, etc.). Only implementations of time handlers wait on the wall clock with time.sleep or asyncio.sleep, and a rule in .semgrep.yaml stops new bare sleeps with ERROR"
  :status "accepted"
  :scope [".semgrep.yaml(rules doeff-no-sleep-in-programs, doeff-no-sleep-in-tests)"
          "packages/doeff-flow/examples/"
          "packages/doeff-linter/src/project/program_sleep.rs(rule DOEFF210)"
          "packages/doeff-linter/tests/program_sleep.rs"
          "pyproject.toml section [tool.doeff-linter.program_sleep]"
          "packages/doeff-linter/src/project/test_sleep.rs(rule DOEFF211)"
          "packages/doeff-linter/tests/test_sleep.rs"
          "pyproject.toml section [tool.doeff-linter.test_sleep]"
          "tests/semgrep/test_no_sleep_in_programs_rule.py"
          "tests/semgrep/test_no_sleep_in_tests_rule.py"
          "tests/semgrep/test_realtime_tests_registry.py"
          "tests/realtime_tests.toml"
          "packages/doeff-time/src/doeff_time/"
          "docs/adr/defadr_doeff_time_001_programs_wait_by_time_effects.hy"]
  :problem
    [(fact
       "Decision 2026-10-12: there is no reason to use asyncio.sleep where waiting happens; use doeff's time effects. This rule is checked by a linter."
       :evidence "The message of commit ba5513169")
     (fact
       "On doeff main as of 2026-10-12, the only sleep rule was semgrep's doeff-no-sleep-in-tests. It covered only tests/, with severity WARNING (not blocking merges to main), and there was no rule for the core (src). There were 30 files under tests/ containing asyncio.sleep."
       :evidence "Record of reading the main working tree with git grep at 2026-10-12 22:4x")
     (fact
       "There were 7 bare sleeps inside @do programs in the core (the retry and upload waits in doeff-gemini structured_llm.py, and the retry waits in doeff-openai chat.py, embeddings.py, structured_llm.py, doeff-openrouter chat.py, and doeff-seedream structured_llm.py). Checks exercising the retry paths (openai 3, gemini 5) either waited 1–2 seconds in real time or replaced asyncio.sleep via monkeypatch."
       :evidence "Commit ba5513169 (on main at 2026-10-14 02:21 JST; replaced the 7 places with Delay and made the 2 rules ERROR)")
     (fact
       "On main as of 2026-10-14 (4dffe90e5) there was no rule for Hy programs. Applying DOEFF210 to that tree hits 3 lines of one defk in an agent package's Hy session host. In the same tree, that package's programs waited with 2 home-grown wait effects (11 places, answered with time.sleep by the package's handlers). The existing rule for home-grown sleep effects looked only at the Python names class SleepEffect / def Sleep, so it hit neither."
       :evidence "Body of the commit that added DOEFF210 (records 3 hits on the main tree and 0 after the change)")
     (fact
       "On main as of 2026-10-14 (2222d4e48) there was no rule for Hy checks. Applying DOEFF211 to that tree gives 62 hits in 32 files (doeff-cluster 27, doeff-core-effects 17, doeff-records 5, and 13 in agent packages since removed from this repository). The lines counted with git grep were 70: 61 real calls, 7 inside strings (program bodies passed to child processes), and 2 zero-second yields. The rule also caught 1 paraphrase that grep misses ((.wait (threading.Event) 0.1)). All 62 hits wait with a deadline on a real thread, a real asyncio loop, or a real external process (child process, HTTP server, PostgreSQL, herdr); none was a wait that could be switched to a virtual clock."
       :evidence "Body of the commit that added DOEFF211")]
  :context
    [(interpretation
       "If a program yields Delay, what a second is is decided by the time handler the caller installs: async_time_handler and sync_time_handler wait on the wall clock, and sim_time_handler only advances a virtual clock without waiting. If a program calls time.sleep or asyncio.sleep, it waits on the wall clock whatever handler is installed, so checks of that program are slowed by the real time and go red with timeouts on busy machines.")
     (interpretation
       "The doeff linter the rule refers to could also be read as the Rust doeff-linter (DOEFFnnn), but commit ba5513169 implemented it as .semgrep.yaml rules. That is the implementer's choice of tool; the rule did not name semgrep. Either tool equally stops new findings via the commit hook and the daily scan.")
     (interpretation
       "Bare sleeps in Python loops that are neither programs nor handlers (polling in CLI watchers, loops of always-running threads) count as boundary entries and are not covered by the rule. This is a reversible judgment made by the implementer on 2026-10-14 (option A) and is not part of the 2026-10-12 decision.")]
  :decision
    [(rule R1 "Programs (@do functions, defk) yield doeff-time effects (Delay, WaitUntil, WaitWithin, RaceWithin, ScheduleAt, WaitTicks) when waiting for time. They do not call time.sleep(x) or asyncio.sleep(x) (including yield Await(asyncio.sleep(x)) and sleep(x) from from time import sleep). sleep(0), which yields once without waiting for time, is not counted.")
     (rule R2 "Only implementations of time handlers (functions receiving (effect, k), and helpers called only from them) may wait on the wall clock. The caller chooses the time handler where it runs the program: in production async_time_handler() (inside await_handler) or sync_time_handler(); in checks sim_time_handler().")
     (rule R3 "Checks run programs on sim_time_handler's virtual clock, or wait by naming the thing (event) waited for. Only checks whose subject is real time itself (a real thread, a real asyncio loop, an external process or library) may call time.sleep or asyncio.sleep; such a check declares the pytest marker realtime and is listed with a reason in [tests] of the registry tests/realtime_tests.toml (support modules of checks that wait on real processes also declare it with the same single pytestmark line and are listed). A suite in which every check drives a real external process as a black box is listed with a reason in the registry's [suites] as a dir in the rule's paths.exclude. These 2 tables are the only ways out of the rule; do not take the rule off with line annotations (nosemgrep), and do not paraphrase sleep as threading.Event().wait(seconds) and the like.")
     (rule R4 "New bare sleeps are stopped by a linter: the .semgrep.yaml rules doeff-no-sleep-in-programs and doeff-no-sleep-in-tests are placed with severity ERROR. The rule definitions (pattern, paths) live in one place, .semgrep.yaml, and are not copied into this file or any other ADR file. Shape correctness (only the lines that should fire fire) is verified by each rule's polarity check applying the .semgrep.yaml rule itself to samples.")
     (rule R5 "Places the rules do not see are outside the laws of R1 and R3 until a child card closes them. As of 2026-10-14 no such place remains: the 7 nosemgrep places under tests/ and conformance/ were closed by R3. Bare time.sleep in Hy programs (defk, defp) and home-grown wait effects were closed by R7. Hy files under tests dirs were closed by R8. Any newly found \"place the rules do not see\" is written here with a child card. The change that closes it adds the added rule or check to the :enforced-by of the law below in the same change.")
     (rule R7 "R1 also applies to Hy programs (bodies of defk and defp), and the doeff-linter rule DOEFF210 stops them as critical (semgrep cannot read Hy). The rule is defined in one place, packages/doeff-linter/src/project/program_sleep.rs, and the population (where programs live) in one place, [tool.doeff-linter.program_sleep] in the root pyproject.toml (the core — tests/ and examples/ are excluded). Hy handler implementations allowed to wait on the wall clock take 3 shapes: defhandler clauses, definitions whose parameter list is [effect k], and helpers called only from handlers that wait for the real time of a real counterpart (child process, terminal). Such a helper declares :role \"foundation\" (the generic I/O role) itself in the :tags of its contract dict and writes in its docstring why it is not a Delay (as of 2026-10-14: doeff-core-effects' group-gone-by — cancellation cleanup runs in a new VM not reached by the request handler stack, and doeff-core-effects does not depend on doeff-time). deff and defn (plain callables) cannot issue effects and are not counted as programs — whether deff is acceptable is owned by ADR-DOE-HY-004.")
     (rule R8 "R3 also applies to Hy checks (Hy files whose path is under a tests dir — checks, check tools, and job bodies passed to child processes — and Hy files with check names wherever they are), and the doeff-linter rule DOEFF211 stops them as critical (semgrep cannot read Hy. Same vessel as DOEFF210, using the same reading components). The rule is defined in one place, packages/doeff-linter/src/project/test_sleep.rs, and the population (where checks live) in one place, [tool.doeff-linter.test_sleep] in the root pyproject.toml. The realtime declaration takes the 3 shapes pytest reads as marks: {:marks [\"realtime\"]} in deftest's meta, the decorator of a plain function check (defn [pytest.mark.realtime] name [] …), and module top-level (val pytestmark pytest.mark.realtime) (files where helpers wait and check tools declare at module level). Declared checks are listed with a reason in [tests] of the same registry as Python checks, tests/realtime_tests.toml, with key names in the form pytest shows (- replaced by _). Standalone scripts that checks launch as separate processes (job bodies, stand-in CLIs) do not import pytest and cannot declare marks — such a file is named by file in the section's exclude, that one line stands in for the declaration, and the registry's [tests] has a row with the same path (as of 2026-10-14: doeff-cluster's tests/fixtures/sleeper.hy). The dir rows of the section's exclude are only the rule's sample dir and the dirs in the registry's [suites]. The registry check verifies that declarations and file namings correspond 1:1 with the registry's [tests], and exclude dirs with [suites]. That the rule and the registry check read the same shapes as declarations is confirmed by both reading the same samples (packages/doeff-linter/tests/fixtures/test_sleep/). There is no way to lower it via a known-hits table (lowered_by_registry is false).")
     (rule R6 "Rollback: to remove only this file, revert the commit that added it. Removing the rules or lowering them to WARNING would overturn the 2026-10-12 decision, so first write the new decision in :problem, then fix the rules, polarity checks, registry, and this file in the same commit.")]
  :laws
    [(law a-program-waits-by-a-time-effect
       :statement "for_all Python program p (@do function) in the core (doeff/ and packages' src) and examples/ (packages/*/examples/ and root examples/), and Hy program p in the core (bodies of defk, defp), and wait w inside p: w is a yield of a doeff-time effect ∧ w ∉ {time.sleep(x), asyncio.sleep(x) | x ≠ 0} (excluding inside handler implementations — in Python, functions receiving (effect, k); in Hy, defhandler clauses, definitions receiving [effect k], and helpers declaring :role foundation [R7])"
       :counterexamples
         [(counterexample "Waiting between retries with yield Await(asyncio.sleep(delay)) (the shape of doeff-gemini structured_llm.py before ba5513169) — checks wait 1–2 seconds in real time or replace asyncio.sleep via monkeypatch")
          (counterexample "Calling time.sleep(1.0) inside an @do program — even with sim_time_handler installed the virtual clock does not advance, and it takes 1 second on the wall clock")
          (counterexample "Calling time.sleep(0.1) inside @do in doeff-flow examples (the shape of the 15 places before the fix) — examples get copied, so bare sleeps spread into users' programs")
          (counterexample "Calling (time.sleep 0.5) in the body of a Hy defk — semgrep cannot read Hy, so doeff-no-sleep-in-programs does not fire, and without DOEFF210 nobody stops it (the shape before DOEFF210 was added)")
          (counterexample "A program yields a package's home-grown wait effect, and that package's handler answers with time.sleep — no sleep appears literally, but even with a time handler installed the virtual clock does not advance (the 11 places before R7's fix)")
          (counterexample "Letting a program defk declare :role \"foundation\" to let a sleep through — only helpers called only from handlers that wait for a real counterpart's real time may declare it, writing the reason in the docstring (R7). The rule trusts the declaration, so whether a declaration is legitimate is for the people reading the change to judge")]
       :enforced-by [".semgrep.yaml::doeff-no-sleep-in-programs"
                     "tests/semgrep/test_no_sleep_in_programs_rule.py::test_only_the_marked_lines_fire"
                     "tests/semgrep/test_no_sleep_in_programs_rule.py::test_examples_are_in_the_range"
                     "packages/doeff-linter/tests/program_sleep.rs::only_the_marked_lines_fire"
                     "packages/doeff-linter/tests/program_sleep.rs::the_hy_programs_of_this_repo_do_not_sleep"
                     "packages/doeff-linter/tests/program_sleep.rs::the_sample_of_a_sleeping_program_is_found_under_the_repo_s_own_settings"]
       :wiring "Wired (2026-10-14, ba5513169) — the Python rules are applied by the commit hook (alias semgrep-rules in .pre-commit-config.yaml) and the daily scan. The Hy rule DOEFF210 is applied by the rust stage of the daily full verification (cargo test via make test-rust — packages/doeff-linter/tests/program_sleep.rs judges this repo's core with the root settings and names hits by file and line), and locally by make lint-doeff and the editor's single-file run. It is not in the commit hook (the root doeff-linter hook measures only Python files). Coverage is the Python core and examples/, and the Hy core (R5, R7). Advice before writing is given by agent-commons' conditional rule doeff-program-waits-by-the-time-effect (it also applies to Hy defk, defp, deftest and to repos outside doeff; it does not block)")
     (law the-time-handler-decides-what-a-second-is
       :statement "for_all program p and Delay(s) inside p: under sim_time_handler the virtual clock advances by exactly s ∧ under async_time_handler and sync_time_handler it waits s on the wall clock (p's text is the same for all 3 clocks)"
       :counterexamples
         [(counterexample "Writing programs differently per clock (adding an argument to the program that skips waits only in checks) — the text checks run and the text production runs diverge")]
       :enforced-by ["packages/doeff-time/tests/test_time_contract.hy::test-delay-advances-by-its-span"]
       :wiring "Wired — the same deftest runs under 3 interpreters: async, sync, and sim")
     (law a-test-sleeps-only-when-it-declares-realtime
       :statement "for_all Python check t in the rule's range (.py whose path is under a tests dir, and files with check names test_*.py, *_test.py, conftest.py wherever they are) and Hy check t in the Hy rule's range (Hy files whose path is under a tests dir, and Hy files with check names test_*.hy, *_test.hy wherever they are [R8]): t calls time.sleep(x), asyncio.sleep(x) (x ≠ 0), or threading.Event().wait(x) ⇒ (t declares marker realtime ∧ t is listed with a reason in [tests] of the registry tests/realtime_tests.toml) ∨ (t's dir is in the rule's exclude ∧ that dir is listed with a reason in the registry's [suites]) ∨ (t is a standalone script that cannot declare marks, named by file in the Hy rule section's exclude, and listed with a reason in the registry's [tests]). Within the range, line annotations that take the rule off number 0"
       :counterexamples
         [(counterexample "A check that waits for a result with time.sleep(0.5) — on a busy machine 0.5 s is not enough and it goes red; on a fast machine it throws away 0.5 s every time")
          (counterexample "A check that declares realtime but is not in the registry — what about real time is the subject is not recorded, and rule exclusions grow without reasons")
          (counterexample "Taking the rule off by putting a nosemgrep annotation on the sleep line (the shape of the 7 places before the fix) — checks waiting in real time grow without going through the registry, and the reason survives only as a comment in the file")
          (counterexample "Paraphrasing time.sleep(0.1) as threading.Event().wait(0.1) — it merely dodges the rule's pattern; the wait is the same")
          (counterexample "Adding a dir to the rule's paths.exclude without listing it in the registry's [suites], or putting checks in a dir not named tests — places the rule cannot reach grow without reasons")
          (counterexample "Waiting for a result with (time.sleep 0.5) inside a Hy deftest — semgrep cannot read Hy, so doeff-no-sleep-in-tests does not fire, and without DOEFF211 nobody stops it (the shape of the 62 hits before DOEFF211 was added)")
          (counterexample "Paraphrasing (time.sleep 0.1) in a Hy check as (.wait (threading.Event) 0.1) or (.sleep time 0.1) (the earlier shape of test_compute_effects.hy) — it merely dodges the rule's reading; the wait is the same")
          (counterexample "Believing a Hy check file is declared via (setv pytestmark pytest.mark.realtime) or a binding inside a function — doeff-hy does not record these shapes as marks, so pytest does not mark them, and neither the rule nor the registry check reads them as declarations")
          (counterexample "Taking a Hy check or tool that can import pytest out of the rule by naming it by file in the section's exclude — naming is a path only for standalone scripts that cannot declare marks, and the registry check turns it red")]
       :enforced-by [".semgrep.yaml::doeff-no-sleep-in-tests"
                     "tests/semgrep/test_no_sleep_in_tests_rule.py::test_only_the_marked_lines_fire"
                     "tests/semgrep/test_no_sleep_in_tests_rule.py::test_the_rule_reaches_test_named_files_outside_a_tests_dir"
                     "tests/semgrep/test_realtime_tests_registry.py::test_registry_lists_exactly_the_realtime_marks"
                     "tests/semgrep/test_realtime_tests_registry.py::test_every_registry_entry_gives_a_reason"
                     "tests/semgrep/test_realtime_tests_registry.py::test_the_rule_reaches_what_this_scan_reads"
                     "tests/semgrep/test_realtime_tests_registry.py::test_the_rule_excludes_exactly_the_registered_suites"
                     "tests/semgrep/test_realtime_tests_registry.py::test_no_comment_takes_the_rule_off"
                     "packages/doeff-linter/tests/test_sleep.rs::only_the_marked_lines_fire"
                     "packages/doeff-linter/tests/test_sleep.rs::a_module_that_declares_realtime_has_no_hits_and_loses_them_only_by_the_declaration"
                     "packages/doeff-linter/tests/test_sleep.rs::the_population_is_the_test_places_and_the_test_names"
                     "packages/doeff-linter/tests/test_sleep.rs::the_hy_tests_of_this_repo_sleep_only_where_they_declare_realtime"
                     "packages/doeff-linter/tests/test_sleep.rs::the_sample_of_a_sleeping_test_is_found_under_the_repo_s_own_settings"
                     "tests/semgrep/test_realtime_tests_registry.py::test_the_linter_excludes_exactly_the_registered_suites_and_its_own_samples"
                     "tests/semgrep/test_realtime_tests_registry.py::test_a_script_the_linter_names_does_not_also_declare"
                     "tests/semgrep/test_realtime_tests_registry.py::test_the_hy_scan_reads_the_declarations_the_rule_excuses"]
       :wiring "Wired (2026-10-14, ba5513169) — the Python rule is applied by the commit hook and the daily scan. The Hy rule DOEFF211 is applied by the rust stage of the daily full verification (cargo test via make test-rust — packages/doeff-linter/tests/test_sleep.rs judges this repo's Hy checks with the root settings and names hits by file and line), and locally by make lint-doeff (checks under packages/) and the editor's single-file run. It is not in the commit hook (the root doeff-linter hook measures only Python files). Reconciliation with the registry (both Python and Hy) is owned by tests/semgrep/test_realtime_tests_registry.py")
     (law the-sleep-rules-stop-as-errors-from-one-definition
       :statement "for_all r ∈ {doeff-no-sleep-in-programs, doeff-no-sleep-in-tests}: |{rules in .semgrep.yaml | id = r}| = 1 ∧ severity(r) = ERROR ∧ r's polarity check applies r itself from .semgrep.yaml"
       :counterexamples
         [(counterexample "Lowering severity to WARNING (the 2026-10-12 shape) — merges to main are not blocked, and sleeps remained in 30 files under tests/")
          (counterexample "Copying the rule's pattern into a defsemgrep in this file — the definition exists in 2 places, and on the day only one is fixed the file's check and the real rule look at different things")
          (counterexample "Rewriting a polarity check's RULE to another rule's id — the check stays green while nobody verifies the shape of the sleep rule")]
       :enforced-by ["docs/adr/defadr_doeff_time_001_programs_wait_by_time_effects.hy::test-adr-doe-time-001-the-sleep-rules-stop-as-errors"
                     "docs/adr/defadr_doeff_time_001_programs_wait_by_time_effects.hy::test-adr-doe-time-001-each-sleep-rule-is-read-by-its-polarity-test"]
       :wiring "Wired (this file)")]
  :enforcement
    [(deftest test-adr-doe-time-001-the-sleep-rules-stop-as-errors
       ;; Each rule exists exactly once in .semgrep.yaml with severity ERROR (R4 — lowering to WARNING, deleting, or placing the same id twice is red).
       (val repo-root (. (Path __file__) parent parent parent))
       (val severities (run (rule-severities (.read-text (/ repo-root SEMGREP-CONFIG) :encoding "utf-8"))))
       (for [rule-id POLARITY-TESTS]
         (assert (= (.get severities rule-id) ["ERROR"])
                 (+ rule-id ": .semgrep.yaml should have exactly 1 rule with severity ERROR (ADR-DOE-TIME-001 R4): "
                    (str (.get severities rule-id))))))
     (deftest test-adr-doe-time-001-each-sleep-rule-is-read-by-its-polarity-test
       ;; A polarity check holds the rule's id in RULE and .semgrep.yaml in CONFIG (R4 — applies the real rule, not a copy).
       (val repo-root (. (Path __file__) parent parent parent))
       (for [#(rule-id rel) (.items POLARITY-TESTS)]
         (val subject (run (polarity-subject (.read-text (/ repo-root rel) :encoding "utf-8"))))
         (assert (= subject #(rule-id SEMGREP-CONFIG))
                 (+ rel ": the polarity check does not apply the .semgrep.yaml rule " rule-id " (ADR-DOE-TIME-001 R4): " (str subject)))))
     (deftest test-adr-doe-time-001-every-law-names-living-checks
       ;; Every :enforced-by entry of the laws points to a rule or check in the current tree (deleting a rule or renaming a check is red — laws do not survive as names only).
       (val repo-root (. (Path __file__) parent parent parent))
       (<- spec (GetAdr ADR-ID))
       (val laws (. spec laws))
       (val unbound (lfor l laws :if (not (.get l "enforced_by")) (get l "id")))
       (assert (= unbound []) (+ "Laws not bound to any rule or check (ADR-DOE-TIME-001): " (str unbound)))
       (val entries (lfor l laws entry (get l "enforced_by") entry))
       (val rules (sfor entry entries :if (.startswith entry (+ SEMGREP-CONFIG "::")) (get (.partition entry "::") 2)))
       (assert (= rules (set POLARITY-TESTS)) (+ "The rules bound by laws differ from the rules in the polarity-check table: " (str (sorted rules))))
       (val dead (run (dead-checks repo-root entries)))
       (assert (= dead []) (+ "Laws' :enforced-by points to rules or checks not in the current tree (ADR-DOE-TIME-001): " (str dead))))
     (deftest test-adr-doe-time-001-judgments-reject-the-counterexamples
       ;; Counterexamples: a WARNING rule, 2 rules with the same id, a polarity check applying another rule, and a polarity check applying a copied config each yield values that fail the comparisons above.
       (val config "rules:\n  - id: a\n    severity: WARNING\n  - id: b\n    severity: ERROR\n  - id: b\n    severity: ERROR\n")
       (assert (= (run (rule-severities config)) {"a" ["WARNING"] "b" ["ERROR" "ERROR"]}))
       (assert (= (run (rule-severities "")) {}))
       (assert (= (run (polarity-subject "RULE = \"doeff-no-star-imports\"\nCONFIG = REPO_ROOT / \".semgrep.yaml\"\n"))
                  #("doeff-no-star-imports" ".semgrep.yaml")))
       (assert (= (run (polarity-subject "RULE = \"doeff-no-sleep-in-tests\"\nCONFIG = FIXTURES / \"copy.yaml\"\n"))
                  #("doeff-no-sleep-in-tests" "copy.yaml")))
       (assert (= (run (polarity-subject "import pytest\n")) #(None None)))
       ;; Reading names: spellings inside comments and strings, and methods inside classes, are not counted as check names.
       (assert (= (run (check-names "tests/sample.py" "# def test_gone(): ...\ndef test_here():\n    pass\nclass T:\n    def test_inner(self):\n        pass\n"))
                  (frozenset #{"test_here"})))
       (assert (= (run (check-names "tests/sample.hy" ";; (deftest test-gone)\n(print \"(deftest test-text)\")\n(defadr X :enforcement [(deftest test-here (assert True))])\n"))
                  (frozenset #{"test-here"})))
       (assert (= (run (check-names SEMGREP-CONFIG config)) (frozenset #{"a" "b"})))
       ;; Rust check names: only functions with #[test]. Helper functions and commented-out checks are not counted.
       (assert (= (run (check-names "tests/sample.rs" "fn helper() {}\n\n#[test]\nfn fires() {}\n\n// #[test]\n// fn gone() {}\n\n    #[test]\n    fn nested_in_a_mod() {}\n"))
                  (frozenset #{"fires" "nested_in_a_mod"})))
       ;; Rules not in the current tree, missing files, and entries without a name are all listed as unbound entries.
       (val repo-root (. (Path __file__) parent parent parent))
       (val missing [".semgrep.yaml::doeff-no-such-sleep-rule"
                     "tests/semgrep/test_no_such_file.py::test_only_the_marked_lines_fire"
                     "tests/realtime_tests.toml"
                     "packages/doeff-linter/tests/program_sleep.rs::no_such_rust_test"
                     (+ THIS-FILE "::test-adr-doe-time-001-no-such-test")])
       (assert (= (len (run (dead-checks repo-root missing))) 5))
       (assert (= (run (dead-checks repo-root [(+ THIS-FILE "::test-adr-doe-time-001-judgments-reject-the-counterexamples")])) [])))]
  :plans [])
