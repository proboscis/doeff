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

from doeff import MissingEnvKeyError, Program, do
from doeff.effects import (
    Ask,
    Gather,
    Get,
    Local,
    Modify,
    Put,
    Safe,
    Spawn,
)

pytestmark = pytest.mark.skip(
    reason=(
        "Legacy CESK-era reader/local semantics are not in the active rust_vm matrix; "
        "tracked by ISSUE-SPEC-009 migration/drop plan."
    )
)

# ============================================================================
# Ask on Missing Key Tests
# ============================================================================


class TestAskMissingKey:
    """Tests for Ask behavior when key is missing from environment."""

    @pytest.mark.asyncio
    async def test_ask_missing_key_raises_missing_env_key_error(
        self, parameterized_interpreter
    ) -> None:
        """Ask raises MissingEnvKeyError when key is not in environment."""

        @do
        def program():
            value = yield Ask("missing_key")
            return value

        result = await parameterized_interpreter.run_async(program(), env={})

        assert result.is_err()
        assert isinstance(result.error, MissingEnvKeyError)
        assert result.error.key == "missing_key"
        assert "missing_key" in str(result.error)

    @pytest.mark.asyncio
    async def test_ask_missing_key_error_has_helpful_message(
        self, parameterized_interpreter
    ) -> None:
        """MissingEnvKeyError includes helpful hints for the user."""

        @do
        def program():
            value = yield Ask("config.database.host")
            return value

        result = await parameterized_interpreter.run_async(program(), env={})

        assert result.is_err()
        error_message = str(result.error)
        assert "config.database.host" in error_message
        assert "Hint:" in error_message

    @pytest.mark.asyncio
    async def test_missing_env_key_error_is_key_error(self, parameterized_interpreter) -> None:
        """MissingEnvKeyError is a KeyError subclass for backwards compatibility."""

        @do
        def program():
            value = yield Ask("missing")
            return value

        result = await parameterized_interpreter.run_async(program(), env={})

        assert result.is_err()
        assert isinstance(result.error, KeyError)

    @pytest.mark.asyncio
    async def test_ask_existing_key_succeeds(self, parameterized_interpreter) -> None:
        """Ask returns value when key exists in environment."""

        @do
        def program():
            value = yield Ask("key")
            return value

        result = await parameterized_interpreter.run_async(program(), env={"key": "value"})
        assert result.is_ok
        assert result.value == "value"

    @pytest.mark.asyncio
    async def test_ask_none_value_succeeds(self, parameterized_interpreter) -> None:
        """Ask returns None when key exists with None value (not missing)."""

        @do
        def program():
            value = yield Ask("nullable_key")
            return value

        result = await parameterized_interpreter.run_async(program(), env={"nullable_key": None})
        assert result.is_ok
        assert result.value is None


# ============================================================================
# Local + Ask Composition Tests
# ============================================================================


class TestLocalAskComposition:
    """Tests for Local + Ask composition: Ask sees override, restored after."""

    @pytest.mark.asyncio
    async def test_local_overrides_ask_inside_scope(self, parameterized_interpreter) -> None:
        """Ask inside Local sees the overridden value."""

        @do
        def inner_program():
            value = yield Ask("key")
            return value

        @do
        def program():
            inner_value = yield Local({"key": "overridden"}, inner_program())
            return inner_value

        result = await parameterized_interpreter.run_async(program(), env={"key": "original"})
        assert result.is_ok
        assert result.value == "overridden"

    @pytest.mark.asyncio
    async def test_local_restores_env_after_scope(self, parameterized_interpreter) -> None:
        """Ask after Local sees the original value (env restored)."""

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

        result = await parameterized_interpreter.run_async(program(), env={"key": "original"})
        assert result.is_ok
        assert result.value == ("original", "overridden", "original")

    @pytest.mark.asyncio
    async def test_local_adds_new_key(self, parameterized_interpreter) -> None:
        """Local can add a new key not present in parent environment."""

        @do
        def inner_program():
            value = yield Ask("new_key")
            return value

        @do
        def program():
            inner_value = yield Local({"new_key": "new_value"}, inner_program())
            return inner_value

        result = await parameterized_interpreter.run_async(program(), env={"other_key": "other"})
        assert result.is_ok
        assert result.value == "new_value"

    @pytest.mark.asyncio
    async def test_local_new_key_not_visible_after(self, parameterized_interpreter) -> None:
        """Key added by Local is not visible after Local completes."""

        @do
        def inner_program():
            return "done"

        @do
        def program():
            yield Local({"new_key": "value"}, inner_program())
            value = yield Ask("new_key")
            return value

        result = await parameterized_interpreter.run_async(program(), env={})
        assert result.is_err()
        assert isinstance(result.error, MissingEnvKeyError)
        assert result.error.key == "new_key"

    @pytest.mark.asyncio
    async def test_local_preserves_unrelated_keys(self, parameterized_interpreter) -> None:
        """Local override doesn't affect other keys in environment."""

        @do
        def inner_program():
            key1 = yield Ask("key1")
            key2 = yield Ask("key2")
            return (key1, key2)

        @do
        def program():
            result = yield Local({"key1": "overridden"}, inner_program())
            return result

        result = await parameterized_interpreter.run_async(
            program(), env={"key1": "original1", "key2": "original2"}
        )
        assert result.is_ok
        assert result.value == ("overridden", "original2")


# ============================================================================
# Local + Local (Nested) Composition Tests
# ============================================================================


