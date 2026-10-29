"""The notice broker on Redis: the lower layer of ``doeff_events._effects.notices`` answered by a Redis server's
Pub/Sub (agora-redesign #3850).

``redis_notice_handler(url, timeout_seconds, silence_seconds)`` needs the optional dependency ``doeff-events[redis]``
(redis-py's asyncio client). The module itself imports without it; the client library is imported when the wrapped
body starts.

How it talks to Redis:

- Every operation is one ``Await`` of a redis-py coroutine (but a ``NextAnnouncement`` answered from what the last
  read took — below — and ``AckAnnouncement``, which are answered here), so it needs ``await_handler`` outside
  (inside ``scheduled``). The wait for the next notice holds no scheduler task busy and has no time limit of its own while
  the connection lives: cancelling the waiting task cancels the coroutine (``await_handler`` forwards the cancel),
  which is how a source stops.
- A subscription's connection that went half-open is lost after ``silence_seconds`` (card ki-227ad148680d). A
  subscribed server sends nothing while nobody announces, so a quiet connection and one whose peer stopped
  answering (the network cut, the host gone, the process stopped) look the same to a reader; TCP keep-alive's OS
  defaults told the second after about 2 h 11 min (Linux), and not at all for a stopped process whose kernel still
  holds the connection. So the wait asks ``PING`` on the subscription's connection after half the silence without
  a message, and a connection that answers nothing — no notice, no answer to the ``PING`` — for the other half is
  lost: ``NextAnnouncement`` answers ``BrokerUnreachable`` and the source tells ``SourceStalled``. The confirmation
  of ``SubscribeChannels`` is held to the same silence. This is the second timed check of ADR-DOE-EVENTS-002 (R8):
  it reads no data, and it runs only while a ``NextAnnouncement`` waits.
- When the server cannot be reached (connection refused, connection lost, a socket error) the answer is
  ``BrokerUnreachable`` with the library's words. The library's timed retries are not used, so the answer comes
  at once; waiting for the server's return is left to ``AwaitBrokerBack`` — which this handler does not answer:
  the composition does, from whatever tells it that the server is back.
- A full pool of connections (redis-py's ``MaxConnectionsError`` — every connection the client's pool allows is
  held by this process) is not an outage: it raises ``BrokerConnectionsExhausted`` at once. Each open subscription
  holds one connection until ``CloseSubscription``.
- ``SubscribeChannels`` answers only after the server confirmed every channel. A subscription lives on one
  connection; when that connection is lost the subscription is dead (``NextAnnouncement`` answers
  ``BrokerUnreachable``) and the caller makes a new one — this handler never re-subscribes behind the caller's
  back, because a notice announced between the loss and the new subscription is gone and the caller must know.
- One read takes everything that has already come, up to ``READ_BATCH`` (agora card ki-a83c715eab23): the read
  that answers a ``NextAnnouncement`` waits for the first notice, then takes the Pub/Sub notices already received
  on the connection without waiting, and ``XREAD`` takes up to ``READ_BATCH`` entries per kept stream. The notices
  after the first stay with the subscription, in order, and the next ``NextAnnouncement``s are answered from them
  without an ``Await`` — so a source publishes everything that came in one stretch, and its subscriber takes it in
  one wait. Before, every notice was one ``Await``, which parks the source's task; a subscriber whose reaction is
  long took one notice per reaction, and a crowd of notices made the ones behind it wait for minutes (agora's
  turn-dispatcher: 905 notices in 3 minutes, one per ~0.16 s cycle, an input notice behind them waited ~150 s).
- Nothing received is dropped on this side: redis-py reads a subscription's connection only when
  ``NextAnnouncement`` asks (no background reader); what one read took and has not handed out yet is kept with the
  subscription until the next ``NextAnnouncement``s (a closed subscription drops it — its kept-channel entries
  were handed out at the read, so they are handed out again to the reader's next subscription like any notice
  not acknowledged); what the server sent in the meantime
  waits in the socket's buffers, and when those are full, in the server's output buffer for this client. The
  server closes the connection of a subscriber whose output buffer passes ``client-output-buffer-limit pubsub``
  — which this handler answers as ``BrokerUnreachable``, like any lost connection.
- Two clients, made when the wrapped body starts and closed when it ends: subscriptions use one that never
  repeats a call (a lost connection must be seen), ``Announce`` uses one that repeats a failed call once, at
  once, on a fresh connection (a pooled connection that died while idle is not an outage). The connection
  settings are the URL and ``timeout_seconds`` (named by the composition — no default): a connection must be made
  within it, and a send (``Announce``, ``ProbeBroker``) must be answered within it, so a server that keeps the
  connection but stops answering never holds a sender. The subscriptions' wait for the next notice is held by
  ``silence_seconds`` instead (above); TCP keep-alive stays on with the OS's intervals.
- Kept channels (agora card ki-86d11cadf3be) are Redis Streams under the key ``doeff-events:kept:<channel>``:
  ``Announce(kept=True)`` is ``XADD`` with an approximate length bound (``KEPT_LOG_LENGTH``), and a subscription's
  kept channels are read with ``XREAD BLOCK`` from the positions this handler keeps for the subscription's reader
  (one process — no consumer group on the server, so a standby process does not take notices from the active one,
  and nothing is left on the server when a process ends). A reader's first subscription starts at the last entry
  of each stream (``XREVRANGE … COUNT 1``). The wait of a subscription that reads both kinds keeps both reads in
  flight between ``NextAnnouncement``s (a read is never cancelled half-way, so no message is lost on the
  connection) and answers whichever comes first; a ``XREAD`` that does not come back within its block plus
  ``silence_seconds`` counts as a lost connection. A notice handed out and not acknowledged within
  ``redeliver_seconds`` is handed out again — the third timed check (ADR-DOE-EVENTS-002 R8 names the others): it
  reads no data and runs only while a ``NextAnnouncement`` waits and a notice is unacknowledged. Entries the bound
  trimmed before a slow reader read them are gone, like Pub/Sub notices of a lost connection.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Final, Generic, TypeAlias, TypeVar, final, get_args

from doeff_core_effects.effects import Await
from doeff_time import Delay, GetTime
from doeff_time.effects.time import DelayEffect, GetTimeEffect

from doeff import K, Pass, Program, Resume, do
from doeff import handler as _program_handler
from doeff_events._effects.notices import (
    KEPT_REDELIVERY_SECONDS,
    AckAnnouncement,
    Announce,
    Announcement,
    AwaitBrokerBack,
    BrokerUnreachable,
    ChannelSubscription,
    CloseSubscription,
    NextAnnouncement,
    ProbeBroker,
    SubscribeChannels,
)
from doeff_events._handlers._kept_readers import KeptReaders

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Generator

    from redis.asyncio import Redis
    from redis.asyncio.client import PubSub

    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

_T = TypeVar("_T")

_NAME: Final = "name"
_BODY: Final = "body"

# The longest a subscription's connection may answer nothing before it counts as lost (card ki-227ad148680d): the
# read replica's keeper in agora must learn of a dead subscription within 7 s (its ADR R10).
SUBSCRIPTION_SILENCE_SECONDS: Final = 7.0

# The most notices one read of a subscription takes from what has already come (agora card ki-a83c715eab23): the
# Pub/Sub notices already received on the connection, and the entries ``XREAD … COUNT`` answers per kept stream.
READ_BATCH: Final = 1000

# The approximate number of entries a kept channel's stream holds (``XADD … MAXLEN ~``): a reader further behind
# than this loses the oldest, as a Pub/Sub subscriber loses what was sent while it was away.
KEPT_LOG_LENGTH: Final = 10000
# The key of a kept channel's stream (a prefix keeps the streams apart from any other key of the database).
KEPT_KEY_PREFIX: Final = "doeff-events:kept:"

# Every lower-layer effect the Redis handler answers: the annotation of ``_broker_handler``'s clauses (the VM skips the
# handler for any other effect) and the declaration of the factory ``redis_notice_handler`` read this one union.
RedisNoticeAnswer: TypeAlias = (
    Announce | SubscribeChannels | NextAnnouncement | AckAnnouncement | CloseSubscription | ProbeBroker
)
REDIS_NOTICE_ANSWERS: Final[tuple[type, ...]] = get_args(RedisNoticeAnswer)
# Every effect the Redis handler performs: each answer is one ``Await`` of a redis-py coroutine, and closing the
# clients when the body ends is one more (``await_handler`` outside answers them).
REDIS_NOTICE_EFFECTS: Final[tuple[type, ...]] = (Await,)
# What ``broker_back_by_retry`` answers, and what it performs while answering: it reads the clock, waits the interval
# and asks ``ProbeBroker`` (which ``redis_notice_handler`` outside answers).
BROKER_BACK_ANSWERS: Final[tuple[type, ...]] = (AwaitBrokerBack,)
BROKER_BACK_EFFECTS: Final[tuple[type, ...]] = (GetTimeEffect, DelayEffect, ProbeBroker)


class BrokerAnswerMalformed(TypeError):
    """Redis (or the client library) answered in a shape this handler does not know — named instead of guessed."""


class BrokerConnectionsExhausted(RuntimeError):
    """The client's pool of connections to the server is full (redis-py's ``MaxConnectionsError``): this process
    holds every connection the pool allows. It is not an outage — the server answers, and waiting gives no
    connection back — so it is raised by name at once instead of answered as ``BrokerUnreachable`` (card
    ki-aa9497a3a412: subscriptions left open filled the pool, and the source waited out its whole patience for a
    broker that was never away)."""


def _text(value: object, what: str) -> str:
    """Check a value read from Redis is text (the client decodes responses)."""
    if not isinstance(value, str):
        raise BrokerAnswerMalformed(f"{what} must be text, got {value!r}")
    return value


@dataclass(frozen=True)
class _Handed:
    """A kept channel's notice handed to its reader and not acknowledged yet, and when (the event loop's clock)."""

    notice: Announcement
    at: float


@final
class _KeptReader:
    """What the handler keeps for one reader name: the id read up to in each kept channel's stream and the notices
    handed out without an acknowledgement, oldest first."""

    __slots__ = ("handed", "positions")

    def __init__(self) -> None:
        """Start a reader that has read nothing (its first subscription sets the positions at the streams' ends)."""
        self.positions: dict[str, str] = {}
        self.handed: tuple[_Handed, ...] = ()

    def due(self, now: float, redeliver_seconds: float) -> _Handed | None:
        """The oldest unacknowledged notice whose time to be handed out again has come, or ``None``."""
        return next((held for held in self.handed if now - held.at >= redeliver_seconds), None)

    def seconds_to_due(self, now: float, redeliver_seconds: float) -> float | None:
        """How long until the oldest unacknowledged notice is due (``None`` = none is held)."""
        if not self.handed:
            return None
        return max(0.0, redeliver_seconds - (now - self.handed[0].at))

    def hand(self, notice: Announcement, now: float) -> Announcement:
        """Hand ``notice`` out (again) at ``now``: it is the newest unacknowledged one from now on."""
        kept = tuple(held for held in self.handed if not _same_notice(held.notice, notice))
        self.handed = (*kept, _Handed(notice, now))
        return notice

    def hand_all(self, notices: tuple[Announcement, ...], now: float) -> tuple[Announcement, ...]:
        """Hand every one of ``notices`` out at ``now``, in order (``hand`` for one read's entries at once)."""
        kept = tuple(held for held in self.handed if not any(_same_notice(held.notice, one) for one in notices))
        self.handed = (*kept, *(_Handed(notice, now) for notice in notices))
        return notices

    def acknowledged(self, notice: Announcement) -> None:
        """Forget ``notice``: it is not handed out again (nothing changes if it is not held)."""
        self.handed = tuple(held for held in self.handed if not _same_notice(held.notice, notice))


def _same_notice(one: Announcement, other: Announcement) -> bool:
    """Whether two notices are the same entry of a kept channel's stream (its channel and its id)."""
    return one.channel == other.channel and one.receipt == other.receipt


@final
class _Listening:
    """The client's side of one ``ChannelSubscription``: redis-py's ``PubSub`` connection for its Pub/Sub channels
    (``None`` when it reads kept channels only), its reader's state for the kept ones, the reads in flight
    between two ``NextAnnouncement``s of a subscription that reads kept channels, and the notices the last read took
    that are not handed out yet (``taken`` — oldest first). Matched by identity."""

    __slots__ = ("pubsub", "pubsub_read", "reader", "stream_read", "subscription", "taken")

    def __init__(
        self, subscription: ChannelSubscription, pubsub: "PubSub | None", reader: _KeptReader | None
    ) -> None:
        """Pair the subscription a program holds with the connection that receives for it."""
        self.subscription: Final = subscription
        self.pubsub: Final = pubsub
        self.reader: Final = reader
        self.pubsub_read: asyncio.Task[tuple[Announcement, ...]] | None = None
        self.stream_read: asyncio.Task[tuple[Announcement, ...]] | None = None
        self.taken: tuple[Announcement, ...] = ()

    def handed_from_taken(self) -> Announcement | None:
        """The oldest notice the last read took and has not handed out yet (it is handed out now), or ``None``."""
        if not self.taken:
            return None
        first, self.taken = self.taken[0], self.taken[1:]
        return first

    def took(self, notices: tuple[Announcement, ...]) -> Announcement:
        """Keep what one read took (never empty) and hand out its first notice."""
        first, self.taken = notices[0], notices[1:]
        return first

    def stop_reads(self) -> None:
        """Cancel the reads in flight and drop what was taken and not handed out (the subscription is closed — its
        connection goes with it)."""
        for read in (self.pubsub_read, self.stream_read):
            if read is not None:
                read.cancel()
        self.pubsub_read = None
        self.stream_read = None
        self.taken = ()


@dataclass(frozen=True)
class _Failed:
    """A broker call that could not reach the server, as a value (``detail`` = the library's words)."""

    detail: str


async def _announce(client: "Redis", effect: Announce) -> int:
    """``PUBLISH`` of one JSON object holding the notice's wire name and encoded event; the server's count of
    subscribers that received it is the answer."""
    text = json.dumps({_NAME: effect.name, _BODY: effect.body}, ensure_ascii=False)
    receivers: object = await client.publish(effect.channel, text)
    if isinstance(receivers, bool) or not isinstance(receivers, int):
        raise BrokerAnswerMalformed(f"PUBLISH must answer the number of receivers, got {receivers!r}")
    return receivers


async def _reached(client: "Redis") -> None:
    """``PING`` — whether a connection to the server can be made now (nothing is read or written)."""
    await client.ping()


def _silent(silence_seconds: float, waited_for: str) -> Exception:
    """The failure of a subscription's connection that answered nothing for ``silence_seconds`` — redis-py's
    ``TimeoutError``, so that it is answered ``BrokerUnreachable`` like any lost connection."""
    from redis.exceptions import TimeoutError as RedisTimeoutError

    return RedisTimeoutError(
        f"the subscription's connection answered nothing for {silence_seconds} s (waiting for {waited_for}): "
        "it is counted lost"
    )


async def _subscribe(client: "Redis", subscription: ChannelSubscription, silence_seconds: float) -> "PubSub":
    """``SUBSCRIBE`` and wait for the server's confirmation of every channel, so that a notice announced after
    the answer is received. A confirmation that does not come within ``silence_seconds`` fails like a lost
    connection. A connection that cannot be made is closed again before the failure is passed on."""
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(*subscription.channels)
        confirmed = 0
        while confirmed < len(subscription.channels):
            message: object = await pubsub.get_message(ignore_subscribe_messages=False, timeout=silence_seconds)
            if message is None:
                raise _silent(silence_seconds, "the confirmation of SUBSCRIBE")
            if isinstance(message, dict) and message.get("type") == "subscribe":
                confirmed += 1
    except BaseException:
        await _or_failed(pubsub.aclose())
        raise
    return pubsub


async def _next_message(pubsub: "PubSub", silence_seconds: float) -> object:
    """The next message on the subscription's connection other than an answer to ``PING``. After half the silence
    without a message, ask ``PING``; after the other half without one either, the connection is lost (a server
    that answers sends the ``PONG`` at once, so only a peer that stopped answering reaches the end)."""
    half = silence_seconds / 2
    asked = False
    while True:
        response: object = await pubsub.parse_response(block=False, timeout=half)
        if response is None:
            if asked:
                raise _silent(silence_seconds, "a notice or the answer to PING")
            # A timed check, not a retry loop: it reads no data and runs only while a NextAnnouncement waits — a
            # subscribed Redis tells nothing while it is quiet, so nothing else can tell a peer that stopped
            # answering (ADR-DOE-EVENTS-002 R8).
            await pubsub.ping()
            asked = True
        elif _answers_ping(response):
            asked = False
        else:
            return await pubsub.handle_message(response, ignore_subscribe_messages=False)


def _answers_ping(response: object) -> bool:
    """Whether a reply read on a subscribed connection is the answer to ``PING``: ``PONG`` under RESP3 (a subscribed
    connection answers commands as usual; notices come as pushes), ``["pong", ""]`` under RESP2. Told apart before
    redis-py's ``handle_message``, which reads a RESP3 ``PONG`` as a message of type ``"P"``."""
    return response == "PONG" or (isinstance(response, list) and response[:1] == ["pong"])


async def _next_notices(pubsub: "PubSub", silence_seconds: float) -> tuple[Announcement, ...]:
    """Wait for the next notice of the subscription — without a limit while the connection answers, and failing
    as a lost connection once it answers nothing for ``silence_seconds`` — then take, without waiting, the notices
    already received on the connection after it (``READ_BATCH`` in all at most; the module's notes on one read).
    Only notices are expected on a subscribed connection once the subscription is confirmed; another kind of
    message is named, not skipped."""
    first = _notice_of(await _next_message(pubsub, silence_seconds))
    return (first, *[notice async for notice in _already_received(pubsub, READ_BATCH - 1)])


async def _already_received(pubsub: "PubSub", limit: int) -> "AsyncIterator[Announcement]":
    """The notices already received on the subscription's connection, in order, ``limit`` at most — each read
    answers at once (``parse_response(block=False, timeout=0)``: ``None`` = nothing more has come). An answer to
    ``PING`` among them is passed over."""
    for _ in range(limit):
        response: object = await pubsub.parse_response(block=False, timeout=0)
        if response is None:
            return
        if not _answers_ping(response):
            yield _notice_of(await pubsub.handle_message(response, ignore_subscribe_messages=False))


def _notice_of(message: object) -> Announcement:
    """The notice of one message read on a subscribed connection (another kind of message is named)."""
    if not isinstance(message, dict) or message.get("type") != "message":
        raise BrokerAnswerMalformed(f"a subscribed connection must deliver a notice, got {message!r}")
    payload: object = json.loads(_text(message.get("data"), "the data of a notice"))
    if not isinstance(payload, dict):
        raise BrokerAnswerMalformed(f"a notice must be a JSON object, got {payload!r}")
    notice: dict[object, object] = payload
    return Announcement(
        channel=_text(message.get("channel"), "the channel of a notice"),
        name=_text(notice.get(_NAME), f"the field {_NAME!r} of a notice"),
        body=_text(notice.get(_BODY), f"the field {_BODY!r} of a notice"),
    )


def _kept_key(channel: str) -> str:
    """The key of a kept channel's stream."""
    return f"{KEPT_KEY_PREFIX}{channel}"


async def _announce_kept(client: "Redis", effect: Announce) -> int:
    """``XADD`` of the notice's wire name and encoded event to the kept channel's stream (approximately bounded to
    ``KEPT_LOG_LENGTH`` entries); ``1`` once the stream holds it."""
    added: object = await client.xadd(
        _kept_key(effect.channel), {_NAME: effect.name, _BODY: effect.body}, maxlen=KEPT_LOG_LENGTH, approximate=True
    )
    _text(added, "the id XADD answers")
    return 1


async def _kept_positions(client: "Redis", channels: tuple[str, ...]) -> dict[str, str]:
    """The id of the last entry of each kept channel's stream (``"0-0"`` for a stream with none): where a reader's
    first subscription starts, so that what is added after the answer is read."""
    positions: dict[str, str] = {}
    for channel in channels:
        last: object = await client.xrevrange(_kept_key(channel), count=1)
        if not isinstance(last, list):
            raise BrokerAnswerMalformed(f"XREVRANGE must answer a list of entries, got {last!r}")
        positions[channel] = "0-0" if not last else _entry_id(last[0])
    return positions


def _entry_id(entry: object) -> str:
    """The id of one stream entry (``[id, fields]``) as Redis answers it."""
    if not isinstance(entry, list | tuple) or len(entry) != 2:
        raise BrokerAnswerMalformed(f"a stream entry must be [id, fields], got {entry!r}")
    return _text(entry[0], "the id of a stream entry")


def _kept_notice(key: object, entry: object) -> Announcement:
    """The notice of one entry of a kept channel's stream (its id is the receipt)."""
    stream = _text(key, "the key of a stream")
    if not stream.startswith(KEPT_KEY_PREFIX):
        raise BrokerAnswerMalformed(f"XREAD answered a stream that is not a kept channel: {stream!r}")
    receipt = _entry_id(entry)
    fields: object = entry[1]  # pyright: ignore[reportIndexIssue] — _entry_id checked the pair
    if not isinstance(fields, dict):
        raise BrokerAnswerMalformed(f"a stream entry's fields must be a mapping, got {fields!r}")
    notice: dict[object, object] = fields
    return Announcement(
        channel=stream[len(KEPT_KEY_PREFIX) :],
        name=_text(notice.get(_NAME), f"the field {_NAME!r} of a kept notice"),
        body=_text(notice.get(_BODY), f"the field {_BODY!r} of a kept notice"),
        receipt=receipt,
    )


def _entries(answer: object) -> tuple[Announcement, ...]:
    """Every entry ``XREAD`` answered, stream by stream and in each stream's order (empty when the block ended with
    none) — RESP2 answers a list of ``[key, entries]``, RESP3 a mapping of key to entries."""
    if answer is None:
        return ()
    streams: object = list(answer.items()) if isinstance(answer, dict) else answer
    if not isinstance(streams, list):
        raise BrokerAnswerMalformed(f"XREAD must answer the streams it read, got {answer!r}")
    for stream in streams:
        if not isinstance(stream, list | tuple) or len(stream) != 2 or not isinstance(stream[1], list):
            raise BrokerAnswerMalformed(f"XREAD must answer [key, entries] per stream, got {stream!r}")
    return tuple(_kept_notice(stream[0], entry) for stream in streams for entry in stream[1])


async def _next_kept_entries(
    client: "Redis", positions: dict[str, str], silence_seconds: float
) -> tuple[Announcement, ...]:
    """``XREAD BLOCK`` for the entries after ``positions`` (``READ_BATCH`` per stream at most — the module's notes on
    one read). Blocks half the silence; empty = nothing came (a quiet stream is not a lost connection). A server
    that does not answer within the block and the silence is a lost connection."""
    half = silence_seconds / 2
    streams = {_kept_key(channel): position for channel, position in positions.items()}
    read = client.xread(streams, count=READ_BATCH, block=int(half * 1000))
    try:
        answer: object = await asyncio.wait_for(read, timeout=half + silence_seconds)
    except TimeoutError:
        raise _silent(silence_seconds, "the answer of XREAD BLOCK") from None
    return _entries(answer)


async def _next_with_kept(
    receiving: "Redis", listening: _Listening, silence_seconds: float, redeliver_seconds: float
) -> tuple[Announcement, ...]:
    """The next notices of a subscription that reads kept channels (never empty): an unacknowledged notice that is
    due again, or what the reads in flight that have answered took — the Pub/Sub connection's next notices and the
    streams' next entries, both when both have answered (the Pub/Sub notices first). The entries are handed out
    here, and the reader's positions move past them. The reads stay in flight across calls (a call cancelled by its
    task leaves them for the next one)."""
    reader = listening.reader
    if reader is None:
        raise LookupError(f"{listening.subscription!r} reads no kept channel")
    loop = asyncio.get_running_loop()
    while True:
        now = loop.time()
        due = reader.due(now, redeliver_seconds)
        if due is not None:
            return (reader.hand(due.notice, now),)
        if listening.pubsub is not None and listening.pubsub_read is None:
            listening.pubsub_read = asyncio.ensure_future(_next_notices(listening.pubsub, silence_seconds))
        if listening.stream_read is None:
            positions = {channel: reader.positions[channel] for channel in listening.subscription.kept}
            listening.stream_read = asyncio.ensure_future(_next_kept_entries(receiving, positions, silence_seconds))
        reads = {read for read in (listening.pubsub_read, listening.stream_read) if read is not None}
        done, _waiting = await asyncio.wait(
            reads, timeout=reader.seconds_to_due(now, redeliver_seconds), return_when=asyncio.FIRST_COMPLETED
        )
        notices: tuple[Announcement, ...] = ()
        if listening.pubsub_read is not None and listening.pubsub_read in done:
            read_notices, listening.pubsub_read = listening.pubsub_read, None
            notices = read_notices.result()
        if listening.stream_read is not None and listening.stream_read in done:
            read_entries, listening.stream_read = listening.stream_read, None
            entries = read_entries.result()
            if entries:
                reader.positions = {**reader.positions, **{entry.channel: entry.receipt for entry in entries}}
                notices = (*notices, *reader.hand_all(entries, loop.time()))
        if notices:
            return notices


async def _or_failed(call: "Awaitable[_T]") -> "_T | _Failed":
    """Run one broker call; a connection that cannot be made or was lost becomes ``_Failed`` (a value) instead
    of an exception, so that the handler answers ``BrokerUnreachable``. A full pool of connections is not that
    (redis-py names it a ``ConnectionError`` too): it raises ``BrokerConnectionsExhausted``."""
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import MaxConnectionsError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    try:
        return await call
    except MaxConnectionsError as error:
        raise BrokerConnectionsExhausted(
            f"the client's pool of connections to the notice broker is full ({error}): this process holds every "
            "connection the pool allows — a subscription or a call that is not given back"
        ) from error
    except (RedisConnectionError, RedisTimeoutError, OSError) as error:
        return _Failed(f"{type(error).__name__}: {error}")


@final
class _OnAwait(Generic[_T]):
    """What the handler hands ``Await``: an awaitable that makes its call's coroutine only when it is awaited.
    When the task performing the ``Await`` is stopped before the answerer took it, the effect is dropped with
    nothing made — no coroutine is left unawaited (agora-redesign #3864: "coroutine '_or_failed' was never
    awaited" when a stopped task's last call was dropped)."""

    __slots__ = ("_call",)

    def __init__(self, call: "Callable[[], Awaitable[_T]]") -> None:
        """Keep the call; make nothing yet."""
        self._call = call

    def __await__(self) -> "Generator[object, None, _T]":
        """Make the call's coroutine now and await it."""
        return self._call().__await__()


def _attempt(call: "Callable[[], Awaitable[_T]]") -> "_OnAwait[_T | _Failed]":
    """One broker call for ``Await``, made when it is awaited, whose lost connection answers ``_Failed``."""
    return _OnAwait(lambda: _or_failed(call()))


def _client(url: str, repeats: int, timeout_seconds: float, *, calls_end: bool) -> "Redis":
    """Make an asyncio client for ``url``: text replies, TCP keep-alive, a connection that must be made within
    ``timeout_seconds``, and a failed call repeated ``repeats`` times at once (never after a wait — the library's
    timed retries are not used). ``calls_end`` = every call must also be answered within ``timeout_seconds`` (the
    sending client: a server that keeps the connection but does not answer, or a network that went silent, is
    unreachable after that time). The subscriptions' client sets no limit on a call: its waits are held by the
    silence of the subscription (``_next_message``)."""
    try:
        from redis.asyncio import Redis
        from redis.asyncio.retry import Retry
        from redis.backoff import NoBackoff
    except ImportError as error:
        raise ImportError(
            "redis_notice_handler needs the Redis client: install the extra 'doeff-events[redis]'"
        ) from error
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_keepalive=True,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds if calls_end else None,
        retry=Retry(NoBackoff(), repeats),
    )


