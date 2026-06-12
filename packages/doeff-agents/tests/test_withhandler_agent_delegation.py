"""WithHandler-based agent delegation tests for doeff-agents."""


import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from doeff_time import DelayEffect

from doeff import Effect, Pass, Resume, do, run
from doeff import handler as _install_raw_handler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (
    AgentSessionLifecycle,
    AgentType,
    CaptureEffect,
    LaunchConfig,
    LaunchEffect,
    MonitorEffect,
    Observation,
    SendEffect,
    SessionHandle,
    SessionStatus,
    StopEffect,
    interactive_session,
    monitor_agent_to_completion,
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
    return SessionHandle(session_id=session_name)


@do
def _monitor_existing_completion(handle: SessionHandle, *, poll_interval: float = 0.1):
    return (
        yield from monitor_agent_to_completion(
            handle,
            poll_interval=poll_interval,
        )
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
        "delay_calls": [],
        "captures": {},
        "launch_lifecycles": [],
        "sent": [],
    }

    @do
    def handler(effect: Effect, k):
        if isinstance(effect, LaunchEffect):
            launch_effect = cast(LaunchEffect, effect)
            state["launches"].append(launch_effect.session_name)
            state["launch_lifecycles"].append(launch_effect.lifecycle)
            handle = _session_handle(
                session_name=launch_effect.session_name,
                agent_type=launch_agent_override or launch_effect.agent_type,
            )
            return (yield Resume(k, handle))

        if isinstance(effect, MonitorEffect):
            monitor_effect = cast(MonitorEffect, effect)
            session_name = monitor_effect.handle.session_id
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
            capture_effect = cast(CaptureEffect, effect)
            output = state["captures"].get(capture_effect.handle.session_id, "")
            return (yield Resume(k, output))

        if isinstance(effect, SendEffect):
            send_effect = cast(SendEffect, effect)
            state["sent"].append((send_effect.handle.session_id, send_effect.message))
            return (yield Resume(k, None))

        if isinstance(effect, StopEffect):
            stop_effect = cast(StopEffect, effect)
            state["stops"].append(stop_effect.handle.session_id)
            return (yield Resume(k, None))

        if isinstance(effect, DelayEffect):
            delay_effect = cast(DelayEffect, effect)
            state["delay_calls"].append(delay_effect.seconds)
            return (yield Resume(k, None))

        yield Pass(effect, k)

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


@do
def _run_interactive(session_name: str, config: LaunchConfig, messages: list[str]):
    return (
        yield from interactive_session(
            session_name,
            config,
            messages,
            poll_interval=0.0,
        )
    )


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
        _install_raw_handler(handler)(_run_completion("worker", _build_config(), poll_interval=0.5)),
    )

    assert result.final_status == SessionStatus.DONE
    assert result.output == "mock output"
    assert result.succeeded
    assert state["launches"] == ["worker"]
    assert state["stops"] == ["worker"]
    assert state["delay_calls"] == [0.5]


def test_monitor_agent_to_completion_cleans_existing_session() -> None:
    handler, state = _make_pipeline_handler(
        {
            "existing": [
                (SessionStatus.RUNNING, "working"),
                (SessionStatus.DONE, "final output"),
            ]
        }
    )
    handle = _session_handle("existing", AgentType.CODEX)

    result = run(
        _install_raw_handler(handler)(_monitor_existing_completion(handle, poll_interval=0.5)),
    )

    assert result.final_status == SessionStatus.DONE
    assert result.output == "final output"
    assert state["launches"] == []
    assert state["stops"] == ["existing"]
    assert state["delay_calls"] == [0.5]


def test_withhandler_delegation_returns_failure_status() -> None:
    handler, state = _make_pipeline_handler(
        {
            "worker-fail": [
                (SessionStatus.FAILED, "fatal error from mock"),
            ]
        }
    )

    result = run(
        _install_raw_handler(handler)(_run_completion("worker-fail", _build_config())),
    )

    assert result.final_status == SessionStatus.FAILED
    assert result.failed
    assert result.output == "fatal error from mock"
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
        _install_raw_handler(handler)(_run_two_completions(_build_config())),
    )

    first, second = result
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
            launch_effect = cast(LaunchEffect, effect)
            launch_calls.append(launch_effect.session_name)
            return (
                yield Resume(
                    k,
                    _session_handle(
                        launch_effect.session_name,
                        launch_effect.agent_type,
                    ),
                )
            )
        yield Pass(effect, k)

    lifecycle_handler, lifecycle_state = _make_pipeline_handler(
        {"typed-flow": [(SessionStatus.DONE, "typed done")]}
    )

    wrapped = lifecycle_handler(_install_raw_handler(launch_only_handler)(_run_completion("typed-flow", _build_config(), poll_interval=0.0)))

    result = run(wrapped)

    assert result.final_status == SessionStatus.DONE
    assert result.output == "typed done"
    assert launch_calls == ["typed-flow"]
    assert lifecycle_state["launches"] == []
    assert lifecycle_state["stops"] == ["typed-flow"]


def test_withhandler_fallback_when_primary_agent_unavailable() -> None:
    primary_attempts: list[AgentType] = []

    @do
    def primary_handler(effect: Effect, k):
        if isinstance(effect, LaunchEffect):
            launch_effect = cast(LaunchEffect, effect)
            primary_attempts.append(launch_effect.agent_type)
            if launch_effect.agent_type == AgentType.CODEX:
                handle = _session_handle(launch_effect.session_name, AgentType.CODEX)
                return (yield Resume(k, handle))
        yield Pass(effect, k)

    fallback_handler, fallback_state = _make_pipeline_handler(
        {"fallback-agent": [(SessionStatus.DONE, "fallback output")]},
        launch_agent_override=AgentType.CODEX,
    )

    wrapped = fallback_handler(_install_raw_handler(primary_handler)(_run_completion(
                "fallback-agent",
                _build_config(agent_type=AgentType.CLAUDE),
                poll_interval=0.0,
            )))

    result = run(wrapped)

    assert result.final_status == SessionStatus.DONE
    assert result.output == "fallback output"
    assert not hasattr(result.handle, "agent_type")
    assert primary_attempts == [AgentType.CLAUDE]
    assert fallback_state["launches"] == ["fallback-agent"]
    assert fallback_state["stops"] == ["fallback-agent"]


def test_interactive_session_launches_with_interactive_lifecycle() -> None:
    handler, state = _make_pipeline_handler(
        {
            "chat": [
                (SessionStatus.BLOCKED, "ready"),
                (SessionStatus.DONE, "done"),
            ]
        }
    )

    wrapped = _install_raw_handler(handler)(_run_interactive(
            "chat",
            _build_config(agent_type=AgentType.CODEX),
            ["continue"],
        ))

    result = run(wrapped)

    assert result.final_status == SessionStatus.DONE
    assert state["launches"] == ["chat"]
    assert state["launch_lifecycles"] == [AgentSessionLifecycle.INTERACTIVE]
