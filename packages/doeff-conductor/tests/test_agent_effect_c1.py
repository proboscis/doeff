"""C1 tests for conductor's schema-validated agent boundary."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from doeff_agents import AgentSessionLifecycle, AgentSessionSnapshot, AgentType, SessionStatus
from doeff_agents.effects import AwaitOutcome, AwaitStatus
from doeff_agents.result_validation import validate_result_payload
from doeff_conductor import CreateWorkspace
from doeff_conductor.effects import Agent, AgentAttemptExhaustedError, AgentEffect, AgentTask
from doeff_conductor.handlers import run_sync
from doeff_conductor.handlers.agent_handler import AgentdAgentBackend, AgentHandler
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
        env = yield CreateWorkspace(workspace_id="ws-impl")
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
        env = yield CreateWorkspace(workspace_id="ws-impl")
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
        env = yield CreateWorkspace(workspace_id="ws-codex-real")
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


def test_agent_handler_delegates_schema_agent_to_agentd_client(
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

    class AvailableAdapter:
        def is_available(self) -> bool:
            return True

    fake_client = FakeAgentdClient()
    monkeypatch.setattr(
        "doeff_agents.handlers.daemon.get_adapter",
        lambda _agent_type: AvailableAdapter(),
    )
    handler = AgentHandler(
        workspace_resolver=resolve_workspace,
        backend=AgentdAgentBackend(client=fake_client),
    )
    task = AgentTask(
        run_id="run-agentd",
        node_id="implement",
        attempt=0,
        env=workspace,
        prompt="return JSON",
        result_schema=IMPLEMENT_SCHEMA,
        verification_class="test-verifiable",
        agent_type="codex",
        max_retries=0,
    )

    result = handler.handle_agent(AgentEffect(task=task))

    assert result == {"summary": "ok", "files_changed": []}
    assert fake_client.launches[0]["session_id"] == "run-agentd-implement-0"
    assert fake_client.launches[0]["prompt"] == "return JSON"
    assert fake_client.launches[0]["expected_result"] == {
        "payload_schema": IMPLEMENT_SCHEMA,
        "max_retries": 0,
    }
    assert fake_client.awaited == [("run-agentd-implement-0", None)]


def test_agent_handler_has_no_subprocess_or_os_environ_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "doeff_conductor"
        / "handlers"
        / "agent_handler.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            imported_names.add(module_name)
            imported_names.update(f"{module_name}.{alias.name}" for alias in node.names)

    assert "subprocess" not in imported_names
    assert "os" not in imported_names
    assert "os.environ" not in imported_names
    assert "os.getenv" not in imported_names


class FakeAgentdClient:
    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.awaited: list[tuple[str, float | None]] = []

    def get_session(self, _session_id: str) -> None:
        return None

    def launch_session(self, **payload: Any) -> AgentSessionSnapshot:
        self.launches.append(payload)
        return AgentSessionSnapshot(
            session_id=payload["session_id"],
            session_name=payload["session_name"],
            agent_type=AgentType(payload["agent_type"]),
            work_dir=payload["work_dir"],
            lifecycle=AgentSessionLifecycle.RUN_TO_COMPLETION,
            status=SessionStatus.RUNNING,
            backend_kind="agentd",
        )

    def await_result(
        self,
        session_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AwaitOutcome:
        self.awaited.append((session_id, timeout_seconds))
        return AwaitOutcome(
            status=AwaitStatus.EXITED,
            result={"summary": "ok", "files_changed": []},
        )

    def list_sessions(self, _query: object = None) -> tuple[AgentSessionSnapshot, ...]:
        return ()

    def capture_session(self, _session_id: str, *, lines: int = 100) -> str:
        return ""

    def send_session(
        self,
        _session_id: str,
        _message: str,
        *,
        enter: bool = True,
        literal: bool = True,
    ) -> None:
        return None

    def cancel_session(self, session_id: str) -> AgentSessionSnapshot:
        return AgentSessionSnapshot(
            session_id=session_id,
            session_name=session_id,
            agent_type=AgentType.CODEX,
            work_dir=Path("."),
            status=SessionStatus.STOPPED,
        )

    def cleanup_session(self, session_id: str) -> AgentSessionSnapshot:
        return AgentSessionSnapshot(
            session_id=session_id,
            session_name=session_id,
            agent_type=AgentType.CODEX,
            work_dir=Path("."),
            status=SessionStatus.STOPPED,
        )
