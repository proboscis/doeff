"""Tests for Control effects composition rules (SPEC-EFF-004).

This module tests the composition behavior of Pure, Safe, and Intercept effects
as defined in specs/effects/SPEC-EFF-004-control.md.

Composition Rules Tested:
- Safe + Local: Environment restored even on caught error
- Safe + Put: State persists on caught error
- Nested Safe: Inner catches first
- Intercept + Intercept: Composition order
- Intercept + Gather: Scope rules

Reference: gh#177
"""

import pytest

from doeff import Intercept, Program, Spawn, do
from doeff.effects import (
    Ask,
    Gather,
    Get,
    Local,
    Pure,
    Put,
    Safe,
    Tell,
)
from doeff.effects.reader import AskEffect

_LOCAL_EFFECT_PENDING = pytest.mark.xfail(
    reason="LocalEffect is unhandled until ISSUE-CORE-505C",
    strict=False,
)

# ============================================================================
# Pure Effect Tests
# ============================================================================


class TestPureEffect:
    """Tests for Pure effect semantics."""

    @pytest.mark.asyncio
    async def test_pure_returns_value(self, parameterized_interpreter) -> None:
        """Pure effect returns its wrapped value."""

        @do
        def program():
            result = yield Pure(42)
            return result

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_pure_no_state_change(self, parameterized_interpreter) -> None:
        """Pure effect does not modify state."""

        @do
        def program():
            yield Put("counter", 10)
            yield Pure("ignored")
            return (yield Get("counter"))

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        assert result.value == 10


# ============================================================================
# Safe Effect Composition Tests
# ============================================================================


@_LOCAL_EFFECT_PENDING
class TestSafeLocalComposition:
    """Tests for Safe + Local composition: Environment restored even on caught error."""

    @pytest.mark.asyncio
    async def test_safe_local_env_restored_on_error(
        self, parameterized_interpreter
    ) -> None:
        """Environment is restored after Safe catches error in Local scope."""

        @do
        def failing_in_local():
            modified = yield Ask("key")
            raise ValueError(f"failed with {modified}")

        @do
        def program():
            original = yield Ask("key")
            result = yield Safe(Local({"key": "modified"}, failing_in_local()))
            after = yield Ask("key")
            return (original, result.is_err(), after)

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_ok
        original, is_err, after = result.value

        assert original == "original"
        assert is_err is True
        assert after == "original"  # Environment restored

    @pytest.mark.asyncio
    async def test_safe_local_env_restored_on_success(
        self, parameterized_interpreter
    ) -> None:
        """Environment is restored after Safe completes successfully in Local scope."""

        @do
        def success_in_local():
            modified = yield Ask("key")
            return modified

        @do
        def program():
            original = yield Ask("key")
            result = yield Safe(Local({"key": "modified"}, success_in_local()))
            after = yield Ask("key")
            return (original, result.value, after)

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_ok
        original, inner_result, after = result.value

        assert original == "original"
        assert inner_result == "modified"
        assert after == "original"  # Environment restored


class TestSafePutComposition:
    """Tests for Safe + Put composition: State persists on caught error."""

    @pytest.mark.asyncio
    async def test_safe_put_state_persists_on_error(
        self, parameterized_interpreter
    ) -> None:
        """State changes persist even when Safe catches an error."""

        @do
        def increment_then_fail():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            raise ValueError("intentional failure")

        @do
        def program():
            yield Put("counter", 0)
            result = yield Safe(increment_then_fail())
            counter = yield Get("counter")
            return (result.is_err(), counter)

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        is_err, counter = result.value

        assert is_err is True
        assert counter == 1  # State persisted despite error

    @pytest.mark.asyncio
    async def test_safe_put_multiple_changes_persist(
        self, parameterized_interpreter
    ) -> None:
        """Multiple state changes persist before error."""

        @do
        def multiple_puts_then_fail():
            yield Put("a", 1)
            yield Put("b", 2)
            yield Put("c", 3)
            raise ValueError("fail after puts")

        @do
        def program():
            result = yield Safe(multiple_puts_then_fail())
            a = yield Get("a")
            b = yield Get("b")
            c = yield Get("c")
            return (result.is_err(), a, b, c)

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        is_err, a, b, c = result.value

        assert is_err is True
        assert (a, b, c) == (1, 2, 3)  # All state changes persisted


