"""The subscription a wrapper's source holds ends with the wrapped body: by the body's normal end, by its error, by
a cancel from outside (also one that lands while the start gaps are told, before the body runs), and after the
source subscribed again past an outage (card ki-aa9497a3a412 — a server that re-made its reader for every followed
turn left one Redis subscription open per reader until redis-py's pool of connections was full).

Read through the broker's own answer: ``Announce`` answers how many subscribers received the notice, so a
subscription left open after its body ended is counted. The in-memory broker forgets a closed subscription at
once, so the count is exact here; the Redis side is read in ``test_notice_laws_redis.py`` (a reader re-made more
times than the client's pool holds).

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

import logging
from typing import TYPE_CHECKING, Final

import pytest
from doeff_core_effects.scheduler import (
    Cancel,
    CompletePromise,
    CreatePromise,
    Promise,
    Spawn,
    Task,
    TaskCancelledError,
    Wait,
)
from doeff_events import EventBus, SourceResumed, SourceStarted, subscribed_event_handler
from doeff_events.effects.events import WaitForEvent
from doeff_events.effects.notices import Announce
from doeff_events.handlers.memory_notices import (
    MemoryBroker,
    hold_broker,
    memory_notice_handler,
    release_broker,
)
from doeff_events.handlers.notice_events import (
    GAP_NOTICE,
    MarkGap,
    NoticeRoute,
    notice_events_handler,
)
from doeff_events.notice_laws import LawNote, law_routes
from notice_law_support import PATIENCE_SECONDS, PREFIX, memory_harness, run_on_virtual_clock

from doeff import K, Pass, Program, Pure, do
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

CHANNEL: Final = f"{PREFIX}:note"
# How many readers each test makes one after another (the incident counted one leaked subscription per reader).
ROUNDS: Final = 6


@do
def _receivers_now() -> "EffectGenerator[object]":
    """Announce on the readers' channel: the broker answers how many subscriptions it still holds there."""
    return (yield Announce(CHANNEL, "law-note", "after"))


def _receivers_left(broker: MemoryBroker) -> object:
    """How many subscriptions on the readers' channel ``broker`` still holds, read in a run of its own."""
    return run_on_virtual_clock(memory_notice_handler(broker)(_receivers_now()))


def test_a_body_that_ends_leaves_no_subscription_behind() -> None:
    broker = MemoryBroker()
    harness = memory_harness(broker)
    for round_ in range(ROUNDS):
        run_on_virtual_clock(harness.as_party(f"law-ends-{round_}", (), Pure(None)))
    assert _receivers_left(broker) == 0


def test_a_body_that_raises_leaves_no_subscription_behind() -> None:
    broker = MemoryBroker()
    harness = memory_harness(broker)

    @do
    def falls() -> "EffectGenerator[None]":
        raise ValueError("the body fell")
        yield

    for round_ in range(ROUNDS):
        with pytest.raises(ValueError, match="the body fell"):
            run_on_virtual_clock(harness.as_party(f"law-falls-{round_}", (), falls()))
    assert _receivers_left(broker) == 0


@do
def _cancelled(reader: Task[object]) -> "EffectGenerator[None]":
    """Cancel ``reader`` from outside and wait until it is unwound; it must end by the cancel."""
    yield Cancel(reader)
    try:
        yield Wait(reader)
    except TaskCancelledError:
        return None
    raise AssertionError("the reader ended by itself, not by the cancel")


def test_a_body_cancelled_from_outside_leaves_no_subscription_behind() -> None:
    # The screen server's way: the reader of the followed turn is cancelled and a new one is made, again and again.
    broker = MemoryBroker()
    harness = memory_harness(broker)

    @do
    def remade_by_cancel() -> "EffectGenerator[object]":
        for round_ in range(ROUNDS):
            ready: Promise[None] = yield CreatePromise()

            @do
            def follows(ready: Promise[None] = ready) -> "EffectGenerator[object]":
                yield CompletePromise(ready, None)
                return (yield WaitForEvent(LawNote))

            reader = yield Spawn(harness.as_party(f"law-followed-{round_}", (LawNote,), follows()))
            yield Wait(ready.future)
            yield _cancelled(reader)
        return (yield _receivers_now())

    assert run_on_virtual_clock(memory_notice_handler(broker)(remade_by_cancel())) == 0


