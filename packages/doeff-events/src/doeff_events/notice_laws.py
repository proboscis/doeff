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

The law of the lower layer (the broker's operations — what the upper layer relies on):

- ``law_notice_reaches_only_current_subscribers`` — a notice reaches whoever is subscribed when it is announced
  and the announcer learns how many that was; a notice announced before the subscription is never received.

What no law promises: a notice announced while a subscriber is not subscribed or not connected is not delivered
later. The program that needs it catches up once from its records on ``SourceStarted`` and ``SourceResumed``.

Upper-layer laws take an ``EventLawHarness`` (how to run a program as one process of the system, and how to take
the broker away and bring it back); the lower-layer law takes the prefix of the names it may use and runs under
a lower-layer handler.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from doeff import Program, do
from doeff_events.effects.events import (
    Publish,
    SourceResumed,
    SourceStalled,
    SourceStarted,
    WaitForEvent,
)
from doeff_events.effects.notices import Announce, Announcement, NextAnnouncement, SubscribeChannels
from doeff_events.handlers.notice_events import NoticeRoute, NoticeSent

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


EVENT_LAWS: Final = (
    law_subscription_precedes_the_body,
    law_start_is_told_after_the_subscription,
    law_start_and_return_are_told_once,
)
"""The upper-layer laws (each takes an ``EventLawHarness``)."""

BROKER_LAWS: Final = (law_notice_reaches_only_current_subscribers,)
"""The lower-layer laws (each takes the prefix of the names it may use)."""
