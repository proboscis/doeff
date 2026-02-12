from __future__ import annotations

from doeff import WithHandler, default_handlers, do, run
from doeff.effects.spawn import Spawn
from doeff.effects.wait import wait
from doeff_time.effects import Delay, GetTime

from doeff_sim.handlers import deterministic_sim_handler


@do
def _worker(name: str, delay_seconds: float):
    yield Delay(delay_seconds)
    now = yield GetTime()
    return f"{name}@{now:.1f}"


@do
def _program():
    task_a = yield Spawn(_worker("task-a", 2.0))
    task_b = yield Spawn(_worker("task-b", 1.0))

    first = yield wait(task_a)
    second = yield wait(task_b)
    return first, second


def test_spawn_wait_are_deterministic() -> None:
    result = run(
        WithHandler(deterministic_sim_handler(start_time=0.0), _program()),
        handlers=default_handlers(),
    )

    assert result.value == ("task-a@2.0", "task-b@3.0")
