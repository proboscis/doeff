from __future__ import annotations

import asyncio
from dataclasses import dataclass

from doeff_events.effects import Publish, WaitForEvent
from doeff_events.handlers import event_handler

from doeff import Spawn, Wait, WithHandler, default_handlers, do, run
from doeff.effects import Await


@dataclass(frozen=True)
class MarketEvent:
    symbol: str


@dataclass(frozen=True)
class PriceUpdated(MarketEvent):
    price_cents: int


@dataclass(frozen=True)
class OrderFilled(MarketEvent):
    quantity: int


def test_waiter_ignores_non_matching_event_types() -> None:
    matching_event = OrderFilled(symbol="AAPL", quantity=5)

    @do
    def wait_for_fill():
        return (yield WaitForEvent(OrderFilled))

    @do
    def publisher():
        _ = yield Await(asyncio.sleep(0.01))
        yield Publish(PriceUpdated(symbol="AAPL", price_cents=10100))
        _ = yield Await(asyncio.sleep(0.01))
        yield Publish(matching_event)
        return "published"

    @do
    def program():
        waiter_task = yield Spawn(wait_for_fill())
        publisher_task = yield Spawn(publisher())
        received = yield Wait(waiter_task)
        publish_status = yield Wait(publisher_task)
        return (received, publish_status)

    result = run(
        WithHandler(event_handler(), program()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    received, publish_status = result.value
    assert publish_status == "published"
    assert received == matching_event
    assert isinstance(received, OrderFilled)


def test_wait_for_base_type_receives_subclass_events() -> None:
    published_event = PriceUpdated(symbol="MSFT", price_cents=42500)

    @do
    def publisher():
        _ = yield Await(asyncio.sleep(0.01))
        yield Publish(published_event)
        return "published"

    @do
    def program():
        publisher_task = yield Spawn(publisher())
        received = yield WaitForEvent(MarketEvent)
        publish_status = yield Wait(publisher_task)
        return (received, publish_status)

    result = run(
        WithHandler(event_handler(), program()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    received, publish_status = result.value
    assert publish_status == "published"
    assert received == published_event
    assert isinstance(received, PriceUpdated)
