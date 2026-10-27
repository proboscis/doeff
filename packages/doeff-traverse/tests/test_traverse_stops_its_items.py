"""Traverse の handler が止まる時に自分の始めた件を止め、止めた件を cancelled と知らせる契約の検(card ki-e0fd319d5b75)。

出自は調査 ki-0d7fee62cd59(依頼 lt-JH5Z0X456AED038XTNFWBDH316)。origin/main da660e83e では、parallel_fail_fast は件の
失敗で抜けた後も残りの件を走らせ続け(失敗の後に走り始める件もあった)、Traverse を走らせる task を外から Cancel すると
sequential は取り消しを 1 件の失敗として飲み込んで残りの件を走らせ、parallel 系は持ち主だけが抜けて件の task は走り切った。
止めた件は知らされず、progress_bars の bar は n < total で開いたまま残った。

契約:
- TaskCancelledError は件の結末にならず、Traverse を終える(3 つの handler とも)。
- parallel・parallel_fail_fast は Gather が投げたら、残りの件の task を Cancel し、解け終わるのを待ってから投げ直す。
- 止めた件(走っていて取り消された件・まだ始まっていない件)は ItemOutcome の cancelled で知らせる。件を 1 つ以上走らせる
  Traverse は、普通に終わっても、fail-fast で止まっても、取り消されても、total ちょうどの知らせを出す。
- progress_bars は cancelled で n を進めず、数えて total で閉じる(bar は n < total のまま閉じる)。

件は時刻ではなく門(Promise の Future)で待たせる: 門が開くまで件は止まっていて、止めた後に門を開けても件が走り続けない
事を見る。どの検も Python 版と Rust 版の scheduler の両方で走らせる。
"""

import io
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, TypeVar

