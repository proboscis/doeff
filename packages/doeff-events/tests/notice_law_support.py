"""Shared pieces of the notice broker's law tests: the upper layers broken in one way each (a law must fail under
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
from doeff_events.effects.notices import (
    Announce,
    AwaitBrokerBack,
    BrokerUnreachable,
    ChannelSubscription,
    NextAnnouncement,
    SubscribeChannels,
)
from doeff_events.handlers.memory_notices import (
    MemoryBroker,
    cut_broker,
    memory_notice_handler,
    restore_broker,
)
from doeff_events.handlers.notice_events import notice_events_handler
from doeff_events.notice_laws import EventLawHarness, GapLawHarness, law_routes
from doeff_time import sim_time_handler

from doeff import K, Pass, Program, Pure, Resume, do, run
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


def swallows(event_type: type) -> "ProgramHandler":
    """Broken upper layer: events of ``event_type`` are never told (sits between ``notice_events_handler`` and
    the subscriber queue)."""

    @do
    def handler(effect: PublishEffect, k: K) -> "EffectGenerator[object]":
        """Drop the publish of the swallowed type; pass every other on."""
        if isinstance(effect.event, event_type):
            return (yield Resume(k, None))
        yield Pass(effect, k)
        return None

    return program_handler(handler)


@final
class _LateSubscription:
    """What the two halves of ``subscribes_when_the_program_waits_for`` share: the subscription answered at once,
    the real one once it is made, whether the program has waited yet, and the task waiting for that."""

    __slots__ = ("asked", "real", "waited", "waiter")

    def __init__(self) -> None:
        """Start before the program's wait, with nothing subscribed."""
        self.asked: ChannelSubscription | None = None
        self.real: ChannelSubscription | None = None
        self.waited = False
        self.waiter: Promise[object] | None = None


@final
class LateSubscriptionLayers:
    """The two halves of the broken upper layer of ``subscribes_when_the_program_waits_for``."""

    __slots__ = ("above_events", "below_events")

    def __init__(self, above_events: "ProgramHandler", below_events: "ProgramHandler") -> None:
        """Name where each half sits: between the program and ``notice_events_handler``, and between it and the
        broker."""
        self.above_events = above_events
        self.below_events = below_events


def subscribes_when_the_program_waits_for(event_type: type) -> LateSubscriptionLayers:
    """Broken upper layer: the subscription is reported as made (so ``SourceStarted`` is told) before it exists;
    it begins only when the program first waits for ``event_type`` — after its first read, after the start was
    told. The half above sees the program's wait; the half below holds the subscription back."""
    late = _LateSubscription()

    @do
    def sees_the_wait(effect: WaitForEventEffect, k: K) -> "EffectGenerator[object]":
        """At the program's first wait for the type, let the held subscription begin; pass the wait on."""
        if not late.waited and event_type in effect.event_types:
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

    return LateSubscriptionLayers(program_handler(sees_the_wait), program_handler(holds_the_subscription))


def memory_harness(
    broker: MemoryBroker,
    *,
    inside_events: Layer = unbroken,
    below_events: Layer = unbroken,
    above_events: Layer = unbroken,
) -> EventLawHarness:
    """The harness of the in-memory broker: every party has its own subscriber queue (its own process) and all
    share ``broker``. ``inside_events`` (between the program and ``notice_events_handler``), ``below_events``
    (between it and the broker) and ``above_events`` (between it and the queue) are where a broken handler goes."""

    def as_party(source: str, event_types: tuple[type, ...], body: Program[object]) -> Program[object]:
        events = notice_events_handler(source, law_routes(PREFIX), PATIENCE_SECONDS)
        queue = subscribed_event_handler(EventBus(), source, event_types)
        return queue(above_events(memory_notice_handler(broker)(below_events(events(inside_events(body))))))

    return EventLawHarness(
        as_party=as_party,
        cut=lambda: cut_broker(broker, "the in-memory broker was cut"),
        restore=lambda: restore_broker(broker),
    )


@final
class SenderOutage:
    """What ``refuses_senders`` knows of the outage a test made: why the broker refuses senders (``None`` = it takes
    them), whether the return was told since the last cut (later waits for it are answered at once, as the
    in-memory broker answers them when it is not cut), the tasks waiting in ``AwaitBrokerBack``, the promise a
    test waits on until somebody waits for the return, and how many waits were asked (to count them)."""

    __slots__ = ("asked", "back_told", "detail", "waiters", "wanted")

    def __init__(self) -> None:
        """Start with the broker taking every notice."""
        self.detail: str | None = None
        self.back_told = True
        self.waiters: tuple[Promise[object], ...] = ()
        self.wanted: Promise[object] | None = None
        self.asked = 0


