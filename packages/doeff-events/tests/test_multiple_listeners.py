from __future__ import annotations

import asyncio
from dataclasses import dataclass

from doeff_events.effects import Publish, WaitForEvent
from doeff_events.handlers import event_handler

from doeff import Spawn, Wait, WithHandler, default_handlers, do, run
from doeff.effects import Await


@dataclass(frozen=True)
class PriceTick:
    symbol: str
    price_cents: int


def test_publish_wakes_multiple_listeners_waiting_same_type() -> None:
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

    first_result = run(
        WithHandler(event_handler(), run_listener("a")),
        handlers=default_handlers(),
    )

    second_result = run(
        WithHandler(event_handler(), run_listener("b")),
        handlers=default_handlers(),
    )

    assert first_result.is_ok()
    assert second_result.is_ok()

    result_a, publish_status_a = first_result.value
    result_b, publish_status_b = second_result.value

    assert publish_status_a == "published"
    assert publish_status_b == "published"
    assert result_a == ("a", "AAPL", 12345)
    assert result_b == ("b", "AAPL", 12345)
