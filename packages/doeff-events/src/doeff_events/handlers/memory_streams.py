"""In-memory event broker: the lower layer of ``doeff_events.effects.streams`` inside one process.

``MemoryBroker`` holds streams with consumer groups (one entry goes to one consumer of a group, stays pending
until acknowledged, can be claimed) and channels whose notices reach only current subscribers — the meaning the
Redis handler has, without a server. The emulated environment and tests share one broker between the handlers of
the programs that stand for separate processes (agora-redesign #3850).

``memory_stream_handler(broker)`` answers the lower-layer effects from that broker. A blocked read waits on a
scheduler promise (``CreatePromise`` / ``Wait``), like ``subscribed_event_handler``, so a virtual clock and the
scheduler's dead-end detection keep working.

A test can take the broker away and bring it back with ``cut_broker`` / ``restore_broker``: while it is cut every
operation answers ``BrokerUnreachable`` and ``AwaitBrokerBack`` waits for the restore.

Positions are ``<n>-0`` with ``n`` counted per stream from 1. A length limit cuts exactly to the limit.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Wait

from doeff import K, Pass, Resume, do
from doeff import handler as _program_handler
from doeff_events.effects.streams import (
    ORIGIN,
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
    GroupPosition,
    NextAnnouncement,
    ReadEntryRange,
    ReadGroup,
    ReadGroupPosition,
    StreamEntry,
    SubscribeChannels,
    position_key,
)

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler


@dataclass(frozen=True)
class _Appended:
    """What appending an entry gave: its position, and the blocked readers to wake with it."""

    position: str
    wakes: "tuple[_Wake, ...]"


@dataclass(frozen=True)
class _Wake:
    """A waiting task to wake: complete ``promise`` with ``value`` (the handler does it — the broker has no effects)."""

    promise: Promise[object]
    value: object


@final
class _Pending:
    """One entry given to a group and not acknowledged yet, and the consumer that holds it."""

    __slots__ = ("consumer", "entry")

    def __init__(self, entry: StreamEntry, consumer: str) -> None:
        """Record that ``consumer`` holds ``entry``."""
        self.entry: Final = entry
        self.consumer = consumer


@final
class _Group:
    """One consumer group of a stream: how far it was given entries and what is pending."""

    __slots__ = ("last_delivered", "pending")

    def __init__(self, last_delivered: str) -> None:
        """Start the group after ``last_delivered``."""
        self.last_delivered = last_delivered
        self.pending: tuple[_Pending, ...] = ()


@final
class _Stream:
    """One stream: its remaining entries in order, the newest id it ever had, and its groups by name."""

    __slots__ = ("added", "entries", "groups", "last_generated")

    def __init__(self) -> None:
        """Start an empty stream with no group."""
        self.entries: tuple[StreamEntry, ...] = ()
        self.groups: Final[dict[str, _Group]] = {}
        self.added = 0
        self.last_generated = ORIGIN


@final
class _Reader:
    """A task blocked in ``ReadGroup``. Compared by identity."""

    __slots__ = ("consumer", "group", "promise", "streams")

    def __init__(self, streams: tuple[str, ...], group: str, consumer: str, promise: Promise[object]) -> None:
        """Remember what the task waits for and the promise that wakes it."""
        self.streams: Final = streams
        self.group: Final = group
        self.consumer: Final = consumer
        self.promise: Final = promise


@final
class _Listener:
    """What the broker keeps for one ``ChannelSubscription``: notices not yet taken, and the waiting task if any."""

    __slots__ = ("queued", "subscription", "waiter")

    def __init__(self, subscription: ChannelSubscription) -> None:
        """Start listening for ``subscription`` with nothing queued."""
        self.subscription: Final = subscription
        self.queued: tuple[Announcement, ...] = ()
        self.waiter: Promise[object] | None = None


class NoSuchGroup(LookupError):
    """A read, an acknowledgement or a claim named a consumer group that was never made (``EnsureGroup`` first)."""


@final
class MemoryBroker:
    """The state of an in-memory broker. Programs never see it; ``memory_stream_handler`` is its only user,
    and ``cut_broker`` / ``restore_broker`` are the test controls."""

    __slots__ = ("_mut_back_waiters", "_mut_down", "_mut_listeners", "_mut_readers", "_streams")

    def __init__(self) -> None:
        """Start a reachable broker with no stream and no subscriber."""
        self._streams: Final[dict[str, _Stream]] = {}
        self._mut_readers: tuple[_Reader, ...] = ()
        self._mut_listeners: tuple[_Listener, ...] = ()
        self._mut_back_waiters: tuple[Promise[object], ...] = ()
        self._mut_down: BrokerUnreachable | None = None

    @property
    def down(self) -> BrokerUnreachable | None:
        """The unreachable answer every operation gives while the broker is cut, or ``None`` while it is up."""
        return self._mut_down

    def _group(self, stream: str, group: str) -> _Group:
        """Find a group for a read, an acknowledgement or a claim — a missing group is a wiring error."""
        found = self._streams.get(stream)
        if found is None or group not in found.groups:
            raise NoSuchGroup(f"stream {stream!r} has no consumer group {group!r}")
        return found.groups[group]

    def append(self, effect: AppendEntry) -> _Appended:
        """Add an entry (cutting the head to ``maxlen``) and give it to blocked readers, one per group."""
        stream = self._streams.setdefault(effect.stream, _Stream())
        stream.added += 1
        position = f"{stream.added}-0"
        stream.last_generated = position
        entries = (*stream.entries, StreamEntry(effect.stream, position, effect.name, effect.body))
        stream.entries = entries if effect.maxlen is None else entries[-effect.maxlen :]
        return _Appended(position, self._wake_readers())

    def ensure_group(self, effect: EnsureGroup) -> None:
        """Make the group after the stream's newest entry unless it exists."""
        stream = self._streams.setdefault(effect.stream, _Stream())
        if effect.group not in stream.groups:
            stream.groups[effect.group] = _Group(stream.last_generated)

    def take(self, streams: tuple[str, ...], group: str, consumer: str) -> tuple[StreamEntry, ...]:
        """Give ``consumer`` every entry of ``streams`` its group was not given yet; they become pending."""
        return tuple(entry for name in streams for entry in self._take_one(name, group, consumer))

    def _take_one(self, stream: str, group: str, consumer: str) -> tuple[StreamEntry, ...]:
        """Give ``consumer`` the entries of one stream its group was not given yet."""
        state = self._group(stream, group)
        after = position_key(state.last_delivered)
        fresh = tuple(entry for entry in self._streams[stream].entries if position_key(entry.position) > after)
        if fresh:
            state.pending = (*state.pending, *(_Pending(entry, consumer) for entry in fresh))
            state.last_delivered = fresh[-1].position
        return fresh

    def _wake_readers(self) -> tuple[_Wake, ...]:
        """Give new entries to the blocked readers in the order they began to wait (an entry reaches one reader
        of a group: the first one takes it, so the next of the same group finds nothing)."""
        offered = tuple((reader, self.take(reader.streams, reader.group, reader.consumer)) for reader in self._mut_readers)
        self._mut_readers = tuple(reader for reader, taken in offered if not taken)
        return tuple(_Wake(reader.promise, taken) for reader, taken in offered if taken)

    def add_reader(self, effect: ReadGroup, promise: Promise[object]) -> _Reader:
        """Register a blocked ``ReadGroup``."""
        reader = _Reader(effect.streams, effect.group, effect.consumer, promise)
        self._mut_readers = (*self._mut_readers, reader)
        return reader

    def remove_reader(self, reader: _Reader) -> None:
        """Forget a blocked reader (woken or cancelled). Unknown readers are ignored."""
        self._mut_readers = tuple(known for known in self._mut_readers if known is not reader)

    def ack(self, effect: AckEntry) -> None:
        """Take the entry out of the group's pending list."""
        state = self._group(effect.stream, effect.group)
        state.pending = tuple(held for held in state.pending if held.entry.position != effect.position)

    def claim(self, effect: ClaimPending) -> ClaimedEntries:
        """Hand every pending entry of the group to the claiming consumer; pending entries cut from the stream
        leave the pending list and are reported as lost."""
        state = self._group(effect.stream, effect.group)
        remaining = self._streams[effect.stream].entries
        kept = tuple(held for held in state.pending if any(entry is held.entry for entry in remaining))
        lost = tuple(held.entry.position for held in state.pending if not any(held is keep for keep in kept))
        state.pending = kept
        for held in kept:
            held.consumer = effect.consumer
        return ClaimedEntries(tuple(held.entry for held in kept), lost)

    def position(self, effect: ReadGroupPosition) -> GroupPosition:
        """Answer where the group stands in its stream."""
        state = self._group(effect.stream, effect.group)
        stream = self._streams[effect.stream]
        first = stream.entries[0].position if stream.entries else None
        return GroupPosition(state.last_delivered, first, stream.last_generated)

    def range(self, effect: ReadEntryRange) -> EntryRange:
        """Answer the entries after ``after`` up to ``until``; complete when the entry at ``after`` is still
        there or the stream never lost an entry (the rule the Redis handler can also keep)."""
        stream = self._streams.get(effect.stream)
        if stream is None:
            return EntryRange((), complete=True)
        after, until = position_key(effect.after), position_key(effect.until)
        entries = tuple(entry for entry in stream.entries if after < position_key(entry.position) <= until)
        anchored = any(entry.position == effect.after for entry in stream.entries)
        return EntryRange(entries, complete=anchored or stream.added == len(stream.entries))

    def announce(self, effect: Announce) -> tuple[_Wake, ...]:
        """Give the notice to every current subscriber of the channel: its waiting task, or its queue."""
        notice = Announcement(effect.channel, effect.name, effect.body)
        hearing = tuple(listener for listener in self._mut_listeners if effect.channel in listener.subscription.channels)
        wakes = tuple(_Wake(listener.waiter, notice) for listener in hearing if listener.waiter is not None)
        for listener in hearing:
            if listener.waiter is None:
                listener.queued = (*listener.queued, notice)
            listener.waiter = None
        return wakes

    def subscribe(self, effect: SubscribeChannels) -> ChannelSubscription:
        """Start a subscription: notices announced from now on reach it."""
        subscription = ChannelSubscription(effect.channels)
        self._mut_listeners = (*self._mut_listeners, _Listener(subscription))
        return subscription

    def listener(self, subscription: ChannelSubscription) -> _Listener:
        """Find the broker's side of ``subscription`` — one made by another broker is a wiring error."""
        for listener in self._mut_listeners:
            if listener.subscription is subscription:
                return listener
        raise LookupError(f"{subscription!r} was not made by this broker")

    def add_back_waiter(self, promise: Promise[object]) -> None:
        """Register a task waiting in ``AwaitBrokerBack``."""
        self._mut_back_waiters = (*self._mut_back_waiters, promise)

    def remove_back_waiter(self, promise: Promise[object]) -> None:
        """Forget a task that stopped waiting for the broker (answered or cancelled)."""
        self._mut_back_waiters = tuple(known for known in self._mut_back_waiters if known is not promise)

    def cut(self, detail: str) -> tuple[_Wake, ...]:
        """Make the broker unreachable: every blocked read and every waiting listener is answered
        ``BrokerUnreachable``, and notices in subscribers' queues are dropped (they were never delivered)."""
        down = BrokerUnreachable(detail)
        self._mut_down = down
        wakes = (
            *(_Wake(reader.promise, down) for reader in self._mut_readers),
            *(_Wake(listener.waiter, down) for listener in self._mut_listeners if listener.waiter is not None),
        )
        self._mut_readers = ()
        for listener in self._mut_listeners:
            listener.queued = ()
            listener.waiter = None
        return wakes

    def restore(self) -> tuple[_Wake, ...]:
        """Make the broker reachable again and answer everyone waiting in ``AwaitBrokerBack``."""
        self._mut_down = None
        wakes = tuple(_Wake(promise, None) for promise in self._mut_back_waiters)
        self._mut_back_waiters = ()
        return wakes


