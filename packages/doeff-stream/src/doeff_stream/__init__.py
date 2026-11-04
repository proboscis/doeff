"""doeff-stream: ``host = fold(step, initial, merge(sources...))``.

- Source (``subscribe``・``timers``): where the inputs come from. Effect answers and task ends are the runtime's own
  sources and are in every merge.
- ``merge``: the sources as one input stream (``Inputs``).
- ``fold``: the runtime. A pure ``step(state, input) -> Next(state, effects)`` decides — or a step whose answer is a
  Program of that ``Next`` that performs no effect (``StepYieldedEffectError`` otherwise); the runtime runs each ``Run``
  in a task and gives the step exactly one input for its end — ``Answered``, ``Raised`` or ``Cancelled``.
- ``replay``: the test harness. A list of inputs in, the states and effects out.
- ``simulate_fold``: the fold as a host assembles it, on a virtual clock and a memory bus of its own. A list of
  inputs and the caller's (simulated) handlers in, the fold's value out.
"""

from doeff_stream._fold import fold
from doeff_stream._harness import Replay, Stepped, replay
from doeff_stream._sim import simulate_fold
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
    "simulate_fold",
    "subscribe",
    "timers",
]
