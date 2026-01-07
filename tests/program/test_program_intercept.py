"""Tests for Program.intercept handling nested Programs and Effects."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pytest

from doeff import (
    Ask,
    CaptureGraph,
    Catch,
    EffectGenerator,
    ExecutionContext,
    Fail,
    Finally,
    FirstSuccess,
    Gather,
    Get,
    Listen,
    Local,
    Log,
    Program,
    ProgramInterpreter,
    Put,
    Recover,
    Retry,
    Safe,
    do,
)
from doeff.effects.gather import GatherEffect
from doeff.effects.graph import GraphCaptureEffect
from doeff.effects.reader import AskEffect, LocalEffect
from doeff.effects.result import (
    ResultCatchEffect,
    ResultFailEffect,
    ResultFinallyEffect,
    ResultFirstSuccessEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    ResultSafeEffect,
)
from doeff.effects.state import StateGetEffect, StatePutEffect
from doeff.effects.writer import WriterListenEffect, WriterTellEffect
from doeff.program import KleisliProgramCall
from doeff.types import Effect, EffectBase, RunResult


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
        @do
        def risky() -> EffectGenerator[None]:
            yield Log("inside risky")
            yield Fail(ValueError("boom"))

        result = yield Safe(risky())
        yield Log("after safe")
        return repr(result)

    return _program()


def _build_catch_program() -> Program:
    @do
    def risky() -> EffectGenerator[str]:
        yield Log("before fail")
        yield Fail(ValueError("boom"))

    @do
    def handler(exc: Exception) -> EffectGenerator[str]:
        yield Log(f"handled {exc}")
        return "ok"

    @do
    def _program() -> EffectGenerator[str]:
        yield Catch(risky(), handler)  # type: ignore[name-defined]
        yield Log("after catch")
        return "done"

    return _program()


def _build_gather_program() -> Program:
    @do
    def child(index: int) -> EffectGenerator[int]:
        yield Log(f"child {index}")
        return index

    @do
    def _program() -> EffectGenerator[str]:
        yield Gather(child(1), child(2))
        yield Log("after gather")
        return "done"

    return _program()


def _build_capture_program() -> Program:
    @do
    def inner() -> EffectGenerator[None]:
        yield Log("inside capture")

    @do
    def _program() -> EffectGenerator[str]:
        yield CaptureGraph(inner())
        yield Log("after capture")
        return "done"

    return _program()


def _build_recover_program() -> Program:
    @do
    def fallback() -> EffectGenerator[str]:
        yield Log("fallback log")
        return "fallback"

    @do
    def _program() -> EffectGenerator[str]:
        yield Recover(Fail(ValueError("boom")), fallback())
        yield Log("after recover")
        return "done"

    return _program()


def _build_finally_program() -> Program:
    @do
    def sub() -> EffectGenerator[str]:
        yield Log("sub log")
        return "sub value"

    @do
    def finalizer() -> EffectGenerator[None]:
        yield Log("finalizer log")
        return None

    @do
    def _program() -> EffectGenerator[str]:
        result = yield Finally(sub(), finalizer())
        yield Log("after finally")
        return result

    return _program()


def _build_retry_program() -> Program:
    attempts: list[int] = []

    @do
    def risky() -> EffectGenerator[None]:
        attempt = len(attempts) + 1
        attempts.append(attempt)
        yield Log(f"attempt {attempt}")
        if attempt == 1:
            yield Fail(ValueError("boom"))
        return None

    @do
    def _program() -> EffectGenerator[str]:
        yield Retry(risky(), max_attempts=2, delay_ms=0)
        yield Log("after retry")
        return "done"

    return _program()


def _build_first_success_program() -> Program:
    @do
    def fail() -> EffectGenerator[str]:
        yield Log("first fail")
        raise ValueError("fail")

    @do
    def succeed() -> EffectGenerator[str]:
        yield Log("success log")
        return "success"

    @do
    def _program() -> EffectGenerator[str]:
        value = yield FirstSuccess(fail(), succeed())
        yield Log("after first success")
        return value

    return _program()


def _build_local_program_with_program_list() -> Program:
    @do
    def context_program() -> EffectGenerator[list[str]]:
        entries = yield Program.list([Log("context log")])
        return entries

    @do
    def inner_program(execution_context: list[str]) -> EffectGenerator[str]:
        yield Log("segment start")
        yield Log(f"context entries: {execution_context}")
        yield Log("segment end")
        return "segmented"

    @do
    def _program() -> EffectGenerator[str]:
        entries = yield context_program()
        yield Local({"ctx": entries}, inner_program(entries))
        yield Log("after local with program list")
        return "done"

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
        expected=(ResultSafeEffect, WriterTellEffect, ResultFailEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="catch_with_log",
        build_program=_build_catch_program,
        build_context=lambda: None,
        expected=(ResultCatchEffect, WriterTellEffect, ResultFailEffect, WriterTellEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="gather_with_log_children",
        build_program=_build_gather_program,
        build_context=lambda: None,
        expected=(GatherEffect, WriterTellEffect, WriterTellEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="capture_graph_with_log",
        build_program=_build_capture_program,
        build_context=lambda: None,
        expected=(GraphCaptureEffect, WriterTellEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="recover_with_log",
        build_program=_build_recover_program,
        build_context=lambda: None,
        expected=(ResultRecoverEffect, WriterTellEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="finally_with_log",
        build_program=_build_finally_program,
        build_context=lambda: None,
        expected=(ResultFinallyEffect, WriterTellEffect, WriterTellEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="retry_with_log",
        build_program=_build_retry_program,
        build_context=lambda: None,
        expected=(ResultRetryEffect, WriterTellEffect, ResultFailEffect, WriterTellEffect, WriterTellEffect),
    ),
    InterceptCase(
        name="first_success_with_log",
        build_program=_build_first_success_program,
        build_context=lambda: None,
        expected=(ResultFirstSuccessEffect, WriterTellEffect, WriterTellEffect, WriterTellEffect),
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
    result = await interpreter.run_async(program.intercept(transformer), context)

    assert result.is_ok
    assert tuple(seen) == case.expected


def _intercept_transform(effect: Effect) -> Effect | Program:
    if isinstance(effect, AskEffect):
        from doeff.effects.writer import Log as WriterLog

        @do
        def replacement() -> EffectGenerator[Effect]:
            yield WriterLog("ask intercepted")
            return effect

        return replacement()
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
    context = ExecutionContext(env={"some_key": "intercepted"})
    result = await interpreter.run_async(intercepted, context)

    assert result.is_ok
    assert "ask intercepted" in result.log
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
    context = ExecutionContext(env={"key-1": "intercepted", "key-2": "intercepted"})
    result = await interpreter.run_async(intercepted, context)

    assert result.is_ok
    assert result.value == ["intercepted", "intercepted"]
    assert result.log.count("ask intercepted") == 2


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
    result = await interpreter.run_async(outer_program().intercept(transformer))

    assert result.is_ok
    assert result.value == "done"
    assert all(count == 1 for count in call_counts.values())


@pytest.mark.asyncio
async def test_intercept_multiple_layers_single_application():
    """Stacked intercept calls still visit each effect once per transformer."""

    @do
    def simple_program() -> EffectGenerator[None]:
        yield Log("hello")
        return None

    counts: dict[str, list[int]] = {"first": [], "second": []}

    def make_transform(name: str) -> Callable[[Effect], Effect]:
        def _transform(effect: Effect) -> Effect:
            counts[name].append(id(effect))
            return effect

        return _transform

    interpreter = ProgramInterpreter()
    program = simple_program().intercept(make_transform("first")).intercept(make_transform("second"))
    result = await interpreter.run_async(program)

    assert result.is_ok
    for call_ids in counts.values():
        assert len(call_ids) == 1


@pytest.mark.asyncio
async def test_intercept_many_layers_single_application():
    """Five chained intercept calls still invoke each transform once."""

    @do
    def simple_program() -> EffectGenerator[None]:
        yield Log("hello")
        return None

    names = [f"layer-{idx}" for idx in range(5)]
    counts: dict[str, list[int]] = {name: [] for name in names}

    def make_transform(name: str) -> Callable[[Effect], Effect]:
        def _transform(effect: Effect) -> Effect:
            counts[name].append(id(effect))
            return effect

        return _transform

    interpreter = ProgramInterpreter()
    program = simple_program()
    for name in names:
        program = program.intercept(make_transform(name))

    result = await interpreter.run_async(program)

    assert result.is_ok
    for name in names:
        assert len(counts[name]) == 1


@pytest.mark.asyncio
async def test_intercept_complex_transform_chain_logs_once_per_effect():
    """Stacked transforms that return Programs should add exactly one extra log per effect."""

    @do
    def child(idx: int) -> EffectGenerator[int]:
        yield Log(f"child-{idx}")
        return idx

    @do
    def outer() -> EffectGenerator[list[int]]:
        values = yield Gather(child(1), child(2))
        yield Log("after-gather")
        yield Log(f"values: {values}")
        return list(values)

    call_counts: dict[str, int] = {}

    def make_transform(tag: str) -> Callable[[Effect], Effect | Program]:
        call_counts[tag] = 0

        @do
        def bounce(effect: Effect) -> EffectGenerator[Effect]:
            call_counts[tag] += 1
            return effect

        def transform(effect: Effect) -> Effect | Program:
            return bounce(effect)

        return transform

    program = outer().intercept(make_transform("layer1")).intercept(make_transform("layer2"))
    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(program)

    assert result.is_ok
    assert result.value == [1, 2]
    log_entries = list(result.context.log)
    assert len(log_entries) == 4
    assert call_counts["layer1"] == call_counts["layer2"] == 5


@pytest.mark.asyncio
async def test_intercept_program_return_with_auto_unwrap_runs_once():
    """Intercept transforms that return Programs should not cause double execution after auto-unwrapping."""

    run_order: list[str] = []

    @do
    def base_program() -> EffectGenerator[str]:
        run_order.append("base-start")
        yield Log("base-log")
        return "ok"

    @do
    def passthrough(program):
        return program

    def identity_interceptor(effect: Effect) -> Effect:
        return effect

    p_base: KleisliProgramCall[str] = base_program()
    p_wrapped: KleisliProgramCall = passthrough(p_base)
    p_wrapped = passthrough(p_wrapped)
    p_intercepted: Program[str] = p_wrapped.intercept(identity_interceptor)  # type: ignore[arg-type]

    interpreter = ProgramInterpreter()
    result: RunResult = await interpreter.run_async(p_intercepted)

    assert result.is_ok
    assert result.value == "ok"
    assert run_order == ["base-start"] # we are failing here, as run_order is now ['base-start', 'base-start','base-start']
    assert result.log.count("base-log") == 1
