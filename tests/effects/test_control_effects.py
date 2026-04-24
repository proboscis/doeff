"""Tests for Control effects composition rules (SPEC-EFF-004).

This module tests the composition behavior of Pure, Try, and WithIntercept effects
as defined in specs/effects/SPEC-EFF-004-control.md.

Composition Rules Tested:
- Try + Local: Environment restored even on caught error
- Try + Put: State persists on caught error
- Nested Try: Inner catches first
- WithIntercept: Composition and transform behavior
- WithIntercept + Gather: Scope rules

Reference: gh#177
"""

import pytest

from doeff import Program, Spawn, WithIntercept, do
from tests._run_helpers import run_with_defaults
from doeff import (
    Ask,
    Gather,
    Get,
    Local,
    Pure,
    Put,
    Try,
)
from doeff_core_effects.effects import Ask


def _with_legacy_intercept_chain(program: Program, *transforms):
    """Model legacy transform-chain behavior on top of WithIntercept in tests."""
    if not transforms:
        raise ValueError("Intercept requires at least one transform function")

    @do
    def intercept(effect):
        for transform in transforms:
            candidate = transform(effect)
            if candidate is not None:
                return candidate
        return effect

    return WithIntercept(intercept, program)

# ============================================================================
# Pure Effect Tests
# ============================================================================


class TestPureEffect:
    """Tests for Pure effect semantics."""

    def test_pure_returns_value(self) -> None:
        """Pure effect returns its wrapped value."""

        @do
        def program():
            result = yield Pure(42)
            return result

        result = run_with_defaults(program())
        assert result.is_ok()
        assert result.value == 42

    def test_pure_no_state_change(self) -> None:
        """Pure effect does not modify state."""

        @do
        def program():
            yield Put("counter", 10)
            yield Pure("ignored")
            return (yield Get("counter"))

        result = run_with_defaults(program())
        assert result.is_ok()
        assert result.value == 10


# ============================================================================
# Try Effect Composition Tests
# ============================================================================


class TestSafeLocalComposition:
    """Tests for Try + Local composition: Environment restored even on caught error."""

    def test_safe_local_env_restored_on_error(self) -> None:
        """Environment is restored after Try catches error in Local scope."""

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

        result = run_with_defaults(program(), env={"key": "original"})
        assert result.is_ok()
        original, is_err, after = result.value

        assert original == "original"
        assert is_err is True
        assert after == "original"  # Environment restored

    def test_safe_local_env_restored_on_success(self) -> None:
        """Environment is restored after Try completes successfully in Local scope."""

        @do
        def success_in_local():
            modified = yield Ask("key")
            return modified

        @do
        def program():
            original = yield Ask("key")
            result = yield Try(Local({"key": "modified"}, success_in_local()))
            after = yield Ask("key")
            return (original, result.value, after)

        result = run_with_defaults(program(), env={"key": "original"})
        assert result.is_ok()
        original, inner_result, after = result.value

        assert original == "original"
        assert inner_result == "modified"
        assert after == "original"  # Environment restored


class TestSafePutComposition:
    """Tests for Try + Put composition: State persists on caught error."""

    def test_safe_put_state_persists_on_error(self) -> None:
        """State changes persist even when Try catches an error."""

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

        result = run_with_defaults(program())
        assert result.is_ok()
        is_err, counter = result.value

        assert is_err is True
        assert counter == 1  # State persisted despite error

    def test_safe_put_multiple_changes_persist(self) -> None:
        """Multiple state changes persist before error."""

        @do
        def multiple_puts_then_fail():
            yield Put("a", 1)
            yield Put("b", 2)
            yield Put("c", 3)
            raise ValueError("fail after puts")

        @do
        def program():
            result = yield Try(multiple_puts_then_fail())
            a = yield Get("a")
            b = yield Get("b")
            c = yield Get("c")
            return (result.is_err(), a, b, c)

        result = run_with_defaults(program())
        assert result.is_ok()
        is_err, a, b, c = result.value

        assert is_err is True
        assert (a, b, c) == (1, 2, 3)  # All state changes persisted


class TestNestedSafe:
    """Tests for Nested Try: Inner catches first."""

    def test_nested_safe_inner_catches_first(self) -> None:
        """Inner Try catches exception, outer Try sees Ok."""

        @do
        def failing_program():
            raise ValueError("inner error")

        @do
        def program():
            result = yield Try(Try(failing_program()))
            return result

        result = run_with_defaults(program())
        assert result.is_ok()

        # Outer Try sees successful completion (Err value from inner)
        outer_result = result.value
        assert outer_result.is_ok()
        # Inner Try caught the error
        inner_result = outer_result.value
        assert inner_result.is_err()
        assert isinstance(inner_result.error, ValueError)

    def test_nested_safe_three_levels(self) -> None:
        """Three levels of nesting: innermost catches."""

        @do
        def failing_program():
            raise ValueError("deep error")

        @do
        def program():
            result = yield Try(Try(Try(failing_program())))
            return result

        result = run_with_defaults(program())
        assert result.is_ok()

        # Level 1 (outermost): Ok
        level1 = result.value
        assert level1.is_ok()
        # Level 2: Ok
        level2 = level1.value
        assert level2.is_ok()
        # Level 3 (innermost): Err
        level3 = level2.value
        assert level3.is_err()

    def test_nested_safe_with_intermediate_success(self) -> None:
        """Nested Try where inner succeeds."""

        @do
        def success_program():
            return 42

        @do
        def program():
            result = yield Try(Try(success_program()))
            return result

        result = run_with_defaults(program())
        assert result.is_ok()

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
    """Tests for legacy transform-chain semantics via WithIntercept."""


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


class TestInterceptErrorHandling:
    """Tests for error handling in scoped interception."""


# ============================================================================
# Combined Composition Tests
# ============================================================================


class TestCombinedComposition:
    """Tests for complex combinations of control effects."""

    def test_gather_with_safe_children(self) -> None:
        """Gather with Try-wrapped children handles errors independently."""

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

        result = run_with_defaults(program(), env={"_": None})
        assert result.is_ok()
        results = result.value

        assert len(results) == 3
        assert results[0].is_ok()
        assert results[0].value == "success"
        assert results[1].is_err()
        assert isinstance(results[1].error, ValueError)
        assert results[2].is_ok()
        assert results[2].value == "success"
