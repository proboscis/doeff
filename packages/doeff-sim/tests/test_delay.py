from __future__ import annotations

from doeff import WithHandler, default_handlers, do, run
from doeff_time.effects import Delay, GetTime

from doeff_sim.handlers import deterministic_sim_handler


@do
def _program():
    before = yield GetTime()
    yield Delay(5.0)
    after = yield GetTime()
    return before, after


def test_delay_and_get_time_use_simulated_clock() -> None:
    result = run(
        WithHandler(deterministic_sim_handler(start_time=100.0), _program()),
        handlers=default_handlers(),
    )

    assert result.value == (100.0, 105.0)
