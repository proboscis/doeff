"""In-memory notice broker: the lower layer of ``doeff_events._effects.notices`` inside one process.

``MemoryBroker`` holds channels whose notices reach only current subscribers — the meaning Redis Pub/Sub has,
without a server. The emulated environment and tests share one broker between the handlers of the programs that
stand for separate processes.

``memory_notice_handler(broker)`` answers the lower-layer effects from that broker. A blocked wait is a scheduler
promise (``CreatePromise`` / ``Wait``), like ``subscribed_event_handler``, so a virtual clock and the scheduler's
dead-end detection keep working. ``Announce`` returns to the sender at once, as a Redis ``PUBLISH`` does: the
subscribers it woke run when the sender next waits (``CompletePromise(..., yield_to_woken=False)``).

A test can take the broker away and bring it back with ``cut_broker`` / ``restore_broker``. A cut ends every
subscription, as a lost connection does: waiting subscribers are answered ``BrokerUnreachable``, notices not yet
taken are gone, and a subscription made before the cut answers ``BrokerUnreachable`` from then on (a new one must
be made). While the broker is cut every operation answers ``BrokerUnreachable`` and ``AwaitBrokerBack`` waits.

Kept channels (``Announce(kept=True)``): the broker keeps one log per channel, which a cut does not empty. Each
handler (one process) keeps, per reader name, the position read up to in each kept channel and the notices handed
out without an acknowledgement. A wait on a subscription that holds unacknowledged notices has a deadline (one
``WaitWithin`` until the oldest is due, under the clock handler outside — a virtual clock in the emulated
environment); the due notice is then handed out again.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final, final

from doeff_core_effects import CompletePromise, CreatePromise, Promise, Wait
from doeff_time import GetTime, WaitWithin

from doeff import K, Pass, Pure, Resume, do
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
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler


@dataclass(frozen=True)
class _Wake:
    """A waiting task to wake: complete ``promise`` with ``value`` (the handler does it — the broker has no effects)."""

    promise: Promise[object]
    value: object


@dataclass(frozen=True)
class _Announced:
    """What announcing a notice gave: how many subscribers received it, and the waiting ones to wake with it."""

    receivers: int
    wakes: tuple[_Wake, ...]


@final
class _Listener:
    """What the broker keeps for one ``ChannelSubscription``: notices not yet taken, and the waiting task if any.
    Matched by the identity of its subscription."""

    __slots__ = ("queued", "subscription", "waiter")

    def __init__(self, subscription: ChannelSubscription) -> None:
        """Start listening for ``subscription`` with nothing queued."""
        self.subscription: Final = subscription
        self.queued: tuple[Announcement, ...] = ()
        self.waiter: Promise[object] | None = None


@dataclass(frozen=True)
class _Handed:
    """A kept channel's notice handed to its reader and not acknowledged yet, and when it was handed out."""

    notice: Announcement
    at: datetime


@final
class _KeptReader:
    """What one handler keeps for one reader name: the position read up to in each kept channel (the number of
    the log's notices already handed out) and the notices handed out without an acknowledgement, oldest first."""

    __slots__ = ("handed", "positions")

    def __init__(self) -> None:
        """Start a reader that has read nothing (its first subscription sets the positions at the logs' ends)."""
        self.positions: dict[str, int] = {}
        self.handed: tuple[_Handed, ...] = ()

    def due(self, now: datetime, redeliver_seconds: float) -> _Handed | None:
        """The oldest unacknowledged notice whose time to be handed out again has come, or ``None``."""
        return next(
            (held for held in self.handed if (now - held.at).total_seconds() >= redeliver_seconds), None
        )

    def seconds_to_due(self, now: datetime, redeliver_seconds: float) -> float | None:
        """How long until the oldest unacknowledged notice is due (``None`` = none is held)."""
        if not self.handed:
            return None
        return max(0.0, redeliver_seconds - (now - self.handed[0].at).total_seconds())

    def hand(self, notice: Announcement, now: datetime) -> Announcement:
        """Hand ``notice`` out (again) at ``now``: it is the newest unacknowledged one from now on."""
        kept = tuple(held for held in self.handed if not _same_notice(held.notice, notice))
        self.handed = (*kept, _Handed(notice, now))
        return notice

    def acknowledged(self, notice: Announcement) -> None:
        """Forget ``notice``: it is not handed out again (nothing changes if it is not held)."""
        self.handed = tuple(held for held in self.handed if not _same_notice(held.notice, notice))


