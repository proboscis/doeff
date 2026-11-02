"""What a step reads and returns, and the one check the runtime and the replay both apply to its answer.

A step is a pure function ``step(state, input) -> Next(state, effects)``. The effects are values the runtime
carries out: ``Run`` starts a program in a task of its own, ``CancelRun`` cancels one, ``Finish`` ends the fold.
Every ``Run`` comes back to the step as exactly one input with its key — ``Answered`` (the program's value),
``Raised`` (the exception it ended with) or ``Cancelled`` (it ended by the ``CancelRun`` of its key).
"""

from collections.abc import Hashable
from dataclasses import dataclass
from typing import Generic, TypeVar

from doeff import Program

S = TypeVar("S")


@dataclass(frozen=True)
class Run:
    """Run ``program`` (a Program or an effect) in a task of its own.

    Its end comes back to the step as one input with ``key``. A key names one run while it is running: a ``Run``
    of a key whose end has not come back yet is refused. Once the end came back, the key may be used again.
    """

    key: Hashable
    program: "Program[object]"


@dataclass(frozen=True)
class CancelRun:
    """Cancel the run ``key``. Its end still comes back as one input: ``Cancelled`` when the cancel stopped it, or
    ``Answered`` / ``Raised`` when it had already ended (that end may be on its way to the step). A key that is not
    running is no error: its end has already come back."""

    key: Hashable


@dataclass(frozen=True)
class Finish:
    """End the fold with ``value``: the effect after which the step is not called again (it comes last).

    The runtime cancels the runs that are still running and waits until each has ended before the fold answers
    ``value``; their ends are not given to a step any more.
    """

    value: object


StepEffect = Run | CancelRun | Finish
"""Everything a step may ask the runtime to do."""


@dataclass(frozen=True)
class Next(Generic[S]):
    """A step's answer: the next state and the effects to carry out, in order."""

    state: S
    effects: tuple[StepEffect, ...] = ()


@dataclass(frozen=True)
class Answered:
    """The run ``key`` ended with ``value``."""

    key: Hashable
    value: object


@dataclass(frozen=True)
class Raised:
    """The run ``key`` ended with the exception ``error``."""

    key: Hashable
    error: Exception


@dataclass(frozen=True)
class Cancelled:
    """The run ``key`` ended because the step cancelled it (``CancelRun``)."""

    key: Hashable


RunEnd = Answered | Raised | Cancelled
"""The inputs the runtime makes of a run's end: exactly one per ``Run``."""


def checked_next(answer: "Next[S]", running: frozenset[Hashable]) -> "Next[S]":
    """``answer`` as the ``Next`` it must be, or the error naming what is wrong with it.

    ``running`` = the keys whose runs have not come back yet. Refused: an answer that is not a ``Next``, an effect
    that is not a ``StepEffect``, a ``Run`` of a running key (or of a key two ``Run``s of the answer share), a
    ``Finish`` that is not the last effect.
    """
    # A step written in Hy or untyped Python is not held to its annotation: the answer is checked here.
    if not isinstance(answer, Next):
        raise TypeError(f"a step answers Next(state, effects), got {type(answer).__name__}")
    effects: tuple[object, ...] = answer.effects
    started = set(running)
    for index, effect in enumerate(effects):
        match effect:
            case Run(key=key):
                if key in started:
                    raise ValueError(f"Run of the key {key!r}, whose run has not come back yet")
                started.add(key)
            case CancelRun():
                pass
            case Finish():
                if index != len(effects) - 1:
                    raise ValueError("Finish is not the last effect of the step's answer")
            case _:
                raise TypeError(
                    f"a step's effects are Run, CancelRun or Finish, got {type(effect).__name__}"
                )
    return answer
