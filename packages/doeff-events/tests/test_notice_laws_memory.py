"""The delivery laws of the notice broker on the in-memory broker, and the proof that each law catches the upper
layer broken in its way (agora-redesign #3850). These always run — no server is needed.

Also: the shared signal invariants of ``event_signal_invariants`` applied to this source (every subscriber is a
process of its own with its own queue; a signal travels through the broker as a notice), and the checks of the
factory's arguments.
"""

import json
from collections.abc import Hashable
from typing import TYPE_CHECKING

import pytest
from doeff_core_effects.scheduler import SchedulerDeadlockError
from doeff_events import EventBus, SourceResumed, SourceStarted, subscribed_event_handler
from doeff_events.effects.events import Publish, WaitForEvent
from doeff_events.effects.notices import Announce, BrokerUnreachable
from doeff_events.handlers.memory_notices import MemoryBroker, cut_broker, memory_notice_handler
from doeff_events.handlers.notice_events import (
    NoticeHeld,
    NoticeRoute,
    NoticeSent,
    NoticeSourceUnreachable,
    UnroutedNotice,
    notice_events_handler,
)
from doeff_events.notice_laws import (
    BROKER_LAWS,
    EVENT_LAWS,
    HELD_LAWS,
    LawBroken,
    LawNote,
    law_held_notice_arrives_once_the_broker_is_back,
    law_routes,
    law_start_and_return_are_told_once,
    law_start_is_told_after_the_subscription,
)
from event_signal_invariants import (
    Changed,
    SignalWorld,
    check_duplicate_signal_gives_same_state,
    check_signal_between_read_and_wait_is_kept,
    check_wait_outside_subscription_is_rejected,
)
from notice_law_support import (
    PATIENCE_SECONDS,
    PREFIX,
    memory_harness,
    never_tells_the_return,
    run_on_virtual_clock,
    sender_outage_harness,
    subscribes_when_the_program_waits_for,
    swallows,
)

from doeff import Program, Pure, do

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler


@pytest.mark.parametrize("law", EVENT_LAWS, ids=lambda law: law.__name__)
def test_event_law_holds_on_memory(law) -> None:
    run_on_virtual_clock(law(memory_harness(MemoryBroker())))


@pytest.mark.parametrize("law", BROKER_LAWS, ids=lambda law: law.__name__)
def test_broker_law_holds_on_memory(law) -> None:
    run_on_virtual_clock(memory_notice_handler(MemoryBroker())(law(PREFIX)))


def test_not_telling_the_return_breaks_the_start_and_return_law() -> None:
    # The program waits for the return and is never told: nothing else can wake it, so the run ends as a dead end.
    harness = memory_harness(MemoryBroker(), above_events=swallows(SourceResumed))
    with pytest.raises(SchedulerDeadlockError):
        run_on_virtual_clock(law_start_and_return_are_told_once(harness))


def test_telling_the_start_before_the_subscription_breaks_the_start_law() -> None:
    broken = subscribes_when_the_program_waits_for(LawNote)
    harness = memory_harness(MemoryBroker(), inside_events=broken.above_events, below_events=broken.below_events)
    with pytest.raises(LawBroken, match="start-is-told-after-the-subscription"):
        run_on_virtual_clock(law_start_is_told_after_the_subscription(harness))


def _signal_world(*, late_subscription: bool = False) -> SignalWorld:
    """The shared invariants' world on this source: every subscriber has a queue of its own and ``Changed``
    travels through one in-memory broker as a notice. ``late_subscription`` puts in the upper layer broken so
    that its subscription begins at the program's first wait for ``Changed``."""
    broker = MemoryBroker()
    route: NoticeRoute[Changed] = NoticeRoute(
        event_type=Changed,
        wire_name="changed",
        channel=lambda _event: "signals",
        encode=lambda event: json.dumps(list(event.keys)),
        decode=lambda body: Changed(tuple(json.loads(body))),
        held_key=lambda event: event.keys,
        reads=("signals",),
    )

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program["ProgramHandler"]:
        def wrap(body: Program[object]) -> Program[object]:
            events = notice_events_handler(subscriber, (route,), PATIENCE_SECONDS)
            queue = subscribed_event_handler(EventBus(), subscriber, event_types)
            if late_subscription and event_types:
                broken = subscribes_when_the_program_waits_for(Changed)
                under = broken.below_events(events(broken.above_events(body)))
                return queue(memory_notice_handler(broker)(under))
            return queue(memory_notice_handler(broker)(events(body)))

        return Pure(wrap)

    def row(key: str) -> Hashable:
        return key

    return SignalWorld(subscribe=subscribe, run=run_on_virtual_clock, row=row)


@pytest.mark.parametrize(
    "invariant",
    [
        check_signal_between_read_and_wait_is_kept,
        check_duplicate_signal_gives_same_state,
        check_wait_outside_subscription_is_rejected,
    ],
    ids=lambda invariant: invariant.__name__,
)
def test_shared_signal_invariant_holds_across_processes(invariant) -> None:
    # The two invariants about a subscription that begins when the handler is built are not applied: like
    # doeff-records' source, this one begins its subscription at the head of the wrapped body (it needs effects).
    invariant(_signal_world())


