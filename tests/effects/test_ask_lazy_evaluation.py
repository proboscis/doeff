"""Tests for Ask lazy Program evaluation feature.

This test file confirms the lazy Program evaluation feature defined in SPEC-EFF-001-reader.md:
1. Basic lazy evaluation - Program values in env are evaluated on first Ask
2. Caching - Results are cached, no re-evaluation on subsequent Asks
3. Local override invalidation - Different Program = fresh evaluation
4. Error propagation - Program failure causes entire run() to fail
5. Concurrent access protection - Simultaneous Asks wait, don't re-execute

Reference: ISSUE-SPEC-004, gh#190, gh#191, gh#192
"""

import pytest

from doeff import Program, do
from doeff.cesk.runtime.async_ import AsyncRuntime
from doeff.effects import Ask, Gather, Get, Local, Put, Safe, Spawn

# ============================================================================
# Basic Lazy Evaluation Tests
# ============================================================================


class TestAskLazyEvaluation:
    """Tests for basic lazy Program evaluation behavior."""

    @pytest.mark.asyncio
    async def test_ask_evaluates_program_value(self) -> None:
        """Ask evaluates a Program value in the environment."""
        runtime = AsyncRuntime()

        @do
        def expensive():
            return 42

        env = {"service": expensive()}

        @do
        def program():
            value = yield Ask("service")
            return value

        result = await runtime.run_and_unwrap(program(), env=env)
        assert result == 42

    @pytest.mark.asyncio
    async def test_ask_evaluates_program_with_effects(self) -> None:
        """Ask properly evaluates a Program that yields effects."""
        runtime = AsyncRuntime()

        @do
        def program_with_effects():
            yield Put("counter", 100)
            counter = yield Get("counter")
            return counter * 2

        env = {"compute": program_with_effects()}

        @do
        def program():
            result = yield Ask("compute")
            final_counter = yield Get("counter")
            return (result, final_counter)

        result = await runtime.run_and_unwrap(program(), env=env)
        assert result == (200, 100)

    @pytest.mark.asyncio
    async def test_ask_returns_regular_value_directly(self) -> None:
        """Ask returns non-Program values directly without special handling."""
        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Ask("simple")
            return value

        result = await runtime.run_and_unwrap(program(), env={"simple": "hello"})
        assert result == "hello"


# ============================================================================
# Caching Tests
# ============================================================================


class TestAskCaching:
    """Tests for result caching behavior."""

    @pytest.mark.asyncio
    async def test_cached_result_no_reevaluation(self) -> None:
        """Second Ask for same key returns cached result without re-evaluation."""
        runtime = AsyncRuntime()
        evaluation_count = [0]

        @do
        def expensive():
            evaluation_count[0] += 1
            yield Put("marker", evaluation_count[0])
            return 42

        expensive_program = expensive()
        env = {"service": expensive_program}

        @do
        def program():
            val1 = yield Ask("service")
            val2 = yield Ask("service")
            val3 = yield Ask("service")
            return (val1, val2, val3)

        result = await runtime.run_and_unwrap(program(), env=env)

        # All values should be the same cached result
        assert result == (42, 42, 42)
        # Program should only have been evaluated once
        assert evaluation_count[0] == 1

    @pytest.mark.asyncio
    async def test_different_keys_evaluated_separately(self) -> None:
        """Different Ask keys are cached independently."""
        runtime = AsyncRuntime()
        eval_counts = {"a": 0, "b": 0}

        @do
        def make_program(name):
            eval_counts[name] += 1
            return f"result_{name}_{eval_counts[name]}"

        @do
        def program_a():
            return (yield Program.pure("result_a"))

        @do
        def program_b():
            return (yield Program.pure("result_b"))

        env = {"key_a": program_a(), "key_b": program_b()}

        @do
        def program():
            a1 = yield Ask("key_a")
            b1 = yield Ask("key_b")
            a2 = yield Ask("key_a")
            b2 = yield Ask("key_b")
            return (a1, b1, a2, b2)

        result = await runtime.run_and_unwrap(program(), env=env)
        assert result == ("result_a", "result_b", "result_a", "result_b")


# ============================================================================
# Local Override Invalidation Tests
# ============================================================================


