"""C1 tests for the L2 session algebra and schema-driven agent effect."""

from __future__ import annotations

from pathlib import Path

import pytest
from doeff_agents import AgentType
from doeff_agents.effects import (
    AgentAttemptExhaustedError,
    AgentSpec,
    AgentTask,
    AgentValidationErrorKind,
    AwaitResult,
    AwaitResultEffect,
    AwaitStatus,
    FollowUp,
    FollowUpEffect,
    L2SessionHandle,
    LaunchSession,
    LaunchSessionEffect,
    ReleaseSession,
    ReleaseSessionEffect,
    StopSession,
    StopSessionEffect,
    agent,
)
from doeff_agents.handlers.testing import ScenarioAgentHandler, ScenarioStep

from doeff import do, run

ARTIFACT_SCHEMA = {
    "type": "object",
    "required": ["summary", "ok"],
    "properties": {
        "summary": {"type": "string"},
        "ok": {"type": "boolean"},
    },
    "additionalProperties": False,
}


@do
def _launch_twice(work_dir: Path):
    spec = AgentSpec(
        run_id="run-001",
        node_id="node-a",
        attempt=0,
        agent_type=AgentType.CODEX,
        work_dir=work_dir,
        prompt="return JSON",
        result_schema=ARTIFACT_SCHEMA,
    )
    first = yield LaunchSession(spec)
    second = yield LaunchSession(spec)
    return first, second


@do
def _follow_up_and_release(work_dir: Path):
    spec = AgentSpec(
        run_id="run-001",
        node_id="node-b",
        attempt=0,
        agent_type=AgentType.CODEX,
        work_dir=work_dir,
        prompt="return JSON",
        result_schema=ARTIFACT_SCHEMA,
    )
    handle = yield LaunchSession(spec)
    outcome = yield AwaitResult(handle, timeout_seconds=5.0)
    retry_handle = yield FollowUp(handle, "validation failed")
    yield StopSession(retry_handle, reason="test cleanup")
    yield ReleaseSession(retry_handle)
    return handle, outcome, retry_handle


@do
def _agent_task(work_dir: Path):
    return (
        yield agent(
            AgentTask(
                run_id="run-001",
                node_id="node-c",
                attempt=0,
                agent_type=AgentType.CODEX,
                work_dir=work_dir,
                prompt="return JSON",
                result_schema=ARTIFACT_SCHEMA,
                max_retries=2,
            )
        )
    )


def test_l2_launch_is_idempotent_and_handle_is_opaque(tmp_path: Path) -> None:
    handler = ScenarioAgentHandler()

    first, second = run(handler.wrap(_launch_twice(tmp_path)))

    assert first == second
    assert isinstance(first, L2SessionHandle)
    assert first.session_id == "run-001-node-a-0"
    assert not hasattr(first, "pane_id")
    assert handler.launch_count("run-001-node-a-0") == 1


def test_l2_effect_constructors_return_core_effects(tmp_path: Path) -> None:
    spec = AgentSpec(
        run_id="run-001",
        node_id="node-a",
        attempt=0,
        agent_type=AgentType.CODEX,
        work_dir=tmp_path,
        prompt="return JSON",
        result_schema=ARTIFACT_SCHEMA,
    )
    handle = L2SessionHandle(session_id="run-001-node-a-0")

    assert isinstance(LaunchSession(spec), LaunchSessionEffect)
    assert isinstance(AwaitResult(handle, timeout_seconds=1.0), AwaitResultEffect)
    assert isinstance(FollowUp(handle, "retry"), FollowUpEffect)
    assert isinstance(StopSession(handle, reason="stop"), StopSessionEffect)
    assert isinstance(ReleaseSession(handle), ReleaseSessionEffect)


def test_agent_retries_invalid_schema_then_returns_valid_payload(tmp_path: Path) -> None:
    handler = ScenarioAgentHandler(
        scripts={
            "run-001-node-c-0": [
                ScenarioStep.invalid(
                    payload={"summary": "missing ok"},
                    validation_error="required property 'ok' is missing",
                ),
                ScenarioStep.success({"summary": "fixed", "ok": True}),
            ]
        }
    )

    result = run(handler.wrap(_agent_task(tmp_path)))

    assert result == {"summary": "fixed", "ok": True}
    assert handler.follow_up_messages("run-001-node-c-0") == [
        "The result artifact was invalid: required property 'ok' is missing. "
        "Return a corrected result artifact that satisfies the schema."
    ]


