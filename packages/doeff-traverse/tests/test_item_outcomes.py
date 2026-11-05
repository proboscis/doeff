"""Tests the contract that the Traverse handler reports each item's outcome with ItemOutcome.

When a progress bar was added to a pipeline with failing items, the bar never closed, stuck at n < total
: the progress handler counted only the items that finished wrapping f, and could not count failed items, items dropped by When,
or items carried over from an earlier stage — only the Traverse handler knew an item's outcome.

Contract: sequential, parallel, and parallel_fail_fast emit ItemOutcome each time an item's outcome (ok, failed, skipped, carried) is decided.
It is emitted under the handler between Traverse and the handler (the inner handler reattached to the item Program),
and if there is no receiver the handler itself answers None. It is not emitted unless one of the inner handlers names ItemOutcome in an effect argument annotation
(emitting costs about as much as running an item, and a Traverse nobody counts does not pay it). progress_bars counts it.
"""

import io
from collections.abc import Callable, Iterable, Iterator
from typing import TypeVar

import pytest
from doeff_core_effects import try_handler
from doeff_core_effects import scheduled
from doeff_traverse import (
    Collection,
    ItemOutcome,
    ItemOutcomeNotPassedError,
    Skip,
    Traverse,
    fail_handler,
    parallel,
    parallel_fail_fast,
    progress_bars,
    sequential,
)
from tqdm import tqdm

from doeff import Pass, Program, Resume, ResumeThrow, do, run
from doeff import handler as _program_handler
from doeff.program import ProgramHandler

_T = TypeVar("_T")

_STRATEGIES: dict[str, Callable[[], ProgramHandler]] = {
    "sequential": lambda: sequential,
    "parallel": lambda: parallel(concurrency=2),
}


@do
def _first(x: int):
    """Stage 1 items: 3 fails, 5 is dropped by When (Skip), the others are multiplied by 10."""
    if x == 3:
        raise ValueError("three")
    if x == 5:
        yield Skip()
    return x * 10


@do
def _second(v: int):
    """Stage 2 items: add 1 to the valid items of stage 1 (items that failed or were skipped in stage 1 are carried over)."""
    if False:
        yield
    return v + 1


@do
def _pipeline(items: Iterable[int]):
    first = yield Traverse(_first, items, label="first")
    second = yield Traverse(_second, first, label="second")
    return (first, second)


@do
def _traversed(f: Callable[[int], Program], items: Iterable[int], label: str | None = None):
    """A Program of just one Traverse (the body of a one-stage test)."""
    return (yield Traverse(f, items, label=label))


class _Reports:
    """A receiver inside a test that names ItemOutcome — keeps what it received in order and passes it outward (it reaches the outer progress_bars too, and if nobody receives it,
    the Traverse handler answers None)."""

    def __init__(self) -> None:
        self._mut_seen: tuple[ItemOutcome, ...] = ()

    @property
    def seen(self) -> tuple[ItemOutcome, ...]:
        """The notices received (in the order received)."""
        return self._mut_seen

    def handler(self) -> ProgramHandler:
        @do
        def watch(effect: ItemOutcome, k):
            if isinstance(effect, ItemOutcome):
                self._mut_seen = (*self._mut_seen, effect)
            yield Pass(effect, k)

        return _program_handler(watch)

    def runs(self) -> list[tuple[ItemOutcome, ...]]:
        """The notices of each Traverse run (in the order first notified; within a run, in item index order)."""
        order: tuple[object, ...] = tuple(
            dict.fromkeys(report.traverse for report in self._mut_seen)
        )
        return [
            tuple(
                sorted(
                    (r for r in self._mut_seen if r.traverse is run_), key=lambda r: r.item.index
                )
            )
            for run_ in order
        ]


class _RecordedBar(tqdm):
    """Records, on a real tqdm (output discarded), whether progress_bars called close."""

    def __init__(self, *, total: int, desc: str) -> None:
        super().__init__(total=total, desc=desc, file=io.StringIO())
        self._mut_closed_by_handler = False

    @property
    def closed_by_handler(self) -> bool:
        """Whether progress_bars called close."""
        return self._mut_closed_by_handler

    def close(self) -> None:
        self._mut_closed_by_handler = True
        super().close()


