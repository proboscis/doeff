"""Counting Python calls (the CALL event of ``sys.monitoring``) - only in runs with the ``--doeff-python-calls`` flag, counted during the call phase of tests that declared a step budget
(section 2 of parent design #2670).

Why it exists: doeff-vm steps are determined by the size of the test scenario, and waste on the Python side (e.g. 180,000 isinstance calls in #2593) does not show in steps.
The call count shows it as a day-to-day trend. The count reproduces within 0.4% for the same test (CPU seconds fluctuate by +-14% for the same steps).

Design decisions:

- What is counted is the CALL event (builtin calls are counted too - with PY_START alone, calls such as isinstance are invisible). cProfile is not used
  (it cannot be split per test and cannot span threads). The count is defined as the number of CALL events and is not compared with cProfile's ``total_calls``.
- The scope is only the call phase of each test (collection, setup and teardown are not counted). Events are enabled with ``set_events`` before the call and set to 0 after (one on/off costs
  about 40 microseconds).
- Only tests that declared a step budget (``declare_step_budget``) are targeted. Putting the monitoring overhead (1.5-2.3x of plain) on all tests would stretch the daily pytest stage
  and bring it close to the run's limit.
- A run without the flag (default) takes no ``sys.monitoring`` slot at all and never calls ``set_events`` - per-test CPU does not change.
- Slots are tried in the order 4 -> 5 -> 3 for a free one (3 may be used by doeff-hy's ``doeff-hy-called-code``; 0-2 are slots named for debugger, coverage and profiler,
  so they are not taken). If all are full, nothing is counted, one "unmeasurable" line is printed at the end, and the run does not fail.
- Counting uses ``next`` of ``itertools.count`` (an atomic increment). In free-threaded Python, ``+= 1`` in the callback loses nearly
  60% of counts across threads (345,383 for 4 threads x 200,000 calls).

The pure part (slot choice, sentence formatting) is separated from the part that touches ``sys.monitoring`` (:class:`CallCounter`). The pytest hooks
live in ``doeff_hy_pytest._budget`` (so the step source and the test declarations are read in one place).
"""

import itertools
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# pytest command-line flag (absent by default) - only the once-a-day run of the daily full verification sets it.
PYTHON_CALLS_OPTION = "--doeff-python-calls"
PYTHON_CALLS_DEST = "doeff_python_calls"
TOOL_NAME = "doeff-hy-pytest-python-calls"
# Order of slots to look for a free one. 0, 1, 2 (debugger, coverage, profiler) are not taken.
TOOL_CANDIDATES = (4, 5, 3)
SUMMARY_HEAD = "Python calls (sys.monitoring CALL)"
UNMEASURABLE_HEAD = "python-calls: unmeasurable"
# Number of tests named in the final line (in descending order of calls).
TOP_TESTS = 3


@dataclass(frozen=True)
class FreeTool:
    """A slot number that can be taken."""

    tool_id: int


@dataclass(frozen=True)
class NotCounted:
    """Not counted (with a reason - all slots in use, steps cannot be measured, no limit configuration). The reason is printed as one line at the end of a flagged run."""

    reason: str


def choose_tool(owner_of: Callable[[int], str | None]) -> FreeTool | NotCounted:
    """To pick a free slot in candidate order (owner_of = name of the slot owner, None if free - the shape of ``sys.monitoring.get_tool``)."""
    for candidate in TOOL_CANDIDATES:
        if owner_of(candidate) is None:
            return FreeTool(candidate)
    taken = ", ".join(f"{candidate}={owner_of(candidate)}" for candidate in TOOL_CANDIDATES)
    return NotCounted(f"all sys.monitoring slots are in use: {taken}")


@dataclass(frozen=True)
class CalledTest:
    """One counted test - the number of Python calls during the call phase and the doeff-vm steps."""

    key: str
    calls: int
    steps: int


def per_step_text(calls: int, steps: int) -> str:
    """How to write calls per step (when steps is 0, write a dash without dividing)."""
    return f"{calls / steps:.1f}" if steps else "—"


def summary_line(tests: Sequence[CalledTest]) -> str:
    """The final summary line (per session) - the daily report bundles all sessions into one ``python-calls:`` line."""
    calls = sum(test.calls for test in tests)
    steps = sum(test.steps for test in tests)
    top = sorted(tests, key=lambda test: test.calls, reverse=True)[:TOP_TESTS]
    top_text = ", ".join(f"{test.key} {test.calls}" for test in top) or "none"
    return (
        f"{SUMMARY_HEAD}: {len(tests)} tests with declared step budgets, {calls} calls, {steps} steps, "
        f"{per_step_text(calls, steps)} calls per step, top {TOP_TESTS} by calls: {top_text}"
    )


def called_test_line(test: CalledTest) -> str:
    """One line per test for ``-v``."""
    return f"  {test.key}: {test.calls} calls, {test.steps} steps, {per_step_text(test.calls, test.steps)} calls per step"


def unmeasurable_line(not_counted: NotCounted) -> str:
    """One line for a flagged run that did not count (does not stay silent - the run does not fail)."""
    return f"{UNMEASURABLE_HEAD}({not_counted.reason})"


class CallCounter:
    """Count CALL events with one acquired slot. Events are enabled only from :meth:`start` to :meth:`stop`."""

    def __init__(self, tool_id: int) -> None:
        self.tool_id = tool_id
        self._mut_tally = itertools.count()

    @classmethod
    def acquire(cls) -> "CallCounter | NotCounted":
        """Take a free slot and install the callback (events are not enabled yet). If it cannot be taken, return the reason."""
        if sys.version_info < (3, 12):
            return NotCounted(f"no sys.monitoring (Python {sys.version_info[0]}.{sys.version_info[1]})")
        monitoring = sys.monitoring
        match choose_tool(monitoring.get_tool):
            case NotCounted() as not_counted:
                return not_counted
            case FreeTool(tool_id=tool_id):
                try:
                    monitoring.use_tool_id(tool_id, TOOL_NAME)
                except ValueError as exc:  # another tool took it first, after we chose
                    return NotCounted(f"cannot take sys.monitoring slot {tool_id}: {exc}")
                counter = cls(tool_id)
                monitoring.register_callback(tool_id, monitoring.events.CALL, counter._called)
                return counter

    def _called(self, code: object, offset: int, callable_: object, arg0: object) -> None:
        """One CALL event - add 1 atomically (does not lose counts even when free-threaded)."""
        next(self._mut_tally)

    def start(self) -> None:
        """Start counting from 0 and enable events (just before the test call)."""
        self._mut_tally = itertools.count()
        sys.monitoring.set_events(self.tool_id, sys.monitoring.events.CALL)

    def stop(self) -> int:
        """Remove events and return the number of calls since start (just after the test call)."""
        sys.monitoring.set_events(self.tool_id, 0)
        return next(self._mut_tally)

    def release(self) -> None:
        """Return the slot (end of session)."""
        sys.monitoring.set_events(self.tool_id, 0)
        sys.monitoring.register_callback(self.tool_id, sys.monitoring.events.CALL, None)
        sys.monitoring.free_tool_id(self.tool_id)


CallCounting = CallCounter | NotCounted
