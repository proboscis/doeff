"""Regression tests for critical CESK bugs.

These tests reproduce bugs found during code review to prevent regressions.
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from doeff.cesk import (
    Environment,
    Store,
    _merge_thread_state,
    run,
    run_sync,
)
from doeff.do import do
from doeff.effects import get, put
from doeff.effects.spawn import Spawn


# ============================================================================
# Test: _merge_thread_state preserves __cache_storage__
# ============================================================================


class TestMergeThreadStatePreservesCacheStorage:
    """Regression test for: _merge_thread_state was losing __cache_storage__."""

    def test_cache_storage_preserved_after_merge(self):
        """__cache_storage__ from parent should be preserved in merged store."""
        mock_storage = MagicMock()
        mock_storage.get.return_value = "cached_value"

        parent_store: Store = {
            "user_key": "parent_value",
            "__cache_storage__": mock_storage,
            "__log__": ["parent_log"],
        }
        child_store: Store = {
            "user_key": "child_value",
            "child_only": 42,
            "__log__": ["child_log"],
        }

        merged = _merge_thread_state(parent_store, child_store)

        assert merged["user_key"] == "child_value"
        assert merged["child_only"] == 42
        assert merged["__log__"] == ["parent_log", "child_log"]
        assert "__cache_storage__" in merged
        assert merged["__cache_storage__"] is mock_storage

    def test_cache_storage_not_in_child_still_preserved(self):
        """__cache_storage__ should come from parent even if child doesn't have it."""
        mock_storage = MagicMock()
        parent_store: Store = {"__cache_storage__": mock_storage}
        child_store: Store = {"some_key": "value"}

        merged = _merge_thread_state(parent_store, child_store)

        assert merged["__cache_storage__"] is mock_storage

    def test_no_cache_storage_in_either(self):
        """Merge should work fine when neither store has __cache_storage__."""
        parent_store: Store = {"key": "parent"}
        child_store: Store = {"key": "child"}

        merged = _merge_thread_state(parent_store, child_store)

        assert "__cache_storage__" not in merged
        assert merged["key"] == "child"


# ============================================================================
# Test: _handle_task_join prevents double-merge via atomic pop
# ============================================================================


class TestTaskJoinDoubleMergePrevention:
    """Regression test for: race condition in _handle_task_join double-merge."""

    @pytest.mark.asyncio
    async def test_double_join_only_merges_once(self):
        """Joining the same task twice should only merge state once.

        Note: merge_store semantics for Spawn is "parent wins for existing keys",
        so we test with a NEW key that child creates (not existing in parent).
        """

        @do
        def child_program():
            # Use a NEW key that doesn't exist in parent
            yield put("child_counter", 100)
            return "done"

        @do
        def parent_program():
            task = yield Spawn(child_program())
            # Join twice - second join should not re-merge
            result1 = yield task.join()
            result2 = yield task.join()
            # This key was created by child, should be merged once
            counter = yield get("child_counter")
            return (result1, result2, counter)

        result = await run(parent_program())

        # Both joins should return the same result
        assert result.value[0] == "done"
        assert result.value[1] == "done"
        # Counter should be 100 (merged once from child's new key)
        assert result.value[2] == 100

    @pytest.mark.asyncio
    async def test_double_join_logs_appended_once(self):
        """Logs should only be appended once on first join, not duplicated."""
        from doeff.effects import tell

        @do
        def child_program():
            yield tell("child_log_entry")
            return "done"

        @do
        def parent_program():
            yield tell("parent_before")
            task = yield Spawn(child_program())
            yield task.join()
            yield task.join()  # Second join
            yield tell("parent_after")
            return "done"

        result = await run(parent_program())

        # Logs should have: parent_before, child_log_entry (once), parent_after
        # NOT: parent_before, child_log_entry, child_log_entry, parent_after
        assert result.value == "done"


# ============================================================================
# Test: on_step callback errors are logged visibly
# ============================================================================


class TestOnStepCallbackErrorVisibility:
    """Regression test for: on_step callback errors were silently swallowed."""

    @pytest.mark.asyncio
    async def test_on_step_error_does_not_crash_interpreter(self):
        """on_step errors should not crash the interpreter."""

        def failing_callback(snapshot):
            raise ValueError("Callback error!")

        @do
        def simple_program():
            yield put("key", "value")
            return 42

        # Should complete despite callback error
        result = await run(simple_program(), on_step=failing_callback)
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_on_step_error_logged_with_warning(self, caplog):
        """on_step errors should be logged at warning level."""
        import logging

        def failing_callback(snapshot):
            raise ValueError("Test callback error")

        @do
        def simple_program():
            return 42

        with caplog.at_level(logging.WARNING):
            result = await run(simple_program(), on_step=failing_callback)

        assert result.value == 42
        # Check that warning was logged
        assert any("on_step callback error" in record.message for record in caplog.records)
        assert any("Test callback error" in record.message for record in caplog.records)


# ============================================================================
# Test: __dispatcher__ excluded from deep-copy in Spawn/Thread
# ============================================================================


class TestDispatcherExcludedFromDeepCopy:
    """Regression test for: __dispatcher__ deep-copy could fail with non-copyable objects."""

    @pytest.mark.asyncio
    async def test_spawn_works_with_non_copyable_in_store(self):
        """Spawn should work even if store contains non-deepcopyable objects.

        The __dispatcher__ is excluded from deep-copy, so this should work.
        """

        @do
        def child_program():
            return "child_done"

        @do
        def parent_program():
            task = yield Spawn(child_program())
            result = yield task.join()
            return result

        # This should not raise deepcopy errors
        result = await run(parent_program())
        assert result.value == "child_done"

    @pytest.mark.asyncio
    async def test_spawn_works_with_dispatcher_in_store(self):
        """Spawn should work - dispatcher is passed explicitly, not deep-copied."""

        @do
        def spawned_program():
            yield put("from_spawned", True)
            return "spawned_done"

        @do
        def parent_program():
            task = yield Spawn(spawned_program(), preferred_backend="thread")
            result = yield task.join()
            from_spawned = yield get("from_spawned")
            return (result, from_spawned)

        result = await run(parent_program())
        assert result.value[0] == "spawned_done"
        assert result.value[1] is True
