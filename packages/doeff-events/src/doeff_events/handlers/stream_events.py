"""Events between processes: ``stream_events_handler`` wraps a business program so that its ``Publish`` and
``WaitForEvent`` reach other processes through an event broker (agora-redesign #3850).

The business program keeps its shape — ``Publish(event)``, ``WaitForEvent(types...)`` and, for one read of a
range, ``EventsBetween`` — and never names a stream, a group, a consumer or an acknowledgement. Those live in
the arguments of this factory: the consumer's own name and the routes (one ``EventRoute`` per event type).

Order of handlers (outer → inner)::

    subscribed_event_handler(bus, subscriber, types)   # the one that answers WaitForEvent
      → clock handler (GetTime / WaitWithin)           # only used while the broker is unreachable
        → memory_stream_handler(broker) | redis_stream_handler(url)  (+ whoever answers AwaitBrokerBack)
          → stream_events_handler(consumer, routes, patience_seconds)
            → business program

What the wrapper does:

- Sending. ``Publish(event)`` of a routed type becomes ``AppendEntry`` (an acknowledged stream) or ``Announce``
  (a notice). If the broker is unreachable the ``Publish`` raises ``EventNotPublished`` in the program. An
  unrouted type goes on to the outer handler (the bus inside the process).
- Receiving. Before the body runs, the wrapper makes its consumer group on every stream it reads (the group's
  name is the consumer's name: every reader has its own group and reads every event), subscribes its channels,
  claims what an earlier run of the same consumer left unacknowledged, and publishes ``SourceStarted`` on the
  bus. Then one task per kind waits in the broker's blocking read and publishes each received event on the bus
  (the body receives them one at a time with ``WaitForEvent`` — the wrapper does not answer it, it only adds
  ``SourceFailed`` to the wait like doeff-records' signal source).
- Acknowledging. Implicit: when the body comes back to ``WaitForEvent`` (or ends without an exception) the
  event it was given last is acknowledged. An event the body was processing when it fell stays pending and is
  given again to the next run of the same consumer.
- Broker outages. A source task that gets ``BrokerUnreachable`` publishes ``SourceStalled`` and waits for
  ``AwaitBrokerBack`` up to ``patience_seconds`` (one ``WaitWithin`` — no retry on a timer). When the broker is
  back it publishes ``SourceResumed`` once, claims again what was pending for it and reads on. Past the
  patience the source fails with ``StreamSourceUnreachable`` (``SourceFailed`` reaches the body).
- Cut heads. At the start and after every return of the broker, if the stream's head was cut past the place
  the group had reached (``head_was_cut``) or a pending entry is gone, the wrapper publishes ``SourceGap``.

The streams a wrapper reads are fixed when it is built (``_Plan.streams`` is the one place that holds them).
"""

from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from functools import partial
from typing import TYPE_CHECKING, Final, Generic, TypeVar, final

from doeff_core_effects.scheduler import (
    Cancel,
    CompletePromise,
    CreatePromise,
    FailPromise,
    Promise,
    Spawn,
    Task,
    TaskCancelledError,
    Wait,
)
from doeff_time import GetTime, WaitWithin

from doeff import K, Pass, Program, Resume, ResumeThrow, do
from doeff import handler as _program_handler
from doeff_events.effects.events import (
    Publish,
    PublishEffect,
    SourceFailed,
    SourceGap,
    SourceResumed,
    SourceStalled,
    SourceStarted,
    WaitForEventEffect,
)
from doeff_events.effects.streams import (
    AckEntry,
    Announce,
    Announcement,
    AppendEntry,
    AwaitBrokerBack,
    BrokerUnreachable,
    ChannelSubscription,
    ClaimedEntries,
    ClaimPending,
    EnsureGroup,
    EntryRange,
    EventsBetween,
    EventsRead,
    GroupPosition,
    NextAnnouncement,
    RangeCut,
    ReadEntryRange,
    ReadGroup,
    ReadGroupPosition,
    StreamEntry,
    SubscribeChannels,
    head_was_cut,
)

if TYPE_CHECKING:
    from doeff import EffectBase, EffectGenerator
    from doeff.program import ProgramHandler

