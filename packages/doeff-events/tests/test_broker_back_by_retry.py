"""The answer to "has the broker come back" for a broker that cannot tell its own return (Redis):
``broker_back_by_retry`` tries a connection (``ProbeBroker``) at the interval the composition names, and only while
somebody waits for the return (agora-redesign #3864 — the decision of 2026-10-07, ADR-DOE-EVENTS-002 R6).

The conditions it is held to, on the in-memory broker under a virtual clock (the in-memory handler answers
``ProbeBroker`` from whether its broker is cut; ``broker_back_by_retry`` sits inside it, so the in-memory
handler's own answer to ``AwaitBrokerBack`` is never used here):

- While no channel holds a gap, nothing is tried — however long the clock runs.
- While a gap is held, a connection is tried every interval; the first try after the broker is back tells the gap.
- Once the next ``Publish`` told the gap, the trying stops.
- A try reads and writes no data (``ProbeBroker`` carries none; on Redis it is ``PING``).

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

from datetime import datetime
from typing import TYPE_CHECKING, final

import pytest
from doeff_events import EventBus, subscribed_event_handler
from doeff_events.effects.events import Publish
from doeff_events.effects.notices import Announce, ProbeBroker
from doeff_events.handlers.memory_notices import (
    MemoryBroker,
    cut_broker,
    memory_notice_handler,
    restore_broker,
)
from doeff_events.handlers.notice_events import (
    GAP_NOTICE,
    MarkGap,
    NoticeRoute,
    notice_events_handler,
)
from doeff_events.handlers.redis_notices import broker_back_by_retry
from doeff_events.notice_laws import LawNote, law_routes
from doeff_time import Delay, GetTime
from notice_law_support import PATIENCE_SECONDS, PREFIX, T0, run_on_virtual_clock

from doeff import K, Pass, Program, do
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

RETRY_SECONDS = 5.0


@final
class _Seen:
    """What the recording layer saw: the seconds (from the start) of every connection try and of every gap told."""

    __slots__ = ("gaps", "tries")

    def __init__(self) -> None:
        """Start having seen nothing."""
        self.tries: tuple[float, ...] = ()
        self.gaps: tuple[float, ...] = ()


def _recording(seen: _Seen) -> "ProgramHandler":
    """The layer between ``broker_back_by_retry`` and the broker that writes down when a connection is tried and
    when a gap is told, and passes everything on."""

    @do
    def handler(effect: ProbeBroker | Announce, k: K) -> "EffectGenerator[object]":
        """Note the time of a try or of a gap notice; pass the effect on."""
        now: datetime = yield GetTime()
        at = (now - T0).total_seconds()
        if isinstance(effect, ProbeBroker):
            seen.tries = (*seen.tries, at)
        elif effect.name == GAP_NOTICE:
            seen.gaps = (*seen.gaps, at)
        yield Pass(effect, k)
        return None

    return program_handler(handler)


def _sender(broker: MemoryBroker, seen: _Seen, body: Program[object]) -> Program[object]:
    """``body`` as a process that only sends, with the retrying answer to the broker's return."""
    routes = tuple(
        NoticeRoute(r.event_type, r.wire_name, r.channel, r.encode, r.decode, MarkGap()) for r in law_routes(PREFIX)
    )
    events = notice_events_handler("law-retry", routes, PATIENCE_SECONDS)
    under = memory_notice_handler(broker)(_recording(seen)(broker_back_by_retry(RETRY_SECONDS)(events(body))))
    return subscribed_event_handler(EventBus(), "law-retry")(under)


def test_nothing_is_tried_while_no_gap_is_held() -> None:
    # Condition 1 (guard): the broker answers every send; an hour passes on the clock; no connection is tried.
    broker, seen = MemoryBroker(), _Seen()

    @do
    def sends_and_idles() -> "EffectGenerator[None]":
        yield Publish(LawNote("taken"))
        yield Delay(3600.0)
        yield Publish(LawNote("taken again"))

    run_on_virtual_clock(_sender(broker, seen, sends_and_idles()))
    assert seen.tries == ()
    assert seen.gaps == ()


def test_a_held_gap_is_tried_every_interval_and_told_at_the_first_try_after_the_return() -> None:
    # The broker is away from 0 s to 12 s. Tries at 0, 5, 10 fail; the try at 15 succeeds and tells the gap; then
    # nothing more is tried while the clock runs on.
    broker, seen = MemoryBroker(), _Seen()

    @do
    def misses_one() -> "EffectGenerator[None]":
        yield cut_broker(broker, "away for the test")
        yield Publish(LawNote("lost"))
        yield Delay(12.0)
        yield restore_broker(broker)
        yield Delay(600.0)

    run_on_virtual_clock(_sender(broker, seen, misses_one()))
    assert seen.tries == (0.0, 5.0, 10.0, 15.0)
    assert seen.gaps == (15.0,)


def test_trying_stops_once_the_next_publish_told_the_gap() -> None:
    # The broker is back at 2 s; the body publishes at 3 s, before the next try at 5 s: that Publish tells the gap,
    # the waiting task is stopped, and no try follows for the rest of the hour.
    broker, seen = MemoryBroker(), _Seen()

    @do
    def tells_by_publishing() -> "EffectGenerator[None]":
        yield cut_broker(broker, "away for the test")
        yield Publish(LawNote("lost"))
        yield Delay(2.0)
        yield restore_broker(broker)
        yield Delay(1.0)
        yield Publish(LawNote("next"))
        yield Delay(3600.0)

    run_on_virtual_clock(_sender(broker, seen, tells_by_publishing()))
    assert seen.tries == (0.0,)
    assert seen.gaps == (3.0,)


@pytest.mark.parametrize("seconds", [0, -1.0, True])
def test_the_interval_is_named_by_the_composition_and_must_be_positive(seconds: object) -> None:
    with pytest.raises(ValueError, match="retry_seconds"):
        broker_back_by_retry(seconds)  # type: ignore[arg-type] — the check refuses what the type allows past it
