"""Events between processes: ``notice_events_handler`` wraps a business program so that its ``Publish`` and
``WaitForEvent`` reach other processes through a notice broker (agora-redesign #3850).

The business program keeps its shape — ``Publish(event)`` and ``WaitForEvent(types...)`` — and never names a
channel. Channels live in the arguments of this factory: the source's name and the routes (one ``NoticeRoute``
per event type).

Order of handlers (outer → inner)::

    subscribed_event_handler(bus, subscriber, types)   # the one that answers WaitForEvent
      → clock handler (GetTime / WaitWithin)           # only used while the broker is unreachable
        → memory_notice_handler(broker) | redis_notice_handler(url, timeout_seconds) → broker_back_by_retry(retry_seconds)
          → notice_events_handler(source, routes, patience_seconds)
            → business program

What the wrapper does:

- Sending. ``Publish(event)`` of a routed type becomes ``Announce`` and answers ``NoticeSent(receivers)`` — the
  broker's count of subscribers that received it (``0`` = nobody was listening; the sender decides what that
  means). An unrouted type goes on to the outer handler (the bus inside the process) as before, without calling
  the wrapper: its handler around the body declares the Publish classes of its routed types only
  (``publish_effect_type``), and the waits only when it reads channels, so the VM skips it for every other effect
  (a wrapper costs no step for what it does not route — card ki-3724ab2e9a0f).
- What the broker could not take (the one place a sender's failed notice is handled — agora-redesign #3864,
  ADR-DOE-EVENTS-002 R5). ``Publish`` never raises for it; it answers one of a closed set: ``NoticeSent``, or,
  by the route's ``when_unsent``, ``NoticeGapMarked`` (``MarkGap`` — the channel is marked as having a gap) or
  ``NoticeDropped`` (``Drop`` — nothing is kept, for what is stale at once). What is kept is only the mark of a
  channel, never the event: a gap is told as one fixed notice on that channel (``GAP_NOTICE``), which a reading
  wrapper turns into ``SourceMissed`` so its program catches up once from its records. A gap is told when the
  broker is back (one task waits for ``AwaitBrokerBack``), before the next ``Publish`` that gets through, and on
  the channels of ``MarkGap.start_channels`` when the sender starts (its previous process may have ended with a
  mark). While another task is telling the gaps, a ``Publish`` waits until it is done, so nothing arrives before
  the gap it follows. The marks belong to one wrapped body and are bounded by the number of channels. When the
  next ``Publish`` told every gap, the task waiting for the return is stopped. When nothing can answer
  ``AwaitBrokerBack``, that task fails, the marks stay for the next ``Publish``, and the failure is raised when
  the body ends. When the body ends, the waiting task is stopped and the marks are gone.
- What the sender cannot tell happened (agora card ki-a93fc14956e3). ``PublishGap(event)`` marks the channel
  ``event`` would travel on, without sending ``event``, and tells the marks at once through the same exit
  (answers ``NoticeGapTold``). What the broker cannot take stays marked like the gap of a missed ``Publish``
  (answers ``NoticeGapMarked``); a ``Drop`` route keeps nothing (``NoticeDropped``). A sender uses it when the
  answer of a write did not come: the write may have landed, and its receivers would otherwise not learn of it
  before their next start.
- Receiving. Before the body runs, the wrapper subscribes the channels its routes read and — only after the
  broker confirmed the subscription — publishes ``SourceStarted`` on the bus. Then one task waits for notices
  and publishes each received event on the bus; the body receives them one at a time with ``WaitForEvent``, or
  all that have arrived at once with ``WaitForEvents`` (the wrapper does not answer either, it only adds
  ``SourceFailed`` to the wait like doeff-records' signal source).
  Every notice received is published; one that no route of this wrapper can decode fails the source by name.
- What is not delivered. A notice reaches the subscribers connected when it is announced. Notices announced
  before the subscription, or while the connection was lost, never arrive. The wrapper does not fill that gap:
  the program that names ``SourceStarted`` / ``SourceResumed`` in its subscription catches up once from its
  records each time it receives one — both are published only after the subscription is in place, so nothing
  announced after the program's catch-up read is missed.
- Kept routes (``NoticeRoute(kept=True)`` — agora card ki-86d11cadf3be). Their events are sent with
  ``Announce(kept=True)`` (the broker keeps each in the channel's log) and their channels are read as the reader
  named ``source``: the source acknowledges each notice once its event is on the bus (``AckAnnouncement``), and
  the broker's handler hands out again a notice not acknowledged in time, and goes on from where the reader was
  after a lost connection. A notice lost between the broker and the bus is therefore received later instead of
  never; an event may then arrive twice (the reader handles repeats as it handles a catch-up).
- Broker outages. When the wait for a notice answers ``BrokerUnreachable`` the source publishes
  ``SourceStalled``, waits for ``AwaitBrokerBack`` up to ``patience_seconds`` (one ``WaitWithin`` — no retry on
  a timer), subscribes again, and after the broker confirmed publishes ``SourceResumed`` once. Past the patience
  it fails with ``NoticeSourceUnreachable`` (``SourceFailed`` reaches the body). The stall's start is one warning
  in the process's log. A broker unreachable at the first subscription is the same outage: ``SourceStalled``,
  then ``SourceResumed`` and ``SourceStarted`` once the subscription is confirmed.
- Stopping. The source task and the task waiting for the return end by ``Cancel`` when the body ends; their waits
  have no time limit of their own. Then the subscription the source held is closed (``CloseSubscription``) — when
  the body ends normally, by an error, or by a cancel from outside — so a process that wraps body after body under
  one broker handler holds no subscription of a body that ended (card ki-aa9497a3a412).
  A cancel can land while the answer of a ``Spawn`` (the task's handle) is still queued, and the scheduler then drops
  the answer: the spawner never learns the task it made. The source task ends by itself when the wrapper has ended
  (``_Reading``); the tasks that wait for the broker's return (the watcher of an outage, the one telling the gaps)
  do not begin before their spawner holds their handle (``_spawned`` / ``_claimed``) — a wait for the return ends
  by nothing but a cancel.

The channels a wrapper reads are fixed when it is built (``_Plan.channels`` is the one place that holds them).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Final, Generic, TypeVar, Union, final

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
from doeff_time import GetTime, WaitWithin
from doeff_time.effects.time import GetTimeEffect, WaitWithinEffect

from doeff import K, Pass, Program, Resume, ResumeThrow, do
from doeff import handler as _program_handler
from doeff_events.effects.events import (
    Publish,
    PublishEffect,
    PublishGapEffect,
    SourceFailed,
    SourceMissed,
    SourceResumed,
    SourceStalled,
    SourceStarted,
    WaitForEventEffect,
    WaitForEventsEffect,
    publish_effect_type,
)
from doeff_events.effects.notices import (
    AckAnnouncement,
    Announce,
    Announcement,
    AwaitBrokerBack,
    BrokerUnreachable,
    ChannelSubscription,
    CloseSubscription,
    NextAnnouncement,
    SubscribeChannels,
)

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

_E = TypeVar("_E")
_T = TypeVar("_T")

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarkGap:
    """``when_unsent`` of a route whose missed events matter: the channel is marked as having a gap, and the gap is
    told (``SourceMissed`` at the receivers, who catch up from their records). ``start_channels`` = the channels
    to tell a gap on when the sender starts, for what its previous process may have ended holding."""

    start_channels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Drop:
    """``when_unsent`` of a route whose events are stale at once: a missed event is not kept and nothing is told."""


WhenUnsent = MarkGap | Drop
"""What a route does with an event the broker could not take (a closed choice, written on every route)."""

GAP_NOTICE: Final = "doeff-events:gap"
"""The wire name of the fixed notice that tells a channel's gap (its body is the sender's source name)."""


@dataclass(frozen=True)
class NoticeRoute(Generic[_E]):
    """How one event type travels through the broker (one row of the route table).

    ``event_type`` = the type a program publishes and waits for.
    ``wire_name`` = the name that tells this type apart on the wire (several types may share a channel).
    ``channel`` = the channel an event is sent to, decided from the event's value.
    ``encode`` = event → text (JSON by convention). ``decode`` = text → event.
    ``when_unsent`` = what is done with an event the broker could not take (``MarkGap`` or ``Drop``).
    ``reads`` = the channels this side receives the type from; ``()`` = this side only sends.
    ``kept`` = the type travels on kept channels (``Announce(kept=True)``): the broker keeps each event in the
    channel's log, and a reader that did not handle one is handed it again (the module's notes). Every side of a
    channel names the same ``kept`` — a sender that does not would send where its readers never look.
    """

    event_type: type[_E]
    wire_name: str
    channel: Callable[[_E], str]
    encode: Callable[[_E], str]
    decode: Callable[[str], _E]
    when_unsent: WhenUnsent
    reads: tuple[str, ...] = ()
    kept: bool = False


@dataclass(frozen=True)
class NoticeSent:
    """What ``Publish`` of a routed event answers when the broker took it: ``receivers`` = how many subscribers the
    broker handed the notice to. ``0`` means nobody was listening and nobody will ever receive it."""

    receivers: int


@dataclass(frozen=True)
class NoticeGapMarked:
    """What ``Publish`` of a ``MarkGap`` route answers when the event was not sent: ``channel`` is marked and its
    gap will be told. ``detail`` = the broker's words, or why the event waits behind a gap being told."""

    channel: str
    detail: str


