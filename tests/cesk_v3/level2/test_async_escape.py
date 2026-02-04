"""Tests for PythonAsyncSyntaxEscape handling in level2_step."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from doeff.cesk_v3.level1_cesk.state import CESKState, EffectYield, ProgramControl, Value
from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase, WithHandlerFrame
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    PythonAsyncSyntaxEscape,
    Resume,
)
from doeff.cesk_v3.level2_algebraic_effects.step import level2_step
from doeff.do import do
from doeff.program import Program


@dataclass(frozen=True)
class AsyncTestEffect(EffectBase):
    value: int


class TestPythonAsyncSyntaxEscapeBasic:
    def test_escape_yielded_by_handler_returns_wrapped_escape(self):
        async def value_returning_action() -> int:
            return 42

        @do
        def escape_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, AsyncTestEffect):
                yield PythonAsyncSyntaxEscape(action=value_returning_action)
                return (yield Resume(effect.value))
            raise NotImplementedError

        @do
        def user_program() -> Program[int]:
            return (yield AsyncTestEffect(value=10))

        state = CESKState(
            C=ProgramControl(user_program()),
            E={},
            S={},
            K=[WithHandlerFrame(handler=escape_handler)],
        )

        while True:
            result = level2_step(state)
            if isinstance(result, PythonAsyncSyntaxEscape):
                break
            assert not isinstance(result, (Value,)), "Should get escape before completion"
            state = result

        assert isinstance(result, PythonAsyncSyntaxEscape)
        assert callable(result.action)

    @pytest.mark.asyncio
    async def test_wrapped_action_returns_cesk_state(self):
        async def value_returning_action() -> int:
            return 42

        @do
        def escape_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, AsyncTestEffect):
                yield PythonAsyncSyntaxEscape(action=value_returning_action)
                return (yield Resume(effect.value))
            raise NotImplementedError

        @do
        def user_program() -> Program[int]:
            return (yield AsyncTestEffect(value=10))

        state = CESKState(
            C=ProgramControl(user_program()),
            E={},
            S={},
            K=[WithHandlerFrame(handler=escape_handler)],
        )

        while True:
            result = level2_step(state)
            if isinstance(result, PythonAsyncSyntaxEscape):
                break
            state = result

        new_state = await result.action()

        assert isinstance(new_state, CESKState)
        assert isinstance(new_state.C, Value)
        assert new_state.C.value == 42

    @pytest.mark.asyncio
    async def test_wrapped_action_captures_correct_k(self):
        async def value_returning_action() -> str:
            return "async_result"

        @do
        def escape_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, AsyncTestEffect):
                yield PythonAsyncSyntaxEscape(action=value_returning_action)
                return (yield Resume("done"))
            raise NotImplementedError

        @do
        def user_program() -> Program[str]:
            return (yield AsyncTestEffect(value=1))

        initial_k = [WithHandlerFrame(handler=escape_handler)]
        state = CESKState(
            C=ProgramControl(user_program()),
            E={"test_env": "preserved"},
            S={"test_store": "also_preserved"},
            K=initial_k,
        )

        while True:
            result = level2_step(state)
            if isinstance(result, PythonAsyncSyntaxEscape):
                break
            state = result

        new_state = await result.action()

        assert new_state.E == state.E
        assert new_state.S == state.S
        assert len(new_state.K) == len(state.K)


class TestAsyncEscapeContinuation:
    @pytest.mark.asyncio
    async def test_can_continue_stepping_after_escape(self):
        call_count = 0

        async def counting_action() -> int:
            nonlocal call_count
            call_count += 1
            return 100

        @do
        def escape_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, AsyncTestEffect):
                yield PythonAsyncSyntaxEscape(action=counting_action)
                return (yield Resume(effect.value * 2))
            raise NotImplementedError

        @do
        def user_program() -> Program[int]:
            result = yield AsyncTestEffect(value=5)
            return result + 1

        state = CESKState(
            C=ProgramControl(user_program()),
            E={},
            S={},
            K=[WithHandlerFrame(handler=escape_handler)],
        )

        from doeff.cesk_v3.level1_cesk.state import Done

        max_steps = 50
        steps = 0
        final_result = None

        while steps < max_steps:
            result = level2_step(state)
            steps += 1

            if isinstance(result, Done):
                final_result = result.value
                break

            if isinstance(result, PythonAsyncSyntaxEscape):
                state = await result.action()
                continue

            state = result

        assert call_count == 1
        assert final_result == 11


class TestAsyncEscapeWithAsyncioOperations:
    @pytest.mark.asyncio
    async def test_escape_with_asyncio_sleep(self):
        async def sleep_action() -> str:
            await asyncio.sleep(0.001)
            return "slept"

        @do
        def escape_handler(effect: EffectBase) -> Program[Any]:
            if isinstance(effect, AsyncTestEffect):
                yield PythonAsyncSyntaxEscape(action=sleep_action)
                return (yield Resume("after_sleep"))
            raise NotImplementedError

        @do
        def user_program() -> Program[str]:
            return (yield AsyncTestEffect(value=1))

        state = CESKState(
            C=ProgramControl(user_program()),
            E={},
            S={},
            K=[WithHandlerFrame(handler=escape_handler)],
        )

        while True:
            result = level2_step(state)
            if isinstance(result, PythonAsyncSyntaxEscape):
                break
            state = result

        new_state = await result.action()

        assert isinstance(new_state.C, Value)
        assert new_state.C.value == "slept"
