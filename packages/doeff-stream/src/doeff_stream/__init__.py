"""doeff-stream: ``host = fold(step, initial, merge(sources...))``.

- Source (``subscribe``・``timers``): where the inputs come from. Effect answers and task ends are the runtime's own
  sources and are in every merge.
- ``merge``: the sources as one input stream (``Inputs``).
- ``fold``: the runtime. A pure ``step(state, input) -> Next(state, effects)`` decides — or a step whose answer is a
  Program of that ``Next`` that performs no effect (``StepYieldedEffectError`` otherwise); the runtime runs each ``Run``
  in a task and gives the step exactly one input for its end — ``Answered``, ``Raised`` or ``Cancelled``; it hands
  each ``Arm`` / ``Disarm`` to the timer handler without a task and gives the step ``TimerFired(tag)`` at the deadline.
- ``FoldMeasured``: what the fold measured for one input (the queue behind it, the runs running, the step's time),
  performed only under a caller's handler of it — which writes it where it goes (a counter, a gauge).
- ``replay``: the test harness. A list of inputs in, the states and effects out.
- ``simulate_fold``: the fold as a host assembles it, on a virtual clock and a memory bus of its own. A list of
  inputs and the caller's (simulated) handlers in, the fold's value out.
"""

from doeff_stream._fold import fold as fold
from doeff_stream._harness import Replay as Replay
from doeff_stream._harness import Stepped as Stepped
from doeff_stream._harness import replay as replay
from doeff_stream._measure import FoldMeasured as FoldMeasured
from doeff_stream._sim import simulate_fold as simulate_fold
from doeff_stream._source import Inputs as Inputs
from doeff_stream._source import Source as Source
from doeff_stream._source import merge as merge
from doeff_stream._source import subscribe as subscribe
from doeff_stream._source import timers as timers
from doeff_stream._step import Answered as Answered
from doeff_stream._step import Arm as Arm
from doeff_stream._step import Cancelled as Cancelled
from doeff_stream._step import CancelRun as CancelRun
from doeff_stream._step import Disarm as Disarm
from doeff_stream._step import Finish as Finish
from doeff_stream._step import Next as Next
from doeff_stream._step import Raised as Raised
from doeff_stream._step import Run as Run
from doeff_stream._step import RunEnd as RunEnd
from doeff_stream._step import StepEffect as StepEffect
from doeff_stream._step import StepYieldedEffectError as StepYieldedEffectError