@dataclass(frozen=True)
class NoticeDropped:
    """What ``Publish`` of a ``Drop`` route answers when the broker could not take the event: it is gone. Also what
    ``PublishGap`` of a ``Drop`` route answers: such a route keeps no gap."""

    detail: str


PublishAnswer = NoticeSent | NoticeGapMarked | NoticeDropped
"""Everything ``Publish`` of a routed event answers (it never raises for a broker it cannot reach)."""


@dataclass(frozen=True)
class NoticeGapTold:
    """What ``PublishGap`` of a ``MarkGap`` route answers when the gap was told: ``channel``'s gap notice reached
    the broker (with every older gap of the body), and its connected readers are told ``SourceMissed``."""

    channel: str


GapAnswer = NoticeGapTold | NoticeGapMarked | NoticeDropped
"""Everything ``PublishGap`` of a routed event answers (it never raises for a broker it cannot reach):
``NoticeGapTold``, ``NoticeGapMarked`` (the broker could not take the gap notice — told later like the gap of a
missed ``Publish``), or ``NoticeDropped`` (a ``Drop`` route)."""


@final
class _Gaps:
    """What one wrapped body keeps for its sends: the channels with a gap not told yet (first-marked order), the
    one exit every send of the body goes through (whether it is taken, and who queues for it — first come, first
    served), the task waiting for the broker's return (``None`` = none), and the error of a waiting task that failed
    (raised when the body ends)."""

    __slots__ = ("_mut_busy", "_mut_channels", "_mut_failure", "_mut_kept", "_mut_queued", "_mut_waiter")

    def __init__(self) -> None:
        """Start with no gap and the exit free."""
        self._mut_channels: tuple[str, ...] = ()
        # The marked channels that are kept channels: their gap notice goes to the channel's log too.
        self._mut_kept: frozenset[str] = frozenset()
        self._mut_busy = False
        self._mut_queued: tuple[Promise[None], ...] = ()
        self._mut_waiter: Task[object] | None = None
        self._mut_failure: Exception | None = None

    def mark(self, channel: str, kept: bool) -> None:
        """Mark ``channel`` as having a gap (once — the marks are bounded by the channels); ``kept`` = it is a kept
        channel."""
        if channel not in self._mut_channels:
            self._mut_channels = (*self._mut_channels, channel)
        if kept:
            self._mut_kept = self._mut_kept | {channel}

    def running(self) -> "tuple[Task[object], ...]":
        """The waiting task, if one runs (the tasks to stop)."""
        return () if self._mut_waiter is None else (self._mut_waiter,)