@do
def _subscribed(
    receiving: "Redis", effect: SubscribeChannels, readers: KeptReaders[_KeptReader], silence_seconds: float
) -> "EffectGenerator[_Listening | _Failed]":
    """Make one subscription: ``SUBSCRIBE`` its Pub/Sub channels (when it has any), and for its kept channels take
    a reader state (``KeptReaders.attach`` — a closed subscription's of the same name, or a fresh one) and set its
    positions at the streams' ends where it has none yet. A subscription that cannot be made gives the state back."""
    subscription = ChannelSubscription(effect.channels, effect.kept, effect.reader)
    name = effect.reader
    reader = readers.attach(name) if name is not None else None
    made = yield _reading(receiving, effect, subscription, reader, silence_seconds)
    if isinstance(made, _Failed):
        if name is not None and reader is not None:
            readers.returned(name, reader)
        return made
    if reader is not None:
        readers.held(subscription, reader)
    return made


@do
def _reading(
    receiving: "Redis",
    effect: SubscribeChannels,
    subscription: ChannelSubscription,
    reader: _KeptReader | None,
    silence_seconds: float,
) -> "EffectGenerator[_Listening | _Failed]":
    """The server's part of a subscription: the kept streams' ends for a reader that has no position yet, then
    ``SUBSCRIBE`` of every channel — the kept ones too, so a notice a sender that has not switched to the kept
    channel yet announces with ``PUBLISH`` still reaches the reader (without a receipt)."""
    if reader is not None:
        fresh = tuple(channel for channel in effect.kept if channel not in reader.positions)
        if fresh:
            ends = yield Await(_attempt(partial(_kept_positions, receiving, fresh)))
            if isinstance(ends, _Failed):
                return ends
            reader.positions = {**ends, **reader.positions}
    pubsub = yield Await(_attempt(partial(_subscribe, receiving, ChannelSubscription(effect.channels), silence_seconds)))
    if isinstance(pubsub, _Failed):
        return pubsub
    return _Listening(subscription, pubsub, reader)