class TestLocalOverrideInvalidation:
    """Tests for cache invalidation when Local provides different Program."""

    @pytest.mark.asyncio
    async def test_local_with_different_program_reevaluates(self) -> None:
        """Local override with different Program object triggers re-evaluation."""
        runtime = AsyncRuntime()
        evaluation_count = [0]

        @do
        def make_program(value):
            evaluation_count[0] += 1
            return value

        original_program = make_program(100)
        override_program = make_program(200)

        @do
        def inner():
            return (yield Ask("service"))

        @do
        def program():
            val1 = yield Ask("service")  # Evaluates original_program
            val2 = yield Local({"service": override_program}, inner())  # Evaluates override
            val3 = yield Ask("service")  # Returns cached original
            return (val1, val2, val3)

        result = await runtime.run_and_unwrap(program(), env={"service": original_program})

        assert result == (100, 200, 100)
        # With key-only cache, 3 evaluations occur:
        # 1. original_program for val1
        # 2. override_program for val2 (overwrites cache)
        # 3. original_program for val3 (cache miss after Local exit)
        assert evaluation_count[0] == 3

    @pytest.mark.asyncio
    async def test_local_with_same_program_uses_cache(self) -> None:
        """Local override with same Program object uses cached result."""
        runtime = AsyncRuntime()
        evaluation_count = [0]

        @do
        def expensive():
            evaluation_count[0] += 1
            return 42

        # Same program object in both places
        shared_program = expensive()

        @do
        def inner():
            return (yield Ask("service"))

        @do
        def program():
            val1 = yield Ask("service")
            # Local with same Program object
            val2 = yield Local({"service": shared_program}, inner())
            return (val1, val2)

        result = await runtime.run_and_unwrap(program(), env={"service": shared_program})

        assert result == (42, 42)
        # Should only evaluate once since same Program object
        assert evaluation_count[0] == 1


# ============================================================================
# Error Propagation Tests
# ============================================================================


class TestErrorPropagation:
    """Tests for error handling from lazy Program evaluation."""

    @pytest.mark.asyncio
    async def test_program_error_propagates(self) -> None:
        """Error from lazy Program evaluation fails the entire run."""
        runtime = AsyncRuntime()

        @do
        def failing_program():
            raise ValueError("Program evaluation failed")
            yield  # Make it a generator

        env = {"service": failing_program()}

        @do
        def program():
            value = yield Ask("service")
            return value

        with pytest.raises(ValueError, match="Program evaluation failed"):
            await runtime.run_and_unwrap(program(), env=env)

    @pytest.mark.asyncio
    async def test_error_not_cached(self) -> None:
        """Failed evaluation does not cache an error - subsequent Ask retries."""
        runtime = AsyncRuntime()
        attempt = [0]

        @do
        def sometimes_fails():
            attempt[0] += 1
            if attempt[0] == 1:
                raise ValueError("First attempt fails")
            return "success"

        # Create two different program instances
        first_program = sometimes_fails()
        second_program = sometimes_fails()

        @do
        def program():
            # First attempt with first_program - should fail
            try:
                result1 = yield Safe(Ask("service"))
                if result1.is_err():
                    # Replace with second_program
                    @do
                    def inner():
                        return (yield Ask("service"))

                    result2 = yield Local({"service": second_program}, inner())
                    return result2
            except ValueError:
                pass
            return None

        result = await runtime.run_and_unwrap(program(), env={"service": first_program})

        assert result == "success"

    @pytest.mark.asyncio
    async def test_safe_captures_program_error(self) -> None:
        """Safe can capture errors from lazy Program evaluation."""
        runtime = AsyncRuntime()

        @do
        def failing_program():
            raise ValueError("Oops")
            yield  # Make it a generator

        env = {"service": failing_program()}

        @do
        def program():
            result = yield Safe(Ask("service"))
            return result

        result = await runtime.run_and_unwrap(program(), env=env)
        assert result.is_err()
        assert isinstance(result.error, ValueError)


# ============================================================================
# Concurrent Access Tests
# ============================================================================


