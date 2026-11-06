"""Test of the effect argument type annotations of the simulated clock handler.

The VM reads the effect argument type annotations of a handler at install time (doeff_vm._effect_types, SPEC-WITHHANDLER-TYPE-FILTER), and for effects outside the types
passes them outward without entering Python for that handler. The simulated clock `handle` only passes effects outside its handled types outward with the final `Pass`, so adding annotations
does not change answers or order; only the number of times the body is entered decreases.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import CodeType, FrameType

from doeff_core_effects import Ask, WriterTellEffect
from doeff_time import (
    DelayEffect,
    GetMonotonicEffect,
    GetTime,
    GetTimeEffect,
    RaceWithinEffect,
    ScheduleAtEffect,
    SetTimeEffect,
    SpendCpuEffect,
    WaitTicksEffect,
    WaitUntilEffect,
    WaitWithinEffect,
    sim_time_handler,
)
from doeff_time._handlers.sim_time import SimTimeRuntime
from time_test_support import run_with_handlers, sim_time

from doeff import do, handler_effect_types


def _raw(installed: object) -> Callable[..., object]:
    """The handler function the VM calls, wrapped by the value of a Program -> Program handler (the field doeff.program.handler places)."""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — field placed by doeff.program.handler (an untyped field)


@dataclass(frozen=True)
class Entered:
    """Number of times a code function started during run (entered), and the answer of run (result)."""

    entered: int
    result: object


def _entries(code: CodeType, run: Callable[[], object]) -> Entered:
    """Number of times a code function started during run, and the answer of run. The body is a generator, so a call event also occurs at each resumption -- count by frame identity."""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is code:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run()
    finally:
        sys.setprofile(None)
    return Entered(entered=len(frames), result=result)


@do
def _asks_then_time():
    a = yield Ask("a")
    b = yield Ask("b")
    c = yield Ask("c")
    now = yield GetTime()
    return a + b + c, now


_HANDLE = SimTimeRuntime.handle.__wrapped__.__code__  # type: ignore[attr-defined] — field placed by functools.wraps of @do


def test_the_clock_declares_the_effects_it_answers() -> None:
    assert handler_effect_types(_raw(sim_time_handler())) == (
        WriterTellEffect,
        DelayEffect,
        WaitUntilEffect,
        GetTimeEffect,
        GetMonotonicEffect,
        ScheduleAtEffect,
        SetTimeEffect,
        SpendCpuEffect,
        WaitWithinEffect,
        WaitTicksEffect,
        RaceWithinEffect,
    )


def test_an_effect_outside_the_types_does_not_enter_the_clock() -> None:
    # The clock is innermost: Ask skips the clock and reaches the outer reader
    seen = _entries(
        _HANDLE,
        lambda: run_with_handlers(
            sim_time_handler(start_time=sim_time(7.0))(_asks_then_time()), env={"a": 1, "b": 2, "c": 3}
        ),
    )
    assert seen.result == (6, sim_time(7.0))
    assert seen.entered == 1, "the clock body is entered only once, for GetTime (the 3 Ask calls are skipped)"
