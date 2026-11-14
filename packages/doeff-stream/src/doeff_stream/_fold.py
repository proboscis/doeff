"""The runtime: ``fold(step, initial, merge(sources...))``.

The fold waits for one input at a time, gives it to the step and carries out the effects the step answers. A ``Run``
starts one task, the runner: it runs the program and publishes how it ended — value or exception — as one
``RunEnded`` on the bus, which reaches the fold through its subscriber queue like any other input. So the end of every
run is an input, whichever way it ended, and the fold itself never waits for a run's answer: it only waits for the
next input.

A ``Run(…, name=…)`` spawns its runner from a frame of that name (``_launcher``), so the scheduler counts the run's
steps under its name and not under the fold.

A ``Run(…, inline=True)`` starts no task: the fold yields its program in its own task where it carries out the effect,
and gives the step its end (``Answered`` / ``Raised``) as the next input, before any input queued on the bus. While it
runs the fold takes no input. A step that holds its inputs while a run is running (its runs go one at a time) saves the
task, the publish and the bus trip of every run this way.

A ``CancelRun`` cancels the runner and starts a watcher, which the fold never cancels: a runner cancelled before it
started, or cancelled again at its publish, publishes nothing, and its watcher publishes its end. A runner that did
publish ends cancelled too (a cancelled task ends cancelled even when it returns), so its watcher publishes a second
end: each ``RunEnded`` carries the mark of its run, and the fold reads past the end of a run whose end it already
took. A runner that ended with ``TaskCancelledError`` is ``Cancelled`` only when the step cancelled it; one that raised
it on its own (a wait on another cancelled task) is ``Raised``.

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
from functools import cache
from types import FunctionType
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
    """One run the fold started and whose end has not reached the step: its runner, the mark its ends carry, its
    priority, and the watcher the step's cancel started (``None`` while the step has not cancelled it)."""

    runner: Task[None]
    mark: object
    priority: int
    watcher: Task[None] | None = None

    @property
    def cancelled(self) -> bool:
        return self.watcher is not None


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
def _reported(
    origin: object, mark: object, key: Hashable, program: "Program[object]"
) -> Generator[Any, Any, None]:
    """The runner: run ``program`` and publish how it ended. Every exception it raises — its own, or
    ``TaskCancelledError`` for a cancel — is caught here, so a runner that reaches its publish publishes once. (Not
    ``Try``: it needs a handler the caller may not have installed, and a runner that fails to publish is a lost end.)"""
    try:
        value = yield program
    except Exception as error:  # every way the program fails is one end
        end: RunEnd = Raised(key, error)
    else:
        end = Answered(key, value)
    yield Publish(RunEnded(origin, mark, key, end))


def _launch(spawn: Spawn) -> Generator[Any, Any, Task[None]]:
    """Yield ``spawn`` and answer its task: the body ``_launcher`` copies under a run's name. A bare generator, not
    ``@do``: the VM runs the wrapper's generator as the frame that yields the ``Spawn``, and the copy's code carries the
    name the scheduler reads there."""
    task: Task[None] = yield spawn
    return task


@cache
def _launcher(name: str) -> Callable[[Spawn], "Program[Task[None]]"]:
    """The ``_launch`` whose code is named ``name``, wrapped in ``@do``: the frame a named ``Run``'s runner is spawned
    from, so the scheduler counts its steps under ``name`` (``Run.name``). One per name — the names are the kinds of
    work a step starts, not one per run."""
    code = _launch.__code__.replace(co_name=name, co_qualname=name)
    return do(FunctionType(code, _launch.__globals__, name))


@do
def _inline(key: Hashable, program: "Program[object]") -> Generator[Any, Any, RunEnd]:
    """Run the inline ``program`` in the fold's task and answer how it ended, as the input the step is given. Every
    exception it raises is its ``Raised`` end, like a runner's (a ``TaskCancelledError`` of the fold's own cancel too:
    the fold's next wait raises the cancel again)."""
    try:
        value = yield program
    except Exception as error:  # every way the program fails is one end
        return Raised(key, error)
    return Answered(key, value)


