"""In-memory notice broker: the lower layer of ``doeff_events.effects.notices`` inside one process.

``MemoryBroker`` holds channels whose notices reach only current subscribers — the meaning Redis Pub/Sub has,
without a server. The emulated environment and tests share one broker between the handlers of the programs that
stand for separate processes (agora-redesign #3850).

``memory_notice_handler(broker)`` answers the lower-layer effects from that broker. A blocked wait is a scheduler
promise (``CreatePromise`` / ``Wait``), like ``subscribed_event_handler``, so a virtual clock and the scheduler's
dead-end detection keep working. ``Announce`` returns to the sender at once, as a Redis ``PUBLISH`` does: the
subscribers it woke run when the sender next waits (``CompletePromise(..., yield_to_woken=False)`` —
agora-redesign #4013).

A test can take the broker away and bring it back with ``cut_broker`` / ``restore_broker``. A cut ends every
subscription, as a lost connection does: waiting subscribers are answered ``BrokerUnreachable``, notices not yet
taken are gone, and a subscription made before the cut answers ``BrokerUnreachable`` from then on (a new one must
be made). While the broker is cut every operation answers ``BrokerUnreachable`` and ``AwaitBrokerBack`` waits.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Wait

from doeff import K, Pass, Pure, Resume, do
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


@final
class MemoryBroker:
    """The state of an in-memory notice broker. Programs never see it; ``memory_notice_handler`` is its only
    user, and ``cut_broker`` / ``restore_broker`` / ``hold_broker`` / ``release_broker`` are the test controls."""

    __slots__ = ("_mut_back_waiters", "_mut_down", "_mut_held", "_mut_listeners")

    def __init__(self) -> None:
        """Start a reachable broker with no subscriber."""
        self._mut_listeners: tuple[_Listener, ...] = ()
        self._mut_back_waiters: tuple[Promise[object], ...] = ()
        self._mut_down: BrokerUnreachable | None = None
        # While held (``hold_broker``): the sends and tries waiting for an answer that does not come.
        self._mut_held: tuple[Promise[object], ...] | None = None

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
        """Give the notice to every current subscriber of the channel: its waiting task, or its queue."""
        notice = Announcement(effect.channel, effect.name, effect.body)
        hearing = tuple(
            listener for listener in self._mut_listeners if effect.channel in listener.subscription.channels
        )
        wakes = tuple(_Wake(listener.waiter, notice) for listener in hearing if listener.waiter is not None)
        for listener in hearing:
            if listener.waiter is None:
                listener.queued = (*listener.queued, notice)
            listener.waiter = None
        return _Announced(len(hearing), wakes)

    def subscribe(self, effect: SubscribeChannels) -> ChannelSubscription:
        """Start a subscription: notices announced from now on reach it."""
        subscription = ChannelSubscription(effect.channels)
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


def memory_notice_handler(broker: MemoryBroker) -> "ProgramHandler":
    """Build the handler that answers the lower-layer notice effects from ``broker``.

    Handlers built from the same broker exchange notices, as processes connected to one Redis do.
    """

    @do
    def handler(
        effect: Announce | SubscribeChannels | NextAnnouncement | CloseSubscription | AwaitBrokerBack | ProbeBroker,
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
            case Announce() | SubscribeChannels() | NextAnnouncement() if broker.down is not None:
                answer = broker.down
            case Announce():
                announced = broker.announce(effect)
                answer, wakes = announced.receivers, announced.wakes
            case SubscribeChannels():
                answer = broker.subscribe(effect)
            case NextAnnouncement(subscription=subscription):
                listener = broker.listener(subscription)
                if listener is None:
                    answer = _ENDED
                elif listener.queued:
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
            # The sender keeps its turn, as a Redis PUBLISH returns without waiting for the subscribers: the ones
            # woken run when the sender next waits, so the notices it sends in one stretch reach them together
            # (agora-redesign #4013). Nothing is lost meanwhile — ``announce`` already took the woken subscribers'
            # waits away, so a notice sent before they ask again goes to their queue.
            yield CompletePromise(wake.promise, wake.value, yield_to_woken=False)
        return (yield Resume(k, answer))

    return _program_handler(handler)