def test_agent_distinguishes_absent_result_in_retry_message(tmp_path: Path) -> None:
    handler = ScenarioAgentHandler(
        scripts={
            "run-001-node-c-0": [
                ScenarioStep.absent(),
                ScenarioStep.success({"summary": "reported", "ok": True}),
            ]
        }
    )

    result = run(handler.wrap(_agent_task(tmp_path)))

    assert result == {"summary": "reported", "ok": True}
    assert handler.follow_up_messages("run-001-node-c-0") == [
        "No result artifact was produced. Return the required result artifact as JSON."
    ]


def test_agent_raises_typed_failure_on_retry_exhaustion(tmp_path: Path) -> None:
    handler = ScenarioAgentHandler(
        scripts={
            "run-001-node-c-0": [
                ScenarioStep.invalid(
                    payload={"summary": "bad"},
                    validation_error="required property 'ok' is missing",
                ),
                ScenarioStep.invalid(
                    payload={"summary": "still bad"},
                    validation_error="required property 'ok' is missing",
                ),
                ScenarioStep.invalid(
                    payload={"summary": "never fixed"},
                    validation_error="required property 'ok' is missing",
                ),
            ]
        }
    )

    with pytest.raises(AgentAttemptExhaustedError) as exc_info:
        run(handler.wrap(_agent_task(tmp_path)))

    assert exc_info.value.session_id == "run-001-node-c-0"
    assert exc_info.value.last_error.kind == AgentValidationErrorKind.INVALID
    assert "required property 'ok' is missing" in exc_info.value.last_error.message


def test_agent_treats_awaiting_input_as_typed_failure(tmp_path: Path) -> None:
    handler = ScenarioAgentHandler(
        scripts={
            "run-001-node-c-0": [
                ScenarioStep.awaiting_input("needs human confirmation"),
            ]
        }
    )

    with pytest.raises(AgentAttemptExhaustedError) as exc_info:
        run(handler.wrap(_agent_task(tmp_path)))

    assert exc_info.value.last_error.kind == AgentValidationErrorKind.AWAITING_INPUT
    assert "needs human confirmation" in exc_info.value.last_error.message


def test_agent_never_follows_up_a_terminal_failure(tmp_path: Path) -> None:
    """Single retry authority: a failure from a TERMINAL session is final.

    The supervisor (agentd) spent the result-contract retries and reaped
    the pane before the await resolved — a follow-up would land on a dead
    session. Observed live four times as 'tmux send-keys failed' replacing
    the clean exhaustion error.
    """
    handler = ScenarioAgentHandler(
        scripts={
            "run-001-node-c-0": [
                ScenarioStep.terminal_invalid(
                    validation_error="output validation exhausted after 2 retries: bad schema",
                ),
            ]
        }
    )

    with pytest.raises(AgentAttemptExhaustedError) as exc_info:
        run(handler.wrap(_agent_task(tmp_path)))

    assert exc_info.value.last_error.kind == AgentValidationErrorKind.INVALID
    assert "exhausted after 2 retries" in exc_info.value.last_error.message
    # The defining assertion: no follow-up was sent to the dead session.
    assert handler.follow_up_messages("run-001-node-c-0") == []


def test_agent_reawaits_on_timeout_without_follow_up(tmp_path: Path) -> None:
    """A timed-out await means the session is alive and still working.

    Re-await it; injecting a retry prompt into a healthily working agent
    is noise (observed live) and must not happen.
    """
    handler = ScenarioAgentHandler(
        scripts={
            "run-001-node-c-0": [
                ScenarioStep.timeout(),
                ScenarioStep.success({"summary": "finished late", "ok": True}),
            ]
        }
    )

    result = run(handler.wrap(_agent_task(tmp_path)))

    assert result == {"summary": "finished late", "ok": True}
    assert handler.follow_up_messages("run-001-node-c-0") == []


def test_scenario_handler_supports_timeout_outcome(tmp_path: Path) -> None:
    handler = ScenarioAgentHandler(
        scripts={"run-001-node-b-0": [ScenarioStep.timeout()]},
    )

    handle, outcome, retry_handle = run(handler.wrap(_follow_up_and_release(tmp_path)))

    assert outcome.status == AwaitStatus.TIMED_OUT
    assert outcome.result is None
    assert retry_handle == handle
    assert handler.stopped_sessions == ["run-001-node-b-0"]
    assert handler.released_sessions == ["run-001-node-b-0"]
