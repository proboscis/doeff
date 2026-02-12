from __future__ import annotations

from doeff import WithHandler, default_handlers, do, run
from doeff.effects.gather import gather
from doeff.effects.race import race
from doeff.effects.spawn import Spawn
from doeff_time.effects import Delay, GetTime

from doeff_sim.handlers import deterministic_sim_handler


@do
def _worker(name: str, delay_seconds: float):
    yield Delay(delay_seconds)
    now = yield GetTime()
    return f"{name}@{now:.1f}"


@do
def _gather_program():
    t1 = yield Spawn(_worker("g1", 1.0))
    t2 = yield Spawn(_worker("g2", 2.0))
    return (yield gather(t1, t2))


@do
def _race_program():
    t1 = yield Spawn(_worker("r1", 2.0))
    t2 = yield Spawn(_worker("r2", 1.0))
    winner = yield race(t2, t1)
    now = yield GetTime()
    return winner, now


def test_gather_order_is_deterministic() -> None:
    result = run(
        WithHandler(deterministic_sim_handler(start_time=0.0), _gather_program()),
        handlers=default_handlers(),
    )

    assert result.value == ["g1@1.0", "g2@3.0"]


def test_race_order_is_deterministic() -> None:
    result = run(
        WithHandler(deterministic_sim_handler(start_time=0.0), _race_program()),
        handlers=default_handlers(),
    )

    assert result.value == ("r1@2.0", 2.0)
