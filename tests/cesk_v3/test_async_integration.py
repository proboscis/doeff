from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume
from doeff.cesk_v3.level3_core_effects import (
    Ask,
    Await,
    Get,
    Modify,
    Put,
    python_async_syntax_escape_handler,
    reader_handler,
    state_handler,
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


class TestSyncRunWithAsyncAwaitables:

    def test_await_simple_coroutine(self):
        async def fetch_value() -> int:
            return 42

        @do
        def program() -> Program[int]:
            return (yield Await(fetch_value()))

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.unwrap() == 42

    def test_await_with_delay(self):
        async def delayed_value() -> str:
            await asyncio.sleep(0.01)
            return "delayed"

        @do
        def program() -> Program[str]:
            return (yield Await(delayed_value()))

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.unwrap() == "delayed"

    def test_multiple_sequential_awaits(self):
        async def step(n: int) -> int:
            await asyncio.sleep(0.001)
            return n * 2

        @do
        def program() -> Program[int]:
            a = yield Await(step(1))
            b = yield Await(step(2))
            c = yield Await(step(3))
            return a + b + c

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.unwrap() == 12

    def test_await_combined_with_state(self):
        async def fetch_increment() -> int:
            return 5

        @do
        def program() -> Program[int]:
            yield Put("counter", 10)
            increment = yield Await(fetch_increment())
            yield Modify("counter", lambda x: x + increment)
            return (yield Get("counter"))

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.unwrap() == 15

    def test_await_combined_with_reader(self):
        async def fetch_with_config(url: str) -> str:
            await asyncio.sleep(0.001)
            return f"fetched from {url}"

        @do
        def program() -> Program[str]:
            base_url = yield Ask("api_url")
            return (yield Await(fetch_with_config(base_url)))

        result = sync_run(
            program(),
            handlers=sync_handlers_preset(env={"api_url": "https://api.example.com"}),
        )
        assert result.unwrap() == "fetched from https://api.example.com"


class TestAsyncRunWithEscapes:

    @pytest.mark.asyncio
    async def test_await_simple_coroutine(self):
        async def fetch_value() -> int:
            return 42

        @do
        def program() -> Program[int]:
            return (yield Await(fetch_value()))

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.unwrap() == 42

    @pytest.mark.asyncio
    async def test_await_with_real_async_sleep(self):
        async def delayed_value() -> str:
            await asyncio.sleep(0.01)
            return "async delayed"

        @do
        def program() -> Program[str]:
            return (yield Await(delayed_value()))

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.unwrap() == "async delayed"

    @pytest.mark.asyncio
    async def test_multiple_awaits_interleaved_with_effects(self):
        call_log: list[str] = []

        async def async_step(name: str) -> str:
            call_log.append(f"start:{name}")
            await asyncio.sleep(0.001)
            call_log.append(f"end:{name}")
            return name

        @do
        def program() -> Program[list[str]]:
            yield Put("results", [])

            r1 = yield Await(async_step("first"))
            results = yield Get("results")
            yield Put("results", results + [r1])

            r2 = yield Await(async_step("second"))
            results = yield Get("results")
            yield Put("results", results + [r2])

            return (yield Get("results"))

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.unwrap() == ["first", "second"]
        assert call_log == ["start:first", "end:first", "start:second", "end:second"]

    @pytest.mark.asyncio
    async def test_nested_programs_with_await(self):
        async def fetch_data(key: str) -> str:
            return f"data:{key}"

        @do
        def fetch_and_store(key: str) -> Program[None]:
            value = yield Await(fetch_data(key))
            yield Put(key, value)

        @do
        def program() -> Program[dict[str, str]]:
            yield fetch_and_store("a")
            yield fetch_and_store("b")
            yield fetch_and_store("c")
            a = yield Get("a")
            b = yield Get("b")
            c = yield Get("c")
            return {"a": a, "b": b, "c": c}

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.unwrap() == {
            "a": "data:a",
            "b": "data:b",
            "c": "data:c",
        }

    @pytest.mark.asyncio
    async def test_error_in_awaited_coroutine(self):
        async def failing_coroutine() -> int:
            raise ValueError("async failure")

        @do
        def program() -> Program[int]:
            return (yield Await(failing_coroutine()))

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.is_error
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "async failure"


class TestCustomEffectsWithAsync:

    @pytest.mark.asyncio
    async def test_custom_effect_with_async_handler(self):
        @dataclass(frozen=True)
        class HttpGet(EffectBase):
            url: str

        @do
        def http_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, HttpGet):

                async def do_request() -> str:
                    await asyncio.sleep(0.001)
                    return f"response from {effect.url}"

                response = yield Await(do_request())
                return (yield Resume(response))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def program() -> Program[str]:
            return (yield HttpGet("https://example.com"))

        result = await async_run(
            program(),
            handlers=[
                http_handler,
                python_async_syntax_escape_handler,
                state_handler(),
                reader_handler(),
            ],
        )
        assert result.unwrap() == "response from https://example.com"

    @pytest.mark.asyncio
    async def test_custom_effect_accumulating_results(self):
        @dataclass(frozen=True)
        class Emit(EffectBase):
            value: Any

        @dataclass(frozen=True)
        class GetEmitted(EffectBase):
            pass

        def emit_handler() -> Any:
            emitted: list[Any] = []

            @do
            def handler(effect: EffectBase) -> Program[Any]:
                if isinstance(effect, Emit):
                    emitted.append(effect.value)
                    return (yield Resume(None))
                if isinstance(effect, GetEmitted):
                    return (yield Resume(list(emitted)))
                forwarded = yield Forward(effect)
                return (yield Resume(forwarded))

            return handler

        async def async_produce(n: int) -> int:
            return n * 10

        @do
        def program() -> Program[list[int]]:
            for i in range(3):
                value = yield Await(async_produce(i))
                yield Emit(value)
            return (yield GetEmitted())

        result = await async_run(
            program(),
            handlers=[
                python_async_syntax_escape_handler,
                emit_handler(),
                state_handler(),
                reader_handler(),
            ],
        )
        assert result.unwrap() == [0, 10, 20]