def test_a_source_that_subscribed_again_after_an_outage_closes_the_new_subscription() -> None:
    # The cut ends the first subscription; the source makes a new one. That new one is what the body's end closes.
    broker = MemoryBroker()
    harness = memory_harness(broker)

    @do
    def rides_an_outage() -> "EffectGenerator[None]":
        yield WaitForEvent(SourceStarted)
        yield harness.cut()
        yield harness.restore()
        yield WaitForEvent(SourceResumed)

    for round_ in range(ROUNDS):
        run_on_virtual_clock(
            harness.as_party(
                f"law-rider-{round_}", (SourceStarted, SourceResumed), rides_an_outage()
            )
        )
    assert _receivers_left(broker) == 0


def _sees_the_start_gap(seen: Promise[None]) -> "ProgramHandler":
    """The layer under the wrapper that tells the test when the wrapper sends its start gap (then passes it on)."""

    @do
    def handler(effect: Announce, k: K) -> "EffectGenerator[object]":
        """Complete ``seen`` at the gap notice; every Announce goes on to the broker."""
        if effect.name == GAP_NOTICE:
            yield CompletePromise(seen, None)
        yield Pass(effect, k)
        return None

    return program_handler(handler)


def test_a_cancel_while_the_start_gaps_are_told_leaves_no_subscription_behind() -> None:
    # The subscription is made before the start gaps are told; a cancel that lands there — before the body ran —
    # still closes it (the broker is held, so the start gap waits for an answer and the cancel finds it waiting).
    broker = MemoryBroker()
    starting = tuple(
        NoticeRoute(
            r.event_type,
            r.wire_name,
            r.channel,
            r.encode,
            r.decode,
            MarkGap(start_channels=(CHANNEL,)),
            r.reads,
        )
        for r in law_routes(PREFIX)
    )

    @do
    def cancelled_at_the_start() -> "EffectGenerator[object]":
        yield hold_broker(broker)
        for round_ in range(ROUNDS):
            seen: Promise[None] = yield CreatePromise()
            source = f"law-restarted-{round_}"
            wrapped = notice_events_handler(source, starting, PATIENCE_SECONDS)(Pure(None))
            party: Program[object] = subscribed_event_handler(EventBus(), source)(
                memory_notice_handler(broker)(_sees_the_start_gap(seen)(wrapped))
            )
            reader = yield Spawn(party)
            yield Wait(seen.future)
            yield _cancelled(reader)
        yield release_broker(broker)
        return (yield _receivers_now())

    assert run_on_virtual_clock(memory_notice_handler(broker)(cancelled_at_the_start())) == 0


def test_the_start_of_a_stall_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    # A stall can last the whole patience before anything else is seen (900 s in the incident): its start is one
    # warning naming the source and the broker's words; the return is not logged again.
    harness = memory_harness(MemoryBroker())

    @do
    def rides_an_outage() -> "EffectGenerator[None]":
        yield WaitForEvent(SourceStarted)
        yield harness.cut()
        yield harness.restore()
        yield WaitForEvent(SourceResumed)

    with caplog.at_level(logging.WARNING, logger="doeff_events.handlers.notice_events"):
        run_on_virtual_clock(
            harness.as_party("law-logged", (SourceStarted, SourceResumed), rides_an_outage())
        )
    stalls = [
        record for record in caplog.records if record.name == "doeff_events.handlers.notice_events"
    ]
    assert len(stalls) == 1, [record.getMessage() for record in stalls]
    message = stalls[0].getMessage()
    assert "law-logged" in message, message
    assert "the in-memory broker was cut" in message, message
    assert f"{PATIENCE_SECONDS}" in message, message
