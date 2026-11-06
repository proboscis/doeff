"""Tests for the Hy test time budget (doeff_hy_pytest/_budget.py).

Collection is measured in CPU time and per-marker execution in wall-clock time, so the tests in this file burn CPU instead of sleeping (burning CPU shows up on
both clocks). The test that waits on a child process, which shows up only on the wall clock, is CHILD_WAIT_CALL. The budget is small (0.05 s), and
the exceeding side spins for 0.3 s, leaving a margin so machine jitter does not flip pass/fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from doeff_hy_pytest._budget import (
    Budgets,
    DEFAULT_CALL_BUDGET_STEPS,
    DEFAULT_RUNAWAY_STEPS_MULTIPLIER,
    CompileCounter,
    CompileTally,
    DeclaredOverBudget,
    GcTally,
    ImportTally,
    Measurement,
    OverBudget,
    RATIO_FLOOR_STEPS,
    RATIO_MIN_TESTS,
    RATIO_NAME_CHARS,
    RATIO_NAMED,
    StaleDeclaration,
    StepCost,
    StepCostRatios,
    TooFewForRatios,
    StepDeclaration,
    RegisteredOverBudget,
    RegisteredWithinBudget,
    RegistryError,
    SettingError,
    UncheckedVmBuild,
    Verdict,
    VmWork,
    WithinBudget,
    WorldMark,
    WorldSteps,
    call_budget_for,
    declare_step_budget,
    declared_steps_of,
    judge,
    judge_call,
    judge_declared,
    judge_steps,
    load_registries,
    load_registry,
    parse_marker_budgets,
    parse_positive_steps,
    read_step_cost_line,
    registry_file_name,
    step_cost_line,
    step_cost_report,
    step_cost_name,
    world_steps,
)

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _own_stores(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests in this file measure the number of Hy compilations and the collection seconds, so each test runs with empty stores. pytester points HOME at an empty dir per test,
    so unless a store is named, the child's Hy code store and the doeff-adr collection records were empty for each test. When the runner (the daily task /
    worker) names shared stores through DOEFF_HY_CODE_STORE and DOEFF_ADR_ITEMS_CACHE, the child inherits them, the part read from the store is
    added to the collection seconds, and test_collect_budget_does_not_charge_a_shared_dependency_to_the_first_file went red (0.146 s in the daily run of 10-11 08:05 JST —
    after agora-controllers 220e2e963; with an empty store per test it was 0.054 s)."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(tmp_path_factory.mktemp("code-store")))
    monkeypatch.delenv("DOEFF_ADR_ITEMS_CACHE", raising=False)

CONFTEST = """\
import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program
"""

# Burn CPU to use up time (sleep does not show up in CPU time).
SPIN = "(import time) (defn spin [seconds] (setv t0 (time.process_time)) (while (< (- (time.process_time) t0) seconds) (setv _ None)))"

SLOW_CALL = f"""\
(require doeff-hy.macros [deftest])
{SPIN}
(deftest test-slow
  (spin 0.3)
  (assert True))
(deftest test-fast
  (assert True))
"""


def _slow_call_marked_as(marker: str) -> str:
    """The same two tests as SLOW_CALL (slow test-slow, fast test-fast — same keys), as a file marked with the marker through a module-level pytestmark."""
    return f"""\
(require doeff-hy.macros [deftest])
(import pytest)
(setv pytestmark [pytest.mark.{marker}])
{SPIN}
(deftest test-slow
  (spin 0.3)
  (assert True))
(deftest test-fast
  (assert True))
"""


# The execution-seconds budget exists only per marker (doeff_test_call_budget_by_marker), so a test that checks the seconds measurement itself makes its test file
# a real_world-marked test and puts a 0.05 s budget on that marker.
REAL_WORLD_SLOW_CALL = _slow_call_marked_as("real_world")
REAL_WORLD_SECONDS_INI = 'markers = ["real_world: real I/O"]\ndoeff_test_call_budget_by_marker = ["real_world=0.05"]\n'
FAIL_MODE_INI = REAL_WORLD_SECONDS_INI + 'doeff_test_budget_mode = "fail"\n'

SLOW_COLLECT = f"""\
(require doeff-hy.macros [deftest])
{SPIN}
(spin 0.3)
(deftest test-fast
  (assert True))
"""

# Burn CPU during macro expansion (inside the compilation) — stands in for the cost of an uncached compilation.
SLOW_COMPILE = f"""\
(require doeff-hy.macros [deftest])
(defmacro slow-expansion []
  {SPIN}
  (spin 0.3)
  1)
(setv VALUE (slow-expansion))
(deftest test-compiled
  (assert (= VALUE 1)))