def _broker_handler(
    sending: "Redis", receiving: "Redis", silence_seconds: float, redeliver_seconds: float
) -> "ProgramHandler":
    """The handler that answers the lower-layer effects: ``Announce`` from ``sending``, subscriptions from
    ``receiving`` whose connections count as lost after ``silence_seconds`` without an answer (see the module's
    notes on the two clients and on a half-open connection), and kept channels' notices handed out again after
    ``redeliver_seconds`` without an acknowledgement."""
    listening: tuple[_Listening, ...] = ()
    readers: KeptReaders[_KeptReader] = KeptReaders(_KeptReader)

    @do
    def handler(effect: RedisNoticeAnswer, k: K) -> "EffectGenerator[object]":
        """Answer one broker operation with one awaited call; ``BrokerUnreachable`` when the server is not there."""
        nonlocal listening
        answer: object = None
        match effect:
            case Announce(kept=True):
                answer = yield Await(_attempt(partial(_announce_kept, sending, effect)))
            case Announce():
                answer = yield Await(_attempt(partial(_announce, sending, effect)))
            case ProbeBroker():
                answer = yield Await(_attempt(partial(_reached, sending)))
            case SubscribeChannels():
                made = yield _subscribed(receiving, effect, readers, silence_seconds)
                if isinstance(made, _Failed):
                    answer = made
                else:
                    listening = (*listening, made)
                    answer = made.subscription
            case NextAnnouncement(subscription=wanted):
                known = next((entry for entry in listening if entry.subscription is wanted), None)
                if known is None:
                    raise LookupError(f"{wanted!r} was not made by this handler, or was closed")
                # What the last read took is handed out first, without an Await (the module's notes on one read).
                answer = known.handed_from_taken()
                if answer is None:
                    if known.reader is not None:
                        read = yield Await(
                            _attempt(partial(_next_with_kept, receiving, known, silence_seconds, redeliver_seconds))
                        )
                    elif known.pubsub is not None:
                        read = yield Await(_attempt(partial(_next_notices, known.pubsub, silence_seconds)))
                    else:
                        raise LookupError(f"{wanted!r} reads no channel")
                    answer = read if isinstance(read, _Failed) else known.took(read)
            case AckAnnouncement(subscription=acked, notice=notice):
                # Kept by the handler only: nothing is sent to the server.
                acking = readers.of(acked)
                if acking is not None:
                    acking.acknowledged(notice)
            case CloseSubscription(subscription=closing):
                closed = tuple(entry for entry in listening if entry.subscription is closing)
                listening = tuple(entry for entry in listening if entry.subscription is not closing)
                readers.detach(closing)
                for entry in closed:
                    entry.stop_reads()
                    if entry.pubsub is not None:
                        yield Await(_attempt(entry.pubsub.aclose))
            case _:
                yield Pass(effect, k)
                return None
        if isinstance(answer, _Failed):
            answer = BrokerUnreachable(answer.detail)
        return (yield Resume(k, answer))

    return _program_handler(handler)


