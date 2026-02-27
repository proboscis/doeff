"""Tests for control-effect composition semantics.

This module covers Pure/Try interactions plus Mask/Override behavior that
supersedes the old Intercept-based composition tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import EffectBase, Mask, Override, Spawn, do
from doeff.effects import (
    Ask,
    Gather,
    Get,
    Local,
    Pure,
    Put,
    Try,
)
from doeff.effects.reader import AskEffect


@dataclass(frozen=True)
class Ping(EffectBase):
    label: str


class TestPureEffect:
    @pytest.mark.asyncio
    async def test_pure_returns_value(self, parameterized_interpreter) -> None:
        @do
        def program():
            result = yield Pure(42)
            return result

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_pure_no_state_change(self, parameterized_interpreter) -> None:
        @do
        def program():
            yield Put("counter", 10)
            yield Pure("ignored")
            return (yield Get("counter"))

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        assert result.value == 10


class TestTryComposition:
    @pytest.mark.asyncio
    async def test_try_local_env_restored_on_error(self, parameterized_interpreter) -> None:
        @do
        def failing_in_local():
            modified = yield Ask("key")
            raise ValueError(f"failed with {modified}")

        @do
        def program():
            original = yield Ask("key")
            result = yield Try(Local({"key": "modified"}, failing_in_local()))
            after = yield Ask("key")
            return (original, result.is_err(), after)

        result = await parameterized_interpreter.run_async(program(), env={"key": "original"})
        assert result.is_ok
        original, is_err, after = result.value
        assert original == "original"
        assert is_err is True
        assert after == "original"

    @pytest.mark.asyncio
    async def test_try_put_state_persists_on_error(self, parameterized_interpreter) -> None:
        @do
        def increment_then_fail():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            raise ValueError("intentional failure")

        @do
        def program():
            yield Put("counter", 0)
            result = yield Try(increment_then_fail())
            counter = yield Get("counter")
            return (result.is_err(), counter)

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        is_err, counter = result.value
        assert is_err is True
        assert counter == 1

    @pytest.mark.asyncio
    async def test_nested_try_inner_catches_first(self, parameterized_interpreter) -> None:
        @do
        def failing_program():
            raise ValueError("inner error")

        @do
        def program():
            result = yield Try(Try(failing_program()))
            return result

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        outer_result = result.value
        assert outer_result.is_ok()
        inner_result = outer_result.value
        assert inner_result.is_err()
        assert isinstance(inner_result.error, ValueError)


class TestMaskOverrideComposition:
    @pytest.mark.asyncio
    async def test_mask_skips_next_matching_handler(self, parameterized_interpreter) -> None:
        def first_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield doeff_vm.Resume(k, f"first:{effect.label}"))
            delegated = yield doeff_vm.Delegate()
            return (yield doeff_vm.Resume(k, delegated))

        def second_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield doeff_vm.Resume(k, f"second:{effect.label}"))
            delegated = yield doeff_vm.Delegate()
            return (yield doeff_vm.Resume(k, delegated))

        @do
        def body():
            return (yield Ping("x"))

        wrapped = doeff_vm.WithHandler(
            first_handler,
            doeff_vm.WithHandler(second_handler, Mask([Ping], body())),
        )

        result = await parameterized_interpreter.run_async(wrapped)
        assert result.is_ok
        assert result.value == "first:x"

    @pytest.mark.asyncio
    async def test_override_replaces_outer_then_delegates(self, parameterized_interpreter) -> None:
        seen: list[str] = []

        def outer_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield doeff_vm.Resume(k, f"outer:{effect.label}"))
            delegated = yield doeff_vm.Delegate()
            return (yield doeff_vm.Resume(k, delegated))

        def override_handler(effect, k):
            if isinstance(effect, Ping):
                seen.append(effect.label)
                delegated = yield doeff_vm.Delegate()
                return (yield doeff_vm.Resume(k, f"override:{delegated}"))
            delegated = yield doeff_vm.Delegate()
            return (yield doeff_vm.Resume(k, delegated))

        @do
        def body():
            return (yield Ping("p"))

        wrapped = doeff_vm.WithHandler(
            outer_handler,
            Override(handler=override_handler, effect_types=[Ping], body=body()),
        )

        result = await parameterized_interpreter.run_async(wrapped)
        assert result.is_ok
        assert result.value == "override:outer:p"
        assert seen == ["p"]


class TestCombinedComposition:
    @pytest.mark.asyncio
    async def test_try_override_local_combined(self, parameterized_interpreter) -> None:
        def ask_override(effect, k):
            if isinstance(effect, AskEffect):
                return (yield doeff_vm.Resume(k, "intercepted"))
            delegated = yield doeff_vm.Delegate()
            return (yield doeff_vm.Resume(k, delegated))

        @do
        def inner_program():
            val = yield Ask("key")
            yield Put("result", val)
            return val

        @do
        def program():
            result = yield Try(
                Local(
                    {"key": "modified"},
                    Override(handler=ask_override, effect_types=[AskEffect], body=inner_program()),
                )
            )
            stored = yield Get("result")
            outer_key = yield Ask("key")
            return (result, stored, outer_key)

        result = await parameterized_interpreter.run_async(program(), env={"key": "original"})
        assert result.is_ok
        safe_result, stored, outer_key = result.value
        assert safe_result.is_ok()
        assert safe_result.value == "intercepted"
        assert stored == "intercepted"
        assert outer_key == "original"

    @pytest.mark.asyncio
    async def test_gather_with_safe_children(self, parameterized_interpreter) -> None:
        @do
        def may_fail(should_fail: bool):
            _ = yield Ask("_")
            if should_fail:
                raise ValueError("failed")
            return "success"

        @do
        def safe_task(should_fail: bool):
            return (yield Try(may_fail(should_fail)))

        @do
        def program():
            t1 = yield Spawn(safe_task(False))
            t2 = yield Spawn(safe_task(True))
            t3 = yield Spawn(safe_task(False))
            results = yield Gather(t1, t2, t3)
            return results

        result = await parameterized_interpreter.run_async(program(), env={"_": None})
        assert result.is_ok
        results = result.value
        assert len(results) == 3
        assert results[0].is_ok()
        assert results[0].value == "success"
        assert results[1].is_err()
        assert isinstance(results[1].error, ValueError)
        assert results[2].is_ok()
        assert results[2].value == "success"
