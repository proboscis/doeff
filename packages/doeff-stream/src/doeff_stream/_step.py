"""What a step reads and returns, and the checks the runtime and the replay both apply to its answer.

A step is a pure function ``step(state, input) -> Next(state, effects)``, or a function whose answer is a Program of
that ``Next`` that performs no effect (a defk that only composes other defks). The effects are values the runtime
carries out: ``Run`` starts a program in a task of its own, ``CancelRun`` cancels one, ``Arm`` / ``Disarm`` set and
take back a deadline of the fold's own (no task), ``Finish`` ends the fold.
Every ``Run`` comes back to the step as exactly one input with its key — ``Answered`` (the program's value),
``Raised`` (the exception it ended with) or ``Cancelled`` (it ended by the ``CancelRun`` of its key).

A step's Program runs under ``_refuse_every_effect``: every effect it performs ends it with ``StepYieldedEffectError``.
The fold and the replay evaluate it through the same ``step_answer``, so both see the same answer.
"""

from collections.abc import Callable, Generator, Hashable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeIs, TypeVar

from doeff_core_effects import TaskPriority

from doeff import DoExpr, EffectBase, K, Program, do, handler

S = TypeVar("S")
T = TypeVar("T")


@dataclass(frozen=True)
class Run:
    """Run ``program`` (a Program or an effect) in a task of its own.

    The program must not wait for the fold's inputs (``WaitForEvent`` / ``WaitForEvents``): it runs under the fold's
    bus handler, so its wait would take an input from the fold's queue. It must also end once cancelled — the runtime
    has no clock, so a run that waits on the outside world bounds its own wait (``WaitWithin``).

    Its end comes back to the step as one input with ``key``. A key names one run while it is running: a ``Run``
    of a key whose end has not come back yet is refused. Once the end came back, the key may be used again.

    ``priority`` is the scheduler priority of the run's two tasks (the program and its watcher, which publishes the
    end): the ``Spawn`` default ``TaskPriority.NORMAL`` unless the step names another (any ``int``, higher first).
    """

    key: Hashable
    program: "Program[object]"
    priority: TaskPriority | int = TaskPriority.NORMAL

    def __post_init__(self) -> None:
        # Checked here, not at the Spawn: the step's mistake is named where it was made, and the replay sees it too.
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError(
                f"Run of the key {self.key!r} takes an int priority, got {type(self.priority).__name__}"
            )


@dataclass(frozen=True)
class CancelRun:
    """Cancel the run ``key``. Its end still comes back as one input: ``Cancelled`` when the cancel stopped it, or
    ``Answered`` / ``Raised`` when it had already ended (that end may be on its way to the step). A key that is not
    running is no error: its end has already come back."""

    key: Hashable


@dataclass(frozen=True)
class Arm:
    """Set the deadline ``tag`` of this fold at ``at`` (a timezone-aware instant). At ``at`` it comes back to the step
    as the input ``TimerFired(tag)``; the fold needs ``timers()`` among its sources.

    No task: the fold hands the deadline to the timer handler itself (``ArmTimer``), under a tag that names the fold,
    so the deadline of another fold on the same bus never reaches this step, whatever its tag. Arming a tag that is
    set moves its deadline. A deadline still set when the fold ends is disarmed then.
    """

    tag: Hashable
    at: datetime

    def __post_init__(self) -> None:
        # Checked here, not at the timer handler: the step's mistake is named where it was made.
        if not isinstance(self.at, datetime):
            raise TypeError(f"Arm takes a datetime, got {type(self.at).__name__}")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("Arm takes a timezone-aware datetime")


@dataclass(frozen=True)
class Disarm:
    """Take back the deadline ``tag`` of this fold: its ``TimerFired`` does not come. A tag that is not set (never
    armed, disarmed, or already fired) is no error."""

    tag: Hashable


@dataclass(frozen=True)
class Finish:
    """End the fold with ``value``: the effect after which the step is not called again.

    An answer has at most one. It may stand anywhere among the effects — the runtime carries it out after the others
    (so the effects of several reducers can be joined in any order). The runtime cancels the runs that are still running and waits until each has ended before the fold answers
    ``value``; their ends are not given to a step any more.
    """

    value: object


StepEffect = Run | CancelRun | Arm | Disarm | Finish
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


