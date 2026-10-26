"""How long a notice source waits for an unreachable broker is the composition's answer, not an argument of the
wrapper (agora card ki-6b3c298bfee1 — the composition answers it from the same value as its other stores' patience,
so the seconds are declared in one place).

- The source of a wrapper that reads channels asks ``ReadBrokerPatience`` once when an outage begins (like
  doeff-records' sources ask theirs), and waits for the broker's return as long as the answer says — not a second longer.
- A composition that placed no answer runs while the broker answers, and falls at its first outage naming the effect
  (there is no default seconds to fall back on).
- A wrapper that only sends never asks it: nothing it waits for has a deadline.
- The answer and its handler refuse what is not a patience.
"""

from types import SimpleNamespace

import pytest
from doeff_events import (
    BrokerPatience,
    EventBus,
    NoticeSent,
    ReadBrokerPatience,
    SourceStalled,
    broker_patience_handler,
    notice_events_handler,
    subscribed_event_handler,
)
from doeff_events.effects.events import Publish, WaitForEvent
from doeff_events.handlers.memory_notices import MemoryBroker, cut_broker, memory_notice_handler
from doeff_events.handlers.notice_events import MarkGap, NoticeRoute, NoticeSourceUnreachable
from doeff_events.notice_laws import LawNote, law_routes
from doeff_time import GetTime
from notice_law_support import PREFIX, T0, run_on_virtual_clock

from doeff import EffectGenerator, K, Pass, Program, Resume, UnhandledEffect, do
from doeff import handler as program_handler
from doeff.program import ProgramHandler

ANSWERED_SECONDS = 42.0


def _reading_party(broker: MemoryBroker, source: str, body: Program[object], *outer: ProgramHandler) -> Program[object]:
    """``body`` under a wrapper that reads the law channels, with ``outer`` (outermost first) above the broker."""
    under = memory_notice_handler(broker)(notice_events_handler(source, law_routes(PREFIX))(body))
    for handler in reversed(outer):
        under = handler(under)
    return subscribed_event_handler(EventBus(), source, (LawNote, SourceStalled))(under)


def _counts_the_question(seen: SimpleNamespace) -> ProgramHandler:
    """A layer that counts every ``ReadBrokerPatience`` it sees (``seen.asked``) and passes it on."""

    @do
    def handler(effect: ReadBrokerPatience, k: K) -> EffectGenerator[object]:
        """Count, then let the outer answer reach the wrapper."""
        seen.asked += 1
        return (yield Pass(effect, k))

    return program_handler(handler)


def test_the_source_waits_as_long_as_the_composition_answered() -> None:
    # The body cuts the broker at once and it never comes back: the source falls exactly at the answered patience.
    broker = MemoryBroker()
    seen = SimpleNamespace(asked=0, fell_after=None)

    @do
    def cuts_and_waits() -> EffectGenerator[object]:
        yield cut_broker(broker, "the broker is away for the rest of the test")
        return (yield WaitForEvent(LawNote))

    party = _reading_party(
        broker,
        "law-answered",
        cuts_and_waits(),
        broker_patience_handler(BrokerPatience(ANSWERED_SECONDS)),
        _counts_the_question(seen),
    )

    @do
    def timed() -> EffectGenerator[object]:
        try:
            return (yield party)
        except NoticeSourceUnreachable:
            seen.fell_after = ((yield GetTime()) - T0).total_seconds()
            raise

    with pytest.raises(NoticeSourceUnreachable, match=f"within {ANSWERED_SECONDS} s"):
        run_on_virtual_clock(timed())
    assert seen.asked == 1
    assert seen.fell_after == ANSWERED_SECONDS


def test_a_reading_wrapper_with_no_answer_falls_at_its_first_outage_naming_the_question() -> None:
    # The broker is up: the wrapper subscribes and the body runs without asking. Then the broker goes away, and the
    # source asks the question nobody answers — it falls by name, not after some default seconds.
    broker = MemoryBroker()
    seen = SimpleNamespace(ran=False)

    @do
    def runs_then_loses_the_broker() -> EffectGenerator[object]:
        seen.ran = True
        yield cut_broker(broker, "the broker is away for the rest of the test")
        return (yield WaitForEvent(LawNote))

    with pytest.raises(UnhandledEffect, match="no handler found for effect: ReadBrokerPatience"):
        run_on_virtual_clock(_reading_party(broker, "law-unanswered", runs_then_loses_the_broker()))
    assert seen.ran is True


def test_a_wrapper_that_only_sends_never_asks() -> None:
    routes = tuple(
        NoticeRoute(r.event_type, r.wire_name, r.channel, r.encode, r.decode, MarkGap()) for r in law_routes(PREFIX)
    )

    @do
    def sends() -> EffectGenerator[object]:
        return (yield Publish(LawNote("sent")))

    sender = notice_events_handler("law-sender", routes)
    party = subscribed_event_handler(EventBus(), "law-sender")(memory_notice_handler(MemoryBroker())(sender(sends())))
    assert run_on_virtual_clock(party) == NoticeSent(receivers=0)


def test_the_answer_must_be_a_patience() -> None:
    with pytest.raises(ValueError, match="BrokerPatience.seconds"):
        BrokerPatience(-1.0)
    with pytest.raises(ValueError, match="BrokerPatience.seconds"):
        BrokerPatience(True)
    with pytest.raises(TypeError, match="broker_patience_handler"):
        broker_patience_handler(900.0)  # type: ignore[arg-type] — the refusal of a bare number is what is checked


def test_a_wrong_answer_is_named() -> None:
    @do
    def answers_a_number(effect: ReadBrokerPatience, k: K) -> EffectGenerator[object]:
        """A broken composition: answers the seconds without their type."""
        return (yield Resume(k, 900.0))

    broker = MemoryBroker()

    @do
    def cuts_and_waits() -> EffectGenerator[object]:
        yield cut_broker(broker, "the broker is away for the rest of the test")
        return (yield WaitForEvent(LawNote))

    with pytest.raises(TypeError, match="law-wrong.*BrokerPatience"):
        run_on_virtual_clock(_reading_party(broker, "law-wrong", cuts_and_waits(), program_handler(answers_a_number)))
