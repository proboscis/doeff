"""C1 tests for conductor's schema-validated agent boundary."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from doeff_agents.result_validation import validate_result_payload
from doeff_conductor import CreateWorkspace
from doeff_conductor.effects import Agent, AgentAttemptExhaustedError, AgentEffect, AgentTask
from doeff_conductor.handlers import run_sync
from doeff_conductor.handlers.agent_handler import AgentHandler, CodexExecAgentBackend
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.types import Workspace

from doeff import do

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


def _run_real_codex_agent(
    effect: AgentEffect,
    tmp_path: Path,
    runtime: MockConductorRuntime,
) -> dict[str, Any]:
    codex_bin = shutil.which("codex")
    if codex_bin is None:
        pytest.skip("codex CLI is not installed")

    schema_path = tmp_path / "codex-agent.schema.json"
    output_path = tmp_path / "codex-agent-output.json"
    schema_path.write_text(json.dumps(effect.task.result_schema), encoding="utf-8")
    prompt = (
        "You are a schema-constrained integration test worker. "
        "Return a JSON object with summary='codex schema ok' and files_changed=[] only. "
        "Do not inspect files and do not include Markdown."
    )

    completed = subprocess.run(
        [
            codex_bin,
            "exec",
            "--ephemeral",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            str(runtime.resolve_path(effect.task.env)),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            prompt,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=55,
    )
    assert completed.returncode == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    validation_error = validate_result_payload(payload, effect.task.result_schema)
    assert validation_error is None, validation_error
    return payload


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
        env = yield CreateWorkspace(suffix="impl")
        implementation = yield Agent(
            AgentTask(
                run_id="run-001",
                node_id="implement",
                attempt=0,
                env=env,
                prompt="implement feature",
                result_schema=IMPLEMENT_SCHEMA,
                verification_class="test-verifiable",
                agent_type="codex",
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
                agent_type="codex",
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
        env = yield CreateWorkspace(suffix="impl")
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
                    agent_type="codex",
                    max_retries=1,
                )
            )
        )

    result = _run(workflow(), runtime)

    assert result.is_err()
    assert isinstance(result.error, AgentAttemptExhaustedError)
    assert result.error.session_id == "run-002-implement-0"
    assert "files_changed" in result.error.last_error.message


def test_real_codex_worker_returns_schema_valid_json_through_agent(tmp_path: Path) -> None:
    runtime = MockConductorRuntime(tmp_path)

    @do
    def workflow():
        env = yield CreateWorkspace(suffix="codex-real")
        return (
            yield Agent(
                AgentTask(
                    run_id="run-real-codex",
                    node_id="implement",
                    attempt=0,
                    env=env,
                    prompt="return a minimal implementation artifact",
                    result_schema=IMPLEMENT_SCHEMA,
                    verification_class="test-verifiable",
                    agent_type="codex",
                    max_retries=0,
                )
            )
        )

    result = run_sync(
        workflow(),
        scheduled_handlers=mock_handlers(
            runtime=runtime,
            overrides={
                AgentEffect: lambda effect: _run_real_codex_agent(effect, tmp_path, runtime),
            },
        ),
    )

    assert result.is_ok()
    assert result.value == {"summary": "codex schema ok", "files_changed": []}


def test_agent_handler_codex_exec_mode_uses_native_structured_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Workspace(
        id="workspace-1",
        repo="default",
        ref="main",
        base_ref="main",
        created_at=datetime.now(timezone.utc),
    )
    def resolve_workspace(_workspace: Workspace) -> Path:
        return tmp_path

    handler = AgentHandler(
        workspace_resolver=resolve_workspace,
        backend=CodexExecAgentBackend(codex_home=tmp_path / "codex-home"),
    )
    task = AgentTask(
        run_id="run-codex-exec",
        node_id="implement",
        attempt=0,
        env=workspace,
        prompt="return JSON",
        result_schema=IMPLEMENT_SCHEMA,
        verification_class="test-verifiable",
        agent_type="codex",
        max_retries=0,
    )

    def fake_run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        output_index = args.index("--output-last-message") + 1
        output_path = Path(args[output_index])
        output_path.write_text(
            json.dumps({"summary": "ok", "files_changed": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("doeff_conductor.handlers.agent_handler.shutil.which", lambda _: "codex")
    monkeypatch.setattr("doeff_conductor.handlers.agent_handler.subprocess.run", fake_run)

    result = handler.handle_agent(AgentEffect(task=task))

    assert result == {"summary": "ok", "files_changed": []}
