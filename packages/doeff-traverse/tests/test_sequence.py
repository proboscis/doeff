"""sequence() の契約の検。

sequence は Program の列を渡された順に 1 つずつ走らせ、答えを tuple で返す。handler は要らず、方針は「順・最初の失敗で止まる」に
固定する(Traverse の handler が決める並び・失敗の扱いとは別の、名前のある道具)。確かめる事は 3 つ。

- 契約: 入力順・重複・None を保つ / 空は () / 最初の例外がそのまま上がり後ろの Program は始まらない / Program は走らせる直前に
  作られる(generator を渡せる)/ 呼び手の handler の下で走る / 同じ Program を 2 度走らせても蓄積を共有しない。
- 失敗を落とさない: Traverse の sequential は失敗した件を記録して残りを読み続けるが、sequence は止まる(起動の読み直しが
  「読めない行があれば止まる」を保つために要る)。
- 線形: 行の数が 10 倍になっても費用は約 10 倍(tuple の継ぎ足しは約 100 倍)。

行の読みには 2 つの形がある。effect を出さない @do(本体に yield が無い — defk の判断)は束ねる所でその場で呼ばれ、effect を出す
@do(本体に yield が在る)は VM が走らせる。契約の検は両方で通す(どちらの形でも同じ答え・同じ止まり方)。
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
    """effect を出さない行の読み(束ねる所でその場で呼ばれる形)。"""

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
    """effect を出す行の読み(VM が走らせる形)。"""

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
    # 3 は作られも走らせられもしない
    assert events == ["make 1", "run 1", "make 2", "run 2"]


@_ROW_FORMS
def test_a_failing_row_is_not_dropped_as_it_is_under_traverse_sequential(
    make_row: Callable[..., Program[int, object]],
) -> None:
    """Traverse の sequential は失敗した件を記録して残りを読み続け、valid_values だけを読む呼び手には失敗が見えない。"""
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
    """rows 行を 3 回走らせた CPU 時間の最小(負荷の揺れを小さくする)。"""
    return min(_cpu_seconds_once(make_and_run, rows) for _ in range(3))


def _cpu_seconds_once(make_and_run: Callable[[int], object], rows: int) -> float:
    started = time.process_time()
    make_and_run(rows)
    return time.process_time() - started


@_ROW_FORMS
def test_the_cost_grows_in_proportion_to_the_rows(
    make_row: Callable[..., Program[int, object]],
) -> None:
    """3 千行 → 3 万行(10 倍)で、費用は約 10 倍。tuple の継ぎ足しなら約 100 倍になる(測り: 3 千行 35 ms → 3 万行 3.5 秒)。

    測りは負荷で揺れるので、線形(約 10 倍)と 2 乗(約 100 倍)の間の 30 倍を境にする。
    """

    def run_rows(rows: int) -> object:
        sink: list[str] = []
        return run(sequence(make_row(index, sink) for index in range(rows)))

    run_rows(300)  # 最初の 1 回の準備(import・bytecode)を測りに入れない
    small = _cpu_seconds(run_rows, 3_000)
    large = _cpu_seconds(run_rows, 30_000)
    assert large < small * 30, (small, large)
