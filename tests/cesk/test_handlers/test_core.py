"""Unit tests for core effect handlers."""

from __future__ import annotations

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk.actions import Resume, ResumeWithStore
from doeff.cesk.handlers import HandlerContext, HandlerResult
from doeff.cesk.handlers.core import (
    handle_ask,
    handle_get,
    handle_modify,
    handle_pure,
    handle_put,
    handle_tell,
)
from doeff.cesk.types import TaskId
from doeff.effects import AskEffect, PureEffect, StateGetEffect, StateModifyEffect, StatePutEffect, WriterTellEffect


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


class TestHandleAsk:
    """Tests for handle_ask."""

    def test_returns_value_from_env(self) -> None:
        """Returns value when key exists in environment."""
        effect = AskEffect(key="x")
        ctx = make_ctx(env={"x": 42})

        result = handle_ask(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], Resume)
        assert result.actions[0].value == 42

    def test_returns_none_when_key_missing(self) -> None:
        """Returns None when key not in environment."""
        effect = AskEffect(key="missing")
        ctx = make_ctx(env={})

        result = handle_ask(effect, ctx)

        assert result.actions[0].value is None


class TestHandleGet:
    """Tests for handle_get."""

    def test_returns_value_from_store(self) -> None:
        """Returns value when key exists in store."""
        effect = StateGetEffect(key="counter")
        ctx = make_ctx(store={"counter": 10})

        result = handle_get(effect, ctx)

        assert isinstance(result.actions[0], Resume)
        assert result.actions[0].value == 10

    def test_returns_none_when_key_missing(self) -> None:
        """Returns None when key not in store."""
        effect = StateGetEffect(key="missing")
        ctx = make_ctx(store={})

        result = handle_get(effect, ctx)

        assert result.actions[0].value is None


class TestHandlePut:
    """Tests for handle_put."""

    def test_updates_store_and_returns_old_value(self) -> None:
        """Updates store and returns previous value."""
        effect = StatePutEffect(key="counter", value=20)
        ctx = make_ctx(store={"counter": 10})

        result = handle_put(effect, ctx)

        assert isinstance(result.actions[0], ResumeWithStore)
        assert result.actions[0].value == 10  # Old value
        assert result.actions[0].store["counter"] == 20  # New value

    def test_returns_none_for_new_key(self) -> None:
        """Returns None when key didn't exist."""
        effect = StatePutEffect(key="new_key", value=100)
        ctx = make_ctx(store={})

        result = handle_put(effect, ctx)

        assert result.actions[0].value is None
        assert result.actions[0].store["new_key"] == 100


class TestHandleModify:
    """Tests for handle_modify."""

    def test_applies_function_to_value(self) -> None:
        """Applies function and stores result."""
        effect = StateModifyEffect(key="counter", func=lambda x: x + 1)
        ctx = make_ctx(store={"counter": 10})

        result = handle_modify(effect, ctx)

        assert isinstance(result.actions[0], ResumeWithStore)
        assert result.actions[0].value == 10  # Old value
        assert result.actions[0].store["counter"] == 11  # Modified value

    def test_handles_none_initial_value(self) -> None:
        """Handles None when key doesn't exist."""
        effect = StateModifyEffect(key="new", func=lambda x: x or 0)
        ctx = make_ctx(store={})

        result = handle_modify(effect, ctx)

        assert result.actions[0].store["new"] == 0


class TestHandleTell:
    """Tests for handle_tell."""

    def test_appends_to_log(self) -> None:
        """Appends message to __log__ list."""
        effect = WriterTellEffect(message="log entry")
        ctx = make_ctx(store={"__log__": ["entry1"]})

        result = handle_tell(effect, ctx)

        assert isinstance(result.actions[0], ResumeWithStore)
        assert result.actions[0].value is None
        assert result.actions[0].store["__log__"] == ["entry1", "log entry"]

    def test_creates_log_if_missing(self) -> None:
        """Creates __log__ list if it doesn't exist."""
        effect = WriterTellEffect(message="first entry")
        ctx = make_ctx(store={})

        result = handle_tell(effect, ctx)

        assert result.actions[0].store["__log__"] == ["first entry"]


class TestHandlePure:
    """Tests for handle_pure."""

    def test_returns_pure_value(self) -> None:
        """Returns the effect's value."""
        effect = PureEffect(value=42)
        ctx = make_ctx()

        result = handle_pure(effect, ctx)

        assert isinstance(result.actions[0], Resume)
        assert result.actions[0].value == 42

    def test_returns_none(self) -> None:
        """Can return None."""
        effect = PureEffect(value=None)
        ctx = make_ctx()

        result = handle_pure(effect, ctx)

        assert result.actions[0].value is None