@final
class _Reading:
    """The subscription the source holds right now — what the wrapper closes when the body ends. ``None`` = none:
    the wrapper reads nothing, or the source gave a dead subscription up and the broker has not confirmed the
    next one yet. ``_mut_ended`` = the wrapper is closing for good: its source, if it still runs, is nobody's any more
    (a cancel that lands before the wrapper holds the source task's handle leaves the source beside the wrapper, which
    can then neither stop it nor tell it apart from a source that lost its connection)."""

    __slots__ = ("_mut_ended", "_mut_held")

    def __init__(self, held: ChannelSubscription | None) -> None:
        """Start holding the first subscription (``None`` for a wrapper that reads nothing)."""
        self._mut_held = held
        self._mut_ended = False


class NoticeSourceUnreachable(RuntimeError):
    """The broker did not come back within the patience the handler was built with — the process falls and its
    restart starts over."""


class UnroutedNotice(LookupError):
    """A notice arrived on a channel this source reads, with a wire name none of its routes for that channel
    knows. It is not dropped silently: the source fails and names it."""


@dataclass(frozen=True)
class _Plan:
    """A checked set of arguments of one wrapper: the source's name, the routes, the channels it reads
    (first-seen order, no repeats), those of them that are kept channels (read from their log as the reader named
    ``source``), its patience, and the effect classes its handler around the body answers (the Publish class of each
    routed type, and the waits when it reads channels — ``()`` = it answers nothing)."""

    source: str
    routes: tuple[NoticeRoute[object], ...]
    channels: tuple[str, ...]
    kept: tuple[str, ...]
    patience_seconds: float
    answered: tuple[type[object], ...]

    def subscribing(self) -> SubscribeChannels:
        """The subscription of this wrapper's reads (its kept channels read as the reader ``source``)."""
        return SubscribeChannels(self.channels, kept=self.kept, reader=self.source if self.kept else None)

    def route_of(self, event: object) -> NoticeRoute[object] | None:
        """The route an event is sent by (by its exact type), or ``None`` for an event that stays in the process."""
        return next((route for route in self.routes if type(event) is route.event_type), None)

    def decoded(self, notice: Announcement) -> object:
        """The event of a received notice, by the route that reads its channel under its wire name; a gap notice
        on a channel this source reads is ``SourceMissed``."""
        if notice.name == GAP_NOTICE and notice.channel in self.channels:
            return SourceMissed(source=self.source, channel=notice.channel)
        route = next(
            (known for known in self.routes if known.wire_name == notice.name and notice.channel in known.reads),
            None,
        )
        if route is None:
            raise UnroutedNotice(
                f"source {self.source!r}: no route reads the notice {notice.name!r} from channel {notice.channel!r}"
            )
        return route.decode(notice.body)


