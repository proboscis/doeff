
import asyncio
from dataclasses import dataclass

from doeff_events.effects import Publish, WaitForEvent
from doeff_events.handlers import event_handler

from doeff import Spawn, Wait, WithHandler, default_handlers, do, run
from doeff.effects import Await


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

    result = run(
        WithHandler(event_handler(), program()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == "ok"


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

    result = run(
        WithHandler(event_handler(), program()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    processed, producer_status = result.value
    assert processed == "Processed: AAPL x100"
    assert producer_status == "done"
