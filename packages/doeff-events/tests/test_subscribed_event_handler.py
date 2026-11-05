"""Check of memory's per-subscriber queues (subscribed_event_handler) — the invariants call the functions of event_signal_invariants with memory's
assembly."""

from collections.abc import Hashable
from dataclasses import dataclass

import pytest
from doeff_core_effects import Promise, SchedulerDeadlockError, Spawn, Wait
from doeff_events import (
    EventBus,
    Publish,
    TimerFired,
    WaitForEvent,
    event_handler,
    subscribed_event_handler,
)
from doeff_events._handlers.memory import EMPTY, SubscriberQueue
from event_signal_invariants import (
    INVARIANTS,
    Changed,
    Invariant,
    SignalWorld,
    Unrelated,
    run_read_process_wait,
)
from events_test_support import run_scheduled

from doeff import EffectGenerator, Program, Pure, do
from doeff.program import ProgramHandler


def memory_world() -> SignalWorld:
    """One world = one new ``EventBus``."""
    bus = EventBus()

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program[ProgramHandler]:
        """Start the subscription on ``bus`` when called, and make a Program that answers the built handler as is (memory's assembly emits no effects)."""
        return Pure(subscribed_event_handler(bus, subscriber, event_types))

    return SignalWorld(subscribe=subscribe, run=run_scheduled)


def legacy_world() -> SignalWorld:
    """Make one original ``event_handler()`` and have all subscribers use the same one (subscriber names and types are ignored)."""
    shared = event_handler()

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program[ProgramHandler]:
        """Return the same handler for any subscriber — this mirrors as is that the original handler has no notion of subscription."""
        return Pure(shared)

    return SignalWorld(subscribe=subscribe, run=run_scheduled)


@pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda invariant: invariant.__name__)
def test_memory_subscribed_handler_keeps_invariant(invariant: Invariant) -> None:
    """Check the shared invariants one by one with memory's assembly."""
    invariant(memory_world())


def test_legacy_event_handler_drops_signal_between_read_and_wait() -> None:
    # Proof that the meaning is unchanged: the original event_handler discards signals while no waiter exists, so in the same scenario the receiver is not woken.
    with pytest.raises(SchedulerDeadlockError):
        run_read_process_wait(legacy_world())


def test_queue_wakes_waiters_in_arrival_order_and_keeps_order() -> None:
    queue = SubscriberQueue((Changed,))
    first, second = Promise[object](1), Promise[object](2)
    queue.add_waiter((Changed,), first)
    queue.add_waiter((Changed,), second)

    assert queue.offer(Changed(("a",))) is first
    assert queue.offer(Unrelated("x")) is None
    assert queue.offer(Changed(("b",))) is second
    assert queue.offer(Changed(("c",))) is None
    # Signals of the same type while no waiter exists merge into one, and the places stay in arrival order.
    assert queue.offer(Changed(("d",))) is None
    assert queue.take((Changed,)) == Changed(("c", "d"))
    assert queue.take((Changed,)) is EMPTY


def test_queue_take_skips_types_not_wanted() -> None:
    queue = SubscriberQueue((Changed, Unrelated))
    assert queue.offer(Unrelated("x")) is None
    assert queue.offer(Changed(("a",))) is None

    assert queue.take((Changed,)) == Changed(("a",))
    assert queue.take((Changed,)) is EMPTY
    assert queue.take((Unrelated,)) == Unrelated("x")


def test_queue_removed_waiter_is_not_woken() -> None:
    queue = SubscriberQueue((Changed,))
    promise = Promise[object](1)
    queue.add_waiter((Changed,), promise)
    queue.remove_waiter(promise)

    assert queue.offer(Changed(("a",))) is None
    assert queue.take((Changed,)) == Changed(("a",))


def test_wait_for_subtype_of_subscribed_type_is_inside() -> None:
    class Narrow(Changed):
        pass

    queue = SubscriberQueue((Changed,))
    assert queue.outside((Narrow, Changed)) == ()
    assert queue.outside((Unrelated, object)) == (Unrelated, object)


# Merge signals of the same type accumulated while no waiter exists into one (design #3072, section 3, item 5).
# If offer is reverted to always appending at the tail, the merging scenarios (the 1st and 3rd) go red.