def _same_notice(one: Announcement, other: Announcement) -> bool:
    """Whether two notices are the same notice of a kept channel's log (its channel and its receipt)."""
    return one.channel == other.channel and one.receipt == other.receipt


@final
class MemoryBroker:
    """The state of an in-memory notice broker. Programs never see it; ``memory_notice_handler`` is its only
    user, and ``cut_broker`` / ``restore_broker`` / ``hold_broker`` / ``release_broker`` are the test controls."""

    __slots__ = ("_mut_back_waiters", "_mut_down", "_mut_held", "_mut_listeners", "_mut_logs")

    def __init__(self) -> None:
        """Start a reachable broker with no subscriber."""
        self._mut_listeners: tuple[_Listener, ...] = ()
        self._mut_back_waiters: tuple[Promise[object], ...] = ()
        self._mut_down: BrokerUnreachable | None = None
        # While held (``hold_broker``): the sends and tries waiting for an answer that does not come.
        self._mut_held: tuple[Promise[object], ...] | None = None
        # The log of each kept channel, oldest first (a notice's receipt is its place in the log, from "1").
        self._mut_logs: dict[str, tuple[Announcement, ...]] = {}

    @property
    def held(self) -> bool:
        """Whether the broker keeps its connections but answers no send (``hold_broker``)."""
        return self._mut_held is not None

    def hold_waiter(self, promise: Promise[object]) -> None:
        """Register a send waiting for an answer while the broker is held."""
        self._mut_held = (*(self._mut_held or ()), promise)

    def hold(self) -> None:
        """Keep every connection but answer no send from now on."""
        if self._mut_held is None:
            self._mut_held = ()

    def release(self) -> tuple[_Wake, ...]:
        """Answer sends again; the ones that waited go on."""
        waiting, self._mut_held = self._mut_held or (), None
        return tuple(_Wake(promise, None) for promise in waiting)

    @property
    def down(self) -> BrokerUnreachable | None:
        """The unreachable answer every operation gives while the broker is cut, or ``None`` while it is up."""
        return self._mut_down

    def announce(self, effect: Announce) -> _Announced:
        """Give the notice to every current subscriber of the channel: its waiting task, or its queue. A kept
        channel's notice goes to the channel's log instead, and wakes the subscriptions waiting on it (they read the
        log themselves). A notice announced without ``kept`` on a channel a subscription reads as kept still reaches
        it (a sender that has not switched to the kept channel yet — the notice carries no receipt)."""
        if effect.kept:
            return self._kept(effect)
        notice = Announcement(effect.channel, effect.name, effect.body)
        hearing = tuple(listener for listener in self._mut_listeners if effect.channel in listener.subscription.channels)
        wakes = tuple(_Wake(listener.waiter, notice) for listener in hearing if listener.waiter is not None)
        for listener in hearing:
            if listener.waiter is None:
                listener.queued = (*listener.queued, notice)
            listener.waiter = None
        return _Announced(len(hearing), wakes)

    def _kept(self, effect: Announce) -> _Announced:
        """Add a kept channel's notice to its log and wake every subscription waiting on that channel (``None`` =
        read the log again). The answer counts the log, not the readers: whoever reads the channel reads it."""
        log = self._mut_logs.get(effect.channel, ())
        notice = Announcement(effect.channel, effect.name, effect.body, receipt=str(len(log) + 1))
        self._mut_logs = {**self._mut_logs, effect.channel: (*log, notice)}
        waking = tuple(
            listener
            for listener in self._mut_listeners
            if effect.channel in listener.subscription.kept and listener.waiter is not None
        )
        wakes = tuple(_Wake(listener.waiter, None) for listener in waking if listener.waiter is not None)
        for listener in waking:
            listener.waiter = None
        return _Announced(1, wakes)

    def log_length(self, channel: str) -> int:
        """How many notices the kept channel's log holds (where a reader's first subscription starts)."""
        return len(self._mut_logs.get(channel, ()))

    def kept_after(self, channel: str, position: int) -> Announcement | None:
        """The notice of the kept channel's log after the first ``position`` ones, or ``None`` when none is."""
        log = self._mut_logs.get(channel, ())
        return log[position] if position < len(log) else None

    def subscribe(self, effect: SubscribeChannels) -> ChannelSubscription:
        """Start a subscription: notices announced from now on reach it."""
        subscription = ChannelSubscription(effect.channels, effect.kept, effect.reader)
        self._mut_listeners = (*self._mut_listeners, _Listener(subscription))
        return subscription

    def listener(self, subscription: ChannelSubscription) -> _Listener | None:
        """The broker's side of ``subscription``, or ``None`` when it is gone (closed, or ended by a cut)."""
        return next((known for known in self._mut_listeners if known.subscription is subscription), None)

    def close(self, subscription: ChannelSubscription) -> None:
        """Forget ``subscription``; nothing is kept for it any more."""
        self._mut_listeners = tuple(
            known for known in self._mut_listeners if known.subscription is not subscription
        )

    def add_back_waiter(self, promise: Promise[object]) -> None:
        """Register a task waiting in ``AwaitBrokerBack``."""
        self._mut_back_waiters = (*self._mut_back_waiters, promise)

    def remove_back_waiter(self, promise: Promise[object]) -> None:
        """Forget a task that stopped waiting for the broker (answered or cancelled)."""
        self._mut_back_waiters = tuple(known for known in self._mut_back_waiters if known is not promise)

    def cut(self, detail: str) -> tuple[_Wake, ...]:
        """Make the broker unreachable and end every subscription, as lost connections do: waiting subscribers
        are answered ``BrokerUnreachable`` and notices not yet taken are gone."""
        down = BrokerUnreachable(detail)
        wakes = tuple(_Wake(known.waiter, down) for known in self._mut_listeners if known.waiter is not None)
        self._mut_down = down
        self._mut_listeners = ()
        return wakes

    def restore(self) -> tuple[_Wake, ...]:
        """Make the broker reachable again and answer everyone waiting in ``AwaitBrokerBack``."""
        wakes = tuple(_Wake(promise, None) for promise in self._mut_back_waiters)
        self._mut_down = None
        self._mut_back_waiters = ()
        return wakes


