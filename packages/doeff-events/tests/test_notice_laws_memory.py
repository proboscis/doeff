"""The delivery laws of the notice broker on the in-memory broker, and the proof that each law catches the upper
layer broken in its way (agora-redesign #3850). These always run — no server is needed.

Also: the shared signal invariants of ``event_signal_invariants`` applied to this source (every subscriber is a
process of its own with its own queue; a signal travels through the broker as a notice), and the checks of the
factory's arguments.
"""

import json
from collections.abc import Hashable
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from doeff_core_effects.scheduler import (
    CompletePromise,
    CreatePromise,
    SchedulerDeadlockError,
    Spawn,
    Wait,
)
from doeff_events import (
    EventBus,
    SourceMissed,
    SourceResumed,
    SourceStarted,
    subscribed_event_handler,
)
from doeff_events.effects.events import Publish, WaitForEvent
from doeff_events.effects.notices import Announce, AwaitBrokerBack, BrokerUnreachable
from doeff_events.handlers.memory_notices import MemoryBroker, cut_broker, memory_notice_handler
from doeff_events.handlers.notice_events import (
    GAP_NOTICE,
    Drop,
    MarkGap,
    NoticeDropped,
    NoticeGapMarked,
    NoticeRoute,
    NoticeSent,
    NoticeSourceUnreachable,
    UnroutedNotice,
    notice_events_handler,
)
from doeff_events.notice_laws import (
    BROKER_LAWS,
    EVENT_LAWS,
    GAP_LAWS,
    LawBroken,
    LawNote,
    law_missed_notice_is_told_once_the_broker_is_back,
    law_routes,
    law_start_and_return_are_told_once,
    law_start_is_told_after_the_subscription,
)
from doeff_time import Delay, GetTime
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
    T0,
    SenderOutage,
    gap_harness,
    memory_harness,
    never_tells_the_return,
    refuses_senders,
    run_on_virtual_clock,
    subscribes_when_the_program_waits_for,
    swallows,
)

from doeff import K, Pass, Program, Pure, do
from doeff import handler as program_handler

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
        when_unsent=MarkGap(),
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


def _sender_only(when_unsent: "MarkGap | Drop") -> "tuple[NoticeRoute[object], ...]":
    """``law_routes`` without the channels they read (a wrapper that only sends), each with ``when_unsent``."""
    return tuple(
        NoticeRoute(r.event_type, r.wire_name, r.channel, r.encode, r.decode, when_unsent) for r in law_routes(PREFIX)
    )


def test_publish_answers_the_number_of_receivers() -> None:
    # Guard: a notice nobody received is not a gap — nothing is marked and nothing is told later.
    @do
    def publishes_alone() -> "EffectGenerator[object]":
        return (yield Publish(LawNote("to nobody")))

    wrapped = memory_notice_handler(MemoryBroker())(
        notice_events_handler("sender", _sender_only(MarkGap()), PATIENCE_SECONDS)(publishes_alone())
    )
    assert run_on_virtual_clock(subscribed_event_handler(EventBus(), "sender")(wrapped)) == NoticeSent(0)


def test_publish_answers_a_marked_gap_with_the_brokers_words_and_the_body_goes_on() -> None:
    broker = MemoryBroker()

    @do
    def publishes_into_an_outage() -> "EffectGenerator[object]":
        yield cut_broker(broker, "cut for the test")
        answer = yield Publish(LawNote("lost"))
        return (answer, "went on")

    wrapped = memory_notice_handler(broker)(
        notice_events_handler("sender", _sender_only(MarkGap()), PATIENCE_SECONDS)(publishes_into_an_outage())
    )
    answer, went_on = run_on_virtual_clock(subscribed_event_handler(EventBus(), "sender")(wrapped))
    assert isinstance(answer, NoticeGapMarked), answer
    assert answer.channel == f"{PREFIX}:note"
    assert "cut for the test" in answer.detail, answer
    assert went_on == "went on"


@pytest.mark.parametrize("law", GAP_LAWS, ids=lambda law: law.__name__)
def test_gap_law_holds_on_memory(law) -> None:
    run_on_virtual_clock(law(gap_harness(MemoryBroker())))


