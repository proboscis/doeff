"""A kept channel's entry is read when it is added, not when the next ``NextAnnouncement`` asks.

A subscriber that reacts between its asks (about a second each in agora's turn-dispatcher) asks once per reaction.
Before, the next ``XREAD`` was sent only by the ask after the one that took the last entries — or after a block that
ended empty — and the Pub/Sub read, which answers at once while Pub/Sub traffic is buffered, won that ask's race: an
entry added meanwhile was handed out 2 to 3 asks after its ``XADD``. The kept streams are now read ahead (the module's
notes in ``redis_notices``): one ``XREAD BLOCK`` stays on the server at all times, and the next ask hands out what
it took.

No redis-server and no clock are needed: a fake client answers ``XREAD BLOCK`` from an in-memory stream after a round
trip of a few turns of the event loop, a block ends empty when the test says so, and a fake Pub/Sub connection has
one notice buffered at every ask (a subscriber with constant Pub/Sub traffic). A reaction is many turns of the loop —
enough for any read in flight to answer.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Final

import pytest
from doeff_events._effects.notices import Announcement, ChannelSubscription
from doeff_events._handlers.redis_notices import _KeptReader, _Listening, _next_with_kept

CHANNEL: Final = "inbox"
PUBSUB_CHANNEL: Final = "reports"
# A request's way to the server and its answer's way back, in turns of the loop: longer than the Pub/Sub read of a
# buffered notice, which answers within the ask's own turn.
ROUND_TRIP_TURNS: Final = 3
# The subscriber's reaction between two asks, in turns of the loop: far longer than a round trip.
REACTION_TURNS: Final = 100
# Far above the test's run: no block ends by the clock and no silence is reached (the test ends blocks itself).
SILENCE_SECONDS: Final = 30.0
REDELIVER_SECONDS: Final = 30.0


async def _turns(count: int) -> None:
    """Let the event loop run ``count`` turns (the fake's way of letting time pass without a clock)."""
    for _ in range(count):
        await asyncio.sleep(0)


class _FakeServer:
    """The fake server's one kept stream: ``XREAD BLOCK`` answers its entries after a position, waiting for one
    until the block ends (``None`` = none came), as Redis does — here a block ends when ``end_blocks`` is called."""

    def __init__(self) -> None:
        """Start with an empty stream and no block ended."""
        self._mut_entries: tuple[tuple[str, dict[str, str]], ...] = ()
        self._mut_blocks_ended = 0
        self.changed = asyncio.Condition()

    async def add(self, added_ms: int) -> str:
        """``XADD`` of one entry whose id carries ``added_ms`` (the wall clock of the add); its id is the answer."""
        entry_id = f"{added_ms}-{len(self._mut_entries)}"
        async with self.changed:
            self._mut_entries = (*self._mut_entries, (entry_id, {"name": "input-sent", "body": "{}"}))
            self.changed.notify_all()
        return entry_id

    async def end_blocks(self) -> None:
        """End every block waiting now: each answers ``None``, as a block whose time ran out with no entry."""
        async with self.changed:
            self._mut_blocks_ended += 1
            self.changed.notify_all()

    async def xread(self, streams: dict[str, str], count: int, block: int) -> object:
        """``XREAD COUNT count BLOCK block`` of the one stream, after a round trip each way."""
        del block
        await _turns(ROUND_TRIP_TURNS)
        ((key, position),) = streams.items()
        async with self.changed:
            started = self._mut_blocks_ended
            while not (after := self._after(position)[:count]):
                if self._mut_blocks_ended != started:
                    return None
                await self.changed.wait()
        await _turns(ROUND_TRIP_TURNS)
        return [[key, [[entry_id, fields] for entry_id, fields in after]]]

    def _after(self, position: str) -> tuple[tuple[str, dict[str, str]], ...]:
        """The entries whose id is past ``position``."""
        return tuple(entry for entry in self._mut_entries if _order(entry[0]) > _order(position))


@dataclass(frozen=True, order=True)
class _StreamOrder:
    """A stream id's order: its milliseconds, then its sequence."""

    ms: int
    seq: int


def _order(entry_id: str) -> _StreamOrder:
    """A stream id's order (milliseconds, sequence)."""
    ms, seq = entry_id.split("-")
    return _StreamOrder(int(ms), int(seq))


class _FakePubSub:
    """A Pub/Sub connection with constant traffic: ``buffer`` puts a notice in its socket, read at once."""

    def __init__(self) -> None:
        """Start with nothing buffered."""
        self._mut_buffered = 0
        self.arrived = asyncio.Event()

    def buffer(self) -> None:
        """One notice arrives on the connection."""
        self._mut_buffered += 1
        self.arrived.set()

    async def parse_response(self, block: bool, timeout: float) -> object:
        """The next buffered reply at once; with nothing buffered, ``None`` at once for a read that does not wait
        (``timeout`` 0), else the reply of the next notice to arrive (no ``PING`` is ever due within the test)."""
        del block
        while not self._mut_buffered:
            if timeout == 0:
                return None
            self.arrived.clear()
            await self.arrived.wait()
        self._mut_buffered -= 1
        return ["message", PUBSUB_CHANNEL, json.dumps({"name": "host-ram", "body": "{}"})]

    async def handle_message(self, response: list[str], ignore_subscribe_messages: bool) -> dict[str, str]:
        """The message of one reply, as redis-py makes it."""
        del ignore_subscribe_messages
        return {"type": response[0], "channel": response[1], "data": response[2]}


def _listening(pubsub: _FakePubSub) -> _Listening:
    """A subscription to one Pub/Sub channel and one kept channel, read from the stream's start."""
    reader = _KeptReader()
    reader.positions = {CHANNEL: "0-0"}
    subscription = ChannelSubscription((PUBSUB_CHANNEL, CHANNEL), kept=(CHANNEL,), reader="reader")
    return _Listening(subscription, pubsub, reader)  # pyright: ignore[reportArgumentType] — the fake PubSub


async def _ask(server: _FakeServer, listening: _Listening, pubsub: _FakePubSub) -> tuple[Announcement, ...]:
    """One ask of the subscriber, with a Pub/Sub notice buffered as traffic, acknowledging the kept entries."""
    pubsub.buffer()
    # The fake server stands for redis-py's client (only ``xread`` is used).
    notices = await _next_with_kept(
        server,  # pyright: ignore[reportArgumentType]
        listening,
        SILENCE_SECONDS,
        REDELIVER_SECONDS,
    )
    assert listening.reader is not None
    for notice in notices:
        if notice.receipt:
            listening.reader.acknowledged(notice)
    return notices


def _receipts(notices: tuple[Announcement, ...]) -> list[str]:
    """The stream ids among ``notices`` (the kept channel's entries)."""
    return [notice.receipt for notice in notices if notice.receipt]


def test_an_entry_added_while_nobody_asks_is_handed_out_by_the_next_ask() -> None:
    """After an ask that took an entry, and after a block that ended empty, the next entry is read when it is added:
    the very next ask hands it out beside the Pub/Sub notice that answers at once."""

    async def scenario() -> None:
        server = _FakeServer()
        pubsub = _FakePubSub()
        listening = _listening(pubsub)
        try:
            await _ask(server, listening, pubsub)
            first = await server.add(int(time.time() * 1000))
            await _turns(REACTION_TURNS)
            assert first in _receipts(await _ask(server, listening, pubsub))
            # The ask that took ``first`` was the last one for a while: the next entry comes during the reaction.
            second = await server.add(int(time.time() * 1000))
            await _turns(REACTION_TURNS)
            assert _receipts(await _ask(server, listening, pubsub)) == [second]
            # A block ends with no entry during a reaction, then one comes before the next ask.
            await server.end_blocks()
            await _turns(REACTION_TURNS)
            third = await server.add(int(time.time() * 1000))
            await _turns(REACTION_TURNS)
            assert _receipts(await _ask(server, listening, pubsub)) == [third]
        finally:
            listening.stop_reads()

    asyncio.run(scenario())


def test_an_entry_handed_out_late_is_logged_with_where_the_time_went(caplog: pytest.LogCaptureFixture) -> None:
    """An entry whose ``XADD`` was 2 s before its read is logged: most of the time before the read, little after."""

    async def scenario() -> str:
        server = _FakeServer()
        pubsub = _FakePubSub()
        listening = _listening(pubsub)
        try:
            await _ask(server, listening, pubsub)
            late = await server.add(int(time.time() * 1000) - 2000)
            await _turns(REACTION_TURNS)
            with caplog.at_level(logging.WARNING, logger="doeff_events._handlers.redis_notices"):
                assert late in _receipts(await _ask(server, listening, pubsub))
        finally:
            listening.stop_reads()
        return late

    late = asyncio.run(scenario())
    (line,) = [record.getMessage() for record in caplog.records if "handed out late" in record.getMessage()]
    fields = dict(part.split("=", 1) for part in line.split() if "=" in part)
    assert (fields["channel"], fields["receipt"]) == (CHANNEL, late)
    assert int(fields["xadd_to_read_ms"]) >= 2000
    assert int(fields["read_to_handed_ms"]) < 2000
