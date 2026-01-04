"""Tests for the JSONL event log system."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest

from doeff_agentic.event_log import (
    EventLogEntry,
    EventLogReader,
    EventLogWriter,
)
from doeff_agentic.types import (
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
)


class TestEventLogEntry:
    """Tests for EventLogEntry."""

    def test_to_dict(self) -> None:
        """Test converting entry to dictionary."""
        entry = EventLogEntry(
            ts="2026-01-04T10:00:00+00:00",
            event_type="session.created",
            data={"id": "sess_123", "name": "reviewer"},
        )
        result = entry.to_dict()
        assert result["ts"] == "2026-01-04T10:00:00+00:00"
        assert result["event_type"] == "session.created"
        assert result["id"] == "sess_123"
        assert result["name"] == "reviewer"

    def test_to_json(self) -> None:
        """Test converting entry to JSON string."""
        entry = EventLogEntry(
            ts="2026-01-04T10:00:00+00:00",
            event_type="session.status",
            data={"name": "reviewer", "status": "running"},
        )
        json_str = entry.to_json()
        parsed = json.loads(json_str)
        assert parsed["ts"] == "2026-01-04T10:00:00+00:00"
        assert parsed["event_type"] == "session.status"

    def test_from_dict(self) -> None:
        """Test creating entry from dictionary."""
        data = {
            "ts": "2026-01-04T10:00:00+00:00",
            "event_type": "workflow.created",
            "id": "a3f8b2c",
            "name": "PR Review",
        }
        entry = EventLogEntry.from_dict(data)
        assert entry.ts == "2026-01-04T10:00:00+00:00"
        assert entry.event_type == "workflow.created"
        assert entry.data == {"id": "a3f8b2c", "name": "PR Review"}


class TestEventLogWriter:
    """Tests for EventLogWriter."""

    @pytest.fixture
    def temp_state_dir(self) -> Generator[Path, None, None]:
        """Create a temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def writer(self, temp_state_dir: Path) -> EventLogWriter:
        """Create a writer with temporary state directory."""
        return EventLogWriter(workflow_id="test123", state_dir=temp_state_dir)

    def test_directory_structure_created(self, writer: EventLogWriter) -> None:
        """Test that directory structure is created."""
        assert writer.workflow_dir.exists()
        assert (writer.workflow_dir / "sessions").exists()
        assert (writer.workflow_dir / "environments").exists()

    def test_log_workflow_created(self, writer: EventLogWriter) -> None:
        """Test logging workflow creation."""
        workflow = AgenticWorkflowHandle(
            id="test123",
            name="Test Workflow",
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            metadata={"key": "value"},
        )
        writer.log_workflow_created(workflow)

        events = writer.read_workflow_events()
        assert len(events) == 1
        assert events[0].event_type == "workflow.created"
        assert events[0].data["id"] == "test123"
        assert events[0].data["name"] == "Test Workflow"
        assert events[0].data["metadata"] == {"key": "value"}

    def test_log_workflow_status(self, writer: EventLogWriter) -> None:
        """Test logging workflow status change."""
        writer.log_workflow_status(AgenticWorkflowStatus.DONE)

        events = writer.read_workflow_events()
        assert len(events) == 1
        assert events[0].event_type == "workflow.status"
        assert events[0].data["status"] == "done"

    def test_log_workflow_status_with_error(self, writer: EventLogWriter) -> None:
        """Test logging workflow status change with error."""
        writer.log_workflow_status(AgenticWorkflowStatus.ERROR, error="Something failed")

        events = writer.read_workflow_events()
        assert len(events) == 1
        assert events[0].data["status"] == "error"
        assert events[0].data["error"] == "Something failed"

    def test_log_session_created(self, writer: EventLogWriter) -> None:
        """Test logging session creation."""
        session = AgenticSessionHandle(
            id="sess_abc123",
            name="reviewer",
            workflow_id="test123",
            environment_id="env_xyz",
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            title="Code Reviewer",
            agent="code-review",
            model="claude-3",
        )
        writer.log_session_created(session)

        events = writer.read_workflow_events()
        assert len(events) == 1
        assert events[0].event_type == "session.created"
        assert events[0].data["name"] == "reviewer"
        assert events[0].data["title"] == "Code Reviewer"

    def test_log_session_status(self, writer: EventLogWriter) -> None:
        """Test logging session status change."""
        writer.log_session_status("reviewer", AgenticSessionStatus.RUNNING)

        events = writer.read_workflow_events()
        assert len(events) == 1
        assert events[0].event_type == "session.status"
        assert events[0].data["name"] == "reviewer"
        assert events[0].data["status"] == "running"

    def test_log_environment_created(self, writer: EventLogWriter) -> None:
        """Test logging environment creation."""
        env = AgenticEnvironmentHandle(
            id="env_abc",
            env_type=AgenticEnvironmentType.WORKTREE,
            name="pr-review",
            working_dir="/tmp/worktrees/abc",
            created_at=datetime.now(timezone.utc),
            base_commit="main",
        )
        writer.log_environment_created(env)

        # Check workflow log
        workflow_events = writer.read_workflow_events()
        assert len(workflow_events) == 1
        assert workflow_events[0].event_type == "environment.created"

        # Check environment-specific log
        env_events = writer.read_environment_events("env_abc")
        assert len(env_events) == 1
        assert env_events[0].event_type == "environment.created"

    def test_log_message_sent(self, writer: EventLogWriter) -> None:
        """Test logging message sent to session."""
        writer.log_message_sent("reviewer", "Review this code please", "msg_123")

        events = writer.read_session_events("reviewer")
        assert len(events) == 1
        assert events[0].event_type == "message.sent"
        assert events[0].data["role"] == "user"
        assert events[0].data["message_id"] == "msg_123"

    def test_log_message_complete(self, writer: EventLogWriter) -> None:
        """Test logging message completion."""
        writer.log_message_complete("reviewer", message_id="msg_123", tokens=500)

        events = writer.read_session_events("reviewer")
        assert len(events) == 1
        assert events[0].event_type == "message.complete"
        assert events[0].data["tokens"] == 500

    def test_log_tool_call(self, writer: EventLogWriter) -> None:
        """Test logging tool call."""
        writer.log_tool_call("reviewer", "read_file", {"path": "main.py"})

        events = writer.read_session_events("reviewer")
        assert len(events) == 1
        assert events[0].event_type == "tool.call"
        assert events[0].data["tool"] == "read_file"

    def test_list_sessions(self, writer: EventLogWriter) -> None:
        """Test listing sessions with log files."""
        writer.log_message_sent("reviewer", "Hello", None)
        writer.log_message_sent("fixer", "Hi", None)

        sessions = writer.list_sessions()
        assert set(sessions) == {"reviewer", "fixer"}

    def test_list_environments(self, writer: EventLogWriter) -> None:
        """Test listing environments with log files."""
        env1 = AgenticEnvironmentHandle(
            id="env_abc",
            env_type=AgenticEnvironmentType.WORKTREE,
            name="env1",
            working_dir="/tmp/1",
            created_at=datetime.now(timezone.utc),
        )
        env2 = AgenticEnvironmentHandle(
            id="env_def",
            env_type=AgenticEnvironmentType.SHARED,
            name="env2",
            working_dir="/tmp/2",
            created_at=datetime.now(timezone.utc),
        )
        writer.log_environment_created(env1)
        writer.log_environment_created(env2)

        envs = writer.list_environments()
        assert set(envs) == {"env_abc", "env_def"}