class TestErrorHandling:

    def test_sync_error_before_await(self):
        async def never_called() -> int:
            raise AssertionError("Should not be called")

        @do
        def program() -> Program[int]:
            raise ValueError("early error")
            yield Await(never_called())

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.is_error
        assert isinstance(result.error, ValueError)

    def test_sync_error_after_await(self):
        async def successful_await() -> int:
            return 42

        @do
        def program() -> Program[int]:
            _ = yield Await(successful_await())
            raise ValueError("late error")

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.is_error
        assert isinstance(result.error, ValueError)

    @pytest.mark.asyncio
    async def test_async_error_propagates(self):
        async def failing() -> int:
            raise RuntimeError("async boom")

        @do
        def program() -> Program[int]:
            return (yield Await(failing()))

        result = await async_run(program(), handlers=async_handlers_preset())
        assert result.is_error
        assert isinstance(result.error, RuntimeError)


class TestPresetCombinations:

    def test_sync_preset_all_effects_together(self):
        async def async_multiply(x: int) -> int:
            return x * 2

        @do
        def program() -> Program[int]:
            base = yield Ask("base_value")
            yield Put("current", base)
            async_result = yield Await(async_multiply(base))
            yield Modify("current", lambda x: x + async_result)
            return (yield Get("current"))

        result = sync_run(
            program(),
            handlers=sync_handlers_preset(
                initial_state={"current": 0},
                env={"base_value": 10},
            ),
        )
        assert result.unwrap() == 30

    @pytest.mark.asyncio
    async def test_async_preset_all_effects_together(self):
        async def async_add(a: int, b: int) -> int:
            await asyncio.sleep(0.001)
            return a + b

        @do
        def program() -> Program[int]:
            multiplier = yield Ask("multiplier")
            yield Put("sum", 0)

            for i in range(3):
                current = yield Get("sum")
                added = yield Await(async_add(current, i * multiplier))
                yield Put("sum", added)

            return (yield Get("sum"))

        result = await async_run(
            program(),
            handlers=async_handlers_preset(env={"multiplier": 10}),
        )
        assert result.unwrap() == 30


class TestEdgeCases:

    def test_empty_program(self):
        @do
        def program() -> Program[None]:
            return None
            yield

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.is_ok
        assert result.unwrap() is None

    def test_await_returning_none(self):
        async def returns_none() -> None:
            return None

        @do
        def program() -> Program[None]:
            return (yield Await(returns_none()))

        result = sync_run(program(), handlers=sync_handlers_preset())
        assert result.is_ok
        assert result.unwrap() is None

    @pytest.mark.asyncio
    async def test_deeply_nested_awaits(self):
        async def level(n: int) -> int:
            if n <= 0:
                return 1
            return n

        @do
        def nested(depth: int) -> Program[int]:
            if depth <= 0:
                return (yield Await(level(0)))
            inner = yield nested(depth - 1)
            current = yield Await(level(depth))
            return inner + current

        result = await async_run(nested(5), handlers=async_handlers_preset())
        assert result.unwrap() == 16
