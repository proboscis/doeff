"""The notice broker on Redis: the lower layer of ``doeff_events.effects.notices`` answered by a Redis server's
Pub/Sub (agora-redesign #3850).

``redis_notice_handler(url, timeout_seconds, silence_seconds)`` needs the optional dependency ``doeff-events[redis]``
(redis-py's asyncio client). The module itself imports without it; the client library is imported when the wrapped
body starts.

How it talks to Redis:

- Every operation is one ``Await`` of a redis-py coroutine, so it needs ``await_handler`` outside (inside
  ``scheduled``). The wait for the next notice holds no scheduler task busy and has no time limit of its own while
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
- Nothing received is dropped on this side: redis-py reads a subscription's connection only when
  ``NextAnnouncement`` asks (no background reader, no queue of its own); what the server sent in the meantime
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
"""

import json
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Final, Generic, TypeVar, final

from doeff_core_effects.effects import Await
from doeff_time import Delay, GetTime

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
    from collections.abc import Awaitable, Callable, Generator

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


async def _next_notice(pubsub: "PubSub", silence_seconds: float) -> Announcement:
    """Wait for the next notice of the subscription — without a limit while the connection answers, and failing
    as a lost connection once it answers nothing for ``silence_seconds``. Only notices are expected on a
    subscribed connection once the subscription is confirmed; another kind of message is named, not skipped."""
    message = await _next_message(pubsub, silence_seconds)
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


def _broker_handler(sending: "Redis", receiving: "Redis", silence_seconds: float) -> "ProgramHandler":
    """The handler that answers the lower-layer effects: ``Announce`` from ``sending``, subscriptions from
    ``receiving`` whose connections count as lost after ``silence_seconds`` without an answer (see the module's
    notes on the two clients and on a half-open connection)."""
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
                answer = yield Await(_attempt(partial(_announce, sending, effect)))
            case ProbeBroker():
                answer = yield Await(_attempt(partial(_reached, sending)))
            case SubscribeChannels(channels=channels):
                subscription = ChannelSubscription(channels)
                pubsub = yield Await(_attempt(partial(_subscribe, receiving, subscription, silence_seconds)))
                if isinstance(pubsub, _Failed):
                    answer = pubsub
                else:
                    listening = (*listening, _Listening(subscription, pubsub))
                    answer = subscription
            case NextAnnouncement(subscription=wanted):
                known = next((entry for entry in listening if entry.subscription is wanted), None)
                if known is None:
                    raise LookupError(f"{wanted!r} was not made by this handler, or was closed")
                answer = yield Await(_attempt(partial(_next_notice, known.pubsub, silence_seconds)))
            case CloseSubscription(subscription=closing):
                closed = tuple(entry for entry in listening if entry.subscription is closing)
                listening = tuple(entry for entry in listening if entry.subscription is not closing)
                for entry in closed:
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
def _run(url: str, timeout_seconds: float, silence_seconds: float, body: "Program[_T]") -> "EffectGenerator[_T]":
    """Make the clients, run the body under the handler, and close the clients when the body ends."""
    sending = _client(url, 1, timeout_seconds, calls_end=True)
    receiving = _client(url, 0, timeout_seconds, calls_end=False)
    # The close runs on an exception and on the normal end, not in ``finally``: yielding inside the GeneratorExit
    # of a discarded process is an error (the same reason as doeff-records' sources).
    try:
        answer = yield _broker_handler(sending, receiving, silence_seconds)(body)
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
    url: str, timeout_seconds: float, silence_seconds: float = SUBSCRIPTION_SILENCE_SECONDS
) -> "ProgramHandler":
    """Build the handler that answers the lower-layer notice effects from the Redis server at ``url``
    (for example ``redis://agora-events:6379/0``). ``timeout_seconds`` = how long a connection may take to be made
    and a send (``Announce``, ``ProbeBroker``) may take to be answered before it counts as unreachable — so a
    server that stops answering, or a network that goes silent, never holds a sender (agora-redesign #3864). No
    default: the composition names it. ``silence_seconds`` = how long a subscription's connection may answer
    nothing (no notice, no answer to ``PING``) before it counts as lost — so a half-open connection is told as
    ``BrokerUnreachable`` within it (card ki-227ad148680d). The default ``SUBSCRIPTION_SILENCE_SECONDS`` (7 s) is
    the target of agora's read replica keeper."""
    if not isinstance(url, str) or not url:
        raise ValueError("redis_notice_handler: url must be a non-empty string")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float) or timeout_seconds <= 0:
        raise ValueError(f"redis_notice_handler: timeout_seconds must be a number > 0, got {timeout_seconds!r}")
    if isinstance(silence_seconds, bool) or not isinstance(silence_seconds, int | float) or silence_seconds <= 0:
        raise ValueError(f"redis_notice_handler: silence_seconds must be a number > 0, got {silence_seconds!r}")
    return _BodyWrapper(_run, url, float(timeout_seconds), float(silence_seconds))


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
