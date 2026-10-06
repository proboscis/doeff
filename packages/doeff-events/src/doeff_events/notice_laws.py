"""The delivery laws of the notice broker — the one place they are declared (agora-redesign #3850).

Every law is a program that drives a handler stack through public effects only and raises ``LawBroken`` when the
law does not hold. The same programs run against the in-memory broker and against a real Redis server, and a
handler broken in one way makes the matching law fail (doeff-events' tests do all three).

The laws of the upper layer (``notice_events_handler`` — what a business program relies on):

- ``law_subscription_precedes_the_body`` — the subscription begins before the program's first effect: what the
  program itself publishes first, it receives.
- ``law_start_is_told_after_the_subscription`` — ``SourceStarted`` is told only after the broker confirmed the
  subscription: a notice announced after the program received ``SourceStarted`` is received. (The program
  catches up from its records when it receives ``SourceStarted``; a notice between that read and a later
  subscription would be lost.)
- ``law_start_and_return_are_told_once`` — the program is told once that the source started and once every time
  the broker came back (``SourceResumed``), and — like the start — only after the subscription is in place again.

The laws of a sender the broker could not take a notice from (the receivers stay subscribed and connected —
agora-redesign #3864, ADR-DOE-EVENTS-002 R5):

- ``law_missed_notice_is_told_once_the_broker_is_back`` — ``Publish`` answers ``NoticeGapMarked`` instead of
  raising, and once the broker is back the receivers are told once that the channel has a gap (``SourceMissed``).
- ``law_gap_is_told_before_the_next_notice`` — when nobody tells the return, the next ``Publish`` that gets
  through tells the gap first.
- ``law_latest_state_wins_after_a_gap`` — a state that went away and came back ends as the latest one at a
  receiver that catches up from its records on ``SourceMissed``.
- ``law_gap_keeps_the_order_of_later_notices`` — a notice published at the return never arrives before the gap.
- ``law_gap_survives_a_second_cut`` — a gap not told because the broker went away again is told at the next return.

The law of the lower layer (the broker's operations — what the upper layer relies on):

- ``law_notice_reaches_only_current_subscribers`` — a notice reaches whoever is subscribed when it is announced
  and the announcer learns how many that was; a notice announced before the subscription is never received.

What no law promises: a notice announced while a subscriber is not subscribed or not connected is not delivered
later. The program that needs it catches up once from its records on ``SourceStarted`` and ``SourceResumed``.

Upper-layer laws take an ``EventLawHarness`` (how to run a program as one process of the system, and how to take
the broker away and bring it back); the lower-layer law takes the prefix of the names it may use and runs under
a lower-layer handler.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from doeff import Program, do
from doeff_events.effects.events import (
    Publish,
    SourceMissed,
    SourceResumed,
    SourceStalled,
    SourceStarted,
    WaitForEvent,
)
from doeff_events.effects.notices import Announce, Announcement, NextAnnouncement, SubscribeChannels
from doeff_events.handlers.notice_events import MarkGap, NoticeGapMarked, NoticeRoute, NoticeSent

if TYPE_CHECKING:
    from doeff import EffectGenerator


class LawBroken(AssertionError):
    """A delivery law does not hold (the message names the law and what was seen)."""


def _require(holds: bool, law: str, seen: object) -> None:
    """Raise ``LawBroken`` naming ``law`` and what was seen, unless the law holds."""
    if not holds:
        raise LawBroken(f"{law}: {seen!r}")


@dataclass(frozen=True)
class LawNote:
    """The laws' notice."""

    text: str


@dataclass(frozen=True)
class LawState:
    """The laws' notice of a state: ``key`` is now ``value`` (the gap laws keep the same truth in their records)."""

    key: str
    value: str


