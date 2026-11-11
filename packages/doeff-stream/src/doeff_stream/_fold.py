"""The runtime: ``fold(step, initial, merge(sources...))``.

The fold waits for one input at a time, gives it to the step and carries out the effects the step answers. A ``Run``
starts two tasks: the runner (the program) and its watcher. The watcher waits for the runner and publishes how it
ended — value, exception or cancel — as one ``RunEnded`` on the bus, which reaches the fold through its subscriber
queue like any other input. So the end of every run is an input, whichever way it ended, and the fold itself never
waits for a run's answer: it only waits for the next input.

The watcher is never cancelled by the fold (``CancelRun`` cancels the runner), so it is there to publish the end even
of a runner cancelled before it started. A runner that ended with ``TaskCancelledError`` is ``Cancelled`` only when the
step cancelled it; one that raised it on its own (a wait on another cancelled task) is ``Raised``.

An ``Arm`` / ``Disarm`` starts no task: the fold performs ``ArmTimer`` / ``DisarmTimer`` itself, under a ``_FoldTimer``
tag (the fold's ``origin`` and the step's tag). Its ``TimerFired`` reaches every subscriber of the bus; the fold takes
those of its own origin, gives the step ``TimerFired(tag)``, and reads past the other folds' ones. A ``TimerFired``
whose tag is not a ``_FoldTimer`` (a ``Run`` of ``ArmTimer``) reaches the step as it is.

The fold installs one handler, around a step's Program only: the one that refuses every effect (``step_answer``).
The caller installs the bus handler subscribed to ``inputs.event_types`` (``subscribed_event_handler``) and, for
``timers()``, the timer handler inside it — under ``scheduled``.

Under a caller's handler of ``FoldMeasured`` (``_measure.py``), the fold performs one after each input it gave the step:
the queue behind that input, the runs running and the step's time. It reads them with ``CountQueued`` (the bus handler)
and ``GetMonotonic`` (the clock handler). Without such a handler it measures nothing.
"""

from collections.abc import Callable, Generator, Hashable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from doeff_core_effects import Cancel, Spawn, Task, TaskCancelledError, Wait, shielded
from doeff_events import ArmTimer, CountQueued, DisarmTimer, Publish, TimerFired, WaitForEvent
from doeff_time import GetMonotonic

from doeff import Program, do
from doeff_stream._measure import FoldMeasured, caller_measures
from doeff_stream._source import Inputs, RunEnded
from doeff_stream._step import (
    A,
    Answered,
    Arm,
    Cancelled,
    CancelRun,
    Disarm,
    Finish,
    Next,
    Raised,
    Run,
    RunEnd,
    checked_next,
    is_program_answer,
    step_answer,
)

S = TypeVar("S")


@dataclass(frozen=True)
class _Running:
    """One run the fold started and whose end has not reached the step: its two tasks, and whether the step
    cancelled it."""

    runner: Task[object]
    watcher: Task[None]
    cancelled: bool = False


@dataclass(frozen=True)
class _StepClock:
    """What the fold read before a measured step: the queue behind its input and the clock."""

    queued: int
    began: float


@dataclass(frozen=True)
class _FoldTimer:
    """The tag the timer handler holds for the ``Arm(tag, …)`` of the fold ``origin``: one fold's tags never meet
    another's in the handler's queue, and its ``TimerFired`` tells which fold it is for."""

    origin: object
    tag: Hashable


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


@dataclass(frozen=True)
class _Foreign:
    """What ``_own_input`` answers for an input of another fold on the same bus: the fold reads past it."""


_FOREIGN = _Foreign()


def _own_input(
    event: object, origin: object, running: dict[Hashable, _Running], armed: set[Hashable]
) -> object:
    """The input the step is given for the bus event ``event`` of the fold ``origin``, or ``_FOREIGN``.

    A run end or a deadline of this fold leaves the fold's tables (``running`` / ``armed``) here.
    """
    match event:
        case RunEnded(origin=other) if other is not origin:
            return _FOREIGN
        case RunEnded(key=key):
            return _as_step_input(event, running.pop(key))
        case TimerFired(tag=_FoldTimer(origin=other)) if other is not origin:
            return _FOREIGN
        case TimerFired(tag=_FoldTimer(tag=tag)):
            armed.discard(tag)
            return TimerFired(tag)
        case _:
            # An event of a source, or a TimerFired of a Run: the step reads it as it is.
            return event


@do
def _start_clock(inputs: Inputs) -> Generator[Any, Any, _StepClock]:
    """Read the queue behind the input just taken, then the clock — right before the step."""
    queued: int = yield CountQueued(*inputs.event_types)
    began: float = yield GetMonotonic()
    return _StepClock(queued, began)


