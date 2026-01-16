"""Unit tests for I/O effect handlers."""

from __future__ import annotations

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk.actions import AwaitExternal, PerformIO, Resume, ResumeWithStore
from doeff.cesk.handlers import HandlerContext, HandlerResult
from doeff.cesk.handlers.io import (
    handle_await,
    handle_cache_delete,
    handle_cache_exists,
    handle_cache_get,
    handle_cache_put,
    handle_io,
)
from doeff.cesk.types import TaskId
from doeff.effects import (
    CacheDeleteEffect,
    CacheExistsEffect,
    CacheGetEffect,
    CachePutEffect,
    FutureAwaitEffect,
    IOPerformEffect,
)


def make_ctx(
    env: dict | None = None,
    store: dict | None = None,
) -> HandlerContext:
    """Create a test HandlerContext."""
    return HandlerContext(
        task_id=TaskId(0),
        env=FrozenDict(env or {}),
        store=store or {},
        kontinuation=[],
    )


class TestHandleIO:
    """Tests for handle_io."""

    def test_returns_perform_io_action(self) -> None:
        """Returns PerformIO action with the function."""

        def my_io():
            return "result"

        effect = IOPerformEffect(action=my_io)
        ctx = make_ctx()

        result = handle_io(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], PerformIO)
        assert result.actions[0].operation is my_io


class TestHandleAwait:
    """Tests for handle_await."""

    def test_returns_await_external_action(self) -> None:
        """Returns AwaitExternal action with the awaitable."""

        async def coro():
            return 42

        awaitable = coro()
        effect = FutureAwaitEffect(awaitable=awaitable)
        ctx = make_ctx()

        result = handle_await(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], AwaitExternal)
        assert result.actions[0].awaitable is awaitable
        # Clean up
        awaitable.close()


class TestHandleCacheGet:
    """Tests for handle_cache_get."""

    def test_returns_cached_value(self) -> None:
        """Returns value from cache."""
        effect = CacheGetEffect(key="my_key")
        ctx = make_ctx(store={"__cache_storage__": {"my_key": "cached_value"}})

        result = handle_cache_get(effect, ctx)

        assert isinstance(result.actions[0], Resume)
        assert result.actions[0].value == "cached_value"

    def test_returns_none_when_not_found(self) -> None:
        """Returns None when key not in cache."""
        effect = CacheGetEffect(key="missing")
        ctx = make_ctx(store={"__cache_storage__": {}})

        result = handle_cache_get(effect, ctx)

        assert result.actions[0].value is None

    def test_handles_empty_cache(self) -> None:
        """Handles missing cache storage."""
        effect = CacheGetEffect(key="any")
        ctx = make_ctx(store={})

        result = handle_cache_get(effect, ctx)

        assert result.actions[0].value is None


class TestHandleCachePut:
    """Tests for handle_cache_put."""

    def test_stores_value_in_cache(self) -> None:
        """Stores value in cache."""
        from doeff.cache_policy import CachePolicy

        effect = CachePutEffect(key="my_key", value="my_value", policy=CachePolicy())
        ctx = make_ctx(store={})

        result = handle_cache_put(effect, ctx)

        assert isinstance(result.actions[0], ResumeWithStore)
        assert result.actions[0].value is None
        assert result.actions[0].store["__cache_storage__"]["my_key"] == "my_value"

    def test_overwrites_existing_value(self) -> None:
        """Overwrites existing cached value."""
        from doeff.cache_policy import CachePolicy

        effect = CachePutEffect(key="my_key", value="new_value", policy=CachePolicy())
        ctx = make_ctx(store={"__cache_storage__": {"my_key": "old_value"}})

        result = handle_cache_put(effect, ctx)

        assert result.actions[0].store["__cache_storage__"]["my_key"] == "new_value"


class TestHandleCacheDelete:
    """Tests for handle_cache_delete."""

    def test_deletes_existing_key(self) -> None:
        """Deletes key and returns True."""
        effect = CacheDeleteEffect(key="my_key")
        ctx = make_ctx(store={"__cache_storage__": {"my_key": "value"}})

        result = handle_cache_delete(effect, ctx)

        assert isinstance(result.actions[0], ResumeWithStore)
        assert result.actions[0].value is True
        assert "my_key" not in result.actions[0].store["__cache_storage__"]

    def test_returns_false_for_missing_key(self) -> None:
        """Returns False when key doesn't exist."""
        effect = CacheDeleteEffect(key="missing")
        ctx = make_ctx(store={"__cache_storage__": {}})

        result = handle_cache_delete(effect, ctx)

        assert result.actions[0].value is False


class TestHandleCacheExists:
    """Tests for handle_cache_exists."""

    def test_returns_true_when_exists(self) -> None:
        """Returns True when key exists."""
        effect = CacheExistsEffect(key="my_key")
        ctx = make_ctx(store={"__cache_storage__": {"my_key": "value"}})

        result = handle_cache_exists(effect, ctx)

        assert isinstance(result.actions[0], Resume)
        assert result.actions[0].value is True

    def test_returns_false_when_missing(self) -> None:
        """Returns False when key doesn't exist."""
        effect = CacheExistsEffect(key="missing")
        ctx = make_ctx(store={"__cache_storage__": {}})

        result = handle_cache_exists(effect, ctx)

        assert result.actions[0].value is False
