"""Coverage for `EffectBase.intercept` across all effect types."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

import pytest

from doeff._vendor import Ok
from doeff.cache_policy import ensure_cache_policy
from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect
from doeff.effects.cache import CacheGetEffect, CachePutEffect
from doeff.effects.dep import DepInjectEffect
from doeff.effects.future import FutureAwaitEffect
from doeff.effects.gather import GatherEffect
from doeff.effects.graph import (
    GraphAnnotateEffect,
    GraphCaptureEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
)
from doeff.effects.io import IOPerformEffect, IOPrintEffect
from doeff.effects.memo import MemoGetEffect, MemoPutEffect
from doeff.effects.reader import AskEffect, LocalEffect
from doeff.effects.result import (
    ResultCatchEffect,
    ResultFailEffect,
    ResultFinallyEffect,
    ResultFirstSuccessEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    ResultSafeEffect,
    ResultUnwrapEffect,
)
from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect
from doeff.effects.writer import WriterListenEffect, WriterTellEffect
from doeff.cesk_adapter import CESKInterpreter
from doeff.program import GeneratorProgram, Program
from doeff.types import Effect

SUFFIX = "|intercepted"


class DummyAwaitable:
    """Simple awaitable that can be instantiated without an event loop."""

    def __await__(self) -> Iterable[None]:
        yield
        return


def writer_program(message: str) -> Program[str]:
    """Program that yields a single writer effect."""

    def generator():
        yield WriterTellEffect(message=message)
        return message

    return GeneratorProgram(generator)


async def writer_message(program: Program[Any]) -> str:
    """Run ``program`` and return the first writer log entry."""

    interpreter = CESKInterpreter()
    run_result = await interpreter.run_async(program)
    assert run_result.is_ok
    assert run_result.context.log
    return run_result.context.log[0]


def tagging_transform(effect: Effect) -> Effect:
    """Append a suffix to every writer tell effect."""

    if isinstance(effect, WriterTellEffect):
        return replace(effect, message=f"{effect.message}{SUFFIX}")
    return effect


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AtomicGetEffect(key="item", default_factory=lambda: "init"),
        lambda: AtomicUpdateEffect(key="item", updater=lambda v: v, default_factory=None),
        lambda: CacheGetEffect(key="cache-key"),
        lambda: CachePutEffect(key="cache-key", value=1, policy=ensure_cache_policy(ttl=1)),
        lambda: DepInjectEffect(key="service"),
        lambda: FutureAwaitEffect(awaitable=DummyAwaitable()),
        lambda: GraphStepEffect(value="value", meta={"step": 1}),
        lambda: GraphAnnotateEffect(meta={"tag": "test"}),
        lambda: GraphSnapshotEffect(),
        lambda: IOPerformEffect(action=lambda: "done"),
        lambda: IOPrintEffect(message="hello"),
        lambda: MemoGetEffect(key="memo"),
        lambda: MemoPutEffect(key="memo", value="value"),
        lambda: AskEffect(key="env"),
        lambda: ResultFailEffect(exception=RuntimeError("boom")),
        lambda: ResultUnwrapEffect(result=Ok("value")),
        lambda: StateGetEffect(key="state"),
        lambda: StatePutEffect(key="state", value=5),
        lambda: StateModifyEffect(key="state", func=lambda v: v),
        lambda: WriterTellEffect(message="note"),
    ],
)
def test_data_effects_return_self_during_intercept(factory: Callable[[], Effect]) -> None:
    effect = factory()
    assert effect.intercept(tagging_transform) is effect


@pytest.mark.asyncio
async def test_local_effect_intercept_rewrites_sub_program() -> None:
    base = LocalEffect(env_update={"key": "value"}, sub_program=writer_program("local"))

    result = base.intercept(tagging_transform)

    assert result is not base
    assert (await writer_message(result.sub_program)).endswith(SUFFIX)


@pytest.mark.asyncio
async def test_writer_listen_effect_intercept_rewrites_sub_program() -> None:
    base = WriterListenEffect(sub_program=writer_program("listen"))

    result = base.intercept(tagging_transform)

    assert result is not base
    assert (await writer_message(result.sub_program)).endswith(SUFFIX)


@pytest.mark.asyncio
async def test_gather_effect_intercept_rewrites_each_program() -> None:
    base = GatherEffect(programs=(writer_program("one"), writer_program("two")))

    result = base.intercept(tagging_transform)

    assert result is not base
    messages = [await writer_message(program) for program in result.programs]
    assert all(message.endswith(SUFFIX) for message in messages)


@pytest.mark.asyncio
async def test_result_finally_effect_intercept_rewrites_programs() -> None:
    base = ResultFinallyEffect(
        sub_program=writer_program("sub"),
        finalizer=writer_program("final"),
    )

    result = base.intercept(tagging_transform)

    assert result is not base
    assert (await writer_message(result.sub_program)).endswith(SUFFIX)
    assert isinstance(result.finalizer, Program)
    assert (await writer_message(result.finalizer)).endswith(SUFFIX)


@pytest.mark.asyncio
async def test_graph_capture_effect_intercept_rewrites_program() -> None:
    base = GraphCaptureEffect(program=writer_program("graph"))

    result = base.intercept(tagging_transform)

    assert result is not base
    assert (await writer_message(result.program)).endswith(SUFFIX)


@pytest.mark.asyncio
async def test_result_catch_effect_intercept_rewrites_sub_program_and_handler() -> None:
    base = ResultCatchEffect(
        sub_program=writer_program("sub"),
        handler=lambda _exc: writer_program("handler"),
    )

    result = base.intercept(tagging_transform)

    assert result is not base
    assert (await writer_message(result.sub_program)).endswith(SUFFIX)

    handled = result.handler(Exception("boom"))
    assert isinstance(handled, Program)
    assert (await writer_message(handled)).endswith(SUFFIX)


@pytest.mark.asyncio
async def test_result_recover_effect_intercept_rewrites_sub_program_and_fallback_program() -> None:
    base = ResultRecoverEffect(
        sub_program=writer_program("sub"),
        fallback=writer_program("fallback"),
    )

    result = base.intercept(tagging_transform)

    assert result is not base
    assert (await writer_message(result.sub_program)).endswith(SUFFIX)
    assert isinstance(result.fallback, Program)
    assert (await writer_message(result.fallback)).endswith(SUFFIX)


@pytest.mark.asyncio
async def test_result_retry_effect_intercept_rewrites_sub_program() -> None:
    base = ResultRetryEffect(sub_program=writer_program("retry"))

    result = base.intercept(tagging_transform)

    assert result is not base
    assert (await writer_message(result.sub_program)).endswith(SUFFIX)


@pytest.mark.asyncio
async def test_result_safe_effect_intercept_rewrites_sub_program() -> None:
    base = ResultSafeEffect(sub_program=writer_program("safe"))

    result = base.intercept(tagging_transform)

    assert result is not base
    assert (await writer_message(result.sub_program)).endswith(SUFFIX)


@pytest.mark.asyncio
async def test_result_first_success_effect_intercept_rewrites_programs() -> None:
    base = ResultFirstSuccessEffect(programs=(writer_program("one"), writer_program("two")))

    result = base.intercept(tagging_transform)

    assert result is not base
    messages = [await writer_message(program) for program in result.programs]
    assert all(message.endswith(SUFFIX) for message in messages)