"""


def _project(pytester: pytest.Pytester, ini: str, files: dict[str, str]) -> None:
    pytester.makeconftest(CONFTEST)
    pytester.makepyprojecttoml(
        '[tool.pytest.ini_options]\ndoeff_adr_hy_files = ["test_*.hy"]\n' + ini
    )
    pytester.makefile(".hy", **files)


def _measurement(cpu: float, compile_cpu: float = 0.0, key: str = "t.hy::test") -> Measurement:
    return Measurement(
        key=key,
        phase="call",
        cpu_seconds=cpu,
        wall_seconds=cpu,
        compile=CompileTally(1 if compile_cpu else 0, compile_cpu, compile_cpu),
    )


def test_judge_subtracts_the_compile_time() -> None:
    """CPU seconds of a compilation inside the window (uncached) are subtracted for the judgment — an uncached compile does not turn red."""
    assert isinstance(judge(_measurement(5.0, compile_cpu=4.5), 1.0, {}), WithinBudget)
    assert isinstance(judge(_measurement(5.0, compile_cpu=3.0), 1.0, {}), OverBudget)


def test_compile_counter_adds_only_the_outermost_window(tmp_path, monkeypatch) -> None:
    """The compilation of an import inside a compilation (Hy require) is not added twice. Imports that hit the cache are not counted."""
    import importlib
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    # So that the first import is truly an uncached compilation, point the code store that spans doeff-hy worktrees (env var
    # DOEFF_HY_CODE_STORE — doeff_hy_bytecode_guard/_loader_hooks.py) at an empty dir for this test. The default user cache
    # store persists across processes, so running this same-content module a second time would hit the store and give 0 compilations.
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(tmp_path / "code-store"))
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "budget_inner_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "budget_outer_mod.hy").write_text(
        "(defmacro load-inner [] (import budget-inner-mod) 1)\n(setv VALUE (load-inner))\n",
        encoding="utf-8",
    )
    counter = CompileCounter()
    counter.install()
    try:
        import hy  # noqa: F401 - installs the Hy import hook

        before = counter.tally()
        imports_before = counter.import_tally()
        importlib.import_module("budget_outer_mod")
        cold = counter.tally().since(before)
        # The outermost window is the first import of budget_outer_mod, so the CPU seconds of the inner compilation go into the import column exactly once (#1752).
        cold_imports = counter.import_tally().since(imports_before)
        for name in ("budget_outer_mod", "budget_inner_mod"):
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        before = counter.tally()
        importlib.import_module("budget_outer_mod")
        cached = counter.tally().since(before)
    finally:
        counter.uninstall()
        for name in ("budget_outer_mod", "budget_inner_mod"):
            sys.modules.pop(name, None)
    assert cold.count == 2
    assert cold.cpu_seconds == 0.0
    assert cold_imports.cpu_seconds > 0
    assert cached == CompileTally(0, 0.0, 0.0)


def test_compile_counter_counts_a_collection_once() -> None:
    """GC that happens outside the subtracted window is counted in the GC column; GC that happens inside a compilation window is already in the compile seconds, so it is not counted in the GC column
    (no double subtraction)."""
    import gc

    counter = CompileCounter()
    counter.install()
    try:
        before = counter.gc_tally()
        gc.collect()
        outside = counter.gc_tally().since(before)
        before = counter.gc_tally()
        with counter.converting():
            gc.collect()
        inside = counter.gc_tally().since(before)
    finally:
        counter.uninstall()
    assert outside.count == 1 and outside.cpu_seconds >= 0.0
    assert inside == GcTally(0, 0.0, 0.0)
    assert counter._on_gc not in gc.callbacks


def test_judge_uses_budget_and_registry() -> None:
    assert isinstance(judge(_measurement(0.5), 1.0, {}), WithinBudget)
    assert isinstance(judge(_measurement(1.5), 1.0, {}), OverBudget)
    assert isinstance(judge(_measurement(1.5), 1.0, {"t.hy::test": "reason"}), RegisteredOverBudget)


def test_registry_file_must_be_named_by_key_hash_and_carry_a_reason(tmp_path) -> None:
    key = "t.hy::test"
    (tmp_path / registry_file_name(key)).write_text(f"{key}\nslow reason\n", encoding="utf-8")
    assert load_registry(tmp_path) == {key: "slow reason"}
    (tmp_path / "000000000000.txt").write_text("other\nreason\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="hash"):
        load_registry(tmp_path)
    (tmp_path / "000000000000.txt").unlink()
    (tmp_path / registry_file_name("empty")).write_text("empty\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="reason"):
        load_registry(tmp_path)


def test_without_settings_nothing_is_measured(pytester: pytest.Pytester) -> None:
    _project(pytester, "", {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    assert "doeff test time budget" not in result.stdout.str()


def test_report_mode_warns_and_lists_but_stays_green(pytester: pytest.Pytester) -> None:
    """Report-only mode (the default) shows tests that exceeded the per-marker seconds budget in a warning and the listing, and does not turn them red."""
    _project(pytester, REAL_WORLD_SECONDS_INI, {"test_slow": REAL_WORLD_SLOW_CALL})
    # The inner pytest running in the same process inherits the outer run's warning filters — when the outer run used `-W ignore::pytest.PytestWarning`,
    # BudgetWarning (a subclass of PytestWarning) vanished and the test went red. Pin the visible side with the inner -W.
    result = pytester.runpytest("-q", "-W", "default::doeff_hy_pytest._budget.BudgetWarning")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*BudgetWarning: doeff test time limit exceeded: test_slow.hy::test_slow run (call)*",
            "*limit exceeded (report only*test_slow.hy::test_slow (call wall-clock *",
        ]
    )
    assert "test_fast (call" not in result.stdout.str()


# Tests that judge an overrun as red are pinned to the budget's reference build (unchecked) (by swapping the extension function that vm_probe_handler, which answers effect ReadInvariantChecks, reads) —
# the daily run uses the checked build from make sync, and since 8d4ff1bb overruns are not red there (the daily t104 red).
# Tests that run in a child process pin it in a conftest, which is read before pytest_configure, where the plugin
# reads the build kind.
PIN_UNCHECKED_VM_BUILD = "\nfrom doeff._vm import native as _vm_ext\n\n_vm_ext._invariant_checks_enabled = lambda: False\n"


def test_fail_mode_fails_the_slow_test_with_time_and_budget(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(pytester, FAIL_MODE_INI, {"test_slow": REAL_WORLD_SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*test_slow.hy::test_slow run (call) took wall-clock *limit wall-clock 0.050 s*"])


def test_checked_vm_build_reports_but_does_not_fail(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When doeff-vm is a checked build, an overrun is not red even in fail mode, and the heading says so (the budget is based on the unchecked build)."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: True)
    _project(pytester, FAIL_MODE_INI, {"test_slow": REAL_WORLD_SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*doeff-vm is a build with checks (invariant-checks) — the budget is based on a build without checks, so overruns in this run are not judged*",
            "*limit exceeded (report only*test_slow.hy::test_slow (call wall-clock *",
        ]
    )


def test_unchecked_vm_build_fails_and_names_the_build(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(pytester, FAIL_MODE_INI, {"test_slow": REAL_WORLD_SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(
        ["*doeff-vm is a build without checks — judged on the budget baseline build*", "*limit exceeded (red*"]
    )


def test_unknown_vm_build_is_named_and_judged_as_configured(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the build kind cannot be read (an old VM without the function — vm_probe_handler refuses with ImportError), the heading says "unknown" and the judgment stays as configured."""
    from doeff._vm import native as vm_ext

    monkeypatch.delattr(vm_ext, "_invariant_checks_enabled")
    _project(pytester, FAIL_MODE_INI, {"test_slow": REAL_WORLD_SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*doeff-vm build kind unknown (*_invariant_checks_enabled*"])


def _registered_project(pytester: pytest.Pytester, mode: str) -> None:
    """A project with both the slow test test_slow and the fast test test_fast in the registry (budget 0.05 s for marker real_world)."""
    _project(
        pytester,
        f'{REAL_WORLD_SECONDS_INI}doeff_test_budget_mode = "{mode}"\n'
        'doeff_test_budget_registry = "budget-breaches"\n',
        {"test_slow": REAL_WORLD_SLOW_CALL},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    registry = pytester.mkdir("budget-breaches")
    for key in ("test_slow.hy::test_slow", "test_slow.hy::test_fast"):
        (registry / registry_file_name(key)).write_text(f"{key}\nexisting slow test\n", encoding="utf-8")


def test_registered_test_is_not_failed_and_stale_entries_are_reported(
    pytester: pytest.Pytester,
) -> None:
    """report mode: an overrun in the registry is not red. A registration that is back within the budget is only reported as deletable."""
    _registered_project(pytester, "report")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*overrun listed in the registry: test_slow.hy::test_slow (call*",
            f"*registry entry back within the limit: test_slow.hy::test_fast; registry file {registry_file_name('test_slow.hy::test_fast')} can be deleted*",
        ]
    )


def test_stale_registration_is_red_in_fail_mode(pytester: pytest.Pytester) -> None:
    """fail mode: a registered test that finishes at or under half the budget is a stale registration and turns red (naming the file to delete). A registered overrun is not red.
    Counter-example = once the registration is deleted, both are green (the slow test stays registered)."""
    _registered_project(pytester, "fail")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(
        [f"*test_slow.hy::test_fast is in the registry, but*this is a stale registry entry, so delete the registry file {registry_file_name('test_slow.hy::test_fast')}*"]
    )
    (pytester.path / "budget-breaches" / registry_file_name("test_slow.hy::test_fast")).unlink()
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)


def test_registered_test_near_the_budget_is_reported_not_red() -> None:
    """A registered test that finishes between half the budget and the budget is only reported as deletable (no pass/fail flapping from jitter). At or under half is a stale registration."""
    registry = {"t.hy::test": "reason"}
    near = judge(_measurement(0.8), 1.0, registry)
    assert isinstance(near, RegisteredWithinBudget) and not near.stale
    far = judge(_measurement(0.3), 1.0, registry)
    assert isinstance(far, RegisteredWithinBudget) and far.stale
    assert isinstance(judge(_measurement(0.3), 1.0, {}), WithinBudget)


def test_collect_budget_subtracts_compilation_but_fails_slow_import(
    pytester: pytest.Pytester,
) -> None:
    """Uncached (the tests of this repo run with PYTHONDONTWRITEBYTECODE=1): a file that is heavy inside the compilation (slow macro expansion) does
    not turn red, and a file whose import execution is heavy does."""
    _project(
        pytester,
        'doeff_test_collect_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n',
        {"test_slow_collect": SLOW_COLLECT, "test_slow_compile": SLOW_COMPILE},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    result = pytester.runpytest_subprocess(
        "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors"
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        [
            "*test_slow_collect.hy load (import) took wall-clock * uncached conversions, wall-clock * s, CPU * s subtracted*"
        ]
    )
    assert "test_slow_compile.hy load (import)" not in result.stdout.str()


# Stands in for a session with many objects kept alive by tests that ran earlier — a heap of 3 million objects (the GC of free-threaded Python does not split generations and
# walks the whole heap each time. With 3 million about 0.2 s each time; in the agora-controllers daily session about 1 s each time).
LARGE_HEAP = """
HEAP = [[i] for i in range(3_000_000)]
"""

# A test file with a light body where GC happens during the load (when GC happens is decided by the number of allocations, and which file of the session
# it hits changes from run to run — here the body calls gc.collect 4 times to produce the hit deterministically; 4 times take about 0.8 s).
COLLECTED_WHILE_IMPORTED = """\
(require doeff-hy.macros [deftest])
(import gc)
(for [_ (range 4)] (gc.collect))
(deftest test-light
  (assert True))
"""


def test_collect_budget_does_not_charge_a_garbage_collection_to_the_file(
    pytester: pytest.Pytester,
) -> None:
    """CPU of GC (garbage collection) that happens inside the load window is not counted toward the weight of that file — the cost of GC is decided by the number of objects alive in the session,
    and which file's load it hits changes from run to run with the allocation count cycle. If it were counted, the file that fails would swap between runs even though neither the file nor what it imports changed
    (agora-controllers daily 10-10 to 10-11: a file that takes 0.03 s alone took 1.0 to 1.4 s in the session). A file whose own body is heavy is still red as before
    (test_collect_budget_subtracts_… above).
    The budget is 0.5 s: this file is the first .hy file read in the process, so the macro digest that the doeff-hy bytecode check pays once per process
    (about 0.2 s, a root separate from GC) is added too — a margin where that part alone does not turn it red, but counting the 4 GCs does."""
    _project(
        pytester,
        'doeff_test_collect_budget_seconds = 0.5\ndoeff_test_budget_mode = "fail"\n',
        {"test_collects": COLLECTED_WHILE_IMPORTED},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD + LARGE_HEAP)
    result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider", "--continue-on-collection-errors")
    result.assert_outcomes(passed=1)
    assert "load (import) took" not in result.stdout.str()


# A light test file that only imports a heavy dependency (a Python module that burns CPU in its body) — two files read the same dependency.
HEAVY_DEPENDENCY_CPU_SECONDS = 0.3
HEAVY_DEPENDENCY = (
    "import time\n_t0 = time.process_time()\n"
    f"while time.process_time() - _t0 < {HEAVY_DEPENDENCY_CPU_SECONDS}:\n    pass\nVALUE = 1\n"
)
# The budget of the shared-dependency tests is half the CPU seconds of the dependency: counting the dependency toward the file always exceeds it, while the jitter of the file's own load (0.03 s locally, 0.058 s under load —
# the 0.052 s red of the daily 10-11 16:00 JST; 0.058 s red reproduced locally by filling the CPU) does not. 0.05 s left no margin.
SHARED_DEPENDENCY_COLLECT_BUDGET_SECONDS = HEAVY_DEPENDENCY_CPU_SECONDS / 2
READS_HEAVY_DEPENDENCY = """\
(require doeff-hy.macros [deftest])
(import budget-heavy-dependency [VALUE])
(deftest test-reads
  (assert (= VALUE 1)))
"""


def test_collect_budget_does_not_charge_a_shared_dependency_to_the_first_file(
    pytester: pytest.Pytester,
) -> None:
    """The one-time import weight of a shared dependency is charged to no file's import — pass/fail does not change with the order or with running just one file.
   A file whose own body is heavy is still red as before (test_collect_budget_subtracts_… above)."""
    _project(
        pytester,
        f'doeff_test_collect_budget_seconds = {SHARED_DEPENDENCY_COLLECT_BUDGET_SECONDS}\ndoeff_test_budget_mode = "fail"\n',
        {"test_reads_a": READS_HEAVY_DEPENDENCY, "test_reads_b": READS_HEAVY_DEPENDENCY},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    pytester.makepyfile(budget_heavy_dependency=HEAVY_DEPENDENCY)
    for args in (("test_reads_a.hy",), ("test_reads_b.hy",), ("test_reads_b.hy", "test_reads_a.hy")):
        result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider", *args)
        result.assert_outcomes(passed=len(args))
        assert "load (import) took" not in result.stdout.str(), args


def test_compile_counter_sets_aside_a_first_import_once_and_not_a_loaded_module(tmp_path, monkeypatch) -> None:
    """The first import of another module goes into the import column exactly once (the compilation inside is not added twice), and an import of a module already in sys.modules
    is not counted."""
    import importlib
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "budget_first_import.py").write_text(HEAVY_DEPENDENCY, encoding="utf-8")
    bootstrap = sys.modules["importlib._bootstrap"]
    original_find_and_load = bootstrap._find_and_load
    counter = CompileCounter()
    counter.install()
    try:
        compile_before = counter.tally()
        imports_before = counter.import_tally()
        importlib.import_module("budget_first_import")
        first_compile = counter.tally().since(compile_before)
        first_imports = counter.import_tally().since(imports_before)
        imports_before = counter.import_tally()
        importlib.import_module("budget_first_import")
        again = counter.import_tally().since(imports_before)
    finally:
        counter.uninstall()
        sys.modules.pop("budget_first_import", None)
    assert first_imports.count == 1
    assert first_imports.cpu_seconds >= HEAVY_DEPENDENCY_CPU_SECONDS
    assert first_compile.count == 1 and first_compile.cpu_seconds == 0.0
    assert again == ImportTally(0, 0.0, 0.0)
    assert bootstrap._find_and_load is original_find_and_load


def test_import_budget_is_judged_at_setup_when_collected_from_records(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For a file collected from records (the second time — the first import writes the pyc and the records), the module import happens at the item's setup, and
    the budget is judged there with the same key (the red is an error of that item's setup)."""
    _project(
        pytester,
        'doeff_test_collect_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n'
        + f'doeff_adr_items_cache = "{tmp_path / "items"}"\n',
        {"test_slow_collect": SLOW_COLLECT},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    first = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider", "--continue-on-collection-errors")
    first.assert_outcomes(errors=1)
    second = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    second.stdout.fnmatch_lines(["*doeff-adr:*1 file*"])
    second.assert_outcomes(errors=1)
    second.stdout.fnmatch_lines(["*ERROR at setup of test_fast*", "*test_slow_collect.hy load (import) took wall-clock *"])
    collected = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider", "--collect-only")
    collected.stdout.fnmatch_lines(["*1 test collected*"])
    assert "load (import) took" not in collected.stdout.str()


# Per-marker budgets. With a budget of 1 s for local tests and 10 s for real_world tests, the measured values are passed to the pure part of the judgment
# (without waiting 11 s on a real clock).
LOCAL_AND_EDGE = {"local": 1.0, "real_world": 10.0}


def _judge_with_markers(
    markers: list[str], cpu: float, registry: dict[str, str] | None = None
) -> Verdict:
    budget = call_budget_for(markers, LOCAL_AND_EDGE)
    assert budget is not None
    return judge(_measurement(cpu), budget, registry or {})


def _fail_mode_budgets(registry: dict[str, str]) -> Budgets:
    return Budgets(
        call_seconds_by_marker=LOCAL_AND_EDGE,
        collect_seconds=None,
        mode="fail",
        registry=registry,
        registry_dirs=("scripts/test_budget/OVER-BUDGET", "scripts/doeff_lint/TEST-KIND-BREACHES"),
        vm_build=UncheckedVmBuild(),
    )


def test_local_marked_test_over_one_second_is_red_in_fail_mode() -> None:
    """A test marked local is judged by the marker's 1 s, and exceeding 1 s is red in fail mode."""
    assert _fail_mode_budgets({}).fails_over_budget
    verdict = _judge_with_markers(["local"], 1.2)
    assert verdict == OverBudget(_measurement(1.2), 1.0)


def test_real_world_test_of_five_seconds_is_green() -> None:
    assert isinstance(_judge_with_markers(["real_world"], 5.0), WithinBudget)


def test_real_world_test_of_eleven_seconds_is_red() -> None:
    assert _judge_with_markers(["real_world"], 11.0) == OverBudget(_measurement(11.0), 10.0)


def test_budget_is_the_longest_of_the_matching_markers() -> None:
    """When several markers match, the longest seconds wins. Markers that do not match (parametrize and the like) have no effect on the choice, and if none match there is no seconds budget (None)."""
    table = {"real_world": 10.0, "e2e": 30.0}
    assert call_budget_for(["parametrize", "real_world", "e2e"], table) == 30.0
    assert call_budget_for(["parametrize"], table) is None
    assert call_budget_for(["real_world"], table) == 10.0
    assert call_budget_for([], table) is None


def test_test_listed_only_in_the_second_registry_is_reported_not_red(tmp_path) -> None:
    """A test listed in the second registry dir is only reported when it exceeds the budget (not red)."""
    first = tmp_path / "scripts/test_budget/OVER-BUDGET"
    second = tmp_path / "scripts/doeff_lint/TEST-KIND-BREACHES"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    key = "t.hy::test"
    (second / registry_file_name(key)).write_text(f"{key}\nknown overrun of an edge test\n", encoding="utf-8")
    registry = load_registries(tmp_path, _fail_mode_budgets({}).registry_dirs)
    assert registry == {key: "known overrun of an edge test"}
    assert isinstance(_judge_with_markers(["local"], 1.2, registry), RegisteredOverBudget)


def test_marker_budget_lines_are_parsed_and_errors_are_values() -> None:
    assert parse_marker_budgets(["real_world=10", " e2e = 30 "]) == {"real_world": 10.0, "e2e": 30.0}
    assert parse_marker_budgets([]) == {}
    assert isinstance(parse_marker_budgets(["real_world=ten"]), SettingError)
    assert isinstance(parse_marker_budgets(["real_world"]), SettingError)


MARKED_SLOW_CALL = f"""\
(require doeff-hy.macros [deftest])
(import pytest)
(setv pytestmark [pytest.mark.real_world])
{SPIN}
(deftest test-slow-edge
  (spin 0.3)
  (assert True))
"""

def test_marker_budget_is_chosen_from_the_item_markers(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests are judged by a different budget per marker they carry (the item's markers are read within a pytest run): the same 0.3 s test is green under marker real_world (5 s) and
    red under marker e2e (0.05 s)."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        'markers = ["real_world: edge test", "e2e: integration test"]\ndoeff_test_budget_mode = "fail"\n'
        'doeff_test_call_budget_by_marker = ["real_world=5", "e2e=0.05"]\n',
        {"test_slow": _slow_call_marked_as("e2e"), "test_edge": MARKED_SLOW_CALL},
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines(["*test_slow.hy::test_slow run (call) took wall-clock *limit wall-clock 0.050 s*"])
    assert "test_edge.hy::test_slow_edge run" not in result.stdout.str()


def test_marker_budget_alone_turns_the_plugin_on_and_fails_the_marked_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measurement is on even with only per-marker budgets configured. A marked test that exceeds the marker's budget is red, and an unmarked test is not measured."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        'markers = ["real_world: edge test"]\ndoeff_test_budget_mode = "fail"\n'
        'doeff_test_call_budget_by_marker = ["real_world=0.05"]\n',
        {"test_slow": SLOW_CALL, "test_edge": MARKED_SLOW_CALL},
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines(["*test_edge.hy::test_slow_edge run (call) took wall-clock *limit wall-clock 0.050 s*"])
    # The listing at the end of a run without a step budget says that the execution budget is only the per-marker seconds.
    result.stdout.fnmatch_lines(["*the execution limit is only per-marker seconds (doeff_test_call_budget_by_marker)*"])


# The weight of a real_world-marked test lies in the child process and the waiting — a test that waits for a 0.3 s
# sleep in a child process has almost 0 CPU seconds in the parent process, while its wall-clock seconds exceed 0.3 s.
CHILD_WAIT_CALL = """\
(require doeff-hy.macros [deftest])
(import pytest subprocess sys)
(setv pytestmark [pytest.mark.real_world])
(deftest test-waits-on-a-child
  (subprocess.run [sys.executable "-c" "import time; time.sleep(0.3)"] :check True)
  (assert True))
"""


def test_marked_test_waiting_on_a_child_over_the_wall_clock_budget_is_red(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker budget is judged by wall-clock seconds: a real_world-marked test that takes 0.3 s waiting on a child process is red in fail mode even if the process CPU seconds are
    below the 0.05 s budget (the message says the budget is wall-clock seconds)."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(pytester, FAIL_MODE_INI, {"test_child": CHILD_WAIT_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*test_child.hy::test_waits_on_a_child run (call) took wall-clock *limit wall-clock 0.050 s*"])


def test_marked_test_waiting_on_a_child_within_the_wall_clock_budget_is_green(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same child-process-waiting test is green even in fail mode when it is within the wall-clock budget (5 s)."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        'markers = ["real_world: real I/O"]\ndoeff_test_call_budget_by_marker = ["real_world=5"]\n'
        'doeff_test_budget_mode = "fail"\n',
        {"test_child": CHILD_WAIT_CALL},
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)
    assert "test_waits_on_a_child execution" not in result.stdout.str()


def test_second_registry_dir_is_read_in_a_run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the registry dirs are written as a list of two, a test listed in the second one is also only reported."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        FAIL_MODE_INI + 'doeff_test_budget_registry = ["over-budget", "kind-breaches"]\n',
        {"test_slow": REAL_WORLD_SLOW_CALL},
    )
    pytester.mkdir("over-budget")
    second = pytester.mkdir("kind-breaches")
    key = "test_slow.hy::test_slow"
    (second / registry_file_name(key)).write_text(f"{key}\nexisting slow test\n", encoding="utf-8")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*overrun listed in the registry: test_slow.hy::test_slow (call*"])


def test_python_test_files_are_not_measured(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a Python test carrying a marker that matches a marker budget is not measured (only .hy test files are targets)."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    pytester.makeconftest(CONFTEST)
    pytester.makepyprojecttoml("[tool.pytest.ini_options]\n" + FAIL_MODE_INI)
    pytester.makepyfile(
        test_py="import time\n\nimport pytest\n\n\n@pytest.mark.real_world\ndef test_spin():\n    t0 = time.process_time()\n"
        "    while time.process_time() - t0 < 0.3:\n        pass\n"
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    ("ini", "message"),
    [
        ('doeff_test_collect_budget_seconds = "abc"\n', "doeff_test_collect_budget_seconds must be a positive number of seconds: 'abc'"),
        ("doeff_test_collect_budget_seconds = 0\n", "doeff_test_collect_budget_seconds must be a positive number of seconds: '0'"),
        ('doeff_test_call_budget_by_marker = ["real_world=1"]\ndoeff_test_budget_mode = "loud"\n', "report or fail"),
        (
            'doeff_test_call_budget_by_marker = ["real_world=abc"]\n',
            "doeff_test_call_budget_by_marker: for real_world, a positive number of seconds: 'abc'",
        ),
        ('doeff_test_call_budget_by_marker = ["real_world=0"]\n', "for real_world, a positive number of seconds"),
        ('doeff_test_call_budget_by_marker = ["real_world"]\n', "in the form marker=seconds: 'real_world'"),
        ('doeff_test_call_budget_by_marker = ["=10"]\n', "in the form marker=seconds: '=10'"),
        (
            'doeff_test_call_budget_by_marker = ["real_world=10", "real_world=5"]\n',
            "has two lines for marker real_world",
        ),
    ],
)
def test_unreadable_settings_stop_the_session(
    pytester: pytest.Pytester, ini: str, message: str
) -> None:
    _project(pytester, ini, {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{message}*"])


@pytest.mark.parametrize(
    "other_ini",
    ["", "doeff_test_collect_budget_seconds = 100\n"],
    ids=["the-key-alone", "the-plugin-on-by-a-collect-budget"],
)
def test_the_removed_call_budget_seconds_setting_does_not_judge_the_run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, other_ini: str
) -> None:
    """The setting doeff_test_call_budget_seconds for an execution-seconds budget is gone (no backward compatibility needed — 2026-10-04): writing it in the ini does not make execution be
    judged by seconds (a test with neither marker nor steps is not measured — even in fail mode with an unchecked build, a 0.3 s test is green). pytest only
    warns about an unregistered setting, and the run does not stop."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        'doeff_test_call_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n' + other_ini,
        {"test_slow": SLOW_CALL},
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*PytestConfigWarning: Unknown config option: doeff_test_call_budget_seconds*"])
    assert "budget exceeded" not in result.stdout.str()


def test_the_analyzers_uncached_expansion_is_subtracted_like_bytecode_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The analyzer's Hy expansion (only when it misses the syntax-tree cache) also goes into the count, and does not outside install."""
    program_effects = pytest.importorskip("doeff_effect_analyzer._program_effects")
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(tmp_path / "trees"))
    source = "(setv answer (+ 1 2))\n"
    counter = CompileCounter()
    counter.install()
    try:
        before = counter.tally()
        program_effects._compile_hy(source, "/src/budget_expanded.hy", "budget_expanded")
        cold = counter.tally().since(before)
        before = counter.tally()
        program_effects._compile_hy(source, "/src/budget_expanded.hy", "budget_expanded")
        cached = counter.tally().since(before)
    finally:
        counter.uninstall()
    before = counter.tally()
    program_effects._compile_hy(source.replace("2", "3"), "/src/budget_expanded.hy", "budget_expanded")
    outside = counter.tally().since(before)
    assert cold.count == 1 and cold.cpu_seconds > 0
    assert cached.count == 0
    assert outside.count == 0


# ── Judging by steps (#2852) ────────────────────────────────────────────────────────
# Only the count hook (the extension function doeff_vm.doeff_vm._vm_work_counts that vm_probe_handler, which answers effect ReadVmWorkCounts, reads) is swapped for a fake function that lets a test add steps. Even if the CPU seconds jitter, the steps are
# a fixed number, so if the judgment is by the count the same test gets the same judgment every time.

FAKE_WORK = """\
STEPS = [0]


def add(steps):
    STEPS[0] += steps


def counts():
    return (STEPS[0], 0)
"""

PIN_FAKE_WORK = (
    "\nfrom doeff._vm import native as _ext\nimport fake_work\n\n_ext._vm_work_counts = fake_work.counts\n"
)

MANY_STEPS = """\
(require doeff-hy.macros [deftest])
(import fake_work)
(deftest test-many-steps
  (fake_work.add 10000)
  (assert True))
(deftest test-few-steps
  (fake_work.add 10)
  (assert True))
"""

STEPS_FAIL_INI = 'doeff_test_budget_mode = "fail"\ndoeff_test_call_budget_steps = "1000"\n'


def _steps_project(pytester: pytest.Pytester, ini: str, files: dict[str, str], *, fake: bool = True) -> None:
    pytester.makepyfile(fake_work=FAKE_WORK)
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD + (PIN_FAKE_WORK if fake else ""))
    pytester.makepyprojecttoml('[tool.pytest.ini_options]\ndoeff_adr_hy_files = ["test_*.hy"]\n' + ini)
    pytester.makefile(".hy", **files)


def _steps_measurement(steps: int, key: str = "t.hy::test") -> Measurement:
    return Measurement(
        key=key, phase="call", cpu_seconds=9.0, wall_seconds=9.0, compile=CompileTally(0, 0.0, 0.0), work=VmWork(steps, 0)
    )


def test_judge_steps_uses_the_steps_not_the_cpu_seconds() -> None:
    """The steps judgment ignores CPU seconds — even at 9 CPU seconds, within the budget in steps is within, and over the budget is over."""
    assert isinstance(judge_steps(_steps_measurement(500), 1000, {}), WithinBudget)
    over = judge_steps(_steps_measurement(1500), 1000, {})
    assert isinstance(over, OverBudget) and over.unit == "steps" and over.budget == 1000
    assert isinstance(judge_steps(_steps_measurement(1500), 1000, {"t.hy::test": "existing"}), RegisteredOverBudget)
    stale = judge_steps(_steps_measurement(400), 1000, {"t.hy::test": "existing"})
    assert isinstance(stale, RegisteredWithinBudget) and stale.stale
    assert not judge_steps(_steps_measurement(800), 1000, {"t.hy::test": "existing"}).stale


def _call_budgets(*, steps: int | None) -> Budgets:
    return Budgets(
        call_seconds_by_marker={"real_world": 5.0},
        collect_seconds=None,
        mode="fail",
        registry={},
        registry_dirs=(),
        vm_build=UncheckedVmBuild(),
        call_steps=steps,
    )


def test_judge_call_takes_the_marker_seconds_first_then_the_steps_and_else_measures_nothing() -> None:
    """The fork of the execution judgment (a test with 9 CPU seconds and 500 steps): if a marker seconds budget matches, seconds (red even if the steps are within) — if not, steps (a test with a declaration gets
    the declared value) — if neither a step budget nor a marker budget matches, it is not measured (None)."""
    measurement = _steps_measurement(500)
    both = _call_budgets(steps=1000)
    assert judge_call(both, ["real_world"], measurement, None) == OverBudget(measurement, 5.0, "wall-seconds")
    assert isinstance(judge_call(both, ["parametrize"], measurement, None), WithinBudget)
    assert isinstance(judge_call(both, [], _steps_measurement(1500), None), OverBudget)
    declaration = StepDeclaration(5000, "assertions depend on the size of the scenario")
    assert isinstance(judge_call(both, [], measurement, declaration), StaleDeclaration)
    marker_only = _call_budgets(steps=None)
    assert judge_call(marker_only, ["real_world"], measurement, None) == OverBudget(measurement, 5.0, "wall-seconds")
    assert judge_call(marker_only, [], measurement, None) is None
    assert judge_call(marker_only, ["parametrize"], measurement, declaration) is None


def test_default_steps_budget_is_the_documented_number_and_judges_at_its_edge(pytester: pytest.Pytester) -> None:
    """`default` is the default budget DEFAULT_CALL_BUDGET_STEPS (100 thousand steps) — exactly the budget is green, one step over is red (#2670, item 2)."""
    assert parse_positive_steps("default") == DEFAULT_CALL_BUDGET_STEPS == 100_000
    edge = (
        "(require doeff-hy.macros [deftest])\n(import fake_work)\n"
        "(deftest test-at-the-edge\n  (fake_work.add 100000)\n  (assert True))\n"
        "(deftest test-one-over\n  (fake_work.add 100001)\n  (assert True))\n"
    )
    ini = 'doeff_test_budget_mode = "fail"\ndoeff_test_call_budget_steps = "default"\n'
    _steps_project(pytester, ini, {"test_edge": edge})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*test_edge.hy::test_one_over*steps 100001*limit steps 100000*"])


def test_steps_judge_fails_many_steps_and_passes_a_cpu_heavy_test_with_few_steps(pytester: pytest.Pytester) -> None:
    """When there is a step budget: a test whose steps exceed the budget is red even if the CPU is fast, and a test that spins the CPU for 0.3 s with few steps is green."""
    spin_few = f"(require doeff-hy.macros [deftest])\n{SPIN}\n(import fake_work)\n(deftest test-spin\n  (fake_work.add 10)\n  (spin 0.3)\n  (assert True))\n"
    _steps_project(pytester, STEPS_FAIL_INI, {"test_steps": MANY_STEPS, "test_spin": spin_few})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines(["*test_steps.hy::test_many_steps*steps 10000*limit steps 1000*"])
    result.stdout.fnmatch_lines(["*execution is judged by doeff-vm steps*"])


def test_a_steps_budget_without_the_counter_stops_the_run_by_name(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a step budget exists on a build without the count hook, the run stops with a UsageError instead of falling back to seconds (red; the message names the setting and why the count hook is missing).
    Neither does a slow test get judged by seconds and turn red (= fall back to seconds), nor does it silently turn green: not a single test runs."""
    from doeff._vm import native as ext

    _steps_project(pytester, STEPS_FAIL_INI, {"test_slow": SLOW_CALL}, fake=False)
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    # pytester runs in the same process, so remove the hook with monkeypatch (restored at the end of the test, not left for later tests)
    monkeypatch.delattr(ext, "_vm_work_counts", raising=False)
    result = pytester.runpytest("-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["*doeff_test_call_budget_steps is set but steps cannot be measured (*_vm_work_counts*)*does not fall back to CPU seconds*"]
    )
    output = result.stdout.str() + result.stderr.str()
    assert "passed" not in output
    assert "failed" not in output
    assert "limit exceeded" not in output


def test_steps_judge_fails_even_on_the_checked_vm_build(pytester: pytest.Pytester) -> None:
    """Steps do not change with whether invariant checks are on, so even on a checked build it is red in fail mode (seconds stay report-only)."""
    _steps_project(pytester, STEPS_FAIL_INI, {"test_steps": MANY_STEPS})
    pytester.makeconftest(
        CONFTEST + "\nfrom doeff._vm import native as _vm_ext\n\n_vm_ext._invariant_checks_enabled = lambda: True\n" + PIN_FAKE_WORK
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)


def test_a_marked_real_io_test_stays_on_seconds(pytester: pytest.Pytester) -> None:
    """A test that matches a per-marker seconds budget (a test with real I/O) is judged by seconds even when a step budget exists — because its weight does not show up in steps."""
    marked = "(require doeff-hy.macros [deftest])\n(import pytest fake_work)\n(setv pytestmark [pytest.mark.real_world])\n(deftest test-io\n  (fake_work.add 10000)\n  (assert True))\n"
    _steps_project(
        pytester,
        STEPS_FAIL_INI + 'doeff_test_call_budget_by_marker = ["real_world=10"]\nmarkers = ["real_world: real I/O"]\n',
        {"test_io": marked},
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_a_registered_test_is_judged_by_steps_and_a_stale_row_fails(pytester: pytest.Pytester) -> None:
    """The registry is the same dir as for seconds: a step overrun of a registered test is green, and a registration that finishes at or under half the budget in steps is a stale registration and red."""
    _steps_project(
        pytester, STEPS_FAIL_INI + 'doeff_test_budget_registry = ["steps-breaches"]\n', {"test_steps": MANY_STEPS}
    )
    registry = pytester.mkdir("steps-breaches")
    for key in ("test_steps.hy::test_many_steps", "test_steps.hy::test_few_steps"):
        (registry / registry_file_name(key)).write_text(f"{key}\nexisting heavy test\n", encoding="utf-8")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*test_few_steps is in the registry, but*times the limit of steps 1000*"])


def test_measured_steps_are_summed_and_listed_per_test_with_verbose(pytester: pytest.Pytester) -> None:
    """Even tests within the budget get a total in the listing at the end if their steps could be measured, and with -v one line per test — material for deciding the step budget (#2853)."""
    _steps_project(
        pytester,
        'doeff_test_call_budget_steps = "100000"\n',
        {"test_steps": MANY_STEPS},
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*tests with measured steps: 2; total steps 10010*"])
    result.stdout.fnmatch_lines(["*test_steps.hy::test_many_steps:*steps 10000*", "*test_steps.hy::test_few_steps:*steps 10*"])


def test_a_test_that_takes_no_vm_steps_is_within_the_steps_budget(pytester: pytest.Pytester) -> None:
    """A test with 0 steps (one that only reads a tree without running the VM — #2855) is within the budget even if it spins the CPU (the smallest form, adding no new mechanism)."""
    no_steps = f"(require doeff-hy.macros [deftest])\n{SPIN}\n(deftest test-reads-only\n  (spin 0.3)\n  (assert True))\n"
    _steps_project(pytester, STEPS_FAIL_INI, {"test_reads": no_steps})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


# ── Step budget while running (#3143) ───────────────────────────────────────────────────────────────
# Run, on doeff's scheduler, a task that keeps signaling itself at the same instant (it creates a promise, spawns the task that fulfills it, and waits).
# Steps are counted by the real count (the extension's _vm_work_counts) — with a fake count the scheduler never sees the overrun. The conftest of the tests above leaves the count hook of the same process
# swapped for a fake function, so the tests here run in a separate process.

SPIN_SCHEDULER = """\
from doeff_core_effects._scheduler import CompletePromise, CreatePromise, Spawn, Wait, scheduled

from doeff import do, run


@do
def _signal(promise):
    yield CompletePromise(promise, None)


@do
def _signal_and_wait():
    promise = yield CreatePromise()
    signaller = yield Spawn(_signal(promise))
    yield Wait(promise.future)
    yield Wait(signaller)


@do
def _spin(count):
    index = 0
    while count is None or index < count:
        index += 1
        yield _signal_and_wait()
    return index


def spin_forever():
    return run(scheduled(_spin(None)))


def spin(count):
    return run(scheduled(_spin(count)))
"""

SPIN_FOREVER_HY = """\
(require doeff-hy.macros [deftest])
(import spin_scheduler)
(deftest test-spin-forever
  (spin_scheduler.spin_forever)
  (assert True))
"""

# 1,000 signals run well past the step budget (1,000) × the multiplier, and finish.
SPIN_BOUNDED_HY = """\
(require doeff-hy.macros [deftest])
(import spin_scheduler)
(deftest test-spin-bounded
  (assert (= (spin_scheduler.spin 1000) 1000)))
"""

SPIN_BOUNDED_PY = """\
import spin_scheduler


def test_spin_bounded_in_python():
    assert spin_scheduler.spin(1000) == 1000
"""


def test_a_spin_that_never_ends_fails_by_name_while_it_runs(pytester: pytest.Pytester) -> None:
    """A Hy test in a repo with a step budget: a spin that never ends is failed by name by the scheduler once it exceeds the step budget × the multiplier.
    After call the budget is removed — a Python test in the same run (no budget armed) is green even when it spins the same loop 1,000 times."""
    _steps_project(pytester, STEPS_FAIL_INI, {"test_spin": SPIN_FOREVER_HY}, fake=False)
    pytester.makepyfile(spin_scheduler=SPIN_SCHEDULER, test_spin_after=SPIN_BOUNDED_PY)
    result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(
        [f"*StepBudgetExceeded: step budget exceeded: * steps since the budget was armed, limit {1000 * DEFAULT_RUNAWAY_STEPS_MULTIPLIER} *"]
    )


def test_the_runaway_multiplier_is_read_from_the_ini(pytester: pytest.Pytester) -> None:
    """The multiplier can be changed with the ini step_budget_multiplier (#3147): with 3, the same spin fails at the budget 1,000 × 3 = 3,000 steps.
    The default (when not written) is 20 — the test above."""
    _steps_project(pytester, STEPS_FAIL_INI + "step_budget_multiplier = \"3\"\n", {"test_spin": SPIN_FOREVER_HY}, fake=False)
    pytester.makepyfile(spin_scheduler=SPIN_SCHEDULER)
    result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*StepBudgetExceeded: step budget exceeded: * steps since the budget was armed, limit 3000 *"])


def test_a_runaway_multiplier_that_is_not_a_positive_integer_stops_the_run(pytester: pytest.Pytester) -> None:
    """A multiplier value that cannot be read stops the run instead of silently falling back to the default."""
    _steps_project(pytester, STEPS_FAIL_INI + "step_budget_multiplier = \"0\"\n", {"test_ok": "(require doeff-hy.macros [deftest])\n(deftest test-ok (assert True))\n"})
    result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*step_budget_multiplier must be a positive integer: '0'*"])


def test_the_runaway_budget_is_the_steps_budget_times_the_multiplier(pytester: pytest.Pytester) -> None:
    """The budget is the step budget × the multiplier: the same 1,000 signals are red at a budget of 1,000 and green at a budget of 100,000 (the steps judgment is also within)."""
    _steps_project(pytester, STEPS_FAIL_INI, {"test_spin": SPIN_BOUNDED_HY}, fake=False)
    pytester.makepyfile(spin_scheduler=SPIN_SCHEDULER)
    result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*StepBudgetExceeded*"])

    _steps_project(
        pytester,
        'doeff_test_budget_mode = "fail"\ndoeff_test_call_budget_steps = "100000"\n',
        {"test_spin": SPIN_BOUNDED_HY},
        fake=False,
    )
    result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_no_steps_setting_arms_no_runaway_budget(pytester: pytest.Pytester) -> None:
    """In a repo without the step budget setting (only per-marker seconds budgets) no budget is armed — the Hy test with the same 1,000 signals is green (as before)."""
    _steps_project(
        pytester, 'doeff_test_call_budget_by_marker = ["real_world=10"]\n', {"test_spin": SPIN_BOUNDED_HY}, fake=False
    )
    pytester.makepyfile(spin_scheduler=SPIN_SCHEDULER)
    result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


# ── Per-test declarations (decision of the Mac coordinator, 2026-10-03 21:5x) ───────────────────────────────────────────────
# The default budget does not change; only a test that cannot be shrunk declares, next to the test definition, the steps it needs and the reason. A test with a declaration is judged by the declared value,
# and a test without one is judged by the default budget as before.


def test_judge_declared_uses_the_declaration_and_reds_a_stale_one() -> None:
    """Declaration of 5000 steps: above the default (1000) but within the declaration is within, over the declaration is red, and at or under half the declaration is a stale declaration and red."""
    declaration = StepDeclaration(5000, "assertions depend on the size of the scenario")
    assert isinstance(judge_declared(_steps_measurement(3000), declaration), WithinBudget)
    assert isinstance(judge_declared(_steps_measurement(5000), declaration), WithinBudget)
    over = judge_declared(_steps_measurement(5001), declaration)
    assert isinstance(over, DeclaredOverBudget) and over.declaration == declaration
    assert isinstance(judge_declared(_steps_measurement(2500), declaration), StaleDeclaration)
    assert isinstance(judge_declared(_steps_measurement(2501), declaration), WithinBudget)


def test_declare_step_budget_attaches_the_declaration_and_refuses_bad_values() -> None:
    """A declaration attaches to the test function and can be read. A declaration whose steps are not a positive integer, or whose reason is empty or two lines, is refused when attached."""

    def a_test() -> None: ...

    declare_step_budget(a_test, 5000, " assertions depend on the size of the scenario ")
    assert declared_steps_of(a_test) == StepDeclaration(5000, "assertions depend on the size of the scenario")

    def other() -> None: ...

    assert declared_steps_of(other) is None
    for steps, reason in ((0, "reason"), (-1, "reason"), (True, "reason"), (5000, ""), (5000, "line 1\nline 2")):
        with pytest.raises(ValueError):
            declare_step_budget(other, steps, reason)
    assert declared_steps_of(other) is None


DECLARED_STEPS = """\
(require doeff-hy.macros [deftest])
(import fake_work)
(import doeff_hy_pytest [declare-step-budget])
(deftest test-declared-heavy
  (fake_work.add 10000)
  (assert True))
(declare-step-budget test-declared-heavy 12000 "assertions depend on the size of the scenario (example test)")
(deftest test-undeclared-heavy
  (fake_work.add 10000)
  (assert True))
(deftest test-stale-declaration
  (fake_work.add 10)
  (assert True))
(declare-step-budget test-stale-declaration 12000 "used to be heavy (example test)")
"""


def test_a_declared_test_is_judged_by_its_declaration_and_the_failure_cases_stay_red(pytester: pytest.Pytester) -> None:
    """In a repo with the default 1000 steps, 10000 steps of a test that declares 12000 is green. Failure cases: a test of the same weight without a declaration is red at the default, and
    a test that finishes at or under half the declaration (10 steps) is red as a stale declaration."""
    _steps_project(pytester, STEPS_FAIL_INI, {"test_declared": DECLARED_STEPS})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=2)
    result.stdout.fnmatch_lines(["*test_declared.hy::test_undeclared_heavy*steps 10000*limit steps 1000*"])
    result.stdout.fnmatch_lines(["*test_declared.hy::test_stale_declaration declares steps 12000, but*at most 0.5 times*"])
    result.stdout.fnmatch_lines(["*stale declaration (at most 0.5 times declared steps 12000): test_declared.hy::test_stale_declaration*"])
    assert "test_declared_heavy" not in "\n".join(line for line in result.stdout.lines if "limit exceeded" in line)


def test_a_test_over_its_declaration_is_red(pytester: pytest.Pytester) -> None:
    """A test over its declared value is red (a declaration is not a way to raise the budget — it lets through only up to the declared value)."""
    over = (
        "(require doeff-hy.macros [deftest])\n(import fake_work)\n(import doeff_hy_pytest [declare-step-budget])\n"
        "(deftest test-over\n  (fake_work.add 20000)\n  (assert True))\n"
        '(declare-step-budget test-over 12000 "example test")\n'
    )
    _steps_project(pytester, STEPS_FAIL_INI, {"test_over": over})
    result = pytester.runpytest("-q")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*exceeded its declared steps: test_over.hy::test_over*steps 20000*declared steps 12000*"])


# ── Judging only by the steps of the simulated world's running ──────────────────────────────────────
# The world marks (doeff_core_effects._world_phases — left by doeff-cluster's sim-main) are recorded from inside a test, lined up with fake steps.


def test_world_steps_splits_the_call_by_the_world_marks() -> None:
    """Split the steps of a call by the world marks: begun → up is start-up, up → stopping is running, stopping → ended is shutdown, and anything else is outside the world.
    If the world runs twice, they are added per section. For a world that never came up, the time up to stopping is start-up, and an up that arrives after stopping does not split.
    A test with no marks gives None (judged by the whole call)."""
    marks = (
        WorldMark("begun", 100), WorldMark("up", 600), WorldMark("stopping", 700), WorldMark("ended", 1000),
        WorldMark("begun", 1050), WorldMark("stopping", 1400), WorldMark("up", 1450), WorldMark("ended", 1500),
    )
    assert world_steps(marks, 0, 1600) == WorldSteps(
        outside=100 + 50 + 100, starting=500 + 350, running=100, stopping=300 + 100, worlds=2, up=1
    )
    assert world_steps((), 0, 1600) is None


WORLD_RUNS = """\
(require doeff-hy.macros [deftest])
(import fake_work)
(import doeff_core_effects._world_phases [mark-world-phase])
(defn world [starting running stopping]
  (mark-world-phase "begun")
  (fake_work.add starting)
  (mark-world-phase "up")
  (fake_work.add running)
  (mark-world-phase "stopping")
  (fake_work.add stopping)
  (mark-world-phase "ended"))
(deftest test-heavy-start-light-run
  (world 50000 500 20000)
  (assert True))
(deftest test-light-start-heavy-run
  (world 10 1500 10)
  (assert True))
(deftest test-no-world
  (fake_work.add 1500)
  (assert True))
"""


WORLD_RUNNING_INI = STEPS_FAIL_INI + 'doeff_test_call_steps_counted = "world-running"\n'


def test_a_world_test_is_judged_by_its_running_steps_and_reports_start_and_stop_apart(pytester: pytest.Pytester) -> None:
    """In a world-running repo (default 1000 steps): even with 50 thousand start-up and 20 thousand shutdown, a world with 500 running steps is green and a world with 1500 running steps is red (the message gives running,
    start-up and shutdown as separate values). A test that does not run a world is still judged by the whole call as before (red at 1500 steps)."""
    _steps_project(pytester, WORLD_RUNNING_INI, {"test_worlds": WORLD_RUNS})
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=2)
    result.stdout.fnmatch_lines(["*for a test that ran a mock world, only the steps of the world run*"])
    result.stdout.fnmatch_lines(
        ["*test_worlds.hy::test_light_start_heavy_run*steps 1520(world run 1500, start 10, stop 10, outside the world 0, of 1 worlds 1 started up — the run is what is judged)*limit steps 1000*"]
    )
    result.stdout.fnmatch_lines(["*test_worlds.hy::test_no_world*steps 1500, *limit steps 1000*"])
    result.stdout.fnmatch_lines(["*test_worlds.hy::test_heavy_start_light_run:*steps 70500(world run 500, start 50000, stop 20000*"])


def test_without_the_world_running_setting_a_world_test_is_judged_by_the_whole_call(pytester: pytest.Pytester) -> None:
    """A repo without the setting (default call) does not read the marks, and even a test that ran a world is judged by the whole call — merely raising the doeff version does not change the judgment on the user side
    (a world with 50 thousand start-up is red at 70,500 steps, and the message shows no split values)."""
    _steps_project(pytester, STEPS_FAIL_INI, {"test_worlds": WORLD_RUNS})
    result = pytester.runpytest("-v")
    result.assert_outcomes(failed=3)
    result.stdout.fnmatch_lines(["*test_worlds.hy::test_heavy_start_light_run*steps 70500, *limit steps 1000*"])
    assert "world run" not in result.stdout.str()


def test_an_unknown_steps_counted_value_stops_the_run_by_name(pytester: pytest.Pytester) -> None:
    """If the setting for the counted section is outside the vocabulary, the run stops with a UsageError instead of silently falling back to the default."""
    _steps_project(pytester, STEPS_FAIL_INI + 'doeff_test_call_steps_counted = "running"\n', {"test_worlds": WORLD_RUNS})
    result = pytester.runpytest("-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*doeff_test_call_steps_counted must be call or world-running: 'running'*"])


# ── Per-test ratio of CPU seconds / steps ───────────────────────────
# A report-only line. Divide the µs/step of tests over the floor (RATIO_FLOOR_STEPS) by the session median and name the outliers (more than RATIO_OUTLIER_FACTOR times, or under 1/that).
# The judgment, the exit code and BudgetWarning do not change.


def _cost(name: str, micros_per_step: float, steps: int = 20_000) -> Measurement:
    """A call measurement with steps = steps and CPU seconds = µs/step × steps (key = t.hy::name)."""
    return Measurement(
        key=f"t.hy::{name}",
        phase="call",
        cpu_seconds=micros_per_step * steps / 1e6,
        wall_seconds=1.0,
        compile=CompileTally(0, 0.0, 0.0),
        work=VmWork(steps, 0),
    )


def _even_costs(count: int, micros_per_step: float = 10.0) -> tuple[Measurement, ...]:
    return tuple(_cost(f"test_{i:02d}", micros_per_step) for i in range(count))


@pytest.mark.parametrize(
    ("steps", "counted"),
    [(RATIO_FLOOR_STEPS - 1, False), (RATIO_FLOOR_STEPS, True), (6, False)],
)
def test_the_floor_keeps_tests_with_few_steps_out_of_the_ratio(steps: int, counted: bool) -> None:
    """Edge of the floor: a test with steps below the floor is not in the ratio, only counted — the 230 thousand µs/step of a 6-step test is neither "highest" nor the median."""
    report = step_cost_report((*_even_costs(RATIO_MIN_TESTS), _cost("test_edge", 230_000.0, steps)))
    assert isinstance(report, StepCostRatios)
    assert report.measured == RATIO_MIN_TESTS + counted
    assert report.below_floor == (not counted)
    assert (report.highest.name == "test_edge") is counted


def test_the_median_is_the_base_of_every_ratio() -> None:
    """Median: 8 tests of 8, 10×6, 30 µs/step → median 10, the highest 30 µs/step test is ×3.0, and the ratio is the value divided by the median."""
    costs = (_cost("test_low", 8.0), *_even_costs(6), _cost("test_high", 30.0))
    report = step_cost_report(costs)
    assert isinstance(report, StepCostRatios)
    assert report.median == pytest.approx(10.0)
    assert report.highest.name == "test_high"
    assert report.highest.ratio == pytest.approx(3.0)
    assert report.highest.micros_per_step == pytest.approx(30.0)


@pytest.mark.parametrize(
    ("micros_per_step", "side"),
    [(20.0, None), (20.5, "above"), (5.0, None), (4.9, "below")],
)
def test_outliers_are_beyond_twice_and_below_half_of_the_median(micros_per_step: float, side: str | None) -> None:
    """Both sides of outliers: in a session with median 10, exactly 2x and exactly half are not outliers, and only the side that goes beyond is."""
    report = step_cost_report((*_even_costs(RATIO_MIN_TESTS), _cost("test_odd", micros_per_step)))
    assert isinstance(report, StepCostRatios)
    above = tuple(c.name for c in report.above)
    below = tuple(c.name for c in report.below)
    assert above == (("test_odd",) if side == "above" else ())
    assert below == (("test_odd",) if side == "below" else ())


def test_a_session_with_fewer_than_the_minimum_tests_over_the_floor_gives_no_ratio() -> None:
    """A session with fewer than RATIO_MIN_TESTS tests over the floor gives no ratio (the median is meaningless) — the line is still shown."""
    report = step_cost_report((*_even_costs(RATIO_MIN_TESTS - 1), _cost("test_tiny", 9.0, 50)))
    assert report == TooFewForRatios(measured=RATIO_MIN_TESTS - 1, below_floor=1)
    assert step_cost_line(report) == (
        f"CPU seconds / steps (us/step): {RATIO_MIN_TESTS - 1} tests with at least 10000 steps (1 below not shown)"
        f"; fewer than {RATIO_MIN_TESTS} tests, so no ratio is shown"
    )


def test_the_report_does_not_depend_on_the_order_of_the_tests() -> None:
    """Independent of order: even lined up in reverse or shifted order, the median, the highest test and the order of outliers are the same (the same ratio is in name order)."""
    costs = (
        *_even_costs(RATIO_MIN_TESTS),
        _cost("test_b", 40.0),
        _cost("test_a", 40.0),
        _cost("test_c", 2.0),
        _cost("test_d", 2.0),
    )
    reports = {step_cost_report(order) for order in (costs, costs[::-1], costs[5:] + costs[:5])}
    assert len(reports) == 1
    (report,) = reports
    assert isinstance(report, StepCostRatios)
    assert report.highest.name == "test_a"
    assert tuple(c.name for c in report.above) == ("test_a", "test_b")
    assert tuple(c.name for c in report.below) == ("test_c", "test_d")


def test_outliers_do_not_change_the_verdicts() -> None:
    """The sequence of judgments is the same even with outliers: judging by steps a set of measurements with a test of the same steps but 50 times the CPU seconds mixed in and one without, the sequence of
    verdict kinds is the same (the ratio does not enter the judgment)."""
    plain = (*_even_costs(RATIO_MIN_TESTS), _cost("test_odd", 10.0, 30_000))
    odd = (*_even_costs(RATIO_MIN_TESTS), _cost("test_odd", 500.0, 30_000))
    match (step_cost_report(plain), step_cost_report(odd)):
        case (StepCostRatios(above=()), StepCostRatios(above=(StepCost(name="test_odd"),))):
            pass
        case reports:
            raise AssertionError(f"expected a pair of measurements with no outlier and with one outlier, got: {reports}")
    kinds = [tuple(type(judge_steps(m, 25_000, {})) for m in run) for run in (plain, odd)]
    assert kinds[0] == kinds[1]


def test_the_line_names_three_outliers_per_side_and_counts_the_rest() -> None:
    """Naming is 3 per side (in descending ratio) and the rest is "N more" — even with 0 outliers the median and the highest test are shown."""
    many = (*_even_costs(10), *(_cost(f"test_slow_{i}", 30.0 + i) for i in range(5)))
    line = step_cost_line(step_cost_report(many))
    assert line == (
        "CPU seconds / steps (us/step): 15 tests with at least 10000 steps (0 below not shown); median 10.0"
        "; highest test_slow_4 ×3.40; 5 above 2x median: test_slow_4 ×3.40; test_slow_3 ×3.30; test_slow_2 ×3.20; and 2 more"
        "; 0 below 1/2 of median"
    )
    quiet = step_cost_line(step_cost_report(_even_costs(RATIO_MIN_TESTS)))
    assert quiet == (
        "CPU seconds / steps (us/step): 8 tests with at least 10000 steps (0 below not shown); median 10.0"
        "; highest test_00 ×1.00; 0 above 2x median; 0 below 1/2 of median"
    )


def test_the_name_is_the_last_part_of_the_node_id_without_separators() -> None:
    """The name used in naming is the last part of the nodeid, up to 56 chars, with whitespace and ; replaced by _ (so reading back does not split a name)."""
    assert step_cost_name("a/b.hy::test_x") == "test_x"
    assert step_cost_name("a/b.hy::test_x[a b;c]") == "test_x[a_b_c]"
    assert len(step_cost_name("a.hy::" + "x" * 80)) == RATIO_NAME_CHARS


@pytest.mark.parametrize(
    "costs",
    [
        pytest.param(_even_costs(RATIO_MIN_TESTS), id="no-outliers"),
        pytest.param((*_even_costs(10), *(_cost(f"test_slow_{i}", 30.0 + i) for i in range(5))), id="many-above"),
        pytest.param(
            (*_even_costs(10), _cost("test_hot[a b]", 25.0), _cost("test_cold", 1.0), _cost("test_colder", 0.5)),
            id="both-sides",
        ),
        pytest.param(_even_costs(3), id="too-few"),
        pytest.param((), id="none"),
    ],
)
def test_the_line_reads_back_to_the_report(costs: tuple[Measurement, ...]) -> None:
    """Round trip of format → read back: read_step_cost_line reads back the spelling of the line (step_cost_line), and the counts, the median (1 decimal place), the highest test and the names
    (3 per side, ratio to 2 decimal places) match the report's values. It reads even with a prefix in front. If the spelling is changed and the reading is forgotten, it goes red."""
    report = step_cost_report(costs)
    read = read_step_cost_line("  " + step_cost_line(report))
    assert read is not None
    assert (read.measured, read.below_floor) == (report.measured, report.below_floor)
    match report:
        case TooFewForRatios():
            assert (read.median, read.highest, read.above, read.below) == (None, None, 0, 0)
            assert read.above_named == read.below_named == ()
        case StepCostRatios():
            assert read.median == round(report.median, 1)
            assert read.highest == (report.highest.name, round(report.highest.ratio, 2))
            assert (read.above, read.below) == (len(report.above), len(report.below))
            assert read.above_named == tuple((c.name, round(c.ratio, 2)) for c in report.above[:RATIO_NAMED])
            assert read.below_named == tuple((c.name, round(c.ratio, 2)) for c in report.below[:RATIO_NAMED])
    assert read_step_cost_line("Tests with measured steps: 2, total steps 10010 (per test: -v)") is None


RATIO_TESTS_INI = 'doeff_test_call_budget_steps = "25000"\n'


def _ratio_tests(*, hot: bool) -> str:
    """8 tests of 20,000 steps and 1 test of 30,000 steps (over the 25,000 budget — a BudgetWarning in report-only mode). Every test spins the CPU 0.01 s per 10 thousand steps,
    to even out the ratio. The steps are fake counts, and without spinning the CPU of one test is under 1 ms, and a jitter of a few ms such as the first test's preparation
    pushed the ratio over 2x (in the daily runs of 10-14 08:00 and 10-17 20:00, a run test with no outlier was named). spin stops by its own
    process CPU seconds, so even when the machine is busy the measured CPU seconds match the spun amount. If hot, the first test spins another 0.3 s (an outlier
    far over 2x the median)."""
    spin = "\n  (spin 0.3)" if hot else ""
    tests = "".join(f"(deftest test-even-{i}\n  (fake_work.add 20000)\n  (spin 0.02)\n  (assert True))\n" for i in range(1, 8))
    return (
        f"(require doeff-hy.macros [deftest])\n{SPIN}\n(import fake_work)\n"
        f"(deftest test-hot\n  (fake_work.add 20000)\n  (spin 0.02){spin}\n  (assert True))\n"
        f"{tests}(deftest test-over\n  (fake_work.add 30000)\n  (spin 0.03)\n  (assert True))\n"
    )


def test_an_outlier_changes_neither_the_outcomes_nor_the_exit_code_nor_the_warnings(pytester: pytest.Pytester) -> None:
    """A ratio line appears in the summary at the end. With and without an outlier, the test outcomes, the exit code and the number of BudgetWarnings are the same (even in fail mode
    it does not turn red because of the ratio)."""

    def run(hot: bool, mode: str) -> tuple[object, ...]:
        """Run once, check the ratio line, and return the sequence of judgments (pass/fail counts, exit code, number of BudgetWarnings)."""
        _steps_project(
            pytester,
            RATIO_TESTS_INI + f'doeff_test_budget_mode = "{mode}"\n',
            {"test_ratio": _ratio_tests(hot=hot)},
        )
        result = pytester.runpytest("-q", "-W", "default::doeff_hy_pytest._budget.BudgetWarning")
        result.stdout.fnmatch_lines(["CPU seconds / steps (us/step): 9 tests with at least 10000 steps (0 below not shown); median *"])
        named = "*; 1 above 2x median: test_hot ×*"
        if hot:
            result.stdout.fnmatch_lines([named])
        else:
            result.stdout.no_fnmatch_line(named)
        counts = result.parseoutcomes()
        return (result.ret, counts.get("passed"), counts.get("failed"), result.stdout.str().count("BudgetWarning"))

    for mode in ("report", "fail"):
        assert run(False, mode) == run(True, mode)
