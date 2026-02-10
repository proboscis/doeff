"""Smoke tests for doeff-agents doeff_vm handler protocol migration."""

from __future__ import annotations

import inspect
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from doeff import Delegate, EffectBase, default_handlers, do, run

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (
    AgentType,
    Launch,
    LaunchConfig,
    MockSessionScript,
    Monitor,
    SessionStatus,
    Stop,
    agent_effectful_handler,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handler,
    mock_agent_handlers,
)


@dataclass(frozen=True)
class UnknownEffect(EffectBase):
    value: str


@do
def _mock_workflow(session_name: str, config: LaunchConfig):
    handle = yield Launch(session_name, config)
    observation = yield Monitor(handle)
    yield Stop(handle)
    return observation.status


def test_protocol_handlers_are_not_dict_registries() -> None:
    assert isinstance(agent_effectful_handlers(), tuple)
    assert isinstance(mock_agent_handlers(), tuple)
    assert not isinstance(agent_effectful_handlers(), dict)
    assert not isinstance(mock_agent_handlers(), dict)


def test_protocol_handlers_have_effect_k_signature() -> None:
    assert tuple(inspect.signature(agent_effectful_handler()).parameters) == ("effect", "k")
    assert tuple(inspect.signature(mock_agent_handler()).parameters) == ("effect", "k")


def test_unknown_effect_delegates() -> None:
    handler = agent_effectful_handler()
    step = next(handler(UnknownEffect(value="noop"), object()))
    assert isinstance(step, Delegate)


def test_mock_handler_runs_program_with_public_vm_api() -> None:
    session_name = f"mock-session-{time.time_ns()}"
    configure_mock_session(
        session_name,
        MockSessionScript(
            observations=[
                (SessionStatus.RUNNING, "working"),
                (SessionStatus.DONE, "done"),
            ]
        ),
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="say hello",
    )

    result = run(
        _mock_workflow(session_name, config),
        handlers=[*mock_agent_handlers(), *default_handlers()],
    )

    assert result.is_ok()
    assert result.value == SessionStatus.RUNNING
