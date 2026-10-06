"""The event broker on Redis: the lower layer of ``doeff_events.effects.streams`` answered by a Redis server
(Streams with consumer groups for acknowledged streams, Pub/Sub for notices — agora-redesign #3850).

``redis_stream_handler(url)`` needs the optional dependency ``doeff-events[redis]`` (redis-py's asyncio client).
The module itself imports without it; the client library is imported when the handler is built.

How it talks to Redis:

- Every operation is one ``Await`` of a redis-py coroutine, so it needs ``await_handler`` outside (inside
  ``scheduled``). A blocked read (``XREADGROUP BLOCK 0``, the wait for the next notice) holds no scheduler task
  busy and has no time limit: cancelling the waiting task cancels the coroutine (``await_handler`` forwards the
  cancel), which is how a source stops.
- When the server cannot be reached (connection refused, connection lost, a socket error) the answer is
  ``BrokerUnreachable`` with the library's words. The library's own timed retries are switched off, so the
  answer comes at once and waiting for the server's return is left to ``AwaitBrokerBack`` — which this handler
  does not answer: the composition does, from whatever tells it that the server is back.
- Two clients, made when the wrapped body starts and closed when it ends. The reading one (group reads, claims,
  notices) never repeats a call: a read whose connection broke must be seen as an outage, because entries the
  server had already handed over stay pending and only a claim brings them back. The sending one (everything
  else) repeats a call once, at once, on a fresh connection — a pooled connection that died while idle is not an
  outage. The only connection setting is the URL (plus TCP keep-alive, so that a blocked read notices a peer
  that vanished).
"""

import json
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final, TypeVar, final

from doeff_core_effects.effects import Await

