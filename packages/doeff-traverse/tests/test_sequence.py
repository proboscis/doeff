"""Tests the contract of sequence().

sequence runs a list of Programs one at a time in the order given and returns the answers as a tuple. No handler is needed, and the policy is fixed to "in order, stop at the first failure"
(a named tool, separate from the ordering and failure handling that the Traverse handler decides). Three things are checked.

- Contract: keeps input order, duplicates, and None / empty gives () / the first exception is raised as is and later Programs do not start / each Program is
  created just before it runs (a generator can be passed) / runs under the caller's handler / running the same Program twice does not share accumulation.
- Does not swallow failures: Traverse's sequential records failed items and keeps reading the rest, but sequence stops (needed so that the startup re-read
  keeps "stop if there is an unreadable line").
- Linear: even when the number of lines grows 10x, the cost is about 10x (appending to a tuple would be about 100x).

There are two forms of reading a line. An @do that emits no effect (no yield in the body — a defk decision) is called on the spot where it is bundled, and an effect-emitting
@do (a yield in the body) is run by the VM. The contract tests pass through both (same answer and same stopping in either form).
"""

import time
from collections.abc import Callable, Generator, Iterator

import pytest
from doeff_core_effects import reader
from doeff_traverse import Inspect, ItemResult, Traverse, sequence, sequential

from doeff import Ask, Program, Pure, do, run

_Effects = Generator[object, object, int]


def _settled_row(
    index: int, events: list[str], fails_at: int | None = None
) -> Program[int, object]:
    """Reading a line that emits no effect (the form called on the spot where it is bundled)."""

    @do
    def row() -> int:
        events.append(f"run {index}")
        if index == fails_at:
            raise ValueError("row failed")
        return index

    return row()


def _yielding_row(
    index: int, events: list[str], fails_at: int | None = None
) -> Program[int, object]:
    """Reading a line that emits an effect (the form the VM runs)."""

    @do
    def row() -> _Effects:
        events.append(f"run {index}")
        value = yield Pure(index)
        if index == fails_at:
            raise ValueError("row failed")
        return value

    return row()


_ROW_FORMS = pytest.mark.parametrize(
    "make_row", [_settled_row, _yielding_row], ids=["settled", "yielding"]
)


def test_empty_needs_no_handler() -> None:
    assert run(sequence([])) == ()


def test_order_duplicates_and_none_are_kept() -> None:
    assert run(sequence([Pure(2), Pure(None), Pure(2), Pure(1)])) == (2, None, 2, 1)


def test_the_answer_is_a_tuple_of_the_answers() -> None:
    answer = run(sequence(Pure(value) for value in range(3)))
    assert type(answer) is tuple
    assert answer == (0, 1, 2)


@_ROW_FORMS
def test_each_program_is_made_just_before_it_runs_and_finishes_before_the_next_is_made(
    make_row: Callable[..., Program[int, object]],
) -> None:
    events: list[str] = []

    def programs() -> Iterator[Program[int, object]]:
        for index in (1, 2):
            events.append(f"make {index}")
            yield make_row(index, events)

    program = sequence(programs())
    assert events == []
    assert run(program) == (1, 2)
    assert events == ["make 1", "run 1", "make 2", "run 2"]


@_ROW_FORMS
def test_the_first_exception_goes_up_as_it_is_and_later_programs_never_start(
    make_row: Callable[..., Program[int, object]],
) -> None:
    events: list[str] = []

    def programs() -> Iterator[Program[int, object]]:
        for index in (1, 2, 3):
            events.append(f"make {index}")
            yield make_row(index, events, 2)

    with pytest.raises(ValueError, match="row failed"):
        run(sequence(programs()))
    # 3 is neither created nor run
    assert events == ["make 1", "run 1", "make 2", "run 2"]


@_ROW_FORMS
def test_a_failing_row_is_not_dropped_as_it_is_under_traverse_sequential(
    make_row: Callable[..., Program[int, object]],
) -> None:
    """Traverse's sequential records failed items and keeps reading the rest, and a caller that reads only valid_values cannot see the failure."""
    events: list[str] = []

    @do
    def traversed() -> Generator[object, object, list[ItemResult]]:
        collection = yield Traverse(lambda index: make_row(index, events, 2), [1, 2, 3])
        return (yield Inspect(collection))

    items = run(sequential(traversed()))
    assert events == ["run 1", "run 2", "run 3"]
    assert [item.failed for item in items] == [False, True, False]

    events.clear()
    with pytest.raises(ValueError, match="row failed"):
        run(sequence(make_row(index, events, 2) for index in (1, 2, 3)))
    assert events == ["run 1", "run 2"]


def test_programs_run_under_the_callers_handlers_and_a_nested_handler_stays_in_its_program() -> (
    None
):
    @do
    def ask_name() -> Generator[object, object, object]:
        return (yield Ask("name"))

    program = sequence([ask_name(), reader({"name": "inner"})(ask_name()), ask_name()])
    assert run(reader({"name": "outer"})(program)) == ("outer", "inner", "outer")


def test_an_effect_nobody_answers_is_not_swallowed_and_later_programs_never_start() -> None:
    events: list[str] = []

    @do
    def ask_missing() -> Generator[object, object, object]:
        return (yield Ask("missing"))

    with pytest.raises(Exception, match=r"(?i)(unhandled|handler)"):
        run(sequence([ask_missing(), _settled_row(2, events)]))
    assert events == []


def test_nested_sequences_keep_the_tuple_shape() -> None:
    assert run(sequence([sequence([Pure(1)]), Pure(2)])) == ((1,), 2)


def test_running_the_same_program_twice_does_not_share_the_accumulation() -> None:
    program = sequence([Pure(1), Pure(2)])
    assert run(program) == (1, 2)
    assert run(program) == (1, 2)


def test_a_long_chain_does_not_hit_the_recursion_limit() -> None:
    answer = run(sequence(Pure(index) for index in range(50_000)))
    assert len(answer) == 50_000
    assert answer[0] == 0
    assert answer[-1] == 49_999


def _cpu_seconds(make_and_run: Callable[[int], object], rows: int) -> float:
    """The minimum CPU time over 3 runs of rows lines (reduces load jitter)."""
    return min(_cpu_seconds_once(make_and_run, rows) for _ in range(3))


def _cpu_seconds_once(make_and_run: Callable[[int], object], rows: int) -> float:
    started = time.process_time()
    make_and_run(rows)
    return time.process_time() - started


@_ROW_FORMS
def test_the_cost_grows_in_proportion_to_the_rows(
    make_row: Callable[..., Program[int, object]],
) -> None:
    """3 thousand lines -> 30 thousand lines (10x): the cost is about 10x. Appending to a tuple would make it about 100x (measured: 3 thousand lines 35 ms -> 30 thousand lines 3.5 s).

    The measurement jitters with load, so the boundary is 30x, between linear (about 10x) and quadratic (about 100x).
    """

    def run_rows(rows: int) -> object:
        sink: list[str] = []
        return run(sequence(make_row(index, sink) for index in range(rows)))

    run_rows(300)  # keep the first-time setup (import, bytecode) out of the measurement
    small = _cpu_seconds(run_rows, 3_000)
    large = _cpu_seconds(run_rows, 30_000)
    assert large < small * 30, (small, large)