class _Bars:
    """make_bar of progress_bars — keeps the bars it made in the order made."""

    def __init__(self) -> None:
        self._mut_made: tuple[_RecordedBar, ...] = ()

    def make(self, *, total: int, desc: str) -> _RecordedBar:
        bar = _RecordedBar(total=total, desc=desc)
        self._mut_made = (*self._mut_made, bar)
        return bar

    def states(self) -> list[tuple[str, int, int, bool]]:
        return [(bar.desc, bar.n, bar.total, bar.closed_by_handler) for bar in self._mut_made]


def _run_under(strategy: ProgramHandler, inside: list[ProgramHandler], program: Program[_T]) -> _T:
    """Runs program under inside (inner first) and under the Traverse handler strategy."""
    body: Program[_T] = program
    for install in [*inside, fail_handler, try_handler]:
        body = install(body)
    return run(scheduled(strategy(body)))


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_progress_bars_close_at_total_when_items_fail(name: str) -> None:
    """Acceptance c6eeb01ac: even in a pipeline with failed, skipped, and carried-over items, the tqdm bar closes at n == total."""
    bars = _Bars()
    _, second = _run_under(
        _STRATEGIES[name](), [progress_bars(bars.make)], _pipeline([1, 2, 3, 4, 5, 6])
    )
    assert second.valid_values == [11, 21, 41, 61]
    assert bars.states() == [("first", 6, 6, True), ("second", 6, 6, True)]


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_each_item_reports_the_outcome_the_traverse_answers(name: str) -> None:
    reports = _Reports()
    first, second = _run_under(
        _STRATEGIES[name](), [reports.handler()], _pipeline([1, 2, 3, 4, 5, 6])
    )
    first_run, second_run = reports.runs()
    assert [r.outcome for r in first_run] == ["ok", "ok", "failed", "ok", "skipped", "ok"]
    assert [r.outcome for r in second_run] == ["ok", "ok", "carried", "ok", "carried", "ok"]
    for run_, answered, label in [(first_run, first, "first"), (second_run, second, "second")]:
        assert {(r.label, r.total) for r in run_} == {(label, 6)}
        # The notified items are the very items of the Collection Traverse answers (value, failure mark, history)
        assert [(r.item.index, r.item.failed, r.item.value, r.item.history) for r in run_] == [
            (item.index, item.failed, item.value, item.history) for item in answered.all_items
        ]


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_items_carried_before_the_first_run_item_are_reported_too(name: str) -> None:
    """The first item of stage 2 is a carry-over that failed in stage 1 — an item before the inner handlers are read is also notified after the read, and the bar closes."""
    reports = _Reports()
    bars = _Bars()
    _, second = _run_under(
        _STRATEGIES[name](), [reports.handler(), progress_bars(bars.make)], _pipeline([3, 1, 2])
    )
    assert second.valid_values == [11, 21]
    _first_run, second_run = reports.runs()
    assert [(r.item.index, r.outcome) for r in second_run] == [(0, "carried"), (1, "ok"), (2, "ok")]
    assert bars.states() == [("first", 3, 3, True), ("second", 3, 3, True)]


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_a_traverse_with_no_item_to_run_reports_nothing(name: str) -> None:
    """A Traverse with 0 items and a Traverse of only carried-over items notify nothing and open no bar (sequential does not read the inner handlers — #2958)."""
    reports = _Reports()
    bars = _Bars()
    _, second = _run_under(
        _STRATEGIES[name](), [reports.handler(), progress_bars(bars.make)], _pipeline([3, 5])
    )
    assert second.valid_values == []
    [first_run] = reports.runs()
    assert [r.outcome for r in first_run] == ["failed", "skipped"]
    assert bars.states() == [("first", 2, 2, True)]

    empty = _Reports()
    _run_under(_STRATEGIES[name](), [empty.handler()], _traversed(_first, [], label="first"))
    assert empty.seen == ()


def test_parallel_fail_fast_reports_the_items_up_to_the_failure_and_the_rest_cancelled() -> None:
    """When it stops at item 3, following the items so far and the failed item, the stopped item 4 is notified as cancelled."""
    reports = _Reports()
    with pytest.raises(ValueError, match="three"):
        _run_under(
            parallel_fail_fast(concurrency=1),
            [reports.handler()],
            _traversed(_first, [1, 2, 3, 4], label="first"),
        )
    [first_run] = reports.runs()
    assert [(r.item.index, r.outcome) for r in first_run] == [
        (0, "ok"),
        (1, "ok"),
        (2, "failed"),
        (3, "cancelled"),
    ]
    assert isinstance(first_run[2].item.value, ValueError)
    assert first_run[3].item.failed
    assert [entry.event for entry in first_run[3].item.history] == ["cancelled"]


