"""Tests the contract that when the Traverse handler stops it stops the items it started and reports the stopped items as cancelled.

Origin: at origin/main da660e83e, parallel_fail_fast kept running the remaining items even after exiting on an item
failure (some items even started running after the failure), and when the task running Traverse was Cancelled from outside,
sequential swallowed the cancellation as one item's failure and ran the remaining items, while the parallel handlers had only the owner exit and the item tasks ran to completion.
Stopped items were not reported, and the progress_bars bar stayed open at n < total.

Contract:
- TaskCancelledError does not become an item outcome; it ends Traverse (for all 3 handlers).
- When Gather throws, parallel and parallel_fail_fast Cancel the remaining item tasks, wait for them to unwind, and then rethrow.
- Stopped items (items that were running and cancelled, items that had not started yet) are reported with ItemOutcome cancelled. A Traverse that runs one or more
    items emits exactly total notices whether it finishes normally, stops by fail-fast, or is cancelled.
- progress_bars does not advance n on cancelled, and counts and closes at total (the bar closes at n < total).

Items are made to wait on a gate (a Promise's Future), not on time: items stay stopped until the gate opens, and we check that items do not keep running
even if the gate is opened after stopping. Every test runs on both the Python and Rust schedulers.
"""

import io
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, TypeVar

import pytest
from doeff_core_effects import Try
from doeff_core_effects import try_handler
from doeff_core_effects import (
    Cancel,
    CompletePromise,
    CreatePromise,
    Future,
    SchedulerImplementation,
    Spawn,
    TaskCancelledError,
    Wait,
    scheduled,
)
from doeff_traverse import (
    Collection,
    ItemOutcome,
    Traverse,
    fail_handler,
    parallel,
    parallel_fail_fast,
    progress_bars,
    sequential,
)
from tqdm import tqdm

from doeff import Err, K, Pass, Program, do, run
from doeff import handler as _program_handler
from doeff.program import ProgramHandler

if TYPE_CHECKING:
    from doeff import EffectGenerator

_T = TypeVar("_T")

_IMPLEMENTATIONS: list[SchedulerImplementation] = ["python", "rust"]

_STRATEGIES: dict[str, Callable[[], ProgramHandler]] = {
    "sequential": lambda: sequential,
    "parallel": lambda: parallel(concurrency=2),
    "parallel_fail_fast": lambda: parallel_fail_fast(concurrency=2),
}


class _Items:
    """A Program for the item, and a record of what happened per item (started, received cancellation, finished).

    Items wait until the gate opens, and a failing item fails right after it starts."""

    def __init__(self, failing: int | None = None) -> None:
        self._failing = failing
        self._mut_events: tuple[tuple[str, int], ...] = ()

    @property
    def events(self) -> tuple[tuple[str, int], ...]:
        """What happened (in the order it happened)."""
        return self._mut_events

    def of(self, kind: str) -> list[int]:
        """The items where kind (started, cancelled, finished) happened (in the order it happened)."""
        return [x for happened, x in self._mut_events if happened == kind]

    def mark(self, name: str) -> None:
        """Places a mark for what happened on the test body's side (asked for cancellation, Traverse exited) among the item records."""
        self._note(name, -1)

    def after(self, name: str) -> list[tuple[str, int]]:
        """What happened to items after the mark name (in the order it happened)."""
        return list(self._mut_events[self._mut_events.index((name, -1)) + 1 :])

    def _note(self, kind: str, x: int) -> None:
        self._mut_events = (*self._mut_events, (kind, x))

    def gated(self, gate: Future[None]) -> Callable[[int], Program[int]]:
        @do
        def item(x: int) -> "EffectGenerator[int]":
            self._note("started", x)
            if x == self._failing:
                raise ValueError(f"item {x}")
            try:
                yield Wait(gate)
            except TaskCancelledError:
                self._note("cancelled", x)
                raise
            self._note("finished", x)
            return x * 10

        return item