_E = TypeVar("_E")
_T = TypeVar("_T")

NO_POSITION: Final = ""
"""The position ``EventRoute.decode`` receives for a notice: a notice has no place in a stream."""


class RouteKind(Enum):
    """How the events of a route travel."""

    ACKED_STREAM = "acked-stream"
    """A stream with consumer groups: an event stays pending until its reader finished with it."""
    NOTICE = "notice"
    """Pub/Sub: an event reaches whoever is subscribed right now and nobody else."""


@dataclass(frozen=True)
class EventRoute(Generic[_E]):
    """How one event type travels through the broker (one row of the route table).

    ``event_type`` = the type a program publishes and waits for.
    ``wire_name`` = the name that tells this type apart on the wire (several types share one stream).
    ``kind`` = an acknowledged stream or a notice.
    ``place`` = the stream or channel an event is sent to, decided from the event's value.
    ``encode`` = event → text (JSON by convention).
    ``decode`` = (position, text) → event. ``position`` is the entry's position in its stream, so the event can
    carry it (``NO_POSITION`` for a notice).
    ``reads`` = the streams or channels this side receives the type from; ``()`` = this side only sends.
    ``maxlen`` = the length the stream is cut to on every send (about — Redis ``MAXLEN ~``); ``None`` = no cut.
    """

    event_type: type[_E]
    wire_name: str
    kind: RouteKind
    place: Callable[[_E], str]
    encode: Callable[[_E], str]
    decode: Callable[[str, str], _E]
    reads: tuple[str, ...] = ()
    maxlen: int | None = None


class EventNotPublished(RuntimeError):
    """``Publish`` of a routed event did not reach the broker (it is unreachable). The event was not sent."""


class StreamSourceUnreachable(RuntimeError):
    """The broker did not come back within the patience the handler was built with — the process falls and its
    restart starts over."""


@dataclass(frozen=True)
class _Plan:
    """A checked set of arguments of one wrapper: who reads (``consumer`` — also the group and the source name),
    the routes, the streams and channels it reads (first-seen order, no repeats) and its patience."""

    consumer: str
    routes: tuple[EventRoute[object], ...]
    streams: tuple[str, ...]
    channels: tuple[str, ...]
    patience_seconds: float

    def route_of(self, event: object) -> EventRoute[object] | None:
        """The route an event is sent by (by its exact type), or ``None`` for an event that stays in the process."""
        return next((route for route in self.routes if type(event) is route.event_type), None)

    def route_named(self, wire_name: str, kind: RouteKind, place: str) -> EventRoute[object] | None:
        """The route a received entry or notice is decoded by, or ``None`` when this reader has no use for it."""
        return next(
            (
                route
                for route in self.routes
                if route.wire_name == wire_name and route.kind is kind and place in route.reads
            ),
            None,
        )


def _first_seen(names: tuple[str, ...]) -> tuple[str, ...]:
    """Names in first-seen order without repeats."""
    return tuple(dict.fromkeys(names))


def _checked_plan(consumer: str, routes: tuple[EventRoute[object], ...], patience_seconds: float) -> _Plan:
    """Check the factory's arguments once, when the handler is built, and name what is wrong."""
    if not isinstance(consumer, str) or not consumer:
        raise ValueError("stream_events_handler: consumer must be a non-empty string")
    if isinstance(patience_seconds, bool) or not isinstance(patience_seconds, int | float) or patience_seconds < 0:
        raise ValueError(f"stream_events_handler: patience_seconds must be a number >= 0, got {patience_seconds!r}")
    for route in routes:
        if not isinstance(route, EventRoute):
            raise TypeError(f"stream_events_handler: routes must be EventRoute values, got {route!r}")
        if not isinstance(route.kind, RouteKind):
            raise TypeError(f"route {route.wire_name!r}: kind must be a RouteKind, got {route.kind!r}")
        if route.maxlen is not None and route.kind is not RouteKind.ACKED_STREAM:
            raise ValueError(f"route {route.wire_name!r}: only an acknowledged stream has a length limit")
        if route.kind is RouteKind.ACKED_STREAM and is_dataclass(route.event_type):
            if any(field.name == "keys" for field in fields(route.event_type)):
                # The subscriber queue merges queued signals that carry ``keys`` into one, so one received event
                # would no longer stand for one entry to acknowledge.
                raise ValueError(
                    f"route {route.wire_name!r}: an acknowledged event type cannot have a field named 'keys'"
                )
    types = tuple(route.event_type for route in routes)
    names = tuple((route.wire_name, route.kind) for route in routes)
    if len(set(types)) != len(types) or len(set(names)) != len(names):
        raise ValueError(f"stream_events_handler({consumer!r}): an event type or a wire name is routed twice")
    return _Plan(
        consumer=consumer,
        routes=routes,
        streams=_first_seen(
            tuple(name for route in routes if route.kind is RouteKind.ACKED_STREAM for name in route.reads)
        ),
        channels=_first_seen(tuple(name for route in routes if route.kind is RouteKind.NOTICE for name in route.reads)),
        patience_seconds=float(patience_seconds),
    )