async def _close(sending: "Redis", receiving: "Redis") -> None:
    """Close both clients' connections; a server that is already gone does not make the close fail."""
    await _or_failed(sending.aclose())
    await _or_failed(receiving.aclose())


@do
def _run(
    url: str, timeout_seconds: float, silence_seconds: float, redeliver_seconds: float, body: "Program[_T]"
) -> "EffectGenerator[_T]":
    """Make the clients, run the body under the handler, and close the clients when the body ends."""
    sending = _client(url, 1, timeout_seconds, calls_end=True)
    receiving = _client(url, 0, timeout_seconds, calls_end=False)
    # The close runs on an exception and on the normal end, not in ``finally``: yielding inside the GeneratorExit
    # of a discarded process is an error (the same reason as doeff-records' sources).
    try:
        answer = yield _broker_handler(sending, receiving, silence_seconds, redeliver_seconds)(body)
    except Exception:
        yield Await(_OnAwait(partial(_close, sending, receiving)))
        raise
    yield Await(_OnAwait(partial(_close, sending, receiving)))
    return answer


# Bare Program (Program[Any, Any]): a partial cannot carry the body's result type to its call; the factory's ProgramHandler does.
class _BodyWrapper(partial["Program"]):
    """A function that wraps a body, marked so that Hy's ``with-handlers`` applies it to the body as it is."""

    _doeff_is_handler_fn = True


