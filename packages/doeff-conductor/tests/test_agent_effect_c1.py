"""C1 tests for conductor's schema-validated agent boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from doeff import do

from doeff_conductor import CreateWorktree
from doeff_conductor.effects import Agent, AgentAttemptExhaustedError, AgentTask
from doeff_conductor.handlers import run_sync
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers


IMPLEMENT_SCHEMA = {
    "type": "object",
    "required": ["files_changed", "summary"],
    "properties": {
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "findings"],
    "properties": {
        "verdict": {"enum": ["PASS", "CHANGES_REQUESTED"]},
        "findings": {"type": "array"},
    },
    "additionalProperties": False,
}


def _run(program, runtime: MockConductorRuntime):
    return run_sync(program, scheduled_handlers=mock_handlers(runtime=runtime))


def test_two_node_workflow_runs_on_scenario_stubs(tmp_path: Path) -> None:
    runtime = MockConductorRuntime(tmp_path)
    runtime.configure_agent_script(
        "run-001-implement-0",
        [{"files_changed": ["src/app.py"], "summary": "implemented"}],
    )
    runtime.configure_agent_script(
        "run-001-review-0",
        [{"verdict": "PASS", "findings": []}],
    )

    @do
    def workflow():
        env = yield CreateWorktree(suffix="impl")
        implementation = yield Agent(
            AgentTask(
                run_id="run-001",
                node_id="implement",
                attempt=0,
                env=env,
                prompt="implement feature",
                result_schema=IMPLEMENT_SCHEMA,
                verification_class="test-verifiable",
            )
        )
        review = yield Agent(
            AgentTask(
                run_id="run-001",
                node_id="review",
                attempt=0,
                env=env,
                prompt=f"review {implementation['summary']}",
                result_schema=REVIEW_SCHEMA,
                verification_class="review",
            )
        )
        return implementation, review

    result = _run(workflow(), runtime)

    assert result.is_ok
    implementation, review = result.value
    assert implementation["files_changed"] == ["src/app.py"]
    assert review["verdict"] == "PASS"


def test_schema_invalid_retry_exhaustion_fails_typed(tmp_path: Path) -> None:
    runtime = MockConductorRuntime(tmp_path)
    runtime.configure_agent_script(
        "run-002-implement-0",
        [
            {"summary": "missing files"},
            {"summary": "still missing files"},
        ],
    )

    @do
    def workflow():
        env = yield CreateWorktree(suffix="impl")
        return (
            yield Agent(
                AgentTask(
                    run_id="run-002",
                    node_id="implement",
                    attempt=0,
                    env=env,
                    prompt="implement feature",
                    result_schema=IMPLEMENT_SCHEMA,
                    verification_class="test-verifiable",
                    max_retries=1,
                )
            )
        )

    with pytest.raises(AgentAttemptExhaustedError) as exc_info:
        _run(workflow(), runtime)

    assert exc_info.value.session_id == "run-002-implement-0"
    assert "files_changed" in exc_info.value.last_error.message