@final
class _Held:
    """An entry read for this consumer and not acknowledged yet, with the event decoded from it. The event is kept
    as the object itself and matched by identity when the body receives it."""

    __slots__ = ("entry", "event")

    def __init__(self, entry: StreamEntry, event: object) -> None:
        """Pair the entry with the event published for it."""
        self.entry: Final = entry
        self.event: Final = event


@final
class _Link:
    """The state one running wrapper keeps about its connection and its deliveries (never seen by a program).

    ``held`` = entries published on the bus and not yet acknowledged; ``current`` = the one the body was given
    last (acknowledged when the body waits again); ``owed`` = entries to acknowledge as soon as the broker
    answers; ``riding`` = the promise of the task that is waiting for the broker's return (other tasks wait on
    it); ``started`` = whether ``SourceStarted`` was published; ``returns`` = how many times the broker came back.
    """

    __slots__ = ("_mut_current", "_mut_held", "_mut_owed", "returns", "riding", "started")

    def __init__(self) -> None:
        """Start with nothing delivered and the broker presumed reachable."""
        self._mut_held: tuple[_Held, ...] = ()
        self._mut_current: _Held | None = None
        self._mut_owed: tuple[StreamEntry, ...] = ()
        self.riding: Promise[bool] | None = None
        self.started = False
        self.returns = 0

    def holds(self, entry: StreamEntry) -> bool:
        """Whether this run already has the entry (published or being processed) — a claim must not give it twice."""
        return any(
            known.stream == entry.stream and known.position == entry.position
            for known in (*(held.entry for held in self._mut_held), *self._mut_owed)
        )

    def hold(self, entry: StreamEntry, event: object) -> None:
        """Remember an entry whose event is about to be published on the bus."""
        self._mut_held = (*self._mut_held, _Held(entry, event))

    def finish_current(self) -> None:
        """The body is done with the event it was given last: its entry is now owed an acknowledgement."""
        if self._mut_current is not None:
            done = self._mut_current
            self._mut_held = tuple(held for held in self._mut_held if held is not done)
            self._mut_owed = (*self._mut_owed, done.entry)
            self._mut_current = None

    def give(self, event: object) -> None:
        """The body is being given ``event``: if it came from a stream, it is the one to acknowledge next."""
        self._mut_current = next((held for held in self._mut_held if held.event is event), None)


@do
def _back_announced(promise: Promise[bool]) -> "EffectGenerator[None]":
    """The watcher task: wait for the broker's return and complete ``promise`` (the rider waits on the promise
    with a deadline; the watcher itself has none)."""
    yield AwaitBrokerBack()
    yield CompletePromise(promise, True)


@do
def _stop_task(task: Task[object]) -> "EffectGenerator[Exception | None]":
    """Stop a task and wait until it is unwound. Answers the error of a task that had already failed (``None`` for
    one that ended by this cancel or by itself) — the caller decides whether to raise it, after it stopped the rest."""
    yield Cancel(task)
    try:
        yield Wait(task)
    except TaskCancelledError:
        return None
    except Exception as error:
        return error
    return None


