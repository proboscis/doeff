"""The sim clock driver must not trip the scheduler's #501 close-out warning.

The driver is spawned by sim_time_handler itself (invisible to user code) and
its final IDLE resume — queued right after its last CompletePromise — is
routinely abandoned when the woken root body returns first. Before the driver
was marked ``daemon=True`` every successful sim run emitted a RuntimeWarning
the user could neither await away nor legitimately suppress.
"""

import warnings
from datetime import datetime, timezone

from doeff_core_effects.scheduler import scheduled
from doeff_time.effects import Delay, GetTime
from doeff_time.handlers.sim_time import sim_time_handler

from doeff import do, run
from doeff import handler as program_handler

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_plain_sim_run_emits_no_close_out_warning() -> None:
    @do
    def body():
        yield Delay(5)
        yield Delay(10)
        return (yield GetTime())

    program = scheduled(program_handler(sim_time_handler(start_time=START))(body()))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = run(program)

    assert result == datetime(2026, 1, 1, 0, 0, 15, tzinfo=timezone.utc)


def test_a_positive_delay_below_one_tick_still_moves_the_clock() -> None:
    """A positive sub-microsecond Delay advances the virtual clock by one tick (not zero).

    Before the fix ``timedelta(seconds=7.2e-08)`` rounded to zero, so a loop sleeping
    "the rest of its timeout" never advanced virtual time (agora-redesign #675).
    """

    @do
    def body():
        start = yield GetTime()
        yield Delay(7.2e-08)
        return (yield GetTime()) - start

    program = scheduled(program_handler(sim_time_handler(start_time=START))(body()))
    assert run(program).total_seconds() == 1e-06


def test_a_wait_loop_on_the_rest_of_its_timeout_terminates() -> None:
    """Sleeping the float remainder of a timeout reaches the timeout (terminates)."""
    from doeff_time.effects import GetMonotonic

    @do
    def body():
        start = yield GetMonotonic()
        timeout = 0.1 + 0.2 + 1e-07
        at = start
        steps = 0
        while at - start < timeout:
            yield Delay(min(0.5, timeout - (at - start)))
            at = yield GetMonotonic()
            steps += 1
            assert steps < 10, "the virtual clock stopped moving"
        return steps

    program = scheduled(program_handler(sim_time_handler(start_time=START))(body()))
    assert run(program) < 10
