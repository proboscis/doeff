"""Events between processes: ``notice_events_handler`` wraps a business program so that its ``Publish`` and
``WaitForEvent`` reach other processes through a notice broker (agora-redesign #3850).

The business program keeps its shape — ``Publish(event)`` and ``WaitForEvent(types...)`` — and never names a
channel. Channels live in the arguments of this factory: the source's name and the routes (one ``NoticeRoute``
per event type).

Order of handlers (outer → inner)::

    subscribed_event_handler(bus, subscriber, types)   # the one that answers WaitForEvent
      → clock handler (GetTime / WaitWithin)           # only used while the broker is unreachable
        → memory_notice_handler(broker) | redis_notice_handler(url)  (+ whoever answers AwaitBrokerBack)
          → notice_events_handler(source, routes, patience_seconds)
            → business program

What the wrapper does:

- Sending. ``Publish(event)`` of a routed type becomes ``Announce`` and answers ``NoticeSent(receivers)`` — the
  broker's count of subscribers that received it (``0`` = nobody was listening; the sender decides what that
  means). An unrouted type goes on to the outer handler (the bus inside the process) as before.
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
- Receiving. Before the body runs, the wrapper subscribes the channels its routes read and — only after the
  broker confirmed the subscription — publishes ``SourceStarted`` on the bus. Then one task waits for notices
  and publishes each received event on the bus; the body receives them one at a time with ``WaitForEvent``
  (the wrapper does not answer it, it only adds ``SourceFailed`` to the wait like doeff-records' signal source).
  Every notice received is published; one that no route of this wrapper can decode fails the source by name.
- What is not delivered. A notice reaches the subscribers connected when it is announced. Notices announced
  before the subscription, or while the connection was lost, never arrive. The wrapper does not fill that gap:
  the program that names ``SourceStarted`` / ``SourceResumed`` in its subscription catches up once from its
  records each time it receives one — both are published only after the subscription is in place, so nothing
  announced after the program's catch-up read is missed.
- Broker outages. When the wait for a notice answers ``BrokerUnreachable`` the source publishes
  ``SourceStalled``, waits for ``AwaitBrokerBack`` up to ``patience_seconds`` (one ``WaitWithin`` — no retry on
  a timer), subscribes again, and after the broker confirmed publishes ``SourceResumed`` once. Past the patience
  it fails with ``NoticeSourceUnreachable`` (``SourceFailed`` reaches the body).
- Stopping. The source task and the task waiting for the return end by ``Cancel`` when the body ends; their waits
  have no time limit of their own.

The channels a wrapper reads are fixed when it is built (``_Plan.channels`` is the one place that holds them).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Final, Generic, TypeVar, final

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
    SourceFailed,
    SourceMissed,
    SourceResumed,
    SourceStalled,
    SourceStarted,
    WaitForEventEffect,
)
from doeff_events.effects.notices import (
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
    """

    event_type: type[_E]
    wire_name: str
    channel: Callable[[_E], str]
    encode: Callable[[_E], str]
    decode: Callable[[str], _E]
    when_unsent: WhenUnsent
    reads: tuple[str, ...] = ()


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
    """What ``Publish`` of a ``Drop`` route answers when the broker could not take the event: it is gone."""

    detail: str


PublishAnswer = NoticeSent | NoticeGapMarked | NoticeDropped
"""Everything ``Publish`` of a routed event answers (it never raises for a broker it cannot reach)."""


@final
class _Gaps:
    """The marks of one wrapped body: the channels with a gap not told yet (first-marked order), whether a gap is
    being told now and who waits for that to end, the task waiting for the broker's return (``None`` = none), and
    the error of a waiting task that failed (raised when the body ends)."""

    __slots__ = ("_mut_after_telling", "_mut_channels", "_mut_failure", "_mut_telling", "_mut_waiter")

    def __init__(self) -> None:
        """Start with no gap."""
        self._mut_channels: tuple[str, ...] = ()
        self._mut_telling = False
        self._mut_after_telling: tuple[Promise[None], ...] = ()
        self._mut_waiter: Task[object] | None = None
        self._mut_failure: Exception | None = None

    def mark(self, channel: str) -> None:
        """Mark ``channel`` as having a gap (once — the marks are bounded by the channels)."""
        if channel not in self._mut_channels:
            self._mut_channels = (*self._mut_channels, channel)

    def running(self) -> "tuple[Task[object], ...]":
        """The waiting task, if one runs (the tasks to stop)."""
        return () if self._mut_waiter is None else (self._mut_waiter,)


class NoticeSourceUnreachable(RuntimeError):
    """The broker did not come back within the patience the handler was built with — the process falls and its
    restart starts over."""


class UnroutedNotice(LookupError):
    """A notice arrived on a channel this source reads, with a wire name none of its routes for that channel
    knows. It is not dropped silently: the source fails and names it."""


