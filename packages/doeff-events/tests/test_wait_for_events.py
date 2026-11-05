"""Check of ``WaitForEvents`` — waits until at least one arrives, then answers with all matching signals in its own queue at that moment, as a tuple in arrival order.

If ``WaitForEvent`` answers one at a time, the receiver folds several notices that arrived at the same time one by one and sends intermediate states outward.
``WaitForEvents`` receives everything that has arrived in one go.

- Two emitted before waiting are received as a pair of two in arrival order (``subscribed_event_handler``).
- One emitted while waiting is received as a pair of one.
- Waiting for a type outside the subscription raises ``ValueError`` (the same message as ``WaitForEvent``).
- ``event_handler``, which has no queues, answers the one that arrived as a pair of one.
- Inside the ``notice_events_handler`` wrapper: a ``SourceFailed`` from its own source raises an exception, and a ``SourceFailed`` from another source is removed from the tuple. Only a wrapper that reads a channel (has a source task)
  adds source failure to waits; a send-only wrapper does not accept waits.
"""

from dataclasses import dataclass

import pytest
from doeff_core_effects import CreatePromise, Promise, Spawn, Wait
from doeff_events import (
    Drop,
    EventBus,
    MemoryBroker,
    NoticeRoute,
    Publish,
    SourceFailed,
    WaitForEvents,
    event_handler,
    memory_notice_handler,
    subscribed_event_handler,
)
from events_test_support import Wakes, announce_listening, patient_notice_events, record_wakes, run_scheduled

from doeff import EffectGenerator, Program, do


@dataclass(frozen=True)
class Rang:
    """A signal the receiver waits for (it has no ``keys``, so it is not merged in the queue)."""

    room: str


@dataclass(frozen=True)
class Unrelated:
    """A signal outside the receiver's subscription types."""

    note: str


@dataclass(frozen=True)
class Heard:
    """A signal the wrapper receives from the channel (nobody sends it in this check — a route type only to give the wrapper a source task)."""

    note: str


HEARD_CHANNEL = "heard"


def _heard_encoded(event: object) -> str:
    """Spelling of ``Heard`` (the route table is passed as a tuple of ``NoticeRoute[object]``, so what it receives is object)."""
    assert isinstance(event, Heard), event
    return event.note


READS_HEARD: "tuple[NoticeRoute[object], ...]" = (
    NoticeRoute(
        event_type=Heard,
        wire_name="heard",
        channel=lambda _event: HEARD_CHANNEL,
        encode=_heard_encoded,
        decode=Heard,
        when_unsent=Drop(),
        reads=(HEARD_CHANNEL,),
    ),
)


class SourceBrokeError(RuntimeError):
    """The exception when the source task has failed (carried by ``SourceFailed``)."""


@do
def _publish_all(events: tuple[object, ...]) -> EffectGenerator[None]:
    """Writer: emit ``events`` in order."""
    for event in events:
        yield Publish(event)


@do
def _publish_when_listening(
    registered: Promise[object], events: tuple[object, ...]
) -> EffectGenerator[None]:
    """Writer: after the receiver has entered the wait (``announce_listening`` completed ``registered``), emit ``events`` in order."""
    yield Wait(registered.future)
    yield _publish_all(events)


@do
def _wait_rang() -> EffectGenerator[tuple[object, ...]]:
    """Receiver: receive ``Rang`` with one ``WaitForEvents``."""
    came: tuple[object, ...] = yield WaitForEvents(Rang)
    return came


def _received_after(
    published: tuple[object, ...], body: Program[tuple[object, ...]]
) -> tuple[object, ...]:
    """The answer when subscriber listener (subscribed to ``Rang``) runs ``body`` after the writer has finished emitting ``published``."""
    bus = EventBus()
    receiving = subscribed_event_handler(bus, "listener", (Rang,))
    sending = subscribed_event_handler(bus, "writer")

    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """Start the receiver's subscription, finish running the writer, and then run the receiver."""
        yield Wait((yield Spawn(sending(_publish_all(published)))))
        answer: tuple[object, ...] = yield receiving(body)
        return answer

    return run_scheduled(main())


def test_wait_for_events_answers_every_queued_signal_in_arrival_order() -> None:
    received = _received_after((Rang("a"), Rang("b")), _wait_rang())
    assert received == (Rang("a"), Rang("b")), received


def test_wait_for_events_answers_the_one_signal_published_while_it_waits() -> None:
    bus = EventBus()
    receiving = subscribed_event_handler(bus, "listener", (Rang,))
    sending = subscribed_event_handler(bus, "writer")
    wakes = Wakes()

    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """The writer emits one while the receiver is waiting."""
        registered: Promise[object] = yield CreatePromise()
        # Spawn the writer first: the order of emitting after the receiver's wait is decided only by the registration event.
        writer = yield Spawn(
            record_wakes(wakes)(sending(_publish_when_listening(registered, (Rang("a"),))))
        )
        answer: tuple[object, ...] = yield receiving(announce_listening(registered)(_wait_rang()))
        yield Wait(writer)
        return answer

    received = run_scheduled(main())
    assert received == (Rang("a"),), received
    # The subscriber's queue also stores signals from before the wait, so the answer alone does not tell whether it was woken or taken from the queue: check that the one emitted woke the waiting receiver.
    assert len(wakes.mut_promises) == 1, wakes.mut_promises


def test_wait_for_events_outside_the_subscription_is_rejected_like_wait_for_event() -> None:
    @do
    def wait_unrelated() -> EffectGenerator[tuple[object, ...]]:
        """Wait for a signal outside the subscription types (a wiring mistake)."""
        came: tuple[object, ...] = yield WaitForEvents(Unrelated)
        return came

    with pytest.raises(ValueError, match=r"subscriber 'listener' waited for a type outside its subscription: Unrelated"):
        _received_after((), wait_unrelated())


def test_the_handler_without_queues_answers_the_one_signal_as_a_tuple() -> None:
    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """The receiver emits one while waiting (a handler without queues passes only to the waiter)."""
        registered: Promise[object] = yield CreatePromise()
        # Spawn the writer first: the order of emitting after the receiver's wait is decided only by the registration event.
        writer = yield Spawn(_publish_when_listening(registered, (Rang("a"),)))
        answer: tuple[object, ...] = yield announce_listening(registered)(_wait_rang())
        yield Wait(writer)
        return answer

    received = run_scheduled(event_handler()(main()))
    assert received == (Rang("a"),), received


def _received_in_notice_wrapper(published: tuple[object, ...]) -> tuple[object, ...]:
    """The answer from doing ``WaitForEvents(Rang)`` once inside the ``notice_events_handler`` wrapper (source name mine, reading one channel)."""
    reader = patient_notice_events("mine", READS_HEARD, 30.0)
    return _received_after(published, memory_notice_handler(MemoryBroker())(reader(_wait_rang())))


def test_wait_for_events_in_the_notice_wrapper_drops_another_sources_failure() -> None:
    other = SourceFailed(source="other", error=SourceBrokeError("other"))
    received = _received_in_notice_wrapper((other, Rang("a"), Rang("b")))
    assert received == (Rang("a"), Rang("b")), received


def test_wait_for_events_in_the_notice_wrapper_raises_its_own_sources_failure() -> None:
    mine = SourceFailed(source="mine", error=SourceBrokeError("mine"))
    with pytest.raises(SourceBrokeError, match="mine"):
        _received_in_notice_wrapper((Rang("a"), mine))