def test_subscribing_after_the_first_read_breaks_the_shared_gap_invariant() -> None:
    # The notice sent between the reader's read and its wait is gone, so the reader is never woken: the scheduler
    # ends the run as a dead end (what event_signal_invariants says of a handler that drops such a signal).
    with pytest.raises(SchedulerDeadlockError):
        check_signal_between_read_and_wait_is_kept(_signal_world(late_subscription=True))


def test_publish_answers_the_number_of_receivers() -> None:
    @do
    def publishes_alone() -> "EffectGenerator[object]":
        return (yield Publish(LawNote("to nobody")))

    sender_only = tuple(
        NoticeRoute(r.event_type, r.wire_name, r.channel, r.encode, r.decode, r.held_key) for r in law_routes(PREFIX)
    )
    wrapped = memory_notice_handler(MemoryBroker())(
        notice_events_handler("sender", sender_only, PATIENCE_SECONDS)(publishes_alone())
    )
    assert run_on_virtual_clock(subscribed_event_handler(EventBus(), "sender")(wrapped)) == NoticeSent(0)


@pytest.mark.parametrize("law", HELD_LAWS, ids=lambda law: law.__name__)
def test_held_law_holds_on_memory(law) -> None:
    run_on_virtual_clock(law(sender_outage_harness(MemoryBroker())))


def test_not_telling_the_return_breaks_the_held_law() -> None:
    # The held note waits for the broker's return, and nothing else sends it: the reader's wait is never answered.
    harness = sender_outage_harness(MemoryBroker(), below_events=never_tells_the_return())
    with pytest.raises(SchedulerDeadlockError):
        run_on_virtual_clock(law_held_notice_arrives_once_the_broker_is_back(harness))


def test_publish_answers_held_with_the_brokers_words_while_it_is_unreachable() -> None:
    broker = MemoryBroker()

    @do
    def publishes_into_an_outage() -> "EffectGenerator[object]":
        yield cut_broker(broker, "cut for the test")
        return (yield Publish(LawNote("held")))

    sender_only = tuple(
        NoticeRoute(r.event_type, r.wire_name, r.channel, r.encode, r.decode, r.held_key) for r in law_routes(PREFIX)
    )
    wrapped = memory_notice_handler(broker)(
        notice_events_handler("sender", sender_only, PATIENCE_SECONDS)(publishes_into_an_outage())
    )
    answer = run_on_virtual_clock(subscribed_event_handler(EventBus(), "sender")(wrapped))
    assert isinstance(answer, NoticeHeld), answer
    assert "cut for the test" in answer.detail, answer


def test_notice_announced_during_an_outage_is_not_delivered_later() -> None:
    # What the laws do not promise, pinned down: the subscription a cut ended does not hand over later what the
    # broker was asked to announce before the new subscription (asked of the lower layer, so nothing holds it).
    # The reader is told the return and catches up from its records.
    harness = memory_harness(MemoryBroker())

    @do
    def reader() -> "EffectGenerator[object]":
        yield WaitForEvent(SourceStarted)
        yield harness.cut()
        refused = yield Announce(f"{PREFIX}:note", "law-note", "during")
        assert isinstance(refused, BrokerUnreachable), refused
        yield harness.restore()
        yield WaitForEvent(SourceResumed)
        yield Publish(LawNote("after"))
        return (yield WaitForEvent(LawNote))

    party = harness.as_party("law-outage", (SourceStarted, SourceResumed, LawNote), reader())
    assert run_on_virtual_clock(party) == LawNote("after")


def test_source_fails_when_the_broker_stays_away_past_the_patience() -> None:
    harness = memory_harness(MemoryBroker())

    @do
    def waits_through_an_outage() -> "EffectGenerator[object]":
        yield harness.cut()
        return (yield WaitForEvent(LawNote))

    with pytest.raises(NoticeSourceUnreachable, match="law-patient"):
        run_on_virtual_clock(harness.as_party("law-patient", (LawNote,), waits_through_an_outage()))


def test_notice_without_a_route_fails_the_source_by_name() -> None:
    broker = MemoryBroker()
    harness = memory_harness(broker)

    @do
    def hears_a_stranger() -> "EffectGenerator[object]":
        from doeff_events.effects.notices import Announce

        yield Announce(f"{PREFIX}:note", "stranger", "?")
        return (yield WaitForEvent(LawNote))

    with pytest.raises(UnroutedNotice, match="stranger"):
        run_on_virtual_clock(harness.as_party("law-strict", (LawNote,), hears_a_stranger()))


def test_unrouted_event_stays_in_the_process() -> None:
    class Local:
        pass

    @do
    def publishes_local() -> "EffectGenerator[object]":
        local = Local()
        yield Publish(local)
        return (yield WaitForEvent(Local)) is local

    party = memory_harness(MemoryBroker()).as_party("law-local", (Local,), publishes_local())
    assert run_on_virtual_clock(party) is True