@dataclass(frozen=True)
class _Plan:
    """A checked set of arguments of one wrapper: the source's name, the routes, the channels it reads
    (first-seen order, no repeats) and its patience."""

    source: str
    routes: tuple[NoticeRoute[object], ...]
    channels: tuple[str, ...]
    patience_seconds: float

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
    types = tuple(route.event_type for route in routes)
    names = tuple(route.wire_name for route in routes)
    if len(set(types)) != len(types) or len(set(names)) != len(names):
        raise ValueError(f"notice_events_handler({source!r}): an event type or a wire name is routed twice")
    channels = tuple(dict.fromkeys(name for route in routes for name in route.reads))
    return _Plan(source=source, routes=routes, channels=channels, patience_seconds=float(patience_seconds))


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


@do
def _came_back_within(seconds: float) -> "EffectGenerator[bool]":
    """Wait for the broker's return for at most ``seconds`` (True = it is back). One watcher task receives the
    answer of ``AwaitBrokerBack`` and one ``WaitWithin`` waits for it — nothing is retried on a timer."""
    if seconds <= 0:
        return False
    promise: Promise[bool] = yield CreatePromise()
    watcher: Task[object] = yield Spawn(_back_announced(promise))
    # The stop effects run on an exception and on the normal end, not in ``finally``: yielding inside the
    # GeneratorExit of a discarded process is an error (the same reason as doeff-records' came-back-within).
    try:
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
        answer = yield SubscribeChannels(plan.channels)
    return answer


@do
def _first_subscription(plan: _Plan) -> "EffectGenerator[ChannelSubscription]":
    """Subscribe the channels before the body runs; a broker that is unreachable at the start is waited for like
    any outage. Answers once the broker confirmed the subscription."""
    answer = yield SubscribeChannels(plan.channels)
    if isinstance(answer, BrokerUnreachable):
        answer = yield _subscribed_after_outage(plan, answer)
    return answer


@do
def _read_notices(plan: _Plan, subscription: ChannelSubscription) -> "EffectGenerator[None]":
    """The source task: wait for the next notice and publish its event on the bus. When the connection is lost,
    give the dead subscription up, get past the outage, and tell ``SourceResumed`` — after the new subscription
    is confirmed. It ends only by ``Cancel`` (the wait has no time limit — the cancel ends it) or by failing."""
    current = subscription
    while True:
        notice = yield NextAnnouncement(current)
        if isinstance(notice, BrokerUnreachable):
            yield CloseSubscription(current)
            current = yield _subscribed_after_outage(plan, notice)
            yield Publish(SourceResumed(source=plan.source))
        else:
            yield Publish(plan.decoded(notice))


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
def _gaps_told(plan: _Plan, gaps: _Gaps) -> "EffectGenerator[BrokerUnreachable | None]":
    """Tell every marked gap, oldest first, and unmark each one told. Answers ``None`` when all were told, or the
    broker's refusal (the rest stay marked). Only one teller at a time (``gaps._mut_telling``)."""
    gaps._mut_telling = True
    refused: BrokerUnreachable | None = None
    while gaps._mut_channels and refused is None:
        receivers = yield Announce(gaps._mut_channels[0], GAP_NOTICE, plan.source)
        if isinstance(receivers, BrokerUnreachable):
            refused = receivers
        else:
            gaps._mut_channels = gaps._mut_channels[1:]
    gaps._mut_telling = False
    waiting, gaps._mut_after_telling = gaps._mut_after_telling, ()
    for done in waiting:
        yield CompletePromise(done, None)
    return refused


@do
def _gaps_told_on_return(plan: _Plan, gaps: _Gaps) -> "EffectGenerator[None]":
    """The task waiting for the broker's return: wait for ``AwaitBrokerBack``, tell the gaps, and wait again if
    the broker is away again. Ends when no gap is left (a later mark starts a new task); the next ``Publish``
    that told every gap stops it."""
    while gaps._mut_channels:
        yield AwaitBrokerBack()
        if not gaps._mut_telling:
            yield _gaps_told(plan, gaps)
    gaps._mut_waiter = None


@do
def _marked(plan: _Plan, gaps: _Gaps, channel: str, detail: str) -> "EffectGenerator[NoticeGapMarked]":
    """Mark ``channel`` and make sure one task waits for the broker's return."""
    gaps.mark(channel)
    if gaps._mut_waiter is None:
        gaps._mut_waiter = yield Spawn(_gaps_told_on_return(plan, gaps))
    return NoticeGapMarked(channel=channel, detail=detail)


