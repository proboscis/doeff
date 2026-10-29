
from dataclasses import dataclass

from doeff_core_effects import CreatePromise, Promise, Spawn, Wait
from doeff_events import Publish, WaitForEvent, event_handler
from events_test_support import announce_listening, run_scheduled

from doeff import do


@dataclass(frozen=True)
class OrderFilled:
    symbol: str
    quantity: int


def test_wait_for_event_receives_next_matching_event() -> None:
    published_event = OrderFilled(symbol="AAPL", quantity=100)

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
        received_event = yield announce_listening(registered)(WaitForEvent(OrderFilled))
        publish_status = yield Wait(publisher_task)
        return (received_event, publish_status)

    result = run_scheduled(event_handler()(program()))

    received_event, publish_status = result
    assert received_event == published_event
    assert publish_status == "published"
