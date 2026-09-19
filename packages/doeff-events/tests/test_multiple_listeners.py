import asyncio
from dataclasses import dataclass

from doeff_core_effects import Await
from doeff_core_effects.scheduler import Spawn, Wait
from doeff_events.effects import Publish, WaitForEvent

from doeff import do


@dataclass(frozen=True)
class PriceTick:
    symbol: str
    price_cents: int


def test_publish_wakes_multiple_listeners_waiting_same_type(run_events) -> None:
    event = PriceTick(symbol="AAPL", price_cents=12345)

    @do
    def listener(name: str):
        received = yield WaitForEvent(PriceTick)
        return (name, received.symbol, received.price_cents)

    @do
    def publisher():
        _ = yield Await(asyncio.sleep(0.01))
        yield Publish(event)
        return "published"

    @do
    def run_listener(name: str):
        listener_task = yield Spawn(listener(name))
        publisher_task = yield Spawn(publisher())
        result = yield Wait(listener_task)
        publish_status = yield Wait(publisher_task)
        return (result, publish_status)

    first_result = run_events(run_listener("a"))

    second_result = run_events(run_listener("b"))

    result_a, publish_status_a = first_result
    result_b, publish_status_b = second_result

    assert publish_status_a == "published"
    assert publish_status_b == "published"
    assert result_a == ("a", "AAPL", 12345)
    assert result_b == ("b", "AAPL", 12345)