def law_routes(prefix: str) -> "tuple[NoticeRoute[object], ...]":
    """The routes every process of an upper-layer law is built with: ``LawNote`` on the channel ``<prefix>:note``."""
    channel = f"{prefix}:note"
    return (
        NoticeRoute(
            event_type=LawNote,
            wire_name="law-note",
            channel=lambda _event: channel,
            encode=lambda event: event.text,
            decode=LawNote,
            when_unsent=MarkGap(),
            reads=(channel,),
        ),
        NoticeRoute(
            event_type=LawState,
            wire_name="law-state",
            channel=lambda _event: channel,
            encode=lambda event: json.dumps([event.key, event.value]),
            decode=lambda body: LawState(*json.loads(body)),
            when_unsent=MarkGap(),
            reads=(channel,),
        ),
    )


@dataclass(frozen=True)
class EventLawHarness:
    """How an upper-layer law reaches the system under test.

    ``as_party(source, event_types, body)`` = ``body`` run as one process: under a subscriber queue of its own
    that subscribes ``event_types``, and under ``notice_events_handler(source, law_routes(prefix), ...)`` on the
    shared broker.
    ``cut()`` / ``restore()`` = take the broker away from every process (its connections are lost), and bring
    it back.
    """

    as_party: Callable[[str, tuple[type, ...], Program[object]], Program[object]]
    cut: Callable[[], Program[None]]
    restore: Callable[[], Program[None]]


@do
def _own_note_arrives(law: str, text: str) -> "EffectGenerator[None]":
    """Publish a note and require that this process, being subscribed, was counted among its receivers and
    receives it. The count is checked first: with no subscription in place the wait would never be answered."""
    sent = yield Publish(LawNote(text))
    _require(isinstance(sent, NoticeSent) and sent.receivers >= 1, f"{law} (nobody was subscribed)", sent)
    note = yield WaitForEvent(LawNote)
    _require(note == LawNote(text), law, note)


@do
def law_subscription_precedes_the_body(harness: EventLawHarness) -> "EffectGenerator[None]":
    """What the program publishes as its first effect, it receives — the subscription was already there."""
    law = "subscription-precedes-the-body"
    yield harness.as_party("law-early", (LawNote,), _own_note_arrives(law, "mine"))


@do
def law_start_is_told_after_the_subscription(harness: EventLawHarness) -> "EffectGenerator[None]":
    """A notice announced after the program received ``SourceStarted`` is received."""
    law = "start-is-told-after-the-subscription"

    @do
    def publishes_after_the_start() -> "EffectGenerator[None]":
        """Wait for the start (where a program would catch up from its records), then publish and receive."""
        started = yield WaitForEvent(SourceStarted)
        _require(started == SourceStarted("law-starter"), law, started)
        yield _own_note_arrives(law, "after-start")

    yield harness.as_party("law-starter", (SourceStarted, LawNote), publishes_after_the_start())


@do
def law_start_and_return_are_told_once(harness: EventLawHarness) -> "EffectGenerator[None]":
    """``SourceStarted`` once at the start; after an outage ``SourceResumed`` once, with the subscription back."""
    law = "start-and-return-are-told-once"
    told = (SourceStarted, SourceStalled, SourceResumed)

    @do
    def rides_an_outage() -> "EffectGenerator[tuple[object, ...]]":
        """Wait for the start, lose the broker, get it back, and list what was told until the next note."""
        started = yield WaitForEvent(SourceStarted)
        yield harness.cut()
        stalled: SourceStalled = yield WaitForEvent(SourceStalled)
        yield harness.restore()
        # Nothing else can tell the program that it is subscribed again: if the return is never told, this wait
        # is never answered (under a virtual clock the scheduler ends the run as a dead end).
        resumed = yield WaitForEvent(SourceResumed)
        sent = yield Publish(LawNote("after-return"))
        _require(isinstance(sent, NoticeSent) and sent.receivers >= 1, f"{law} (resumed before subscribed)", sent)
        heard: tuple[object, ...] = (started, SourceStalled(stalled.source, "", stalled.since), resumed)
        while True:
            came = yield WaitForEvent(*told, LawNote)
            if isinstance(came, LawNote):
                return heard
            heard = (*heard, came)

    heard = yield harness.as_party("law-rider", (*told, LawNote), rides_an_outage())
    kinds = tuple(type(event) for event in heard)
    _require(kinds == (SourceStarted, SourceStalled, SourceResumed), law, heard)
    _require(all(event.source == "law-rider" for event in heard), law, heard)


