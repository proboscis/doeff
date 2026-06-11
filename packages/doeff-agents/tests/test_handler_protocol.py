"""Smoke tests for doeff-agents doeff_vm handler protocol migration."""


import inspect
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from doeff import Effect, EffectBase, Pass, Resume, WithHandler, do, run

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
    run_agent_to_completion,
)


@dataclass(frozen=True)
class UnknownEffect(EffectBase):
    value: str


@do
def _mock_workflow(session_name: str, config: LaunchConfig):
    handle = yield Launch(
        session_name,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        prompt=config.prompt,
        model=config.model,
        mcp_tools=config.mcp_tools,
    )
    observation = yield Monitor(handle)
    yield Stop(handle)
    return observation.status


@do
def _mock_run_to_completion(session_name: str, config: LaunchConfig):
    return (
        yield from run_agent_to_completion(
            session_name,
            config,
            poll_interval=5.0,
        )
    )


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


def test_unknown_effect_delegates() -> None:
    result = run(
        WithHandler(
            _unknown_effect_fallback,
            agent_effectful_handler()(_unknown_workflow()),
        )
    )
    assert result == "noop"


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

    result = run(mock_agent_handler()(_mock_workflow(session_name, config)))

    assert result == SessionStatus.RUNNING


def _install_handlers(handlers, program):
    wrapped = program
    for handler in reversed(handlers):
        if tuple(inspect.signature(handler).parameters) == ("__doeff_body__",):
            wrapped = handler(wrapped)
        else:
            wrapped = WithHandler(handler, wrapped)
    return wrapped


def test_mock_handlers_include_time_handler_for_run_to_completion() -> None:
    session_name = f"mock-run-completion-{time.time_ns()}"
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
        _install_handlers(
            mock_agent_handlers(),
            _mock_run_to_completion(session_name, config),
        ),
    )

    assert result.succeeded
    assert result.output == "done"
