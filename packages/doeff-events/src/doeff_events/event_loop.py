"""The parts the ``event-loop`` macro calls in its expansion (agora-redesign #3080).

The macro (``doeff_events/macros.hy``) writes the loop of a worker that waits for events: read the state,
then wait for the next event of the types its clauses name or for the stop signal, whichever comes first,
and run the clause of what came. These parts own the waiting so every loop waits the same way:

- ``begin_watch`` asks ``StopRequested`` once and, when no stop is requested yet, spawns one stop watcher for the
  loop's whole life: it waits for ``AwaitStop`` and publishes ``StopArrived`` on the loop's bus (the loop notices a
  stop without waking up to ask — agora-redesign #2205).
- ``next_event`` waits for one ``WaitForEvent`` of the clause types and ``StopArrived`` together and records each
  event it hands to a clause (``slog``). Nothing is spawned, raced or withdrawn per wake — an earlier form raced a
  spawned event wait against the stop wait on every wake (about 340 VM steps per wake — agora-redesign #3112).
- ``end_watch`` withdraws the stop watcher when the loop ends.

The bus handler must deliver ``StopArrived`` to the loop: ``subscribed_event_handler`` always subscribes it.
Hand-written loops that wait for events use the same three parts.

Design: agora-controllers docs/design/event-waits/README.md section 4.
"""

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import hy  # noqa: F401 — stop_signal_effects below is a Hy module
from doeff_core_effects.effects import slog
from doeff_core_effects.scheduler import Cancel, Spawn, Task, TaskCancelledError, Wait
from doeff_core_effects.stop_signal_effects import AwaitStop, StopRequested

from doeff import do
from doeff_events.effects import Publish, WaitForEvent

# StopArrived is defined in doeff_events.effects; the explicit re-export keeps it importable from here for the loops
# written before it moved.
from doeff_events.effects import StopArrived as StopArrived

T = TypeVar("T")


@dataclass(frozen=True)
class LoopStop(Generic[T]):
    """A clause ended the loop: ``value`` is what the ``event-loop`` form evaluates to.

    The macro writes it for ``(stop value)`` in the last-value position of a clause body.
    """

    value: T


Watch = Task[None] | StopArrived


@do
def _stop_announced() -> Generator[Any, Any, None]:
    """Wait for the stop signal and publish it on the loop's bus as ``StopArrived`` (the loop's one stop watcher)."""

    reason = yield AwaitStop()
    yield Publish(StopArrived(reason))
    return None


@do
def begin_watch() -> Generator[Any, Any, Watch]:
    """Ask once whether a stop is already requested; otherwise spawn the loop's one stop watcher."""

    first = yield StopRequested()
    if first is not None:
        return StopArrived(first)
    watcher = yield Spawn(_stop_announced())
    return watcher


@do
def _withdraw(task: Task[Any]) -> Generator[Any, Any, None]:
    yield Cancel(task)
    try:
        yield Wait(task)
    except TaskCancelledError:
        # The withdrawn task's own cancellation — the outcome we asked for, not a failure of this program.
        return None
    return None


@do
def _withdrawn(task: Task[Any]) -> Generator[Any, Any, None]:
    """Cancel ``task`` and wait until it has unwound, in a task of its own: a ``TaskCancelledError`` that
    reaches this program while it waits is this program's own cancellation and is raised again."""

    cleaning = yield Spawn(_withdraw(task))
    try:
        yield Wait(cleaning)
    except TaskCancelledError:
        yield Wait(cleaning)
        raise
    return None


@do
def next_event(event_types: tuple[type[Any], ...], watch: Watch) -> Generator[Any, Any, object]:
    """The next event of ``event_types``, or ``StopArrived`` when the stop signal comes first.

    ``watch`` is what ``begin_watch`` answered: a stop asked before the loop is answered at once. A stop raised while
    the previous event was being processed wins over an event already queued: the loop asks ``StopRequested`` once
    before it waits (the queue would hand the event published first to the loop first). One ``WaitForEvent`` waits
    for the clause types and the watcher's ``StopArrived`` together — no task per wake."""

    if isinstance(watch, StopArrived):
        return watch
    raised = yield StopRequested()
    if raised is not None:
        return StopArrived(raised)
    came = yield WaitForEvent(*event_types, StopArrived)
    if isinstance(came, StopArrived):
        return came
    yield slog("event-loop", event=type(came).__name__)
    return came


@do
def end_watch(watch: Watch) -> Generator[Any, Any, None]:
    """Withdraw the loop's stop wait (a no-op when the stop already came or was asked before the loop)."""

    if isinstance(watch, Task):
        yield _withdrawn(watch)
    return None


def stopped_value(result: "LoopStop[T] | T") -> T:
    """The value of a clause that ends the loop: ``(stop v)`` gives ``v``, any other value is itself."""

    if isinstance(result, LoopStop):
        return result.value
    return result
