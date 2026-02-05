from __future__ import annotations

import asyncio

import pytest

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume
from doeff.cesk_v3.level3_core_effects import (
    Await,
    AwaitEffect,
    Get,
    Put,
    python_async_syntax_escape_handler,
    sync_await_handler,
)
from doeff.cesk_v3.run import (
    async_handlers_preset,
    async_run,
    sync_handlers_preset,
    sync_run,
)
from doeff.do import do
from doeff.program import Program


class TestAwaitEffect:
    def test_await_creates_effect(self):
        async def dummy():
            return 1

        effect = Await(dummy())
        assert isinstance(effect, AwaitEffect)


class TestSyncAwaitHandler:
    def test_sync_await_handler_runs_awaitable_in_thread(self):
        async def async_computation() -> int:
            await asyncio.sleep(0.001)
            return 42

        @do
        def program() -> Program[int]:
            result = yield Await(async_computation())
            return result

        result = sync_run(program(), handlers=[sync_await_handler])
        assert result.is_ok
        assert result.unwrap() == 42

    def test_sync_await_handler_preserves_value(self):
        async def get_message() -> str:
            return "hello from async"

        @do
        def program() -> Program[str]:
            return (yield Await(get_message()))

        result = sync_run(program(), handlers=[sync_await_handler])
        assert result.unwrap() == "hello from async"

    def test_sync_await_handler_forwards_non_await_effects(self):
        @do
        def other_handler(effect: EffectBase) -> Program[int]:
            if isinstance(effect, AwaitEffect):
                raise AssertionError("Should not reach here")
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        async def async_fn() -> int:
            return 1

        @do
        def program() -> Program[int]:
            return (yield Await(async_fn()))

        result = sync_run(
            program(), handlers=[sync_await_handler, other_handler]
        )
        assert result.unwrap() == 1


class TestPythonAsyncSyntaxEscapeHandler:
    @pytest.mark.asyncio
    async def test_escape_handler_awaits_and_continues(self):
        async def async_computation() -> int:
            await asyncio.sleep(0.001)
            return 42

        @do
        def program() -> Program[int]:
            result = yield Await(async_computation())
            return result

        result = await async_run(
            program(), handlers=[python_async_syntax_escape_handler]
        )
        assert result.is_ok
        assert result.unwrap() == 42

    @pytest.mark.asyncio
    async def test_escape_handler_returns_awaited_value(self):
        async def get_data() -> str:
            return "async data"

        @do
        def program() -> Program[str]:
            data = yield Await(get_data())
            return f"got: {data}"

        result = await async_run(
            program(), handlers=[python_async_syntax_escape_handler]
        )
        assert result.unwrap() == "got: async data"

    @pytest.mark.asyncio
    async def test_escape_handler_multiple_awaits(self):
        call_order: list[int] = []

        async def step(n: int) -> int:
            call_order.append(n)
            return n * 10

        @do
        def program() -> Program[int]:
            a = yield Await(step(1))
            b = yield Await(step(2))
            c = yield Await(step(3))
            return a + b + c

        result = await async_run(
            program(), handlers=[python_async_syntax_escape_handler]
        )
        assert result.unwrap() == 60
        assert call_order == [1, 2, 3]


class TestSyncHandlersPreset:
    def test_preset_includes_state(self):
        @do
        def program() -> Program[int]:
            yield Put("x", 10)
            return (yield Get("x"))

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.unwrap() == 10

    def test_preset_with_initial_state(self):
        @do
        def program() -> Program[int]:
            x = yield Get("x")
            yield Put("x", x + 1)
            return (yield Get("x"))

        result = sync_run(
            program(), handlers=sync_handlers_preset(initial_state={"x": 5})
        )
        assert result.unwrap() == 6

    def test_preset_includes_await_via_thread(self):
        async def async_fn() -> int:
            return 123

        @do
        def program() -> Program[int]:
            result = yield Await(async_fn())
            yield Put("result", result)
            return (yield Get("result"))

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.unwrap() == 123


class TestAsyncHandlersPreset:
    @pytest.mark.asyncio
    async def test_preset_includes_state(self):
        @do
        def program() -> Program[int]:
            yield Put("x", 20)
            return (yield Get("x"))

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.unwrap() == 20

    @pytest.mark.asyncio
    async def test_preset_with_initial_state(self):
        @do
        def program() -> Program[int]:
            x = yield Get("x")
            yield Put("x", x * 2)
            return (yield Get("x"))

        result = await async_run(
            program(), handlers=async_handlers_preset(initial_state={"x": 7})
        )
        assert result.unwrap() == 14

    @pytest.mark.asyncio
    async def test_preset_includes_async_await(self):
        async def fetch_data() -> str:
            await asyncio.sleep(0.001)
            return "fetched"

        @do
        def program() -> Program[str]:
            data = yield Await(fetch_data())
            yield Put("data", data)
            return (yield Get("data"))

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.unwrap() == "fetched"

    @pytest.mark.asyncio
    async def test_preset_combined_effects(self):
        async def slow_add(a: int, b: int) -> int:
            await asyncio.sleep(0.001)
            return a + b

        @do
        def program() -> Program[int]:
            yield Put("counter", 0)

            v1 = yield Await(slow_add(1, 2))
            c1 = yield Get("counter")
            yield Put("counter", c1 + v1)

            v2 = yield Await(slow_add(3, 4))
            c2 = yield Get("counter")
            yield Put("counter", c2 + v2)

            return (yield Get("counter"))

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.unwrap() == 10  # (1+2) + (3+4) = 10
