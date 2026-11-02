"""The runtime: ``fold(step, initial, merge(sources...))``.

The fold waits for one input at a time, gives it to the step and carries out the effects the step answers. A ``Run``
starts two tasks: the runner (the program) and its watcher. The watcher waits for the runner and publishes how it
ended — value, exception or cancel — as one ``RunEnded`` on the bus, which reaches the fold through its subscriber
queue like any other input. So the end of every run is an input, whichever way it ended, and the fold itself never
waits for a run's answer: it only waits for the next input.

The watcher is never cancelled by the fold (``CancelRun`` cancels the runner), so it is there to publish the end even
of a runner cancelled before it started. A runner that ended with ``TaskCancelledError`` is ``Cancelled`` only when the
step cancelled it; one that raised it on its own (a wait on another cancelled task) is ``Raised``.

The fold does not install a handler. The caller installs the bus handler subscribed to ``inputs.event_types``
(``subscribed_event_handler``) and, for ``timers()``, the timer handler inside it — under ``scheduled``.
"""

from collections.abc import Callable, Generator, Hashable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from doeff_core_effects import Cancel, Spawn, Task, TaskCancelledError, Wait, shielded
from doeff_events import Publish, WaitForEvent

from doeff import do
from doeff_stream._source import Inputs, RunEnded
from doeff_stream._step import (
    Answered,
    Cancelled,
    CancelRun,
    Finish,
    Next,
    Raised,
    Run,
    RunEnd,
    checked_next,
)

S = TypeVar("S")


@dataclass(frozen=True)
class _Running:
    """One run the fold started and whose end has not reached the step: its two tasks, and whether the step
    cancelled it."""

    runner: Task[object]
    watcher: Task[None]
    cancelled: bool = False


@do
def _watch(origin: object, key: Hashable, runner: Task[object]) -> Generator[Any, Any, None]:
    """Wait for ``runner`` and publish how it ended. Every exception the wait raises — the runner's own, or
    ``TaskCancelledError`` for a cancelled runner — is caught here, so this publishes exactly once. (Not ``Try``: it
    needs a handler the caller may not have installed, and a watcher that fails to publish is a lost end.)"""
    try:
        value = yield Wait(runner)
    except Exception as error:  # every way the runner fails is one end
        end: RunEnd = Raised(key, error)
    else:
        end = Answered(key, value)
    yield Publish(RunEnded(origin, key, end))


def _as_step_input(ended: RunEnded, running: _Running) -> RunEnd:
    """The input the step is given for ``ended``: the cancel it asked for reads ``Cancelled``."""
    if (
        running.cancelled
        and isinstance(ended.end, Raised)
        and isinstance(ended.end.error, TaskCancelledError)
    ):
        return Cancelled(ended.key)
    return ended.end


@do
def _close(running: dict[Hashable, _Running]) -> Generator[Any, Any, None]:
    """Cancel every run still running and wait until each has ended (its watcher published its end).

    The wait has no deadline: the fold has no clock. A run that does not end once cancelled (it waits inside
    ``shielded``, or never waits) holds the fold here — a run that waits on the outside world bounds its own wait.
    """
    for entry in running.values():
        yield Cancel(entry.runner)
    for entry in running.values():
        yield Wait(entry.watcher)
    running.clear()


@do
def fold(
    step: Callable[[S, object], Next[S]], initial: S, inputs: Inputs
) -> Generator[Any, Any, object]:
    """Run ``step`` over the inputs of ``inputs`` from ``initial`` until it answers ``Finish(value)``; answer ``value``.

    Each input is one event of ``inputs.event_types`` (in the order the bus queued them) or the end of a run
    (``Answered`` / ``Raised`` / ``Cancelled``). If the step raises, or the fold's task is cancelled, the runs still
    running are cancelled, the fold waits until each has ended, and the error goes on.
    """
    origin = object()
    # The fold's own table of the runs it started (key -> tasks): its loop state, never shared.
    running: dict[Hashable, _Running] = {}
    state = initial
    try:
        while True:
            event = yield WaitForEvent(*inputs.event_types)
            if isinstance(event, RunEnded):
                if event.origin is not origin:
                    continue  # another fold's run on the same bus
                event = _as_step_input(event, running.pop(event.key))
            answer = checked_next(step(state, event), frozenset(running))
            state = answer.state
            for effect in answer.effects:
                match effect:
                    case Run(key=key, program=program):
                        runner = yield Spawn(program)
                        watcher = yield Spawn(_watch(origin, key, runner))
                        running[key] = _Running(runner, watcher)
                    case CancelRun(key=key):
                        entry = running.get(key)
                        if entry is not None and not entry.cancelled:
                            yield Cancel(entry.runner)
                            running[key] = replace(entry, cancelled=True)
                    case Finish(value=value):
                        yield _close(running)
                        return value
    except Exception:
        # Shielded: in a fold whose own task is being cancelled, every wait outside a shield raises again.
        yield shielded(_close(running))
        raise