def _checked_plan(source: str, routes: tuple[NoticeRoute[object], ...], patience_seconds: float) -> _Plan:
    """Check the factory's arguments once, when the handler is built, and name what is wrong."""
    if not isinstance(source, str) or not source:
        raise ValueError("notice_events_handler: source must be a non-empty string")
    if isinstance(patience_seconds, bool) or not isinstance(patience_seconds, int | float) or patience_seconds < 0:
        raise ValueError(f"notice_events_handler: patience_seconds must be a number >= 0, got {patience_seconds!r}")
    for route in routes:
        if not isinstance(route, NoticeRoute):
            raise TypeError(f"notice_events_handler: routes must be NoticeRoute values, got {route!r}")
        if not isinstance(route.when_unsent, MarkGap | Drop):
            raise TypeError(
                f"notice_events_handler: the route {route.wire_name!r} must say what is done with an event the broker "
                f"could not take (when_unsent = MarkGap(...) or Drop()), got {route.when_unsent!r}"
            )
        if route.wire_name == GAP_NOTICE:
            raise ValueError(f"notice_events_handler: the wire name {GAP_NOTICE!r} is the gap notice's own")
    types = tuple(route.event_type for route in routes)
    names = tuple(route.wire_name for route in routes)
    if len(set(types)) != len(types) or len(set(names)) != len(names):
        raise ValueError(f"notice_events_handler({source!r}): an event type or a wire name is routed twice")
    channels = tuple(dict.fromkeys(name for route in routes for name in route.reads))
    kept = tuple(dict.fromkeys(name for route in routes if route.kept for name in route.reads))
    plain = frozenset(name for route in routes if not route.kept for name in route.reads)
    if plain & frozenset(kept):
        raise ValueError(
            f"notice_events_handler({source!r}): the channels {sorted(plain & frozenset(kept))!r} are read both as kept "
            "channels and as Pub/Sub channels — every route of a channel names the same kept"
        )
    # A wrapper that reads nothing has no source whose failure could end the body's wait: the waits go outward as
    # they are, without it.
    waits = (WaitForEventEffect, WaitForEventsEffect) if channels else ()
    # ``PublishGap`` has one class for every event type: a wrapper with routes answers it and passes on the types it
    # does not route.
    gap = (PublishGapEffect,) if routes else ()
    answered = (*(publish_effect_type(route.event_type) for route in routes), *gap, *waits)
    return _Plan(
        source=source,
        routes=routes,
        channels=channels,
        kept=kept,
        patience_seconds=float(patience_seconds),
        answered=answered,
    )


@do
def _back_announced(promise: Promise[bool]) -> "EffectGenerator[None]":
    """The watcher task: wait for the broker's return and complete ``promise`` (the source waits on the promise
    with a deadline; the watcher itself has none)."""
    yield AwaitBrokerBack()
    yield CompletePromise(promise, True)


@do
def _stop_task(task: Task[object]) -> "EffectGenerator[Exception | None]":
    """Stop a task and wait until it is unwound. Answers the error of a task that had already failed (``None`` for
    one that ended by this cancel or by itself) — the caller decides whether to raise it."""
    yield Cancel(task)
    try:
        yield Wait(task)
    except TaskCancelledError:
        return None
    except Exception as error:
        return error
    return None


@dataclass(frozen=True)
class _Spawned:
    """A task made by ``_spawned`` and the claim that lets it begin (``_claimed``)."""

    task: Task[object]
    claim: Promise[bool]


@do
def _begun_when_claimed(claim: Promise[bool], program: "Program[None]") -> "EffectGenerator[None]":
    """The body of a task made by ``_spawned``: do nothing until the spawner claims it, then run ``program``. A
    spawner that was cancelled before it held this task's handle releases the claim instead (``False``)."""
    if (yield Wait(claim.future)):
        yield program


@do
def _spawned(program: "Program[None]") -> "EffectGenerator[_Spawned]":
    """Spawn ``program`` held back until its spawner claims it. The scheduler drops the answer of a ``Spawn`` (the
    task's handle) when the spawner is cancelled before it resumes, and the tasks of this file wait in
    ``AwaitBrokerBack``, which only a cancel wakes: a task begun at once would wait on with nobody able to stop it
    (the spawner's stop never learned it). So the spawner keeps the handle first and ``_claimed`` lets the task
    begin; a spawner cancelled in the ``Spawn`` releases the claim in its own stop, and the task ends unbegun."""
    claim: Promise[bool] = yield CreatePromise()
    try:
        task: Task[object] = yield Spawn(_begun_when_claimed(claim, program))
    except Exception:
        yield CompletePromise(claim, False)
        raise
    return _Spawned(task, claim)


@do
def _claimed(spawned: _Spawned) -> "EffectGenerator[None]":
    """Let a task made by ``_spawned`` begin — after its spawner has kept its handle where its stop finds it."""
    yield CompletePromise(spawned.claim, True)


@do
def _came_back_within(seconds: float) -> "EffectGenerator[bool]":
    """Wait for the broker's return for at most ``seconds`` (True = it is back). One watcher task receives the
    answer of ``AwaitBrokerBack`` and one ``WaitWithin`` waits for it — nothing is retried on a timer."""
    if seconds <= 0:
        return False
    promise: Promise[bool] = yield CreatePromise()
    spawned = yield _spawned(_back_announced(promise))
    watcher = spawned.task
    # The stop effects run on an exception and on the normal end, not in ``finally``: yielding inside the
    # GeneratorExit of a discarded process is an error (the same reason as doeff-records' came-back-within).
    try:
        yield _claimed(spawned)
        came = yield WaitWithin(promise.future, seconds)
    except Exception:
        yield _stop_task(watcher)
        raise
    failed = yield _stop_task(watcher)
    if failed is not None:
        # Nobody could tell whether the broker is back (for example no handler answers AwaitBrokerBack).
        raise failed
    return came is not None


