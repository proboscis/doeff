from __future__ import annotations

from doeff import (
    AskEffect,
    Delegate,
    Listen,
    Resume,
    Tell,
    WithHandler,
    ask,
    default_handlers,
    do,
    run,
)
from doeff_time.effects import Delay, GetTime

from doeff_sim.handlers import SIMULATION_START_TIME_ENV_KEY, deterministic_sim_handler


@do
def _program_with_ask():
    yield Delay(1.0)
    api_key = yield ask("api_key")
    now = yield GetTime()
    return api_key, now


@do
def _program_with_tell():
    yield Tell("hello")
    return "ok"


def test_handler_composes_with_other_handlers() -> None:
    def api_key_handler(effect, k):
        if isinstance(effect, AskEffect) and effect.key == "api_key":
            return (yield Resume(k, "mock-key"))
        yield Delegate()

    wrapped = WithHandler(
        deterministic_sim_handler(start_time=0.0),
        WithHandler(api_key_handler, _program_with_ask()),
    )

    result = run(wrapped, handlers=default_handlers())
    assert result.value == ("mock-key", 1.0)


def test_log_formatter_decorates_tell_messages() -> None:
    wrapped = Listen(
        WithHandler(
            deterministic_sim_handler(
                start_time=7.5,
                log_formatter=lambda sim_time, msg: f"[sim:{sim_time:.1f}] {msg}",
            ),
            _program_with_tell(),
        )
    )

    result = run(wrapped, handlers=default_handlers())
    listen_result = result.value

    assert listen_result.value == "ok"
    assert list(listen_result.log) == ["[sim:7.5] hello"]


def test_simulation_start_time_env_key_constant() -> None:
    assert SIMULATION_START_TIME_ENV_KEY == "simulation_start_time"