class TestLocalLocalComposition:
    """Tests for nested Local: Inner overrides outer, both restore."""

    @pytest.mark.asyncio
    async def test_nested_local_inner_overrides_outer(self, parameterized_interpreter) -> None:
        """Inner Local overrides same key from outer Local."""

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

        result = await parameterized_interpreter.run_async(program(), env={"key": "original"})
        assert result.is_ok
        assert result.value == "inner"

    @pytest.mark.asyncio
    async def test_nested_local_both_restore(self, parameterized_interpreter) -> None:
        """Both inner and outer Local restore their environments."""

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

        result = await parameterized_interpreter.run_async(program(), env={"key": "original"})
        assert result.is_ok
        before_outer, (before_inner, inner, after_inner), after_outer = result.value

        assert before_outer == "original"
        assert before_inner == "outer"
        assert inner == "inner"
        assert after_inner == "outer"
        assert after_outer == "original"

    @pytest.mark.asyncio
    async def test_nested_local_different_keys(self, parameterized_interpreter) -> None:
        """Nested Local with different keys both visible."""

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

        result = await parameterized_interpreter.run_async(
            program(), env={"key1": "orig1", "key2": "orig2"}
        )
        assert result.is_ok
        assert result.value == ("outer1", "inner2")


# ============================================================================
# Local + State Interaction Tests
# ============================================================================


class TestLocalStateInteraction:
    """Tests for Local + State: State changes inside Local persist outside."""

    @pytest.mark.asyncio
    async def test_state_changes_persist_outside_local(self, parameterized_interpreter) -> None:
        """State (Put) changes made inside Local persist after Local completes."""

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

        result = await parameterized_interpreter.run_async(program(), state={"counter": 0})
        assert result.is_ok
        assert result.value == (0, 42)

    @pytest.mark.asyncio
    async def test_state_modify_persists_outside_local(self, parameterized_interpreter) -> None:
        """State (Modify) changes made inside Local persist after Local."""

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

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        assert result.value == (15, 15)

    @pytest.mark.asyncio
    async def test_env_and_state_independent(self, parameterized_interpreter) -> None:
        """Local scopes env (Ask) but NOT state (Get/Put)."""

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

        result = await parameterized_interpreter.run_async(
            program(), env={"env_key": "original_env"}
        )
        assert result.is_ok
        before_env, (inner_env, inner_state), after_env, after_state = result.value

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
    async def test_gather_children_inherit_parent_env(self, parameterized_interpreter) -> None:
        """All Gather children inherit the parent's environment."""

        @do
        def child():
            value = yield Ask("shared_key")
            return value

        @do
        def program():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            t3 = yield Spawn(child())
            results = yield Gather(t1, t2, t3)
            return results

        result = await parameterized_interpreter.run_async(
            program(), env={"shared_key": "shared_value"}
        )
        assert result.is_ok
        assert result.value == ["shared_value", "shared_value", "shared_value"]

    @pytest.mark.asyncio
    async def test_gather_children_inherit_local_override(self, parameterized_interpreter) -> None:
        """Gather children inside Local inherit the overridden env."""

        @do
        def child():
            value = yield Ask("key")
            return value

        @do
        def gather_children():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            return (yield Gather(t1, t2))

        @do
        def program():
            results = yield Local({"key": "from_local"}, gather_children())
            return results

        result = await parameterized_interpreter.run_async(program(), env={"key": "original"})
        assert result.is_ok
        assert result.value == ["from_local", "from_local"]

    @pytest.mark.asyncio
    async def test_child_local_does_not_affect_siblings(self, parameterized_interpreter) -> None:
        """Local in one Gather child doesn't affect sibling children."""

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
            t1 = yield Spawn(child_with_local())
            t2 = yield Spawn(child_normal())
            t3 = yield Spawn(child_normal())
            results = yield Gather(t1, t2, t3)
            return results

        result = await parameterized_interpreter.run_async(program(), env={"key": "parent_value"})
        assert result.is_ok

        assert result.value[0] == "local_child:child_override"
        assert result.value[1] == "normal_child:parent_value"
        assert result.value[2] == "normal_child:parent_value"

    @pytest.mark.asyncio
    async def test_env_restored_after_gather(self, parameterized_interpreter) -> None:
        """Parent env restored after Gather completes (no child pollution)."""

        @do
        def child_with_local():
            @do
            def inner():
                return (yield Ask("key"))

            return (yield Local({"key": "child_value"}, inner()))

        @do
        def program():
            before = yield Ask("key")
            t1 = yield Spawn(child_with_local())
            t2 = yield Spawn(child_with_local())
            results = yield Gather(t1, t2)
            after = yield Ask("key")
            return (before, results, after)

        result = await parameterized_interpreter.run_async(program(), env={"key": "parent_value"})
        assert result.is_ok
        before, children_results, after = result.value

        assert before == "parent_value"
        assert children_results == ["child_value", "child_value"]
        assert after == "parent_value"

    @pytest.mark.asyncio
    async def test_gather_state_is_isolated_not_shared(self, parameterized_interpreter) -> None:
        """State changes in spawned Gather children are NOT visible to each other.

        With Spawn + Gather, each task has isolated state.
        """

        @do
        def child_increment():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            return current

        @do
        def program():
            yield Put("counter", 0)
            t1 = yield Spawn(child_increment())
            t2 = yield Spawn(child_increment())
            t3 = yield Spawn(child_increment())
            results = yield Gather(t1, t2, t3)
            final = yield Get("counter")
            return (results, final)

        result = await parameterized_interpreter.run_async(program())
        assert result.is_ok
        results, final = result.value

        assert final == 0
        assert results == [0, 0, 0]