@dataclass(frozen=True)
class Moved:
    """A signal with places ``keys`` of a type different from ``Changed``."""

    keys: tuple[Hashable, ...]


@dataclass(frozen=True)
class EndOfQueue:
    """A signal the receiver emits as the end-of-queue marker of its own queue (it has no ``keys``, so it is not merged)."""


def received_after_quiet_spell(published: tuple[object, ...], waited: tuple[type, ...]) -> tuple[object, ...]:
    """The list of signals the receiver received until its queue became empty, after the writer finished emitting ``published`` while the receiver was not waiting.

    The receiver stacks one ``WaitForEvent`` at a time and stops at the end-of-queue marker (``EndOfQueue``, which it emits at the tail itself).
    """
    bus = EventBus()
    receiving = subscribed_event_handler(bus, "worker", (*waited, EndOfQueue))
    sending = subscribed_event_handler(bus, "writer")

    @do
    def publish_all() -> EffectGenerator[None]:
        """Writer: emit ``published`` in order (the receiver is not waiting yet)."""
        for event in published:
            yield Publish(event)

    @do
    def drain(received: tuple[object, ...]) -> EffectGenerator[tuple[object, ...]]:
        """Receiver: receive the next signal, and return the signals up to the end-of-queue marker in arrival order."""
        event: object = yield WaitForEvent(*waited, EndOfQueue)
        if isinstance(event, EndOfQueue):
            return received
        return (yield drain((*received, event)))

    @do
    def receive_all() -> EffectGenerator[tuple[object, ...]]:
        """Receiver: emit the end-of-queue marker at the tail of its own queue, then receive up to the marker."""
        yield Publish(EndOfQueue())
        return (yield drain(()))

    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """Wait for the writer to finish emitting, then run the receiver."""
        yield Wait((yield Spawn(sending(publish_all()))))
        return (yield receiving(receive_all()))

    result: tuple[object, ...] = run_scheduled(main())
    assert isinstance(result, tuple), result
    return result


def test_same_type_signals_while_no_one_waits_are_received_in_one_wait() -> None:
    received = received_after_quiet_spell(
        (Changed(("a",)), Changed(("b", "a")), Changed(("c",))), (Changed,)
    )
    # The three signals are received in one WaitForEvent, and the places are combined in arrival order with duplicates removed.
    assert received == (Changed(("a", "b", "c")),), received


def test_timer_fired_is_received_per_tag() -> None:
    # TimerFired, which has no keys, has a different meaning per tag, so it is not merged (a second one with the same tag is also received separately).
    received = received_after_quiet_spell(
        (TimerFired("x"), TimerFired("y"), TimerFired("x")), (TimerFired,)
    )
    assert received == (TimerFired("x"), TimerFired("y"), TimerFired("x")), received


def test_signals_of_other_types_are_received_apart() -> None:
    received = received_after_quiet_spell(
        (Changed(("a",)), Moved(("a",)), Changed(("b",))), (Changed, Moved)
    )
    # Different types are not merged, and the same type merges at the position of the earlier one.
    assert received == (Changed(("a", "b")), Moved(("a",))), received


@dataclass(frozen=True)
class BoardChanged:
    """A signal that has fields other than ``keys``."""

    keys: tuple[Hashable, ...]
    board: str


@dataclass
class MutableChanged:
    """A signal with places ``keys`` that is not frozen."""

    keys: tuple[Hashable, ...]


def test_only_frozen_keyed_signals_with_equal_other_fields_merge() -> None:
    queue = SubscriberQueue((BoardChanged, MutableChanged))
    for event in (
        BoardChanged(("a",), "left"),
        BoardChanged(("b",), "right"),
        BoardChanged(("c",), "left"),
        MutableChanged(("a",)),
        MutableChanged(("b",)),
    ):
        assert queue.offer(event) is None

    # If a field other than keys differs, merging would lose one side's field, so do not merge. A non-frozen dataclass is not merged.
    assert queue.take((BoardChanged,)) == BoardChanged(("a", "c"), "left")
    assert queue.take((BoardChanged,)) == BoardChanged(("b",), "right")
    assert queue.take((MutableChanged,)) == MutableChanged(("a",))
    assert queue.take((MutableChanged,)) == MutableChanged(("b",))
    assert queue.take((BoardChanged, MutableChanged)) is EMPTY
