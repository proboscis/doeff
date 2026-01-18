"""Tests for Reader effects (Ask, Local) semantics and composition.

This test file confirms the semantics defined in SPEC-EFF-001-reader.md:
1. Ask on missing key behavior
2. Local + Ask composition
3. Local + Local (nested) composition
4. Local + Safe composition
5. Local + State interaction
6. Local + Gather child isolation

Reference: gh#174
"""

import pytest

from doeff import do, Program
from doeff.cesk.errors import MissingEnvKeyError
from doeff.cesk.runtime.async_ import AsyncRuntime
from doeff.effects import (
    Ask,
    Get,
    Gather,
    Local,
    Modify,
    Put,
    Safe,
)


# ============================================================================
# Ask on Missing Key Tests
# ============================================================================


class TestAskMissingKey:
    """Tests for Ask behavior when key is missing from environment."""

    @pytest.mark.asyncio
    async def test_ask_missing_key_raises_missing_env_key_error(self) -> None:
        """Ask raises MissingEnvKeyError when key is not in environment."""
        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Ask("missing_key")
            return value

        with pytest.raises(MissingEnvKeyError) as excinfo:
            await runtime.run(program(), env={})

        assert excinfo.value.key == "missing_key"
        assert "missing_key" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_ask_missing_key_error_has_helpful_message(self) -> None:
        """MissingEnvKeyError includes helpful hints for the user."""
        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Ask("config.database.host")
            return value

        with pytest.raises(MissingEnvKeyError) as excinfo:
            await runtime.run(program(), env={})

        error_message = str(excinfo.value)
        assert "config.database.host" in error_message
        assert "Hint:" in error_message

    @pytest.mark.asyncio
    async def test_missing_env_key_error_is_key_error(self) -> None:
        """MissingEnvKeyError is a KeyError subclass for backwards compatibility."""
        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Ask("missing")
            return value

        with pytest.raises(KeyError):
            await runtime.run(program(), env={})

    @pytest.mark.asyncio
    async def test_ask_existing_key_succeeds(self) -> None:
        """Ask returns value when key exists in environment."""
        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Ask("key")
            return value

        result = await runtime.run(program(), env={"key": "value"})
        assert result == "value"

    @pytest.mark.asyncio
    async def test_ask_none_value_succeeds(self) -> None:
        """Ask returns None when key exists with None value (not missing)."""
        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Ask("nullable_key")
            return value

        result = await runtime.run(program(), env={"nullable_key": None})
        assert result is None


# ============================================================================
# Local + Ask Composition Tests
# ============================================================================