@do
def law_notice_reaches_only_current_subscribers(prefix: str) -> "EffectGenerator[None]":
    """A notice announced before the subscription has no receiver and is never received; one announced after it
    has one receiver and is received."""
    law = "notice-reaches-only-current-subscribers"
    channel = f"{prefix}:heard"
    early = yield Announce(channel, "law", "early")
    _require(early == 0, f"{law} (receivers before the subscription)", early)
    subscription = yield SubscribeChannels((channel,))
    late = yield Announce(channel, "law", "late")
    _require(late == 1, f"{law} (receivers after the subscription)", late)
    heard: Announcement = yield NextAnnouncement(subscription)
    _require(heard == Announcement(channel, "law", "late"), law, heard)


@dataclass(frozen=True)
class GapLawHarness:
    """How a gap law reaches the system under test: the broker is taken from the sending side only, and the
    receivers stay subscribed and connected.

    ``as_party`` = like ``EventLawHarness.as_party``. ``channel`` = the channel ``law_routes`` sends on.
    ``cut()`` = refuse every send. ``reopen()`` = take sends again without telling anyone waiting for the return.
    ``restore()`` = take sends again and tell everyone waiting. ``flap()`` = tell everyone waiting while sends are
    still refused (the broker went away again at once).
    """

    as_party: Callable[[str, tuple[type, ...], Program[object]], Program[object]]
    channel: str
    cut: Callable[[], Program[None]]
    reopen: Callable[[], Program[None]]
    restore: Callable[[], Program[None]]
    flap: Callable[[], Program[None]]


@do
def _heard_until_end(*types: type) -> "EffectGenerator[tuple[object, ...]]":
    """Publish the end note and list what arrives of ``types`` until it does (everything published earlier has
    arrived by then — the broker keeps one sender's order)."""
    yield Publish(LawNote("end"))
    heard: tuple[object, ...] = ()
    while True:
        came = yield WaitForEvent(*types, LawNote)
        if came == LawNote("end"):
            return heard
        heard = (*heard, came)


@do
def law_missed_notice_is_told_once_the_broker_is_back(harness: GapLawHarness) -> "EffectGenerator[None]":
    """A notice the broker could not take is not raised in the program: ``Publish`` answers ``NoticeGapMarked``.
    Once the broker is back, a subscriber that stayed connected is told once that the channel has a gap
    (``SourceMissed``)."""
    law = "missed-notice-is-told-once-the-broker-is-back"

    @do
    def misses_one() -> "EffectGenerator[None]":
        """Publish while sends are refused, bring the broker back, and list what arrives."""
        yield harness.cut()
        answer = yield Publish(LawNote("lost"))
        _require(isinstance(answer, NoticeGapMarked), f"{law} (answer while unreachable)", answer)
        yield harness.restore()
        # Nothing is published before the gap is heard: only the return can tell it (without it, this wait is
        # never answered).
        told = yield WaitForEvent(SourceMissed, LawNote)
        _require(told == SourceMissed("law-gap", harness.channel), law, told)
        heard = yield _heard_until_end(SourceMissed)
        _require(heard == (), f"{law} (told more than once)", heard)

    yield harness.as_party("law-gap", (SourceMissed, LawNote), misses_one())


@do
def law_gap_is_told_before_the_next_notice(harness: GapLawHarness) -> "EffectGenerator[None]":
    """Nobody tells the sender that the broker is back, but the next ``Publish`` gets through: the gap is told
    first, then the notice."""
    law = "gap-is-told-before-the-next-notice"

    @do
    def sends_after_a_silent_return() -> "EffectGenerator[None]":
        """Miss a notice, let sends through without telling the return, and publish the next one."""
        yield harness.cut()
        yield Publish(LawNote("lost"))
        yield harness.reopen()
        sent = yield Publish(LawNote("next"))
        _require(isinstance(sent, NoticeSent), f"{law} (answer after the return)", sent)
        heard = yield _heard_until_end(SourceMissed)
        _require(heard == (SourceMissed("law-next", harness.channel), LawNote("next")), law, heard)

    yield harness.as_party("law-next", (SourceMissed, LawNote), sends_after_a_silent_return())


