"""模擬の時計の handler の effect の引数の型の註の検(agora-redesign #2008)。

VM は handler の effect の引数の型の註を install の時に読み(doeff_vm._effect_types・SPEC-WITHHANDLER-TYPE-FILTER)、型の外の effect では
その handler を Python に入らずに外へ渡す。模擬の時計の `handle` は、扱う型の外の effect を最後の `Pass` で外へ渡すだけなので、註を足しても
答えと順は変わらず、本体に入る回数だけが減る。
"""

import sys
from collections.abc import Callable
from types import CodeType, FrameType

from doeff_core_effects import Ask, WriterTellEffect
from doeff_time import GetTime, sim_time_handler
from doeff_time.effects import (
    DelayEffect,
    GetMonotonicEffect,
    GetTimeEffect,
    ScheduleAtEffect,
    SetTimeEffect,
    WaitUntilEffect,
)
from doeff_time.handlers.sim_time import SimTimeRuntime
from doeff_vm._effect_types import handler_effect_types
from time_test_support import run_with_handlers, sim_time

from doeff import do


def _raw(installed: object) -> Callable[..., object]:
    """Program -> Program の handler の値が包む、VM が呼ぶ handler の関数(doeff.program.handler が置く欄)。"""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — doeff.program.handler が置く欄(型の無い欄)


def _entries(code: CodeType, run: Callable[[], object]) -> tuple[int, object]:
    """run の間に code の関数が始まった回数と、run の答え。本体は generator なので再開ごとにも call の事象が出る — frame の同一性で数える。"""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is code:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run()
    finally:
        sys.setprofile(None)
    return len(frames), result


@do
def _asks_then_time():
    a = yield Ask("a")
    b = yield Ask("b")
    c = yield Ask("c")
    now = yield GetTime()
    return a + b + c, now


_HANDLE = SimTimeRuntime.handle.__wrapped__.__code__  # type: ignore[attr-defined] — @do の functools.wraps が置く欄


def test_the_clock_declares_the_effects_it_answers() -> None:
    assert handler_effect_types(_raw(sim_time_handler())) == (
        WriterTellEffect,
        DelayEffect,
        WaitUntilEffect,
        GetTimeEffect,
        GetMonotonicEffect,
        ScheduleAtEffect,
        SetTimeEffect,
    )


def test_an_effect_outside_the_types_does_not_enter_the_clock() -> None:
    # 時計は一番内側: Ask は時計を飛ばして外の reader に届く
    entered, result = _entries(
        _HANDLE,
        lambda: run_with_handlers(
            sim_time_handler(start_time=sim_time(7.0))(_asks_then_time()), env={"a": 1, "b": 2, "c": 3}
        ),
    )
    assert result == (6, sim_time(7.0))
    assert entered == 1, "時計の本体に入るのは GetTime の 1 回だけ(Ask 3 回は飛ばす)"
