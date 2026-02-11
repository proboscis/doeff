from __future__ import annotations

from dataclasses import dataclass

from doeff import WithHandler, default_handlers, do, run
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