@do
def cut_broker(broker: MemoryBroker, detail: str) -> "EffectGenerator[None]":
    """Test control: take ``broker`` away, so that everyone using it sees it as unreachable from now on."""
    for wake in broker.cut(detail):
        yield CompletePromise(wake.promise, wake.value)


@do
def hold_broker(broker: MemoryBroker) -> "EffectGenerator[None]":
    """Test control: the broker keeps every connection but stops answering sends (``Announce``, ``ProbeBroker``) —
    a server that hangs, or a network that went silent. Subscriptions stay as they are."""
    broker.hold()
    yield Pure(None)


@do
def release_broker(broker: MemoryBroker) -> "EffectGenerator[None]":
    """Test control: the held broker answers sends again; those that waited are answered now."""
    for wake in broker.release():
        yield CompletePromise(wake.promise, wake.value)


@do
def restore_broker(broker: MemoryBroker) -> "EffectGenerator[None]":
    """Test control: bring ``broker`` back, so that everyone waiting for it goes on."""
    for wake in broker.restore():
        yield CompletePromise(wake.promise, wake.value)


_ENDED: Final = BrokerUnreachable("the subscription ended when the broker was cut")


@do
def _heard(listener: _Listener, seconds: float | None) -> "EffectGenerator[object]":
    """Wait on ``listener`` for what the broker gives it — a Pub/Sub notice, a kept channel's wake (``None``), or
    ``BrokerUnreachable`` — for at most ``seconds`` (``None`` = no limit; a timeout answers ``None``)."""
    hearing: Promise[object] = yield CreatePromise()
    listener.waiter = hearing
    try:
        if seconds is None:
            return (yield Wait(hearing.future))
        return (yield WaitWithin(hearing.future, seconds))
    finally:
        if listener.waiter is hearing:
            listener.waiter = None


@do
def _next_kept(
    broker: MemoryBroker, listener: _Listener, reader: _KeptReader, redeliver_seconds: float
) -> "EffectGenerator[object]":
    """The next notice of a subscription that reads kept channels: a queued Pub/Sub notice first, then an
    unacknowledged notice that is due again, then the next notice of a kept log; otherwise wait — until the oldest
    unacknowledged notice is due when one is held."""
    while True:
        if listener.queued:
            notice, listener.queued = listener.queued[0], listener.queued[1:]
            return notice
        now: datetime = yield GetTime()
        due = reader.due(now, redeliver_seconds)
        if due is not None:
            return reader.hand(due.notice, now)
        for channel in listener.subscription.kept:
            fresh = broker.kept_after(channel, reader.positions[channel])
            if fresh is not None:
                reader.positions = {**reader.positions, channel: reader.positions[channel] + 1}
                return reader.hand(fresh, now)
        heard = yield _heard(listener, reader.seconds_to_due(now, redeliver_seconds))
        if heard is not None:
            return heard


