"""
Tests for cache effects with CESK interpreter.

Test IDs from issue spec:
- T7: Workflow replay uses cached values - Expensive op not re-run
- T8: on_step callback invoked - Called each step
- T9: ExecutionSnapshot.k_stack accurate - Matches actual K
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from doeff.cesk import run_sync
from doeff.do import do
from doeff.effects.cache import (
    CacheDelete,
    CacheExists,
    CacheGet,
    CachePut,
)
from doeff.effects.io import perform
from doeff.effects.pure import Pure
from doeff.storage import InMemoryStorage, SQLiteStorage
from doeff.cesk_observability import ExecutionSnapshot


class TestCacheEffects:
    """Tests for cache effects in CESK interpreter."""

    def test_cacheput_then_cacheget(self) -> None:
        """T1: CachePut then CacheGet same key returns cached value."""
        storage = InMemoryStorage()

        @do
        def workflow():
            yield CachePut("test_key", {"data": 42})
            result = yield CacheGet("test_key")
            return result

        result = run_sync(workflow(), storage=storage)
        assert result.is_ok
        assert result.value == {"data": 42}

    def test_cacheget_nonexistent(self) -> None:
        """T2: CacheGet non-existent key returns None."""
        storage = InMemoryStorage()

        @do
        def workflow():
            result = yield CacheGet("nonexistent")
            return result

        result = run_sync(workflow(), storage=storage)
        assert result.is_ok
        assert result.value is None

    def test_cacheput_overwrites(self) -> None:
        """T3: CachePut overwrites existing value."""
        storage = InMemoryStorage()

        @do
        def workflow():
            yield CachePut("key", "old_value")
            yield CachePut("key", "new_value")
            result = yield CacheGet("key")
            return result

        result = run_sync(workflow(), storage=storage)
        assert result.is_ok
        assert result.value == "new_value"

    def test_cachedelete_existing(self) -> None:
        """T4: CacheDelete existing key returns True, key gone."""
        storage = InMemoryStorage()

        @do
        def workflow():
            yield CachePut("key", "value")
            deleted = yield CacheDelete("key")
            exists_after = yield CacheExists("key")
            return {"deleted": deleted, "exists_after": exists_after}

        result = run_sync(workflow(), storage=storage)
        assert result.is_ok
        assert result.value["deleted"] is True
        assert result.value["exists_after"] is False

    def test_cachedelete_nonexistent(self) -> None:
        """T4: CacheDelete non-existent key returns False."""
        storage = InMemoryStorage()

        @do
        def workflow():
            return (yield CacheDelete("nonexistent"))

        result = run_sync(workflow(), storage=storage)
        assert result.is_ok
        assert result.value is False

    def test_cacheexists_existing(self) -> None:
        """T5: CacheExists on existing key returns True."""
        storage = InMemoryStorage()

        @do
        def workflow():
            yield CachePut("key", "value")
            return (yield CacheExists("key"))

        result = run_sync(workflow(), storage=storage)
        assert result.is_ok
        assert result.value is True

    def test_cacheexists_nonexistent(self) -> None:
        """T5: CacheExists on non-existent key returns False."""
        storage = InMemoryStorage()

        @do
        def workflow():
            return (yield CacheExists("nonexistent"))

        result = run_sync(workflow(), storage=storage)
        assert result.is_ok
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
            result = yield CacheGet("expensive_step")
            if result is None:
                result = yield perform(expensive_operation)
                yield CachePut("expensive_step", result)
            return result

        result1 = run_sync(workflow(), storage=storage)
        assert result1.is_ok
        assert result1.value == {"computed": 42}
        assert execution_count["expensive_op"] == 1

        result2 = run_sync(workflow(), storage=storage)
        assert result2.is_ok
        assert result2.value == {"computed": 42}
        assert execution_count["expensive_op"] == 1

    def test_sqlite_persistence(self, tmp_path: Path) -> None:
        """T6: SQLite storage persists across runs."""
        db_path = tmp_path / "workflow.db"
        storage = SQLiteStorage(db_path)

        @do
        def write_workflow():
            yield CachePut("persistent_data", {"step": 1, "result": "done"})
            return "written"

        result1 = run_sync(write_workflow(), storage=storage)
        assert result1.is_ok
        storage.close()

        storage2 = SQLiteStorage(db_path)

        @do
        def read_workflow():
            return (yield CacheGet("persistent_data"))

        result2 = run_sync(read_workflow(), storage=storage2)
        assert result2.is_ok
        assert result2.value == {"step": 1, "result": "done"}
        storage2.close()

    def test_no_storage_returns_none(self) -> None:
        """Cache effects return None/False when no storage configured."""

        @do
        def workflow():
            get_result = yield CacheGet("key")
            exists_result = yield CacheExists("key")
            delete_result = yield CacheDelete("key")
            return {
                "get": get_result,
                "exists": exists_result,
                "delete": delete_result,
            }

        result = run_sync(workflow())
        assert result.is_ok
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
        assert result.is_ok
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
        assert result.is_ok

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
            yield CachePut("new_key", "new_value")
            return "done"

        result = run_sync(workflow(), storage=storage, on_step=on_step)
        assert result.is_ok

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
            yield CachePut("key", "value")
            result = yield CacheGet("key")
            return result

        result = run_sync(workflow(), storage=storage, on_step=on_step)
        assert result.is_ok

        assert "PureEffect" in effects_seen
        assert "CachePutEffect" in effects_seen
        assert "CacheGetEffect" in effects_seen

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
        assert result.is_ok

        # Should have "running" and end with "completed"
        assert "running" in statuses
        assert statuses[-1] == "completed"
