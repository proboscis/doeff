"""The test harness: give a step a list of inputs and read the states and effects it answered.

``replay`` calls the step the way ``fold`` does — no task, no bus — and checks the step's answers with the same
check. A step that answers a value runs without the VM. A step that answers a Program is evaluated on the VM through
the fold's own ``step_answer`` (the handler that refuses every effect), one ``run`` per input. It also holds the
inputs to what the runtime can deliver: the end of a run comes only for a run that is
running, and no input comes after ``Finish``. So a test writes the ends of the runs it expects
(``Answered(key, value)``, ``Raised(key, error)``, ``Cancelled(key)``) among the other inputs and asserts the trace.
``Arm`` and ``Disarm`` stand in the effects like the others; a test writes the ``TimerFired(tag)`` of a deadline it
expects to come as an input (``Replay.armed`` = the tags set and not yet back).
"""

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

from doeff_events import TimerFired

from doeff import Program, run
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


def replay(
    step: Callable[[S, object], "Next[S] | Program[Next[S]]"], initial: S, inputs: Iterable[object]
) -> Replay[S]:
    """Give ``step`` each of ``inputs`` from ``initial`` and answer the trace.

    Refused like the runtime refuses it: an answer that is not a valid ``Next``, and a step's Program that performs
    an effect (``StepYieldedEffectError``). Refused because the runtime never delivers it: the end of a run that is not
    running, and an input after ``Finish``.
    """
    return _replayed(step, initial, inputs, clocked=False)


def replay_clocked(
    step: Callable[[S, Clocked], "Next[S] | Program[Next[S]]"], initial: S, inputs: Iterable[Clocked]
) -> Replay[S]:
    """``replay`` for a step of ``fold_clocked``: each input is a ``Clocked`` (the input with the instant the fold took
    it), given to the step as it is. The instants are part of the recorded inputs, so the same inputs answer the same
    trace. Refused besides what ``replay`` refuses, because ``fold_clocked`` never delivers it: an input that is not a
    ``Clocked``, and an instant earlier than the one before.
    """
    return _replayed(step, initial, inputs, clocked=True)


def _replayed(
    step: Callable[[S, Any], "Next[S] | Program[Next[S]]"], initial: S, inputs: Iterable[object], *, clocked: bool
) -> Replay[S]:
    """The loop of ``replay`` and ``replay_clocked`` (``clocked`` = the inputs are ``Clocked``)."""
    trace: Replay[S] = Replay(initial, ())
    taken_at: datetime | None = None
    for given in inputs:
        if trace.finished is not None:
            raise ValueError(f"input {given!r} after Finish: the fold has ended")
        if clocked:
            if not isinstance(given, Clocked):
                raise TypeError(f"replay_clocked takes Clocked inputs, got {type(given).__name__}")
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
            answer = run(step_answer(step, answer))
        answer = checked_next(answer, running)
        trace = Replay(initial, (*trace.steps, Stepped(given, answer.state, answer.effects)))
    return trace
