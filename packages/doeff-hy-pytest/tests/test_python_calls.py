"""Tests for counting Python calls (doeff_hy_pytest/_python_calls.py).

Knocks out the acceptance conditions and failure cases of request B one by one: a run without the flag takes no slot and does not call set_events (acceptance 2); only the call phase of a test that declared
a step budget is counted (acceptance 3, decision 3); counts are not lost across threads (acceptance 4); slots are taken in the order 4 -> 5 -> 3 and never 0-2, and if all
are full a single "unmeasurable" line is printed and the run does not fail (decision 5).

As in test_budget.py, steps are added through a fake step source (fake_work). Tests that fill sys.monitoring slots with fake owners run in a separate process, so that the slot doeff-hy's
called_code decides once per process (if it was full it stays in the form that does not pick it up) is not carried over to later tests.
"""

from __future__ import annotations

import re
import sys
import threading

import pytest
from doeff_hy_pytest._budget import (
    RATIO_EXCLUDED_TEXT,
    RATIO_MIN_TESTS,
    CompileTally,
    Measurement,
    StepCostRatios,
    TooFewForRatios,
    VmWork,
    read_step_cost_line,
    step_cost_line,
    step_cost_report,
)
from doeff_hy_pytest._python_calls import (
    TOOL_NAME,
    CallCounter,
    CalledTest,
    FreeTool,
    NotCounted,
    choose_tool,
    summary_line,
    unmeasurable_line,
)

pytest_plugins = ["pytester"]

# Owner name of the slot taken by doeff-hy's called_code (doeff_hy_bytecode_guard/_called_code.py).
DOEFF_HY_TOOL = "doeff-hy-called-code"

# Marker for tests that take a sys.monitoring slot and count - sys.monitoring exists from Python 3.12. A 3.11 run does not count and reports "unmeasurable"
# (watched by test_before_3_12_the_flag_reports_unmeasurable).
needs_monitoring = pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring exists from Python 3.12")


