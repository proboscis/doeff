"""Tests for JSONL event logging system."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from doeff_agentic.event_log import (
    EnvironmentEventType,
    EventLogReader,
    EventLogWriter,
    LogEvent,
    MessageEventType,
    SessionEventType,
    WorkflowEventType,
    WorkflowState,
    read_log_file,
)
from doeff_agentic.types import (
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
)


@pytest.fixture
def temp_log_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_workflow_handle():
    return AgenticWorkflowHandle(
        id="abc1234",
        name="test-workflow",
        status=AgenticWorkflowStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
        metadata={"key": "value"},
    )


@pytest.fixture
def sample_session_handle():
    return AgenticSessionHandle(
        id="sess_12345",
        name="reviewer",
        workflow_id="abc1234",
        environment_id="env_1234",
        status=AgenticSessionStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        title="Code Reviewer",
        agent="claude",
        model="claude-3",
    )


@pytest.fixture
def sample_environment_handle():
    return AgenticEnvironmentHandle(
        id="env_1234",
        env_type=AgenticEnvironmentType.WORKTREE,
        name="review-env",
        working_dir="/tmp/worktree/abc",
        created_at=datetime.now(timezone.utc),
        base_commit="main",
    )


class TestLogEvent:
    def test_to_dict(self):
        ts = datetime(2026, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
        event = LogEvent(
            ts=ts,
            event_type="test.event",
            data={"key": "value"},
        )
        result = event.to_dict()

        assert result["ts"] == "2026-01-04T12:00:00+00:00"
        assert result["event_type"] == "test.event"
        assert result["key"] == "value"

    def test_from_dict(self):
        data = {
            "ts": "2026-01-04T12:00:00+00:00",
            "event_type": "test.event",
            "key": "value",
        }
        event = LogEvent.from_dict(data)

        assert event.event_type == "test.event"
        assert event.data["key"] == "value"
        assert event.ts.year == 2026


class TestEventLogWriter:
    def test_workflow_log_path(self, temp_log_dir):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)

        assert writer.workflow_log_path == temp_log_dir / "abc1234" / "workflow.jsonl"

    def test_session_log_path(self, temp_log_dir):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)

        path = writer.session_log_path("reviewer")
        assert path == temp_log_dir / "abc1234" / "sessions" / "reviewer.jsonl"

    def test_environment_log_path(self, temp_log_dir):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)

        path = writer.environment_log_path("env_1234")
        assert path == temp_log_dir / "abc1234" / "environments" / "env_1234.jsonl"

    def test_log_workflow_created(self, temp_log_dir, sample_workflow_handle):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        writer.log_workflow_created(sample_workflow_handle)

        assert writer.workflow_log_path.exists()

        with writer.workflow_log_path.open() as f:
            line = f.readline()
            data = json.loads(line)

        assert data["event_type"] == "workflow.created"
        assert data["id"] == "abc1234"
        assert data["name"] == "test-workflow"

    def test_log_session_created(self, temp_log_dir, sample_session_handle):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        writer.log_session_created(sample_session_handle)

        assert writer.workflow_log_path.exists()
        assert writer.session_log_path("reviewer").exists()

        events = list(read_log_file(writer.workflow_log_path))
        assert len(events) == 1
        assert events[0].event_type == "session.created"
        assert events[0].data["name"] == "reviewer"

    def test_log_environment_created(self, temp_log_dir, sample_environment_handle):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        writer.log_environment_created(sample_environment_handle)

        assert writer.workflow_log_path.exists()
        assert writer.environment_log_path("env_1234").exists()

        events = list(read_log_file(writer.workflow_log_path))
        assert len(events) == 1
        assert events[0].event_type == "environment.created"
        assert events[0].data["env_type"] == "worktree"

    def test_log_message_sent(self, temp_log_dir):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        writer.log_message_sent("reviewer", "msg_1", "user", "Hello, world!")

        session_log = writer.session_log_path("reviewer")
        assert session_log.exists()

        events = list(read_log_file(session_log))
        assert len(events) == 1
        assert events[0].event_type == "message.sent"
        assert events[0].data["preview"] == "Hello, world!"

    def test_log_session_status(self, temp_log_dir):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        writer.log_session_status("reviewer", AgenticSessionStatus.RUNNING)

        events = list(read_log_file(writer.workflow_log_path))
        assert len(events) == 1
        assert events[0].event_type == "session.status"
        assert events[0].data["status"] == "running"


class TestEventLogReader:
    def test_list_workflows(self, temp_log_dir):
        (temp_log_dir / "abc1234").mkdir()
        (temp_log_dir / "abc1234" / "workflow.jsonl").write_text("")
        (temp_log_dir / "def5678").mkdir()
        (temp_log_dir / "def5678" / "workflow.jsonl").write_text("")

        reader = EventLogReader(base_dir=temp_log_dir)
        workflows = reader.list_workflows()

        assert len(workflows) == 2
        assert "abc1234" in workflows
        assert "def5678" in workflows

    def test_resolve_prefix_exact(self, temp_log_dir):
        (temp_log_dir / "abc1234").mkdir()
        (temp_log_dir / "abc1234" / "workflow.jsonl").write_text("")

        reader = EventLogReader(base_dir=temp_log_dir)
        result = reader.resolve_prefix("abc1234")

        assert result == "abc1234"

    def test_resolve_prefix_partial(self, temp_log_dir):
        (temp_log_dir / "abc1234").mkdir()
        (temp_log_dir / "abc1234" / "workflow.jsonl").write_text("")

        reader = EventLogReader(base_dir=temp_log_dir)
        result = reader.resolve_prefix("abc")

        assert result == "abc1234"

    def test_resolve_prefix_ambiguous(self, temp_log_dir):
        (temp_log_dir / "abc1234").mkdir()
        (temp_log_dir / "abc1234" / "workflow.jsonl").write_text("")
        (temp_log_dir / "abc5678").mkdir()
        (temp_log_dir / "abc5678" / "workflow.jsonl").write_text("")

        reader = EventLogReader(base_dir=temp_log_dir)

        with pytest.raises(ValueError, match="Ambiguous prefix"):
            reader.resolve_prefix("abc")

    def test_resolve_prefix_not_found(self, temp_log_dir):
        reader = EventLogReader(base_dir=temp_log_dir)
        result = reader.resolve_prefix("xyz")

        assert result is None

    def test_reconstruct_workflow(
        self, temp_log_dir, sample_workflow_handle, sample_session_handle
    ):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        writer.log_workflow_created(sample_workflow_handle)
        writer.log_session_created(sample_session_handle)
        writer.log_session_status("reviewer", AgenticSessionStatus.RUNNING)

        reader = EventLogReader(base_dir=temp_log_dir)
        state = reader.reconstruct_workflow("abc1234")

        assert state is not None
        assert state.id == "abc1234"
        assert state.name == "test-workflow"
        assert state.status == AgenticWorkflowStatus.RUNNING
        assert "reviewer" in state.sessions
        assert state.sessions["reviewer"].status == AgenticSessionStatus.RUNNING

    def test_reconstruct_workflow_with_environment(
        self, temp_log_dir, sample_workflow_handle, sample_environment_handle
    ):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        writer.log_workflow_created(sample_workflow_handle)
        writer.log_environment_created(sample_environment_handle)

        reader = EventLogReader(base_dir=temp_log_dir)
        state = reader.reconstruct_workflow("abc1234")

        assert state is not None
        assert "env_1234" in state.environments
        assert state.environments["env_1234"].env_type == AgenticEnvironmentType.WORKTREE

    def test_get_session_events(self, temp_log_dir, sample_workflow_handle):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        # Need to create workflow first so resolve_prefix can find it
        writer.log_workflow_created(sample_workflow_handle)
        writer.log_message_sent("reviewer", "msg_1", "user", "Hello")
        writer.log_message_sent("reviewer", "msg_2", "assistant", "Hi there")

        reader = EventLogReader(base_dir=temp_log_dir)
        events = reader.get_session_events("abc1234", "reviewer")

        assert len(events) == 2
        assert events[0].event_type == "message.sent"
        assert events[1].event_type == "message.sent"

    def test_get_workflow_events(self, temp_log_dir, sample_workflow_handle):
        writer = EventLogWriter("abc1234", base_dir=temp_log_dir)
        writer.log_workflow_created(sample_workflow_handle)
        writer.log_workflow_status(AgenticWorkflowStatus.DONE)

        reader = EventLogReader(base_dir=temp_log_dir)
        events = reader.get_workflow_events("abc1234")

        assert len(events) == 2
        assert events[0].event_type == "workflow.created"
        assert events[1].event_type == "workflow.status"


class TestWorkflowState:
    def test_to_handle(self):
        state = WorkflowState(
            id="abc1234",
            name="test",
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )

        handle = state.to_handle()

        assert isinstance(handle, AgenticWorkflowHandle)
        assert handle.id == "abc1234"
        assert handle.name == "test"
        assert handle.status == AgenticWorkflowStatus.RUNNING