@do
def _subscribed_after_outage(plan: _Plan, first: BrokerUnreachable) -> "EffectGenerator[ChannelSubscription]":
    """Get past a broker outage without falling: publish ``SourceStalled``, wait for the broker's return up to the
    patience (counted from the first failure), and subscribe again. Each new attempt follows an answer of
    ``AwaitBrokerBack`` — not a timer. Answers the new, confirmed subscription; past the patience raises
    ``NoticeSourceUnreachable``."""
    since: datetime = yield GetTime()
    # Nothing else is seen until the broker is back or the patience is over: the stall's start is one line in the
    # process's log (card ki-aa9497a3a412 — a full pool of connections stalled a source silently for 900 s).
    _logger.warning(
        "notice source %r stalled: the broker is unreachable (%s); waiting up to %s s for its return",
        plan.source,
        first.detail,
        plan.patience_seconds,
    )
    yield Publish(SourceStalled(source=plan.source, detail=first.detail, since=since))
    answer: ChannelSubscription | BrokerUnreachable = first
    while isinstance(answer, BrokerUnreachable):
        now: datetime = yield GetTime()
        back = yield _came_back_within(plan.patience_seconds - (now - since).total_seconds())
        if not back:
            raise NoticeSourceUnreachable(
                f"source {plan.source!r}: the notice broker did not come back within {plan.patience_seconds} s: "
                f"{answer.detail}"
            )
        answer = yield plan.subscribing()
    return answer


@do
def _first_subscription(plan: _Plan) -> "EffectGenerator[ChannelSubscription]":
    """Subscribe the channels before the body runs; a broker that is unreachable at the start is waited for like
    any outage, and its end is told like any outage's: ``SourceResumed`` once the broker confirmed the new
    subscription (``SourceStalled`` promises it — a program that keeps the stalled sources until their return
    would otherwise hold this one as stalled until the process ends: agora card ki-b7aa8ddce32d). Answers once the
    broker confirmed the subscription."""
    answer = yield plan.subscribing()
    if isinstance(answer, BrokerUnreachable):
        answer = yield _subscribed_after_outage(plan, answer)
        yield Publish(SourceResumed(source=plan.source))
    return answer


@do
def _read_notices(plan: _Plan, subscription: ChannelSubscription, reading: _Reading) -> "EffectGenerator[None]":
    """The source task: wait for the next notice and publish its event on the bus. When the connection is lost,
    give the dead subscription up, get past the outage, and tell ``SourceResumed`` — after the new subscription
    is confirmed. ``reading`` follows the subscription it holds. It ends only by ``Cancel`` (the wait has no time
    limit — the cancel ends it), by failing, or by itself when the wrapper has ended (``reading._mut_ended``: the
    wrapper's cancel reached it before it held this task's handle, so the cancel could not stop it — it ends here,
    and a subscription closed by the wrapper is not read as a broker that went away)."""
    current = subscription
    while True:
        if reading._mut_ended:
            return None
        notice = yield NextAnnouncement(current)
        if reading._mut_ended:
            return None
        if isinstance(notice, BrokerUnreachable):
            yield CloseSubscription(current)
            reading._mut_held = None
            current = yield _subscribed_after_outage(plan, notice)
            if reading._mut_ended:
                # The wrapper ended while this source waited for the broker: nobody will close what it just made.
                yield CloseSubscription(current)
                return None
            reading._mut_held = current
            yield Publish(SourceResumed(source=plan.source))
        else:
            yield Publish(plan.decoded(notice))
            if notice.receipt:
                # A kept channel's notice is acknowledged once it is on the bus: one that never got here (the task
                # stopped, or it was lost on the way up) is handed out again by the broker's handler.
                yield AckAnnouncement(current, notice)


@do
def _subscription_closed(reading: _Reading) -> "EffectGenerator[None]":
    """Close the subscription the stopped source held, if it held one (once — it is then held by nobody), and mark
    the wrapper ended so that a source this wrapper could not stop ends itself."""
    reading._mut_ended = True
    held, reading._mut_held = reading._mut_held, None
    if held is not None:
        yield CloseSubscription(held)


@do
def _failure_announced(source: str, program: "Program[None]") -> "EffectGenerator[None]":
    """The body of the source task: if it falls (not by cancel), publish ``SourceFailed`` with its error first, so
    the body's wait receives the failure as an event instead of racing the task."""
    try:
        yield program
    except TaskCancelledError:
        raise
    except Exception as error:
        yield Publish(SourceFailed(source=source, error=error))
        raise


