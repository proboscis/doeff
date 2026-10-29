"""Kept channels on the in-memory broker (agora card ki-86d11cadf3be): a notice of a kept channel is handed out
again until its reader acknowledges it, a reader goes on from where it was after a lost connection, and a notice
lost between the broker and the bus still reaches the program.

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from doeff_events import EventBus, SourceStarted, subscribed_event_handler
from doeff_events import Publish, WaitForEvent
from doeff_events import (
    AckAnnouncement,
    Announce,
    Announcement,
    BrokerUnreachable,
    CloseSubscription,
    NextAnnouncement,
    SubscribeChannels,
)
from doeff_events._effects.notices import KEPT_REDELIVERY_SECONDS
from doeff_events import MemoryBroker, cut_broker, memory_notice_handler, restore_broker
from doeff_events import MarkGap, NoticeRoute, NoticeSent
from doeff_events._notice_laws import LawNote
from doeff_time import GetTime
from events_test_support import patient_notice_events
from notice_law_support import PATIENCE_SECONDS, PREFIX, run_on_virtual_clock

from doeff import K, Program, Resume, do
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

CHANNEL = f"{PREFIX}:kept"


def kept_routes() -> "tuple[NoticeRoute[object], ...]":
    """``LawNote`` on the kept channel ``<prefix>:kept`` (sent and read by every party)."""
    return (
        NoticeRoute(
            event_type=LawNote,
            wire_name="law-note",
            channel=lambda _event: CHANNEL,
            encode=lambda event: json.dumps(event.text),
            decode=lambda body: LawNote(json.loads(body)),
            when_unsent=MarkGap(),
            reads=(CHANNEL,),
            kept=True,
        ),
    )


def _seconds(since: datetime, until: datetime) -> float:
    """The seconds between two readings of the clock."""
    return (until - since).total_seconds()


def test_a_kept_notice_is_handed_out_again_until_it_is_acknowledged() -> None:
    @do
    def reads_without_acknowledging() -> "EffectGenerator[object]":
        subscription = yield SubscribeChannels((CHANNEL,), kept=(CHANNEL,), reader="reader")
        kept = yield Announce(CHANNEL, "law-note", "one", kept=True)
        first: Announcement = yield NextAnnouncement(subscription)
        handed = yield GetTime()
        again: Announcement = yield NextAnnouncement(subscription)
        handed_again = yield GetTime()
        yield AckAnnouncement(subscription, again)
        yield Announce(CHANNEL, "law-note", "two", kept=True)
        after: Announcement = yield NextAnnouncement(subscription)
        return kept, first, again, _seconds(handed, handed_again), after

    kept, first, again, waited, after = run_on_virtual_clock(memory_notice_handler(MemoryBroker())(reads_without_acknowledging()))
    assert kept == 1, kept
    assert first == Announcement(CHANNEL, "law-note", "one", receipt="1"), first
    assert again == first, again
    assert waited == KEPT_REDELIVERY_SECONDS, waited
    assert after == Announcement(CHANNEL, "law-note", "two", receipt="2"), after


def test_a_readers_first_subscription_starts_at_the_end_of_the_log() -> None:
    @do
    def subscribes_late() -> "EffectGenerator[object]":
        yield Announce(CHANNEL, "law-note", "before", kept=True)
        subscription = yield SubscribeChannels((CHANNEL,), kept=(CHANNEL,), reader="reader")
        yield Announce(CHANNEL, "law-note", "after", kept=True)
        return (yield NextAnnouncement(subscription))

    heard = run_on_virtual_clock(memory_notice_handler(MemoryBroker())(subscribes_late()))
    assert heard == Announcement(CHANNEL, "law-note", "after", receipt="2"), heard


def test_a_reader_goes_on_from_where_it_was_after_a_lost_connection() -> None:
    # The cut ends the subscription; what was announced before the reader subscribed again, and what it held
    # without an acknowledgement, still reach it — in that order (the unacknowledged one is due later).
    broker = MemoryBroker()

    @do
    def rides_a_cut() -> "EffectGenerator[object]":
        subscription = yield SubscribeChannels((CHANNEL,), kept=(CHANNEL,), reader="reader")
        yield Announce(CHANNEL, "law-note", "held", kept=True)
        held: Announcement = yield NextAnnouncement(subscription)
        yield cut_broker(broker, "cut")
        lost = yield NextAnnouncement(subscription)
        # As the source does: give the dead subscription up before subscribing again (it leaves its reader state).
        yield CloseSubscription(subscription)
        yield restore_broker(broker)
        yield Announce(CHANNEL, "law-note", "between", kept=True)
        again = yield SubscribeChannels((CHANNEL,), kept=(CHANNEL,), reader="reader")
        between: Announcement = yield NextAnnouncement(again)
        yield AckAnnouncement(again, between)
        repeated: Announcement = yield NextAnnouncement(again)
        return held, lost, between, repeated

    held, lost, between, repeated = run_on_virtual_clock(memory_notice_handler(broker)(rides_a_cut()))
    assert held.body == "held", held
    assert isinstance(lost, BrokerUnreachable), lost
    assert between == Announcement(CHANNEL, "law-note", "between", receipt="2"), between
    assert repeated == held, repeated


def test_two_open_subscriptions_of_one_reader_each_receive_every_kept_notice() -> None:
    # A process that remakes its readers (a screen server) may hold two subscriptions of one source name at once:
    # neither takes a notice from the other, as two Pub/Sub subscriptions would both hear it.
    @do
    def two_readers() -> "EffectGenerator[object]":
        one = yield SubscribeChannels((CHANNEL,), kept=(CHANNEL,), reader="screen")
        two = yield SubscribeChannels((CHANNEL,), kept=(CHANNEL,), reader="screen")
        yield Announce(CHANNEL, "law-note", "both", kept=True)
        first: Announcement = yield NextAnnouncement(one)
        yield AckAnnouncement(one, first)
        second: Announcement = yield NextAnnouncement(two)
        return first, second

    first, second = run_on_virtual_clock(memory_notice_handler(MemoryBroker())(two_readers()))
    assert first == second == Announcement(CHANNEL, "law-note", "both", receipt="1"), (first, second)


def test_a_notice_announced_without_kept_on_a_kept_channel_still_reaches_its_reader() -> None:
    # While a system is redeclared, a sender of the old version announces over Pub/Sub on the channel the new reader
    # reads as kept: the reader still hears it (no receipt — nothing to acknowledge), so the order of the
    # declarations loses nothing.
    @do
    def hears_an_old_sender() -> "EffectGenerator[object]":
        subscription = yield SubscribeChannels((CHANNEL,), kept=(CHANNEL,), reader="reader")
        sent = yield Announce(CHANNEL, "law-note", "old")
        return sent, (yield NextAnnouncement(subscription))

    sent, heard = run_on_virtual_clock(memory_notice_handler(MemoryBroker())(hears_an_old_sender()))
    assert sent == 1, sent
    assert heard == Announcement(CHANNEL, "law-note", "old"), heard


def test_a_pubsub_notice_still_reaches_a_subscription_that_also_reads_a_kept_channel() -> None:
    @do
    def reads_both() -> "EffectGenerator[object]":
        subscription = yield SubscribeChannels(("plain", CHANNEL), kept=(CHANNEL,), reader="reader")
        sent = yield Announce("plain", "law-note", "plain")
        return sent, (yield NextAnnouncement(subscription))

    sent, heard = run_on_virtual_clock(memory_notice_handler(MemoryBroker())(reads_both()))
    assert sent == 1, sent
    assert heard == Announcement("plain", "law-note", "plain"), heard


def _drops_the_first_kept_notice(state: SimpleNamespace) -> "ProgramHandler":
    """Between ``notice_events_handler`` and the broker: the first notice of a kept channel never reaches the source
    (lost on the way up — the source asks for the next one)."""

    @do
    def handler(effect: NextAnnouncement, k: K) -> "EffectGenerator[object]":
        """Answer the wait with the notice after the first kept one."""
        heard = yield effect
        if isinstance(heard, Announcement) and heard.receipt and not state.dropped:
            state.dropped = True
            heard = yield effect
        return (yield Resume(k, heard))

    return program_handler(handler)


def _kept_party(broker: MemoryBroker, body: "Program[object]", below: "ProgramHandler | None" = None) -> "Program[object]":
    """``body`` as one process on ``broker`` with the kept routes (``below`` between the wrapper and the broker)."""
    events = patient_notice_events("law-kept", kept_routes(), PATIENCE_SECONDS)
    queue = subscribed_event_handler(EventBus(), "law-kept", (SourceStarted, LawNote))
    wrapped = events(body)
    return queue(memory_notice_handler(broker)(below(wrapped) if below is not None else wrapped))


def test_a_kept_notice_lost_on_the_way_to_the_bus_is_received_after_the_redelivery_time() -> None:
    state = SimpleNamespace(dropped=False)

    @do
    def sends_and_waits() -> "EffectGenerator[object]":
        yield WaitForEvent(SourceStarted)
        sent = yield Publish(LawNote("lost"))
        since = yield GetTime()
        first = yield WaitForEvent(LawNote)
        received = yield GetTime()
        yield Publish(LawNote("next"))
        second = yield WaitForEvent(LawNote)
        return sent, first, _seconds(since, received), second

    party = _kept_party(MemoryBroker(), sends_and_waits(), _drops_the_first_kept_notice(state))
    sent, first, waited, second = run_on_virtual_clock(party)
    assert state.dropped
    assert sent == NoticeSent(1), sent
    assert first == LawNote("lost"), first
    assert waited == KEPT_REDELIVERY_SECONDS, waited
    # Acknowledged once on the bus: the next event is the next note, not the lost one again.
    assert second == LawNote("next"), second


def test_a_kept_notice_that_reached_the_bus_is_received_once() -> None:
    @do
    def sends_two() -> "EffectGenerator[object]":
        yield WaitForEvent(SourceStarted)
        yield Publish(LawNote("one"))
        first = yield WaitForEvent(LawNote)
        since = yield GetTime()
        yield Publish(LawNote("two"))
        second = yield WaitForEvent(LawNote)
        later = yield GetTime()
        return first, second, _seconds(since, later)

    first, second, waited = run_on_virtual_clock(_kept_party(MemoryBroker(), sends_two()))
    assert (first, second) == (LawNote("one"), LawNote("two")), (first, second)
    assert waited == 0.0, waited


def test_a_channel_read_both_kept_and_not_is_refused_when_the_wrapper_is_built() -> None:
    plain = NoticeRoute(
        event_type=str,
        wire_name="plain",
        channel=lambda _event: CHANNEL,
        encode=str,
        decode=str,
        when_unsent=MarkGap(),
        reads=(CHANNEL,),
    )
    with pytest.raises(ValueError, match=r"both as kept channels and as Pub/Sub channels"):
        patient_notice_events("law-mixed", (*kept_routes(), plain), PATIENCE_SECONDS)


def test_a_subscription_that_reads_kept_channels_must_name_its_reader() -> None:
    with pytest.raises(ValueError, match=r"SubscribeChannels\.reader"):
        SubscribeChannels((CHANNEL,), kept=(CHANNEL,))
    with pytest.raises(ValueError, match="needs kept channels"):
        SubscribeChannels((CHANNEL,), reader="reader")
