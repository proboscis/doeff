"""Smoke tests for doeff-agents doeff_vm handler protocol migration."""


import inspect
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_time import Delay

from doeff import Effect, EffectBase, Pass, Resume, do, run
from doeff import handler as _install_raw_handler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (
    AgentType,
    Launch,
    MockSessionScript,
    Monitor,
    SessionStatus,
    Stop,
    agent_effectful_handler,
    agent_effectful_handlers,
    mock_agent_handler,
    mock_agent_handlers,
)


@dataclass(frozen=True)
class UnknownEffect(EffectBase):
    value: str


@do
def _mock_workflow(session_name: str):
    handle = yield Launch(session_name, agent_type=AgentType.CLAUDE, work_dir=str(Path.cwd()), prompt="say hello")
    observation = yield Monitor(handle)
    yield Stop(handle)
    return observation.status


@do
def _wait_then_watch_to_the_end(session_name: str):
    # Launch, wait between observations (doeff-time Delay) and watch until the script says the session is done.
    handle = yield Launch(session_name, agent_type=AgentType.CLAUDE, work_dir=str(Path.cwd()), prompt="say hello")
    observation = yield Monitor(handle)
    while observation.status is not SessionStatus.DONE:
        yield Delay(5.0)
        observation = yield Monitor(handle)
    yield Stop(handle)
    return observation


@do
def _unknown_workflow():
    return (yield UnknownEffect(value="noop"))


@do
def _unknown_effect_fallback(effect: Effect, k):
    if isinstance(effect, UnknownEffect):
        return (yield Resume(k, effect.value))
    yield Pass(effect, k)


def test_protocol_handlers_are_not_dict_registries() -> None:
    assert isinstance(agent_effectful_handlers(), tuple)
    assert isinstance(mock_agent_handlers(), tuple)
    assert not isinstance(agent_effectful_handlers(), dict)
    assert not isinstance(mock_agent_handlers(), dict)


def test_agent_handlers_are_defhandler_program_wrappers() -> None:
    assert tuple(inspect.signature(agent_effectful_handler()).parameters) == (
        "__doeff_body__",
    )
    assert tuple(inspect.signature(mock_agent_handler()).parameters) == (
        "__doeff_body__",
    )


def test_testing_handlers_are_defhandlers_not_classes() -> None:
    # ADR-DOE-API-001 R4: the mock and scenario handlers are doeff handlers (defhandler) over a state value —
    # no handler class with a method per effect.
    handlers_dir = Path(__file__).resolve().parents[1] / "src" / "doeff_agents" / "_handlers"
    source = (handlers_dir / "testing.hy").read_text()

    assert not (handlers_dir / "testing.py").exists()
    assert "(defhandler _mock-agent-defhandler [state]" in source
    assert "(defhandler _scenario-session-defhandler [world]" in source
    assert "AgentHandler" not in source
    assert "def handler(effect: Effect, k)" not in source
    assert "yield Pass(effect, k)" not in source


def test_tmux_handler_is_a_defhandler_not_a_class() -> None:
    # ADR-DOE-API-001 R4: the production tmux handler is a defhandler whose clauses answer each effect with a
    # function over the tmux world — no handler class with a method per effect, and no handler base class.
    import doeff_agents

    handlers_dir = Path(__file__).resolve().parents[1] / "src" / "doeff_agents" / "_handlers"
    production = (handlers_dir / "production.py").read_text()
    effectful = (handlers_dir / "effectful.hy").read_text()

    assert "class TmuxAgentHandler" not in production
    assert "class AgentHandler" not in production
    assert "(defhandler tmux-agent-defhandler" in effectful
    assert "AgentHandler" not in effectful
    for name in ("AgentHandler", "TmuxAgentHandler", "agent_handler_defhandler"):
        assert not hasattr(doeff_agents, name), name


def test_unknown_effect_delegates() -> None:
    result = run(
        _install_raw_handler(_unknown_effect_fallback)(agent_effectful_handler()(_unknown_workflow()))
    )
    assert result == "noop"


def _script(session_name: str) -> MockSessionScript:
    return MockSessionScript(
        session_name=session_name,
        observations=[
            (SessionStatus.RUNNING, "working"),
            (SessionStatus.DONE, "done"),
        ],
    )


def test_mock_handler_runs_program_with_public_vm_api() -> None:
    session_name = f"mock-session-{time.time_ns()}"

    result = run(mock_agent_handler(sessions=(_script(session_name),))(_mock_workflow(session_name)))

    assert result == SessionStatus.RUNNING


def test_each_mock_handler_keeps_its_own_sessions() -> None:
    # The scripted sessions belong to the handler a call returns, not to a module-wide mock: a second
    # handler built without the script launches the same name afresh and reads only its launch status.
    session_name = f"mock-own-{time.time_ns()}"
    scripted = run(mock_agent_handler(sessions=(_script(session_name),))(_mock_workflow(session_name)))
    unscripted = run(mock_agent_handler()(_mock_workflow(session_name)))

    assert scripted == SessionStatus.RUNNING
    assert unscripted == SessionStatus.BOOTING


def test_a_mock_session_script_names_its_session() -> None:
    with pytest.raises(ValueError, match="session_name"):
        mock_agent_handler(sessions=(MockSessionScript(observations=[(SessionStatus.DONE, "")]),))


def _install_handlers(handlers, program):
    wrapped = program
    for handler in reversed(handlers):
        if tuple(inspect.signature(handler).parameters) == ("__doeff_body__",):
            wrapped = _install_raw_handler(handler)(wrapped)
        else:
            wrapped = _install_raw_handler(handler)(wrapped)
    return wrapped


def test_mock_handlers_include_time_handler_for_waits_between_observations() -> None:
    session_name = f"mock-run-completion-{time.time_ns()}"

    result = run(
        _install_handlers(
            mock_agent_handlers(sessions=(_script(session_name),)),
            _wait_then_watch_to_the_end(session_name),
        ),
    )

    assert result.status == SessionStatus.DONE
    assert result.output_snippet == "done"
