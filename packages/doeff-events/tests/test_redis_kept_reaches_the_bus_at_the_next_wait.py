"""A kept channel's entry added while its subscriber reacts is on the bus at the subscriber's next wait.

agora's turn-dispatcher reacts for 1.4 to 3 s of CPU between two waits (a zero ``Delay`` that lets the other tasks run,
then a count of what came to its queue). Its source has Pub/Sub traffic all the time, so the source's ask for the next
notice is answered by a Pub/Sub notice early in the reaction, and an entry added to the kept stream later in the same
reaction waits in the read ahead. At the wait the source publishes the Pub/Sub notice and asks again; before, that ask
was one more ``Await`` on the bridge's event loop, which answered after the subscriber's own zero ``Delay`` — the
subscriber went on with the Pub/Sub notice only, and the entry reached the bus one or two waits later (4.0 to 5.7 s
after its ``XADD`` in production, 2026-11-17).

The world: the real scheduler, ``await_handler``'s bridge thread, ``async_time_handler``, ``notice_events_handler`` and
the subscriber queue; the Redis handler of the module (``_broker_handler``) over a fake client whose kept stream and
Pub/Sub connection live on the bridge's event loop. Another process's ``XADD`` and the Pub/Sub traffic are put there
from the test's thread while the subscriber reacts. A reaction holds the scheduler's thread (``time.sleep`` — the
bridge's loop runs meanwhile, as it does beside a reaction of CPU).

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pytest
from doeff_core_effects import await_handler, scheduled
from doeff_core_effects._handlers import _get_await_bridge_loop
from doeff_events import (
    CountQueued,
    Drop,
    EventBus,
    MarkGap,
    NoticeRoute,
    SourceStarted,
    WaitForEvent,
    WaitForEvents,
    subscribed_event_handler,
)
from doeff_events._handlers.redis_notices import _broker_handler
from doeff_time import Delay, async_time_handler
from events_test_support import patient_notice_events

from doeff import do, run

if TYPE_CHECKING:
    from concurrent.futures import Future

    from doeff import EffectGenerator

# The subject is real threads: the scheduler's thread held by a reaction while the bridge's event loop reads.
pytestmark = pytest.mark.realtime

INBOX: Final = "inbox"
REPORTS: Final = "reports"
# The subscriber's reactions: each has Pub/Sub traffic first, then one entry added to the kept stream.
REACTIONS: Final = 4
# The parts of a reaction (seconds the scheduler's thread is held): the Pub/Sub notice answers the source's ask
# within the first, the read ahead takes the entry within the second. Both are far longer than a turn of the loop.
BEFORE_THE_ENTRY_SECONDS: Final = 0.05
AFTER_THE_ENTRY_SECONDS: Final = 0.1
SILENCE_SECONDS: Final = 30.0
TIMEOUT_SECONDS: Final = 5.0
REDELIVER_SECONDS: Final = 30.0


@dataclass(frozen=True)
class InputNote:
    """An input placed for the subscriber (the kept channel's event)."""

    number: int


@dataclass(frozen=True)
class HostReport:
    """A host's report (the Pub/Sub traffic)."""

    number: int


def _routes() -> "tuple[NoticeRoute[object], ...]":
    """``InputNote`` on the kept channel, ``HostReport`` on a Pub/Sub channel (the subscriber reads both)."""
    return (
        NoticeRoute(
            event_type=InputNote,
            wire_name="input-note",
            channel=lambda _event: INBOX,
            encode=lambda event: json.dumps(event.number),
            decode=lambda body: InputNote(json.loads(body)),
            when_unsent=MarkGap(),
            reads=(INBOX,),
            kept=True,
        ),
        NoticeRoute(
            event_type=HostReport,
            wire_name="host-report",
            channel=lambda _event: REPORTS,
            encode=lambda event: json.dumps(event.number),
            decode=lambda body: HostReport(json.loads(body)),
            when_unsent=Drop(),
            reads=(REPORTS,),
        ),
    )


class _FakePubSub:
    """The subscription's Pub/Sub connection: confirms ``SUBSCRIBE``, then answers each read with the next buffered
    notice (``buffer`` — on the loop's thread)."""

    def __init__(self) -> None:
        """Start with nothing buffered and nothing subscribed."""
        self._mut_confirmations: tuple[str, ...] = ()
        self._mut_buffered: tuple[int, ...] = ()
        self._mut_arrived: asyncio.Event | None = None

    def _event(self) -> asyncio.Event:
        """The loop's event of an arrival (made on the loop's thread)."""
        if self._mut_arrived is None:
            self._mut_arrived = asyncio.Event()
        return self._mut_arrived

    def buffer(self, number: int) -> None:
        """One notice arrives on the connection."""
        self._mut_buffered = (*self._mut_buffered, number)
        self._event().set()

    async def subscribe(self, *channels: str) -> None:
        """``SUBSCRIBE``: one confirmation per channel."""
        self._mut_confirmations = (*self._mut_confirmations, *channels)

    async def get_message(self, ignore_subscribe_messages: bool, timeout: float | None) -> object:
        """The next confirmation."""
        del ignore_subscribe_messages, timeout
        channel, self._mut_confirmations = self._mut_confirmations[0], self._mut_confirmations[1:]
        return {"type": "subscribe", "channel": channel, "data": 1}

    async def parse_response(self, block: bool, timeout: float) -> object:
        """The next buffered notice; with none, ``None`` at once for a read that does not wait, else the next to
        arrive."""
        del block
        while not self._mut_buffered:
            if timeout == 0:
                return None
            self._event().clear()
            await self._event().wait()
        number, self._mut_buffered = self._mut_buffered[0], self._mut_buffered[1:]
        return ["message", REPORTS, json.dumps({"name": "host-report", "body": json.dumps(number)})]

    async def handle_message(self, response: list[str], ignore_subscribe_messages: bool) -> dict[str, str]:
        """The message of one reply, as redis-py makes it."""
        del ignore_subscribe_messages
        return {"type": response[0], "channel": response[1], "data": response[2]}

    async def aclose(self) -> None:
        """Nothing to close."""


class _FakeClient:
    """The subscriptions' client: one kept stream (``add`` — on the loop's thread) read by ``XREAD BLOCK``, and one
    Pub/Sub connection."""

    def __init__(self) -> None:
        """Start with an empty stream."""
        self.pubsub_connection = _FakePubSub()
        self._mut_entries: tuple[tuple[str, dict[str, str]], ...] = ()
        self._mut_changed: asyncio.Condition | None = None

    def _condition(self) -> asyncio.Condition:
        """The loop's condition of a change of the stream (made on the loop's thread)."""
        if self._mut_changed is None:
            self._mut_changed = asyncio.Condition()
        return self._mut_changed

    def pubsub(self) -> _FakePubSub:
        """The one Pub/Sub connection."""
        return self.pubsub_connection

    async def xrevrange(self, key: str, count: int) -> list[object]:
        """The stream is empty when the reader subscribes."""
        del key, count
        return []

    async def add(self, number: int) -> None:
        """``XADD`` of one entry (another process's), stamped with the wall clock."""
        entry_id = f"{int(time.time() * 1000)}-{len(self._mut_entries)}"
        async with self._condition():
            self._mut_entries = (*self._mut_entries, (entry_id, {"name": "input-note", "body": json.dumps(number)}))
            self._condition().notify_all()

    async def xread(self, streams: dict[str, str], count: int, block: int) -> object:
        """``XREAD COUNT count BLOCK block`` of the one stream (the block never ends within the test)."""
        del block
        ((key, position),) = streams.items()
        async with self._condition():
            while not (after := [entry for entry in self._mut_entries if _seq(entry[0]) > _seq(position)][:count]):
                await self._condition().wait()
        return [[key, [[entry_id, fields] for entry_id, fields in after]]]

    async def aclose(self) -> None:
        """Nothing to close."""


def _seq(entry_id: str) -> int:
    """The order of an entry of the fake stream (its sequence part — the stream's own count)."""
    return -1 if entry_id == "0-0" else int(entry_id.split("-")[1])


def _on_the_loop(call: "Future[None]") -> None:
    """Wait (on the test's thread) until the loop has done ``call``."""
    call.result(timeout=TIMEOUT_SECONDS)


@do
def _reacts(client: _FakeClient) -> "EffectGenerator[tuple[tuple[int, ...], ...]]":
    """The subscriber: per reaction, Pub/Sub traffic, then an entry, while it holds the thread; then the wait (a zero
    ``Delay``) and what came to its queue. Answers the entries taken at each wait."""
    yield WaitForEvent(SourceStarted)
    # The source's first ask starts the read ahead.
    yield Delay(BEFORE_THE_ENTRY_SECONDS)
    loop = _get_await_bridge_loop()
    taken: tuple[tuple[int, ...], ...] = ()
    for number in range(REACTIONS):
        _on_the_loop(asyncio.run_coroutine_threadsafe(_buffered(client, number), loop))
        time.sleep(BEFORE_THE_ENTRY_SECONDS)
        _on_the_loop(asyncio.run_coroutine_threadsafe(client.add(number), loop))
        time.sleep(AFTER_THE_ENTRY_SECONDS)
        yield Delay(0.0)
        came: int = yield CountQueued(InputNote, HostReport)
        events: tuple[object, ...] = (yield WaitForEvents(InputNote, HostReport)) if came else ()
        taken = (*taken, tuple(event.number for event in events if isinstance(event, InputNote)))
    return taken


async def _buffered(client: _FakeClient, number: int) -> None:
    """One Pub/Sub notice arrives (on the loop's thread)."""
    client.pubsub_connection.buffer(number)


def test_an_entry_added_while_the_subscriber_reacts_is_taken_at_its_next_wait() -> None:
    client = _FakeClient()
    broker = _broker_handler(
        _FakeClient(),  # pyright: ignore[reportArgumentType] — the sending client is not used
        client,  # pyright: ignore[reportArgumentType] — the fake client
        TIMEOUT_SECONDS,
        SILENCE_SECONDS,
        REDELIVER_SECONDS,
    )
    events = patient_notice_events("subscriber", _routes(), TIMEOUT_SECONDS)
    queue = subscribed_event_handler(EventBus(), "subscriber", (InputNote, HostReport, SourceStarted))
    body = queue(broker(events(_reacts(client))))
    taken = run(scheduled(await_handler(async_time_handler()(body))))
    assert taken == tuple((number,) for number in range(REACTIONS)), taken
