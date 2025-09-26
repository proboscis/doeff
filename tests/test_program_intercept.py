"""Tests for Program.intercept handling nested Programs and Effects."""

from __future__ import annotations

import pytest

from dataclasses import dataclass
from typing import Callable, Sequence

from doeff import (
    EffectGenerator,
    ExecutionContext,
    Log,
    Program,
    ProgramInterpreter,
    Ask,
    Listen,
    Gather,
    Get,
    Local,
    Put,
    Safe,
    Fail,
    do,
)
from doeff.effects.reader import AskEffect, LocalEffect
from doeff.effects.state import StateGetEffect, StatePutEffect
from doeff.effects.writer import WriterListenEffect, WriterTellEffect
from doeff.effects.result import ResultSafeEffect
from doeff.types import EffectBase, Effect


@dataclass(frozen=True)
class InterceptCase:
    name: str
    build_program: Callable[[], Program]
    build_context: Callable[[], ExecutionContext | None]
    expected: Sequence[type[EffectBase]]


def _build_ask_program() -> Program:
    @do
    def _program() -> EffectGenerator[str]:
        yield Ask("key")
        yield Log("after ask")
        return "ok"

    return _program()


def _build_get_program() -> Program:
    @do
    def _program() -> EffectGenerator[str]:
        yield Get("value")
        yield Log("after get")
        return "ok"

    return _program()


def _build_put_program() -> Program:
    @do
    def _program() -> EffectGenerator[str]:
        yield Put("value", 1)
        yield Log("after put")
        return "ok"

    return _program()


def _build_local_program() -> Program:
    @do
    def _inner() -> EffectGenerator[None]:
        yield Log("inner log")

    @do
    def _program() -> EffectGenerator[str]:
        yield Local({"scoped": True}, _inner())
        yield Log("outer log")
        return "ok"

    return _program()


def _build_listen_program() -> Program:
    @do
    def _inner() -> EffectGenerator[str]:
        yield Log("inside listen")
        return "done"

    @do
    def _program() -> EffectGenerator[str]:
        yield Listen(_inner())
        yield Log("after listen")
        return "ok"

    return _program()


def _build_safe_program() -> Program:
    @do
    def _program() -> EffectGenerator[str]:
        result = yield Safe(Fail(ValueError("boom")))
        yield Log("after safe")
        return repr(result)

    return _program()


INTERCEPT_CASES: tuple[InterceptCase, ...] = (
    InterceptCase(
        name="ask_with_log",
        build_program=_build_ask_program,
        build_context=lambda: ExecutionContext(env={"key": "value"}),
        expected=(AskEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="get_with_log",
        build_program=_build_get_program,
        build_context=lambda: ExecutionContext(state={"value": 3}),
        expected=(StateGetEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="put_with_log",
        build_program=_build_put_program,
        build_context=lambda: ExecutionContext(state={}),
        expected=(StatePutEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="local_with_inner_log",
        build_program=_build_local_program,
        build_context=lambda: None,
        expected=(LocalEffect, WriterTellEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="listen_with_log",
        build_program=_build_listen_program,
        build_context=lambda: None,
        expected=(WriterListenEffect, WriterTellEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="safe_with_log",
        build_program=_build_safe_program,
        build_context=lambda: None,
        expected=(ResultSafeEffect, WriterTellEffect),
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", INTERCEPT_CASES, ids=lambda case: case.name)
async def test_intercept_effect_with_log_calls(case: InterceptCase) -> None:
    """Each effect combined with Log should trigger the transformer the expected number of times."""

    seen: list[type[EffectBase]] = []

    def transformer(effect: EffectBase) -> EffectBase:
        seen.append(effect.__class__)
        return effect

    program = case.build_program()
    context = case.build_context()
    interpreter = ProgramInterpreter()
    result = await interpreter.run(program.intercept(transformer), context)

    assert result.is_ok
    assert tuple(seen) == case.expected


def _intercept_transform(effect: Effect) -> Effect | Program:
    if isinstance(effect, AskEffect):
        return Program.pure("intercepted")
    return effect


@pytest.mark.asyncio
async def test_intercept_rewrites_local_subprogram():
    """Intercept should transform Ask deep inside Local effect payloads."""

    @do
    def inner_program():
        return (yield Ask("some_key"))

    @do
    def outer_program():
        return (yield Local({}, inner_program()))

    intercepted = outer_program().intercept(_intercept_transform)  # type: ignore[arg-type]

    interpreter = ProgramInterpreter()
    result = await interpreter.run(intercepted)

    assert result.is_ok
    assert result.value == "intercepted"


@pytest.mark.asyncio
async def test_intercept_rewrites_gathered_programs():
    """Intercept should reach Programs stored inside gather effects."""

    @do
    def child_program(index: int):
        return (yield Ask(f"key-{index}"))

    @do
    def gather_program():
        return (yield Gather(child_program(1), child_program(2)))

    intercepted = gather_program().intercept(_intercept_transform)  # type: ignore[arg-type]

    interpreter = ProgramInterpreter()
    result = await interpreter.run(intercepted)

    assert result.is_ok
    assert result.value == ["intercepted", "intercepted"]


@pytest.mark.asyncio
async def test_intercept_visits_each_effect_once():
    """Each effect instance should trigger the transformer exactly once."""

    @do
    def inner_program() -> EffectGenerator[str]:
        yield Log("inner")
        return "inner-result"

    @do
    def outer_program() -> EffectGenerator[str]:
        yield Log("outer start")
        yield Local({"config": "scoped"}, inner_program())
        yield Log("outer end")
        return "done"

    call_counts: dict[int, int] = {}

    def transformer(effect: Effect) -> Effect:
        key = id(effect)
        call_counts[key] = call_counts.get(key, 0) + 1
        return effect

    interpreter = ProgramInterpreter()
    result = await interpreter.run(outer_program().intercept(transformer))

    assert result.is_ok
    assert result.value == "done"
    assert all(count == 1 for count in call_counts.values())
