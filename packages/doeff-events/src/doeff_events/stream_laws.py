"""The delivery laws of the event broker — the one place they are declared (agora-redesign #3850).

Every law is a program that drives a handler stack through public effects only and raises ``LawBroken`` when the
law does not hold. The same programs run against the in-memory broker and against a real Redis server, and a
handler broken in one way makes the matching law fail (doeff-events' tests do all three).

The laws of the upper layer (``stream_events_handler`` — what a business program relies on):

- (a) ``law_unfinished_event_comes_again`` — an event of an acknowledged stream is not lost when the program
  falls before it finished with it: the next run of the same consumer is given it again. An event the program
  finished with is not given again.
- (c) ``law_start_and_return_are_told_once`` — the program is told once that the source started
  (``SourceStarted``) and once every time the broker came back (``SourceResumed``).
- (d) ``law_subscription_precedes_the_body`` — the subscription begins before the program's first effect: what
  the program itself publishes first, it receives.
- (e) ``law_cut_head_is_told`` — when the head of the stream was cut past the place the reader had reached, the
  reader is told (``SourceGap``) before it is given the remaining events, and a range whose head was cut is
  answered ``RangeCut``; a whole range is answered ``EventsRead``.

The laws of the lower layer (the broker's operations — what the upper layer relies on):

- (b) ``law_entry_reaches_one_consumer_of_a_group`` — one entry is given to one consumer of a group.
- ``law_unacked_entry_stays_and_can_be_claimed`` — an entry stays pending until it is acknowledged, and another
  consumer of the group can take it over.
- ``law_notice_reaches_only_current_subscribers`` — a notice reaches whoever is subscribed when it is announced.
- ``law_cut_head_is_visible`` — a cut head shows in the group's position, in the claim of pending entries and
  in a range read.

Upper-layer laws take an ``EventLawHarness`` (how to run a program as one process of the system, and how to
take the broker away and bring it back); lower-layer laws take the prefix of the names they may use and run
under a lower-layer handler.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from doeff import Program, do
from doeff_events.effects.events import (
    Publish,
    SourceGap,
    SourceResumed,
    SourceStalled,
    SourceStarted,
    WaitForEvent,
)
from doeff_events.effects.streams import (
    AckEntry,
    Announce,
    Announcement,
    AppendEntry,
    ClaimedEntries,
    ClaimPending,
    EnsureGroup,
    EntryRange,
    EventsBetween,
    EventsRead,
    GroupPosition,
    NextAnnouncement,
    RangeCut,
    ReadEntryRange,
    ReadGroup,
    ReadGroupPosition,
    StreamEntry,
    SubscribeChannels,
    head_was_cut,
)
from doeff_events.handlers.stream_events import EventRoute, RouteKind

if TYPE_CHECKING:
    from doeff import EffectGenerator

LAW_MAXLEN: Final = 10
"""The length limit of the laws' stream."""

LAW_FLOOD: Final = 300
"""How many events a law sends to push earlier ones past the length limit (Redis cuts whole blocks of about 100
entries, so the flood is several blocks long)."""

LAW_GROUP: Final = "law-group"
LAW_CONSUMERS: Final = ("law-first", "law-second")
"""The group and the two consumers the lower-layer laws use."""


class LawBroken(AssertionError):
    """A delivery law does not hold (the message names the law and what was seen)."""


def _require(holds: bool, law: str, seen: object) -> None:
    """Raise ``LawBroken`` naming ``law`` and what was seen, unless the law holds."""
    if not holds:
        raise LawBroken(f"{law}: {seen!r}")


@dataclass(frozen=True)
class LawWork:
    """The laws' event of an acknowledged stream: ``position`` is where it is in the stream (empty when sent)."""

    position: str
    text: str


@dataclass(frozen=True)
class LawNote:
    """The laws' notice."""

    text: str


def _work_stream(prefix: str) -> str:
    return f"{prefix}:work"


def _note_channel(prefix: str) -> str:
    return f"{prefix}:note"