@do
def law_latest_state_wins_after_a_gap(harness: GapLawHarness) -> "EffectGenerator[None]":
    """A worker-like state goes away and comes back with nothing else to tell the two apart: ``a`` is published
    ``gone`` while sends are refused, then ``here`` after the return. A receiver that applies each state it hears
    and catches up from the records on ``SourceMissed`` ends with ``here``."""
    law = "latest-state-wins-after-a-gap"
    records = {"a": "here"}

    @do
    def goes_and_comes_back() -> "EffectGenerator[None]":
        """Publish ``gone`` into the outage and ``here`` after it, keeping the records as the truth."""
        yield harness.cut()
        records["a"] = "gone"
        yield Publish(LawState("a", "gone"))
        records["a"] = "here"
        yield harness.restore()
        yield Publish(LawState("a", "here"))
        view = {"a": "unknown"}
        for came in (yield _heard_until_end(SourceMissed, LawState)):
            if isinstance(came, SourceMissed):
                view = dict(records)
            else:
                view[came.key] = came.value
        _require(view == {"a": "here"}, law, view)

    yield harness.as_party("law-latest", (SourceMissed, LawState, LawNote), goes_and_comes_back())


@do
def law_gap_keeps_the_order_of_later_notices(harness: GapLawHarness) -> "EffectGenerator[None]":
    """A notice published right after the return, while the gap may still be being told, never arrives before
    the gap: every ``LawNote`` heard comes after a ``SourceMissed``."""
    law = "gap-keeps-the-order-of-later-notices"

    @do
    def publishes_at_the_return() -> "EffectGenerator[None]":
        """Miss a notice, bring the broker back, and publish at once."""
        yield harness.cut()
        yield Publish(LawNote("lost"))
        yield harness.restore()
        yield Publish(LawNote("right-after"))
        heard = yield _heard_until_end(SourceMissed)
        _require(bool(heard) and isinstance(heard[0], SourceMissed), law, heard)
        _require(LawNote("lost") not in heard, f"{law} (a missed notice arrived)", heard)

    yield harness.as_party("law-order", (SourceMissed, LawNote), publishes_at_the_return())


@do
def law_gap_survives_a_second_cut(harness: GapLawHarness) -> "EffectGenerator[None]":
    """The broker comes back and goes away at once, before the gap could be told: the gap is kept and told at
    the next return."""
    law = "gap-survives-a-second-cut"

    @do
    def misses_through_a_flap() -> "EffectGenerator[None]":
        """Miss a notice, let the return be told while sends are still refused, then bring the broker back."""
        yield harness.cut()
        yield Publish(LawNote("lost"))
        yield harness.flap()
        yield harness.restore()
        heard = yield _heard_until_end(SourceMissed)
        _require(heard == (SourceMissed("law-flap", harness.channel),), law, heard)

    yield harness.as_party("law-flap", (SourceMissed, LawNote), misses_through_a_flap())


EVENT_LAWS: Final = (
    law_subscription_precedes_the_body,
    law_start_is_told_after_the_subscription,
    law_start_and_return_are_told_once,
)
"""The upper-layer laws (each takes an ``EventLawHarness``)."""

GAP_LAWS: Final = (
    law_missed_notice_is_told_once_the_broker_is_back,
    law_gap_is_told_before_the_next_notice,
    law_latest_state_wins_after_a_gap,
    law_gap_keeps_the_order_of_later_notices,
    law_gap_survives_a_second_cut,
)
"""The laws of a sender the broker could not take a notice from (each takes a ``GapLawHarness``)."""

BROKER_LAWS: Final = (law_notice_reaches_only_current_subscribers,)
"""The lower-layer laws (each takes the prefix of the names it may use)."""
