"""doeff-stream: ``host = fold(step, initial, merge(sources...))``.

- Source (``subscribe``・``timers``): where the inputs come from. Effect answers and task ends are the runtime's own
  sources and are in every merge.
- ``merge``: the sources as one input stream (``Inputs``).
- ``fold``: the runtime. A pure ``step(state, input) -> Next(state, effects)`` decides — or a step whose answer is a
  Program of that ``Next`` that performs no effect (``StepYieldedEffectError`` otherwise); the runtime runs each ``Run``
  in a task and gives the step exactly one input for its end — ``Answered``, ``Raised`` or ``Cancelled``.
- ``replay``: the test harness. A list of inputs in, the states and effects out.
"""

from doeff_stream._fold import fold
from doeff_stream._harness import Replay, Stepped, replay
from doeff_stream._source import Inputs, Source, merge, subscribe, timers
from doeff_stream._step import (
    Answered,
    Cancelled,
    CancelRun,
    Finish,
    Next,
    Raised,
    Run,
    RunEnd,
    StepEffect,
    StepYieldedEffectError,
)

__all__ = [
    "Answered",
    "CancelRun",
    "Cancelled",
    "Finish",
    "Inputs",
    "Next",
    "Raised",
    "Replay",
    "Run",
    "RunEnd",
    "Source",
    "StepEffect",
    "StepYieldedEffectError",
    "Stepped",
    "fold",
    "merge",
    "replay",
    "subscribe",
    "timers",
]
