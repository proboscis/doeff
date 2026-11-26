"""doeff-stream: ``host = Fold(step, initial, merge(sources...))``, answered by ``fold_handler``.

- Source (``subscribe``・``timers``): where the inputs come from. Effect answers and task ends are the runtime's own
  sources and are in every merge.
- ``merge``: the sources as one input stream (``Inputs``).
- ``Fold``: the effect that runs a step over the inputs. A pure ``step(state, input) -> Next(state, effects)``
  decides — or a step whose answer is a Program of that ``Next`` that performs no effect (``StepYieldedEffectError``
  otherwise). ``Fold(…, clocked=True)`` gives the step each input with the instant the fold took it (``Clocked`` — the
  clock read right before the step, never going back), for a step that judges deadlines, which has no clock of its own.
- ``fold_handler``: the runtime that answers ``Fold``. It runs each ``Run`` in a task and gives the step exactly one
  input for its end — ``Answered``, ``Raised`` or ``Cancelled``; it hands each ``Arm`` / ``Disarm`` to the timer
  handler without a task and gives the step ``TimerFired(tag)`` at the deadline. Installed innermost: the runs are
  spawned under the handlers around it.
- ``FoldMeasured``: what the fold measured for one input (the queue behind it, the runs running, the step's time),
  performed only under a caller's handler of it — which writes it where it goes (a counter, a gauge).
- ``ReplayFold`` / ``replay_handler``: the test harness. A list of inputs in, the trace (``Replay`` — the states and
  effects) out.
"""

from doeff_stream._effects import Fold as Fold
from doeff_stream._fold import fold_handler as fold_handler
from doeff_stream._harness import Replay as Replay
from doeff_stream._harness import ReplayFold as ReplayFold
from doeff_stream._harness import replay_handler as replay_handler
from doeff_stream._measure import FoldMeasured as FoldMeasured
from doeff_stream._source import Inputs as Inputs
from doeff_stream._source import Source as Source
from doeff_stream._source import merge as merge
from doeff_stream._source import subscribe as subscribe
from doeff_stream._source import timers as timers
from doeff_stream._step import Answered as Answered
from doeff_stream._step import Arm as Arm
from doeff_stream._step import Cancelled as Cancelled
from doeff_stream._step import CancelRun as CancelRun
from doeff_stream._step import Clocked as Clocked
from doeff_stream._step import Disarm as Disarm
from doeff_stream._step import Finish as Finish
from doeff_stream._step import Next as Next
from doeff_stream._step import Raised as Raised
from doeff_stream._step import Run as Run
from doeff_stream._step import StepYieldedEffectError as StepYieldedEffectError
