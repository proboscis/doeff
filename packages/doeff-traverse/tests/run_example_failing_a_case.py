"""Runs one example the same as `hy <example>`, and fails the first item inside the item on each Traverse run (a child of test_examples_run.py).

Usage: python run_example_failing_a_case.py <example path>

An in-item failure is caused by raising an exception inside the Program that Traverse builds from the item function — a mutation that rewrites the example's source strings (handler expressions, mock values)
made the test file get fixed even for rewrites inside the example that do not change the output.

This entry point relies on three traverse-lib promises: Traverse is built as `Traverse(f, items, label=None)`, the handler calls f per item, and
it records an exception inside the item Program as that item's failure and moves on. While the third holds, the child exits with 0 and only the output comparison catches the failure.
If any of them changes, the child exits non-zero or the marker becomes 0, and the tests of test_examples_run.py turn red for every example that runs
(it never silently stays green). Then, adapt this entry point to the new promise.

On exit it writes the run marker — the number of times the item function was called and the number of Programs run by doeff's run — to stderr in the form COUNTS_LINE.
It counts run counts, not names, so it does not count a mere import, a call inside an uncalled function, a quote, or the same name outside doeff (asyncio.run, a run inside the example).
The run count is taken by receiving the start of execution of the run function's code with sys.monitoring (Python 3.12+) — doeff's namespace is
not rewritten (the same code regardless of what name the example imports it under).
"""

import atexit
import sys
from collections import Counter
from collections.abc import Callable, Generator, Iterable
from types import CodeType
from typing import Any, Never, TypeVar

from doeff_traverse import Traverse

import doeff
from doeff import Program, do

Item = TypeVar("Item")
Answer = TypeVar("Answer")

# The line that writes the run marker (read by _COUNTS in test_examples_run.py).
COUNTS_LINE = "case-calls: {case} program-runs: {program}"

# The run marker (case = number of item-function calls, program = number of doeff run calls).
_calls: Counter[str] = Counter()

# The tool id of sys.monitoring that receives the start of run's execution. The doeff-hy bytecode guard takes 3 and 4 in the same process, so
# use the profiler id, among the named ids, that fits this child's role (counting executions).
_TOOL = sys.monitoring.PROFILER_ID


class InjectedCaseError(Exception):
    """The failure this entry point raises inside an item."""


@do
def _failing_case(value: object) -> Generator[object, object, Never]:
    """A Program that fails inside the item (raises right after it starts — the yield exists only to make it a generator)."""
    raise InjectedCaseError(f"injected into the case of {value!r}")
    yield


def _first_case_fails(
    f: Callable[[Item], Program[Answer, Any]],
) -> Callable[[Item], Program[Answer, Any]]:
    """Returns something that, with the same item function as f, makes a Program that fails inside the item only for the first item called."""
    failed = False

    def case(value: Item) -> Program[Answer, Any]:
        nonlocal failed
        _calls["case"] += 1
        if failed:
            return f(value)
        failed = True
        return _failing_case(value)

    return case


_traverse_init = Traverse.__init__


def _init(
    self: Traverse[Item, Answer],
    f: Callable[[Item], Program[Answer, Any]],
    items: Iterable[Item],
    label: str | None = None,
) -> None:
    """Same shape as Traverse.__init__, wrapping only the item function."""
    _traverse_init(self, _first_case_fails(f), items, label)


def _program_started(code: CodeType, offset: int) -> None:
    """A sys.monitoring notice — counts that the execution of doeff's run started (the example ran a Program)."""
    _calls["program"] += 1


def _count_program_runs() -> None:
    """Receives only the start of execution of the code of doeff's run function."""
    monitoring = sys.monitoring
    monitoring.use_tool_id(_TOOL, "doeff-traverse-example-marks")
    monitoring.register_callback(_TOOL, monitoring.events.PY_START, _program_started)
    monitoring.set_local_events(_TOOL, doeff.run.__code__, monitoring.events.PY_START)


def _write_counts() -> None:
    """Writes the run marker to stderr (the example's stdout is what gets compared, so it is not mixed in)."""
    print(COUNTS_LINE.format(case=_calls["case"], program=_calls["program"]), file=sys.stderr)


def main(example: str) -> None:
    Traverse.__init__ = _init
    _count_program_runs()
    atexit.register(_write_counts)
    sys.argv = ["hy", example]
    from hy.cmdline import hy_main

    hy_main()


if __name__ == "__main__":
    main(sys.argv[1])