@do
def _exit_taken(gaps: _Gaps) -> "EffectGenerator[None]":
    """Take the body's one exit: at once if it is free, otherwise after everyone who queued before (the one leaving
    hands it over — first come, first served). A task stopped while it queues leaves the queue, and one stopped
    right after it was handed the exit hands it on, so the exit is never left taken by nobody."""
    if not gaps._mut_busy:
        gaps._mut_busy = True
        return None
    turn: Promise[None] = yield CreatePromise()
    gaps._mut_queued = (*gaps._mut_queued, turn)
    try:
        yield Wait(turn.future)
    except Exception:
        if turn in gaps._mut_queued:
            gaps._mut_queued = tuple(queued for queued in gaps._mut_queued if queued is not turn)
        else:
            yield _exit_left(gaps)
        raise
    return None


@do
def _exit_left(gaps: _Gaps) -> "EffectGenerator[None]":
    """Leave the exit: hand it to the first who queued, or free it when nobody did."""
    if gaps._mut_queued:
        turn, gaps._mut_queued = gaps._mut_queued[0], gaps._mut_queued[1:]
        yield CompletePromise(turn, None)
    else:
        gaps._mut_busy = False


@do
def _through_exit(gaps: _Gaps, sending: "Program[_T]") -> "EffectGenerator[_T]":
    """Run ``sending`` holding the exit, and leave it afterwards — also when ``sending`` raises or is stopped."""
    yield _exit_taken(gaps)
    try:
        answer = yield sending
    except Exception:
        yield _exit_left(gaps)
        raise
    yield _exit_left(gaps)
    return answer


@do
def _gaps_told(plan: _Plan, gaps: _Gaps) -> "EffectGenerator[BrokerUnreachable | None]":
    """Tell every marked gap, oldest first, and unmark each one told (the caller holds the exit). Answers ``None``
    when all were told, or the broker's refusal (the rest stay marked)."""
    while gaps._mut_channels:
        channel = gaps._mut_channels[0]
        receivers = yield Announce(channel, GAP_NOTICE, plan.source, kept=channel in gaps._mut_kept)
        if isinstance(receivers, BrokerUnreachable):
            return receivers
        gaps._mut_channels = gaps._mut_channels[1:]
    return None


@do
def _gaps_told_on_return(plan: _Plan, gaps: _Gaps) -> "EffectGenerator[None]":
    """The task waiting for the broker's return: wait for ``AwaitBrokerBack``, then tell the gaps through the exit
    (behind any send already in it), and wait again if the broker is away again. Ends when no gap is left (a later
    mark starts a new task); the next ``Publish`` that told every gap stops it. When ``AwaitBrokerBack`` cannot be
    answered (nothing answers it, or its answerer fails), this task fails: no new task is started for the marks,
    the next ``Publish`` that gets through still tells them, and the failure is raised when the body ends."""
    while gaps._mut_channels:
        yield AwaitBrokerBack()
        yield _through_exit(gaps, _gaps_told(plan, gaps))
    gaps._mut_waiter = None


@do
def _marked(plan: _Plan, gaps: _Gaps, channel: str, kept: bool, detail: str) -> "EffectGenerator[NoticeGapMarked]":
    """Mark ``channel`` (``kept`` = a kept channel) and make sure one task waits for the broker's return."""
    gaps.mark(channel, kept)
    if gaps._mut_waiter is None:
        spawned = yield _spawned(_gaps_told_on_return(plan, gaps))
        gaps._mut_waiter = spawned.task
        yield _claimed(spawned)
    return NoticeGapMarked(channel=channel, detail=detail)


@do
def _sent_in_exit(plan: _Plan, gaps: _Gaps, route: NoticeRoute[object], event: object) -> "EffectGenerator[PublishAnswer]":
    """Send one routed event while holding the exit: tell the gaps first if there are any, then the event. What
    the broker cannot take is marked (``MarkGap``) or dropped (``Drop``)."""
    channel = route.channel(event)
    match route.when_unsent:
        case Drop():
            receivers = yield Announce(channel, route.wire_name, route.encode(event), kept=route.kept)
            if isinstance(receivers, BrokerUnreachable):
                return NoticeDropped(detail=receivers.detail)
            return NoticeSent(receivers)
        case MarkGap():
            if gaps._mut_channels:
                refused = yield _gaps_told(plan, gaps)
                if refused is not None:
                    return (yield _marked(plan, gaps, channel, route.kept, refused.detail))
                failed = yield _stopped_tasks(gaps.running())
                gaps._mut_waiter = None
                gaps._mut_failure = gaps._mut_failure if gaps._mut_failure is not None else failed
            receivers = yield Announce(channel, route.wire_name, route.encode(event), kept=route.kept)
            if isinstance(receivers, BrokerUnreachable):
                return (yield _marked(plan, gaps, channel, route.kept, receivers.detail))
            return NoticeSent(receivers)


