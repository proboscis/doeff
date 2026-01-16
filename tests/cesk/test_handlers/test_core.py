"""Tests for core effect handlers: Ask, Get, Put, Modify, Tell."""

from __future__ import annotations

import pytest

from doeff._vendor import FrozenDict
from doeff.cesk.actions import AppendLog, Resume
from doeff.cesk.handlers.core import (
    handle_ask,
    handle_get,
    handle_modify,
    handle_put,
    handle_tell,
)
from doeff.cesk.unified_step import HandlerContext
from doeff.cesk.types import TaskId
from doeff.effects import AskEffect, StateGetEffect, StateModifyEffect, StatePutEffect, WriterTellEffect


def make_ctx(
    env: dict | None = None,
    store: dict | None = None,
    task_id: int = 0,
) -> HandlerContext:
    return HandlerContext(
        env=FrozenDict(env or {}),
        store=store or {},
        task_id=TaskId(task_id),
        kontinuation=[],
    )


class TestHandleAsk:
    def test_returns_env_value_when_key_exists(self) -> None:
        ctx = make_ctx(env={"user": "alice"})
        effect = AskEffect(key="user")
        
        (action,) = handle_ask(effect, ctx)
        
        assert isinstance(action, Resume)
        assert action.value == "alice"
    
    def test_returns_none_when_key_missing(self) -> None:
        ctx = make_ctx(env={})
        effect = AskEffect(key="missing")
        
        (action,) = handle_ask(effect, ctx)
        
        assert isinstance(action, Resume)
        assert action.value is None


class TestHandleGet:
    def test_returns_store_value_when_key_exists(self) -> None:
        ctx = make_ctx(store={"counter": 42})
        effect = StateGetEffect(key="counter")
        
        (action,) = handle_get(effect, ctx)
        
        assert isinstance(action, Resume)
        assert action.value == 42
    
    def test_returns_none_when_key_missing(self) -> None:
        ctx = make_ctx(store={})
        effect = StateGetEffect(key="missing")
        
        (action,) = handle_get(effect, ctx)
        
        assert isinstance(action, Resume)
        assert action.value is None


class TestHandlePut:
    def test_updates_store_and_returns_none(self) -> None:
        ctx = make_ctx(store={})
        effect = StatePutEffect(key="counter", value=100)
        
        (action,) = handle_put(effect, ctx)
        
        assert isinstance(action, Resume)
        assert action.value is None
        assert action.store == {"counter": 100}
    
    def test_overwrites_existing_value(self) -> None:
        ctx = make_ctx(store={"counter": 1})
        effect = StatePutEffect(key="counter", value=99)
        
        (action,) = handle_put(effect, ctx)
        
        assert action.store == {"counter": 99}


class TestHandleModify:
    def test_applies_function_and_returns_new_value(self) -> None:
        ctx = make_ctx(store={"counter": 10})
        effect = StateModifyEffect(key="counter", func=lambda x: x + 5)
        
        (action,) = handle_modify(effect, ctx)
        
        assert isinstance(action, Resume)
        assert action.value == 15
        assert action.store == {"counter": 15}
    
    def test_handles_none_initial_value(self) -> None:
        ctx = make_ctx(store={})
        effect = StateModifyEffect(key="counter", func=lambda x: 0 if x is None else x + 1)
        
        (action,) = handle_modify(effect, ctx)
        
        assert action.value == 0
        assert action.store == {"counter": 0}


class TestHandleTell:
    def test_appends_single_message_to_log(self) -> None:
        ctx = make_ctx(store={})
        effect = WriterTellEffect(message="hello")
        
        (action,) = handle_tell(effect, ctx)
        
        assert isinstance(action, Resume)
        assert action.value is None
        assert action.store is not None
        assert action.store["__log__"] == ["hello"]
    
    def test_appends_list_of_messages(self) -> None:
        ctx = make_ctx(store={})
        effect = WriterTellEffect(message=["a", "b", "c"])
        
        (action,) = handle_tell(effect, ctx)
        
        assert action.store is not None
        assert action.store["__log__"] == ["a", "b", "c"]
    
    def test_appends_to_existing_log(self) -> None:
        ctx = make_ctx(store={"__log__": ["first"]})
        effect = WriterTellEffect(message="second")
        
        (action,) = handle_tell(effect, ctx)
        
        assert action.store is not None
        assert action.store["__log__"] == ["first", "second"]