@do
def cut_broker(broker: MemoryBroker, detail: str) -> "EffectGenerator[None]":
    """Test control: take ``broker`` away, so that everyone using it sees it as unreachable from now on."""
    for wake in broker.cut(detail):
        yield CompletePromise(wake.promise, wake.value)


@do
def restore_broker(broker: MemoryBroker) -> "EffectGenerator[None]":
    """Test control: bring ``broker`` back, so that everyone waiting for it goes on."""
    for wake in broker.restore():
        yield CompletePromise(wake.promise, wake.value)


def memory_stream_handler(broker: MemoryBroker) -> "ProgramHandler":
    """Build the handler that answers the lower-layer stream effects from ``broker``.

    Handlers built from the same broker exchange entries and notices, as processes connected to one Redis do.
    """

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
        | NextAnnouncement
        | AwaitBrokerBack,
        k: K,
    ) -> "EffectGenerator[object]":
        """Answer one broker operation; while the broker is cut, answer ``BrokerUnreachable`` to all but the wait
        for its return."""
        if isinstance(effect, AwaitBrokerBack):
            if broker.down is not None:
                back: Promise[object] = yield CreatePromise()
                broker.add_back_waiter(back)
                try:
                    yield Wait(back.future)
                finally:
                    broker.remove_back_waiter(back)
            return (yield Resume(k, None))
        if broker.down is not None:
            return (yield Resume(k, broker.down))
        answer: object = None
        wakes: tuple[_Wake, ...] = ()
        match effect:
            case AppendEntry():
                appended = broker.append(effect)
                answer, wakes = appended.position, appended.wakes
            case EnsureGroup():
                broker.ensure_group(effect)
            case ReadGroup(streams=streams, group=group, consumer=consumer):
                answer = broker.take(streams, group, consumer)
                if not answer:
                    waiting: Promise[object] = yield CreatePromise()
                    reader = broker.add_reader(effect, waiting)
                    try:
                        answer = yield Wait(waiting.future)
                    finally:
                        broker.remove_reader(reader)
            case AckEntry():
                broker.ack(effect)
            case ClaimPending():
                answer = broker.claim(effect)
            case ReadGroupPosition():
                answer = broker.position(effect)
            case ReadEntryRange():
                answer = broker.range(effect)
            case Announce():
                wakes = broker.announce(effect)
            case SubscribeChannels():
                answer = broker.subscribe(effect)
            case NextAnnouncement(subscription=subscription):
                listener = broker.listener(subscription)
                if listener.queued:
                    answer, listener.queued = listener.queued[0], listener.queued[1:]
                else:
                    hearing: Promise[object] = yield CreatePromise()
                    listener.waiter = hearing
                    try:
                        answer = yield Wait(hearing.future)
                    finally:
                        if listener.waiter is hearing:
                            listener.waiter = None
            case _:
                yield Pass(effect, k)
                return None
        for wake in wakes:
            yield CompletePromise(wake.promise, wake.value)
        return (yield Resume(k, answer))

    return _program_handler(handler)