@pytest.fixture(autouse=True)
def _own_stores(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty the child Hy code store and the doeff-adr collection records per test (note on the same-named fixture in test_budget.py)."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(tmp_path_factory.mktemp("code-store")))
    monkeypatch.delenv("DOEFF_ADR_ITEMS_CACHE", raising=False)


CONFTEST = """\
import pytest

from doeff._vm import native as _ext
import fake_work

_ext._invariant_checks_enabled = lambda: False
_ext._vm_work_counts = fake_work.counts


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program
"""

FAKE_WORK = """\
STEPS = [0]


def add(steps):
    STEPS[0] += steps


def counts():
    return (STEPS[0], 0)


def noop():
    return None


def call_many(count):
    for _ in range(count):
        noop()
"""

# Calls in the body of the declared test (BODY_CALLS times), and calls in the sections that must not be counted (module head = collection, fixture = setup and teardown)
# (NOISE_CALLS each). If counting leaks a section, the total exceeds NOISE_CALLS.
BODY_CALLS = 20_000
NOISE_CALLS = 300_000
THREADS = 4
CALLS_PER_THREAD = 50_000

DECLARED = f"""\
(require doeff-hy.macros [deftest])
(import pytest threading fake_work)
(import doeff_hy_pytest [declare-step-budget])
(fake_work.call-many {NOISE_CALLS})
(defn [pytest.fixture] noisy []
  (fake_work.call-many {NOISE_CALLS})
  (yield None)
  (fake_work.call-many {NOISE_CALLS}))
(deftest test-declared [noisy]
  (fake_work.add 1000)
  (fake_work.call-many {BODY_CALLS})
  (assert True))
(declare-step-budget test-declared 1500 "example test")
(deftest test-threaded
  (fake_work.add 1000)
  (setv threads (lfor _ (range {THREADS}) (threading.Thread :target fake_work.call-many :args #({CALLS_PER_THREAD}))))
  (for [t threads] (.start t))
  (for [t threads] (.join t))
  (assert True))
(declare-step-budget test-threaded 1500 "example test")
(deftest test-undeclared
  (fake_work.add 10)
  (fake_work.call-many {NOISE_CALLS})
  (assert True))
"""

STEPS_INI = 'doeff_test_budget_mode = "fail"\ndoeff_test_call_budget_steps = "100000"\n'


def _project(pytester: pytest.Pytester, conftest_extra: str = "") -> None:
    pytester.makepyfile(fake_work=FAKE_WORK)
    pytester.makeconftest(CONFTEST + conftest_extra)
    pytester.makepyprojecttoml('[tool.pytest.ini_options]\ndoeff_adr_hy_files = ["test_*.hy"]\n' + STEPS_INI)
    pytester.makefile(".hy", test_declared=DECLARED)


def _counted(result: pytest.RunResult, key: str) -> int:
    """From the per-test lines of -v, read that test's call count."""
    pattern = re.compile(rf"^  \S*{re.escape(key)}: (\d+) calls, (\d+) steps")
    found = [int(m.group(1)) for line in result.stdout.lines if (m := pattern.match(line))]
    assert len(found) == 1, result.stdout.str()
    return found[0]


# ── pure part ─────────────────────────────────────────────────────────────────────────────


def test_the_tool_is_chosen_four_then_five_then_three_and_never_zero_to_two() -> None:
    """Slots are taken in the order 4 -> 5 -> 3. 0-2 (debugger, coverage, profiler) are not taken even when free; if 3-5 are full, it does not count and gives a reason."""
    owners: dict[int, str] = {}
    assert choose_tool(owners.get) == FreeTool(4)
    owners[4] = "fake owner"
    assert choose_tool(owners.get) == FreeTool(5)
    owners[5] = "optimizer"
    owners[3] = "doeff-hy-called-code"
    assert choose_tool(owners.get) == NotCounted(
        "all sys.monitoring slots are in use: 4=fake owner, 5=optimizer, 3=doeff-hy-called-code"
    ), "a slot was chosen although only 0-2 were free"
    del owners[3]
    assert choose_tool(owners.get) == FreeTool(3)


def test_the_summary_line_names_the_total_the_steps_the_ratio_and_the_top_three() -> None:
    """The final line: number of counted tests, total calls, steps, per step, top 3 in descending order. Even with no tests it does not stay silent and says 0 tests."""
    tests = [CalledTest("a.hy::t1", 100, 10), CalledTest("a.hy::t2", 900, 20), CalledTest("b.hy::t3", 300, 30), CalledTest("b.hy::t4", 50, 40)]
    assert summary_line(tests) == (
        "Python calls (sys.monitoring CALL): 4 tests with declared step budgets, 1350 calls, 100 steps, "
        "13.5 calls per step, top 3 by calls: a.hy::t2 900, b.hy::t3 300, a.hy::t1 100"
    )
    assert summary_line([]) == (
        "Python calls (sys.monitoring CALL): 0 tests with declared step budgets, 0 calls, 0 steps, — calls per step, top 3 by calls: none"
    )
    assert unmeasurable_line(NotCounted("reason")) == "python-calls: unmeasurable(reason)"


def _cost(name: str, micros_per_step: float, python_calls: int | None = None) -> Measurement:
    """A call measurement with 20,000 steps and CPU seconds = us/step x 20,000 (python_calls = number of counted calls; None for uncounted tests)."""
    return Measurement(
        key=f"t.hy::{name}",
        phase="call",
        cpu_seconds=micros_per_step * 20_000 / 1e6,
        wall_seconds=1.0,
        compile=CompileTally(0, 0.0, 0.0),
        work=VmWork(20_000, 0),
        python_calls=python_calls,
    )


def test_tests_whose_python_calls_were_counted_are_left_out_of_the_cpu_per_step_ratio() -> None:
    """Acceptance 5: tests whose calls were counted (CPU seconds 1.5-1.7x because of monitoring overhead) are left out of request A's CPU seconds / steps population, and the excluded count is
    written in the ratio line and can be read back. Even if a counted test is 30 us/step it does not appear as "highest" or an outlier. The line of a run with no counted tests keeps the previous spelling."""
    plain = tuple(_cost(f"test_{i:02d}", 10.0) for i in range(RATIO_MIN_TESTS))
    report = step_cost_report((*plain, _cost("test_watched_a", 30.0, 400_000), _cost("test_watched_b", 25.0, 0)))
    assert isinstance(report, StepCostRatios)
    assert report.measured == RATIO_MIN_TESTS
    assert report.excluded == 2
    assert report.above == ()
    line = step_cost_line(report)
    assert RATIO_EXCLUDED_TEXT.format(2) in line
    read = read_step_cost_line(line)
    assert read is not None
    assert read.excluded == 2
    assert read.measured == RATIO_MIN_TESTS
    unwatched = step_cost_report(plain)
    assert RATIO_EXCLUDED_TEXT.format(0) not in step_cost_line(unwatched) and str(RATIO_MIN_TESTS) in step_cost_line(unwatched)
    unwatched_read = read_step_cost_line(step_cost_line(unwatched))
    assert unwatched_read is not None
    assert unwatched_read.excluded == 0
    few = step_cost_report((plain[0], _cost("test_watched", 30.0, 10)))
    assert few == TooFewForRatios(measured=1, below_floor=0, excluded=1)
    few_read = read_step_cost_line(step_cost_line(few))
    assert few_read is not None
    assert few_read.excluded == 1


# ── part that touches sys.monitoring ──────────────────────────────────────────────────────────────────


def _noop() -> None:
    return None


def _call_many(count: int) -> None:
    for _ in range(count):
        _noop()


@needs_monitoring
def test_the_counter_does_not_drop_calls_across_threads() -> None:
    """Do not lose counts of 4 threads x 50,000 calls (in free-threaded Python the callback `+= 1` lost nearly 60% - measured 5).
    After counting, return the slot."""
    counter = CallCounter.acquire()
    assert isinstance(counter, CallCounter), counter
    try:
        assert sys.monitoring.get_tool(counter.tool_id) == TOOL_NAME
        counter.start()
        threads = [threading.Thread(target=_call_many, args=(CALLS_PER_THREAD,)) for _ in range(THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        calls = counter.stop()
    finally:
        counter.release()
    assert calls >= THREADS * CALLS_PER_THREAD
    assert sys.monitoring.get_tool(counter.tool_id) is None


# ── pytest runs ─────────────────────────────────────────────────────────────────────────────


@needs_monitoring
def test_counts_only_the_call_of_a_declared_test_and_not_an_undeclared_one(pytester: pytest.Pytester) -> None:
    """A flagged run: counts only the calls in the call phase of tests that declared a step budget - does not count the 300,000 calls each in collection (module head), setup and teardown (fixture),
    nor the 300,000 in the undeclared test. The 4-thread test does not fall below the lower bound."""
    _project(pytester)
    result = pytester.runpytest("-v", "--doeff-python-calls")
    result.assert_outcomes(passed=3)
    declared = _counted(result, "test_declared.hy::test_declared")
    assert BODY_CALLS <= declared < NOISE_CALLS
    assert _counted(result, "test_declared.hy::test_threaded") >= THREADS * CALLS_PER_THREAD
    assert not any(re.match(r"^  \S*test_undeclared: \d+ calls, \d+ steps", line) for line in result.stdout.lines)
    result.stdout.fnmatch_lines(
        [
            "*sys.monitoring CALL*2 tests*2000*",
            "*test_declared.hy::test_threaded *test_declared.hy::test_declared *",
            "*sys.monitoring*4*",
        ]
    )


@needs_monitoring
def test_without_the_flag_no_tool_is_taken_and_set_events_is_never_called(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run without the flag (default): takes no slot and never calls set_events on slots other than doeff-hy's (does not change per-test CPU).
    The owners of all slots are the same before and after the run. No calls line is printed at the end."""
    _project(pytester)
    monitoring = sys.monitoring
    owners_before = tuple(monitoring.get_tool(tool) for tool in range(6))
    use_tool_id = monitoring.use_tool_id
    set_events = monitoring.set_events

    def refusing_use_tool_id(tool: int, name: str) -> None:
        """If anything other than doeff-hy's called_code takes a slot, fail the run on the spot."""
        if name != DOEFF_HY_TOOL:
            raise AssertionError(f"slot {tool} was taken in a run without the flag: {name}")
        use_tool_id(tool, name)

    def refusing_set_events(tool: int, events: int) -> None:
        """If anything adds or removes events on a slot other than doeff-hy's called_code, fail the run on the spot."""
        if monitoring.get_tool(tool) != DOEFF_HY_TOOL:
            raise AssertionError(f"set_events was called on slot {tool} ({monitoring.get_tool(tool)}) in a run without the flag")
        set_events(tool, events)

    monkeypatch.setattr(monitoring, "use_tool_id", refusing_use_tool_id)
    monkeypatch.setattr(monitoring, "set_events", refusing_set_events)
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=3)
    # If doeff-hy first converts Hy during the run, it may take one free slot (owners that existed before stay as they are).
    owners_after = tuple(monitoring.get_tool(tool) for tool in range(6))
    assert all(
        after == before or (before is None and after == DOEFF_HY_TOOL)
        for before, after in zip(owners_before, owners_after, strict=True)
    ), (owners_before, owners_after)
    assert TOOL_NAME not in owners_after
    assert "sys.monitoring CALL" not in result.stdout.str()
    assert "python-calls: " not in result.stdout.str()


OCCUPY = """
import sys

for _tool in {tools}:
    if sys.monitoring.get_tool(_tool) is None:
        sys.monitoring.use_tool_id(_tool, "fake owner")
"""


@needs_monitoring
def test_a_taken_tool_four_moves_the_counter_to_five(pytester: pytest.Pytester) -> None:
    """When slot 4 is held by a fake owner, take slot 5 and count (taking a fixed number crashed pytest at startup on an in-use slot - a failure case)."""
    _project(pytester, OCCUPY.format(tools=(4,)))
    result = pytester.runpytest_subprocess("-v", "-p", "no:cacheprovider", "--doeff-python-calls")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*2 tests*", "*sys.monitoring*5*"])


@needs_monitoring
def test_all_tools_taken_reports_unmeasurable_and_the_run_does_not_fail(pytester: pytest.Pytester) -> None:
    """If slots 3-5 are all full, it does not count, prints one line with "unmeasurable" and the owners, and does not fail the run (does not fall back to 0-2)."""
    _project(pytester, OCCUPY.format(tools=(3, 4, 5)))
    result = pytester.runpytest_subprocess("-v", "-p", "no:cacheprovider", "--doeff-python-calls")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*python-calls*4=fake owner*5=*3=*"])
    assert "sys.monitoring CALL" not in result.stdout.str()


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="counting on versions that have sys.monitoring is covered by the tests above")
def test_before_3_12_the_flag_reports_unmeasurable(pytester: pytest.Pytester) -> None:
    """A flagged run on a version without sys.monitoring (3.11) does not count, prints one line with the reason "unmeasurable", and does not fail the run."""
    _project(pytester)
    result = pytester.runpytest("-v", "--doeff-python-calls")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*python-calls*sys.monitoring*Python 3.11*"])
    assert "sys.monitoring CALL" not in result.stdout.str()


def test_the_flag_without_budget_settings_reports_unmeasurable(pytester: pytest.Pytester) -> None:
    """A repo flagged but with no limit configuration (steps not measured) does not stay silent and prints one "unmeasurable" line."""
    pytester.makepyfile(test_plain="def test_plain():\n    assert True\n")
    result = pytester.runpytest("-q", "--doeff-python-calls")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*python-calls*"])
