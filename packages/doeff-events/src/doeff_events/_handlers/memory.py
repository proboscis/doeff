"""In-memory publish/subscribe handlers.

``event_handler()`` broadcasts a signal to everyone waiting at that moment and discards the signal if nobody is waiting (the original behavior unchanged).
``subscribed_event_handler()`` keeps a queue per subscriber and also queues signals emitted while no waiter exists — so a signal emitted by another task between reading the state and entering
``WaitForEvent`` is not lost (design #3072). "Something changed" signals of the same type piled up in the queue
are merged into one. ``WaitForEvents`` waits until at least one arrives, then receives, in arrival order as a tuple, all
matching signals in the queue at that moment — several notices arriving at the same time are merged into one read.
"""

import inspect
from dataclasses import fields, is_dataclass, replace
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard, final

from doeff_core_effects import CompletePromise, CreatePromise, Promise, Wait

from doeff import K, Pass, Resume, ResumeThrow, do
from doeff import handler as _program_handler
from doeff_events._effects import (
    CountQueuedEffect,
    PublishEffect,
    SourceFailed,
    StopArrived,
    TimerFired,
    WaitForEventEffect,
    WaitForEventsEffect,
)

if TYPE_CHECKING:
    from collections.abc import Hashable

    from _typeshed import DataclassInstance

    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

    class _KeyedSignal(DataclassInstance, Protocol):
        """Shape of a mergeable signal: a frozen dataclass with a field ``keys`` holding a tuple of changed places."""

        @property
        def keys(self) -> tuple[Hashable, ...]:
            """Changed places (``ChangedRow`` in the record-signal source of doeff-records)."""
            ...


def _matching_promises(listeners, event):
    matched = {}
    for event_type, queued in listeners.items():
        if not isinstance(event, event_type):
            continue
        for promise in queued:
            matched.setdefault(id(promise), promise)
    return matched


def _remove_promises(listeners, promise_ids):
    if not promise_ids:
        return
    for event_type, queued in list(listeners.items()):
        remaining = [p for p in queued if id(p) not in promise_ids]
        if remaining:
            listeners[event_type] = remaining
        else:
            listeners.pop(event_type, None)


def event_handler():
    """Create a stateful in-memory pub/sub handler.

    WaitForEvent creates a Promise and blocks via Wait(promise.future).
    WaitForEvents waits the same way and answers the one event it received as ``(event,)`` (there is no queue to
    hold more).
    Publish resolves promises for listeners whose registered type matches.
    """
    listeners: dict[type, list] = {}

    @do
    def handler(effect: WaitForEventEffect | WaitForEventsEffect | PublishEffect, k):
        if isinstance(effect, WaitForEventEffect | WaitForEventsEffect):
            promise = yield CreatePromise()
            for event_type in effect.event_types:
                listeners.setdefault(event_type, []).append(promise)
            try:
                event = yield Wait(promise.future)
            finally:
                _remove_promises(listeners, {id(promise)})
            answer = (event,) if isinstance(effect, WaitForEventsEffect) else event
            result = yield Resume(k, answer)
            return result

        if isinstance(effect, PublishEffect):
            event = effect.event
            matched = _matching_promises(listeners, event)
            _remove_promises(listeners, set(matched))
            for promise in matched.values():
                yield CompletePromise(promise, event)
            result = yield Resume(k, None)
            return result

        yield Pass(effect, k)

    return _program_handler(handler)


@final
class Empty:
    """Marker for ``SubscriberQueue.take``: the queue has no signal matching the requested types.

    A signal itself may be ``None``, so "none" is expressed as a separate value that cannot be confused with a signal. Compare with ``isinstance(x, Empty)``
    or ``x is EMPTY``.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        """Print the marker under a name distinguishable from a signal in assertion messages and logs."""
        return "EMPTY"


EMPTY: Final[Empty] = Empty()


@final
class _Waiter:
    """One waiter in the queue: a pair of the type tuple requested by ``WaitForEvent`` and the promise used to hand over a signal. Compared by identity (one waiter per promise)."""

    __slots__ = ("event_types", "promise")

    def __init__(self, event_types: tuple[type, ...], promise: Promise[object]) -> None:
        """Create a waiter — ``offer`` checks the signal's type against ``event_types`` and returns this promise."""
        self.event_types: Final = event_types
        self.promise: Final = promise


def _require_types(event_types: tuple[type, ...]) -> tuple[type, ...]:
    # Validate input from callers without type annotations (Hy, plain Python) at construction time (do not defer until a signal is handed over).
    for event_type in event_types:
        if not inspect.isclass(event_type):
            raise TypeError(f"SubscriberQueue event types must be type objects, got {event_type!r}")
    return event_types


