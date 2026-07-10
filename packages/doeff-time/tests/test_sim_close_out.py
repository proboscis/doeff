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