def test_not_telling_the_return_breaks_the_missed_notice_law() -> None:
    # The gap waits for the broker's return, and nothing else tells it: the wait for the gap is never answered.
    harness = gap_harness(MemoryBroker(), below_events=never_tells_the_return())
    with pytest.raises(SchedulerDeadlockError):
        run_on_virtual_clock(law_missed_notice_is_told_once_the_broker_is_back(harness))


def test_a_dropped_route_marks_nothing_and_says_so() -> None:
    # Guard: a route declared Drop is not held: Publish answers NoticeDropped and no gap is told after the return.
    outage = SenderOutage()
    harness = gap_harness(MemoryBroker(), outage=outage)
    routes = _sender_only(Drop())
    channel = f"{PREFIX}:note"
    reading = tuple(NoticeRoute(r.event_type, r.wire_name, r.channel, r.encode, r.decode, r.when_unsent, (channel,))
                    for r in routes)

    @do
    def drops_one() -> "EffectGenerator[object]":
        yield harness.cut()
        answer = yield Publish(LawNote("stale"))
        yield harness.restore()
        yield Publish(LawNote("end"))
        return (answer, (yield WaitForEvent(SourceMissed, LawNote)))

    events = notice_events_handler("dropper", reading, PATIENCE_SECONDS)
    queue = subscribed_event_handler(EventBus(), "dropper", (SourceMissed, LawNote))
    party = queue(memory_notice_handler(MemoryBroker())(refuses_senders(outage)(events(drops_one()))))
    answer, first = run_on_virtual_clock(party)
    assert isinstance(answer, NoticeDropped), answer
    assert first == LawNote("end"), first
    assert outage.asked == 0, "a dropped notice does not wait for the broker's return"


def test_many_missed_notices_on_one_channel_are_told_as_one_gap() -> None:
    # Guard: what is held is bounded by the channels — 50 missed notices on one channel are one gap.
    harness = gap_harness(MemoryBroker())

    @do
    def misses_many() -> "EffectGenerator[object]":
        yield harness.cut()
        for number in range(50):
            yield Publish(LawNote(f"lost-{number}"))
        yield harness.restore()
        yield Publish(LawNote("end"))
        heard: tuple[object, ...] = ()
        while (came := (yield WaitForEvent(SourceMissed, LawNote))) != LawNote("end"):
            heard = (*heard, came)
        return heard

    assert run_on_virtual_clock(harness.as_party("law-many", (SourceMissed, LawNote), misses_many())) == (
        SourceMissed("law-many", f"{PREFIX}:note"),
    )


def test_a_gap_left_when_a_body_ends_does_not_reach_the_next_body() -> None:
    # Guard: the marks belong to one wrapped body. A body that ends during an outage leaves its gap behind (its
    # process would restart and tell the gap at its start); the next body on the same broker is told nothing.
    broker = MemoryBroker()
    first = gap_harness(broker)
    second = gap_harness(broker)

    @do
    def leaves_a_gap() -> "EffectGenerator[None]":
        yield first.cut()
        yield Publish(LawNote("lost"))

    @do
    def starts_clean() -> "EffectGenerator[object]":
        yield Publish(LawNote("end"))
        return (yield WaitForEvent(SourceMissed, LawNote))

    run_on_virtual_clock(first.as_party("law-first", (LawNote,), leaves_a_gap()))
    assert run_on_virtual_clock(second.as_party("law-second", (SourceMissed, LawNote), starts_clean())) == LawNote(
        "end"
    )


