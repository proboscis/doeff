"""Traverse の handler が件ごとの結末を ItemOutcome で知らせる契約の検(card ki-e3a936788353)。

失敗する件のある pipeline に進捗の bar を足すと、bar が n < total のまま閉じなかった(設計検証
lt-JQVA466JCC2G49XC7TSGKZD6D0 の盲検 A): 進捗の handler は f を包んで終わった件だけを数え、失敗した件・When で
落ちた件・前の段から持ち越した件を数えられなかった — 件の結末を知るのは Traverse の handler だけだった。

契約: sequential・parallel・parallel_fail_fast は、件の結末(ok・failed・skipped・carried)が決まるたびに
ItemOutcome を出す。出す所は Traverse と handler の間の handler(件の Program に付け直す内側の handler)の下で、
受け手が無ければ handler 自身が None で答える。内側の handler のどれも効果の引数の注記に ItemOutcome を名指さなければ
出さない(出す費用は件を走らせる費用ほどあり、誰も数えない Traverse は払わない)。progress_bars はそれを数える。
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
    """1 段目の件: 3 は失敗、5 は When で落ちる(Skip)、他は 10 倍。"""
    if x == 3:
        raise ValueError("three")
    if x == 5:
        yield Skip()
    return x * 10


@do
def _second(v: int):
    """2 段目の件: 1 段目の有効な件に 1 を足す(1 段目で失敗・skipped の件は持ち越す)。"""
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
    """Traverse 1 回だけの Program(段 1 つの検の本体)。"""
    return (yield Traverse(f, items, label=label))


class _Reports:
    """ItemOutcome を名指す検の中の受け手 — 受けた順に持ち、外へ渡す(外の progress_bars にも届き、誰も受けなければ
    Traverse の handler が None で答える)。"""

    def __init__(self) -> None:
        self._mut_seen: tuple[ItemOutcome, ...] = ()

    @property
    def seen(self) -> tuple[ItemOutcome, ...]:
        """受けた知らせ(受けた順)。"""
        return self._mut_seen

    def handler(self) -> ProgramHandler:
        @do
        def watch(effect: ItemOutcome, k):
            if isinstance(effect, ItemOutcome):
                self._mut_seen = (*self._mut_seen, effect)
            yield Pass(effect, k)

        return _program_handler(watch)

    def runs(self) -> list[tuple[ItemOutcome, ...]]:
        """Traverse の 1 回ごとの知らせ(初めて知らせた順・中は件の番号の順)。"""
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


def _run_under(strategy: ProgramHandler, inside: list[ProgramHandler], program: Program[_T]) -> _T:
    """program を inside(内側が先)の下、Traverse の handler strategy の下で走らせる。"""
    body: Program[_T] = program
    for install in [*inside, fail_handler, try_handler]:
        body = install(body)
    return run(scheduled(strategy(body)))


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_progress_bars_close_at_total_when_items_fail(name: str) -> None:
    """受入 c6eeb01ac: 失敗・skipped・持ち越しの件がある pipeline でも、tqdm の bar が n == total で閉じる。"""
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
        # 知らせた件は、Traverse が答える Collection の件そのもの(値・失敗の印・履歴)
        assert [(r.item.index, r.item.failed, r.item.value, r.item.history) for r in run_] == [
            (item.index, item.failed, item.value, item.history) for item in answered.all_items
        ]


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
def test_items_carried_before_the_first_run_item_are_reported_too(name: str) -> None:
    """2 段目の頭の件は 1 段目で失敗した持ち越し — 内側の handler を読む前の件も、読んだ後に知らせて bar は閉じる。"""
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
    """件 0 の Traverse と持ち越しだけの Traverse は何も知らせず、bar も開かない(sequential は内側の handler を読まない — #2958)。"""
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
    """件 3 で止まると、それまでの件と失敗した件に続き、止めた件 4 を cancelled と知らせる(card ki-e0fd319d5b75)。"""
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
    """件の Program が出す Traverse も、付け直した内側の handler の下で知らせる — 1 回ごとに別の traverse。"""
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
    """iterable から件を引いた所と、件を走らせた所を、起きた順に持つ。"""

    def __init__(self) -> None:
        self._mut_events: tuple[str, ...] = ()

    @property
    def events(self) -> tuple[str, ...]:
        """引いた所と走らせた所(起きた順)。"""
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
    """sequential は長さの無い iterable を件ごとに引く。知らせる時だけ、total のために最初の件で残りを読み切る。"""
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
    """名指す受け手が内側に無ければ出さない: 外側の受け手には届かず、何でも受ける内側の handler にも ItemOutcome は来ない。"""
    outside = _Reports()
    seen_inside: tuple[type, ...] = ()

    @do
    def any_effect(effect, k):  # 注記なし = どの効果も受ける
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
    """名指すが受けずに Pass する handler の下でも、Traverse の handler が答えて走り終わる。"""

    @do
    def passes(effect: ItemOutcome, k):
        yield Pass(effect, k)

    _, second = _run_under(
        _STRATEGIES[name](), [_program_handler(passes)], _pipeline([1, 2, 3, 4, 5, 6])
    )
    assert isinstance(second, Collection)
    assert second.valid_values == [11, 21, 41, 61]


# 受け手の決まり(設計検証 lt-H07C2RG4T2BM59EENGQWQHSJKH の盲検 A・B): 受け手は知らせを見て外へ渡し、答えるのは Traverse の
# handler だけ。A — 答えて止める受け手が 1 つ居ると、並び次第で別の受け手(2 本目の bar・失敗の記録)が黙って何も受けなかった。
# B — 知らせに例外を投げ返す受け手(失敗の上限で Traverse を止める)が、Traverse の走り方の判断を受け手へ移したまま検を通った。


@pytest.mark.parametrize("name", sorted(_STRATEGIES))
@pytest.mark.parametrize("order", ["bars-inside", "bars-outside"])
def test_consumers_compose_in_any_order(name: str, order: str) -> None:
    """progress_bars 2 本と記録係は、どの並びでも全部の知らせを受ける(どれも渡すので)。"""
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
    """知らせに答えて止める受け手は、外の受け手から知らせを隠す — Traverse の handler が ItemOutcomeNotPassedError で断る。"""

    @do
    def answers(effect: ItemOutcome, k):
        return (yield Resume(k, None))

    with pytest.raises(ItemOutcomeNotPassedError) as raised:
        _run_under(_STRATEGIES[name](), [_program_handler(answers)], _pipeline([1, 2, 3, 4, 5, 6]))
    assert raised.value.cause is None


class _BudgetError(RuntimeError):
    """受け手が投げ返す例外(Traverse を止めようとする受け手の形)。"""


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
    """知らせに投げ返す受け手は Traverse を止める判断を握る — Traverse の handler が ItemOutcomeNotPassedError で断り、投げた物を cause に持つ。"""
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
    """入れ子の Traverse で受け手が投げ返しても、外の Traverse はそれを外の件の失敗として Collection に飲み込まない
    (for/do の From を 2 つ重ねると、外の件ごとに内の Traverse が出る — 飲み込むと契約の誤りが黙る)。"""
    with pytest.raises(ItemOutcomeNotPassedError) as raised:
        _run_under(
            strategy(),
            [_program_handler(_throws_back)],
            _traversed(_nested_failing, [1, 2], label="outer"),
        )
    assert raised.value.report.label == "inner"