class TestNestedSafe:
    """Tests for Nested Safe: Inner catches first."""

    @pytest.mark.asyncio
    async def test_nested_safe_inner_catches_first(
        self, parameterized_interpreter
    ) -> None:
        """Inner Safe catches exception, outer Safe sees Ok."""

        @do
        def failing_program():
            raise ValueError("inner error")

        @do
        def program():
            result = yield Safe(Safe(failing_program()))
            return result

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok

        # Outer Safe sees successful completion (Err value from inner)
        outer_result = result.value
        assert outer_result.is_ok()
        # Inner Safe caught the error
        inner_result = outer_result.value
        assert inner_result.is_err()
        assert isinstance(inner_result.error, ValueError)

    @pytest.mark.asyncio
    async def test_nested_safe_three_levels(
        self, parameterized_interpreter
    ) -> None:
        """Three levels of nesting: innermost catches."""

        @do
        def failing_program():
            raise ValueError("deep error")

        @do
        def program():
            result = yield Safe(Safe(Safe(failing_program())))
            return result

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok

        # Level 1 (outermost): Ok
        level1 = result.value
        assert level1.is_ok()
        # Level 2: Ok
        level2 = level1.value
        assert level2.is_ok()
        # Level 3 (innermost): Err
        level3 = level2.value
        assert level3.is_err()

    @pytest.mark.asyncio
    async def test_nested_safe_with_intermediate_success(
        self, parameterized_interpreter
    ) -> None:
        """Nested Safe where inner succeeds."""

        @do
        def success_program():
            return 42

        @do
        def program():
            result = yield Safe(Safe(success_program()))
            return result

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok

        # Both levels see success
        outer = result.value
        assert outer.is_ok()
        inner_result = outer.value
        assert inner_result.is_ok()
        assert inner_result.value == 42


# ============================================================================
# Intercept Composition Tests
# ============================================================================


class TestInterceptComposition:
    """Tests for Intercept + Intercept: Composition order."""

    @pytest.mark.asyncio
    async def test_intercept_first_non_none_wins(
        self, parameterized_interpreter
    ) -> None:
        """First transform that returns non-None wins."""

        def transform_f(e):
            if isinstance(e, AskEffect) and e.key == "key":
                return Program.pure("from_f")
            return None

        def transform_g(e):
            if isinstance(e, AskEffect) and e.key == "key":
                return Program.pure("from_g")
            return None

        @do
        def inner_program():
            return (yield Ask("key"))

        @do
        def program():
            # f is applied first (innermost), should win
            result = yield Intercept(inner_program(), transform_f, transform_g)
            return result

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_ok
        assert result.value == "from_f"  # f wins because it's checked first

    @pytest.mark.asyncio
    async def test_intercept_passthrough_to_next(
        self, parameterized_interpreter
    ) -> None:
        """Transform returning None passes to next transform."""

        def transform_f(e):
            # Only intercept AskEffect for "other_key"
            if isinstance(e, AskEffect) and e.key == "other_key":
                return Program.pure("from_f")
            return None  # Passthrough

        def transform_g(e):
            if isinstance(e, AskEffect) and e.key == "key":
                return Program.pure("from_g")
            return None

        @do
        def inner_program():
            return (yield Ask("key"))

        @do
        def program():
            # f passes through, g intercepts
            result = yield Intercept(inner_program(), transform_f, transform_g)
            return result

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_ok
        assert result.value == "from_g"  # g wins because f returns None

    @pytest.mark.asyncio
    async def test_intercept_all_passthrough(
        self, parameterized_interpreter
    ) -> None:
        """All transforms returning None uses original effect."""

        def transform_f(e):
            return None  # Always passthrough

        def transform_g(e):
            return None  # Always passthrough

        @do
        def inner_program():
            return (yield Ask("key"))

        @do
        def program():
            result = yield Intercept(inner_program(), transform_f, transform_g)
            return result

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_ok
        assert result.value == "original"  # Original effect executed

    @pytest.mark.asyncio
    async def test_intercept_returns_program(
        self, parameterized_interpreter
    ) -> None:
        """Transform returning Program executes that Program."""

        @do
        def replacement_program():
            yield Tell("replacement executed")
            return "from_replacement"

        def transform(e):
            if isinstance(e, AskEffect):
                return replacement_program()
            return None

        @do
        def inner_program():
            return (yield Ask("key"))

        @do
        def program():
            result = yield Intercept(inner_program(), transform)
            return result

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_ok
        assert result.value == "from_replacement"


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


