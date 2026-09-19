import asyncio
from dataclasses import dataclass

from doeff_core_effects import Await
from doeff_core_effects.scheduler import Spawn, Wait
from doeff_events.effects import Publish, WaitForEvent

from doeff import do


@dataclass(frozen=True)
class OrderFilled:
    symbol: str
    quantity: int


def test_wait_for_event_receives_next_matching_event(run_events) -> None:
    published_event = OrderFilled(symbol="AAPL", quantity=100)

    @do
    def delayed_publisher():
        _ = yield Await(asyncio.sleep(0.01))
        yield Publish(published_event)
        return "published"

    @do
    def program():
        publisher_task = yield Spawn(delayed_publisher())
        received_event = yield WaitForEvent(OrderFilled)
        publish_status = yield Wait(publisher_task)
        return (received_event, publish_status)

    result = run_events(program())
    received_event, publish_status = result
    assert received_event == published_event
    assert publish_status == "published"