_KEYS: Final = "keys"


def _coalescible(event: object) -> "TypeGuard[_KeyedSignal]":
    """Whether ``event`` may be merged with a signal of the same type in the queue — the merge rule is decided in this one place.

    Mergeable = a frozen dataclass signal whose construction fields include ``keys`` holding a tuple (the shape emitted by the
    record-signal source of doeff-records — elements are hashable places, used to remove duplicates when merging). A signal of this shape carries only
    "places ``keys`` changed", and the receiver re-reads the records by place, so merging it with a not-yet-delivered signal of the same type by combining ``keys`` does not change the state the receiver
    builds. Signals without ``keys`` (such as ``TimerFired`` — meaning differs per tag) are not merged.
    """
    if isinstance(event, type) or not is_dataclass(event):
        return False
    params: object = getattr(type(event), "__dataclass_params__", None)
    if getattr(params, "frozen", False) is not True:
        return False
    if not any(field.name == _KEYS and field.init for field in fields(event)):
        return False
    keys: object = getattr(event, _KEYS)
    return isinstance(keys, tuple)


def _same_apart_from_keys(queued: "_KeyedSignal", arriving: "_KeyedSignal") -> bool:
    """Whether two signals have the same type and equal fields other than ``keys`` — if any other field differs, merging would lose one side's field, so do not merge."""
    if type(queued) is not type(arriving):
        return False
    return all(
        getattr(queued, field.name) == getattr(arriving, field.name)
        for field in fields(queued)
        if field.name != _KEYS
    )


def _with_keys_of(queued: "_KeyedSignal", arriving: "_KeyedSignal") -> "_KeyedSignal":
    """The signal made from ``queued`` with the ``keys`` of ``arriving`` combined (in arrival order; of duplicates only the first is kept)."""
    # dict.fromkeys keeps arrival order and removes duplicates (the values are unused).
    return replace(queued, keys=tuple(dict.fromkeys((*queued.keys, *arriving.keys))))


@final
class SubscriberQueue:
    """Signal queue of one subscriber.

    ``offer(event)`` hands a signal matching the subscription types to a waiter whose types match, if one exists (one waiter, in arrival order), via its promise, and otherwise
    to the queue. If the queue already holds a mergeable signal of the same type (``_coalescible``), it replaces that signal with one with combined ``keys``
    (keeping the earlier position); otherwise it appends at the tail. Signals not matching the subscription types are discarded. ``take(types)`` removes from the head of the queue
    the first signal matching ``types``. Each signal is delivered only once per subscriber (to one waiter, or to
    one place in the queue — a merged signal lives in its ``keys``). ``take_all(types)`` removes all signals matching ``types`` in arrival order.

    A handler that receives signals from the record service can also hand the received signals to this ``offer`` and so
    share the waiting behavior of the ``WaitForEvent`` side (``take`` -> if none, wait as a waiter via the promise).
    """

    __slots__ = ("_event_types", "_pending", "_waiters")

    def __init__(self, event_types: tuple[type, ...]) -> None:
        """Create an empty queue — only signals ``offer``ed after this point are queued (the start of the subscription)."""
        self._event_types: Final = _require_types(tuple(event_types))
        self._pending: Final[list[object]] = []
        self._waiters: Final[list[_Waiter]] = []

    @property
    def event_types(self) -> tuple[type, ...]:
        """Subscription types (``()`` = queue nothing — a subscriber that only emits)."""
        return self._event_types

    def outside(self, wanted: tuple[type, ...]) -> tuple[type, ...]:
        """The types in ``wanted`` that are outside the subscription types.

        A type t is inside = a subtype of one of the subscription types (a signal matching t always enters the queue). Waiting for an outside type means some or
        all signals of that type never enter the queue, so the wait is never satisfied.
        """
        return tuple(t for t in wanted if not issubclass(t, self._event_types))

    def offer(self, event: object) -> Promise[object] | None:
        """Hand ``event`` to the queue. Returns the promise of the waiter to wake (removed from the waiters; the caller does ``CompletePromise``).

        If no waiter of a matching type exists, place it in the queue and return ``None`` — if the queue has a mergeable signal of the same type, merge into it as one;
        otherwise append at the tail. If it does not match the subscription types, discard it and return ``None``.
        """
        if not isinstance(event, self._event_types):
            return None
        for waiter in self._waiters:
            if isinstance(event, waiter.event_types):
                self._waiters.remove(waiter)
                return waiter.promise
        self._place(event)
        return None

    def _place(self, event: object) -> None:
        """Place ``event``, which has no waiter, in the queue: a deadline (``TimerFired``) goes after the deadlines already in the queue and before other signals; if a mergeable signal of the same type
        exists, merge at that position, otherwise append at the tail.

        Why deadlines go in front: the deadline's moment has already passed. If appended at the tail, the deadline is never delivered behind signals (one event per record write) that are queued faster than the receiver can process them
        (the daily pruning deadline of job agent-toolset-sync). Deadlines among themselves keep arrival order.
        """
        if isinstance(event, TimerFired):
            ahead = next(
                (i for i, queued in enumerate(self._pending) if not isinstance(queued, TimerFired)),
                None,
            )
            self._pending.insert(len(self._pending) if ahead is None else ahead, event)
            return
        if _coalescible(event):
            for index, queued in enumerate(self._pending):
                if _coalescible(queued) and _same_apart_from_keys(queued, event):
                    self._pending[index] = _with_keys_of(queued, event)
                    return
        self._pending.append(event)

    def take(self, wanted: tuple[type, ...]) -> object | Empty:
        """Remove the first signal matching ``wanted`` from the head of the queue. ``EMPTY`` if none."""
        for index, event in enumerate(self._pending):
            if isinstance(event, wanted):
                del self._pending[index]
                return event
        return EMPTY

    def count(self, wanted: tuple[type, ...]) -> int:
        """How many signals matching ``wanted`` are in the queue (a merged signal counts once). The queue is left as it is."""
        return sum(1 for event in self._pending if isinstance(event, wanted))

    def take_all(self, wanted: tuple[type, ...]) -> tuple[object, ...]:
        """Remove all signals matching ``wanted`` from the head of the queue and return them as a tuple in arrival order. Empty tuple if none. Non-matching signals stay in the queue."""
        taken = tuple(event for event in self._pending if isinstance(event, wanted))
        self._pending[:] = [event for event in self._pending if not isinstance(event, wanted)]
        return taken

    def add_waiter(self, wanted: tuple[type, ...], promise: Promise[object]) -> None:
        """Append a waiter that waits for signals of ``wanted`` via ``promise`` to the tail (woken in arrival order)."""
        self._waiters.append(_Waiter(_require_types(tuple(wanted)), promise))

    def remove_waiter(self, promise: Promise[object]) -> None:
        """Remove the waiter of ``promise``. Do nothing if it is already gone because a signal was handed over."""
        self._waiters[:] = [waiter for waiter in self._waiters if waiter.promise is not promise]


