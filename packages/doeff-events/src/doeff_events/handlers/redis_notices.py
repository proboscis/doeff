"""The notice broker on Redis: the lower layer of ``doeff_events.effects.notices`` answered by a Redis server's
Pub/Sub (agora-redesign #3850).

``redis_notice_handler(url)`` needs the optional dependency ``doeff-events[redis]`` (redis-py's asyncio client).
The module itself imports without it; the client library is imported when the wrapped body starts.

How it talks to Redis:

- Every operation is one ``Await`` of a redis-py coroutine, so it needs ``await_handler`` outside (inside
  ``scheduled``). The wait for the next notice holds no scheduler task busy and has no time limit: cancelling the
  waiting task cancels the coroutine (``await_handler`` forwards the cancel), which is how a source stops.
- When the server cannot be reached (connection refused, connection lost, a socket error) the answer is
  ``BrokerUnreachable`` with the library's words. The library's timed retries are not used, so the answer comes
  at once; waiting for the server's return is left to ``AwaitBrokerBack`` — which this handler does not answer:
  the composition does, from whatever tells it that the server is back.
- ``SubscribeChannels`` answers only after the server confirmed every channel. A subscription lives on one
  connection; when that connection is lost the subscription is dead (``NextAnnouncement`` answers
  ``BrokerUnreachable``) and the caller makes a new one — this handler never re-subscribes behind the caller's
  back, because a notice announced between the loss and the new subscription is gone and the caller must know.
- Nothing received is dropped on this side: redis-py reads a subscription's connection only when
  ``NextAnnouncement`` asks (no background reader, no queue of its own); what the server sent in the meantime
  waits in the socket's buffers, and when those are full, in the server's output buffer for this client. The
  server closes the connection of a subscriber whose output buffer passes ``client-output-buffer-limit pubsub``
  — which this handler answers as ``BrokerUnreachable``, like any lost connection.
- Two clients, made when the wrapped body starts and closed when it ends: subscriptions use one that never
  repeats a call (a lost connection must be seen), ``Announce`` uses one that repeats a failed call once, at
  once, on a fresh connection (a pooled connection that died while idle is not an outage). The only connection
  setting is the URL (plus TCP keep-alive, so that a waiting subscriber notices a peer that vanished).
"""

import json
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final, TypeVar, final

from doeff_core_effects.effects import Await
from doeff_time import Delay

from doeff import K, Pass, Program, Resume, do
from doeff import handler as _program_handler
from doeff_events.effects.notices import (
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

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from redis.asyncio import Redis
    from redis.asyncio.client import PubSub

    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

_T = TypeVar("_T")

_NAME: Final = "name"
_BODY: Final = "body"


class BrokerAnswerMalformed(TypeError):
    """Redis (or the client library) answered in a shape this handler does not know — named instead of guessed."""


def _text(value: object, what: str) -> str:
    """Check a value read from Redis is text (the client decodes responses)."""
    if not isinstance(value, str):
        raise BrokerAnswerMalformed(f"{what} must be text, got {value!r}")
    return value


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


async def _subscribe(client: "Redis", subscription: ChannelSubscription) -> "PubSub":
    """``SUBSCRIBE`` and wait for the server's confirmation of every channel, so that a notice announced after
    the answer is received. A connection that cannot be made is closed again before the failure is passed on."""
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(*subscription.channels)
        confirmed = 0
        while confirmed < len(subscription.channels):
            message: object = await pubsub.get_message(ignore_subscribe_messages=False, timeout=None)
            if isinstance(message, dict) and message.get("type") == "subscribe":
                confirmed += 1
    except BaseException:
        await _or_failed(pubsub.aclose())
        raise
    return pubsub


async def _next_notice(pubsub: "PubSub") -> Announcement:
    """Wait (no time limit) for the next notice of the subscription. Only notices are expected on a subscribed
    connection once the subscription is confirmed; another kind of message is named, not skipped."""
    message: object = await pubsub.get_message(ignore_subscribe_messages=False, timeout=None)
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
            "redis_notice_handler needs the Redis client: install the extra 'doeff-events[redis]'"
        ) from error
    return Redis.from_url(url, decode_responses=True, socket_keepalive=True, retry=Retry(NoBackoff(), repeats))


def _broker_handler(sending: "Redis", receiving: "Redis") -> "ProgramHandler":
    """The handler that answers the lower-layer effects: ``Announce`` from ``sending``, subscriptions from
    ``receiving`` (see the module's note on the two clients)."""
    listening: tuple[_Listening, ...] = ()

    @do
    def handler(
        effect: Announce | SubscribeChannels | NextAnnouncement | CloseSubscription | ProbeBroker, k: K
    ) -> "EffectGenerator[object]":
        """Answer one broker operation with one awaited call; ``BrokerUnreachable`` when the server is not there."""
        nonlocal listening
        answer: object = None
        match effect:
            case Announce():
                answer = yield Await(_or_failed(_announce(sending, effect)))
            case ProbeBroker():
                answer = yield Await(_or_failed(_reached(sending)))
            case SubscribeChannels(channels=channels):
                subscription = ChannelSubscription(channels)
                pubsub = yield Await(_or_failed(_subscribe(receiving, subscription)))
                if isinstance(pubsub, _Failed):
                    answer = pubsub
                else:
                    listening = (*listening, _Listening(subscription, pubsub))
                    answer = subscription
            case NextAnnouncement(subscription=wanted):
                known = next((entry for entry in listening if entry.subscription is wanted), None)
                if known is None:
                    raise LookupError(f"{wanted!r} was not made by this handler, or was closed")
                answer = yield Await(_or_failed(_next_notice(known.pubsub)))
            case CloseSubscription(subscription=closing):
                closed = tuple(entry for entry in listening if entry.subscription is closing)
                listening = tuple(entry for entry in listening if entry.subscription is not closing)
                for entry in closed:
                    yield Await(_or_failed(entry.pubsub.aclose()))
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
def _run(url: str, body: "Program[_T]") -> "EffectGenerator[_T]":
    """Make the clients, run the body under the handler, and close the clients when the body ends."""
    sending, receiving = _client(url, 1), _client(url, 0)
    # The close runs on an exception and on the normal end, not in ``finally``: yielding inside the GeneratorExit
    # of a discarded process is an error (the same reason as doeff-records' sources).
    try:
        answer = yield _broker_handler(sending, receiving)(body)
    except Exception:
        yield Await(_close(sending, receiving))
        raise
    yield Await(_close(sending, receiving))
    return answer


class _BodyWrapper(partial["Program[object]"]):
    """A function that wraps a body, marked so that Hy's ``with-handlers`` applies it to the body as it is."""

    _doeff_is_handler_fn = True


def redis_notice_handler(url: str) -> "ProgramHandler":
    """Build the handler that answers the lower-layer notice effects from the Redis server at ``url``
    (for example ``redis://agora-events:6379/0``). Nothing but the URL is configured."""
    if not isinstance(url, str) or not url:
        raise ValueError("redis_notice_handler: url must be a non-empty string")
    return _BodyWrapper(_run, url)


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

    @do
    def handler(effect: AwaitBrokerBack, k: K) -> "EffectGenerator[object]":
        """Try the connection until it can be made."""
        # Retried on a timer because a broker that is down sends nothing: nothing else can tell its return.
        while isinstance((yield ProbeBroker()), BrokerUnreachable):
            yield Delay(retry_seconds)
        return (yield Resume(k, None))

    return _program_handler(handler)