class TestLocalAskComposition:
    """Tests for Local + Ask composition: Ask sees override, restored after."""

    @pytest.mark.asyncio
    async def test_local_overrides_ask_inside_scope(self) -> None:
        """Ask inside Local sees the overridden value."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            value = yield Ask("key")
            return value

        @do
        def program():
            inner_value = yield Local({"key": "overridden"}, inner_program())
            return inner_value

        result = await runtime.run(program(), env={"key": "original"})
        assert result == "overridden"

    @pytest.mark.asyncio
    async def test_local_restores_env_after_scope(self) -> None:
        """Ask after Local sees the original value (env restored)."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            value = yield Ask("key")
            return value

        @do
        def program():
            before = yield Ask("key")
            inner = yield Local({"key": "overridden"}, inner_program())
            after = yield Ask("key")
            return (before, inner, after)

        result = await runtime.run(program(), env={"key": "original"})
        assert result == ("original", "overridden", "original")

    @pytest.mark.asyncio
    async def test_local_adds_new_key(self) -> None:
        """Local can add a new key not present in parent environment."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            value = yield Ask("new_key")
            return value

        @do
        def program():
            inner_value = yield Local({"new_key": "new_value"}, inner_program())
            return inner_value

        result = await runtime.run(program(), env={"other_key": "other"})
        assert result == "new_value"

    @pytest.mark.asyncio
    async def test_local_new_key_not_visible_after(self) -> None:
        """Key added by Local is not visible after Local completes."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            return "done"

        @do
        def program():
            yield Local({"new_key": "value"}, inner_program())
            value = yield Ask("new_key")
            return value

        with pytest.raises(MissingEnvKeyError) as excinfo:
            await runtime.run(program(), env={})
        assert excinfo.value.key == "new_key"

    @pytest.mark.asyncio
    async def test_local_preserves_unrelated_keys(self) -> None:
        """Local override doesn't affect other keys in environment."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            key1 = yield Ask("key1")
            key2 = yield Ask("key2")
            return (key1, key2)

        @do
        def program():
            result = yield Local({"key1": "overridden"}, inner_program())
            return result

        result = await runtime.run(program(), env={"key1": "original1", "key2": "original2"})
        assert result == ("overridden", "original2")


# ============================================================================
# Local + Local (Nested) Composition Tests
# ============================================================================


class TestLocalLocalComposition:
    """Tests for nested Local: Inner overrides outer, both restore."""

    @pytest.mark.asyncio
    async def test_nested_local_inner_overrides_outer(self) -> None:
        """Inner Local overrides same key from outer Local."""
        runtime = AsyncRuntime()

        @do
        def innermost():
            value = yield Ask("key")
            return value

        @do
        def middle():
            inner_value = yield Local({"key": "inner"}, innermost())
            return inner_value

        @do
        def program():
            result = yield Local({"key": "outer"}, middle())
            return result

        result = await runtime.run(program(), env={"key": "original"})
        assert result == "inner"

    @pytest.mark.asyncio
    async def test_nested_local_both_restore(self) -> None:
        """Both inner and outer Local restore their environments."""
        runtime = AsyncRuntime()

        @do
        def innermost():
            return (yield Ask("key"))

        @do
        def middle():
            before_inner = yield Ask("key")
            inner = yield Local({"key": "inner"}, innermost())
            after_inner = yield Ask("key")
            return (before_inner, inner, after_inner)

        @do
        def program():
            before_outer = yield Ask("key")
            outer_result = yield Local({"key": "outer"}, middle())
            after_outer = yield Ask("key")
            return (before_outer, outer_result, after_outer)

        result = await runtime.run(program(), env={"key": "original"})
        before_outer, (before_inner, inner, after_inner), after_outer = result

        assert before_outer == "original"
        assert before_inner == "outer"
        assert inner == "inner"
        assert after_inner == "outer"
        assert after_outer == "original"

    @pytest.mark.asyncio
    async def test_nested_local_different_keys(self) -> None:
        """Nested Local with different keys both visible."""
        runtime = AsyncRuntime()

        @do
        def innermost():
            key1 = yield Ask("key1")
            key2 = yield Ask("key2")
            return (key1, key2)

        @do
        def middle():
            result = yield Local({"key2": "inner2"}, innermost())
            return result

        @do
        def program():
            result = yield Local({"key1": "outer1"}, middle())
            return result

        result = await runtime.run(program(), env={"key1": "orig1", "key2": "orig2"})
        assert result == ("outer1", "inner2")


# ============================================================================
# Local + Safe Composition Tests
# ============================================================================


class TestLocalSafeComposition:
    """Tests for Local + Safe: Env restored even when Safe catches."""

    @pytest.mark.asyncio
    async def test_local_env_restored_on_safe_success(self) -> None:
        """Env is restored after Local when Safe catches success."""
        runtime = AsyncRuntime()

        @do
        def inner():
            value = yield Ask("key")
            return value

        @do
        def program():
            before = yield Ask("key")
            safe_result = yield Safe(Local({"key": "in_local"}, inner()))
            after = yield Ask("key")
            return (before, safe_result.value if safe_result.is_ok() else None, after)

        result = await runtime.run(program(), env={"key": "original"})
        assert result == ("original", "in_local", "original")

    @pytest.mark.asyncio
    async def test_local_env_restored_on_safe_error(self) -> None:
        """Env is restored after Local even when Safe catches an error."""
        runtime = AsyncRuntime()

        @do
        def failing_inner():
            yield Ask("key")  # Access modified env
            raise ValueError("intentional error")

        @do
        def program():
            before = yield Ask("key")
            safe_result = yield Safe(Local({"key": "in_local"}, failing_inner()))
            after = yield Ask("key")
            is_error = safe_result.is_err()
            return (before, is_error, after)

        result = await runtime.run(program(), env={"key": "original"})
        assert result == ("original", True, "original")

    @pytest.mark.asyncio
    async def test_safe_inside_local_env_still_restored(self) -> None:
        """Safe inside Local: Local env still restored after completion."""
        runtime = AsyncRuntime()

        @do
        def inner_with_safe():
            # Safe catches error, but Local should still restore env
            result = yield Safe(Program.pure(42))
            value = yield Ask("key")
            return (result, value)

        @do
        def program():
            before = yield Ask("key")
            result = yield Local({"key": "in_local"}, inner_with_safe())
            after = yield Ask("key")
            return (before, result, after)

        result = await runtime.run(program(), env={"key": "original"})
        before, (safe_result, inner_value), after = result
        assert before == "original"
        assert safe_result.is_ok() and safe_result.value == 42
        assert inner_value == "in_local"
        assert after == "original"


# ============================================================================
# Local + State Interaction Tests
# ============================================================================


class TestLocalStateInteraction:
    """Tests for Local + State: State changes inside Local persist outside."""

    @pytest.mark.asyncio
    async def test_state_changes_persist_outside_local(self) -> None:
        """State (Put) changes made inside Local persist after Local completes."""
        runtime = AsyncRuntime()

        @do
        def inner():
            yield Put("counter", 42)
            return "done"

        @do
        def program():
            before = yield Get("counter")
            yield Local({"key": "value"}, inner())
            after = yield Get("counter")
            return (before, after)

        result = await runtime.run(program(), store={"counter": 0})
        assert result == (0, 42)

    @pytest.mark.asyncio
    async def test_state_modify_persists_outside_local(self) -> None:
        """State (Modify) changes made inside Local persist after Local."""
        runtime = AsyncRuntime()

        @do
        def inner():
            new_val = yield Modify("counter", lambda x: x + 10)
            return new_val

        @do
        def program():
            yield Put("counter", 5)
            inner_result = yield Local({"key": "value"}, inner())
            after = yield Get("counter")
            return (inner_result, after)

        result = await runtime.run(program())
        assert result == (15, 15)

    @pytest.mark.asyncio
    async def test_env_and_state_independent(self) -> None:
        """Local scopes env (Ask) but NOT state (Get/Put)."""
        runtime = AsyncRuntime()

        @do
        def inner():
            env_val = yield Ask("env_key")
            state_val = yield Get("state_key")
            yield Put("state_key", "modified_by_inner")
            return (env_val, state_val)

        @do
        def program():
            yield Put("state_key", "original_state")
            before_env = yield Ask("env_key")
            result = yield Local({"env_key": "overridden_env"}, inner())
            after_env = yield Ask("env_key")
            after_state = yield Get("state_key")
            return (before_env, result, after_env, after_state)

        result = await runtime.run(program(), env={"env_key": "original_env"})
        before_env, (inner_env, inner_state), after_env, after_state = result

        # Env is scoped by Local
        assert before_env == "original_env"
        assert inner_env == "overridden_env"
        assert after_env == "original_env"

        # State is NOT scoped - persists outside Local
        assert inner_state == "original_state"
        assert after_state == "modified_by_inner"


# ============================================================================
# Local + Gather Composition Tests
# ============================================================================


class TestLocalGatherComposition:
    """Tests for Local + Gather: Children inherit, child's Local isolated."""

    @pytest.mark.asyncio
    async def test_gather_children_inherit_parent_env(self) -> None:
        """All Gather children inherit the parent's environment."""
        runtime = AsyncRuntime()

        @do
        def child():
            value = yield Ask("shared_key")
            return value

        @do
        def program():
            results = yield Gather(child(), child(), child())
            return results

        result = await runtime.run(program(), env={"shared_key": "shared_value"})
        assert result == ["shared_value", "shared_value", "shared_value"]

    @pytest.mark.asyncio
    async def test_gather_children_inherit_local_override(self) -> None:
        """Gather children inside Local inherit the overridden env."""
        runtime = AsyncRuntime()

        @do
        def child():
            value = yield Ask("key")
            return value

        @do
        def gather_children():
            results = yield Gather(child(), child())
            return results

        @do
        def program():
            results = yield Local({"key": "from_local"}, gather_children())
            return results

        result = await runtime.run(program(), env={"key": "original"})
        assert result == ["from_local", "from_local"]

    @pytest.mark.asyncio
    async def test_child_local_does_not_affect_siblings(self) -> None:
        """Local in one Gather child doesn't affect sibling children."""
        runtime = AsyncRuntime()

        @do
        def child_with_local():
            # This child overrides its own env
            @do
            def inner():
                return (yield Ask("key"))

            result = yield Local({"key": "child_override"}, inner())
            return f"local_child:{result}"

        @do
        def child_normal():
            value = yield Ask("key")
            return f"normal_child:{value}"

        @do
        def program():
            results = yield Gather(
                child_with_local(),
                child_normal(),
                child_normal(),
            )
            return results

        result = await runtime.run(program(), env={"key": "parent_value"})

        # Child with Local sees its override
        assert result[0] == "local_child:child_override"
        # Other children see parent value (not affected by sibling's Local)
        assert result[1] == "normal_child:parent_value"
        assert result[2] == "normal_child:parent_value"

    @pytest.mark.asyncio
    async def test_env_restored_after_gather(self) -> None:
        """Parent env restored after Gather completes (no child pollution)."""
        runtime = AsyncRuntime()

        @do
        def child_with_local():
            @do
            def inner():
                return (yield Ask("key"))

            return (yield Local({"key": "child_value"}, inner()))

        @do
        def program():
            before = yield Ask("key")
            results = yield Gather(child_with_local(), child_with_local())
            after = yield Ask("key")
            return (before, results, after)

        result = await runtime.run(program(), env={"key": "parent_value"})
        before, children_results, after = result

        assert before == "parent_value"
        assert children_results == ["child_value", "child_value"]
        assert after == "parent_value"

    @pytest.mark.asyncio
    async def test_gather_state_is_shared_not_isolated(self) -> None:
        """State changes in Gather children ARE visible to each other.

        Note: This confirms that Local scopes env only, not state.
        Gather children share the same store.
        """
        runtime = AsyncRuntime()

        @do
        def child_increment():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            return current

        @do
        def program():
            yield Put("counter", 0)
            results = yield Gather(
                child_increment(),
                child_increment(),
                child_increment(),
            )
            final = yield Get("counter")
            return (sorted(results), final)

        result = await runtime.run(program())
        sorted_results, final = result

        # All children contribute to the shared counter
        assert final == 3
        # Results show the counter values each child saw
        assert sorted_results == [0, 1, 2]