class TestEventLogReader:
    """Tests for EventLogReader."""

    @pytest.fixture
    def temp_state_dir(self) -> Generator[Path, None, None]:
        """Create a temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def reader(self, temp_state_dir: Path) -> EventLogReader:
        """Create a reader with temporary state directory."""
        return EventLogReader(state_dir=temp_state_dir)

    def test_list_workflows_empty(self, reader: EventLogReader) -> None:
        """Test listing workflows when none exist."""
        workflows = reader.list_workflows()
        assert workflows == []

    def test_list_workflows(self, reader: EventLogReader) -> None:
        """Test listing workflows with logs."""
        # Create some workflow logs
        writer1 = reader.get_writer("workflow1")
        writer2 = reader.get_writer("workflow2")

        workflow1 = AgenticWorkflowHandle(
            id="workflow1",
            name="Workflow 1",
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )
        workflow2 = AgenticWorkflowHandle(
            id="workflow2",
            name="Workflow 2",
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )
        writer1.log_workflow_created(workflow1)
        writer2.log_workflow_created(workflow2)

        workflows = reader.list_workflows()
        assert set(workflows) == {"workflow1", "workflow2"}

    def test_reconstruct_workflow_state(self, reader: EventLogReader) -> None:
        """Test reconstructing workflow state from events."""
        writer = reader.get_writer("test_wf")

        # Log workflow creation
        workflow = AgenticWorkflowHandle(
            id="test_wf",
            name="Test Workflow",
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )
        writer.log_workflow_created(workflow)

        # Log environment creation
        env = AgenticEnvironmentHandle(
            id="env_abc",
            env_type=AgenticEnvironmentType.WORKTREE,
            name="main-env",
            working_dir="/tmp/worktree",
            created_at=datetime.now(timezone.utc),
            base_commit="main",
        )
        writer.log_environment_created(env)

        # Log session creation
        session = AgenticSessionHandle(
            id="sess_123",
            name="reviewer",
            workflow_id="test_wf",
            environment_id="env_abc",
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            title="Code Reviewer",
        )
        writer.log_session_created(session)

        # Log session status change
        writer.log_session_status("reviewer", AgenticSessionStatus.RUNNING)

        # Reconstruct state
        state = reader.reconstruct_workflow_state("test_wf")
        assert state is not None
        assert state["id"] == "test_wf"
        assert state["name"] == "Test Workflow"
        assert state["status"] == "running"

        # Check session state
        assert "reviewer" in state["sessions"]
        assert state["sessions"]["reviewer"]["status"] == "running"

        # Check environment state
        assert "env_abc" in state["environments"]
        assert state["environments"]["env_abc"]["env_type"] == "worktree"

    def test_reconstruct_with_deleted_session(self, reader: EventLogReader) -> None:
        """Test that deleted sessions are removed from state."""
        writer = reader.get_writer("test_wf")

        workflow = AgenticWorkflowHandle(
            id="test_wf",
            name="Test",
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )
        writer.log_workflow_created(workflow)

        session = AgenticSessionHandle(
            id="sess_123",
            name="reviewer",
            workflow_id="test_wf",
            environment_id="env_abc",
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        writer.log_session_created(session)
        writer.log_session_deleted("reviewer")

        state = reader.reconstruct_workflow_state("test_wf")
        assert state is not None
        assert "reviewer" not in state["sessions"]

    def test_reconstruct_nonexistent_workflow(self, reader: EventLogReader) -> None:
        """Test reconstructing nonexistent workflow returns None."""
        state = reader.reconstruct_workflow_state("nonexistent")
        assert state is None
