"""The recorded-workflow effects under recorded_workflows_handler, as the TUI runs them.

The workflows are recorded in a temporary state directory by StateManager (what a run
under the agentic handlers writes). No session backend is reached: the workflows the
stop test stops have no agents, and an agent's output that cannot be read is "".
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from doeff_agentic import (
    AgenticAnswerInput,
    AgenticFindWorkflow,
    AgenticInputRequested,
    AgenticListEnvironments,
    AgenticListWorkflows,
    AgenticStopWorkflow,
    AgentInfo,
    AgentStatus,
    StateManager,
    WorkflowInfo,
    WorkflowStatus,
    recorded_workflows_handler,
)
from doeff_agentic._tui import AgenticTUI
from doeff_agentic._tui.screens import _workflow_view

STARTED = datetime(2026, 10, 27, tzinfo=timezone.utc)


def _record(
    state: StateManager, workflow_id: str, minutes: int, agents: tuple[str, ...] = ()
) -> None:
    updated = STARTED + timedelta(minutes=minutes)
    state.write_workflow_meta(
        WorkflowInfo(
            id=workflow_id,
            name=f"flow-{workflow_id}",
            status=WorkflowStatus.RUNNING,
            started_at=STARTED,
            updated_at=updated,
            current_agent=agents[0] if agents else None,
            agents=(),
        )
    )
    for name in agents:
        state.write_agent_state(
            workflow_id,
            AgentInfo(
                name=name,
                status=AgentStatus.RUNNING,
                session_name=f"doeff-{workflow_id}-{name}",
                pane_id=None,
                started_at=STARTED,
            ),
        )


@pytest.fixture
def app(tmp_path) -> AgenticTUI:
    state = StateManager(tmp_path)
    _record(state, "a3f8b2c", minutes=1, agents=("reviewer",))
    _record(state, "b7e1d4f", minutes=5)
    return AgenticTUI(handlers=(recorded_workflows_handler(tmp_path),))


def test_list_answers_the_recorded_workflows_most_recent_first(app: AgenticTUI):
    workflows = app.perform(AgenticListWorkflows())

    assert [workflow.id for workflow in workflows] == ["b7e1d4f", "a3f8b2c"]


def test_find_answers_by_prefix_and_none_for_an_unrecorded_id(app: AgenticTUI):
    found = app.perform(AgenticFindWorkflow(workflow_id="a3f"))

    assert found is not None
    assert found.id == "a3f8b2c"
    assert [agent.name for agent in found.agents] == ["reviewer"]
    assert app.perform(AgenticFindWorkflow(workflow_id="ffff")) is None


def test_watch_view_reads_the_workflow_and_one_snippet_per_agent(app: AgenticTUI):
    view = app.perform(_workflow_view("a3f8b2c", 0))

    assert view is not None
    assert view.workflow.id == "a3f8b2c"
    assert view.snippets == ("-",)
    assert app.perform(_workflow_view("ffff", 0)) is None


def test_stop_records_the_workflow_as_stopped_keeping_its_last_status(tmp_path):
    state = StateManager(tmp_path)
    _record(state, "b7e1d4f", minutes=5)
    recorded = state.read_workflow("b7e1d4f")
    assert recorded is not None
    state.write_workflow_meta(replace(recorded, last_slog={"status": "fixing"}, error="boom"))
    app = AgenticTUI(handlers=(recorded_workflows_handler(tmp_path),))

    stopped = app.perform(AgenticStopWorkflow(workflow_id="b7e1d4f"))

    assert stopped == ()
    found = app.perform(AgenticFindWorkflow(workflow_id="b7e1d4f"))
    assert found is not None
    assert found.status is WorkflowStatus.STOPPED
    assert (found.last_slog, found.error) == ({"status": "fixing"}, "boom")


def test_input_requested_until_the_answer_is_written(tmp_path, app: AgenticTUI):
    assert app.perform(AgenticInputRequested(workflow_id="a3f8b2c")) is False

    workflow_dir = tmp_path / "workflows" / "a3f8b2c"
    (workflow_dir / "input_request.json").write_text('{"prompt": "approve?"}')
    assert app.perform(AgenticInputRequested(workflow_id="a3f8b2c")) is True

    app.perform(AgenticAnswerInput(workflow_id="a3f8b2c", text="approve"))
    assert (workflow_dir / "input_response.txt").read_text() == "approve"


def test_list_environments_answers_none_without_event_logs(app: AgenticTUI):
    assert app.perform(AgenticListEnvironments()) == ()
    assert app.perform(AgenticListEnvironments(workflow_id="a3f")) == ()