@do
def _set_deadline(
    effect: Arm | Disarm, origin: object, inputs: Inputs, armed: set[Hashable]
) -> Generator[Any, Any, None]:
    """Hand the step's ``Arm`` / ``Disarm`` to the timer handler under the fold's own tag, and keep ``armed``."""
    match effect:
        case Arm(tag=tag, at=at):
            if TimerFired not in inputs.event_types:
                raise ValueError(
                    f"Arm of the tag {tag!r} in a fold without timers() among its sources: "
                    "its TimerFired would never reach the step"
                )
            yield ArmTimer(_FoldTimer(origin, tag), at)
            armed.add(tag)
        case Disarm(tag=tag):
            if tag in armed:
                yield DisarmTimer(_FoldTimer(origin, tag))
                armed.discard(tag)


@do
def _close(
    origin: object, running: dict[Hashable, _Running], armed: set[Hashable]
) -> Generator[Any, Any, None]:
    """Disarm the deadlines still set, cancel every run still running and wait until each has ended (its watcher
    published its end).

    The wait has no deadline: the fold has no clock. A run that does not end once cancelled (it waits inside
    ``shielded``, or never waits) holds the fold here — a run that waits on the outside world bounds its own wait.
    """
    for tag in armed:
        yield DisarmTimer(_FoldTimer(origin, tag))
    armed.clear()
    for entry in running.values():
        yield Cancel(entry.runner)
    for entry in running.values():
        yield Wait(entry.watcher)
    running.clear()


@do
def fold(
    step: Callable[[S, object], "Next[S, A] | Program[Next[S, A]]"], initial: S, inputs: Inputs
) -> Generator[Any, Any, A]:
    """Run ``step`` over the inputs of ``inputs`` from ``initial`` until it answers ``Finish(value)``; answer ``value``.

    The answer's type is the ``A`` of the step's ``Next[S, A]`` (the type its ``Finish`` carries — ``object`` when the
    step answers a bare ``Next[S]``).

    A step that answers a Program is evaluated under the handler that refuses every effect: its value is the
    ``Next``, and an effect it performs raises ``StepYieldedEffectError`` here like any error of the step.

    Each input is one event of ``inputs.event_types`` (in the order the bus queued them), the end of a run
    (``Answered`` / ``Raised`` / ``Cancelled``) or the deadline of an ``Arm`` (``TimerFired(tag)``). If the step raises,
    or the fold's task is cancelled, the deadlines still set are disarmed, the runs still running are cancelled, the
    fold waits until each has ended, and the error goes on.

    Under a handler annotated with ``FoldMeasured``, the fold performs one ``FoldMeasured`` after each input it gave
    the step (its effects carried out); under none it measures nothing.
    """
    origin = object()
    # The fold's own table of the runs it started (key -> tasks): its loop state, never shared.
    running: dict[Hashable, _Running] = {}
    # The tags of the step's deadlines set and not yet fired or disarmed: the fold disarms them when it ends.
    armed: set[Hashable] = set()
    state = initial
    measured: bool = yield caller_measures()
    try:
        while True:
            event = yield WaitForEvent(*inputs.event_types)
            event = _own_input(event, origin, running, armed)
            if event is _FOREIGN:
                continue
            clock = (yield _start_clock(inputs)) if measured else None
            answer = step(state, event)
            if is_program_answer(answer):
                answer = yield step_answer(step, answer)
            answer = checked_next(answer, frozenset(running))
            step_seconds = (yield GetMonotonic()) - clock.began if clock is not None else 0.0
            state = answer.state
            finish: Finish[A] | None = None
            for effect in answer.effects:
                match effect:
                    case Run(key=key, program=program, priority=priority):
                        # The watcher too: a watcher below its runner would hold the end behind the lower work.
                        runner = yield Spawn(program, priority=priority)
                        watcher = yield Spawn(_watch(origin, key, runner), priority=priority)
                        running[key] = _Running(runner, watcher)
                    case CancelRun(key=key):
                        entry = running.get(key)
                        if entry is not None and not entry.cancelled:
                            yield Cancel(entry.runner)
                            running[key] = replace(entry, cancelled=True)
                    case Arm() | Disarm():
                        yield _set_deadline(effect, origin, inputs, armed)
                    case Finish():
                        yield _close(origin, running, armed)
                        finish = effect
                        break
            if clock is not None:
                yield FoldMeasured(clock.queued, len(running), step_seconds)
            if finish is not None:
                return finish.value
    except Exception:
        # Shielded: in a fold whose own task is being cancelled, every wait outside a shield raises again.
        yield shielded(_close(origin, running, armed))
        raise