@do
def _gap_told_in_exit(plan: _Plan, gaps: _Gaps, route: NoticeRoute[object], event: object) -> "EffectGenerator[GapAnswer]":
    """Tell the gap of ``event``'s channel while holding the exit, with every older gap first (``PublishGap``): the
    channel is marked like a missed send, and the marks are told now. What the broker cannot take stays marked for
    the return or the next ``Publish`` (``MarkGap``); a ``Drop`` route keeps nothing."""
    channel = route.channel(event)
    match route.when_unsent:
        case Drop():
            return NoticeDropped(detail=f"the route {route.wire_name!r} drops what it misses: no gap is kept")
        case MarkGap():
            gaps.mark(channel, route.kept)
            refused = yield _gaps_told(plan, gaps)
            if refused is not None:
                return (yield _marked(plan, gaps, channel, route.kept, refused.detail))
            failed = yield _stopped_tasks(gaps.running())
            gaps._mut_waiter = None
            gaps._mut_failure = gaps._mut_failure if gaps._mut_failure is not None else failed
            return NoticeGapTold(channel=channel)


@do
def _sent(plan: _Plan, gaps: _Gaps, route: NoticeRoute[object], event: object) -> "EffectGenerator[PublishAnswer]":
    """Send one routed event through the body's one exit, so sends leave in the order they came and nothing
    overtakes a gap being told (the sends of tasks the body spawned queue there too)."""
    answer: PublishAnswer = yield _through_exit(gaps, _sent_in_exit(plan, gaps, route, event))
    return answer


def _foreign_failure(plan: _Plan, wanted: tuple[type[object], ...], came: object) -> bool:
    """Whether ``came`` is a failure of another source on the same bus that the body did not ask for (it asks for
    failures by naming ``SourceFailed``) — such a failure is not this body's, and the wait drops it."""
    return isinstance(came, SourceFailed) and came.source != plan.source and SourceFailed not in wanted


def _own_failure(plan: _Plan, came: object) -> SourceFailed | None:
    """``came`` if it is the failure of this wrapper's source (the body's wait ends with its error), else ``None``."""
    return came if isinstance(came, SourceFailed) and came.source == plan.source else None


def _body_handler(plan: _Plan, gaps: _Gaps) -> "ProgramHandler":
    """The handler around the body: sends routed ``Publish`` (marking what the broker cannot take) and passes
    ``WaitForEvent`` / ``WaitForEvents`` outward with ``SourceFailed`` added, so that a failure of this wrapper's
    source ends the body's wait with its error. Its effect annotation is ``plan.answered`` (read per closure by the
    VM), so it is not called for an unrouted ``Publish``, nor for a wait when the wrapper reads nothing; the clauses
    below still pass such an effect on if it comes."""
    answered = Union[plan.answered]  # noqa: UP007 — the VM's filter needs a union of the runtime classes

    @do
    def handler(effect: answered, k: K) -> "EffectGenerator[object]":  # pyright: ignore[reportInvalidTypeForm] — a union of the per-type Publish classes chosen when the wrapper is built (doeff_vm._effect_types reads it per closure)
        """Translate one effect of the body (see ``_body_handler``)."""
        match effect:
            case PublishEffect(event=event):
                route = plan.route_of(event)
                if route is None:
                    yield Pass(effect, k)
                    return None
                answer = yield _sent(plan, gaps, route, event)
                return (yield Resume(k, answer))
            case PublishGapEffect(event=event):
                route = plan.route_of(event)
                if route is None:
                    yield Pass(effect, k)
                    return None
                told = yield _through_exit(gaps, _gap_told_in_exit(plan, gaps, route, event))
                return (yield Resume(k, told))
            case WaitForEventEffect(event_types=wanted):
                came = yield WaitForEventEffect((*wanted, SourceFailed))
                while _foreign_failure(plan, wanted, came):
                    came = yield WaitForEventEffect((*wanted, SourceFailed))
                failure = _own_failure(plan, came)
                if failure is not None:
                    return (yield ResumeThrow(k, failure.error))
                return (yield Resume(k, came))
            case WaitForEventsEffect(event_types=wanted):
                kept: tuple[object, ...] = ()
                # Wait again while every event that came was another source's failure the body did not ask for.
                while not kept:
                    came_all: tuple[object, ...] = yield WaitForEventsEffect((*wanted, SourceFailed))
                    failure = next((own for came in came_all if (own := _own_failure(plan, came)) is not None), None)
                    if failure is not None:
                        return (yield ResumeThrow(k, failure.error))
                    kept = tuple(came for came in came_all if not _foreign_failure(plan, wanted, came))
                return (yield Resume(k, kept))

    return _program_handler(handler)


@do
def _stopped_tasks(tasks: "tuple[Task[object], ...]") -> "EffectGenerator[Exception | None]":
    """Stop every task; answer the first error of one that had already failed (``None`` if none had)."""
    first: Exception | None = None
    for task in tasks:
        failed = yield _stop_task(task)
        first = first if first is not None else failed
    return first