@do
def _stop_all(tasks: tuple[Task[object], ...]) -> "EffectGenerator[Exception | None]":
    """Stop every task and answer the first error one of them had failed with (``None`` when none had)."""
    first: Exception | None = None
    for task in tasks:
        failed = yield _stop_task(task)
        first = first or failed
    return first


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
def _ride_out(plan: _Plan, link: _Link, first: BrokerUnreachable, asked_at: int) -> "EffectGenerator[None]":
    """Get past a broker outage without falling: the first task that sees it publishes ``SourceStalled`` and waits
    for the return up to the patience, then publishes ``SourceResumed`` (once — other tasks of the same wrapper
    wait for this task's word). Past the patience it raises ``StreamSourceUnreachable`` for all of them.

    ``asked_at`` = ``link.returns`` when the failed call was made. If the broker came back since then, the failure
    belongs to an outage that was already told (each connection finds out on its own that it broke): nothing is
    told again and the caller just asks again."""
    if link.returns != asked_at:
        return None
    if link.riding is not None:
        yield Wait(link.riding.future)
        return None
    riding: Promise[bool] = yield CreatePromise()
    link.riding = riding
    since: datetime = yield GetTime()
    yield Publish(SourceStalled(source=plan.consumer, detail=first.detail, since=since))
    back = yield _came_back_within(plan.patience_seconds)
    link.riding = None
    if not back:
        error = StreamSourceUnreachable(
            f"consumer {plan.consumer!r}: the event broker did not come back within {plan.patience_seconds} s: "
            f"{first.detail}"
        )
        yield FailPromise(riding, error)
        raise error
    link.returns += 1
    if link.started:
        yield Publish(SourceResumed(source=plan.consumer))
    yield CompletePromise(riding, True)


@do
def _reached(plan: _Plan, link: _Link, ask: "EffectBase[_T | BrokerUnreachable]") -> "EffectGenerator[_T]":
    """Ask the broker and, while the answer is ``BrokerUnreachable``, wait for its return and ask again (each
    repeat follows an answer of ``AwaitBrokerBack`` — not a timer)."""
    asked_at = link.returns
    answer = yield ask
    while isinstance(answer, BrokerUnreachable):
        yield _ride_out(plan, link, answer, asked_at)
        asked_at = link.returns
        answer = yield ask
    return answer


@do
def _publish_entries(plan: _Plan, link: _Link, entries: tuple[StreamEntry, ...]) -> "EffectGenerator[None]":
    """Publish on the bus the events of entries read for this consumer, one at a time. An entry this reader has
    no route for (another reader's event type in a shared stream) is acknowledged without being delivered."""
    for entry in entries:
        if link.holds(entry):
            continue
        route = plan.route_named(entry.name, RouteKind.ACKED_STREAM, entry.stream)
        if route is None:
            link._mut_owed = (*link._mut_owed, entry)
            continue
        event = route.decode(entry.position, entry.body)
        link.hold(entry, event)
        yield Publish(event)


@do
def _settle(plan: _Plan, link: _Link) -> "EffectGenerator[None]":
    """Acknowledge every entry the body is done with. If the broker is unreachable the rest stays owed and is
    tried at the body's next wait (an entry never acknowledged is given again to the next run — at least once)."""
    while link._mut_owed:
        entry = link._mut_owed[0]
        answer = yield AckEntry(entry.stream, plan.consumer, entry.position)
        if isinstance(answer, BrokerUnreachable):
            return None
        link._mut_owed = link._mut_owed[1:]


@do
def _catch_up(plan: _Plan, link: _Link) -> "EffectGenerator[None]":
    """What the reader does once when it starts and once every time the broker comes back: make sure its groups
    exist, tell a cut head (``SourceGap``), and take again the entries that are pending for it but that this run
    does not have (left by an earlier run, or lost on the way when the connection broke)."""
    cut = False
    pending: tuple[StreamEntry, ...] = ()
    for stream in plan.streams:
        yield _reached(plan, link, EnsureGroup(stream, plan.consumer))
        position: GroupPosition = yield _reached(plan, link, ReadGroupPosition(stream, plan.consumer))
        claimed: ClaimedEntries = yield _reached(plan, link, ClaimPending(stream, plan.consumer, plan.consumer))
        cut = cut or head_was_cut(position) or bool(claimed.lost)
        pending = (*pending, *claimed.entries)
    if cut:
        yield Publish(SourceGap(source=plan.consumer))
    yield _publish_entries(plan, link, pending)