def law_routes(prefix: str) -> "tuple[EventRoute[object], ...]":
    """The routes every process of an upper-layer law is built with: ``LawWork`` on the acknowledged stream
    ``<prefix>:work`` (cut to ``LAW_MAXLEN``) and ``LawNote`` on the channel ``<prefix>:note``."""
    stream, channel = _work_stream(prefix), _note_channel(prefix)
    return (
        EventRoute(
            event_type=LawWork,
            wire_name="law-work",
            kind=RouteKind.ACKED_STREAM,
            place=lambda _event: stream,
            encode=lambda event: event.text,
            decode=LawWork,
            reads=(stream,),
            maxlen=LAW_MAXLEN,
        ),
        EventRoute(
            event_type=LawNote,
            wire_name="law-note",
            kind=RouteKind.NOTICE,
            place=lambda _event: channel,
            encode=lambda event: event.text,
            decode=lambda _position, body: LawNote(body),
            reads=(channel,),
        ),
    )


@dataclass(frozen=True)
class EventLawHarness:
    """How an upper-layer law reaches the system under test.

    ``as_party(consumer, event_types, body)`` = ``body`` run as one process: under a subscriber queue of its own
    that subscribes ``event_types``, and under ``stream_events_handler(consumer, law_routes(prefix), ...)`` on the
    shared broker. Running the same consumer name again is that process started again.
    ``cut()`` / ``restore()`` = take the broker away from every process, and bring it back.
    """

    as_party: Callable[[str, tuple[type, ...], Program[object]], Program[object]]
    cut: Callable[[], Program[None]]
    restore: Callable[[], Program[None]]


class _Fell(RuntimeError):
    """A law's process fell in the middle of its work."""


@do
def law_unfinished_event_comes_again(harness: EventLawHarness) -> "EffectGenerator[None]":
    """(a) An event the program had not finished when it fell is given again; a finished one is not."""
    law = "(a) unfinished-event-comes-again"

    @do
    def falls_while_processing() -> "EffectGenerator[None]":
        """First run: receive the event and fall before coming back to the wait."""
        yield Publish(LawWork("", "first"))
        yield WaitForEvent(LawWork)
        raise _Fell("fell while processing 'first'")

    @do
    def restarted() -> "EffectGenerator[tuple[str, ...]]":
        """Second run: the unfinished event must come before anything sent after the restart."""
        yield Publish(LawWork("", "second"))
        again: LawWork = yield WaitForEvent(LawWork)
        if again.text != "first":
            # The unfinished event is lost: say so now — a second wait would never be answered.
            return (again.text,)
        after: LawWork = yield WaitForEvent(LawWork)
        return (again.text, after.text)

    @do
    def started_once_more() -> "EffectGenerator[str]":
        """Third run: both earlier events were finished, so the first one received is the new one."""
        yield Publish(LawWork("", "third"))
        new: LawWork = yield WaitForEvent(LawWork)
        return new.text

    try:
        yield harness.as_party("law-reader", (LawWork,), falls_while_processing())
    except _Fell:
        pass
    else:
        raise LawBroken(f"{law}: the first run did not fall")
    seen = yield harness.as_party("law-reader", (LawWork,), restarted())
    _require(seen == ("first", "second"), law, seen)
    new = yield harness.as_party("law-reader", (LawWork,), started_once_more())
    _require(new == "third", f"{law} (a finished event is not given again)", new)


@do
def law_subscription_precedes_the_body(harness: EventLawHarness) -> "EffectGenerator[None]":
    """(d) What the program publishes as its first effects, it receives — the subscription was already there."""
    law = "(d) subscription-precedes-the-body"

    @do
    def publishes_then_waits() -> "EffectGenerator[tuple[object, ...]]":
        """Publish one event of each kind first, then wait for both."""
        yield Publish(LawWork("", "mine"))
        yield Publish(LawNote("note"))
        work: LawWork = yield WaitForEvent(LawWork)
        note: LawNote = yield WaitForEvent(LawNote)
        return (work.text, bool(work.position), note)

    seen = yield harness.as_party("law-early", (LawWork, LawNote), publishes_then_waits())
    _require(seen == ("mine", True, LawNote("note")), law, seen)


