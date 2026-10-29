
import copy
import pickle
from dataclasses import dataclass

from doeff_core_effects import CreatePromise, Promise, Spawn, Wait
from doeff_events import Publish, PublishEffect, WaitForEvent, event_handler
from doeff_events._effects import publish_effect_type
from events_test_support import announce_listening, run_scheduled

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
    def producer(registered: Promise[object]):
        # event_handler drops a Publish that comes before the consumer listens: publish once it is registered.
        yield Wait(registered.future)
        yield Publish(event)
        return "done"

    @do
    def program():
        registered = yield CreatePromise()
        # The producer is spawned first: only `registered` puts its Publish after the consumer's WaitForEvent.
        producer_task = yield Spawn(producer(registered))
        consumer_task = yield Spawn(announce_listening(registered)(consumer()))
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