def refuses_senders(outage: SenderOutage) -> "ProgramHandler":
    """The layer under ``notice_events_handler`` that takes the broker from the sending side only (the in-memory
    broker's ``cut_broker`` takes it from everyone): while the outage is on, ``Announce`` answers
    ``BrokerUnreachable``. ``AwaitBrokerBack`` is answered at once when the return was told since the last cut,
    and otherwise waits until the test tells it. Subscriptions and their waits go on to the broker untouched —
    the receivers stay connected."""

    @do
    def handler(effect: Announce | AwaitBrokerBack, k: K) -> "EffectGenerator[object]":
        """Refuse a send while the outage is on; hold a wait for the return until the test tells it."""
        if isinstance(effect, Announce):
            if outage.detail is None:
                yield Pass(effect, k)
                return None
            return (yield Resume(k, BrokerUnreachable(outage.detail)))
        outage.asked += 1
        if outage.back_told:
            return (yield Resume(k, None))
        back: Promise[object] = yield CreatePromise()
        outage.waiters = (*outage.waiters, back)
        wanted, outage.wanted = outage.wanted, None
        if wanted is not None:
            yield CompletePromise(wanted, None)
        yield Wait(back.future)
        return (yield Resume(k, None))

    return program_handler(handler)


@do
def _told_back(outage: SenderOutage) -> "EffectGenerator[None]":
    """Answer everyone waiting for the broker's return."""
    waiters, outage.waiters = outage.waiters, ()
    for waiter in waiters:
        yield CompletePromise(waiter, None)


PartyUnder = Callable[[Layer], Callable[[str, tuple[type, ...], Program[object]], Program[object]]]
"""How a broker's harness runs a party with a given layer between ``notice_events_handler`` and the broker."""


def gap_harness(
    broker: MemoryBroker, *, outage: SenderOutage | None = None, below_events: Layer = unbroken
) -> GapLawHarness:
    """The harness of ``GAP_LAWS`` on the in-memory broker: parties on ``broker`` whose sends the test refuses and
    lets through. ``below_events`` (between ``notice_events_handler`` and ``refuses_senders``) is where a broken
    handler goes; ``outage`` lets a test read what the layer saw."""
    return gap_harness_over(
        lambda layer: memory_harness(broker, below_events=layer).as_party, outage=outage, below_events=below_events
    )


def gap_harness_over(
    party_under: PartyUnder, *, outage: SenderOutage | None = None, below_events: Layer = unbroken
) -> GapLawHarness:
    """The harness of ``GAP_LAWS`` on any broker: ``party_under`` runs a party with the given layer under
    ``notice_events_handler``; this harness puts ``refuses_senders`` (and ``below_events`` above it) there."""
    known = outage if outage is not None else SenderOutage()
    refusing = refuses_senders(known)
    as_party = party_under(lambda program: refusing(below_events(program)))

    @do
    def cut() -> "EffectGenerator[None]":
        """Refuse every send from now on; the return is not told."""
        known.detail = "the broker refuses senders"
        known.back_told = False
        yield Pure(None)

    @do
    def reopen() -> "EffectGenerator[None]":
        """Take sends again; nobody is told."""
        known.detail = None
        yield Pure(None)

    @do
    def restore() -> "EffectGenerator[None]":
        """Take sends again and tell the return (to everyone waiting and to whoever asks later)."""
        known.detail = None
        known.back_told = True
        yield _told_back(known)

    @do
    def flap() -> "EffectGenerator[None]":
        """Once somebody waits for the return, tell it while sends are still refused (the broker went away again)."""
        if not known.waiters:
            wanted: Promise[object] = yield CreatePromise()
            known.wanted = wanted
            yield Wait(wanted.future)
        yield _told_back(known)

    return GapLawHarness(
        as_party=as_party, channel=f"{PREFIX}:note", cut=cut, reopen=reopen, restore=restore, flap=flap
    )


def never_tells_the_return() -> "ProgramHandler":
    """Broken layer: ``AwaitBrokerBack`` is never answered (sits between ``notice_events_handler`` and
    ``refuses_senders``)."""

    @do
    def handler(effect: AwaitBrokerBack, k: K) -> "EffectGenerator[object]":
        """Wait on a promise nobody completes."""
        never: Promise[object] = yield CreatePromise()
        yield Wait(never.future)
        return (yield Resume(k, None))

    return program_handler(handler)


def run_on_virtual_clock(program: Program[object]) -> object:
    """Run a law under the scheduler and a virtual clock (the clock only moves while a source waits for the
    broker's return)."""
    return run(scheduled(sim_time_handler(start_time=T0)(program)))
