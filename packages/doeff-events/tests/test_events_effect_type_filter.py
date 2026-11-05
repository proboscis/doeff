"""Check of the type annotation on the handler's effect argument for event handlers.

The VM reads the type annotation on the handler's effect argument at install time (doeff_vm._effect_types, SPEC-WITHHANDLER-TYPE-FILTER) and, for effects outside the type,
passes the effect outward without entering that handler in Python. The two memory handlers (event_handler, subscribed_event_handler) answer only Publish, WaitForEvent and
WaitForEvents and merely pass everything else outward with Pass, so narrowing to those types by annotation does not change answers or order, only the number of times the body is entered (while the annotation was EffectBase,
all effects of the worker's body passed through this handler via Pass, and a single agora turn scenario used 7,000-11,000 steps).
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import CodeType, FrameType

from doeff_core_effects import Ask, reader
from doeff_events import (
    EventBus,
    Publish,
    PublishEffect,
    WaitForEventEffect,
    WaitForEventsEffect,
    event_handler,
    subscribed_event_handler,
)
from events_test_support import run_scheduled

from doeff import do, handler_effect_types


@dataclass(frozen=True)
class Changed:
    """A signal used only inside the check."""

    key: str


def _raw(installed: object) -> Callable[..., object]:
    """The handler function the VM calls, wrapped in the value of the Program -> Program handler (the field doeff.program.handler places)."""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — field placed by doeff.program.handler (an untyped field)


@dataclass(frozen=True)
class Entered:
    """The number of times a code function started during the run (entered) and the run's answer (result)."""

    entered: int
    result: object


def _entries(code: CodeType, run: Callable[[], object]) -> Entered:
    """The number of times a code function started during the run and the run's answer. The body is a generator, so call events also occur on each resumption — count by frame identity."""
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
def _asks_then_publish():
    a = yield Ask("a")
    b = yield Ask("b")
    c = yield Ask("c")
    yield Publish(Changed("x"))
    return a + b + c


def _subscriber_code() -> CodeType:
    """The code of the handler function that subscribed_event_handler builds (even if each closure is a different function, the code is one)."""
    return _raw(subscribed_event_handler(EventBus(), "code", (Changed,))).__wrapped__.__code__  # type: ignore[attr-defined] — field placed by functools.wraps of @do


def test_both_memory_handlers_declare_the_effects_they_answer() -> None:
    assert handler_effect_types(_raw(subscribed_event_handler(EventBus(), "s", (Changed,)))) == (
        WaitForEventEffect,
        WaitForEventsEffect,
        PublishEffect,
    )
    assert handler_effect_types(_raw(event_handler())) == (WaitForEventEffect, WaitForEventsEffect, PublishEffect)


def test_an_effect_outside_the_types_does_not_enter_the_subscriber() -> None:
    # The subscriber's handler is inner: Ask skips the subscriber's handler and reaches the outer reader; only Publish enters the body, once
    seen = _entries(
        _subscriber_code(),
        lambda: run_scheduled(
            reader({"a": 1, "b": 2, "c": 3})(subscribed_event_handler(EventBus(), "s", (Changed,))(_asks_then_publish()))
        ),
    )
    assert seen.result == 6
    assert seen.entered == 1, "the subscriber handler's body is entered only once, for Publish (the 3 Ask are skipped)"