@do
def _watch_cancelled(
    origin: object, mark: object, key: Hashable, runner: Task[None]
) -> Generator[Any, Any, None]:
    """Wait for the cancelled ``runner`` and publish its end when its task ended cancelled. A runner cancelled before
    it started, or again at its publish, published nothing; one that published ended cancelled all the same, and the
    fold reads past this second end of ``mark``. A runner that returned before the cancel reached it ends with its
    value, and this publishes nothing."""
    try:
        yield Wait(runner)
    except Exception as error:  # the runner catches its program's errors: this is its cancel
        yield Publish(RunEnded(origin, mark, key, Raised(key, error)))


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

    A run end or a deadline of this fold leaves the fold's tables (``running`` / ``armed``) here. The second end of a
    cancelled run (its watcher's, after the runner's own) is read past like another fold's input.
    """
    match event:
        case RunEnded(origin=other) if other is not origin:
            return _FOREIGN
        case RunEnded(key=key, mark=mark):
            entry = running.get(key)
            if entry is None or entry.mark is not mark:
                return _FOREIGN
            del running[key]
            return _as_step_input(event, entry)
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
    """Disarm the deadlines still set, cancel every run still running and wait until each has ended (its runner, and
    the watcher of a run the step cancelled, published what they publish).

    The wait has no deadline: the fold has no clock. A run that does not end once cancelled (it waits inside
    ``shielded``, or never waits) holds the fold here — a run that waits on the outside world bounds its own wait.
    """
    for tag in armed:
        yield DisarmTimer(_FoldTimer(origin, tag))
    for entry in running.values():
        yield Cancel(entry.runner)
    for entry in running.values():
        yield _ended(entry)


@do
def _ended(entry: _Running) -> Generator[Any, Any, None]:
    """Wait until the run ``entry`` has ended, once ``_close`` cancelled it: its watcher if the step cancelled it,
    else its runner, whose wait raises the cancel ``_close`` just made (the runner catches every error of its
    program)."""
    if entry.watcher is not None:
        yield Wait(entry.watcher)
    else:
        try:
            yield Wait(entry.runner)
        except TaskCancelledError:
            pass  # the cancel _close made: the runner published what it could


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
    # The ends of the inline runs carried out and not given to the step yet: they go before any input on the bus.
    inline_ends: list[RunEnd] = []
    state = initial
    measured: bool = yield caller_measures()
    try:
        while True:
            if inline_ends:
                event = inline_ends.pop(0)
            else:
                event = yield WaitForEvent(*inputs.event_types)
                event = _own_input(event, origin, running, armed)
                if event is _FOREIGN:
                    continue
            clock = (yield _start_clock(inputs)) if measured else None
            answer = step(state, event)
            if is_program_answer(answer):
                answer = yield step_answer(step, answer)
            answer = checked_next(answer, frozenset(running) | {end.key for end in inline_ends})
            step_seconds = (yield GetMonotonic()) - clock.began if clock is not None else 0.0
            state = answer.state
            finish: Finish[A] | None = None
            for effect in answer.effects:
                match effect:
                    case Run(key=key, program=program, inline=True):
                        inline_ends.append((yield _inline(key, program)))
                    case Run(key=key, program=program, priority=priority, name=name):
                        mark = object()
                        spawn = Spawn(_reported(origin, mark, key, program), priority=priority)
                        runner = yield (spawn if name is None else _launcher(name)(spawn))
                        running[key] = _Running(runner, mark, priority)
                    case CancelRun(key=key):
                        entry = running.get(key)
                        if entry is not None and not entry.cancelled:
                            yield Cancel(entry.runner)
                            # At the run's priority: a watcher below its runner would hold the end behind lower work.
                            watcher = yield Spawn(
                                _watch_cancelled(origin, entry.mark, key, entry.runner),
                                priority=entry.priority,
                            )
                            running[key] = replace(entry, watcher=watcher)
                    case Arm() | Disarm():
                        yield _set_deadline(effect, origin, inputs, armed)
                    case Finish():
                        yield _close(origin, running, armed)
                        running, armed, finish = {}, set(), effect
                        break
            if clock is not None:
                yield FoldMeasured(clock.queued, len(running), step_seconds)
            if finish is not None:
                return finish.value
    except Exception:
        # Shielded: in a fold whose own task is being cancelled, every wait outside a shield raises again.
        yield shielded(_close(origin, running, armed))
        raise


# The declaration doeff-effect-analyzer reads for the fold: the ``program`` of each ``Run`` the function handed as
# ``step`` builds (``Run.__doeff_runs_carried__``) runs in a task the fold spawns, under the handlers around the fold.
# The reader reads each such program where the step (or a function it calls) writes the Run, carried by ``Spawn``,
# and the runner's ``yield program`` adds nothing (the Run's program taken out by the ``case Run(...)`` above).
fold.__doeff_runs_built__ = {"step": Spawn}  # type: ignore[attr-defined] — the analyzer's mark on a function (no typed slot)
