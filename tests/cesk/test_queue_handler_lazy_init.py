"""Tests for queue_handler lazy initialization of scheduler store keys."""

from __future__ import annotations

from uuid import UUID

import pytest

from doeff.cesk.handlers.queue_handler import (
    CURRENT_TASK_KEY,
    TASK_QUEUE_KEY,
    TASK_REGISTRY_KEY,
    WAITERS_KEY,
    _ensure_scheduler_store_initialized,
)


class TestEnsureSchedulerStoreInitialized:

    def test_initializes_missing_keys(self):
        store: dict[str, object] = {}
        _ensure_scheduler_store_initialized(store)
        
        assert TASK_QUEUE_KEY in store
        assert TASK_REGISTRY_KEY in store
        assert WAITERS_KEY in store
        assert CURRENT_TASK_KEY in store

    def test_initializes_with_correct_defaults(self):
        store: dict[str, object] = {}
        _ensure_scheduler_store_initialized(store)
        
        assert store[TASK_QUEUE_KEY] == []
        assert store[TASK_REGISTRY_KEY] == {}
        assert store[WAITERS_KEY] == {}
        assert isinstance(store[CURRENT_TASK_KEY], UUID)

    def test_does_not_overwrite_existing_queue(self):
        existing_queue = [{"task_id": "test", "k": []}]
        store: dict[str, object] = {TASK_QUEUE_KEY: existing_queue}
        _ensure_scheduler_store_initialized(store)
        
        assert store[TASK_QUEUE_KEY] is existing_queue
        assert len(store[TASK_QUEUE_KEY]) == 1  # type: ignore[arg-type]

    def test_does_not_overwrite_existing_registry(self):
        existing_registry = {"handle_1": "task_info"}
        store: dict[str, object] = {TASK_REGISTRY_KEY: existing_registry}
        _ensure_scheduler_store_initialized(store)
        
        assert store[TASK_REGISTRY_KEY] is existing_registry
        assert "handle_1" in store[TASK_REGISTRY_KEY]  # type: ignore[operator]

    def test_does_not_overwrite_existing_waiters(self):
        existing_waiters = {"handle_1": [{"waiter": "info"}]}
        store: dict[str, object] = {WAITERS_KEY: existing_waiters}
        _ensure_scheduler_store_initialized(store)
        
        assert store[WAITERS_KEY] is existing_waiters
        assert "handle_1" in store[WAITERS_KEY]  # type: ignore[operator]

    def test_does_not_overwrite_existing_current_task(self):
        from uuid import uuid4
        existing_task_id = uuid4()
        store: dict[str, object] = {CURRENT_TASK_KEY: existing_task_id}
        _ensure_scheduler_store_initialized(store)
        
        assert store[CURRENT_TASK_KEY] is existing_task_id

    def test_preserves_user_store_entries(self):
        store: dict[str, object] = {"user_key": "user_value", "counter": 42}
        _ensure_scheduler_store_initialized(store)
        
        assert store["user_key"] == "user_value"
        assert store["counter"] == 42
        assert TASK_QUEUE_KEY in store

    def test_idempotent_multiple_calls(self):
        store: dict[str, object] = {}
        _ensure_scheduler_store_initialized(store)
        first_task_id = store[CURRENT_TASK_KEY]
        
        _ensure_scheduler_store_initialized(store)
        
        assert store[CURRENT_TASK_KEY] is first_task_id
