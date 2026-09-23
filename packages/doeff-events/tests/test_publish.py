
import asyncio
from dataclasses import dataclass

from doeff_core_effects import Await
from doeff_core_effects.scheduler import Spawn, Wait
from doeff_events.effects import Publish, WaitForEvent
from doeff_events.handlers import event_handler
from events_test_support import run_scheduled

from doeff import do


@dataclass(frozen=True)
class Heartbeat:
    value: str


@dataclass(frozen=True)
class OrderFilled:
    symbol: str
    quantity: int


def test_publish_with_no_listeners_is_noop() -> None:
    @do
    def program():
        yield Publish(Heartbeat("alive"))
        return "ok"

    result = run_scheduled(event_handler()(program()))

    assert result == "ok"


def test_composes_with_spawn_producer_consumer_pattern() -> None:
    event = OrderFilled(symbol="AAPL", quantity=100)

    @do
    def consumer():
        received = yield WaitForEvent(OrderFilled)
        return f"Processed: {received.symbol} x{received.quantity}"

    @do
    def producer():
        _ = yield Await(asyncio.sleep(0.01))
        yield Publish(event)
        return "done"

    @do
    def program():
        consumer_task = yield Spawn(consumer())
        producer_task = yield Spawn(producer())
        processed = yield Wait(consumer_task)
        producer_status = yield Wait(producer_task)
        return (processed, producer_status)

    result = run_scheduled(event_handler()(program()))

    processed, producer_status = result
    assert processed == "Processed: AAPL x100"
    assert producer_status == "done"
