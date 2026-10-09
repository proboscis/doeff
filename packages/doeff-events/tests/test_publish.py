
import asyncio
import copy
import pickle
from dataclasses import dataclass

from doeff_core_effects import Await
from doeff_core_effects.scheduler import Spawn, Wait
from doeff_events.effects import Publish, PublishEffect, WaitForEvent, publish_effect_type
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


def test_every_publish_is_of_the_class_of_its_event_type() -> None:
    # A handler names the Publish classes of the event types it answers, and the VM skips it for the others: the
    # class must be the same however the value is made, and one per event type.
    filled = OrderFilled(symbol="AAPL", quantity=100)
    assert type(Publish(filled)) is publish_effect_type(OrderFilled)
    assert type(PublishEffect(filled)) is publish_effect_type(OrderFilled)
    assert isinstance(Publish(filled), PublishEffect)
    assert publish_effect_type(OrderFilled) is not publish_effect_type(Heartbeat)
    assert Publish(filled).event == filled


def test_a_copied_publish_keeps_its_class_and_event() -> None:
    filled = OrderFilled(symbol="AAPL", quantity=100)
    for copied in (pickle.loads(pickle.dumps(Publish(filled))), copy.copy(Publish(filled)), copy.deepcopy(Publish(filled))):
        assert type(copied) is publish_effect_type(OrderFilled)
        assert copied.event == filled
