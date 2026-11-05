"""The fold's measure: one ``FoldMeasured`` per input the step was given, for a caller that answers it.

The subscriber queue of a fold has no bound, and the fold takes one input per wait; ``FoldMeasured`` is how a caller
sees it fall behind. The fold reads the queue (``CountQueued``) and the clock (``GetMonotonic``) through the caller's
handlers and performs ``FoldMeasured``; where the value goes (a counter, a gauge, a log) is the caller's handler.

A fold under no handler annotated with ``FoldMeasured`` measures nothing: the default drops the measures, and the fold
performs the effects it performs without them — no ``CountQueued``, no clock reading.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

from doeff import EffectBase, GetOuterHandlers, do, handler_effect_types


@dataclass(frozen=True)
class FoldMeasured(EffectBase):
    """What the fold measured for one input it gave the step, once it carried out the step's answer. Answers ``None``.

    - ``queued``: the inputs waiting in the fold's subscriber queue when the fold took this one
      (``CountQueued(*inputs.event_types)`` — the inputs of the other folds on the same bus included).
    - ``running``: the runs the fold started whose end has not reached the step, after the step's effects were carried
      out (``0`` after a ``Finish``).
    - ``step_seconds``: how long the step took, its Program's evaluation included, as two readings of ``GetMonotonic``
      tell it (``0.0`` on the virtual clock, which does not move during a step).

    A handler that answers it is annotated with ``FoldMeasured`` (a handler that admits every effect is not read as
    one).
    """

    queued: int
    running: int
    step_seconds: float


def _answers_measures(handler: Callable[..., object]) -> bool:
    """Whether ``handler``'s effect annotation names ``FoldMeasured``."""
    types = handler_effect_types(handler)
    return types is not None and FoldMeasured in types


@do
def caller_measures() -> Generator[Any, Any, bool]:
    """Whether a handler around the fold answers ``FoldMeasured`` (read once, when the fold starts)."""
    handlers: list[Callable[..., object]] = yield GetOuterHandlers()
    return any(_answers_measures(handler) for handler in handlers)
