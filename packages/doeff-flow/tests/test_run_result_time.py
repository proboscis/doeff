"""``run_result`` and ``run_workflow`` answer doeff-time effects on the wall clock.

The examples wait with ``yield Delay(seconds)`` instead of ``time.sleep`` (card ki-94380dba532f).
That only works if the runner installs a time handler: without one the first ``Delay`` fails with
an unhandled ``DelayEffect``. These tests run a program that reads the clock, delays, and reads the
clock again, and check that the delay was answered and that the clock moved by the delay.
"""

from datetime import timedelta
from pathlib import Path

from doeff_flow import run_result, run_workflow
from doeff_time import Delay, GetTime

from doeff import EffectGenerator, Ok, do

_DELAY_SECONDS = 0.05
# asyncio may wake a timer a clock tick early, so the floor is a little under the delay.
_MIN_ELAPSED = timedelta(seconds=0.04)


@do
def _delay_between_clock_reads() -> EffectGenerator[timedelta]:
    started = yield GetTime()
    yield Delay(_DELAY_SECONDS)
    finished = yield GetTime()
    return finished - started


def test_run_result_answers_delay_on_the_wall_clock() -> None:
    result = run_result(_delay_between_clock_reads())

    assert isinstance(result, Ok), result
    assert result.value >= _MIN_ELAPSED


def test_run_workflow_answers_delay_and_writes_the_trace(tmp_path: Path) -> None:
    result = run_workflow(_delay_between_clock_reads(), workflow_id="delay-wf", trace_dir=tmp_path)

    assert isinstance(result, Ok), result
    assert result.value >= _MIN_ELAPSED
    assert (tmp_path / "delay-wf" / "trace.jsonl").exists()
