"""Check that the event sender (``notice_events_handler``) uses no doeff-vm steps for publishes not on a route and for event waits when it only sends
(checkpoint U).

The sender's body handler accepted ``Publish`` and ``WaitForEvent`` / ``WaitForEvents`` by type and looked at whether they were on a route only after accepting them.
A publish not on a route is merely passed outward, and a wait of a send-only sender (no channel to read) merely adds ``SourceFailed`` and passes outward, but both
are called every time. In agora's simulation check (test_whole), ``PROFILE-EVENT-SENDER`` alone used 594 steps on 96 unrelated publishes, and send-only
senders of 11 kinds accepted waits 683 times in total. Each time a sender is added, a check
that has a step-count declaration exceeds its declaration by this amount.

Declaration: the step-count difference between an assembly with the sender and one without does not grow with the number of publishes not on a route or (when send-only) the number of waits.
The bus is a ``subscribed_event_handler`` with per-subscriber queues (events the body emits are also queued in its own queue), so the body only emits and then waits and
does not spawn a task — spawning a task would mix in steps that re-wrap the handler's queue over the task.
"""

from dataclasses import dataclass

from doeff_core_effects import scheduled
from doeff_core_effects._scheduler import _read_vm_steps
from doeff_events import (
    BrokerPatience,
    Drop,
    EventBus,
    MemoryBroker,
    NoticeRoute,
    NoticeSent,
    Publish,
    PublishEffect,
    WaitForEvent,
    WaitForEvents,
    broker_patience_handler,
    memory_notice_handler,
    notice_events_handler,
    subscribed_event_handler,
)

from doeff import EffectGenerator, Program, do, run, with_handlers
from doeff.program import ProgramHandler

PATIENCE_SECONDS = 30.0
CHANNEL = "sender-steps"


@dataclass(frozen=True)
class Routed:
    """An event on a route (the sender turns it into a notice)."""

    n: int


@dataclass(frozen=True)
class Local:
    """An event not on a route (it passes only through the bus inside the process)."""

    n: int


def _encoded(event: object) -> str:
    """Spelling of the route-type event (the route table is passed as a tuple of ``NoticeRoute[object]``, so what it receives is object)."""
    assert isinstance(event, Routed), event
    return str(event.n)


SENDS: "tuple[NoticeRoute[object], ...]" = (
    NoticeRoute(
        event_type=Routed,
        wire_name="routed",
        channel=lambda _event: CHANNEL,
        encode=_encoded,
        decode=lambda body: Routed(n=int(body)),
        when_unsent=Drop(),
    ),
)
READS: "tuple[NoticeRoute[object], ...]" = (
    NoticeRoute(
        event_type=Routed,
        wire_name="routed",
        channel=lambda _event: CHANNEL,
        encode=_encoded,
        decode=lambda body: Routed(n=int(body)),
        when_unsent=Drop(),
        reads=(CHANNEL,),
    ),
)


def _steps_of(program: Program[object]) -> int:
    """The number of doeff-vm steps when ``program`` is run under the scheduler."""
    before = _read_vm_steps()
    run(scheduled(program))
    return _read_vm_steps() - before


@do
def _publishes(count: int) -> EffectGenerator[None]:
    """body: emit an event not on a route ``count`` times."""
    for n in range(count):
        yield Publish(Local(n))


@do
def _waits(count: int, many: bool) -> EffectGenerator[None]:
    """body: emit an event not on a route and wait for it from its own queue, ``count`` times (``WaitForEvents`` if ``many``)."""
    for n in range(count):
        yield Publish(Local(n))
        if many:
            yield WaitForEvents(Local)
        else:
            yield WaitForEvent(Local)


def _composed(body: Program[object], *inner: ProgramHandler) -> Program[object]:
    """Wrap body with ``inner`` (outer first) under one new bus subscriber."""
    return with_handlers([subscribed_event_handler(EventBus(), "body", (Local,)), *inner], body)


def _sender_steps(body: Program[None]) -> int:
    """The number of steps added when a send-only sender is wrapped over the body (the difference from the same assembly without it)."""
    sender = notice_events_handler("steps-sender", SENDS)
    return _steps_of(_composed(body, sender)) - _steps_of(_composed(body))


def _reader_steps(body: Program[None]) -> int:
    """The number of steps added when a reading sender (subscribing to a channel) is wrapped over the body (the difference from the same assembly without it)."""
    reader = notice_events_handler("steps-reader", READS)
    patience = broker_patience_handler(BrokerPatience(PATIENCE_SECONDS))
    return _steps_of(_composed(body, memory_notice_handler(MemoryBroker()), patience, reader)) - _steps_of(
        _composed(body, memory_notice_handler(MemoryBroker()))
    )


def test_a_sender_spends_no_step_on_events_it_does_not_route() -> None:
    assert _sender_steps(_publishes(10)) == _sender_steps(_publishes(0))


def test_a_reading_source_spends_no_step_on_events_it_does_not_route() -> None:
    assert _reader_steps(_publishes(10)) == _reader_steps(_publishes(0))


def test_a_sender_that_reads_nothing_spends_no_step_on_the_bodys_waits() -> None:
    assert _sender_steps(_waits(10, many=False)) == _sender_steps(_waits(0, many=False))
    assert _sender_steps(_waits(10, many=True)) == _sender_steps(_waits(0, many=True))


@do
def _sends_directly() -> EffectGenerator[object]:
    """body: emit an event on a route by building ``PublishEffect`` directly (the shape doeff-records' memory source builds)."""
    answer: object = yield PublishEffect(Routed(1))
    return answer


def test_a_routed_event_made_as_publish_effect_is_still_sent() -> None:
    # The sender accepts only the Publish class of the route type — a directly built value is also that class, so it becomes a notice without leaking outward.
    sender = notice_events_handler("steps-sender", SENDS)
    answer = run(
        scheduled(_composed(_sends_directly(), memory_notice_handler(MemoryBroker()), sender))
    )
    assert answer == NoticeSent(receivers=0)