class TestInterceptErrorHandling:
    """Tests for error handling in Intercept."""

    @pytest.mark.asyncio
    async def test_intercept_transform_exception_propagates(
        self, parameterized_interpreter
    ) -> None:
        """Exception in transform propagates as error result."""

        def bad_transform(e):
            raise RuntimeError("transform error")

        @do
        def inner_program():
            return (yield Ask("key"))

        @do
        def program():
            result = yield Intercept(inner_program(), bad_transform)
            return result

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_err()
        assert isinstance(result.error, RuntimeError)
        assert "transform error" in str(result.error)

    @pytest.mark.asyncio
    async def test_intercept_does_not_catch_errors(
        self, parameterized_interpreter
    ) -> None:
        """Intercept does not catch errors from the program."""

        def passthrough(e):
            return None

        @do
        def failing_program():
            yield Ask("key")
            raise ValueError("program error")

        @do
        def program():
            result = yield Intercept(failing_program(), passthrough)
            return result

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert "program error" in str(result.error)

    @pytest.mark.asyncio
    async def test_safe_with_intercept(
        self, parameterized_interpreter
    ) -> None:
        """Safe can catch errors from intercepted programs."""

        def passthrough(e):
            return None

        @do
        def failing_program():
            yield Ask("key")
            raise ValueError("caught error")

        @do
        def program():
            result = yield Safe(Intercept(failing_program(), passthrough))
            return result

        result = await parameterized_interpreter.run_async(
            program(), env={"key": "original"}
        )
        assert result.is_ok
        safe_result = result.value
        assert safe_result.is_err()
        assert isinstance(safe_result.error, ValueError)


# ============================================================================
# Combined Composition Tests
# ============================================================================


class TestCombinedComposition:
    """Tests for complex combinations of control effects."""

    @pytest.mark.asyncio
    @_LOCAL_EFFECT_PENDING
    async def test_safe_intercept_local_combined(
        self, parameterized_interpreter
    ) -> None:
        """Complex combination: Safe + Intercept + Local."""

        def intercept_ask(e):
            if isinstance(e, AskEffect):
                return Program.pure("intercepted")
            return None

        @do
        def inner_program():
            val = yield Ask("key")
            yield Put("result", val)
            return val

        @do
        def program():
            # Safe wraps Local wraps intercepted program
            result = yield Safe(
                Local(
                    {"key": "modified"},
                    Intercept(inner_program(), intercept_ask)
                )
            )
            stored = yield Get("result")
            outer_key = yield Ask("key")
            return (result, stored, outer_key)

        result = await parameterized_interpreter.run_async(
            program(),
            env={"key": "original"}
        )
        assert result.is_ok
        safe_result, stored, outer_key = result.value

        assert safe_result.is_ok()
        assert safe_result.value == "intercepted"  # Intercept worked
        assert stored == "intercepted"  # State persisted
        assert outer_key == "original"  # Environment restored

    @pytest.mark.asyncio
    async def test_gather_with_safe_children(
        self, parameterized_interpreter
    ) -> None:
        """Gather with Safe-wrapped children handles errors independently."""

        @do
        def may_fail(should_fail: bool):
            _ = yield Ask("_")
            if should_fail:
                raise ValueError("failed")
            return "success"

        @do
        def safe_task(should_fail: bool):
            return (yield Safe(may_fail(should_fail)))

        @do
        def program():
            t1 = yield Spawn(safe_task(False))
            t2 = yield Spawn(safe_task(True))
            t3 = yield Spawn(safe_task(False))
            results = yield Gather(t1, t2, t3)
            return results

        result = await parameterized_interpreter.run_async(
            program(), env={"_": None}
        )
        assert result.is_ok
        results = result.value

        assert len(results) == 3
        assert results[0].is_ok()
        assert results[0].value == "success"
        assert results[1].is_err()
        assert isinstance(results[1].error, ValueError)
        assert results[2].is_ok()
        assert results[2].value == "success"