@do
def _read_streams(plan: _Plan, link: _Link) -> "EffectGenerator[None]":
    """The stream source task: wait in the broker's blocking read and publish what arrives. It ends only by
    ``Cancel`` (the read has no time limit — the cancel ends it) or by failing."""
    returns = link.returns
    while True:
        asked_at = link.returns
        entries = yield ReadGroup(plan.streams, plan.consumer, plan.consumer)
        if isinstance(entries, BrokerUnreachable):
            yield _ride_out(plan, link, entries, asked_at)
        if link.returns != returns:
            returns = link.returns
            yield _catch_up(plan, link)
        if not isinstance(entries, BrokerUnreachable):
            yield _publish_entries(plan, link, entries)


@do
def _read_channels(plan: _Plan, link: _Link, subscription: ChannelSubscription) -> "EffectGenerator[None]":
    """The notice source task: wait for the next notice and publish its event. Ends like ``_read_streams``."""
    while True:
        notice: Announcement = yield _reached(plan, link, NextAnnouncement(subscription))
        route = plan.route_named(notice.name, RouteKind.NOTICE, notice.channel)
        if route is not None:
            yield Publish(route.decode(NO_POSITION, notice.body))


@do
def _failure_announced(source: str, program: "Program[None]") -> "EffectGenerator[None]":
    """The body of a source task: if it falls (not by cancel), publish ``SourceFailed`` with its error first, so
    the body's wait receives the failure as an event instead of racing the task."""
    try:
        yield program
    except TaskCancelledError:
        raise
    except Exception as error:
        yield Publish(SourceFailed(source=source, error=error))
        raise


@do
def _send(plan: _Plan, route: EventRoute[object], event: object) -> "EffectGenerator[EventNotPublished | None]":
    """Send one routed event to the broker; answer the ``EventNotPublished`` to raise in the program when the
    broker is unreachable (``None`` = sent)."""
    place = route.place(event)
    body = route.encode(event)
    if route.kind is RouteKind.ACKED_STREAM:
        answer = yield AppendEntry(place, route.wire_name, body, route.maxlen)
    else:
        answer = yield Announce(place, route.wire_name, body)
    if isinstance(answer, BrokerUnreachable):
        return EventNotPublished(
            f"consumer {plan.consumer!r}: {type(event).__name__} was not published to {place!r}: {answer.detail}"
        )
    return None


@do
def _between(
    plan: _Plan, link: _Link, effect: EventsBetween[object]
) -> "EffectGenerator[EventsRead | RangeCut | ValueError]":
    """Read the range of ``effect`` once from the stream its type's route reads, and decode the routed entries.
    A type without such a route is answered as the ``ValueError`` to raise in the program."""
    route = next((known for known in plan.routes if known.event_type is effect.event_type), None)
    if route is None or route.kind is not RouteKind.ACKED_STREAM or len(route.reads) != 1:
        return ValueError(
            f"consumer {plan.consumer!r}: EventsBetween({effect.event_type.__name__}) needs a route of an "
            "acknowledged stream that reads exactly one stream"
        )
    stream = route.reads[0]
    found: EntryRange = yield _reached(plan, link, ReadEntryRange(stream, effect.after, effect.until))
    decoded = tuple(
        (plan.route_named(entry.name, RouteKind.ACKED_STREAM, stream), entry) for entry in found.entries
    )
    events = tuple(known.decode(entry.position, entry.body) for known, entry in decoded if known is not None)
    return EventsRead(events) if found.complete else RangeCut(events)