@do
def _sent(plan: _Plan, gaps: _Gaps, route: NoticeRoute[object], event: object) -> "EffectGenerator[PublishAnswer]":
    """Send one routed event: tell the gaps first if there are any, then the event. What the broker cannot take
    is marked (``MarkGap``) or dropped (``Drop``)."""
    channel = route.channel(event)
    match route.when_unsent:
        case Drop():
            receivers = yield Announce(channel, route.wire_name, route.encode(event))
            if isinstance(receivers, BrokerUnreachable):
                return NoticeDropped(detail=receivers.detail)
            return NoticeSent(receivers)
        case MarkGap():
            while gaps._mut_telling:
                # Another task is telling the gaps: wait until it is done, so nothing overtakes the gap.
                done: Promise[None] = yield CreatePromise()
                gaps._mut_after_telling = (*gaps._mut_after_telling, done)
                yield Wait(done.future)
            if gaps._mut_channels:
                refused = yield _gaps_told(plan, gaps)
                if refused is not None:
                    return (yield _marked(plan, gaps, channel, refused.detail))
                failed = yield _stopped_tasks(gaps.running())
                gaps._mut_waiter = None
                gaps._mut_failure = gaps._mut_failure if gaps._mut_failure is not None else failed
            receivers = yield Announce(channel, route.wire_name, route.encode(event))
            if isinstance(receivers, BrokerUnreachable):
                return (yield _marked(plan, gaps, channel, receivers.detail))
            return NoticeSent(receivers)


def _body_handler(plan: _Plan, gaps: _Gaps) -> "ProgramHandler":
    """The handler around the body: sends routed ``Publish`` (marking what the broker cannot take) and passes
    ``WaitForEvent`` outward with ``SourceFailed`` added, so that a failure of this wrapper's source ends the
    body's wait with its error."""

    @do
    def handler(effect: PublishEffect | WaitForEventEffect, k: K) -> "EffectGenerator[object]":
        """Translate one effect of the body (see ``_body_handler``)."""
        if isinstance(effect, PublishEffect):
            route = plan.route_of(effect.event)
            if route is None:
                yield Pass(effect, k)
                return None
            answer = yield _sent(plan, gaps, route, effect.event)
            return (yield Resume(k, answer))
        wanted = effect.event_types
        came = yield WaitForEventEffect((*wanted, SourceFailed))
        # A failure of another source on the same bus is not this body's unless it asked for failures.
        while isinstance(came, SourceFailed) and came.source != plan.source and SourceFailed not in wanted:
            came = yield WaitForEventEffect((*wanted, SourceFailed))
        if isinstance(came, SourceFailed) and came.source == plan.source:
            return (yield ResumeThrow(k, came.error))
        return (yield Resume(k, came))

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
                gaps.mark(channel)
    if gaps._mut_channels:
        refused = yield _gaps_told(plan, gaps)
        if refused is not None:
            gaps._mut_waiter = yield Spawn(_gaps_told_on_return(plan, gaps))


@do
def _run(plan: _Plan, body: "Program[_T]") -> "EffectGenerator[_T]":
    """Begin the subscription, tell the start once it is confirmed, tell the start gaps, run the source task beside
    the body (the body stays in this task, so a cancel from outside reaches it), and stop the source and the task
    waiting for the return when the body ends. A wrapper that only sends has no source: nothing to subscribe,
    nothing to tell but its start gaps."""
    gaps = _Gaps()
    source: tuple[Task[object], ...] = ()
    if plan.channels:
        subscription = yield _first_subscription(plan)
        yield Publish(SourceStarted(source=plan.source))
        reader: Task[object] = yield Spawn(_failure_announced(plan.source, _read_notices(plan, subscription)))
        source = (reader,)
    yield _started_gaps_told(plan, gaps)
    # The stop runs on an exception and on the normal end, not in ``finally`` (see ``_came_back_within``).
    try:
        answer = yield _body_handler(plan, gaps)(body)
    except Exception:
        yield _stopped_tasks((*source, *gaps.running()))
        raise
    failed = yield _stopped_tasks((*source, *gaps.running()))
    failed = gaps._mut_failure if gaps._mut_failure is not None else failed
    if failed is not None:
        # A task fell while the body did not wait: the failure is not dropped.
        raise failed
    return answer


class _BodyWrapper(partial["Program[object]"]):
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
# ``NextAnnouncement``, ``CloseSubscription``), sending a routed event (``Announce``), what the source task publishes
# on the bus (``PublishEffect`` — the received events and ``SourceStarted`` / ``SourceStalled`` / ``SourceResumed`` /
# ``SourceFailed``), getting past an outage (``AwaitBrokerBack``, ``GetTimeEffect``, ``WaitWithinEffect``, the watcher
# task's ``CreatePromise`` / ``CompletePromise``), and running and stopping its tasks (``Spawn``, ``Cancel``, ``Wait``).
# The body's own ``WaitForEvent`` and its unrouted ``Publish`` go outward as the body's effects and are not counted.
# ``tests/test_notice_events_closure.py`` checks that this is what the wrapper really performs.
NOTICE_EVENTS_EFFECTS = (
    SubscribeChannels,
    NextAnnouncement,
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