def test_parallel_fail_fast_bar_closes_when_no_item_fails() -> None:
    bars = _Bars()
    answered = _run_under(
        parallel_fail_fast(concurrency=2),
        [progress_bars(bars.make)],
        _traversed(_first, [1, 2, 4, 5], label="first"),
    )
    assert answered.valid_values == [10, 20, 40]
    assert bars.states() == [("first", 4, 4, True)]


@do
def _nested(x: int):
    inner = yield Traverse(_second, list(range(x)), label="inner")
    return inner.valid_values


@pytest.mark.parametrize(
    "strategy",
    [sequential, lambda: parallel(concurrency=2), lambda: parallel_fail_fast(concurrency=2)],
    ids=["sequential", "parallel", "parallel_fail_fast"],
)
def test_a_nested_traverse_reports_as_its_own_run(strategy: Callable[[], ProgramHandler]) -> None:
    """A Traverse emitted by an item Program is also notified under the reattached inner handler — a separate traverse each time."""
    reports = _Reports()
    bars = _Bars()
    answered = _run_under(
        strategy(),
        [reports.handler(), progress_bars(bars.make)],
        _traversed(_nested, [1, 2], label="outer"),
    )
    assert answered.valid_values == [[1], [1, 2]]
    runs = reports.runs()
    assert sorted((run_[0].label, len(run_), run_[0].total) for run_ in runs) == [
        ("inner", 1, 1),
        ("inner", 2, 2),
        ("outer", 2, 2),
    ]
    assert sorted(bars.states()) == [
        ("inner", 1, 1, True),
        ("inner", 2, 2, True),
        ("outer", 2, 2, True),
    ]


class _Pulls:
    """Keeps, in the order they happened, where items were pulled from the iterable and where items were run."""

    def __init__(self) -> None:
        self._mut_events: tuple[str, ...] = ()

    @property
    def events(self) -> tuple[str, ...]:
        """Where they were pulled and where they were run (in the order they happened)."""
        return self._mut_events

    def items(self, values: list[int]) -> Iterator[int]:
        for value in values:
            self._mut_events = (*self._mut_events, f"pull {value}")
            yield value

    def step(self, x: int) -> Program:
        @do
        def ran():
            self._mut_events = (*self._mut_events, f"run {x}")
            if False:
                yield
            return x

        return ran()


def test_an_iterable_without_length_is_read_to_its_end_only_when_reported() -> None:
    """sequential pulls items one at a time from an iterable with no length. Only when notifying, it reads the rest to the end at the first item, for total."""
    quiet = _Pulls()
    _run_under(sequential, [], _traversed(quiet.step, quiet.items([1, 2, 3])))
    assert quiet.events == ("pull 1", "run 1", "pull 2", "run 2", "pull 3", "run 3")

    watched = _Pulls()
    reports = _Reports()
    _run_under(
        sequential, [reports.handler()], _traversed(watched.step, watched.items([1, 2, 3]))
    )
    assert watched.events == ("pull 1", "pull 2", "pull 3", "run 1", "run 2", "run 3")
    assert [(r.item.value, r.total) for r in reports.seen] == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_nothing_is_reported_unless_a_handler_inside_names_item_outcome(name: str) -> None:
    """Not emitted if no naming receiver is inside: it does not reach an outer receiver, and ItemOutcome does not come to an inner handler that accepts anything either."""
    outside = _Reports()
    seen_inside: tuple[type, ...] = ()

    @do
    def any_effect(effect, k):  # no annotation = accepts every effect
        nonlocal seen_inside
        seen_inside = (*seen_inside, type(effect))
        yield Pass(effect, k)

    body = _pipeline([1, 2, 3, 4, 5, 6])
    for install in [_program_handler(any_effect), fail_handler, try_handler]:
        body = install(body)
    _, second = run(scheduled(outside.handler()(_STRATEGIES[name]()(body))))
    assert second.valid_values == [11, 21, 41, 61]
    assert outside.seen == ()
    assert ItemOutcome not in seen_inside


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_a_report_no_handler_takes_is_answered_by_the_traverse_handler(name: str) -> None:
    """Even under a handler that names it but Passes without receiving, the Traverse handler answers and the run finishes."""

    @do
    def passes(effect: ItemOutcome, k):
        yield Pass(effect, k)

    _, second = _run_under(
        _STRATEGIES[name](), [_program_handler(passes)], _pipeline([1, 2, 3, 4, 5, 6])
    )
    assert isinstance(second, Collection)
    assert second.valid_values == [11, 21, 41, 61]