class _Reports:
    """A receiver that names ItemOutcome — keeps what it received in order and passes it outward."""

    def __init__(self) -> None:
        self._mut_seen: tuple[ItemOutcome, ...] = ()

    @property
    def seen(self) -> tuple[ItemOutcome, ...]:
        """The notices received (in the order received)."""
        return self._mut_seen

    def handler(self) -> ProgramHandler:
        @do
        def watch(effect: ItemOutcome, k: K) -> "EffectGenerator[object]":
            if isinstance(effect, ItemOutcome):
                self._mut_seen = (*self._mut_seen, effect)
            yield Pass(effect, k)

        return _program_handler(watch)

    def of(self, label: str | None) -> dict[int, list[str]]:
        """The outcomes of notices per item index of the Traverse with label (in the order received)."""
        outcomes: dict[int, list[str]] = {}
        for report in self._mut_seen:
            if report.label == label:
                outcomes[report.item.index] = [*outcomes.get(report.item.index, []), report.outcome]
        return dict(sorted(outcomes.items()))


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

    def lines(self) -> list[int]:
        """The terminal line of the bar made (tqdm's position — if a bar is left open, later bars go one line down)."""
        return [abs(bar.pos) for bar in self._mut_made]


def _run_under(
    strategy: ProgramHandler,
    inside: list[ProgramHandler],
    program: Program[_T],
    implementation: SchedulerImplementation,
) -> _T:
    """Runs program under inside (inner first) and under the Traverse handler strategy, on the implementation scheduler."""
    body: Program[_T] = program
    for install in [*inside, fail_handler, try_handler]:
        body = install(body)
    return run(scheduled(strategy(body), implementation=implementation))


@do
def _traversed(
    f: Callable[[int], Program[_T]], items: Iterable[int], label: str
) -> "EffectGenerator[Collection[_T]]":
    return (yield Traverse(f, items, label=label))


@do
def _nothing() -> "EffectGenerator[None]":
    if False:
        yield


@do
def _let_the_others_run(rounds: int = 20) -> "EffectGenerator[None]":
    """Yields the calling task's turn rounds times — in the meantime the queued tasks (items, the Traverse owner) run."""
    for _ in range(rounds):
        turn = yield Spawn(_nothing())
        yield Wait(turn)


def _assert_total_reports(reports: _Reports, label: str | None, total: int) -> None:
    """The Traverse with label emitted exactly total notices, one per item."""
    runs = {report.traverse for report in reports.seen if report.label == label}
    assert len(runs) == 1
    assert {report.total for report in reports.seen if report.label == label} == {total}
    assert {index: len(outcomes) for index, outcomes in reports.of(label).items()} == dict.fromkeys(
        range(total), 1
    )


@pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
def test_parallel_fail_fast_stops_its_items_when_the_run_goes_on(
    implementation: SchedulerImplementation,
) -> None:
    """Acceptance c717e1a24: even if item 2's failure is caught with Try and run continues, running items are cancelled and the remaining items do not start running.

    There are 8 items, 4 at a time. Items 0, 1, 3 wait on the gate, and items 4 to 7 wait for a free slot when item 2 fails."""
    items = _Items(failing=2)
    reports = _Reports()

    @do
    def main() -> "EffectGenerator[object]":
        gate = yield CreatePromise()
        caught = yield Try(_traversed(items.gated(gate.future), range(8), label="first"))
        items.mark("raised")
        # run continues: the gate opens, and the queued tasks get their turn
        yield CompletePromise(gate, None)
        yield _let_the_others_run()
        return caught

    caught = _run_under(
        parallel_fail_fast(concurrency=4), [reports.handler()], main(), implementation
    )
    assert isinstance(caught, Err)
    assert isinstance(caught.error, ValueError)
    assert items.of("started") == [0, 1, 2, 3]
    assert sorted(items.of("cancelled")) == [0, 1, 3]
    assert items.of("finished") == []
    # By the time Traverse exits, all items are stopped
    assert items.after("raised") == []
    assert reports.of("first") == {
        0: ["cancelled"],
        1: ["cancelled"],
        2: ["failed"],
        3: ["cancelled"],
        4: ["cancelled"],
        5: ["cancelled"],
        6: ["cancelled"],
        7: ["cancelled"],
    }


@pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_cancelling_the_task_running_a_traverse_stops_its_items(
    name: str, implementation: SchedulerImplementation
) -> None:
    """Acceptance c21a5e2a7: when the task running Traverse is Cancelled from outside, running items are cancelled and the remaining items do not start running
    — no item runs to completion even if the gate is opened after cancellation. The waiter receives TaskCancelledError."""
    items = _Items()
    reports = _Reports()

    @do
    def main() -> "EffectGenerator[object]":
        gate = yield CreatePromise()
        task = yield Spawn(_traversed(items.gated(gate.future), range(8), label="first"))
        yield _let_the_others_run()
        items.mark("cancel")
        yield Cancel(task)
        yield CompletePromise(gate, None)
        waited = yield Try(Wait(task))
        yield _let_the_others_run()
        return waited

    waited = _run_under(_STRATEGIES[name](), [reports.handler()], main(), implementation)
    running = [0] if name == "sequential" else [0, 1]
    assert isinstance(waited, Err)
    assert isinstance(waited.error, TaskCancelledError)
    assert items.of("started") == running
    assert sorted(items.of("cancelled")) == running
    assert items.of("finished") == []
    # After asking for cancellation, the only thing that happened to items is that they received the cancellation
    assert [kind for kind, _ in items.after("cancel")] == ["cancelled"] * len(running)
    assert reports.of("first") == {index: ["cancelled"] for index in range(8)}