@do
def _started_gaps_told(plan: _Plan, gaps: _Gaps) -> "EffectGenerator[None]":
    """Tell a gap on every start channel of the routes (what the previous process may have ended holding);
    what the broker cannot take stays marked for the return or the next ``Publish``."""
    for route in plan.routes:
        if isinstance(route.when_unsent, MarkGap):
            for channel in route.when_unsent.start_channels:
                gaps.mark(channel, route.kept)
    if gaps._mut_channels:
        refused = yield _through_exit(gaps, _gaps_told(plan, gaps))
        if refused is not None:
            spawned = yield _spawned(_gaps_told_on_return(plan, gaps))
            gaps._mut_waiter = spawned.task
            yield _claimed(spawned)


@do
def _run(plan: _Plan, body: "Program[_T]") -> "EffectGenerator[_T]":
    """Begin the subscription, tell the start once it is confirmed, tell the start gaps, run the source task beside
    the body (the body stays in this task, so a cancel from outside reaches it), and when the body ends — normally,
    by an error, or by a cancel — stop the source and the task waiting for the return, then close the subscription
    the source held (card ki-aa9497a3a412: a process that wraps body after body holds one subscription at a time,
    not one per body). A wrapper that only sends has no source: nothing to subscribe, nothing to tell but its
    start gaps."""
    gaps = _Gaps()
    first: ChannelSubscription | None = None
    if plan.channels:
        first = yield _first_subscription(plan)
    reading = _Reading(first)
    source: tuple[Task[object], ...] = ()
    # The stop runs on an exception and on the normal end, not in ``finally`` (see ``_came_back_within``). It covers
    # everything after the subscription, so a cancel that lands while the start is told also closes it.
    try:
        if first is not None:
            yield Publish(SourceStarted(source=plan.source))
            reader: Task[object] = yield Spawn(_failure_announced(plan.source, _read_notices(plan, first, reading)))
            source = (reader,)
        yield _started_gaps_told(plan, gaps)
        # A wrapper with no route answers nothing around the body (the source alone is enough).
        wrapped = _body_handler(plan, gaps)(body) if plan.answered else body
        answer = yield wrapped
    except Exception:
        yield _stopped_tasks((*source, *gaps.running()))
        yield _subscription_closed(reading)
        raise
    failed = yield _stopped_tasks((*source, *gaps.running()))
    yield _subscription_closed(reading)
    failed = gaps._mut_failure if gaps._mut_failure is not None else failed
    if failed is not None:
        # A task fell while the body did not wait: the failure is not dropped.
        raise failed
    return answer


# Bare Program (Program[Any, Any]): a partial cannot carry the body's result type to its call; the factory's ProgramHandler does.
class _BodyWrapper(partial["Program"]):
    """A function that wraps a body, marked so that Hy's ``with-handlers`` applies it to the body as it is."""

    _doeff_is_handler_fn = True


def notice_events_handler(
    source: str, routes: tuple[NoticeRoute[object], ...], patience_seconds: float
) -> "ProgramHandler":
    """Build the wrapper that carries a body's events through the notice broker.

    ``source`` = this process's name for its source: the ``source`` of the ``SourceStarted`` / ``SourceStalled`` /
    ``SourceResumed`` / ``SourceFailed`` it publishes.
    ``routes`` = one ``NoticeRoute`` per event type that travels through the broker.
    ``patience_seconds`` = how long the source waits for an unreachable broker before it fails (no default: the
    composition chooses it in one place).

    The arguments are checked here; the subscription begins at the head of the wrapped body, before the body's
    first effect.
    """
    return _BodyWrapper(_run, _checked_plan(source, routes, patience_seconds))


# Every effect one wrapper performs around the body: the subscription and the wait for notices (``SubscribeChannels``,
# ``NextAnnouncement``, ``AckAnnouncement`` of a kept channel's notice, ``CloseSubscription``), sending a routed
# event (``Announce``), what the source task publishes
# on the bus (``PublishEffect`` — the received events and ``SourceStarted`` / ``SourceStalled`` / ``SourceResumed`` /
# ``SourceFailed``), getting past an outage (``AwaitBrokerBack``, ``GetTimeEffect``, ``WaitWithinEffect``, the watcher
# task's ``CreatePromise`` / ``CompletePromise``), and running and stopping its tasks (``Spawn``, ``Cancel``, ``Wait``).
# The body's own ``WaitForEvent`` and its unrouted ``Publish`` go outward as the body's effects and are not counted.
# ``tests/test_notice_events_closure.py`` checks that this is what the wrapper really performs.
NOTICE_EVENTS_EFFECTS = (
    SubscribeChannels,
    NextAnnouncement,
    AckAnnouncement,
    CloseSubscription,
    Announce,
    PublishEffect,
    AwaitBrokerBack,
    GetTimeEffect,
    WaitWithinEffect,
    CreatePromise,
    CompletePromise,
    Spawn,
    Cancel,
    Wait,
)

# The declaration doeff-effect-analyzer reads for a body wrapper (it cannot read inside the factory): the wrapper
# closes nothing for the handlers outside it (``__doeff_handles__ = ()`` — a routed ``Publish`` becomes ``Announce``,
# everything else the body performs goes outward) and performs ``NOTICE_EVENTS_EFFECTS`` around the body.
notice_events_handler.__doeff_handles__ = ()
notice_events_handler.__doeff_effects__ = NOTICE_EVENTS_EFFECTS
