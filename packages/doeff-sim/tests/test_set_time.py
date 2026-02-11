from __future__ import annotations

from doeff import WithHandler, default_handlers, do, run
from doeff_time.effects import GetTime

from doeff_sim.effects import SetTime
from doeff_sim.handlers import deterministic_sim_handler


@do
def _program():
    yield SetTime(42.0)
    return (yield GetTime())


def test_set_time_effect_updates_sim_clock() -> None:
    result = run(
        WithHandler(deterministic_sim_handler(start_time=0.0), _program()),
        handlers=default_handlers(),
    )

    assert result.value == 42.0