from doeff import K, Pass, Program, Resume, do
from doeff import handler as _program_handler
from doeff_events.effects.streams import (
    ORIGIN,
    AckEntry,
    Announce,
    Announcement,
    AppendEntry,
    BrokerUnreachable,
    ChannelSubscription,
    ClaimedEntries,
    ClaimPending,
    EnsureGroup,
    EntryRange,
    GroupPosition,
    NextAnnouncement,
    ReadEntryRange,
    ReadGroup,
    ReadGroupPosition,
    StreamEntry,
    SubscribeChannels,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from redis.asyncio import Redis
    from redis.asyncio.client import PubSub

    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

_T = TypeVar("_T")

READ_BATCH: Final = 64
"""How many entries one ``XREADGROUP`` / ``XAUTOCLAIM`` call takes at most (the handler gives them on one by one)."""

_NAME: Final = "name"
_BODY: Final = "body"


class BrokerAnswerMalformed(TypeError):
    """Redis (or the client library) answered in a shape this handler does not know — named instead of guessed."""


def _text(value: object, what: str) -> str:
    """Check a value read from Redis is text (the client decodes responses)."""
    if not isinstance(value, str):
        raise BrokerAnswerMalformed(f"{what} must be text, got {value!r}")
    return value


def _entry(stream: str, raw: object) -> StreamEntry:
    """One ``(id, fields)`` pair of a stream reply as a ``StreamEntry``."""
    if not isinstance(raw, tuple | list) or len(raw) != 2 or not isinstance(raw[1], dict):
        raise BrokerAnswerMalformed(f"an entry of stream {stream!r} must be (id, fields), got {raw!r}")
    fields: dict[object, object] = raw[1]
    return StreamEntry(
        stream=stream,
        position=_text(raw[0], "an entry id"),
        name=_text(fields.get(_NAME), f"the field {_NAME!r} of entry {raw[0]!r} of stream {stream!r}"),
        body=_text(fields.get(_BODY), f"the field {_BODY!r} of entry {raw[0]!r} of stream {stream!r}"),
    )


def _entries(stream: str, raw: object) -> tuple[StreamEntry, ...]:
    """A list of ``(id, fields)`` pairs as entries (an entry deleted from the stream comes as ``(id, None)`` in a
    claim's reply and is left out — the claim reports it among the lost ids)."""
    if not isinstance(raw, list):
        raise BrokerAnswerMalformed(f"the entries of stream {stream!r} must be a list, got {raw!r}")
    return tuple(_entry(stream, item) for item in raw if not (isinstance(item, tuple | list) and item[1] is None))


@final
class _Listening:
    """The client's side of one ``ChannelSubscription``: redis-py's ``PubSub`` connection. Matched by identity."""

    __slots__ = ("pubsub", "subscription")

    def __init__(self, subscription: ChannelSubscription, pubsub: "PubSub") -> None:
        """Pair the subscription a program holds with the connection that receives for it."""
        self.subscription: Final = subscription
        self.pubsub: Final = pubsub


@dataclass(frozen=True)
class _Failed:
    """A broker call that could not reach the server, as a value (``detail`` = the library's words)."""

    detail: str


async def _append(client: "Redis", effect: AppendEntry) -> str:
    """``XADD`` with ``MAXLEN ~`` when a length limit is given."""
    position: object = await client.xadd(
        effect.stream, {_NAME: effect.name, _BODY: effect.body}, maxlen=effect.maxlen, approximate=True
    )
    return _text(position, "the id XADD answered")


async def _ensure_group(client: "Redis", effect: EnsureGroup) -> None:
    """``XGROUP CREATE ... $ MKSTREAM``; an existing group (``BUSYGROUP``) is left as it is."""
    from redis.exceptions import ResponseError

    try:
        await client.xgroup_create(effect.stream, effect.group, id="$", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise


async def _read_group(client: "Redis", effect: ReadGroup) -> tuple[StreamEntry, ...]:
    """``XREADGROUP ... BLOCK 0`` for new entries (``>``) of every stream: returns when at least one arrived."""
    reply: object = await client.xreadgroup(
        effect.group, effect.consumer, dict.fromkeys(effect.streams, ">"), count=READ_BATCH, block=0
    )
    if not isinstance(reply, list):
        raise BrokerAnswerMalformed(f"XREADGROUP must answer a list of streams, got {reply!r}")
    pairs: list[object] = reply
    return tuple(
        entry
        for pair in pairs
        if isinstance(pair, tuple | list) and len(pair) == 2
        for entry in _entries(_text(pair[0], "a stream name"), pair[1])
    )


async def _claim(client: "Redis", effect: ClaimPending) -> ClaimedEntries:
    """``XAUTOCLAIM`` with idle time 0 from the start of the pending list to its end (the cursor the server
    returns is followed until it is back at the start)."""
    entries: tuple[StreamEntry, ...] = ()
    lost: tuple[str, ...] = ()
    cursor = ORIGIN
    while True:
        reply: object = await client.xautoclaim(
            effect.stream, effect.group, effect.consumer, min_idle_time=0, start_id=cursor, count=READ_BATCH
        )
        if not isinstance(reply, tuple | list) or len(reply) != 3 or not isinstance(reply[2], list):
            raise BrokerAnswerMalformed(f"XAUTOCLAIM must answer (next id, entries, deleted ids), got {reply!r}")
        deleted: list[object] = reply[2]
        entries = (*entries, *_entries(effect.stream, reply[1]))
        lost = (*lost, *(_text(position, "a deleted id") for position in deleted))
        cursor = _text(reply[0], "the next id of XAUTOCLAIM")
        if cursor == ORIGIN:
            return ClaimedEntries(entries, lost)


async def _group_position(client: "Redis", effect: ReadGroupPosition) -> GroupPosition:
    """``XINFO GROUPS`` for the group's last delivered id, ``XINFO STREAM`` for the stream's first entry and
    newest id."""
    groups: object = await client.xinfo_groups(effect.stream)
    stream: object = await client.xinfo_stream(effect.stream)
    if not isinstance(groups, list) or not isinstance(stream, dict):
        raise BrokerAnswerMalformed(f"XINFO of stream {effect.stream!r} answered {groups!r} / {stream!r}")
    listed: list[object] = groups
    info: dict[object, object] = stream
    found = next((group for group in listed if isinstance(group, dict) and group.get("name") == effect.group), None)
    if found is None:
        raise LookupError(f"stream {effect.stream!r} has no consumer group {effect.group!r}")
    first: object = info.get("first-entry")
    return GroupPosition(
        last_delivered=_text(found.get("last-delivered-id"), "last-delivered-id"),
        first=None if first is None else _entry(effect.stream, first).position,
        last_generated=_text(info.get("last-generated-id"), "last-generated-id"),
    )


async def _range(client: "Redis", effect: ReadEntryRange) -> EntryRange:
    """``XRANGE`` from ``after`` (inclusive, to see whether that entry is still there) to ``until``. When it is
    gone, the range is whole only if the stream never lost an entry (``entries-added`` equals its length)."""
    from redis.exceptions import ResponseError

    found = _entries(effect.stream, await client.xrange(effect.stream, min=effect.after, max=effect.until))
    if found and found[0].position == effect.after:
        return EntryRange(found[1:], complete=True)
    try:
        stream: object = await client.xinfo_stream(effect.stream)
    except ResponseError as error:
        if "no such key" not in str(error):
            raise
        return EntryRange((), complete=True)
    if not isinstance(stream, dict):
        raise BrokerAnswerMalformed(f"XINFO STREAM of {effect.stream!r} answered {stream!r}")
    info: dict[object, object] = stream
    return EntryRange(found, complete=info.get("entries-added") == info.get("length"))


async def _announce(client: "Redis", effect: Announce) -> None:
    """``PUBLISH`` of one JSON object holding the notice's wire name and encoded event."""
    await client.publish(effect.channel, json.dumps({_NAME: effect.name, _BODY: effect.body}, ensure_ascii=False))


async def _subscribe(client: "Redis", subscription: ChannelSubscription) -> "PubSub":
    """``SUBSCRIBE`` and wait for the server's confirmation of every channel, so that a notice announced after
    the answer is received."""
    pubsub = client.pubsub()
    await pubsub.subscribe(*subscription.channels)
    confirmed = 0
    while confirmed < len(subscription.channels):
        message: object = await pubsub.get_message(ignore_subscribe_messages=False, timeout=None)
        if isinstance(message, dict) and message.get("type") == "subscribe":
            confirmed += 1
    return pubsub


async def _next_notice(pubsub: "PubSub") -> Announcement:
    """Wait (no time limit) for the next notice of the subscription; other message kinds are skipped."""
    while True:
        message: object = await pubsub.get_message(ignore_subscribe_messages=True, timeout=None)
        if not isinstance(message, dict) or message.get("type") != "message":
            continue
        payload: object = json.loads(_text(message.get("data"), "the data of a notice"))
        if not isinstance(payload, dict):
            raise BrokerAnswerMalformed(f"a notice must be a JSON object, got {payload!r}")
        notice: dict[object, object] = payload
        return Announcement(
            channel=_text(message.get("channel"), "the channel of a notice"),
            name=_text(notice.get(_NAME), f"the field {_NAME!r} of a notice"),
            body=_text(notice.get(_BODY), f"the field {_BODY!r} of a notice"),
        )


async def _or_failed(call: "Awaitable[_T]") -> "_T | _Failed":
    """Run one broker call; a connection that cannot be made or was lost becomes ``_Failed`` (a value) instead
    of an exception, so that the handler answers ``BrokerUnreachable``."""
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    try:
        return await call
    except (RedisConnectionError, RedisTimeoutError, OSError) as error:
        return _Failed(f"{type(error).__name__}: {error}")


def _client(url: str, repeats: int) -> "Redis":
    """Make an asyncio client for ``url``: text replies, TCP keep-alive, and a failed call repeated ``repeats``
    times at once (never after a wait — the library's timed retries are not used)."""
    try:
        from redis.asyncio import Redis
        from redis.asyncio.retry import Retry
        from redis.backoff import NoBackoff
    except ImportError as error:
        raise ImportError(
            "redis_stream_handler needs the Redis client: install the extra 'doeff-events[redis]'"
        ) from error
    return Redis.from_url(url, decode_responses=True, socket_keepalive=True, retry=Retry(NoBackoff(), repeats))


def _broker_handler(client: "Redis", reading: "Redis") -> "ProgramHandler":
    """The handler that answers the lower-layer effects: reads, claims and notices from ``reading``, the rest
    from ``client`` (see the module's note on the two clients)."""
    listening: tuple[_Listening, ...] = ()

    @do
    def handler(
        effect: AppendEntry
        | EnsureGroup
        | ReadGroup
        | AckEntry
        | ClaimPending
        | ReadGroupPosition
        | ReadEntryRange
        | Announce
        | SubscribeChannels
        | NextAnnouncement,
        k: K,
    ) -> "EffectGenerator[object]":
        """Answer one broker operation with one awaited call; ``BrokerUnreachable`` when the server is not there."""
        nonlocal listening
        answer: object
        match effect:
            case AppendEntry():
                answer = yield Await(_or_failed(_append(client, effect)))
            case EnsureGroup():
                answer = yield Await(_or_failed(_ensure_group(client, effect)))
            case ReadGroup():
                answer = yield Await(_or_failed(_read_group(reading, effect)))
            case AckEntry(stream=stream, group=group, position=position):
                acked = yield Await(_or_failed(client.xack(stream, group, position)))
                answer = acked if isinstance(acked, _Failed) else None
            case ClaimPending():
                answer = yield Await(_or_failed(_claim(reading, effect)))
            case ReadGroupPosition():
                answer = yield Await(_or_failed(_group_position(client, effect)))
            case ReadEntryRange():
                answer = yield Await(_or_failed(_range(client, effect)))
            case Announce():
                answer = yield Await(_or_failed(_announce(client, effect)))
            case SubscribeChannels(channels=channels):
                subscription = ChannelSubscription(channels)
                pubsub = yield Await(_or_failed(_subscribe(reading, subscription)))
                if isinstance(pubsub, _Failed):
                    answer = pubsub
                else:
                    listening = (*listening, _Listening(subscription, pubsub))
                    answer = subscription
            case NextAnnouncement(subscription=wanted):
                known = next((entry for entry in listening if entry.subscription is wanted), None)
                if known is None:
                    raise LookupError(f"{wanted!r} was not made by this handler")
                answer = yield Await(_or_failed(_next_notice(known.pubsub)))
            case _:
                yield Pass(effect, k)
                return None
        if isinstance(answer, _Failed):
            answer = BrokerUnreachable(answer.detail)
        return (yield Resume(k, answer))

    return _program_handler(handler)


async def _close(client: "Redis", reading: "Redis") -> None:
    """Close both clients' connections; a server that is already gone does not make the close fail."""
    await _or_failed(client.aclose())
    await _or_failed(reading.aclose())


@do
def _run(url: str, body: "Program[_T]") -> "EffectGenerator[_T]":
    """Make the clients, run the body under the handler, and close the clients when the body ends."""
    client, reading = _client(url, 1), _client(url, 0)
    # The close runs on an exception and on the normal end, not in ``finally``: yielding inside the GeneratorExit
    # of a discarded process is an error (the same reason as doeff-records' sources).
    try:
        answer = yield _broker_handler(client, reading)(body)
    except Exception:
        yield Await(_close(client, reading))
        raise
    yield Await(_close(client, reading))
    return answer


class _BodyWrapper(partial["Program[object]"]):
    """A function that wraps a body, marked so that Hy's ``with-handlers`` applies it to the body as it is."""

    _doeff_is_handler_fn = True


def redis_stream_handler(url: str) -> "ProgramHandler":
    """Build the handler that answers the lower-layer stream effects from the Redis server at ``url``
    (for example ``redis://agora-events:6379/0``). Nothing but the URL is configured."""
    if not isinstance(url, str) or not url:
        raise ValueError("redis_stream_handler: url must be a non-empty string")
    return _BodyWrapper(_run, url)
