"""The fold's measures: one ``FoldMeasured`` per input, for a caller that answers it.

- Under no handler of ``FoldMeasured`` the fold measures nothing: it reads neither the queue nor the clock, and a
  handler that admits every effect is not taken for one.
- Under a handler of ``FoldMeasured`` each input the step was given brings one value: the queue behind the input
  when the fold took it, the runs running once the step's effects were carried out, and the step's time on the
  clock the caller's handlers answer.
"""

from doeff_events import CountQueuedEffect
from doeff_stream import Finish, FoldMeasured, Next, Run, merge, simulate_fold, subscribe
from doeff_time import GetMonotonicEffect
from stream_test_support import T0, Kick, forever

from doeff import Pass, Resume, handler


class Poke:
    """An external event in these checks."""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Poke)

    def __hash__(self) -> int:
        return 1


def _step(state: int, event: object) -> Next[int]:
    """Start a run that never ends at the ``Kick``; finish at the second ``Poke``."""
    match event:
        case Kick():
            return Next(state, (Run("forever", forever()),))
        case Poke() if state == 1:
            return Next(2, (Finish("done"),))
        case Poke():
            return Next(state + 1)
        case _:
            return Next(state)


INPUTS = merge(subscribe(Kick, Poke))
EVENTS = (Kick(), Poke(), Poke())


def test_a_fold_without_a_measures_handler_measures_nothing() -> None:
    performed: frozenset[type] = frozenset()

    @handler
    def every_effect(effect, k):  # admits every effect: not a handler of FoldMeasured
        nonlocal performed
        performed = performed | {type(effect)}
        yield Pass(effect, k)

    answer = simulate_fold(
        _step, 0, INPUTS, events=EVENTS, handlers=(every_effect,), start_time=T0
    )
    assert answer == "done"
    assert performed, "the recorder saw the fold's effects"
    measuring = {FoldMeasured, CountQueuedEffect, GetMonotonicEffect}
    assert not measuring & performed, performed


def test_a_measures_handler_gets_one_value_per_input() -> None:
    measured: tuple[FoldMeasured, ...] = ()
    readings = iter((10.0, 10.5, 20.0, 20.25, 30.0, 32.0))

    @handler
    def measures(effect: FoldMeasured, k):
        nonlocal measured
        measured = (*measured, effect)
        return (yield Resume(k, None))

    @handler
    def ticking_clock(effect: GetMonotonicEffect, k):
        return (yield Resume(k, next(readings)))

    answer = simulate_fold(
        _step, 0, INPUTS, events=EVENTS, handlers=(measures, ticking_clock), start_time=T0
    )
    assert answer == "done"
    assert measured == (
        # Kick: two Pokes wait behind it; its run is running.
        FoldMeasured(queued=2, running=1, step_seconds=0.5),
        FoldMeasured(queued=1, running=1, step_seconds=0.25),
        # The Finish cancelled the run and waited for its end.
        FoldMeasured(queued=0, running=0, step_seconds=2.0),
    )