def redis_notice_handler(
    url: str,
    timeout_seconds: float,
    silence_seconds: float = SUBSCRIPTION_SILENCE_SECONDS,
    redeliver_seconds: float = KEPT_REDELIVERY_SECONDS,
) -> "ProgramHandler":
    """Build the handler that answers the lower-layer notice effects from the Redis server at ``url``
    (for example ``redis://agora-events:6379/0``). ``timeout_seconds`` = how long a connection may take to be made
    and a send (``Announce``, ``ProbeBroker``) may take to be answered before it counts as unreachable — so a
    server that stops answering, or a network that goes silent, never holds a sender (agora-redesign #3864). No
    default: the composition names it. ``silence_seconds`` = how long a subscription's connection may answer
    nothing (no notice, no answer to ``PING``) before it counts as lost — so a half-open connection is told as
    ``BrokerUnreachable`` within it (card ki-227ad148680d). The default ``SUBSCRIPTION_SILENCE_SECONDS`` (7 s) is
    the target of agora's read replica keeper. ``redeliver_seconds`` = how long a kept channel's notice handed to
    its reader waits for the acknowledgement before it is handed out again (default ``KEPT_REDELIVERY_SECONDS``)."""
    if not isinstance(url, str) or not url:
        raise ValueError("redis_notice_handler: url must be a non-empty string")
    for name, seconds in (
        ("timeout_seconds", timeout_seconds),
        ("silence_seconds", silence_seconds),
        ("redeliver_seconds", redeliver_seconds),
    ):
        if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds <= 0:
            raise ValueError(f"redis_notice_handler: {name} must be a number > 0, got {seconds!r}")
    return _BodyWrapper(_run, url, float(timeout_seconds), float(silence_seconds), float(redeliver_seconds))