@final
class EventBus:
    """In-memory signal store: subscriber name -> that subscriber's queue.

    One per process (one world in simulation); ``subscribed_event_handler``s given the same bus exchange signals with one another.
    Programs (``Publish``, ``WaitForEvent``) never see the bus — only handlers touch it.
    """

    __slots__ = ("_queues",)

    def __init__(self) -> None:
        """Create a store with no subscribers — shared as the place where handlers of one world exchange signals."""
        self._queues: Final[dict[str, SubscriberQueue]] = {}

    def subscribe(self, subscriber: str, event_types: tuple[type, ...]) -> SubscriberQueue:
        """Start a fresh queue for ``subscriber`` and return it. A previous queue of the same name is discarded (only signals after this point are queued)."""
        queue = SubscriberQueue(event_types)
        self._queues[subscriber] = queue
        return queue

    def offer(self, event: object) -> tuple[Promise[object], ...]:
        """Hand ``event`` to every subscriber's queue and return the promises of waiters to wake (the caller does ``CompletePromise``)."""
        offered = tuple(queue.offer(event) for queue in self._queues.values())
        return tuple(promise for promise in offered if promise is not None)


def subscribed_event_handler(
    bus: EventBus,
    subscriber: str,
    event_types: tuple[type, ...] = (),
) -> "ProgramHandler":
    """Build an in-memory pub/sub handler with a per-subscriber queue.

    At construction time, starts ``subscriber``'s subscription on ``bus`` and queues only signals of ``event_types`` emitted after that point.
    A subscriber with awaited types always also subscribes to the stop signal ``StopArrived`` and the source-failure signal ``SourceFailed`` — the loop's stop watcher
    (``event_loop.begin_watch``) and the signal-source task (the record-signal source of doeff-records) emit them on the same bus, and the waiting side awaits them with the same
    ``WaitForEvent`` as events (so even if they are forgotten in the awaited-type declaration, the worker neither fails to stop nor misses a source failure).
    Rebuilding with the same ``subscriber`` name discards the previous queue and starts fresh (restart after a crash — catching up is done by the Program
    reading the records first). ``event_types=()`` is an emit-only handler (it queues neither stop nor source-failure signals).

    - ``Publish(event)``: hand to the queues of all subscribers on ``bus`` (including itself), complete the promises of waiters to wake, and continue immediately — woken
      subscribers run the next time the publisher waits (``CompletePromise(..., yield_to_woken=False)`` — same
      shape as memory's notice store). An emitted signal also stays in the queue of subscribers not waiting at that moment (``offer`` removes the woken waiter
      from the waiters, so signals emitted in succession are queued in that subscriber's queue, and ``WaitForEvents`` after waking receives them all). If the turn were yielded to the woken subscriber
      on every signal, the signal-source task (receiving notices) would stall at every emit until the end of the subscriber's one round,
      and the subscriber receives only one of the accumulated signals per round (the dispatcher handled only one event per round,
      and a conversation's input waited 2-3 minutes behind a swarm of LeaseReturned).
    - ``WaitForEvent(*types)``: take from its own queue. If none, wait via the inner promise (``CreatePromise``, ``Wait``).
      If ``types`` contains a type outside the subscription types, ``ValueError`` (an unsatisfiable wait = a wiring mistake, named on the spot).
    - ``WaitForEvents(*types)``: if its own queue has matching signals, answer with all of them as a tuple in arrival order. If none, wait for one via a promise as ``WaitForEvent`` does,
      and after waking add the rest in the queue at that moment and answer (never answers an empty tuple). The outside-type check is the same.
    - ``CountQueued(*types)``: answer how many signals matching ``types`` wait in its own queue, and take none. The queue has no
      bound, so this is how a receiver reads its backlog (doeff-stream's ``fold`` measures with it). The outside-type check is the same.

    The subscriber name, queue and ``bus`` appear only in this construction's arguments, not in the Program. There is no ack and no cursor.
    """
    queue = bus.subscribe(
        subscriber, (*event_types, StopArrived, SourceFailed) if event_types else ()
    )

    def outside_error(wanted: tuple[type, ...]) -> ValueError | None:
        """A ``ValueError`` naming types in ``wanted`` that are outside the subscription types, if any. ``None`` otherwise (the check shared by the two waits)."""
        outside = queue.outside(wanted)
        if not outside:
            return None
        names = ", ".join(t.__name__ for t in outside)
        subscribed = ", ".join(t.__name__ for t in queue.event_types) or "none"
        return ValueError(
            f"subscriber {subscriber!r} waited for a type outside its subscription: {names} "
            f"(subscription types: {subscribed}) — signals of outside types are not queued, so the wait is never satisfied"
        )

    @do
    def handler(
        effect: WaitForEventEffect | WaitForEventsEffect | PublishEffect | CountQueuedEffect, k: K
    ) -> "EffectGenerator[object]":
        """Answer this subscriber's Program's Publish, WaitForEvent, WaitForEvents and CountQueued with the bus and its own queue. Because of the effect type annotation, the VM skips this handler for other
        effects (doeff-vm's _effect_types.py — it does not add steps where all body effects pass through here via Pass)."""
        match effect:
            case WaitForEventEffect(event_types=wanted) | WaitForEventsEffect(event_types=wanted):
                # The two waits take the same path (take one from the queue; if none, wait for one via a promise). Do not factor the path out into a helper Program — the helper adds an extra level on every wait
                # and adds 3 steps to each woken wait (tests/test_wait_steps.py). The only difference is the shape of the answer.
                error = outside_error(wanted)
                if error is not None:
                    return (yield ResumeThrow(k, error))
                found = queue.take(wanted)
                if isinstance(found, Empty):
                    promise: Promise[object] = yield CreatePromise()
                    queue.add_waiter(wanted, promise)
                    try:
                        found = yield Wait(promise.future)
                    finally:
                        queue.remove_waiter(promise)
                # WaitForEvents adds the rest in the queue at that moment, in arrival order, to the one taken and answers (the one taken is first).
                many = isinstance(effect, WaitForEventsEffect)
                return (yield Resume(k, (found, *queue.take_all(wanted)) if many else found))
            case PublishEffect(event=event):
                for promise in bus.offer(event):
                    # The emitter keeps the turn and continues (see the Publish item in the docstring — the queue keeps signals, so signals emitted in succession are not lost).
                    yield CompletePromise(promise, event, yield_to_woken=False)
                return (yield Resume(k, None))
            case CountQueuedEffect(event_types=wanted):
                error = outside_error(wanted)
                if error is not None:
                    return (yield ResumeThrow(k, error))
                return (yield Resume(k, queue.count(wanted)))
            case _:
                yield Pass(effect, k)

    return _program_handler(handler)


__all__ = [
    "EMPTY",
    "Empty",
    "EventBus",
    "SubscriberQueue",
    "event_handler",
    "subscribed_event_handler",
]
