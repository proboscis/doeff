"""The parts the ``event-loop`` macro calls in its expansion (agora-redesign #3080).

The macro (``doeff_events/macros.hy``) writes the loop of a worker that waits for events: read the state,
then wait for the next event of the types its clauses name or for the stop signal, whichever comes first,
and run the clause of what came. These parts own the waiting so every loop waits the same way:

- ``begin_watch`` asks ``StopRequested`` once and, when no stop is requested yet, spawns one ``AwaitStop``
  wait for the loop's whole life (the loop notices a stop without waking up to ask — agora-redesign #2205).
- ``next_event`` races one ``WaitForEvent`` of the clause types against that stop wait, withdraws the event
  wait when the stop comes first, and records each event it hands to a clause (``slog``).
- ``end_watch`` withdraws the stop wait when the loop ends.

Design: agora-controllers docs/design/event-waits/README.md section 4.
"""

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import hy  # noqa: F401 — stop_signal_effects below is a Hy module
from doeff_core_effects.effects import slog
from doeff_core_effects.scheduler import Cancel, Race, Spawn, Task, TaskCancelledError, Wait
from doeff_core_effects.stop_signal_effects import AwaitStop, StopRequested

from doeff import do
from doeff_events.effects import WaitForEvent

T = TypeVar("T")

@dataclass(frozen=True)
class StopArrived:
    """The stop signal came first: ``reason`` is the stop reason ``AwaitStop`` answered."""

    reason: str


@dataclass(frozen=True)
class LoopStop(Generic[T]):
    """A clause ended the loop: ``value`` is what the ``event-loop`` form evaluates to.

    The macro writes it for ``(stop value)`` in the last-value position of a clause body.
    """

    value: T


Watch = Task[StopArrived] | StopArrived


@do
def _stop_arrived() -> Generator[Any, Any, StopArrived]:
    reason = yield AwaitStop()
    return StopArrived(reason)


@do
def begin_watch() -> Generator[Any, Any, Watch]:
    """Ask once whether a stop is already requested; otherwise spawn the loop's one ``AwaitStop`` wait."""

    first = yield StopRequested()
    if first is not None:
        return StopArrived(first)
    watcher = yield Spawn(_stop_arrived())
    return watcher


@do
def _next_of(event_types: tuple[type[Any], ...]) -> Generator[Any, Any, object]:
    event = yield WaitForEvent(*event_types)
    return event


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
    before it waits (both would be ready at the same moment, and the race would hand the queued event to a clause
    first)."""

    if isinstance(watch, StopArrived):
        return watch
    raised = yield StopRequested()
    if raised is not None:
        return StopArrived(raised)
    waiting = yield Spawn(_next_of(event_types))
    won: object = None
    try:
        won = yield Race(waiting, watch)
    finally:
        if not isinstance(won, event_types):
            yield _withdrawn(waiting)
    if isinstance(won, StopArrived):
        return won
    yield slog("event-loop", event=type(won).__name__)
    return won


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
