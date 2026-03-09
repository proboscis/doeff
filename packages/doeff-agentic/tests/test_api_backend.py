"""Backend injection tests for AgenticAPI."""


from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from doeff_agentic.api import AgenticAPI
from doeff_agentic.state import StateManager
from doeff_agentic.types import AgentInfo, AgentStatus, WorkflowInfo, WorkflowStatus


class FakeBackend:
    def __init__(self) -> None:
        self.attached: list[str] = []
        self.sent: list[tuple[str, str, bool, bool]] = []
        self.killed: list[str] = []
        self.captures: dict[str, str] = {}

    def attach_session(self, session: str) -> None:
        self.attached.append(session)

    def send_keys(
        self,
        target: str,
        keys: str,
        *,
        literal: bool = True,
        enter: bool = True,
    ) -> None:
        self.sent.append((target, keys, literal, enter))

    def kill_session(self, session: str) -> None:
        self.killed.append(session)

    def capture_pane(self, target: str, lines: int = 100, *, strip_ansi_codes: bool = True) -> str:
        return self.captures.get(target, "")


def _write_workflow(state_dir: Path) -> WorkflowInfo:
    manager = StateManager(state_dir)
    now = datetime.now(timezone.utc)
    workflow = WorkflowInfo(
        id="wf12345",
        name="demo",
        status=WorkflowStatus.RUNNING,
        started_at=now,
        updated_at=now,
        current_agent="reviewer",
        agents=(
            AgentInfo(
                name="reviewer",
                status=AgentStatus.RUNNING,
                session_name="sess-reviewer",
                pane_id="%42",
                started_at=now,
            ),
        ),
    )
    manager.write_workflow_meta(workflow)
    for agent in workflow.agents:
        manager.write_agent_state(workflow.id, agent)
    return workflow


def test_api_attach_send_capture_and_stop_use_injected_backend(tmp_path: Path) -> None:
    workflow = _write_workflow(tmp_path)
    backend = FakeBackend()
    backend.captures["%42"] = "assistant output"
    api = AgenticAPI(state_dir=tmp_path, backend=backend)

    api.attach(workflow.id)
    assert backend.attached == ["sess-reviewer"]

    assert api.send_message(workflow.id, "continue") is True
    assert backend.sent == [("%42", "continue", True, True)]

    assert api.get_agent_output(workflow.id) == "assistant output"

    stopped = api.stop(workflow.id)
    assert stopped == ["reviewer"]
    assert backend.killed == ["sess-reviewer"]


def test_api_send_message_prefers_wait_for_input_file_over_backend(tmp_path: Path) -> None:
    workflow = _write_workflow(tmp_path)
    backend = FakeBackend()
    api = AgenticAPI(state_dir=tmp_path, backend=backend)

    workflow_dir = tmp_path / "workflows" / workflow.id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "input_request.json").write_text('{"prompt":"continue"}')

    assert api.send_message(workflow.id, "user reply") is True
    assert backend.sent == []
    assert (workflow_dir / "input_response.txt").read_text() == "user reply"
