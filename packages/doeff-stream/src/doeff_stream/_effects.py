"""The effect a caller performs to run a step: ``Fold``.

A caller does not call the runtime: it performs ``Fold(step, initial, inputs)`` and a handler answers it — ``fold_handler``
in a host (``_fold.py``), or another handler of the caller's that runs the fold its own way. A test performs
``ReplayFold(step, initial, inputs)`` with the inputs written out, and ``replay_handler`` answers the trace
(``_harness.py`` — it takes the step as ``Fold`` does).

The step's type is written out in ``Fold`` and in the test harness's ``ReplayFold``: the inputs it is given
(``StepInput``) and the ``Next`` it answers, whose effects are the values the runtime carries out. So the values a step
reads and writes are the value types of these effects (ADR-DOE-API-001 R2).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from doeff_core_effects import Spawn
from doeff_events import TimerFired

from doeff import EffectBase, Program
from doeff_stream._source import Inputs
from doeff_stream._step import A, Answered, Cancelled, Clocked, Next, Raised

S = TypeVar("S")

StepInput = Answered | Raised | Cancelled | Clocked | TimerFired | object
"""What a step is given: the end of a run (``Answered`` / ``Raised`` / ``Cancelled``), the deadline of an ``Arm``
(``TimerFired(tag)``), an event of a source — or, under ``clocked``, each of them inside a ``Clocked``."""


@dataclass(frozen=True)
class Fold(EffectBase[A], Generic[S, A]):
    """Run ``step`` over the inputs of ``inputs`` from ``initial`` until it answers ``Finish(value)``; answer ``value``.

    The answer's type is the ``A`` of the step's ``Next[S, A]`` (the type its ``Finish`` carries — ``object`` when the
    step answers a bare ``Next[S]``). A step may answer a Program of its ``Next`` that performs no effect.

    ``clocked``: the step is given each input inside a ``Clocked`` — the input with the instant the fold took it (the
    clock read right before the step, never going back) — for a step that judges deadlines, which has no clock of its
    own.

    ``fold_handler`` answers it in a host: install it inside the handlers the step's runs need (its runs are spawned
    from the handler, under the handlers around it) and inside the bus and timer handlers it waits on.
    """

    step: "Callable[[S, StepInput], Next[S, A] | Program[Next[S, A]]]"
    initial: S
    inputs: Inputs
    clocked: bool = False

    def __post_init__(self) -> None:
        # Checked here, not where a handler reads it: the caller's mistake is named where it was made.
        if not callable(self.step):
            raise TypeError(f"Fold takes a step function, got {type(self.step).__name__}")
        if not isinstance(self.inputs, Inputs):
            raise TypeError(
                f"Fold takes the Inputs of merge(...), got {type(self.inputs).__name__}"
            )
        if not isinstance(self.clocked, bool):
            raise TypeError(f"Fold takes a bool clocked, got {type(self.clocked).__name__}")


# The declaration doeff-effect-analyzer reads for the fold: the ``program`` of each ``Run`` the function handed as
# ``step`` builds (``Run.__doeff_runs_carried__``) runs in a task the fold spawns, under the handlers around it. The
# reader reads each such program where the step (or a function it calls) writes the Run, carried by ``Spawn``.
Fold.__doeff_runs_built__ = {"step": Spawn}  # type: ignore[attr-defined] — the analyzer's mark on a class (no typed slot)
