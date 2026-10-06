"""The delivery laws of the event broker on the in-memory broker, and the proof that each law catches the handler
broken in its way (agora-redesign #3850). These always run — no server is needed.

Also: the shared signal invariants of ``event_signal_invariants`` applied to this source (every subscriber is a
process of its own with its own queue; a signal travels through the broker as a notice), and the checks of the
factory's arguments.
"""

from __future__ import annotations

import json
from collections.abc import Hashable
from typing import TYPE_CHECKING

import pytest
from doeff_core_effects.scheduler import SchedulerDeadlockError, scheduled
from doeff_events import EventBus, SourceGap, SourceResumed, subscribed_event_handler
from doeff_events.effects.events import Publish, WaitForEvent
from doeff_events.effects.streams import GroupPosition, head_was_cut, position_key
from doeff_events.handlers.memory_streams import MemoryBroker, cut_broker, memory_stream_handler
from doeff_events.handlers.stream_events import (
    EventNotPublished,
    EventRoute,
    RouteKind,
    StreamSourceUnreachable,
    stream_events_handler,
)
from doeff_events.stream_laws import (
    BROKER_LAWS,
    EVENT_LAWS,
    LawBroken,
    LawWork,
    law_cut_head_is_told,
    law_entry_reaches_one_consumer_of_a_group,
    law_routes,
    law_start_and_return_are_told_once,
    law_unfinished_event_comes_again,
)
from event_signal_invariants import (
    Changed,
    SignalWorld,
    check_duplicate_signal_gives_same_state,
    check_signal_between_read_and_wait_is_kept,
    check_wait_outside_subscription_is_rejected,
)
from stream_law_support import (
    PATIENCE_SECONDS,
    PREFIX,
    acks_on_delivery,
    gives_to_every_consumer,
    memory_harness,
    run_on_virtual_clock,
    subscribes_at_the_first_wait,
    swallows,
)

from doeff import Program, Pure, do, run

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler


@pytest.mark.parametrize("law", EVENT_LAWS, ids=lambda law: law.__name__)
def test_event_law_holds_on_memory(law) -> None:
    run_on_virtual_clock(law(memory_harness(MemoryBroker())))


@pytest.mark.parametrize("law", BROKER_LAWS, ids=lambda law: law.__name__)
def test_broker_law_holds_on_memory(law) -> None:
    run_on_virtual_clock(memory_stream_handler(MemoryBroker())(law(PREFIX)))


def test_acknowledging_on_delivery_breaks_the_redelivery_law() -> None:
    harness = memory_harness(MemoryBroker(), below_events=acks_on_delivery())
    with pytest.raises(LawBroken, match="unfinished-event-comes-again"):
        run_on_virtual_clock(law_unfinished_event_comes_again(harness))


def test_not_telling_the_return_breaks_the_start_and_return_law() -> None:
    harness = memory_harness(MemoryBroker(), above_events=swallows(SourceResumed))
    with pytest.raises(LawBroken, match="start-and-return-are-told-once"):
        run_on_virtual_clock(law_start_and_return_are_told_once(harness))


def test_not_telling_the_cut_head_breaks_the_gap_law() -> None:
    harness = memory_harness(MemoryBroker(), above_events=swallows(SourceGap))
    with pytest.raises(LawBroken, match="cut-head-is-told"):
        run_on_virtual_clock(law_cut_head_is_told(harness))


def test_giving_an_entry_to_two_consumers_of_a_group_breaks_the_one_consumer_law() -> None:
    broken = memory_stream_handler(MemoryBroker())(gives_to_every_consumer()(law_entry_reaches_one_consumer_of_a_group(PREFIX)))
    with pytest.raises(LawBroken, match="entry-reaches-one-consumer-of-a-group"):
        run_on_virtual_clock(broken)


