
from dataclasses import dataclass

from doeff_core_effects import CreatePromise, Promise, Spawn, Wait
from doeff_events import Publish, WaitForEvent, event_handler
from events_test_support import announce_listening, run_scheduled

from doeff import do


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
    def publisher(registered: Promise[object]):
        # The non-matching event must come while the waiter listens (a Publish before that is dropped, and the test
        # would not see the waiter ignore it): publish once it is registered. The matching one follows in this
        # task's own order.
        yield Wait(registered.future)
        yield Publish(PriceUpdated(symbol="AAPL", price_cents=10100))
        yield Publish(matching_event)
        return "published"

    @do
    def program():
        registered = yield CreatePromise()
        # The publisher is spawned first: only `registered` puts its Publish after the waiter's WaitForEvent.
        publisher_task = yield Spawn(publisher(registered))
        waiter_task = yield Spawn(announce_listening(registered)(wait_for_fill()))
        received = yield Wait(waiter_task)
        publish_status = yield Wait(publisher_task)
        return (received, publish_status)

    result = run_scheduled(event_handler()(program()))

    received, publish_status = result
    assert publish_status == "published"
    assert received == matching_event
    assert isinstance(received, OrderFilled)


def test_wait_for_base_type_receives_subclass_events() -> None:
    published_event = PriceUpdated(symbol="MSFT", price_cents=42500)

    @do
    def publisher(registered: Promise[object]):
        # event_handler drops a Publish that comes before the program listens: publish once it is registered.
        yield Wait(registered.future)
        yield Publish(published_event)
        return "published"

    @do
    def program():
        registered = yield CreatePromise()
        publisher_task = yield Spawn(publisher(registered))
        received = yield announce_listening(registered)(WaitForEvent(MarketEvent))
        publish_status = yield Wait(publisher_task)
        return (received, publish_status)

    result = run_scheduled(event_handler()(program()))

    received, publish_status = result
    assert publish_status == "published"
    assert received == published_event
    assert isinstance(received, PriceUpdated)