@do
def law_start_and_return_are_told_once(harness: EventLawHarness) -> "EffectGenerator[None]":
    """(c) ``SourceStarted`` once at the start, ``SourceResumed`` once after the broker came back."""
    law = "(c) start-and-return-are-told-once"
    told = (SourceStarted, SourceStalled, SourceResumed)

    @do
    def rides_an_outage() -> "EffectGenerator[tuple[object, ...]]":
        """Wait for the start, lose the broker, get it back, and list what was told until the next event."""
        started = yield WaitForEvent(SourceStarted)
        yield harness.cut()
        stalled: SourceStalled = yield WaitForEvent(SourceStalled)
        yield harness.restore()
        yield Publish(LawWork("", "after"))
        heard: tuple[object, ...] = (started, SourceStalled(stalled.source, "", stalled.since))
        while True:
            came = yield WaitForEvent(*told, LawWork)
            if isinstance(came, LawWork):
                return heard
            heard = (*heard, came)

    heard = yield harness.as_party("law-rider", (*told, LawWork), rides_an_outage())
    kinds = tuple(type(event) for event in heard)
    _require(kinds == (SourceStarted, SourceStalled, SourceResumed), law, heard)
    _require(all(event.source == "law-rider" for event in heard), law, heard)


@do
def law_cut_head_is_told(harness: EventLawHarness) -> "EffectGenerator[None]":
    """(e) A reader whose unread events were cut from the head is told ``SourceGap`` first; a range with a cut
    head is ``RangeCut`` and a whole range is ``EventsRead``."""
    law = "(e) cut-head-is-told"

    @do
    def reads_one() -> "EffectGenerator[str]":
        """First run: receive one event and remember its position."""
        yield Publish(LawWork("", "old"))
        old: LawWork = yield WaitForEvent(LawWork)
        return old.position

    @do
    def floods() -> "EffectGenerator[None]":
        """Another process sends enough events to cut the head past the first reader's place."""
        for index in range(LAW_FLOOD):
            yield Publish(LawWork("", f"flood-{index}"))

    @do
    def comes_back(old: str) -> "EffectGenerator[tuple[object, ...]]":
        """Second run of the reader: what it is told first, and what two range reads answer."""
        first = yield WaitForEvent(SourceGap, LawWork)
        one: LawWork = yield WaitForEvent(LawWork)
        two: LawWork = yield WaitForEvent(LawWork)
        cut = yield EventsBetween(LawWork, old, one.position)
        whole = yield EventsBetween(LawWork, one.position, two.position)
        return (first, cut, whole, two)

    old = yield harness.as_party("law-slow", (LawWork,), reads_one())
    yield harness.as_party("law-flood", (), floods())
    first, cut, whole, two = yield harness.as_party("law-slow", (SourceGap, LawWork), comes_back(old))
    _require(first == SourceGap("law-slow"), law, first)
    _require(isinstance(cut, RangeCut), f"{law} (a range with a cut head)", cut)
    _require(whole == EventsRead((two,)), f"{law} (a whole range)", whole)


@do
def law_entry_reaches_one_consumer_of_a_group(prefix: str) -> "EffectGenerator[None]":
    """(b) An entry given to one consumer of a group is not given to another consumer of the same group."""
    law = "(b) entry-reaches-one-consumer-of-a-group"
    stream = f"{prefix}:one"
    first, second = LAW_CONSUMERS
    yield EnsureGroup(stream, LAW_GROUP)
    yield AppendEntry(stream, "law", "e1")
    yield AppendEntry(stream, "law", "e2")
    taken: tuple[StreamEntry, ...] = yield ReadGroup((stream,), LAW_GROUP, first)
    _require(tuple(entry.body for entry in taken) == ("e1", "e2"), law, taken)
    yield AppendEntry(stream, "law", "e3")
    others: tuple[StreamEntry, ...] = yield ReadGroup((stream,), LAW_GROUP, second)
    _require(tuple(entry.body for entry in others) == ("e3",), law, others)


