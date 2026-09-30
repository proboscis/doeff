"""doeff-traverse の handler の effect の引数の型の註の検(agora-redesign #2008)。

VM は handler の effect の引数の型の註を install の時に読み(doeff_vm._effect_types・SPEC-WITHHANDLER-TYPE-FILTER)、型の外の effect では
その handler を Python に入らずに外へ渡す。traverse の handler は、扱う型の外の effect を最後の `Pass` で外へ渡すだけなので、註を足しても
答えと順は変わらず、本体に入る回数だけが減る。
"""

import sys
from collections.abc import Callable
from types import CodeType, FrameType

import pytest
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader
from doeff_core_effects.scheduler import scheduled
from doeff_traverse.effects import Fail, Inspect, Reduce, Skip, SortBy, Take, Traverse, Zip
from doeff_traverse.handlers import fail_handler, normalize_to_none, parallel, parallel_fail_fast, sequential
from doeff_vm._effect_types import handler_effect_types

from doeff import do, run, with_handlers

COLLECTION = (Skip, Traverse, Reduce, Zip, Inspect, SortBy, Take)


def _raw(installed: object) -> Callable[..., object]:
    """Program -> Program の handler の値が包む、VM が呼ぶ handler の関数(doeff.program.handler が置く欄)。"""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — doeff.program.handler が置く欄(型の無い欄)


def _entries(code: CodeType, run_it: Callable[[], object]) -> tuple[int, object]:
    """run_it の間に code の関数が始まった回数と、その答え。本体は generator なので再開ごとにも call の事象が出る — frame の同一性で数える。"""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is code:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run_it()
    finally:
        sys.setprofile(None)
    return len(frames), result


@pytest.mark.parametrize(
    ("installed", "types"),
    [
        (sequential(), COLLECTION),
        (parallel(concurrency=2), COLLECTION),
        (parallel_fail_fast(concurrency=2), COLLECTION),
        (fail_handler, (Fail,)),
        (normalize_to_none, (Fail,)),
    ],
)
def test_each_handler_declares_the_types_it_answers(installed: object, types: tuple[type, ...]) -> None:
    assert handler_effect_types(_raw(installed)) == types


@do
def _double(x: int):
    return x * 2


@do
def _asks_then_traverse():
    a = yield Ask("a")
    b = yield Ask("b")
    collection = yield Traverse(_double, [a, b])
    return (yield Inspect(collection))


def test_an_effect_outside_the_types_does_not_enter_the_handler() -> None:
    # traverse の handler は一番内側: Ask は飛ばして外の reader に届く
    handler = sequential()
    body = _raw(handler).__wrapped__.__code__  # type: ignore[attr-defined] — @do の functools.wraps が置く欄
    entered, result = _entries(
        body, lambda: run(scheduled(with_handlers([reader({"a": 1, "b": 2}), handler], _asks_then_traverse())))
    )
    assert [item.value for item in result] == [2, 4]
    assert entered == 2, "traverse の handler の本体に入るのは Traverse と Inspect の 2 回だけ(Ask 2 回は飛ばす)"
