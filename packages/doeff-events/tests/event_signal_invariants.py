"""Check of the invariants a signal handler keeps (design #3072).

Each function takes how to assemble a handler (``SignalWorld``) as an argument, runs a scenario, and checks the invariant. Both memory's
``subscribed_event_handler`` and an assembly that places inside it a source emitting record-service changes as signals
call the same functions with their own assembly. Placed in a file whose name does not start with ``test_`` so pytest does not collect it directly.

A signal carries only "where it changed" (a record key), and the receiver re-reads the records to build state. The scenario's records are
represented by ``RecordBook`` (an index of key -> version). Do not wait on time; the order between tasks is decided by promises (``CreatePromise``, ``Wait``).

Widened in 3 points so that the #3077 assembly can also be called (the meaning of memory's assembly is unchanged):

- The assembly is a Program (the answer of ``subscribe`` = a Program that returns the handler) — the record-service source reads the start position of the subscription from the records
  (an effect), so the assembly itself emits effects. Memory's assembly just wraps with ``Pure``.
- The field of the signal ``Changed`` is ``keys`` (a tuple of changed places), and mapping the scenario's keys to that world's "places" is ``SignalWorld.row``
  (memory does not map — the default). In the record-service world, a place is a record row (table name and key).
- The second signal of (b) is emitted after the receiver has received the first — the record-service source merges signals of the same type within one change bundle,
  so two emitted in succession can become one signal (write scenarios that deliver a duplicate to the receiver twice so that they span bundles).
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, final

from doeff_core_effects import CompletePromise, CreatePromise, Promise, Spawn, Wait
from doeff_events import Publish, WaitForEvent

from doeff import do

if TYPE_CHECKING:
    from doeff import EffectGenerator, Program
    from doeff.program import ProgramHandler


class Subscribe(Protocol):
    """A Program that builds the handler of subscriber ``subscriber`` (the subscription starts when it is built). ``event_types=()`` only emits."""

    def __call__(
        self, subscriber: str, event_types: tuple[type, ...] = (), /
    ) -> Program[ProgramHandler]: ...


def _same_key(key: str) -> Hashable:
    """Use the scenario's key as the signal's place as is (the default of memory's world)."""
    return key


@dataclass(frozen=True)
class SignalWorld:
    """How to assemble and run signal handlers, received by the invariant checks.

    ``subscribe`` = a Program that builds a subscriber's handler. Handlers of the same world exchange signals with one another.
    ``run`` = runs a Program under the foundation (scheduler, etc.) that the handler needs and returns the value.
    ``row`` = scenario key -> the place that the world's signal carries (an element of ``Changed.keys`` and a key of ``RecordBook``).
    """

    subscribe: Subscribe
    run: Callable[[Program[object]], object]
    row: Callable[[str], Hashable] = _same_key


@dataclass(frozen=True)
class Changed:
    """A signal saying places ``keys`` changed (it does not carry a state diff)."""

    keys: tuple[Hashable, ...]


@dataclass(frozen=True)
class Unrelated:
    """A signal outside the subscription types."""

    key: str


@final
class RecordBook:
    """The scenario's records: place -> index of versions. The receiver re-reads here at the signal's place to build state."""

    __slots__ = ("_versions",)

    def __init__(self, versions: dict[Hashable, int]) -> None:
        self._versions: Final[dict[Hashable, int]] = dict(versions)

    def read(self, key: Hashable) -> int:
        """For the receiver to rebuild state, read the current version of a place."""
        return self._versions[key]

    def write(self, key: Hashable, version: int) -> None:
        """The writer changes the records (the signal is emitted separately afterward)."""
        self._versions[key] = version


@dataclass(frozen=True)
class GapOutcome:
    """Result of the read -> process -> wait scenario."""

    version_before: int
    signal: Changed
    version_after: int


@do
def _run_as(
    world: SignalWorld, subscriber: str, event_types: tuple[type, ...], program: Program[object]
) -> EffectGenerator[object]:
    """Build the ``subscriber`` handler and run ``program`` under it and return the answer."""
    handler: ProgramHandler = yield world.subscribe(subscriber, event_types)
    return (yield handler(program))


def check_signal_between_read_and_wait_is_kept(world: SignalWorld) -> None:
    """(a) Do not lose a signal another task emits between reading the state -> processing -> ``WaitForEvent``."""
    outcome = run_read_process_wait(world)
    job = world.row("job")
    assert outcome == GapOutcome(version_before=1, signal=Changed((job,)), version_after=2), outcome


def run_read_process_wait(world: SignalWorld) -> GapOutcome:
    """A scenario where the receiver reads the records, and while it processes, the writer writes the records and emits a signal, and then the receiver waits for a signal.

    With a handler that discards signals while no waiter exists, the receiver is not woken and ends at a scheduler dead end.
    """
    job = world.row("job")
    book = RecordBook({job: 1})

    @do
    def worker(read_done: Promise[object], written: Promise[object]) -> EffectGenerator[GapOutcome]:
        """Receiver: read the records, process, then wait for a signal and re-read the records."""
        version_before = book.read(job)
        yield CompletePromise(read_done, None)
        # During processing the writer writes the records and finishes emitting the signal.
        yield Wait(written.future)
        signal = yield WaitForEvent(Changed)
        return GapOutcome(version_before, signal, book.read(signal.keys[0]))

    @do
    def writer(read_done: Promise[object], written: Promise[object]) -> EffectGenerator[None]:
        """Writer: after the receiver has finished reading (during processing), write the records and emit a signal."""
        yield Wait(read_done.future)
        book.write(job, 2)
        yield Publish(Changed((job,)))
        yield CompletePromise(written, None)

    @do
    def main() -> EffectGenerator[GapOutcome]:
        """Start the receiver's subscription, then run the receiver and writer in separate tasks and return the receiver's result."""
        read_done: Promise[object] = yield CreatePromise()
        written: Promise[object] = yield CreatePromise()
        receiving: ProgramHandler = yield world.subscribe("worker", (Changed,))
        sending: ProgramHandler = yield world.subscribe("writer")
        worker_task = yield Spawn(receiving(worker(read_done, written)))
        writer_task = yield Spawn(sending(writer(read_done, written)))
        outcome: GapOutcome = yield Wait(worker_task)
        yield Wait(writer_task)
        return outcome

    result = world.run(main())
    assert isinstance(result, GapOutcome), result
    return result


def check_duplicate_signal_gives_same_state(world: SignalWorld) -> None:
    """(b) Even if the same signal arrives twice the receiver's result is the same — the receiver re-reads the records at the signal's place to build state (idempotent)."""
    job = world.row("job")
    book = RecordBook({job: 1})

    @do
    def worker(ready: Promise[object], first_seen: Promise[object]) -> EffectGenerator[tuple[int, ...]]:
        """Receiver: receive a signal twice, and return the state built by re-reading the records each time."""
        yield CompletePromise(ready, None)
        first: Changed = yield WaitForEvent(Changed)
        state_after_first = book.read(first.keys[0])
        yield CompletePromise(first_seen, None)
        second: Changed = yield WaitForEvent(Changed)
        return (state_after_first, book.read(second.keys[0]))

    @do
    def writer(ready: Promise[object], first_seen: Promise[object]) -> EffectGenerator[None]:
        """Writer: change the records only once and emit the same signal twice (the second after the receiver has received the first)."""
        yield Wait(ready.future)
        book.write(job, 2)
        yield Publish(Changed((job,)))
        yield Wait(first_seen.future)
        yield Publish(Changed((job,)))

    @do
    def main() -> EffectGenerator[tuple[int, ...]]:
        """Run the receiver and writer in separate tasks and return the list of the receiver's states."""
        ready: Promise[object] = yield CreatePromise()
        first_seen: Promise[object] = yield CreatePromise()
        worker_task = yield Spawn(_run_as(world, "worker", (Changed,), worker(ready, first_seen)))
        writer_task = yield Spawn(_run_as(world, "writer", (), writer(ready, first_seen)))
        states: tuple[int, ...] = yield Wait(worker_task)
        yield Wait(writer_task)
        return states

    states = world.run(main())
    assert states == (2, 2), states


def check_wait_outside_subscription_is_rejected(world: SignalWorld) -> None:
    """(c) Waiting for a type outside the subscription raises ``ValueError`` — the message names the subscriber and the offending type. A wait by an emit-only subscriber is the same."""

    @do
    def wait_unrelated() -> EffectGenerator[object]:
        """Wait for a signal outside the subscription types (a wiring mistake)."""
        return (yield WaitForEvent(Unrelated))

    for subscriber, event_types in (("worker", (Changed,)), ("writer", ())):
        try:
            world.run(_run_as(world, subscriber, event_types, wait_unrelated()))
        except ValueError as error:
            message = str(error)
            assert subscriber in message, message
            assert "Unrelated" in message, message
        else:
            raise AssertionError(f"{subscriber} was able to wait for Unrelated, which is outside its subscription types")


def check_signal_before_subscription_is_not_kept(world: SignalWorld) -> None:
    """(d) A signal emitted before the subscription starts is not queued — the subscription queues only signals after that point."""

    @do
    def publish(key: str) -> EffectGenerator[None]:
        """Writer: emit one signal with key ``key``."""
        yield Publish(Changed((world.row(key),)))

    @do
    def main() -> EffectGenerator[Changed]:
        """Finish emitting one signal, then start the subscription, emit the next signal, and return the first signal the receiver receives."""
        yield Wait((yield Spawn(_run_as(world, "writer", (), publish("early")))))
        # Start the subscription after finishing emitting "early".
        receive: ProgramHandler = yield world.subscribe("worker", (Changed,))
        yield Wait((yield Spawn(_run_as(world, "writer", (), publish("late")))))
        signal: Changed = yield receive(_wait_changed())
        return signal

    received = world.run(main())
    assert received == Changed((world.row("late"),)), received


def check_resubscribe_discards_previous_queue(world: SignalWorld) -> None:
    """(d) Rebuilding with the same name discards the previous queue and starts fresh from signals after the rebuild."""

    @do
    def publish(key: str) -> EffectGenerator[None]:
        """Writer: emit one signal with key ``key``."""
        yield Publish(Changed((world.row(key),)))

    @do
    def main() -> EffectGenerator[Changed]:
        """Rebuild with the same name while signals remain in the previous queue, and return the first signal the rebuilt receiver receives."""
        yield world.subscribe("worker", (Changed,))
        # They remain in the previous queue, and the receiver crashes without reading them.
        yield Wait((yield Spawn(_run_as(world, "writer", (), publish("before-restart")))))
        restarted: ProgramHandler = yield world.subscribe("worker", (Changed,))
        yield Wait((yield Spawn(_run_as(world, "writer", (), publish("after-restart")))))
        signal: Changed = yield restarted(_wait_changed())
        return signal

    received = world.run(main())
    assert received == Changed((world.row("after-restart"),)), received


@do
def _wait_changed() -> EffectGenerator[Changed]:
    """Receiver: receive one Changed signal and return it."""
    signal: Changed = yield WaitForEvent(Changed)
    return signal


Invariant = Callable[[SignalWorld], None]
"""One invariant check: take a world and raise AssertionError if it is broken."""

INVARIANTS: Final[tuple[Invariant, ...]] = (
    check_signal_between_read_and_wait_is_kept,
    check_duplicate_signal_gives_same_state,
    check_wait_outside_subscription_is_rejected,
    check_signal_before_subscription_is_not_kept,
    check_resubscribe_discards_previous_queue,
)