import pytest
from doeff_core_effects.effects import Try
from doeff_core_effects.handlers import try_handler
from doeff_core_effects.scheduler import (
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
from doeff_traverse.collection import Collection
from doeff_traverse.effects import ItemOutcome, Traverse
from doeff_traverse.handlers import (
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
    "sequential": sequential,
    "parallel": lambda: parallel(concurrency=2),
    "parallel_fail_fast": lambda: parallel_fail_fast(concurrency=2),
}


class _Items:
    """件の Program と、件ごとに起きた事(始まった・取り消しを受けた・終わった)の記録。

    件は門が開くまで待ち、failing の件は始まってすぐ失敗する。"""

    def __init__(self, failing: int | None = None) -> None:
        self._failing = failing
        self._mut_events: tuple[tuple[str, int], ...] = ()

    @property
    def events(self) -> tuple[tuple[str, int], ...]:
        """起きた事(起きた順)。"""
        return self._mut_events

    def of(self, kind: str) -> list[int]:
        """kind(started・cancelled・finished)の起きた件(起きた順)。"""
        return [x for happened, x in self._mut_events if happened == kind]

    def mark(self, name: str) -> None:
        """検の本体の側で起きた事(取り消しを頼んだ・Traverse が抜けた)の印を、件の記録の間に置く。"""
        self._note(name, -1)

    def after(self, name: str) -> list[tuple[str, int]]:
        """印 name の後に件に起きた事(起きた順)。"""
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
    """ItemOutcome を名指す受け手 — 受けた順に持ち、外へ渡す。"""

    def __init__(self) -> None:
        self._mut_seen: tuple[ItemOutcome, ...] = ()

    @property
    def seen(self) -> tuple[ItemOutcome, ...]:
        """受けた知らせ(受けた順)。"""
        return self._mut_seen

    def handler(self) -> ProgramHandler:
        @do
        def watch(effect: ItemOutcome, k: K) -> "EffectGenerator[object]":
            if isinstance(effect, ItemOutcome):
                self._mut_seen = (*self._mut_seen, effect)
            yield Pass(effect, k)

        return _program_handler(watch)

    def of(self, label: str | None) -> dict[int, list[str]]:
        """label の Traverse の件の番号ごとの知らせの結末(受けた順)。"""
        outcomes: dict[int, list[str]] = {}
        for report in self._mut_seen:
            if report.label == label:
                outcomes[report.item.index] = [*outcomes.get(report.item.index, []), report.outcome]
        return dict(sorted(outcomes.items()))


class _RecordedBar(tqdm):
    """本物の tqdm(出力は捨てる)に、progress_bars が close を呼んだかを残す。"""

    def __init__(self, *, total: int, desc: str) -> None:
        super().__init__(total=total, desc=desc, file=io.StringIO())
        self._mut_closed_by_handler = False

    @property
    def closed_by_handler(self) -> bool:
        """progress_bars が close を呼んだか。"""
        return self._mut_closed_by_handler

    def close(self) -> None:
        self._mut_closed_by_handler = True
        super().close()


class _Bars:
    """progress_bars の make_bar — 作った bar を作った順に持つ。"""

    def __init__(self) -> None:
        self._mut_made: tuple[_RecordedBar, ...] = ()

    def make(self, *, total: int, desc: str) -> _RecordedBar:
        bar = _RecordedBar(total=total, desc=desc)
        self._mut_made = (*self._mut_made, bar)
        return bar

    def states(self) -> list[tuple[str, int, int, bool]]:
        return [(bar.desc, bar.n, bar.total, bar.closed_by_handler) for bar in self._mut_made]

    def lines(self) -> list[int]:
        """作った bar の端末の行(tqdm の位置 — 開いたままの bar が在ると、後の bar は 1 行下になる)。"""
        return [abs(bar.pos) for bar in self._mut_made]


def _run_under(
    strategy: ProgramHandler,
    inside: list[ProgramHandler],
    program: Program[_T],
    implementation: SchedulerImplementation,
) -> _T:
    """program を inside(内側が先)の下、Traverse の handler strategy の下で、implementation の scheduler で走らせる。"""
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
    """呼んだ task の番を rounds 回譲る — その間に待ち行列の task(件・Traverse の持ち主)が走る。"""
    for _ in range(rounds):
        turn = yield Spawn(_nothing())
        yield Wait(turn)


def _assert_total_reports(reports: _Reports, label: str | None, total: int) -> None:
    """label の Traverse が件の 1 つずつに知らせを 1 つずつ、total ちょうど出した。"""
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
    """受入 c717e1a24: 件 2 の失敗を Try で受けて run が続いても、走っていた件は取り消され、残りの件は走り始めない。

    件は 8 つ・同時に 4 つ。件 0・1・3 は門で待ち、件 4〜7 は空きを待つ所で件 2 が失敗する。"""
    items = _Items(failing=2)
    reports = _Reports()

    @do
    def main() -> "EffectGenerator[object]":
        gate = yield CreatePromise()
        caught = yield Try(_traversed(items.gated(gate.future), range(8), label="first"))
        items.mark("raised")
        # run は続く: 門が開き、待ち行列の task に番が回る
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
    # Traverse が抜けた時には、件はみな止まっている
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
    """受入 c21a5e2a7: Traverse を走らせる task を外から Cancel すると、走っていた件は取り消され、残りの件は走り始めない
    — 取り消しの後に門を開けても、どの件も走り終わらない。待ち手は TaskCancelledError を受ける。"""
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
    # 取り消しを頼んだ後に件に起きたのは、取り消しを受けた事だけ
    assert [kind for kind, _ in items.after("cancel")] == ["cancelled"] * len(running)
    assert reports.of("first") == {index: ["cancelled"] for index in range(8)}


@do
def _ends(items: _Items) -> "EffectGenerator[Collection[int]]":
    """門を先に開けた Traverse — どの件も待たずに終わる。"""
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
    """受入 cb8ad96b3: 件を 1 つ以上走らせる Traverse は、普通に終わっても、fail-fast で止まっても、取り消されても、
    件の 1 つずつに知らせを 1 つずつ — total ちょうど出す。止めた件は cancelled。"""
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
    """件の中の Traverse(for/do の重ねた From)も止まる: 外の Traverse を走らせる task を取り消すと、待ち手が起きるまでに
    内の件も止まり(後で門を開けても走り終わらない)、外の Traverse も内の Traverse も 1 回ごとに total ちょうど知らせる。

    門は待ち手が起きた後に開ける: 取り消しは 1 段ずつ届くので、取り消しの直後に開けると、内の Traverse に届く前に起こされて
    待ち行列に在った内の件は走り終わる(持ち主が起きる前に待ち行列に在った件 — card ki-e0fd319d5b75 が許した形)。"""
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
    """受入 cf75ceb59: parallel_fail_fast が件の失敗か外からの取り消しで止まると、bar は止めた件で n を進めず、n < total の
    まま閉じる(tqdm の素の loop が例外で抜けた時と同じ見え方)。失敗した件は数える。"""
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
    """同じ progress_bars を、fail-fast で止まった run の後に使い回しても、開いたままの bar が残らない
    (前は止まった bar が tqdm の位置を持ち続け、後の bar が 1 行ずつ下にずれた)。"""
    bars = _Bars()
    count = progress_bars(bars.make)
    _run_under(
        parallel_fail_fast(concurrency=4), [count], _fails_fast(_Items(failing=2)), implementation
    )
    _run_under(sequential(), [count], _ends(_Items()), implementation)
    assert bars.states() == [("first", 1, 8, True), ("first", 8, 8, True)]
    stopped_run, next_run = bars.lines()
    assert next_run == stopped_run


@do
def _blocked() -> "EffectGenerator[None]":
    """誰も完了しない Promise を待ち続ける task の本体(取り消されるまで終わらない)。"""
    never = yield CreatePromise()
    yield Wait(never.future)


@do
def _cancelled_task_waited(x: int) -> "EffectGenerator[int]":
    """件 1 は、取り消した別の task を Wait して TaskCancelledError を受ける(Try で包まない)。"""
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
    """件の中から漏れた TaskCancelledError は、その件 1 つの失敗にならず Traverse を終える(耐えたい件は自分の Wait を Try
    で包む)。止めた件も知らせ、total ちょうどになる。"""
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
    """件が自分の Wait を Try で包めば、取り消された task を待っても TaskCancelledError を値として受け、件は普通に終わる。"""

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