class StepYieldedEffectError(TypeError):
    """A step's Program performed an effect. A step decides from its state and its input alone: what it needs from
    the outside world comes as an input, and what it wants done goes out as a ``Run``.

    Every effect is refused, not a list of them — ``Ask`` and the clock too, or the inputs would no longer decide
    the replay. The step cannot catch this: its Program is not resumed, so the error is raised where the step's
    answer is read (in the fold, or in the replay).
    """

    def __init__(self, step: str, effect: object) -> None:
        super().__init__(
            f"the step {step} performed the effect {type(effect).__name__}: a step answers Next(state, effects) "
            "without effects — read the outside world as an input, ask for work with Run"
        )
        self.step = step
        self.effect = effect


@dataclass(frozen=True)
class _Performed:
    """What the step's Program ends with when it performed ``effect``: the handler's answer in place of the
    Program's, so the Program is never resumed (a ``try`` in the step cannot catch the refusal)."""

    effect: object


@handler
def _refuse_every_effect(effect: object, k: K) -> "_Performed":
    """Every effect the step's Program performs ends it: the continuation ``k`` is dropped, not resumed."""
    return _Performed(effect)


# The declaration doeff-effect-analyzer reads for this handler: it has no ``isinstance`` clause to read — it answers
# every effect (EffectBase) and performs none, so nothing the Program it wraps would perform leaves it (the reader
# need not follow that Program, a value the step makes at run time).
_refuse_every_effect.__doeff_handles__ = (EffectBase,)  # type: ignore[attr-defined] — the analyzer's mark on a function (no typed slot)
_refuse_every_effect.__doeff_effects__ = ()  # type: ignore[attr-defined] — the analyzer's mark on a function (no typed slot)


def step_name(step: Callable[..., object]) -> str:
    """The name an error gives ``step`` by."""
    return getattr(step, "__qualname__", None) or getattr(step, "__name__", None) or repr(step)


@do
def step_answer(
    step: Callable[..., object], answer: "Program[T]"
) -> Generator[Any, Any, T]:
    """The value of the step's Program ``answer``, evaluated under ``_refuse_every_effect``; ``StepYieldedEffectError``
    when it performed an effect. The fold yields this and the replay runs it: one evaluation for both."""
    value = yield _refuse_every_effect(answer)
    if isinstance(value, _Performed):
        raise StepYieldedEffectError(step_name(step), value.effect)
    return value


def is_program_answer(answer: "Next[S] | Program[Next[S]]") -> TypeIs["Program[Next[S]]"]:
    """Whether the step answered a Program (to evaluate) rather than a value (to check as it is)."""
    # The tuple form: to the type checker DoExpr is the union of the program nodes, at run time a virtual base.
    return isinstance(answer, (DoExpr,))


def checked_next(answer: "Next[S]", running: frozenset[Hashable]) -> "Next[S]":
    """``answer`` as the ``Next`` it must be, or the error naming what is wrong with it.

    ``running`` = the keys whose runs have not come back yet. Refused: an answer that is not a ``Next``, an effect
    that is not a ``StepEffect``, a ``Run`` whose program is neither a Program nor an effect, a ``Run`` of a running
    key (or of a key two ``Run``s of the answer share), two ``Finish``. The answer comes back with its ``Finish`` moved
    after the other effects — the order the runtime carries them out in.
    """
    # A step written in Hy or untyped Python is not held to its annotation: the answer is checked here.
    if not isinstance(answer, Next):
        raise TypeError(
            f"a step answers Next(state, effects) or a Program of it, got {type(answer).__name__}"
        )
    effects: tuple[object, ...] = answer.effects
    started = set(running)
    for effect in effects:
        match effect:
            case Run(key=key, program=program):
                # Caught here, not in the run: a non-program would come back as a run's Raised, a step's mistake
                # read as the run's failure.
                if not isinstance(program, (DoExpr, EffectBase)):
                    raise TypeError(
                        f"Run of the key {key!r} takes a Program or an effect, got {type(program).__name__}"
                    )
                if key in started:
                    raise ValueError(f"Run of the key {key!r}, whose run has not come back yet")
                started.add(key)
            case CancelRun() | Arm() | Disarm() | Finish():
                pass
            case _:
                raise TypeError(
                    "a step's effects are Run, CancelRun, Arm, Disarm or Finish, "
                    f"got {type(effect).__name__}"
                )
    finishes = tuple(effect for effect in answer.effects if isinstance(effect, Finish))
    if len(finishes) > 1:
        raise ValueError(f"an answer has at most one Finish, got {len(finishes)}")
    others = tuple(effect for effect in answer.effects if not isinstance(effect, Finish))
    return Next(answer.state, (*others, *finishes))