@do
def law_unacked_entry_stays_and_can_be_claimed(prefix: str) -> "EffectGenerator[None]":
    """An entry is pending until acknowledged, another consumer of the group can claim it, and after the
    acknowledgement nothing is left to claim."""
    law = "unacked-entry-stays-and-can-be-claimed"
    stream = f"{prefix}:pending"
    first, second = LAW_CONSUMERS
    yield EnsureGroup(stream, LAW_GROUP)
    yield AppendEntry(stream, "law", "e1")
    taken: tuple[StreamEntry, ...] = yield ReadGroup((stream,), LAW_GROUP, first)
    claimed: ClaimedEntries = yield ClaimPending(stream, LAW_GROUP, second)
    _require(claimed == ClaimedEntries(taken, ()), law, claimed)
    yield AckEntry(stream, LAW_GROUP, taken[0].position)
    left: ClaimedEntries = yield ClaimPending(stream, LAW_GROUP, first)
    _require(left == ClaimedEntries((), ()), f"{law} (after the acknowledgement)", left)


@do
def law_notice_reaches_only_current_subscribers(prefix: str) -> "EffectGenerator[None]":
    """A notice announced before the subscription is never received; one announced after it is."""
    law = "notice-reaches-only-current-subscribers"
    channel = f"{prefix}:heard"
    yield Announce(channel, "law", "early")
    subscription = yield SubscribeChannels((channel,))
    yield Announce(channel, "law", "late")
    heard: Announcement = yield NextAnnouncement(subscription)
    _require(heard == Announcement(channel, "law", "late"), law, heard)


@do
def law_cut_head_is_visible(prefix: str) -> "EffectGenerator[None]":
    """A head cut by the length limit shows in the group's position, in the claim (the pending entry is lost)
    and in a range read from a position that is gone; a range read from a remaining position is whole."""
    law = "cut-head-is-visible"
    stream = f"{prefix}:cut"
    first, _second = LAW_CONSUMERS
    yield EnsureGroup(stream, LAW_GROUP)
    anchor: str = yield AppendEntry(stream, "law", "anchor")
    yield ReadGroup((stream,), LAW_GROUP, first)
    before: GroupPosition = yield ReadGroupPosition(stream, LAW_GROUP)
    _require(not head_was_cut(before), f"{law} (before the cut)", before)
    positions: tuple[str, ...] = ()
    for index in range(LAW_FLOOD):
        position: str = yield AppendEntry(stream, "law", f"flood-{index}", LAW_MAXLEN)
        positions = (*positions, position)
    after: GroupPosition = yield ReadGroupPosition(stream, LAW_GROUP)
    _require(head_was_cut(after), f"{law} (the group's position)", after)
    claimed: ClaimedEntries = yield ClaimPending(stream, LAW_GROUP, first)
    _require(claimed == ClaimedEntries((), (anchor,)), f"{law} (the claim)", claimed)
    cut: EntryRange = yield ReadEntryRange(stream, anchor, positions[-1])
    _require(not cut.complete, f"{law} (a range from a position that is gone)", (cut.complete, len(cut.entries)))
    whole: EntryRange = yield ReadEntryRange(stream, positions[-2], positions[-1])
    _require(
        whole.complete and tuple(entry.body for entry in whole.entries) == (f"flood-{LAW_FLOOD - 1}",),
        f"{law} (a range from a remaining position)",
        whole,
    )


EVENT_LAWS: Final = (
    law_unfinished_event_comes_again,
    law_subscription_precedes_the_body,
    law_start_and_return_are_told_once,
    law_cut_head_is_told,
)
"""The upper-layer laws (each takes an ``EventLawHarness``)."""

BROKER_LAWS: Final = (
    law_entry_reaches_one_consumer_of_a_group,
    law_unacked_entry_stays_and_can_be_claimed,
    law_notice_reaches_only_current_subscribers,
    law_cut_head_is_visible,
)
"""The lower-layer laws (each takes the prefix of the names it may use)."""