@do
def _ends(items: _Items) -> "EffectGenerator[Collection[int]]":
    """A Traverse whose gate was opened first — it finishes without any item waiting."""
    gate = yield CreatePromise()
    yield CompletePromise(gate, None)
    return (yield _traversed(items.gated(gate.future), range(8), label="first"))


@do
def _fails_fast(items: _Items) -> "EffectGenerator[object]":
    gate = yield CreatePromise()
    return (yield Try(_traversed(items.gated(gate.future), range(8), label="first")))


@do
def _is_cancelled(items: _Items) -> "EffectGenerator[object]":
    gate = yield CreatePromise()
    task = yield Spawn(_traversed(items.gated(gate.future), range(8), label="first"))
    yield _let_the_others_run()
    yield Cancel(task)
    return (yield Try(Wait(task)))


@pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
@pytest.mark.parametrize(
    ("strategy", "ending", "failing"),
    [
        (sequential, _ends, None),
        (lambda: parallel(concurrency=2), _ends, None),
        (lambda: parallel_fail_fast(concurrency=2), _ends, None),
        (lambda: parallel_fail_fast(concurrency=1), _fails_fast, 0),
        (lambda: parallel_fail_fast(concurrency=4), _fails_fast, 2),
        (sequential, _is_cancelled, None),
        (lambda: parallel(concurrency=2), _is_cancelled, None),
        (lambda: parallel_fail_fast(concurrency=2), _is_cancelled, None),
    ],
    ids=[
        "sequential-ends",
        "parallel-ends",
        "parallel_fail_fast-ends",
        "parallel_fail_fast-fails-fast-1",
        "parallel_fail_fast-fails-fast-4",
        "sequential-is-cancelled",
        "parallel-is-cancelled",
        "parallel_fail_fast-is-cancelled",
    ],
)
def test_a_traverse_reports_exactly_total_outcomes_however_it_ends(
    strategy: Callable[[], ProgramHandler],
    ending: Callable[[_Items], Program[object]],
    failing: int | None,
    implementation: SchedulerImplementation,
) -> None:
    """Acceptance cb8ad96b3: a Traverse that runs one or more items emits one notice per item — exactly total — whether it finishes normally, stops by fail-fast, or is cancelled.
    Stopped items are cancelled."""
    reports = _Reports()
    _run_under(strategy(), [reports.handler()], ending(_Items(failing=failing)), implementation)
    _assert_total_reports(reports, "first", 8)
    assert reports.of("first") == {
        index: ["ok" if ending is _ends else "failed" if index == failing else "cancelled"]
        for index in range(8)
    }


@pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
@pytest.mark.parametrize(
    "strategy",
    [sequential, lambda: parallel(concurrency=2), lambda: parallel_fail_fast(concurrency=2)],
    ids=["sequential", "parallel", "parallel_fail_fast"],
)
def test_cancelling_a_traverse_stops_and_reports_the_traverses_inside_its_items(
    strategy: Callable[[], ProgramHandler], implementation: SchedulerImplementation
) -> None:
    """A Traverse inside an item (the stacked From of for/do) also stops: when the task running the outer Traverse is cancelled, by the time the waiter wakes up
    the inner items also stop (they do not run to completion even if the gate is opened later), and both the outer and inner Traverse notify exactly total per run.

    The gate is opened after the waiter wakes up: cancellation arrives one level at a time, so if opened right after cancellation, the waiter is woken before it reaches the inner Traverse and
    the inner items that were in the queue run to completion (items that were in the queue before the owner woke up)."""
    items = _Items()
    reports = _Reports()

    @do
    def main() -> "EffectGenerator[object]":
        gate = yield CreatePromise()

        @do
        def outer_item(x: int) -> "EffectGenerator[list[int]]":
            inner = yield _traversed(items.gated(gate.future), range(3), label=f"inner-{x}")
            return inner.valid_values

        task = yield Spawn(_traversed(outer_item, range(2), label="outer"))
        yield _let_the_others_run()
        yield Cancel(task)
        waited = yield Try(Wait(task))
        yield CompletePromise(gate, None)
        yield _let_the_others_run()
        return waited

    waited = _run_under(strategy(), [reports.handler()], main(), implementation)
    assert isinstance(waited, Err)
    assert isinstance(waited.error, TaskCancelledError)
    assert items.of("finished") == []
    assert {report.outcome for report in reports.seen} == {"cancelled"}
    _assert_total_reports(reports, "outer", 2)
    for inner in {report.label for report in reports.seen} - {"outer"}:
        _assert_total_reports(reports, inner, 3)


@pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
@pytest.mark.parametrize(
    ("ending", "failing", "counted"),
    [(_fails_fast, 2, 1), (_is_cancelled, None, 0)],
    ids=["fails-fast", "is-cancelled"],
)
def test_parallel_fail_fast_closes_its_bar_below_total_when_it_stops(
    ending: Callable[[_Items], Program[object]],
    failing: int | None,
    counted: int,
    implementation: SchedulerImplementation,
) -> None:
    """Acceptance cf75ceb59: when parallel_fail_fast stops on an item failure or an outside cancellation, the bar does not advance n for stopped items and closes at n < total
    (the same look as when a bare tqdm loop exits by an exception). Failed items are counted."""
    bars = _Bars()
    _run_under(
        parallel_fail_fast(concurrency=4),
        [progress_bars(bars.make)],
        ending(_Items(failing=failing)),
        implementation,
    )
    assert bars.states() == [("first", counted, 8, True)]


@pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
def test_a_progress_bars_used_again_after_a_stopped_traverse_keeps_no_bar_open(
    implementation: SchedulerImplementation,
) -> None:
    """Even if the same progress_bars is reused after a run stopped by fail-fast, no open bar remains
    (previously the stopped bar kept tqdm's position, and later bars shifted down one line at a time)."""
    bars = _Bars()
    count = progress_bars(bars.make)
    _run_under(
        parallel_fail_fast(concurrency=4), [count], _fails_fast(_Items(failing=2)), implementation
    )
    _run_under(sequential, [count], _ends(_Items()), implementation)
    assert bars.states() == [("first", 1, 8, True), ("first", 8, 8, True)]
    stopped_run, next_run = bars.lines()
    assert next_run == stopped_run


@do
def _blocked() -> "EffectGenerator[None]":
    """The body of a task that keeps waiting on a Promise nobody completes (it does not finish until cancelled)."""
    never = yield CreatePromise()
    yield Wait(never.future)


@do
def _cancelled_task_waited(x: int) -> "EffectGenerator[int]":
    """Item 1 Waits on another task that was cancelled and receives TaskCancelledError (not wrapped in Try)."""
    if x == 1:
        other = yield Spawn(_blocked())
        yield Cancel(other)
        yield Wait(other)
    return x


@pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_a_task_cancelled_error_out_of_an_item_ends_the_traverse(
    name: str, implementation: SchedulerImplementation
) -> None:
    """A TaskCancelledError leaking out of an item does not become that single item's failure; it ends Traverse (an item that wants to survive wraps its own Wait in Try).
    Stopped items are also notified, so it comes to exactly total."""
    reports = _Reports()

    @do
    def main() -> "EffectGenerator[object]":
        return (yield Try(_traversed(_cancelled_task_waited, range(4), label="first")))

    caught = _run_under(_STRATEGIES[name](), [reports.handler()], main(), implementation)
    assert isinstance(caught, Err)
    assert isinstance(caught.error, TaskCancelledError)
    _assert_total_reports(reports, "first", 4)
    assert reports.of("first")[1] == ["cancelled"]


@pytest.mark.parametrize("implementation", _IMPLEMENTATIONS)
@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_an_item_that_tries_its_wait_on_a_cancelled_task_is_an_item_like_any_other(
    name: str, implementation: SchedulerImplementation
) -> None:
    """If an item wraps its own Wait in Try, even waiting on a cancelled task it receives TaskCancelledError as a value and the item finishes normally."""

    @do
    def tolerant(x: int) -> "EffectGenerator[bool]":
        other = yield Spawn(_blocked())
        yield Cancel(other)
        waited = yield Try(Wait(other))
        return isinstance(waited, Err) and isinstance(waited.error, TaskCancelledError)

    answered = _run_under(
        _STRATEGIES[name](), [], _traversed(tolerant, range(3), label="first"), implementation
    )
    assert answered.valid_values == [True, True, True]