# Receiver rules: a receiver looks at the notice and passes it outward, and only the Traverse
# handler answers. A — if one receiver answers and stops, then depending on order another receiver (a second bar, the failure recorder) silently received nothing.
# B — a receiver that throws an exception back at the notice (stopping Traverse at a failure limit) passed the test while moving the decision of how Traverse runs to the receiver.


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
@pytest.mark.parametrize("order", ["bars-inside", "bars-outside"])
def test_consumers_compose_in_any_order(name: str, order: str) -> None:
    """Two progress_bars and the recorder receive all notices in any order (because each passes it on)."""
    reports = _Reports()
    bars, more_bars = _Bars(), _Bars()
    consumers = [progress_bars(bars.make), reports.handler(), progress_bars(more_bars.make)]
    _, second = _run_under(
        _STRATEGIES[name](),
        consumers if order == "bars-inside" else consumers[::-1],
        _pipeline([1, 2, 3, 4, 5, 6]),
    )
    assert second.valid_values == [11, 21, 41, 61]
    assert len(reports.seen) == 12
    closed = [("first", 6, 6, True), ("second", 6, 6, True)]
    assert (bars.states(), more_bars.states()) == (closed, closed)


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_a_consumer_that_answers_the_report_fails_the_traverse(name: str) -> None:
    """A receiver that answers the notice and stops hides the notice from outer receivers — the Traverse handler rejects it with ItemOutcomeNotPassedError."""

    @do
    def answers(effect: ItemOutcome, k):
        return (yield Resume(k, None))

    with pytest.raises(ItemOutcomeNotPassedError) as raised:
        _run_under(_STRATEGIES[name](), [_program_handler(answers)], _pipeline([1, 2, 3, 4, 5, 6]))
    assert raised.value.cause is None


class _BudgetError(RuntimeError):
    """The exception a receiver throws back (the shape of a receiver trying to stop Traverse)."""


@do
def _throws_back(effect: ItemOutcome, k):
    if effect.outcome == "failed":
        return (yield ResumeThrow(k, _BudgetError(f"failed at {effect.item.index}")))
    yield Pass(effect, k)


@do
def _raises(effect: ItemOutcome, k):
    if effect.outcome == "failed":
        raise _BudgetError(f"failed at {effect.item.index}")
    yield Pass(effect, k)


@pytest.mark.parametrize(
    "strategy",
    [sequential, lambda: parallel(concurrency=2), lambda: parallel_fail_fast(concurrency=1)],
    ids=["sequential", "parallel", "parallel_fail_fast"],
)
@pytest.mark.parametrize("consumer", [_throws_back, _raises], ids=["resume-throw", "raise"])
def test_a_consumer_that_throws_into_the_report_fails_the_traverse(
    strategy: Callable[[], ProgramHandler], consumer: Callable[..., object]
) -> None:
    """A receiver that throws back at the notice holds the decision to stop Traverse — the Traverse handler rejects it with ItemOutcomeNotPassedError and keeps what was thrown as cause."""
    with pytest.raises(ItemOutcomeNotPassedError) as raised:
        _run_under(
            strategy(),
            [_program_handler(consumer)],
            _traversed(_first, [1, 2, 3, 4], label="first"),
        )
    assert isinstance(raised.value.cause, _BudgetError)
    assert raised.value.report.outcome == "failed"


@do
def _nested_failing(x: int):
    inner = yield Traverse(_first, [x, 3], label="inner")
    return inner.valid_values


@pytest.mark.parametrize(
    "strategy",
    [sequential, lambda: parallel(concurrency=2), lambda: parallel_fail_fast(concurrency=1)],
    ids=["sequential", "parallel", "parallel_fail_fast"],
)
def test_a_broken_report_contract_in_a_nested_traverse_is_no_item_failure(
    strategy: Callable[[], ProgramHandler],
) -> None:
    """Even if a receiver throws back in a nested Traverse, the outer Traverse does not swallow it into the Collection as an outer item's failure
    (stacking two From in for/do emits an inner Traverse per outer item — swallowing would silence a contract error)."""
    with pytest.raises(ItemOutcomeNotPassedError) as raised:
        _run_under(
            strategy(),
            [_program_handler(_throws_back)],
            _traversed(_nested_failing, [1, 2], label="outer"),
        )
    assert raised.value.report.label == "inner"
