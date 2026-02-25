from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from doeff import (
    Ask,
    EffectBase,
    Local,
    Pass,
    Resume,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
    with_intercept,
)


def _base_handlers(mode: str) -> list[Any]:
    if mode == "sync":
        return list(default_handlers())
    return list(default_async_handlers())


async def _run_with_handlers(
    mode: str,
    program,
    extra_handlers: list[Any],
    env: dict[str, Any],
):
    handlers = [*_base_handlers(mode), *extra_handlers]
    if mode == "sync":
        return run(program, handlers=handlers, env=env)
    return await async_run(program, handlers=handlers, env=env)


@pytest.mark.asyncio
async def test_handler_ask_sees_local_scope(parameterized_interpreter) -> None:
    """Handler-emitted Ask resolves against Local overrides."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    def ping_handler(effect, k):
        if not isinstance(effect, Ping):
            yield Pass()
            return
        value = yield Ask("config")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    result = await _run_with_handlers(
        parameterized_interpreter.mode,
        Local({"config": "from_local"}, body()),
        [ping_handler],
        env={},
    )
    assert result.is_ok
    assert result.value == "from_local"


@pytest.mark.asyncio
async def test_handler_ask_falls_through_to_outer_env(parameterized_interpreter) -> None:
    """Handler Ask for a non-overridden key resolves to outer env."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    def ping_handler(effect, k):
        if not isinstance(effect, Ping):
            yield Pass()
            return
        value = yield Ask("outer_key")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    result = await _run_with_handlers(
        parameterized_interpreter.mode,
        Local({"other": "irrelevant"}, body()),
        [ping_handler],
        env={"outer_key": "from_env"},
    )
    assert result.is_ok
    assert result.value == "from_env"


@pytest.mark.asyncio
async def test_handler_ask_nested_local(parameterized_interpreter) -> None:
    """Handler Ask resolves the innermost Local override."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    def ping_handler(effect, k):
        if not isinstance(effect, Ping):
            yield Pass()
            return
        value = yield Ask("key")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    program = Local({"key": "outer"}, Local({"key": "inner"}, body()))
    result = await _run_with_handlers(
        parameterized_interpreter.mode,
        program,
        [ping_handler],
        env={"key": "root"},
    )
    assert result.is_ok
    assert result.value == "inner"


@pytest.mark.asyncio
async def test_handler_ask_with_intercept_and_local(parameterized_interpreter) -> None:
    """WithIntercept must not break Local visibility for handler-emitted Ask."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    def observer(effect):
        return effect

    def ping_handler(effect, k):
        if not isinstance(effect, Ping):
            yield Pass()
            return
        value = yield Ask("key")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    program = with_intercept(
        observer,
        Local({"key": "intercepted_local"}, body()),
        types=(),
        mode="exclude",
    )
    result = await _run_with_handlers(
        parameterized_interpreter.mode,
        program,
        [ping_handler],
        env={},
    )
    assert result.is_ok
    assert result.value == "intercepted_local"


@pytest.mark.asyncio
async def test_multiple_handlers_ask_in_local(parameterized_interpreter) -> None:
    """Multiple handlers emitting Ask should share the same Local scope."""

    @dataclass(frozen=True)
    class EffA(EffectBase):
        pass

    @dataclass(frozen=True)
    class EffB(EffectBase):
        pass

    def handler_a(effect, k):
        if not isinstance(effect, EffA):
            yield Pass()
            return
        value = yield Ask("key_a")
        return (yield Resume(k, value))

    def handler_b(effect, k):
        if not isinstance(effect, EffB):
            yield Pass()
            return
        value = yield Ask("key_b")
        return (yield Resume(k, value))

    @do
    def body():
        a = yield EffA()
        b = yield EffB()
        return (a, b)

    result = await _run_with_handlers(
        parameterized_interpreter.mode,
        Local({"key_a": "alpha", "key_b": "beta"}, body()),
        [handler_a, handler_b],
        env={},
    )
    assert result.is_ok
    assert result.value == ("alpha", "beta")


@pytest.mark.asyncio
async def test_handler_ask_lazy_value_in_local(parameterized_interpreter) -> None:
    """Handler Ask resolves lazy Local values exactly once."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    call_count = 0

    @do
    def expensive():
        nonlocal call_count
        call_count += 1
        if False:
            yield
        return 42

    def ping_handler(effect, k):
        if not isinstance(effect, Ping):
            yield Pass()
            return
        value = yield Ask("lazy_svc")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    result = await _run_with_handlers(
        parameterized_interpreter.mode,
        Local({"lazy_svc": expensive()}, body()),
        [ping_handler],
        env={},
    )
    assert result.is_ok
    assert result.value == 42
    assert call_count == 1
