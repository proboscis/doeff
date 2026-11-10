;;; Executable ADR: the canonical enforcement gate is the default local pytest.
;;; It does not depend on GitHub CI (stopped due to budget). doeff guards its own invariants
;;; with its own mechanisms (defadr / deftest / defsemgrep), inside the default run.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest val])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-ENFORCE-001
  :title "pytest canonical gate: all of doeff's enforcement (defadr collection, defsemgrep static checks, the VM conformance oracle, the ledger ratchet) is collected and run by the default `uv run pytest`. Checks that only run when invoked manually, checks that go green by skipping, and checks that stay silent outside testpaths are forbidden"
  :status "proposed"
  :scope ["pyproject.toml"
          "uv.lock"
          "Makefile"
          ".semgrep.yaml"
          ".pre-commit-config.yaml"
          "packages/doeff-adr/src/doeff_adr"
          "packages/doeff-vm-core/Cargo.toml"
          "scripts/check_enforcement_ledger.py"
          "scripts/git-hooks/pre-commit"
          "tests/test_daily_test_population.py"
          "docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy"]
  :problem
    [(fact
       "Verdict of the 2026-07-02 erosion audit: doeff's declared invariants are ENFORCED 7 / PARTIAL 4 / EXISTS-NOT-WIRED 10 / DOC-ONLY 6. The audit's conclusion: 'Currently, there are zero places where the default run guards its own invariants with the defadr mechanism.'"
       :evidence "docs/crystallization/erosion-audit-2026-07-02.md")
     (fact
       "The VM conformance oracle (runtime invariants I1–I8 — mechanizing the lessons extracted from 3 rebuilds) is implemented under doeff-vm-core's cargo feature `invariant-checks` (+ `python_bridge`), but there is no launch path in either the Makefile or the default tests (`grep invariant Makefile` -> 0 hits, measured 2026-07-14)."
       :evidence "packages/doeff-vm-core/Cargo.toml:14-20; Makefile(2026-07-14); docs/crystallization/invariants.md")
     (fact
       "When this ADR was drafted, the 229 rules in .semgrep.yaml (measured 2026-07-14) ran only via a manual `make lint-semgrep`, and semgrep was not even a dev dependency. The R3 implementation added `semgrep>=1.161.0,<2` as a dev dependency, pinned to 1.169.0 in uv.lock, so `make sync` alone installs it. In the sister repository ACP, the first gate run on 2026-07-11 turned 16 checks red immediately (semgrep binary missing) — a check that is 'written but never run' creates false reassurance even if it has teeth."
       :evidence ".semgrep.yaml; docs/crystallization/erosion-audit-2026-07-02.md; agent-control-plane .github/workflows/ci.yml first run 2026-07-11")
     (fact
       "pyproject's testpaths are only tests + 4 package tests and do not include docs/adr. The 10 existing defadrs are invisible to the default pytest. Three repositories, doeff / proboscis-ema / agent-control-plane, fell into the same trap at the same time (defadr silently not collected outside testpaths) — not carelessness of each repository but a systematic defect to be crushed in the owning layer (doeff-adr)."
       :evidence "pyproject.toml:80-86 (2026-07-14); erosion-audit cross-cutting finding #3; doeff issue doeff-adr-wiring-selfcheck")
     (fact
       "GitHub Actions is stopped due to budget constraints, and re-enabling it is not an option (decided 2026-07-14). The gate can only live in local runs that developers and agents always execute."
       :evidence "2026-07-14 doeff investment plan discussion")]
  :context
    [(interpretation
       "The dominant failure mode is not 'absence of checks' but 'absence of wiring' (cross-cutting finding #1 of the erosion audit). The checks are well written — they just do not run. So this ADR's job is not to write new checks but to move existing checks to where 'pytest green = enforcement ran' holds structurally.")
     (interpretation
       "Since the authors are agents, the canonical gate must be 'something agents always run in their own loop'. That is pytest. GitHub CI is (budget aside) outside the agent's loop.")
     (interpretation
       "Skip is a breeding ground for false green. A missing semgrep binary, missing fixtures, or an unbuilt feature must surface as a hard fail, not a skip — fail-fast is the basic policy of this family of repositories.")]
  :decision
    [(rule R1 "The canonical enforcement gate is the default `uv run pytest` (testpaths collection). It does not depend on GitHub CI. pre-commit / make may be aliases of this gate, but not substitutes.")
     (rule R2 "testpaths includes docs/adr. In addition, the doeff-adr package owns and provides a defadr collection self-check (checking that the number of defadr_*.hy files equals the number of collected ADR modules), inherited by all consuming repositories (the root fix for issue doeff-adr-wiring-selfcheck).")
     (rule R3 "The rules in .semgrep.yaml are put into the default pytest collection via defsemgrep (installed-rule form). A missing semgrep binary is a hard fail, not a skip. semgrep is a dev dependency and is always installed by `make sync`.")
     (rule R4 "The dev build builds doeff-vm-core with features `invariant-checks` + `python_bridge` enabled (2026-07-14 B3 ruling). The VM conformance oracle (I1–I8) is launched from pytest. Oracle tests on a build with invariant-checks disabled are a hard fail (skip forbidden). [2026-09-29 supplement — moved to enabling at runtime] Splitting builds by cargo feature created two paths, make sync (with checks) and plain uv sync (without checks; venv-run fires it automatically when the lock changes), and the same venv became 15x faster or slower depending on which path built it last (measured: one agora check took 3.3-5.7 s / 49.8-62.2 s). The checks are compiled into every build and enabled at runtime — via the environment variable DOEFF_VM_INVARIANT_CHECKS=1 (the extension reads it once at the first step; any value other than 0 / 1 panics) or doeff_vm.doeff_vm.set_invariant_checks(True). doeff's pytest has the root conftest.py enable it (child processes inherit the variable); elsewhere (agora and production) it is disabled by default. The cargo feature `invariant-checks` is kept, meaning 'always enabled', for the Rust conformance tests (make test-vm-invariants). The rule that tests/test_vm_invariant_checks_enabled.py hard-fails a pytest run with the oracle disabled is unchanged. The cost when disabled is one atomic read per step.")
     (rule R5 "anti-drop ratchet: put in the default collection a meta-test that fails if the enforcement ledger (defadr count, law count, defsemgrep count, deftest enforcement count) silently decreases (a pytest version of orch SpecInventorySpec). An intentional reduction of the ledger comes with an explicit update of the ledger file.")
     (rule R6 "The gate's wall-clock deadline is a function of the machine's overload ratio, not a constant (added 2026-08-17 — fix the deadline side). What a deadline should catch is a hang, not busyness. The duration of tests that wait on external processes (semgrep, CLI, build) grows in proportion to the overload ratio, so a constant deadline degenerates, in the overloaded band, into 'a device that turns correct work red'. Factor = 1-minute load average / core count (floor 1.0, cap env PYTEST_DEADLINE_SCALE_CAP default 8 — because of the cap, a true hang still fails within bounded time). Disable with env PYTEST_DEADLINE_SCALE=off (for CI that controls its own load). [The two deadlines always move together] Both pytest-timeout's per-test deadline and the SIGKILL watchdog standing above it scale by the same factor, and the watchdog must always be strictly above the per-test deadline — raising only one turns 'one slow test goes red' into 'the whole run is lost to SIGKILL' (measured 2026-08-17: applying PYTEST_TIMEOUT=600 against the bare 90-second watchdog killed the full battery instantly at 45%). Marker deadlines (@pytest.mark.timeout) are scaled by the same factor too — pytest-timeout gives markers precedence over ini, so scaling only ini leaves 'tests that declared themselves slow' = exactly the tests that launch external processes stuck with the bare deadline. [Scaling is not silent] A run whose factor exceeds 1 prints to stderr that it scaled and the effective values (a scaled deadline is also the signal 'this machine is overloaded', and a run that took 8x and silently returns green is a missing observation). [There are three deadlines] Besides the inner two (per-test, watchdog), the time budget of the gate run itself (gate.timeout_s in the landing declaration) is the third deadline. Scaling the inner ones necessarily lengthens the total run time (naturally, since tests that failed at 60 s can now run for hundreds of seconds; this is design, not a defect), so leaving the third deadline unchanged makes the inner fix merely move 'one test red' to 'the whole run times out' — measured 2026-08-17: a batch that fixed only the inner ones hit the gate's 2700 s at 2710.7 s (breakdown = Rust rebuild about 12 min + battery over 33 min, load around 100). The third deadline is a static declaration read by the federation mechanism (dotfiles land.py) and has no hook to follow load, so declare a value that lets even a run whose factor is pinned at the cap finish, and write the rationale next to the declaration (7200 as of 2026-08-17). [2026-09-27 supplement — how the watchdog ends] The 'SIGKILL watchdog' above records the shape as of 2026-08-17. The watchdog stopped using SIGKILL and changed to naming the stuck check in pytest's short-summary form (`FAILED <nodeid> - WATCHDOG: …`) and exiting with exit code 1 (because the landing tool reads a run ending by signal as 'killed from outside' and does not count it as red). Furthermore, when the daily session writer (plugin `ai-session-answer`) is installed, the stuck check is also named through its hook `answer_stopped`. The rule that the two deadlines move by the same factor and the watchdog sits above the per-test deadline is unchanged. [2026-10-12 supplement — the denominator is the number of usable CPUs] 'Core count' above is read as the number of CPUs this process can use = the smaller of the affinity (os.sched_getaffinity) and the CPU quota of its own cgroup and its ancestors (cgroup v2 cpu.max). Dividing by the machine's core count (os.cpu_count()) reads the overload ratio as half when a Pod with a quota of 8 runs on a 16-core machine (measured in the 2026-10-11 20:00 daily on node-a: the worker Pod kept using 7.3-8.0 of its 8-core quota from 20:10 to 20:40. At load 12 the old formula gives 0.75 and leaves the deadline unchanged, yet the Pod's 8 CPUs are packed 1.5x). load is a machine-wide number, so load outside the cgroup errs toward reading the factor high (the side where the deadline gets longer, bounded by the cap). The same factor is also applied, via the root conftest fixture deadline_scale_factor, to time limits that checks pass to children themselves (subprocess timeout=) (in the same run, a child with a fixed 30 seconds in packages/doeff-adr/tests/test_indexed_collection.py was killed).")
     (rule R7 "R5's ledger reconciliation also runs at authoring time (added 2026-08-21 — the root fix for daily verify red doeff-verify-20260821-065005). The live case: the new ADR-DOE-HY-004 (f47f0a4b) carried defadr +1, deftest +2, law +1 without updating the ledger, but the landing window is mechanics-only (decided 2026-08-17, mode = focus), and moreover this commit reached main by a direct push without going through the land queue — since a legitimate path exists that no hardening of the window can catch, the only detection point that can structurally stop missed bookkeeping is authoring time = git commit time (on any path, a commit always goes through the authoring machine's git). Implementation: the single point defining the count is scripts/check_enforcement_ledger.py (stdlib only — runs regardless of venv or dependency state), and the default pytest R5 check tests/test_enforcement_ledger.py consumes the same home (no second definition point — from the day the regexes diverge, hook green = verify green no longer holds). The hook (tracked original = scripts/git-hooks/pre-commit; install = make hooks-install; on machines with the pre-commit framework, enforcement-ledger in .pre-commit-config.yaml) reconciles the staged snapshot (index) — reconciling the working tree would let 'fixed the ledger too but forgot to stage it' pass. It does not judge during a rewrite (rebase / cherry-pick / sequencer) — so as not to block the landing window's catch-up and replay. The canonical gate remains the default pytest as in R1 (the hook implements R1's 'pre-commit may be an alias of the gate' and is not a substitute — machines without the hook and --no-verify are still caught by the daily verify).")
     (rule R8 "The population of the daily full verification is four things: the root pytest (testpaths), per-package pytest (make test-packages), per-Rust-crate cargo test (make test-rust), and make test-hy-floor, which runs doeff-hy's checks on the declared minimum Hy version (added 2026-10-12 — the minimum is read from packages/doeff-hy/pyproject.toml); they are placed in separate processing stages of gate.full in the daily gate's declaration (added 2026-09-25; since 2026-11-10 the declaration is kept outside this repository). Only the leading build stage (make sync) may stop what follows; every test stage redoes make sync itself (remote sync deletes target/ per stage, and stages may fall back to local one at a time). A red in one population does not leave other populations unexecuted. The package population visits every package that has check files other than fixtures in packages/<p>/tests, plus roots outside a package's tests/ (declared at one point, PACKAGE_EXTRA_TEST_ROOTS in the Makefile); it does not stop at one package's red, and at the end exits non-zero naming the failed packages. Failure names are emitted relative to the repo root (the daily tool takes failure names verbatim from the summary line and passes them as-is to pytest from the repo root). The repo's check files (pytest python_files test_*.py / *_test.py; Hy test_*.hy collected by the doeff-adr plugin via doeff_hy_test_files in the root ini (both deftest files and scripts executed top to bottom; the collector is not copied into conftest.py; a test_*.py / test_*.hy hit by no collector is red); the defadr_*.hy pattern collected by the doeff-adr plugin — excluding under fixtures; defined at one point, tests/test_daily_test_population.py) are under a population root or in the table of exclusions with reasons in tests/test_daily_test_population.py. Packages do not have their own pytest settings (if they did, the root ini and conftest would not apply to the package population). The behavior itself is pinned by tests/test_daily_test_population.py, which runs every time in the root stage = daily. The declaration shape was pinned by this ADR's deftest until 2026-11-10; the deftest left this repository with the declaration and the shape is no longer checked here.")
     (rule R9 "The ledger is a list generated from the tree and is not written by hand (added 2026-09-28). The live case: e15f9516 (2026-09-26, retirement of agentd) deleted the ADR-DOE-AGENTS-012 file, 7 semgrep rules, and 1 law of ADR-DOE-AGENTS-004, but entered main without lowering the ledger; afterwards every commit touching docs/adr failed on this hook, and workers bypassed it with SKIP=enforcement-ledger / --no-verify (10+ batches, 2026-09-27 to 28). The count ledger had three defects — (a) git cannot merge counts as the sum of two parallel batches (2026-09-17 ed98775a: two batches each bumped the same 104 -> 105, silently merged as 105), (b) textual counting (occurrences of `\"(law \"`) also counted spellings inside comments and strings (2026-09-28: law 134 vs. 132 forms; deftest 82 vs. 80), (c) it blamed drift already on main on later commits unrelated to the drift. Shape: the ledger docs/adr/enforcement-ledger.json is a **list of names** of enforcement assets (defadr file names, .semgrep.yaml rule ids, and ADR laws / deftests / defsemgreps as 'ADR id name'), created only by scripts/check_enforcement_ledger.py --write (= make enforcement-ledger). Removed items remain by name in the ledger diff, and --write reports removed items by name — the place to see whether a deletion was intended is the ledger diff. ADRs are counted as forms using a Hy reading (strings, `;` comments, forms discarded with `#_`, and the inside of `#[[…]]` are not counted), and tests/test_enforcement_ledger.py checks against all real ADRs that this reading returns the same answer as the Hy reader. The hook (--staged) passes if the ledger matches, and even if it does not, passes when **that commit's own delta** (the difference from HEAD) is reflected as-is in the ledger delta, only reporting drift that already existed at HEAD (the responsibility of the commit that introduced the drift; do not block later commits and make bypassing the norm). Exact match continues to be imposed by the default pytest (daily). The proposal to add reconciliation to the landing window is not adopted — the window is mechanics-only (decided 2026-08-17). [2026-09-28 supplement — report the source of red separately] The live case: even after the fix above (8c4754cb), workers kept bypassing with --no-verify under the pre-fix understanding that every commit touching docs/adr goes red because the ledger on main is drifted, and among those, b32941c3 (which added law defk-answers-are-not-used-bare to ADR-DOE-HY-007) entered main without being put in the ledger — the red the hook emitted at that time was that commit's own missed bookkeeping. The hook's red report names separately 'the part of this commit's delta not in the ledger' and '(for reference) drift already at HEAD', and states in the text that the red comes only from the former. The one-sided shape that passes on increases and goes red only on decreases (the idea of agora-controllers scripts/ratchet_tables.hy) is not adopted — the ledger is a list of names and parallel additions merge line by line, so there is no source of flapping, and a shape that does not put added items in the ledger leaves no name in the ledger diff when that item is later removed, so R5's watch does not work.")
     (rule R10 "The processing stages of the daily full verification (gate.full in the daily gate's declaration) run directly where the daily task runs, not by shipping the tree to another machine — do not write a remote_check prefix (`remote_check.py --node <machine> … -- sh -c \"…\"`) in run (added 2026-10-10 — assignment by coordinator cisco-c8). Why: on node-h, conversation speed has top priority (decided 2026-10-05). Since 2026-10-10 16:00 the daily task runs in node-a's static doeff-worker Pod (agora-controllers' daily-verify), but all 6 doeff stages shipped the tree to node-h via `remote_check.py --node node-h`, so the Rust build, the root pytest, the checks of 18 packages, and cargo test landed on node-h's CPU and competed with conversation turns. The alternative `--node node-a` (ssh to the node-a machine) is not taken — it needs an ssh key to the machine and preparation of the machine's environment, which amounts to operating the machine. For the uv cache location, the caller's UV_CACHE_DIR (the daily task passes the worker's shared cache) is used first, falling back to the dedicated lane `${XDG_CACHE_HOME:-$HOME/.cache}/uv-lanes/land-doeff` only when absent — the task's HOME is an empty dir per run, so a lane under HOME is a cold cache every time. The tool dependencies at the run location (redis-server for the packages stage, doeff-linter at the baseline key for the lint stage) are prepared by the daily verification job (node-h's land-arm development build and snapshot location are not in the node-a task's HOME; until 2026-11-10 they were prepared at one point in this repository). R8's 'every test stage redoes make sync itself' is kept — stages run in order on the same tree, so make sync from the second time on only confirms the uv sync, and even rerunning a single stage stays self-contained. Rollback: rewrap each stage's run with the remote_check prefix (`python3 $HOME/dotfiles/agentcli/src/agentcli/remote_check.py --node node-h --tree . --label doeff-gate-full --sync-timeout 900 --run-timeout 3600 --fallback-local -- sh -c \"…\"`; the lint stage uses label doeff-gate-lint) (with inner `$` as `\\$` so the remote sh resolves it), and remove this R10 and law daily-stages-run-where-the-task-runs. [2026-11-10 supplement] The declaration is kept outside this repository since 2026-11-10; the deftest that pinned this rule's declaration shape left with it, and the shape is no longer checked here.")]
  :laws
    [(law default-pytest-sees-all-enforcement
       :statement "for_all declared_enforcement e: collected_by(default_pytest, e) AND (missing_dependency(e) => hard_fail, not skip)"
       :counterexamples
         [(counterexample "The current state where 10 defadrs are silent outside testpaths — `uv run pytest` is green with zero enforcement")
          (counterexample "In an environment without semgrep, defsemgrep is skipped and the suite is green while all 229 rules are dead")
          (counterexample "Against a release build with invariant-checks disabled, the oracle tests are skipped and it is green while the VM invariants are unverified")])
     (law deadline-measures-hang-not-load
       :statement "effective_deadline == base_deadline * clamp(loadavg1 / usable_cpus, 1.0, CAP) where usable_cpus == min(affinity, cgroup_cpu_quota); scaled_together(per_test_deadline, watchdog) AND watchdog > per_test_deadline for_all scales; scale > 1 => announced"
       :counterexamples
         [(counterexample
             "Measured 2026-08-17 (before the fix): on an 18-core machine running steadily at load 60-156, against a constant 60-second per-test deadline, semgrep alone used 29.7 seconds at load 80, and exceeded it once the load of the whole battery was added. The landing gate failed 5 times in a row (a different external-process-launching test each time), and the same tests failed the same way when run alone on an untouched main = not a defect of the branch but a property of the deadline. 9 landings across 2 seats died, and the correct fix could not be shipped")
          (counterexample
             "Scaling only the per-test deadline — the SIGKILL watchdog above it stays constant, so instead of one slow test going red, the whole run dies instantly and even the results of passed tests are all lost (measured 2026-08-17: the full battery was SIGKILLed at 45% with PYTEST_TIMEOUT=600). There are two deadlines, and moving only one is worse than moving neither")
          (counterexample
             "Scaling only the ini deadline and leaving markers (@pytest.mark.timeout) bare — pytest-timeout gives markers precedence over ini, so only the tests that declared 'I am slow' = exactly the tests that launch external processes are left behind")
          (counterexample
             "Putting no cap on the factor — on a machine with unbounded overload the deadline effectively disappears and a true hang never fails (the flip side of 'mistaking busy for a hang': now a hang is mistaken for busy)")
          (counterexample
             "Scaling silently — a run that passed with an 8x deadline looks only 'green', and the equally important observation that the machine is overloaded is lost")
          (counterexample
             "Fixing only the inner two and leaving the gate's time budget (gate.timeout_s) unchanged — scaling the inner ones always lengthens the total run time, so red merely moves from 'one test timeout' to 'the whole run timing out', and landing still does not pass (measured 2026-08-17: a batch that fixed only the inner ones hit the 2700-second gate at 2710.7 seconds). There are three deadlines; fix them as a family")
          (counterexample
             "Dividing load by the machine's core count (os.cpu_count()) — when a Pod with a CPU quota of 8 runs on a 16-core machine, the factor is read as half even when the Pod is at its full quota. In the 2026-10-11 20:00 daily (node-a), the worker Pod was pinned at 7.3-8.0 cores from 20:10 to 20:40, and the child of doeff-adr's index check (1.5 seconds alone) was killed at a fixed 30 seconds")])
     (law ledger-checked-at-authoring-time
       :statement "for_all commit c touching(enforcement_assets): checked_against_ledger(staged_snapshot(c)) before commit_created on hook-installed machines; counting_home(pre_commit_hook) == counting_home(default_pytest_R5) == scripts/check_enforcement_ledger.py"
       :counterexamples
         [(counterexample
             "2026-08-21 live case: the new ADR-DOE-HY-004 (f47f0a4b) carried defadr +1, deftest +2, law +1 to main by a direct push without updating the ledger, and also slipped through the landing window (mechanics-only) — the first detection was delayed until the next morning's daily verify (doeff-verify-20260821-065005), and two innocent batches (L43 / L44) came under suspicion")
          (counterexample
             "Live case at installation (2026-08-09): during the bootstrap of installing the land queue, missed bookkeeping of semgrep_rules 246≠247 surfaced as a real red on main — this time is the second of the same shape. Not carelessness of individual authors but a defect of a structure that reconciles nothing at authoring time")
          (counterexample
             "A hook that reconciles only the working tree — a commit that fixed the ledger but forgot to stage it passes green, and enters history with the tree matching but the commit not matching")
          (counterexample
             "A second definition point for the count (the hook has its own regex) — from the day the test's and the hook's regexes diverge, hook green = verify green no longer holds, and the hook becomes a device that hands out false reassurance")
          (counterexample
             "A hook that also judges during a rewrite (rebase / replay) — the landing window's base catch-up is blocked by mismatches in intermediate snapshots unrelated to the batch, and the land queue jams")])
     (law daily-populations-are-independent-and-complete
       :statement "for_all population p in {root_pytest, package_pytest, rust_cargo_test}: count(stages s in gate.full where invokes(s, p)) == 1 AND blocks_rest(s) => s == build AND (red(p) => executed(q) for_all q != p); visited(make_test_packages) == { pkg : exists test_*.py or test_*.hy in packages/pkg/tests outside fixtures } regardless of red(pkg); failure_names(package_pytest) are repo_root_relative; for_all test-named file f: under(f, population_roots) OR f in excluded_with_reason"
       :counterexamples
         [(counterexample
             "The 2026-09-24 daily (snapshot f271ae39): gate.full was a single command chaining the root pytest, make test-packages, and make test-rust with &&; it stopped at 3 root reds (test_effectful 2, test_semgrep_gate 1), and not a single test of the package and Rust populations ran. With only one processing stage in the ledger, full, it recorded coverage = {complete: true, unexecuted: []} — it claimed 'measured everything' while there were populations that had not run")
          (counterexample
             "A change that overlooked a reader landed while green on a tree with no daily caller (one package's tests dir) — reds in that tree appear in nobody's daily")
          (counterexample
             "`|| exit 1` in the make test-packages loop: on a day when the first package in name order is red, none of the 26 packages after it run (measured with a fake runner making only the first package red — called 1 time)")
          (counterexample
             "Failure names from running pytest from a package dir: the summary line is relative to cwd, so it becomes `FAILED tests/test_cli.py::…`; 4 pairs with same-named test files (test_cli, test_effect_handlers, test_effects, test_types) collide across packages, and the daily tool cannot re-fire them from the repo root")
          (counterexample
             "A fixed test that takes the expected set of packages from the Makefile's actual run: if the Makefile skips a package that is in the root testpaths, the visited set and the expectation shrink together and it stays green (blind B)")])
     (law ledger-is-generated-from-the-tree
       :statement "ledger(tree) == inventory(tree) where inventory = sorted names of { defadr files, .semgrep.yaml rule ids, (law …) / (deftest …) / (defsemgrep …) forms read as Hy (not strings, comments, #_ forms) }; written only by check_enforcement_ledger --write; for_all commit c: hook passes c iff ledger(c) == inventory(c) OR (inventory(c) - inventory(HEAD)) == (ledger(c) - ledger(HEAD))"
       :counterexamples
         [(counterexample
             "2026-09-26 e15f9516: a commit that deleted the ADR-DOE-AGENTS-012 file (deftest 69, law 56, defsemgrep 5) and 7 semgrep rules entered main with the ledger counts unchanged, and afterwards 10+ batches touching docs/adr failed the hook on the unrelated drift and bypassed it with SKIP / --no-verify")
          (counterexample
             "2026-09-17 ed98775a: two parallel batches both bumped the ledger's deftest count 104 -> 105, and rebase silently merged it to 105 as the same change on the same line (actual 106) — counts do not add up under git merge")
          (counterexample
             "Textual counting text.count(\"(law \"): spellings inside comments of ADR-DOE-AGENTS-006 and ADR-DOE-CLUSTER-001 were counted as laws, giving law 134 (132 forms) and deftest 82 (80 forms) on main on 2026-09-28. Adding a single comment line turns the ledger red")])
     (law daily-stages-run-where-the-task-runs
       :statement "for_all stage s in gate.full: not ships_tree_to_another_machine(s) (no remote_check, no --node) AND for_all assignment UV_CACHE_DIR=v in run(s): v defers to the caller's UV_CACHE_DIR"
       :counterexamples
         [(counterexample
             "2026-10-10 16:18, the first doeff daily on node-a (log doeff-20261010-071839-31779, snapshot f858bd96): the task ran in node-a's doeff-worker Pod, but all 6 stages showed `remote-check: ran=remote node=node-h` — root pytest 1091 s, checks of 18 packages 2352 s, cargo test 200 s landed on node-h's CPU (contrary to the 2026-10-05 rule that on node-h conversation speed has top priority)")
          (counterexample
             "Running inside the task with UV_CACHE_DIR overwritten as `${XDG_CACHE_HOME:-$HOME/.cache}/uv-lanes/land-doeff` — the task's HOME is empty per run, so all 4 daily stages re-fetch dependencies from a cold cache (the worker's shared cache is in the caller's UV_CACHE_DIR but is not used)")])]
  :enforcement
    [(deftest test-adr-doe-enforce-001-docs-adr-in-default-testpaths
       ;; RED (2026-07-14): pyproject testpaths does not include docs/adr. Green with the R2 implementation.
       (import tomllib)
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv cfg (tomllib.loads (.read-text (/ root "pyproject.toml"))))
       (setv testpaths (get cfg "tool" "pytest" "ini_options" "testpaths"))
       (assert (in "docs/adr" testpaths)
               f"docs/adr is not in testpaths: {testpaths} — ADR-DOE-ENFORCE-001 R2"))
     (deftest test-adr-doe-enforce-001-vm-oracle-wired
       ;; RED (2026-07-14): the Makefile has no launch path for invariant-checks. Green with the R4 implementation.
       ;; 2026-09-29: the wiring is the root conftest.py enabling the oracle at runtime. Checks that it is enabled in this run
       ;; (= the wiring is effective), and that make sync does not split the build by feature.
       (import pathlib [Path])
       (import doeff [ReadInvariantChecks run])
       (import doeff_core_effects [vm-probe-handler])
       (setv root (get (. (Path __file__) parents) 2))
       (setv conftest (.read-text (/ root "conftest.py")))
       (setv makefile (.read-text (/ root "Makefile")))
       (assert (in "\"vm_invariant_checks\"" conftest)
               "the root conftest.py does not enable the VM oracle — ADR-DOE-ENFORCE-001 R4")
       (assert (run (vm-probe-handler (ReadInvariantChecks)))
               "the VM oracle is disabled in this pytest run — ADR-DOE-ENFORCE-001 R4")
       (assert (not-in "maturin develop --release --features" makefile)
               "make sync splits the VM build by feature — this reverts to the mismatch between two build paths"))
     (deftest test-adr-doe-enforce-001-deadlines-are-load-scaled
       ;; R6 + law deadline-measures-hang-not-load: a pin that both deadlines are
       ;; multiplied by the factor, that the factor has a single home, and that scaling
       ;; is not silent. The behavior itself is in tests/test_deadline_load_scaling.py
       ;; (5 tests: the factor's floor, cap, disabling, the watchdog never overtaken by per-test,
       ;;  and not clobbering the caller's explicit value).
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv conftest (.read-text (/ root "conftest.py") :encoding "utf-8"))
       ;; The factor's single home.
       (assert (in "def deadline_scale(" conftest)
               "the single home of the deadline factor is missing from the root conftest — ADR-DOE-ENFORCE-001 R6")
       (assert (in "os.getloadavg()" conftest)
               "the factor does not read the machine's overload ratio (regressed to a constant)")
       (assert (in "\"deadline_scale_cap\"" conftest)
               "the factor has no cap — a true hang no longer fails within bounded time")
       (assert (in "\"deadline_scale\"" conftest)
               "no disable switch (CI that controls its own load cannot fix the deadline)")
       ;; The two deadlines move together — the watchdog is derived from the per-test deadline.
       (assert (in "def scaled_watchdog_timeout(" conftest)
               "the watchdog is not derived from the per-test deadline (only one scales and the run is lost to SIGKILL)")
       ;; Scaling is not silent.
       (assert (in "deadline scale x" conftest)
               "a run whose factor exceeds 1 is silent (the overload observation is lost)")
       ;; Marker deadlines are multiplied by the same factor too.
       (assert (in "def pytest_collection_modifyitems(" conftest)
               "marker deadlines stay bare (pytest-timeout gives markers precedence over ini)")
       ;; Pin that the behavior itself exists (makes a deletion regression loud).
       (setv behavioral (/ root "tests/test_deadline_load_scaling.py"))
       (assert (.exists behavioral)
               "R6's behavior body tests/test_deadline_load_scaling.py has disappeared"))
     (deftest test-adr-doe-enforce-001-ledger-authoring-guard-wired
       ;; R7 + law ledger-checked-at-authoring-time: pin that the authoring-time wiring of ledger reconciliation exists.
       ;; The behavior itself is in tests/test_enforcement_ledger_hook.py (10 tests: count agreement, staged snapshot,
       ;; detection of forgotten staging, silence during rebase, letting unrelated commits pass).
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       ;; The count's single home.
       (setv checker (/ root "scripts/check_enforcement_ledger.py"))
       (assert (.exists checker)
               "the count's home scripts/check_enforcement_ledger.py has disappeared — ADR-DOE-ENFORCE-001 R7")
       ;; The hook's tracked original consumes the home on the staged snapshot.
       (setv hook (/ root "scripts/git-hooks/pre-commit"))
       (assert (.exists hook)
               "the hook's tracked original scripts/git-hooks/pre-commit has disappeared — R7")
       (setv hook-text (.read-text hook :encoding "utf-8"))
       (assert (in "check_enforcement_ledger.py" hook-text)
               "the hook does not call the count's home (regression to a second definition point)")
       (assert (in "--staged" hook-text)
               "the hook does not reconcile the staged snapshot (regression that lets forgotten staging pass)")
       ;; Machines with the pre-commit framework get the same check too.
       (setv pc (.read-text (/ root ".pre-commit-config.yaml") :encoding "utf-8"))
       (assert (in "check_enforcement_ledger.py --staged" pc)
               ".pre-commit-config.yaml has no enforcement-ledger check — the hook is missing on machines that install the framework")
       ;; The install path exists.
       (setv makefile (.read-text (/ root "Makefile") :encoding "utf-8"))
       (assert (in "hooks-install" makefile)
               "the Makefile has no hooks-install — the hook's install path is gone")
       ;; R9: the ledger is generated — the generation switch (--write) and the make entry exist.
       (assert (in "--write" (.read-text checker :encoding "utf-8"))
               "the count's home has no ledger generation switch --write — R9 (regression to a hand-written ledger)")
       (assert (in "enforcement-ledger:" makefile)
               "the Makefile has no ledger generation entry enforcement-ledger — R9")
       ;; Pin that the behavior itself exists (makes a deletion regression loud).
       (assert (.exists (/ root "tests/test_enforcement_ledger_hook.py"))
               "R7's behavior body tests/test_enforcement_ledger_hook.py has disappeared")
       ;; The default pytest R5 check consumes the same home.
       (setv r5-test (.read-text (/ root "tests/test_enforcement_ledger.py") :encoding "utf-8"))
       (assert (in "check_enforcement_ledger" r5-test)
               "the R5 check does not consume the count's home — regression to a second definition point"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