def _signal_world(*, late_subscription: bool = False) -> SignalWorld:
    """The shared invariants' world on this source: every subscriber has a queue of its own and ``Changed``
    travels through one in-memory broker as a notice. ``late_subscription`` puts in the upper layer broken so
    that its subscription begins at the program's first wait."""
    broker = MemoryBroker()
    route: EventRoute[Changed] = EventRoute(
        event_type=Changed,
        wire_name="changed",
        kind=RouteKind.NOTICE,
        place=lambda _event: "signals",
        encode=lambda event: json.dumps(list(event.keys)),
        decode=lambda _position, body: Changed(tuple(json.loads(body))),
        reads=("signals",),
    )

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program[ProgramHandler]:
        def wrap(body: Program[object]) -> Program[object]:
            events = stream_events_handler(subscriber, (route,), PATIENCE_SECONDS)
            queue = subscribed_event_handler(EventBus(), subscriber, event_types)
            if late_subscription and event_types:
                sees_the_wait, holds_the_subscription = subscribes_at_the_first_wait()
                return queue(memory_stream_handler(broker)(holds_the_subscription(events(sees_the_wait(body)))))
            return queue(memory_stream_handler(broker)(events(body)))

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


def test_publish_raises_when_the_broker_is_unreachable() -> None:
    broker = MemoryBroker()

    @do
    def publishes_into_an_outage() -> EffectGenerator[str]:
        yield cut_broker(broker, "cut for the test")
        try:
            yield Publish(LawWork("", "lost"))
        except EventNotPublished as error:
            return str(error)
        return "published"

    routes = tuple(EventRoute(r.event_type, r.wire_name, r.kind, r.place, r.encode, r.decode) for r in law_routes(PREFIX))
    wrapped = memory_stream_handler(broker)(stream_events_handler("sender", routes, PATIENCE_SECONDS)(publishes_into_an_outage()))
    message = run_on_virtual_clock(subscribed_event_handler(EventBus(), "sender")(wrapped))
    assert "cut for the test" in message, message


def test_source_fails_when_the_broker_stays_away_past_the_patience() -> None:
    broker = MemoryBroker()
    harness = memory_harness(broker)

    @do
    def waits_through_an_outage() -> EffectGenerator[object]:
        yield harness.cut()
        return (yield WaitForEvent(LawWork))

    with pytest.raises(StreamSourceUnreachable, match="law-patient"):
        run_on_virtual_clock(harness.as_party("law-patient", (LawWork,), waits_through_an_outage()))


def test_unrouted_event_stays_in_the_process() -> None:
    class Local:
        pass

    @do
    def publishes_local() -> EffectGenerator[object]:
        local = Local()
        yield Publish(local)
        return (yield WaitForEvent(Local)) is local

    party = memory_harness(MemoryBroker()).as_party("law-local", (Local,), publishes_local())
    assert run_on_virtual_clock(party) is True


def test_acknowledged_event_type_with_keys_is_rejected() -> None:
    route: EventRoute[Changed] = EventRoute(Changed, "changed", RouteKind.ACKED_STREAM, lambda _e: "s", str, lambda _p, _b: Changed(()))
    with pytest.raises(ValueError, match="keys"):
        stream_events_handler("reader", (route,), PATIENCE_SECONDS)


def test_head_was_cut_rule() -> None:
    assert not head_was_cut(GroupPosition("0-0", None, "0-0"))
    assert not head_was_cut(GroupPosition("5-0", "3-0", "9-0"))
    assert head_was_cut(GroupPosition("5-0", "7-0", "9-0"))
    assert head_was_cut(GroupPosition("5-0", None, "9-0"))
    assert position_key("1700000000000-12") < position_key("1700000000001-0") < position_key("1700000000001-3")
    with pytest.raises(ValueError, match="position"):
        position_key("not-a-position")


def test_reading_before_the_group_exists_is_a_named_error() -> None:
    from doeff_events.effects.streams import ReadGroup
    from doeff_events.handlers.memory_streams import NoSuchGroup

    with pytest.raises(NoSuchGroup, match="nobody"):
        run(scheduled(memory_stream_handler(MemoryBroker())(ReadGroup(("s",), "nobody", "c"))))
