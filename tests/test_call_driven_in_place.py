"""effect を出さずに終わる生成器の ``@do`` の定義の呼びを、VM が親の stream の中でその場で回す(agora-redesign #2816)。

親の生成器が、本体が yield する定義の ``Call`` を yield すると、VM の stream が子の生成器をその場で作って
``PyIter_Send`` で回し、返った値を親へ送り直す(VM の歩を使わない)。子が更に ``Call`` を yield すれば明示の
stack に積む。子のどれかが ``Call`` 以外(effect など)を yield した・例外を上げた時は、回し始めた生成器の鎖を
「最初の答えを持った stream」として VM へ渡す — 枠の順(親 → 子 → 孫)と、例外の時の doeff の traceback は
VM が全部を回していた時と同じ。``open_bind``(``<-``)と ``Call`` の形は変えない(ADR-DOE-CORE-EFFECTS-003 R5)。
"""

import itertools
from collections.abc import Generator
from dataclasses import dataclass

import pytest
from doeff_vm import PyVM

from doeff import EffectBase, Resume, do, run, with_handlers


class Probe(EffectBase[int]):
    pass


class BoomError(Exception):
    pass


@do
def count_down(n: int) -> Generator[object, int, int]:
    if n == 0:
        return 0
    rest = yield count_down(n - 1)
    return rest + 1


@do
def raises_at(n: int) -> Generator[object, int, int]:
    if n == 0:
        raise BoomError("bottom")
    return (yield raises_at(n - 1))


@do
def answer_probe(effect: Probe, k: object) -> Generator[object, object, object]:
    return (yield Resume(k, 100))


@dataclass(frozen=True)
class CountedRun:
    steps: int
    value: object


def _counted_run(program: object) -> CountedRun:
    vm = PyVM()
    value = vm.run(program)
    return CountedRun(steps=vm.step_count(), value=value)


def _frame_names(error: BaseException) -> list[str]:
    # VM が例外に置く doeff の traceback(``["frame", 限定名, file, 行]`` の列)— 型の無い属性なので __dict__ から読む。
    # 名は限定名の最後の部分(検の中の関数は ``<検の名>.<locals>.parent``)。
    return [
        entry[1].rsplit(".", 1)[-1]
        for entry in vars(error)["__doeff_traceback__"]
        if entry[0] == "frame"
    ]


def test_a_recursion_that_performs_no_effect_takes_the_same_vm_steps_at_any_depth() -> None:
    shallow = _counted_run(count_down(10))
    deep = _counted_run(count_down(20))
    assert (shallow.value, deep.value) == (10, 20)
    assert deep.steps == shallow.steps


def test_a_deep_recursion_runs_past_the_in_place_chain_limit() -> None:
    assert run(count_down(5000)) == 5000


def test_an_effect_at_the_bottom_is_answered_and_each_level_runs_once() -> None:
    entries = itertools.count()

    @do
    def down(n: int) -> Generator[object, int, int]:
        next(entries)  # 段に入った数 — その場で回し始めた生成器を VM が 2 度回せば増える
        if n == 0:
            return (yield Probe())
        rest = yield down(n - 1)
        return rest + 1

    assert run(with_handlers([answer_probe], down(5))) == 105
    assert next(entries) == 6


def test_an_exception_of_an_in_place_callee_is_caught_where_the_caller_yields() -> None:
    @do
    def parent() -> Generator[object, object, object]:
        try:
            yield raises_at(3)
        except BoomError as error:
            return ("caught", str(error))
        return "not caught"

    assert run(parent()) == ("caught", "bottom")


def test_an_uncaught_exception_of_an_in_place_callee_keeps_every_frame() -> None:
    @do
    def parent() -> Generator[object, object, object]:
        return (yield raises_at(2))

    with pytest.raises(BoomError) as info:
        run(parent())
    # VM が全部を回していた時(#2801 の後)と同じ列 — 続けて同じ関数の枠は VM が畳む
    assert _frame_names(info.value) == ["parent", "raises_at", "raises_at"]


def test_an_exception_after_an_effect_keeps_the_frames_in_call_order() -> None:
    @do
    def grandchild() -> Generator[object, object, None]:
        yield Probe()
        raise BoomError("late")

    @do
    def child() -> Generator[object, object, None]:
        return (yield grandchild())

    @do
    def parent() -> Generator[object, object, None]:
        return (yield child())

    with pytest.raises(BoomError) as info:
        run(with_handlers([answer_probe], parent()))
    assert _frame_names(info.value) == ["parent", "child", "grandchild"]