def _body_handler(plan: _Plan, link: _Link) -> "ProgramHandler":
    """The handler around the body: sends routed ``Publish``, answers ``EventsBetween``, and passes ``WaitForEvent``
    outward with ``SourceFailed`` added — acknowledging, on the way, the event the body was given before."""

    @do
    def handler(effect: PublishEffect | WaitForEventEffect | EventsBetween, k: K) -> "EffectGenerator[object]":
        """Translate one effect of the body (see ``_body_handler``)."""
        match effect:
            case PublishEffect(event=event):
                route = plan.route_of(event)
                if route is None:
                    yield Pass(effect, k)
                    return None
                unsent: EventNotPublished | None = yield _send(plan, route, event)
                if unsent is not None:
                    return (yield ResumeThrow(k, unsent))
                return (yield Resume(k, None))
            case EventsBetween():
                answer = yield _between(plan, link, effect)
                if isinstance(answer, ValueError):
                    return (yield ResumeThrow(k, answer))
                return (yield Resume(k, answer))
            case WaitForEventEffect(event_types=wanted):
                # Coming back to the wait is the body's word that it finished the event it was given before.
                link.finish_current()
                yield _settle(plan, link)
                wants_failures = SourceFailed in wanted
                while True:
                    came = yield WaitForEventEffect((*wanted, SourceFailed))
                    if not isinstance(came, SourceFailed) or came.source == plan.consumer or wants_failures:
                        break
                if isinstance(came, SourceFailed) and came.source == plan.consumer:
                    return (yield ResumeThrow(k, came.error))
                link.give(came)
                return (yield Resume(k, came))
            case _:
                yield Pass(effect, k)

    return _program_handler(handler)


@do
def _run(plan: _Plan, body: "Program[_T]") -> "EffectGenerator[_T]":
    """Begin the subscription, tell the start, run the source tasks beside the body (the body stays in this task,
    so a cancel from outside reaches it), and stop the sources when the body ends."""
    link = _Link()
    subscription: ChannelSubscription | None = None
    if plan.channels:
        subscription = yield _reached(plan, link, SubscribeChannels(plan.channels))
    # The start is told before the claimed entries are published: a body catches up from its records first.
    for stream in plan.streams:
        yield _reached(plan, link, EnsureGroup(stream, plan.consumer))
    if plan.streams or plan.channels:
        link.started = True
        yield Publish(SourceStarted(source=plan.consumer))
    yield _catch_up(plan, link)
    sources: tuple[Task[object], ...] = ()
    if plan.streams:
        sources = (*sources, (yield Spawn(_failure_announced(plan.consumer, _read_streams(plan, link)))))
    if subscription is not None:
        reading = _read_channels(plan, link, subscription)
        sources = (*sources, (yield Spawn(_failure_announced(plan.consumer, reading))))
    try:
        answer = yield _body_handler(plan, link)(body)
    except Exception:
        yield _stop_all(sources)
        raise
    # The body ended without an exception: it finished the last event it was given.
    link.finish_current()
    yield _settle(plan, link)
    failed = yield _stop_all(sources)
    if failed is not None:
        # A source that fell while the body did not wait: the failure is not dropped.
        raise failed
    return answer


class _BodyWrapper(partial["Program[object]"]):
    """A function that wraps a body, marked so that Hy's ``with-handlers`` applies it to the body as it is."""

    _doeff_is_handler_fn = True


def stream_events_handler(
    consumer: str, routes: tuple[EventRoute[object], ...], patience_seconds: float
) -> "ProgramHandler":
    """Build the wrapper that carries a body's events through the event broker.

    ``consumer`` = this reader's own name: its consumer name, the name of its consumer group (every reader has
    its own group and reads every event; a restart under the same name takes over what was left unacknowledged)
    and the ``source`` of the ``SourceStarted`` / ``SourceStalled`` / ``SourceResumed`` / ``SourceGap`` /
    ``SourceFailed`` it publishes.
    ``routes`` = one ``EventRoute`` per event type that travels through the broker.
    ``patience_seconds`` = how long a source waits for an unreachable broker before it fails (no default: the
    composition chooses it in one place).

    The arguments are checked here; the subscription begins at the head of the wrapped body, before the body's
    first effect.
    """
    return _BodyWrapper(_run, _checked_plan(consumer, routes, patience_seconds))
