"""WithHandler-based agent delegation tests for doeff-agents."""


import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from doeff import Effect, Pass, Resume, WithHandler, default_handlers, do, run

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (
    AgentType,
    CaptureEffect,
    LaunchConfig,
    LaunchEffect,
    MonitorEffect,
    Observation,
    SessionHandle,
    SessionStatus,
    SleepEffect,
    StopEffect,
    run_agent_to_completion,
)

ProtocolHandler = Callable[[Any, Any], Any]
Script = dict[str, list[tuple[SessionStatus, str]]]


def _build_config(*, agent_type: AgentType = AgentType.CLAUDE) -> LaunchConfig:
    return LaunchConfig(
        agent_type=agent_type,
        work_dir=Path.cwd(),
        prompt="test prompt",
    )


def _session_handle(session_name: str, agent_type: AgentType) -> SessionHandle:
    return SessionHandle(
        session_name=session_name,
        pane_id=f"%{session_name}",
        agent_type=agent_type,
        work_dir=Path.cwd(),
    )


def _make_pipeline_handler(
    scripts: Script,
    *,
    launch_agent_override: AgentType | None = None,
) -> tuple[ProtocolHandler, dict[str, Any]]:
    queue = {session_name: list(observations) for session_name, observations in scripts.items()}
    state: dict[str, Any] = {
        "launches": [],
        "stops": [],
        "sleep_calls": [],
        "captures": {},
    }

    @do
    def handler(effect: Effect, k):
        if isinstance(effect, LaunchEffect):
            state["launches"].append(effect.session_name)
            handle = _session_handle(
                session_name=effect.session_name,
                agent_type=launch_agent_override or effect.config.agent_type,
            )
            return (yield Resume(k, handle))

        if isinstance(effect, MonitorEffect):
            session_name = effect.handle.session_name
            script = queue.setdefault(session_name, [])

            if script:
                status, output = script.pop(0)
            else:
                status, output = SessionStatus.DONE, ""

            state["captures"][session_name] = output
            observation = Observation(
                status=status,
                output_changed=True,
                output_snippet=output,
            )
            return (yield Resume(k, observation))

        if isinstance(effect, CaptureEffect):
            output = state["captures"].get(effect.handle.session_name, "")
            return (yield Resume(k, output))

        if isinstance(effect, StopEffect):
            state["stops"].append(effect.handle.session_name)
            return (yield Resume(k, None))

        if isinstance(effect, SleepEffect):
            state["sleep_calls"].append(effect.seconds)
            return (yield Resume(k, None))

        yield Pass()

    return handler, state


@do
def _run_completion(session_name: str, config: LaunchConfig, *, poll_interval: float = 0.1):
    return (
        yield from run_agent_to_completion(
            session_name=session_name,
            config=config,
            poll_interval=poll_interval,
        )
    )


@do
def _run_two_completions(config: LaunchConfig):
    first = yield from run_agent_to_completion("alpha", config, poll_interval=0.0)
    second = yield from run_agent_to_completion("beta", config, poll_interval=0.0)
    return first, second


def test_withhandler_delegation_returns_success() -> None:
    handler, state = _make_pipeline_handler(
        {
            "worker": [
                (SessionStatus.RUNNING, "working"),
                (SessionStatus.DONE, "mock output"),
            ]
        }
    )

    result = run(
        WithHandler(handler=handler, expr=_run_completion("worker", _build_config(), poll_interval=0.5)),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value.final_status == SessionStatus.DONE
    assert result.value.output == "mock output"
    assert result.value.succeeded
    assert state["launches"] == ["worker"]
    assert state["stops"] == ["worker"]
    assert state["sleep_calls"] == [0.5]


def test_withhandler_delegation_returns_failure_status() -> None:
    handler, state = _make_pipeline_handler(
        {
            "worker-fail": [
                (SessionStatus.FAILED, "fatal error from mock"),
            ]
        }
    )

    result = run(
        WithHandler(handler=handler, expr=_run_completion("worker-fail", _build_config())),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value.final_status == SessionStatus.FAILED
    assert result.value.failed
    assert result.value.output == "fatal error from mock"
    assert state["launches"] == ["worker-fail"]
    assert state["stops"] == ["worker-fail"]


def test_withhandler_multiple_agent_delegations_in_sequence() -> None:
    handler, state = _make_pipeline_handler(
        {
            "alpha": [(SessionStatus.DONE, "alpha output")],
            "beta": [(SessionStatus.DONE, "beta output")],
        }
    )

    result = run(
        WithHandler(handler=handler, expr=_run_two_completions(_build_config())),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    first, second = result.value
    assert first.final_status == SessionStatus.DONE
    assert second.final_status == SessionStatus.DONE
    assert first.output == "alpha output"
    assert second.output == "beta output"
    assert state["launches"] == ["alpha", "beta"]
    assert state["stops"] == ["alpha", "beta"]


def test_withhandler_protocol_compliance_with_explicit_launch_handler() -> None:
    launch_calls: list[str] = []

    @do
    def launch_only_handler(effect: Effect, k):
        if isinstance(effect, LaunchEffect):
            launch_calls.append(effect.session_name)
            return (yield Resume(k, _session_handle(effect.session_name, effect.config.agent_type)))
        yield Pass()

    lifecycle_handler, lifecycle_state = _make_pipeline_handler(
        {"typed-flow": [(SessionStatus.DONE, "typed done")]}
    )

    wrapped = WithHandler(
        handler=lifecycle_handler,
        expr=WithHandler(
            handler=launch_only_handler,
            expr=_run_completion("typed-flow", _build_config(), poll_interval=0.0),
        ),
    )

    result = run(wrapped, handlers=default_handlers())

    assert result.is_ok()
    assert result.value.final_status == SessionStatus.DONE
    assert result.value.output == "typed done"
    assert launch_calls == ["typed-flow"]
    assert lifecycle_state["launches"] == []
    assert lifecycle_state["stops"] == ["typed-flow"]


def test_withhandler_fallback_when_primary_agent_unavailable() -> None:
    primary_attempts: list[AgentType] = []

    @do
    def primary_handler(effect: Effect, k):
        if isinstance(effect, LaunchEffect):
            primary_attempts.append(effect.config.agent_type)
            if effect.config.agent_type == AgentType.CODEX:
                handle = _session_handle(effect.session_name, AgentType.CODEX)
                return (yield Resume(k, handle))
        yield Pass()

    fallback_handler, fallback_state = _make_pipeline_handler(
        {"fallback-agent": [(SessionStatus.DONE, "fallback output")]},
        launch_agent_override=AgentType.CODEX,
    )

    wrapped = WithHandler(
        handler=fallback_handler,
        expr=WithHandler(
            handler=primary_handler,
            expr=_run_completion(
                "fallback-agent",
                _build_config(agent_type=AgentType.CLAUDE),
                poll_interval=0.0,
            ),
        ),
    )

    result = run(wrapped, handlers=default_handlers())

    assert result.is_ok()
    assert result.value.final_status == SessionStatus.DONE
    assert result.value.output == "fallback output"
    assert result.value.handle.agent_type == AgentType.CODEX
    assert primary_attempts == [AgentType.CLAUDE]
    assert fallback_state["launches"] == ["fallback-agent"]
    assert fallback_state["stops"] == ["fallback-agent"]
