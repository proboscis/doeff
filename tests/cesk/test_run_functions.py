"""Tests for sync_run and async_run functions."""

import asyncio

import pytest

from doeff.cesk.run import (
    async_handlers_preset,
    async_run,
    sync_handlers_preset,
    sync_run,
)
from doeff.do import do
from doeff.effects.future import Await
from doeff.effects.state import Get, Put


class TestSyncRun:
    """Tests for sync_run function."""

    def test_sync_run_basic_effects(self) -> None:
        """sync_run handles basic Get/Put effects."""
        @do
        def program():
            yield Put("x", 42)
            return (yield Get("x"))

        result = sync_run(program(), sync_handlers_preset)
        assert result.is_ok
        assert result.value == 42

    def test_sync_run_with_await(self) -> None:
        """sync_run handles Await effect via background thread."""
        @do
        def program():
            async def get_value():
                await asyncio.sleep(0.01)
                return 100

            return (yield Await(get_value()))

        result = sync_run(program(), sync_handlers_preset)
        assert result.is_ok
        assert result.value == 100

    def test_sync_run_with_sleep(self) -> None:
        """sync_run handles Await(asyncio.sleep) via background thread."""
        @do
        def program():
            yield Await(asyncio.sleep(0.01))
            return "delayed"

        result = sync_run(program(), sync_handlers_preset)
        assert result.is_ok
        assert result.value == "delayed"

    def test_sync_run_state_preserved_across_await(self) -> None:
        """State is preserved across Await effects."""
        @do
        def program():
            yield Put("counter", 0)

            async def increment():
                await asyncio.sleep(0.01)
                return 1

            val = yield Await(increment())
            current = yield Get("counter")
            yield Put("counter", current + val)

            val = yield Await(increment())
            current = yield Get("counter")
            yield Put("counter", current + val)

            return (yield Get("counter"))

        result = sync_run(program(), sync_handlers_preset)
        assert result.is_ok
        assert result.value == 2

    def test_sync_run_error_handling(self) -> None:
        """sync_run captures errors properly."""
        @do
        def program():
            raise ValueError("test error")
            yield Put("x", 1)  # noqa: B027

        result = sync_run(program(), sync_handlers_preset)
        assert result.is_err
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "test error"

    def test_sync_run_with_custom_env(self) -> None:
        """sync_run accepts custom environment."""
        from doeff.effects.reader import Ask

        @do
        def program():
            value = yield Ask("my_key")
            return value * 2

        result = sync_run(program(), sync_handlers_preset, env={"my_key": 21})
        assert result.is_ok
        assert result.value == 42

    def test_sync_run_with_initial_store(self) -> None:
        """sync_run accepts initial store."""
        @do
        def program():
            return (yield Get("preset_key"))

        result = sync_run(program(), sync_handlers_preset, store={"preset_key": "preset_value"})
        assert result.is_ok
        assert result.value == "preset_value"


class TestAsyncRun:
    """Tests for async_run function."""

    @pytest.mark.asyncio
    async def test_async_run_basic_effects(self) -> None:
        """async_run handles basic Get/Put effects."""
        @do
        def program():
            yield Put("x", 42)
            return (yield Get("x"))

        result = await async_run(program(), async_handlers_preset)
        assert result.is_ok
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_async_run_with_await(self) -> None:
        """async_run handles Await effect via user's event loop."""
        @do
        def program():
            async def get_value():
                await asyncio.sleep(0.01)
                return 200

            return (yield Await(get_value()))

        result = await async_run(program(), async_handlers_preset)
        assert result.is_ok
        assert result.value == 200

    @pytest.mark.asyncio
    async def test_async_run_with_sleep(self) -> None:
        """async_run handles Await(asyncio.sleep)."""
        @do
        def program():
            yield Await(asyncio.sleep(0.01))
            return "delayed"

        result = await async_run(program(), async_handlers_preset)
        assert result.is_ok
        assert result.value == "delayed"

    @pytest.mark.asyncio
    async def test_async_run_state_preserved_across_await(self) -> None:
        """State is preserved across Await effects in async_run."""
        @do
        def program():
            yield Put("counter", 0)

            async def increment():
                await asyncio.sleep(0.01)
                return 1

            val = yield Await(increment())
            current = yield Get("counter")
            yield Put("counter", current + val)

            val = yield Await(increment())
            current = yield Get("counter")
            yield Put("counter", current + val)

            return (yield Get("counter"))

        result = await async_run(program(), async_handlers_preset)
        assert result.is_ok
        assert result.value == 2

    @pytest.mark.asyncio
    async def test_async_run_error_handling(self) -> None:
        """async_run captures errors properly."""
        @do
        def program():
            raise ValueError("async test error")
            yield Put("x", 1)  # noqa: B027

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "async test error"

    @pytest.mark.asyncio
    async def test_async_run_with_custom_env(self) -> None:
        """async_run accepts custom environment."""
        from doeff.effects.reader import Ask

        @do
        def program():
            value = yield Ask("my_key")
            return value * 2

        result = await async_run(program(), async_handlers_preset, env={"my_key": 21})
        assert result.is_ok
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_async_run_with_initial_store(self) -> None:
        """async_run accepts initial store."""
        @do
        def program():
            return (yield Get("preset_key"))

        result = await async_run(program(), async_handlers_preset, store={"preset_key": "async_preset"})
        assert result.is_ok
        assert result.value == "async_preset"


class TestHandlerPresets:
    """Tests for handler presets."""

    def test_sync_handlers_preset_is_list(self) -> None:
        """sync_handlers_preset is a list of handlers."""
        assert isinstance(sync_handlers_preset, list)
        assert len(sync_handlers_preset) == 10

    def test_async_handlers_preset_is_list(self) -> None:
        """async_handlers_preset is a list of handlers."""
        assert isinstance(async_handlers_preset, list)
        assert len(async_handlers_preset) == 10

    def test_presets_are_different(self) -> None:
        """sync and async presets have different handlers for external wait and async effects."""
        # Index 1: sync uses sync_external_wait_handler, async uses async_external_wait_handler
        # Index 3: sync uses sync_await_handler, async uses python_async_syntax_escape_handler
        assert sync_handlers_preset[1] is not async_handlers_preset[1]
        assert sync_handlers_preset[3] is not async_handlers_preset[3]

    def test_sync_preset_contains_sync_await_handler(self) -> None:
        """sync_handlers_preset contains sync_await_handler."""
        from doeff.cesk.handlers.sync_await_handler import sync_await_handler
        assert sync_await_handler in sync_handlers_preset

    def test_async_preset_contains_python_async_syntax_escape_handler(self) -> None:
        """async_handlers_preset contains python_async_syntax_escape_handler."""
        from doeff.cesk.handlers.python_async_syntax_escape_handler import python_async_syntax_escape_handler
        assert python_async_syntax_escape_handler in async_handlers_preset