def memory_notice_handler(broker: MemoryBroker, redeliver_seconds: float = KEPT_REDELIVERY_SECONDS) -> "ProgramHandler":
    """Build the handler that answers the lower-layer notice effects from ``broker``.

    Handlers built from the same broker exchange notices, as processes connected to one Redis do.
    ``redeliver_seconds`` = how long a kept channel's notice handed to its reader waits for the acknowledgement
    before it is handed out again. The readers of kept channels belong to this handler (one process): each open
    subscription holds a reader state of its own, and a closed one leaves it to the next subscription of the same
    reader name (``handlers/_kept_readers.py``).
    """
    if isinstance(redeliver_seconds, bool) or not isinstance(redeliver_seconds, int | float) or redeliver_seconds <= 0:
        raise ValueError(f"memory_notice_handler: redeliver_seconds must be a number > 0, got {redeliver_seconds!r}")
    readers: KeptReaders[_KeptReader] = KeptReaders(_KeptReader)

    @do
    def handler(
        effect: Announce
        | SubscribeChannels
        | NextAnnouncement
        | AckAnnouncement
        | CloseSubscription
        | AwaitBrokerBack
        | ProbeBroker,
        k: K,
    ) -> "EffectGenerator[object]":
        """Answer one broker operation; while the broker is cut, answer ``BrokerUnreachable`` to all but the wait
        for its return and the close."""
        answer: object = None
        wakes: tuple[_Wake, ...] = ()
        if isinstance(effect, Announce | ProbeBroker) and broker.held:
            # A held broker answers no send until it is released (whoever waits must have a time limit of its own).
            unanswered: Promise[object] = yield CreatePromise()
            broker.hold_waiter(unanswered)
            yield Wait(unanswered.future)
        match effect:
            case AwaitBrokerBack():
                if broker.down is not None:
                    back: Promise[object] = yield CreatePromise()
                    broker.add_back_waiter(back)
                    try:
                        yield Wait(back.future)
                    finally:
                        broker.remove_back_waiter(back)
            case ProbeBroker():
                answer = broker.down
            case CloseSubscription(subscription=closing):
                broker.close(closing)
                readers.detach(closing)
            case AckAnnouncement(subscription=acked, notice=notice):
                # Kept by the handler only: it answers while the broker is cut too (nothing is sent).
                acking = readers.of(acked)
                if acking is not None:
                    acking.acknowledged(notice)
            case Announce() | SubscribeChannels() | NextAnnouncement() if broker.down is not None:
                answer = broker.down
            case Announce():
                announced = broker.announce(effect)
                answer, wakes = announced.receivers, announced.wakes
            case SubscribeChannels(kept=kept, reader=name):
                subscription = broker.subscribe(effect)
                if name is not None:
                    reader = readers.attach(name)
                    # A reader's first subscription starts at the end of each log; a later one goes on where it was.
                    reader.positions = {channel: broker.log_length(channel) for channel in kept} | reader.positions
                    readers.held(subscription, reader)
                answer = subscription
            case NextAnnouncement(subscription=subscription):
                listener = broker.listener(subscription)
                reading = readers.of(subscription)
                if listener is None:
                    answer = _ENDED
                elif reading is not None:
                    answer = yield _next_kept(broker, listener, reading, redeliver_seconds)
                elif listener.queued:
                    answer, listener.queued = listener.queued[0], listener.queued[1:]
                else:
                    answer = yield _heard(listener, None)
            case _:
                yield Pass(effect, k)
                return None
        for wake in wakes:
            # The sender keeps its turn, as a Redis PUBLISH returns without waiting for the subscribers: the ones
            # woken run when the sender next waits, so the notices it sends in one stretch reach them together
            #. Nothing is lost meanwhile — ``announce`` already took the woken subscribers'
            # waits away, so a notice sent before they ask again goes to their queue.
            yield CompletePromise(wake.promise, wake.value, yield_to_woken=False)
        return (yield Resume(k, answer))

    return _program_handler(handler)
