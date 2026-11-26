"""The test harness: give a step a list of inputs and read the states and effects it answered.

``ReplayFold(step, initial, inputs)`` is answered by ``replay_handler``, which calls the step the way ``fold`` does —
no task, no bus — and checks the step's answers with the same check. A step that answers a Program is evaluated through
the fold's own ``step_answer`` (the handler that refuses every effect). It also holds the inputs to what the runtime can
deliver: the end of a run comes only for a run that is running, and no input comes after ``Finish``. So a test writes
the ends of the runs it expects (``Answered(key, value)``, ``Raised(key, error)``, ``Cancelled(key)``) among the other
inputs and asserts the trace. ``Arm`` and ``Disarm`` stand in the effects like the others; a test writes the
``TimerFired(tag)`` of a deadline it expects to come as an input (``Replay.armed`` = the tags set and not yet back).

    trace = run(replay_handler(ReplayFold(step, initial, inputs)))
"""

from collections.abc import Callable, Generator, Hashable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

from doeff_events import TimerFired

from doeff import EffectBase, K, Program, Transfer, TransferThrow, do, handler
from doeff_stream._effects import StepInput
from doeff_stream._step import (
    Answered,
    Arm,
    Cancelled,
    CancelRun,
    Clocked,
    Disarm,
    Finish,
    Next,
    Raised,
    Run,
    StepEffect,
    checked_next,
    is_program_answer,
    step_answer,
)

S = TypeVar("S")


@dataclass(frozen=True)
class Stepped(Generic[S]):
    """One call of the step: the input it was given, the state it answered and the effects it asked for."""

    input: object
    state: S
    effects: tuple[StepEffect, ...]


@dataclass(frozen=True)
class Replay(Generic[S]):
    """The trace of a replay: the initial state and each step, in input order."""

    initial: S
    steps: tuple[Stepped[S], ...]

    @property
    def states(self) -> tuple[S, ...]:
        """The state after each input."""
        return tuple(stepped.state for stepped in self.steps)

    @property
    def effects(self) -> tuple[StepEffect, ...]:
        """Every effect the step asked for, in order."""
        return tuple(effect for stepped in self.steps for effect in stepped.effects)

    @property
    def final(self) -> S:
        """The state after the last input (the initial state when there was none)."""
        return self.steps[-1].state if self.steps else self.initial

    @property
    def running(self) -> frozenset[Hashable]:
        """The keys of the runs whose end has not been given yet."""
        started: set[Hashable] = set()
        for stepped in self.steps:
            event = _bare(stepped.input)
            if isinstance(event, Answered | Raised | Cancelled):
                started.discard(event.key)
            started.update(effect.key for effect in stepped.effects if isinstance(effect, Run))
        return frozenset(started)

    @property
    def armed(self) -> frozenset[Hashable]:
        """The tags of the deadlines set (``Arm``) and neither disarmed nor given back as ``TimerFired`` yet."""
        tags: set[Hashable] = set()
        for stepped in self.steps:
            event = _bare(stepped.input)
            if isinstance(event, TimerFired):
                tags.discard(event.tag)
            for effect in stepped.effects:
                match effect:
                    case Arm(tag=tag):
                        tags.add(tag)
                    case Disarm(tag=tag):
                        tags.discard(tag)
                    case Run() | CancelRun() | Finish():
                        pass
        return frozenset(tags)

    @property
    def finished(self) -> Finish | None:
        """The ``Finish`` the step answered, if it did."""
        last = self.steps[-1].effects if self.steps else ()
        return last[-1] if last and isinstance(last[-1], Finish) else None


def _bare(event: object) -> object:
    """The input as ``fold`` gives it: a ``Clocked`` input without its instant, any other input as it is."""
    return event.input if isinstance(event, Clocked) else event


@dataclass(frozen=True)
class ReplayFold(EffectBase[Replay[S]], Generic[S]):
    """Give ``step`` each of ``inputs`` from ``initial`` the way ``Fold`` gives them, and answer the trace (``Replay``).

    No task and no bus: the runs are not started, and a test writes the ends it expects (``Answered(key, value)``,
    ``Raised(key, error)``, ``Cancelled(key)``) and the ``TimerFired(tag)`` of the deadlines it expects among the
    inputs. ``clocked``: each input is a ``Clocked`` (the step of a ``Fold(…, clocked=True)``), given to the step as
    it is — the instants are part of the recorded inputs, so the same inputs answer the same trace.

    ``replay_handler`` answers it.
    """

    step: "Callable[[S, StepInput], Next[S] | Program[Next[S]]]"
    initial: S
    inputs: tuple[StepInput, ...]
    clocked: bool = False

    def __post_init__(self) -> None:
        # Checked here, not where the handler reads it: the test's mistake is named where it was made.
        if not callable(self.step):
            raise TypeError(f"ReplayFold takes a step function, got {type(self.step).__name__}")
        if not isinstance(self.clocked, bool):
            raise TypeError(f"ReplayFold takes a bool clocked, got {type(self.clocked).__name__}")
        # A list or a generator of inputs is kept as the tuple it gives: the effect is a value a handler reads.
        object.__setattr__(self, "inputs", tuple(self.inputs))


@handler
def replay_handler(effect: ReplayFold, k: K):
    """Answer ``ReplayFold`` with the trace of its step over its inputs.

    Refused like the runtime refuses it, and thrown where ``ReplayFold`` was performed: an answer that is not a valid
    ``Next``, and a step's Program that performs an effect (``StepYieldedEffectError``). Refused because the runtime
    never delivers it: the end of a run that is not running, and an input after ``Finish`` — and, ``clocked``, an input
    that is not a ``Clocked`` and an instant earlier than the one before.
    """
    try:
        trace = yield _replayed(effect)
    except Exception as error:  # every refusal is the test's to read where it performed ReplayFold
        return (yield TransferThrow(k, error))
    return (yield Transfer(k, trace))


@do
def _replayed(effect: "ReplayFold[S]") -> Generator[Any, Any, Replay[S]]:
    """The loop of ``replay_handler``: the step called the way ``fold`` calls it, its answers checked the same way."""
    step, initial, clocked = effect.step, effect.initial, effect.clocked
    trace: Replay[S] = Replay(initial, ())
    taken_at: datetime | None = None
    for given in effect.inputs:
        if trace.finished is not None:
            raise ValueError(f"input {given!r} after Finish: the fold has ended")
        if clocked:
            if not isinstance(given, Clocked):
                raise TypeError(
                    f"a clocked ReplayFold takes Clocked inputs, got {type(given).__name__}"
                )
            if taken_at is not None and given.at < taken_at:
                raise ValueError(
                    f"input {given!r} taken before the input before it ({taken_at}): the fold's instants never go back"
                )
            taken_at = given.at
        event = _bare(given) if clocked else given
        running = trace.running
        if isinstance(event, Answered | Raised | Cancelled):
            if event.key not in running:
                raise ValueError(f"end of the run {event.key!r}, which is not running")
            running = running - {event.key}
        answer = step(trace.final, given)
        if is_program_answer(answer):
            answer = yield step_answer(step, answer)
        answer = checked_next(answer, running)
        trace = Replay(initial, (*trace.steps, Stepped(given, answer.state, answer.effects)))
    return trace
