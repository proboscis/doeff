"""Check of the effect-argument type annotations of the doeff-core-effects handlers.

The VM reads the handler's effect-argument type annotation at install time (doeff_vm._effect_types, SPEC-WITHHANDLER-TYPE-FILTER), and for an effect outside the type
passes it outward without entering Python for that handler. Every core-effects handler only passes an effect outside the types it handles outward with `Pass` first, so
adding the annotation changes neither the answer nor the order; only the number of times the handler body is entered decreases.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import CodeType, FrameType

import pytest
from doeff_core_effects import (
    Ask,
    Await,
    Get,
    Listen,
    Local,
    Put,
    ReadWriterLog,
    Slog,
    Try,
    WriterTellEffect,
    await_handler,
    env_var_ask,
    lazy_ask,
    listen_handler,
    local_handler,
    reader,
    slog_discard_handler,
    slog_handler,
    state,
    try_handler,
    vm_probe_handler,
    writer,
)
from doeff_core_effects import (
    _cache_handlers as cache_handlers,
)
from doeff_vm._effect_types import handler_effect_types

from doeff import Pure, ReadVmWorkCounts, do, handler, with_handlers
from doeff import run as doeff_run


def _raw(installed: object) -> Callable[..., object]:
    """The handler function the VM calls, wrapped by the value of a Program -> Program handler (the field doeff.program.handler places)."""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] -- the field doeff.program.handler places (an untyped field)


@pytest.mark.parametrize(
    ("installed", "types"),
    [
        (reader({}), (Ask,)),
        (state(), (Get, Put)),
        (writer, (WriterTellEffect, ReadWriterLog)),
        (try_handler, (Try,)),
        (slog_handler, (Slog,)),
        (slog_discard_handler, (Slog,)),
        (local_handler, (Local,)),
        (listen_handler, (Listen,)),
        (await_handler, (Await,)),
        (lazy_ask({}), (Ask, Local)),
        (env_var_ask(), (Ask,)),
        (
            cache_handlers.in_memory_cache_handler(),
            (
                cache_handlers.CacheGetEffect,
                cache_handlers.CacheExistsEffect,
                cache_handlers.CachePutEffect,
                cache_handlers.CacheDeleteEffect,
            ),
        ),
    ],
)
def test_each_handler_declares_the_types_it_answers(installed: object, types: tuple[type, ...]) -> None:
    assert handler_effect_types(_raw(installed)) == types


@dataclass(frozen=True)
class _Entries:
    """The number of times the code function started during the run (entered) and the answer of the run (result)."""

    entered: int
    result: object


def _entries(code: CodeType, run: Callable[[], object]) -> _Entries:
    """The number of times the code function started during the run, and the answer of the run. The body is a generator, so a call event also fires on each resumption -- count by frame identity."""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is code:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run()
    finally:
        sys.setprofile(None)
    return _Entries(entered=len(frames), result=result)


@do
def _asks_then_state():
    a = yield Ask("a")
    b = yield Ask("b")
    c = yield Ask("c")
    yield Put("n", a + b + c)
    return (yield Get("n"))


def test_an_effect_outside_the_types_does_not_enter_the_handler() -> None:
    # The last in the list is the innermost: Ask skips state and reaches the outer reader. state is a Rust handler inside the VM
    # with no Python body, so count by the number of times the VM called a handler.
    stack = [reader({"a": 1, "b": 2, "c": 3}), state()]
    result, calls = _handler_calls_of(with_handlers(stack, _asks_then_state()))
    _, reading = _handler_calls_of(Pure(None))  # the part where the later reading effect calls vm_probe_handler
    assert result == 6
    assert calls - reading == 5, "handlers are called only for the reader's 3 Asks and state's Put and Get (2 times) (Ask skips state)"


def _handler_calls_of(program: object) -> tuple[object, int]:
    """The answer of the program, and the number of times the VM called a handler while it ran (the difference of ReadVmWorkCounts before and after -- including the 1 call of the later reading)."""

    @do
    def counted():
        _, before = yield ReadVmWorkCounts()
        value = yield program
        _, after = yield ReadVmWorkCounts()
        return value, after - before

    return doeff_run(vm_probe_handler(counted()))


def test_the_answers_and_the_types_it_answers_are_unchanged() -> None:
    # The handled types are delivered as before; a plain handler with no type annotation receives every effect.
    @do
    def passes_everything(effect, k):  # a plain handler with no type annotation
        from doeff import Pass

        yield Pass(effect, k)

    stack = [reader({"a": 1, "b": 2, "c": 3}), state(), handler(passes_everything)]
    code = passes_everything.__wrapped__.__code__  # type: ignore[attr-defined] -- the field @do's functools.wraps places
    seen = _entries(code, lambda: doeff_run(with_handlers(stack, _asks_then_state())))
    assert seen.result == 6
    assert seen.entered == 5, "a handler with no type annotation receives all 5: Ask 3, Put, Get"