# The declaration doeff-effect-analyzer reads for the factory (it cannot read the handler made inside it): it answers
# ``REDIS_NOTICE_ANSWERS`` and performs ``REDIS_NOTICE_EFFECTS``, so a user's closure check needs no copy of either.
# ``tests/test_redis_notices_closure.py`` checks both against what ``_broker_handler`` and ``_run`` really do.
redis_notice_handler.__doeff_handles__ = REDIS_NOTICE_ANSWERS
redis_notice_handler.__doeff_effects__ = REDIS_NOTICE_EFFECTS


def broker_back_by_retry(retry_seconds: float) -> "ProgramHandler":
    """Build the handler that answers ``AwaitBrokerBack`` for a broker that cannot tell its own return (Redis):
    try ``ProbeBroker`` (a connection only, no data), and while it fails, wait ``retry_seconds`` and try again
    (agora-redesign #3864 — the decision of 2026-10-07, ADR-DOE-EVENTS-002 R6). Answers once a try succeeds.

    Placed between ``redis_notice_handler`` (outside — it answers ``ProbeBroker``) and ``notice_events_handler``
    (inside — it asks ``AwaitBrokerBack`` only while a channel holds a gap or a subscription lost its broker), so
    nothing is tried while nobody waits. ``retry_seconds`` has no default: the composition names it.
    """
    if isinstance(retry_seconds, bool) or not isinstance(retry_seconds, int | float) or retry_seconds <= 0:
        raise ValueError(f"broker_back_by_retry: retry_seconds must be a number > 0, got {retry_seconds!r}")

    # When this handler last answered (``None`` = never). A connection that works does not mean sends work: when
    # the broker is asked about again within the interval after an answer, the interval is waited out first, so a
    # broker that takes the PING but refuses the sends is not asked about without a pause.
    last_answer: datetime | None = None

    @do
    def handler(effect: AwaitBrokerBack, k: K) -> "EffectGenerator[object]":
        """Try the connection until it can be made (after the rest of the interval, when asked again soon)."""
        nonlocal last_answer
        now: datetime = yield GetTime()
        if last_answer is not None and (now - last_answer).total_seconds() < retry_seconds:
            yield Delay(retry_seconds - (now - last_answer).total_seconds())
        # Retried on a timer because a broker that is down sends nothing: nothing else can tell its return.
        while isinstance((yield ProbeBroker()), BrokerUnreachable):
            yield Delay(retry_seconds)
        last_answer = yield GetTime()
        return (yield Resume(k, None))

    return _program_handler(handler)


# The declaration doeff-effect-analyzer reads for the factory (the handler is made inside it and is selected by its
# annotation, with no clause to read); ``tests/test_redis_notices_closure.py`` checks it against the handler.
broker_back_by_retry.__doeff_handles__ = BROKER_BACK_ANSWERS
broker_back_by_retry.__doeff_effects__ = BROKER_BACK_EFFECTS
