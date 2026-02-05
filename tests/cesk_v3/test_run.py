from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    Forward,
    PythonAsyncSyntaxEscape,
    Resume,
)
from doeff.cesk_v3.run import RunResult, async_run, sync_run
from doeff.do import do
from doeff.program import Program


@dataclass(frozen=True)
class SimpleEffect(EffectBase):
    value: int


@dataclass(frozen=True)
class AsyncSimpleEffect(EffectBase):
    value: int


class TestRunResult:
    def test_is_ok_when_value_present(self):
        result: RunResult[int] = RunResult(value=42)
        assert result.is_ok is True
        assert result.is_error is False

    def test_is_error_when_error_present(self):
        result: RunResult[int] = RunResult(error=ValueError("test"))
        assert result.is_ok is False
        assert result.is_error is True

    def test_unwrap_returns_value(self):
        result: RunResult[int] = RunResult(value=42)
        assert result.unwrap() == 42

    def test_unwrap_raises_error(self):
        result: RunResult[int] = RunResult(error=ValueError("test error"))
        with pytest.raises(ValueError, match="test error"):
            result.unwrap()

    def test_final_store_preserved(self):
        result: RunResult[int] = RunResult(value=1, final_store={"key": "value"})
        assert result.final_store == {"key": "value"}


class TestSyncRun:
    def test_pure_program_returns_value(self):
        @do
        def program() -> Program[int]:
            return 42
            yield  # type: ignore

        result = sync_run(program())
        assert result.is_ok
        assert result.unwrap() == 42

    def test_program_with_handler(self):
        @do
        def handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, SimpleEffect):
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def program() -> Program[int]:
            result = yield SimpleEffect(value=21)
            return result

        result = sync_run(program(), handlers=[handler])
        assert result.is_ok
        assert result.unwrap() == 42

    def test_program_with_single_handler(self):
        @do
        def double_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, SimpleEffect):
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def program() -> Program[int]:
            return (yield SimpleEffect(value=10))

        result = sync_run(program(), handlers=[double_handler])
        assert result.is_ok
        assert result.unwrap() == 20

    def test_forward_then_resume_raises_error(self):
        @do
        def outer_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, SimpleEffect):
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def inner_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, SimpleEffect):
                result = yield Forward(effect)
                return (yield Resume(result + 1))
            return (yield Forward(effect))

        @do
        def program() -> Program[int]:
            return (yield SimpleEffect(value=10))

        result = sync_run(program(), handlers=[inner_handler, outer_handler])
        assert result.is_error
        assert "Resume called after Forward" in str(result.error)

    def test_error_captured_in_result(self):
        @do
        def program() -> Program[int]:
            raise ValueError("test error")
            yield  # type: ignore

        result = sync_run(program())
        assert result.is_error
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "test error"

    def test_raises_type_error_on_escape(self):
        async def dummy_action() -> int:
            return 1

        @do
        def escape_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, AsyncSimpleEffect):
                yield PythonAsyncSyntaxEscape(action=dummy_action)
                return (yield Resume(1))
            return (yield Forward(effect))

        @do
        def program() -> Program[int]:
            return (yield AsyncSimpleEffect(value=1))

        with pytest.raises(TypeError, match="sync_run received PythonAsyncSyntaxEscape"):
            sync_run(program(), handlers=[escape_handler])

    def test_store_parameter(self):
        @do
        def program() -> Program[str]:
            return "done"
            yield  # type: ignore

        result = sync_run(program(), store={"initial": "value"})
        assert result.is_ok


class TestAsyncRun:
    @pytest.mark.asyncio
    async def test_pure_program_returns_value(self):
        @do
        def program() -> Program[int]:
            return 42
            yield  # type: ignore

        result = await async_run(program())
        assert result.is_ok
        assert result.unwrap() == 42

    @pytest.mark.asyncio
    async def test_program_with_handler(self):
        @do
        def handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, SimpleEffect):
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def program() -> Program[int]:
            result = yield SimpleEffect(value=21)
            return result

        result = await async_run(program(), handlers=[handler])
        assert result.is_ok
        assert result.unwrap() == 42

    @pytest.mark.asyncio
    async def test_handles_escape_and_continues(self):
        async def value_action() -> int:
            return 100

        @do
        def escape_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, AsyncSimpleEffect):
                yield PythonAsyncSyntaxEscape(action=value_action)
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def program() -> Program[int]:
            result = yield AsyncSimpleEffect(value=5)
            return result + 1

        result = await async_run(program(), handlers=[escape_handler])
        assert result.is_ok
        assert result.unwrap() == 11

    @pytest.mark.asyncio
    async def test_error_captured_in_result(self):
        @do
        def program() -> Program[int]:
            raise ValueError("async error")
            yield  # type: ignore

        result = await async_run(program())
        assert result.is_error
        assert isinstance(result.error, ValueError)

    @pytest.mark.asyncio
    async def test_multiple_escapes(self):
        call_count = 0

        async def counting_action() -> int:
            nonlocal call_count
            call_count += 1
            return call_count

        @do
        def escape_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, AsyncSimpleEffect):
                yield PythonAsyncSyntaxEscape(action=counting_action)
                return (yield Resume(effect.value))
            return (yield Forward(effect))

        @do
        def program() -> Program[int]:
            a = yield AsyncSimpleEffect(value=1)
            b = yield AsyncSimpleEffect(value=2)
            c = yield AsyncSimpleEffect(value=3)
            return a + b + c

        result = await async_run(program(), handlers=[escape_handler])
        assert result.is_ok
        assert result.unwrap() == 6
        assert call_count == 3