def test_a_sender_tells_a_gap_on_its_start_channels_when_it_starts() -> None:
    # A sender whose previous process may have ended holding a gap tells it when it starts: a reader connected
    # before the sender started is told SourceMissed on that channel.
    broker = MemoryBroker()
    channel = f"{PREFIX}:note"
    starting = tuple(
        NoticeRoute(r.event_type, r.wire_name, r.channel, r.encode, r.decode, MarkGap(start_channels=(channel,)))
        for r in law_routes(PREFIX)
    )
    harness = memory_harness(broker)

    @do
    def reader_then_sender() -> "EffectGenerator[object]":
        from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Spawn, Wait

        ready = yield CreatePromise()

        @do
        def reads() -> "EffectGenerator[object]":
            yield WaitForEvent(SourceStarted)
            yield CompletePromise(ready, None)
            return (yield WaitForEvent(SourceMissed, LawNote))

        reader = yield Spawn(harness.as_party("law-reader", (SourceStarted, SourceMissed, LawNote), reads()))
        yield Wait(ready.future)
        sender = memory_notice_handler(broker)(
            notice_events_handler("law-restarted", starting, PATIENCE_SECONDS)(Pure(None))
        )
        yield subscribed_event_handler(EventBus(), "law-restarted")(sender)
        return (yield Wait(reader))

    assert run_on_virtual_clock(reader_then_sender()) == SourceMissed("law-reader", channel)


def test_a_return_nobody_can_tell_ends_the_body_with_that_error_after_the_next_publish_told_the_gap() -> None:
    # When nothing can answer AwaitBrokerBack, the task waiting for the return fails; the body goes on, the next
    # Publish that gets through still tells the gap first, and the failure is raised when the body ends.
    @do
    def cannot_tell(effect: AwaitBrokerBack, k) -> "EffectGenerator[object]":
        raise RuntimeError("nobody can tell whether the broker is back")
        yield

    from doeff import handler as program_handler

    harness = gap_harness(MemoryBroker(), below_events=program_handler(cannot_tell))
    # The body's answer is lost to the raise at its end, so it leaves what it heard here.
    seen = SimpleNamespace(heard=())

    @do
    def sends_on() -> "EffectGenerator[None]":
        yield harness.cut()
        yield Publish(LawNote("lost"))
        yield harness.reopen()
        sent = yield Publish(LawNote("next"))
        assert isinstance(sent, NoticeSent), sent
        first = yield WaitForEvent(SourceMissed, LawNote)
        second = yield WaitForEvent(SourceMissed, LawNote)
        seen.heard = (first, second)

    with pytest.raises(RuntimeError, match="nobody can tell"):
        run_on_virtual_clock(harness.as_party("law-unknown", (SourceMissed, LawNote), sends_on()))
    assert seen.heard == (SourceMissed("law-unknown", f"{PREFIX}:note"), LawNote("next"))


def test_the_wait_for_the_return_stops_once_the_next_publish_told_the_gap() -> None:
    # Once the next Publish told the gap, nothing waits for the return any more: telling the return afterwards
    # makes nobody send again, and the body's end finds no waiting task left to stop.
    outage = SenderOutage()
    harness = gap_harness(MemoryBroker(), outage=outage)

    @do
    def clears_by_publishing() -> "EffectGenerator[object]":
        yield harness.cut()
        yield Publish(LawNote("lost"))
        yield harness.reopen()
        yield Publish(LawNote("next"))
        asked_before = outage.asked
        yield harness.restore()
        yield Publish(LawNote("end"))
        heard: tuple[object, ...] = ()
        while (came := (yield WaitForEvent(SourceMissed, LawNote))) != LawNote("end"):
            heard = (*heard, came)
        return (asked_before, outage.asked, heard)

    asked_before, asked_after, heard = run_on_virtual_clock(
        harness.as_party("law-stop", (SourceMissed, LawNote), clears_by_publishing())
    )
    assert asked_before == 1, asked_before
    assert asked_after == 1, asked_after
    assert heard == (SourceMissed("law-stop", f"{PREFIX}:note"), LawNote("next")), heard


def test_notice_announced_during_an_outage_is_not_delivered_later() -> None:
    # What the laws do not promise, pinned down: the subscription a cut ended does not hand over later what was
    # announced before the new subscription — only a gap is told. The reader catches up from its records.
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


