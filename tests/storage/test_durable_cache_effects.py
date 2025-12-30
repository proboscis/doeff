"""
Tests for durable cache effects with CESK interpreter.

Test IDs from issue spec:
- T7: Workflow replay uses cached values - Expensive op not re-run
- T8: on_step callback invoked - Called each step
- T9: ExecutionSnapshot.k_stack accurate - Matches actual K
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from doeff._vendor import Ok
from doeff.cesk import run_sync
from doeff.do import do
from doeff.effects.durable_cache import (
    cachedelete,
    cacheexists,
    cacheget,
    cacheput,
)
from doeff.effects.io import perform
from doeff.effects.pure import Pure
from doeff.storage import InMemoryStorage, SQLiteStorage
from doeff.cesk_observability import ExecutionSnapshot


class TestDurableCacheEffects:
    """Tests for durable cache effects in CESK interpreter."""

    def test_cacheput_then_cacheget(self) -> None:
        """T1: cacheput then cacheget same key returns cached value."""
        storage = InMemoryStorage()

        @do
        def workflow():
            yield cacheput("test_key", {"data": 42})
            result = yield cacheget("test_key")
            return result

        result = run_sync(workflow(), storage=storage)
        assert isinstance(result, Ok)
        assert result.value == {"data": 42}

    def test_cacheget_nonexistent(self) -> None:
        """T2: cacheget non-existent key returns None."""
        storage = InMemoryStorage()

        @do
        def workflow():
            result = yield cacheget("nonexistent")
            return result

        result = run_sync(workflow(), storage=storage)
        assert isinstance(result, Ok)
        assert result.value is None

    def test_cacheput_overwrites(self) -> None:
        """T3: cacheput overwrites existing value."""
        storage = InMemoryStorage()

        @do
        def workflow():
            yield cacheput("key", "old_value")
            yield cacheput("key", "new_value")
            result = yield cacheget("key")
            return result

        result = run_sync(workflow(), storage=storage)
        assert isinstance(result, Ok)
        assert result.value == "new_value"

    def test_cachedelete_existing(self) -> None:
        """T4: cachedelete existing key returns True, key gone."""
        storage = InMemoryStorage()

        @do
        def workflow():
            yield cacheput("key", "value")
            deleted = yield cachedelete("key")
            exists_after = yield cacheexists("key")
            return {"deleted": deleted, "exists_after": exists_after}

        result = run_sync(workflow(), storage=storage)
        assert isinstance(result, Ok)
        assert result.value["deleted"] is True
        assert result.value["exists_after"] is False

    def test_cachedelete_nonexistent(self) -> None:
        """T4: cachedelete non-existent key returns False."""
        storage = InMemoryStorage()

        @do
        def workflow():
            return (yield cachedelete("nonexistent"))

        result = run_sync(workflow(), storage=storage)
        assert isinstance(result, Ok)
        assert result.value is False

    def test_cacheexists_existing(self) -> None:
        """T5: cacheexists on existing key returns True."""
        storage = InMemoryStorage()

        @do
        def workflow():
            yield cacheput("key", "value")
            return (yield cacheexists("key"))

        result = run_sync(workflow(), storage=storage)
        assert isinstance(result, Ok)
        assert result.value is True

    def test_cacheexists_nonexistent(self) -> None:
        """T5: cacheexists on non-existent key returns False."""
        storage = InMemoryStorage()

        @do
        def workflow():
            return (yield cacheexists("nonexistent"))

        result = run_sync(workflow(), storage=storage)
        assert isinstance(result, Ok)
        assert result.value is False

    def test_workflow_replay_uses_cache(self) -> None:
        """T7: Workflow replay uses cached values - expensive op not re-run."""
        storage = InMemoryStorage()
        execution_count = {"expensive_op": 0}

        def expensive_operation() -> dict:
            execution_count["expensive_op"] += 1
            return {"computed": 42}

        @do
        def workflow():
            # Idempotent pattern: check cache first
            result = yield cacheget("expensive_step")
            if result is None:
                result = yield perform(expensive_operation)
                yield cacheput("expensive_step", result)
            return result

        # First run - should execute expensive operation
        result1 = run_sync(workflow(), storage=storage)
        assert isinstance(result1, Ok)
        assert result1.value == {"computed": 42}
        assert execution_count["expensive_op"] == 1

        # Second run (replay) - should use cache, not re-run
        result2 = run_sync(workflow(), storage=storage)
        assert isinstance(result2, Ok)
        assert result2.value == {"computed": 42}
        assert execution_count["expensive_op"] == 1  # Still 1!

    def test_sqlite_persistence(self, tmp_path: Path) -> None:
        """T6: SQLite storage persists across runs."""
        db_path = tmp_path / "workflow.db"
        storage = SQLiteStorage(db_path)

        @do
        def write_workflow():
            yield cacheput("persistent_data", {"step": 1, "result": "done"})
            return "written"

        result1 = run_sync(write_workflow(), storage=storage)
        assert isinstance(result1, Ok)
        storage.close()

        # New connection
        storage2 = SQLiteStorage(db_path)

        @do
        def read_workflow():
            return (yield cacheget("persistent_data"))

        result2 = run_sync(read_workflow(), storage=storage2)
        assert isinstance(result2, Ok)
        assert result2.value == {"step": 1, "result": "done"}
        storage2.close()

    def test_no_storage_returns_none(self) -> None:
        """Cache effects return None/False when no storage configured."""

        @do
        def workflow():
            get_result = yield cacheget("key")
            exists_result = yield cacheexists("key")
            delete_result = yield cachedelete("key")
            return {
                "get": get_result,
                "exists": exists_result,
                "delete": delete_result,
            }

        # Run without storage parameter
        result = run_sync(workflow())
        assert isinstance(result, Ok)
        assert result.value["get"] is None
        assert result.value["exists"] is False
        assert result.value["delete"] is False


class TestObservability:
    """Tests for execution observability features."""

    def test_on_step_callback_invoked(self) -> None:
        """T8: on_step callback invoked each step."""
        storage = InMemoryStorage()
        snapshots: list[ExecutionSnapshot] = []

        def on_step(snapshot: ExecutionSnapshot) -> None:
            snapshots.append(snapshot)

        @do
        def workflow():
            yield Pure(1)
            yield Pure(2)
            yield Pure(3)
            return "done"

        result = run_sync(workflow(), storage=storage, on_step=on_step)
        assert isinstance(result, Ok)
        assert result.value == "done"

        # Should have been called multiple times
        assert len(snapshots) > 0

        # Step counts should be increasing
        step_counts = [s.step_count for s in snapshots]
        assert step_counts == sorted(step_counts)
        assert step_counts[-1] > 0

    def test_snapshot_k_stack_accurate(self) -> None:
        """T9: ExecutionSnapshot.k_stack reflects actual K stack."""
        storage = InMemoryStorage()
        snapshots: list[ExecutionSnapshot] = []

        def on_step(snapshot: ExecutionSnapshot) -> None:
            snapshots.append(snapshot)

        @do
        def inner():
            yield Pure(1)
            return "inner"

        @do
        def outer():
            result = yield inner()
            return result

        result = run_sync(outer(), storage=storage, on_step=on_step)
        assert isinstance(result, Ok)

        # Find snapshots with non-empty K stack
        has_k_stack = [s for s in snapshots if len(s.k_stack) > 0]
        assert len(has_k_stack) > 0

        # Verify frame types are present
        frame_types = set()
        for snapshot in has_k_stack:
            for frame in snapshot.k_stack:
                frame_types.add(frame.frame_type)

        assert "ReturnFrame" in frame_types

    def test_snapshot_cache_keys(self) -> None:
        """ExecutionSnapshot includes cache keys from storage."""
        storage = InMemoryStorage()
        storage.put("existing_key", "value")
        snapshots: list[ExecutionSnapshot] = []

        def on_step(snapshot: ExecutionSnapshot) -> None:
            snapshots.append(snapshot)

        @do
        def workflow():
            yield cacheput("new_key", "new_value")
            return "done"

        result = run_sync(workflow(), storage=storage, on_step=on_step)
        assert isinstance(result, Ok)

        # At least one snapshot should have cache keys
        has_keys = [s for s in snapshots if len(s.cache_keys) > 0]
        assert len(has_keys) > 0

        # Final snapshot should have both keys
        final_snapshot = snapshots[-1]
        assert "existing_key" in final_snapshot.cache_keys
        assert "new_key" in final_snapshot.cache_keys

    def test_snapshot_current_effect(self) -> None:
        """ExecutionSnapshot captures current effect when processing."""
        storage = InMemoryStorage()
        effects_seen: list[Any] = []

        def on_step(snapshot: ExecutionSnapshot) -> None:
            if snapshot.current_effect is not None:
                effects_seen.append(type(snapshot.current_effect).__name__)

        @do
        def workflow():
            yield Pure(1)
            yield cacheput("key", "value")
            result = yield cacheget("key")
            return result

        result = run_sync(workflow(), storage=storage, on_step=on_step)
        assert isinstance(result, Ok)

        # Should have seen Pure and cache effects
        assert "PureEffect" in effects_seen
        assert "DurableCachePut" in effects_seen
        assert "DurableCacheGet" in effects_seen

    def test_snapshot_status_transitions(self) -> None:
        """ExecutionSnapshot shows correct status transitions."""
        storage = InMemoryStorage()
        statuses: list[str] = []

        def on_step(snapshot: ExecutionSnapshot) -> None:
            statuses.append(snapshot.status)

        @do
        def workflow():
            yield Pure(1)
            return "done"

        result = run_sync(workflow(), storage=storage, on_step=on_step)
        assert isinstance(result, Ok)

        # Should have "running" and end with "completed"
        assert "running" in statuses
        assert statuses[-1] == "completed"
