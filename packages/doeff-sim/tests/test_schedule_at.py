from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from doeff import Listen, Tell, WithHandler, default_handlers, do, run
from doeff.effects.spawn import Spawn
from doeff.effects.wait import wait
from doeff_events.effects import Publish, WaitForEvent
from doeff_events.handlers import event_handler
from doeff_time.effects import Delay, GetTime, ScheduleAt

from doeff_sim.handlers import deterministic_sim_handler


@dataclass(frozen=True)
class MarketOpen:
    time: float


@do
def _program():
    now = yield GetTime()
    target = now + 60.0
    yield ScheduleAt(target, Publish(MarketOpen(time=target)))
    yield Delay(60.0)
    event = yield WaitForEvent(MarketOpen)
    end = yield GetTime()
    return event, end


@do
def _advance_worker():
    yield Delay(10.0)
    return "worker-done"


@do
def _schedule_at_with_spawn():
    yield ScheduleAt(5.0, Tell("scheduled"))
    task: Any = yield Spawn(_advance_worker())
    result: str = yield wait(task)
    return result


@pytest.mark.skip(
    reason="Requires event_handler to support publish-before-wait pattern; tracked for doeff-events improvement"
)
def test_schedule_at_composes_with_event_handler() -> None:
    wrapped = WithHandler(
        deterministic_sim_handler(start_time=1_704_067_200.0),
        WithHandler(event_handler(), _program()),
    )

    result = run(wrapped, handlers=default_handlers())
    event, end = result.value

    assert isinstance(event, MarketOpen)
    assert event.time == 1_704_067_260.0
    assert end == 1_704_067_260.0


def test_schedule_at_runs_before_wait_completion() -> None:
    wrapped: Any = Listen(
        WithHandler(
            deterministic_sim_handler(
                start_time=0.0,
                log_formatter=lambda sim_time, msg: f"[sim:{sim_time:.1f}] {msg}",
            ),
            _schedule_at_with_spawn(),
        )
    )

    result: Any = run(wrapped, handlers=default_handlers())
    listen_result: Any = result.value
    log_messages: list[str] = list(listen_result.log)

    assert listen_result.value == "worker-done"
    # The scheduled Tell runs during time advancement.
    # Log formatting applies when the Tell flows through the sim handler.
    assert any("scheduled" in msg for msg in log_messages)
