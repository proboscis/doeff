"""Tests for time-related effect handlers."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from doeff._vendor import FrozenDict
from doeff.cesk.actions import Delay, Resume, WaitUntil
from doeff.cesk.handlers.time import (
    handle_delay,
    handle_get_time,
    handle_wait_until,
)
from doeff.cesk.unified_step import HandlerContext
from doeff.cesk.types import TaskId
from doeff.effects import DelayEffect, GetTimeEffect, WaitUntilEffect


def make_ctx() -> HandlerContext:
    return HandlerContext(
        env=FrozenDict(),
        store={},
        task_id=TaskId(0),
        kontinuation=[],
    )


class TestHandleDelay:
    def test_returns_delay_action_with_duration(self) -> None:
        ctx = make_ctx()
        effect = DelayEffect(seconds=5.0)
        
        (action,) = handle_delay(effect, ctx)
        
        assert isinstance(action, Delay)
        assert action.duration == timedelta(seconds=5.0)
    
    def test_handles_fractional_seconds(self) -> None:
        ctx = make_ctx()
        effect = DelayEffect(seconds=0.5)
        
        (action,) = handle_delay(effect, ctx)
        
        assert action.duration == timedelta(seconds=0.5)


class TestHandleWaitUntil:
    def test_returns_wait_until_action_with_target(self) -> None:
        ctx = make_ctx()
        target = datetime(2025, 6, 15, 12, 0, 0)
        effect = WaitUntilEffect(target_time=target)
        
        (action,) = handle_wait_until(effect, ctx)
        
        assert isinstance(action, WaitUntil)
        assert action.target == target


class TestHandleGetTime:
    def test_returns_current_datetime(self) -> None:
        ctx = make_ctx()
        effect = GetTimeEffect()
        
        before = datetime.now()
        (action,) = handle_get_time(effect, ctx)
        after = datetime.now()
        
        assert isinstance(action, Resume)
        assert before <= action.value <= after
