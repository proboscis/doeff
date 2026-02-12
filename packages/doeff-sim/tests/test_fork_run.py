from __future__ import annotations

from doeff import WithHandler, default_handlers, do, run
from doeff_time.effects import Delay, GetTime

from doeff_sim.effects import ForkRun, SetTime
from doeff_sim.handlers import deterministic_sim_handler


@do
def _fork_body():
    yield Delay(10.0)
    return (yield GetTime())


@do
def _program():
    yield SetTime(100.0)
    outer_before = yield GetTime()
    fork_value = yield ForkRun(_fork_body(), start_time=5.0)
    outer_after = yield GetTime()
    return outer_before, fork_value, outer_after


def test_fork_run_uses_isolated_simulation_state() -> None:
    result = run(
        WithHandler(deterministic_sim_handler(start_time=0.0), _program()),
        handlers=default_handlers(),
    )

    assert result.value == (100.0, 15.0, 100.0)