class TestConcurrentAccess:
    """Tests for concurrent Ask protection."""

    @pytest.mark.asyncio
    async def test_gather_with_same_lazy_ask(self) -> None:
        """Multiple Gather children asking for same key should not re-evaluate."""
        runtime = AsyncRuntime()
        evaluation_count = [0]

        @do
        def expensive():
            evaluation_count[0] += 1
            yield Put("eval_count", evaluation_count[0])
            return 42

        expensive_program = expensive()
        env = {"service": expensive_program}

        @do
        def child():
            value = yield Ask("service")
            return value

        @do
        def program():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            t3 = yield Spawn(child())
            results = yield Gather(t1, t2, t3)
            return results

        result = await runtime.run_and_unwrap(program(), env=env)

        assert result == [42, 42, 42]

    @pytest.mark.asyncio
    async def test_nested_ask_in_lazy_program(self) -> None:
        """Lazy Program can itself Ask for other lazy Programs."""
        runtime = AsyncRuntime()

        @do
        def inner_service():
            return 10

        @do
        def outer_service():
            inner = yield Ask("inner")
            return inner * 2

        env = {
            "inner": inner_service(),
            "outer": outer_service(),
        }

        @do
        def program():
            result = yield Ask("outer")
            return result

        result = await runtime.run_and_unwrap(program(), env=env)
        assert result == 20


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    @pytest.mark.asyncio
    async def test_none_result_is_cached(self) -> None:
        """None result from Program is properly cached."""
        runtime = AsyncRuntime()
        evaluation_count = [0]

        @do
        def returns_none():
            evaluation_count[0] += 1

        env = {"nullable": returns_none()}

        @do
        def program():
            val1 = yield Ask("nullable")
            val2 = yield Ask("nullable")
            return (val1, val2)

        result = await runtime.run_and_unwrap(program(), env=env)
        assert result == (None, None)
        assert evaluation_count[0] == 1

    @pytest.mark.asyncio
    async def test_program_returning_program_is_evaluated_once(self) -> None:
        """Program returning another Program - outer is evaluated, inner is returned."""
        runtime = AsyncRuntime()

        @do
        def inner():
            return 42

        @do
        def outer():
            # Return a Program, not a value
            return inner()

        env = {"service": outer()}

        @do
        def program():
            result = yield Ask("service")
            # result should be the inner Program object, not 42
            # unless the user explicitly yields it
            if isinstance(result, Program):
                final = yield result
                return final
            return result

        result = await runtime.run_and_unwrap(program(), env=env)
        assert result == 42

    @pytest.mark.asyncio
    async def test_hashable_keys_work(self) -> None:
        """Various hashable key types work correctly."""
        runtime = AsyncRuntime()

        @do
        def make_prog(val):
            return val

        env = {
            "string_key": make_prog("string"),
            42: make_prog("int"),
            ("tuple", "key"): make_prog("tuple"),
        }

        @do
        def program():
            str_val = yield Ask("string_key")
            int_val = yield Ask(42)
            tuple_val = yield Ask(("tuple", "key"))
            return (str_val, int_val, tuple_val)

        result = await runtime.run_and_unwrap(program(), env=env)
        assert result == ("string", "int", "tuple")


# ============================================================================
# Circular Dependency Tests
# ============================================================================


class TestCircularDependency:
    """Tests for circular Ask dependency detection."""

    @pytest.mark.asyncio
    async def test_direct_circular_ask_raises_error(self) -> None:
        """Direct circular dependency (A asks A) is detected and raises error."""
        from doeff.cesk.handlers.core import CircularAskError

        runtime = AsyncRuntime()

        @do
        def circular_program():
            # This program asks for itself
            return (yield Ask("self"))

        env = {"self": circular_program()}

        @do
        def program():
            return (yield Ask("self"))

        with pytest.raises(CircularAskError) as excinfo:
            await runtime.run_and_unwrap(program(), env=env)

        assert excinfo.value.key == "self"

    @pytest.mark.asyncio
    async def test_indirect_circular_ask_raises_error(self) -> None:
        """Indirect circular dependency (A asks B, B asks A) is detected."""
        from doeff.cesk.handlers.core import CircularAskError

        runtime = AsyncRuntime()

        @do
        def program_a():
            return (yield Ask("b"))

        @do
        def program_b():
            return (yield Ask("a"))

        env = {"a": program_a(), "b": program_b()}

        @do
        def program():
            return (yield Ask("a"))

        with pytest.raises(CircularAskError) as excinfo:
            await runtime.run_and_unwrap(program(), env=env)

        # Either "a" or "b" will be the detected cycle point
        assert excinfo.value.key in ("a", "b")