def _fails_the_first_gap_notice(raised: SimpleNamespace) -> "ProgramHandler":
    """Broken lower layer: the first gap notice raises (a broker client that fails in a way it does not answer as
    unreachable); every other send goes on."""

    @do
    def handler(effect: Announce, k: K) -> "EffectGenerator[object]":
        """Raise once on a gap notice; pass everything else on."""
        if effect.name == GAP_NOTICE and not raised.done:
            raised.done = True
            raise RuntimeError("the client failed while telling the gap")
        yield Pass(effect, k)
        return None

    return program_handler(handler)


def test_an_error_while_telling_a_gap_leaves_the_exit_free_for_the_next_publish() -> None:
    # Fix C of the review: the task telling the gap fails in the middle; the exit is given back, so the next
    # Publish is answered (it tells the gap itself and is sent) instead of waiting forever, and the failure is
    # raised when the body ends.
    raised = SimpleNamespace(done=False)
    harness = gap_harness(MemoryBroker(), below_events=_fails_the_first_gap_notice(raised))
    seen = SimpleNamespace(answer=None, heard=())

    @do
    def sends_after_a_failed_telling() -> "EffectGenerator[None]":
        yield harness.cut()
        yield Publish(LawNote("lost"))
        yield harness.restore()
        yield Delay(1.0)
        seen.answer = yield Publish(LawNote("next"))
        first = yield WaitForEvent(SourceMissed, LawNote)
        second = yield WaitForEvent(SourceMissed, LawNote)
        seen.heard = (first, second)

    with pytest.raises(RuntimeError, match="failed while telling the gap"):
        run_on_virtual_clock(harness.as_party("law-failed", (SourceMissed, LawNote), sends_after_a_failed_telling()))
    assert raised.done
    assert isinstance(seen.answer, NoticeSent), seen.answer
    assert seen.heard == (SourceMissed("law-failed", f"{PREFIX}:note"), LawNote("next")), seen.heard


def _holds_gap_notices(gate: SimpleNamespace) -> "ProgramHandler":
    """The layer that holds every gap notice until the test opens ``gate`` (so a send made meanwhile must queue
    behind it), and writes down when it was held."""

    @do
    def handler(effect: Announce, k: K) -> "EffectGenerator[object]":
        """Hold a gap notice on the gate; pass everything else on."""
        if effect.name == GAP_NOTICE and gate.promise is not None:
            gate.held = True
            yield Wait(gate.promise.future)
        yield Pass(effect, k)
        return None

    return program_handler(handler)


def test_a_publish_made_while_another_task_tells_the_gap_waits_behind_it() -> None:
    # Fix 5 of the review: the order law passes whichever task goes first, so here the path that waits is forced.
    # The task waiting for the return tells the gap and is held at the gate; the body's Publish made meanwhile
    # queues behind it, is answered only after the gate opens (at 1 s), and arrives after the gap.
    gate = SimpleNamespace(promise=None, held=False)
    harness = gap_harness(MemoryBroker(), below_events=_holds_gap_notices(gate))
    seen = SimpleNamespace(answered_at=None, heard=())

    @do
    def opens_later() -> "EffectGenerator[None]":
        yield Delay(1.0)
        yield CompletePromise(gate.promise, None)

    @do
    def publishes_behind_the_gap() -> "EffectGenerator[None]":
        yield harness.cut()
        yield Publish(LawNote("lost"))
        gate.promise = yield CreatePromise()
        yield Spawn(opens_later())
        yield harness.restore()
        # Let the task waiting for the return reach the exit first (it is held at the gate there).
        yield Delay(0.1)
        assert gate.held, "the gap notice was held at the gate"
        yield Publish(LawNote("right-after"))
        now = yield GetTime()
        seen.answered_at = (now - T0).total_seconds()
        first = yield WaitForEvent(SourceMissed, LawNote)
        second = yield WaitForEvent(SourceMissed, LawNote)
        seen.heard = (first, second)

    run_on_virtual_clock(harness.as_party("law-queued", (SourceMissed, LawNote), publishes_behind_the_gap()))
    assert seen.answered_at is not None, seen.answered_at
    assert seen.answered_at >= 1.0, seen.answered_at
    assert seen.heard == (SourceMissed("law-queued", f"{PREFIX}:note"), LawNote("right-after")), seen.heard
