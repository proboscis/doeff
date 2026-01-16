"""Unit tests for time effect handlers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk.actions import Resume, WaitForDuration, WaitUntilTime
from doeff.cesk.handlers import HandlerContext, HandlerResult
from doeff.cesk.handlers.time import (
    handle_delay,
    handle_get_time,
    handle_wait_until,
)
from doeff.cesk.types import TaskId
from doeff.effects import DelayEffect, GetTimeEffect, WaitUntilEffect


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


class TestHandleDelay:
    """Tests for handle_delay."""

    def test_returns_wait_for_duration(self) -> None:
        """Returns WaitForDuration action."""
        effect = DelayEffect(seconds=5.0)
        ctx = make_ctx()

        result = handle_delay(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], WaitForDuration)
        assert result.actions[0].seconds == 5.0

    def test_handles_fractional_seconds(self) -> None:
        """Handles sub-second durations."""
        effect = DelayEffect(seconds=0.5)
        ctx = make_ctx()

        result = handle_delay(effect, ctx)

        assert result.actions[0].seconds == 0.5


class TestHandleWaitUntil:
    """Tests for handle_wait_until."""

    def test_returns_wait_until_time(self) -> None:
        """Returns WaitUntilTime action with timestamp."""
        target = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        effect = WaitUntilEffect(target_time=target)
        ctx = make_ctx()

        result = handle_wait_until(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], WaitUntilTime)
        assert result.actions[0].target_time == target.timestamp()


class TestHandleGetTime:
    """Tests for handle_get_time."""

    def test_returns_simulated_time_when_present(self) -> None:
        """Returns simulated time from store."""
        sim_time = 1705320000.0  # Some Unix timestamp
        effect = GetTimeEffect()
        ctx = make_ctx(store={"__current_time__": sim_time})

        result = handle_get_time(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], Resume)
        # Result should be datetime from the simulated time
        dt = result.actions[0].value
        assert isinstance(dt, datetime)
        assert dt.timestamp() == sim_time

    def test_returns_real_time_when_not_simulated(self) -> None:
        """Returns real time when not in simulation."""
        effect = GetTimeEffect()
        ctx = make_ctx(store={})

        before = datetime.now(timezone.utc)
        result = handle_get_time(effect, ctx)
        after = datetime.now(timezone.utc)

        assert isinstance(result.actions[0], Resume)
        dt = result.actions[0].value
        assert isinstance(dt, datetime)
        assert before <= dt <= after
