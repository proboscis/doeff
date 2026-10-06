"""Shared pieces of the event broker's law tests: the handlers broken in one way each (a law must fail under
each of them), and the harness of the in-memory broker.

Named without ``test_`` so pytest does not collect it; the memory test and the Redis test both import it.

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter, and a handler whose annotation is only text is called for every effect.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, final

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Wait, scheduled
from doeff_events import EventBus, subscribed_event_handler
from doeff_events.effects.events import PublishEffect, WaitForEventEffect
from doeff_events.effects.streams import (
    AckEntry,
    BrokerUnreachable,
    ChannelSubscription,
    ClaimedEntries,
    ClaimPending,
    EnsureGroup,
    NextAnnouncement,
    ReadGroup,
    SubscribeChannels,
)
from doeff_events.handlers.memory_streams import (
    MemoryBroker,
    cut_broker,
    memory_stream_handler,
    restore_broker,
)
from doeff_events.handlers.stream_events import stream_events_handler
from doeff_events.stream_laws import LAW_CONSUMERS, EventLawHarness, law_routes
from doeff_time import sim_time_handler

from doeff import K, Pass, Program, Resume, do, run
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

T0 = datetime(2026, 10, 6, tzinfo=timezone.utc)
PATIENCE_SECONDS = 30.0
PREFIX = "law"

Layer = Callable[[Program[object]], Program[object]]
"""A handler, as a function from a program to the program under it."""


def unbroken(program: Program[object]) -> Program[object]:
    """The place of a broken handler when nothing is broken."""
    return program


def acks_on_delivery() -> "ProgramHandler":
    """Broken upper layer: every entry is acknowledged the moment the broker hands it over, before the program
    processed it (sits between ``stream_events_handler`` and the broker)."""

    @do
    def handler(effect: ReadGroup | ClaimPending, k: K) -> "EffectGenerator[object]":
        """Pass the read on, then acknowledge everything it answered."""
        answer = yield effect
        entries = answer.entries if isinstance(answer, ClaimedEntries) else answer
        if not isinstance(entries, BrokerUnreachable):
            for entry in entries:
                yield AckEntry(entry.stream, effect.group, entry.position)
        return (yield Resume(k, answer))

    return program_handler(handler)


def swallows(event_type: type) -> "ProgramHandler":
    """Broken upper layer: events of ``event_type`` are never told (sits between ``stream_events_handler`` and
    the subscriber queue)."""

    @do
    def handler(effect: PublishEffect, k: K) -> "EffectGenerator[object]":
        """Drop the publish of the swallowed type; pass every other on."""
        if isinstance(effect.event, event_type):
            return (yield Resume(k, None))
        yield Pass(effect, k)
        return None

    return program_handler(handler)


def gives_to_every_consumer() -> "ProgramHandler":
    """Broken broker: an entry is given to every consumer of a group (each consumer gets a private group, made
    when the shared one is — sits above the broker's handler)."""

    @do
    def handler(effect: EnsureGroup | ReadGroup, k: K) -> "EffectGenerator[object]":
        """Make and read one group per consumer in place of the shared one."""
        if isinstance(effect, EnsureGroup):
            for consumer in LAW_CONSUMERS:
                yield EnsureGroup(effect.stream, f"{effect.group}/{consumer}")
            return (yield Resume(k, None))
        answer = yield ReadGroup(effect.streams, f"{effect.group}/{effect.consumer}", effect.consumer)
        return (yield Resume(k, answer))

    return program_handler(handler)


@final
class _LateSubscription:
    """What the two halves of ``subscribes_at_the_first_wait`` share: the channels asked for, whether the body
    has waited yet, the task waiting for that, and the real subscription once it is made."""

    __slots__ = ("asked", "real", "waited", "waiter")

    def __init__(self) -> None:
        """Start before the body's first wait, with nothing subscribed."""
        self.asked: ChannelSubscription | None = None
        self.real: ChannelSubscription | None = None
        self.waited = False
        self.waiter: Promise[object] | None = None


def subscribes_at_the_first_wait() -> "tuple[ProgramHandler, ProgramHandler]":
    """Broken upper layer: the subscription to the channels begins only when the program first waits, after its
    first read. Two halves: the first sits between the program and ``stream_events_handler`` (it sees the wait),
    the second between ``stream_events_handler`` and the broker (it holds the subscription back)."""
    late = _LateSubscription()

    @do
    def sees_the_wait(effect: WaitForEventEffect, k: K) -> "EffectGenerator[object]":
        """At the program's first wait, let the held subscription begin; pass the wait on."""
        if not late.waited:
            late.waited = True
            if late.waiter is not None:
                yield CompletePromise(late.waiter, None)
        yield Pass(effect, k)
        return None

    @do
    def holds_the_subscription(effect: SubscribeChannels | NextAnnouncement, k: K) -> "EffectGenerator[object]":
        """Answer the subscription at once without making it; make it when the program has waited."""
        if isinstance(effect, SubscribeChannels):
            late.asked = ChannelSubscription(effect.channels)
            return (yield Resume(k, late.asked))
        if effect.subscription is not late.asked:
            yield Pass(effect, k)
            return None
        if late.real is None:
            if not late.waited:
                late.waiter = yield CreatePromise()
                yield Wait(late.waiter.future)
            late.real = yield SubscribeChannels(effect.subscription.channels)
        answer = yield NextAnnouncement(late.real)
        return (yield Resume(k, answer))

    return program_handler(sees_the_wait), program_handler(holds_the_subscription)


def memory_harness(broker: MemoryBroker, *, below_events: Layer = unbroken, above_events: Layer = unbroken) -> EventLawHarness:
    """The harness of the in-memory broker: every party has its own subscriber queue (its own process) and all
    share ``broker``. ``below_events`` / ``above_events`` are where a broken handler is put."""

    def as_party(consumer: str, event_types: tuple[type, ...], body: Program[object]) -> Program[object]:
        events = stream_events_handler(consumer, law_routes(PREFIX), PATIENCE_SECONDS)
        queue = subscribed_event_handler(EventBus(), consumer, event_types)
        return queue(above_events(memory_stream_handler(broker)(below_events(events(body)))))

    return EventLawHarness(
        as_party=as_party,
        cut=lambda: cut_broker(broker, "the in-memory broker was cut"),
        restore=lambda: restore_broker(broker),
    )


def run_on_virtual_clock(program: Program[object]) -> object:
    """Run a law under the scheduler and a virtual clock (the clock only moves while a source waits for the
    broker's return)."""
    return run(scheduled(sim_time_handler(start_time=T0)(program)))
