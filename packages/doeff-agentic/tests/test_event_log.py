"""Tests for JSONL event logging."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from doeff_agentic.event_log import (
    EventLogEntry,
    EventLogReader,
    EventLogWriter,
    WorkflowIndex,
)
from doeff_agentic.types import (
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticSessionHandle,
    AgenticSessionStatus,
)


@pytest.fixture
def temp_state_dir():
    """Create a temporary state directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestEventLogEntry:
    """Tests for EventLogEntry dataclass."""

    def test_to_json(self):
        """Test serialization to JSON."""
        entry = EventLogEntry(
            ts="2026-01-05T00:00:00+00:00",
            event_type="workflow.created",
            data={"id": "abc123", "name": "test"},
        )
        json_str = entry.to_json()
        parsed = json.loads(json_str)

        assert parsed["ts"] == "2026-01-05T00:00:00+00:00"
        assert parsed["event_type"] == "workflow.created"
        assert parsed["id"] == "abc123"
        assert parsed["name"] == "test"

    def test_from_json(self):
        """Test deserialization from JSON."""
        json_str = '{"ts": "2026-01-05T00:00:00+00:00", "event_type": "workflow.created", "id": "abc123"}'
        entry = EventLogEntry.from_json(json_str)

        assert entry.ts == "2026-01-05T00:00:00+00:00"
        assert entry.event_type == "workflow.created"
        assert entry.data == {"id": "abc123"}

    def test_roundtrip(self):
        """Test JSON serialization roundtrip."""
        original = EventLogEntry(
            ts="2026-01-05T00:00:00+00:00",
            event_type="session.status",
            data={"name": "reviewer", "status": "running"},
        )
        json_str = original.to_json()
        restored = EventLogEntry.from_json(json_str)

        assert original.ts == restored.ts
        assert original.event_type == restored.event_type
        assert original.data == restored.data


class TestEventLogWriter:
    """Tests for EventLogWriter."""

    def test_log_workflow_created(self, temp_state_dir: Path):
        """Test logging workflow creation."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test-workflow", {"key": "value"})

        log_path = temp_state_dir / "workflows" / "abc123" / "workflow.jsonl"
        assert log_path.exists()

        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["event_type"] == "workflow.created"
        assert entry["id"] == "abc123"
        assert entry["name"] == "test-workflow"
        assert entry["metadata"] == {"key": "value"}

    def test_log_workflow_status(self, temp_state_dir: Path):
        """Test logging workflow status change."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")
        writer.log_workflow_status("abc123", "running")

        log_path = temp_state_dir / "workflows" / "abc123" / "workflow.jsonl"

        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2

        entry = json.loads(lines[1])
        assert entry["event_type"] == "workflow.status"
        assert entry["status"] == "running"

    def test_log_environment_created(self, temp_state_dir: Path):
        """Test logging environment creation."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")

        env = AgenticEnvironmentHandle(
            id="env-001",
            env_type=AgenticEnvironmentType.WORKTREE,
            name="test-env",
            working_dir="/tmp/test",
            created_at=datetime.now(timezone.utc),
            base_commit="main",
        )
        writer.log_environment_created("abc123", env)

        # Check workflow log
        workflow_log = temp_state_dir / "workflows" / "abc123" / "workflow.jsonl"
        with open(workflow_log) as f:
            lines = f.readlines()
        assert len(lines) == 2

        entry = json.loads(lines[1])
        assert entry["event_type"] == "environment.created"
        assert entry["id"] == "env-001"
        assert entry["env_type"] == "worktree"

        # Check environment log
        env_log = temp_state_dir / "workflows" / "abc123" / "environments" / "env-001.jsonl"
        assert env_log.exists()

    def test_log_session_created(self, temp_state_dir: Path):
        """Test logging session creation."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")

        session = AgenticSessionHandle(
            id="sess-001",
            name="reviewer",
            workflow_id="abc123",
            environment_id="env-001",
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            title="Code Reviewer",
        )
        writer.log_session_created("abc123", session)

        # Check workflow log
        workflow_log = temp_state_dir / "workflows" / "abc123" / "workflow.jsonl"
        with open(workflow_log) as f:
            lines = f.readlines()

        entry = json.loads(lines[-1])
        assert entry["event_type"] == "session.created"
        assert entry["name"] == "reviewer"

        # Check session log
        session_log = temp_state_dir / "workflows" / "abc123" / "sessions" / "reviewer.jsonl"
        assert session_log.exists()

    def test_log_message_sent(self, temp_state_dir: Path):
        """Test logging message sent event."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")
        writer.log_message_sent("abc123", "reviewer", "Review this code", wait=True)

        session_log = temp_state_dir / "workflows" / "abc123" / "sessions" / "reviewer.jsonl"
        assert session_log.exists()

        with open(session_log) as f:
            lines = f.readlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["event_type"] == "message.sent"
        assert entry["role"] == "user"
        assert entry["wait"] is True


class TestEventLogReader:
    """Tests for EventLogReader."""

    def test_list_workflows(self, temp_state_dir: Path):
        """Test listing workflow IDs."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test1")
        writer.log_workflow_created("def456", "test2")

        reader = EventLogReader(temp_state_dir)
        workflows = reader.list_workflows()

        assert set(workflows) == {"abc123", "def456"}

    def test_list_sessions(self, temp_state_dir: Path):
        """Test listing session names."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")
        writer.log_message_sent("abc123", "reviewer", "test")
        writer.log_message_sent("abc123", "fixer", "test")

        reader = EventLogReader(temp_state_dir)
        sessions = reader.list_sessions("abc123")

        assert set(sessions) == {"reviewer", "fixer"}

    def test_read_workflow_events(self, temp_state_dir: Path):
        """Test reading workflow events."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")
        writer.log_workflow_status("abc123", "running")

        reader = EventLogReader(temp_state_dir)
        events = reader.read_workflow_events("abc123")

        assert len(events) == 2
        assert events[0].event_type == "workflow.created"
        assert events[1].event_type == "workflow.status"

    def test_reconstruct_workflow_state(self, temp_state_dir: Path):
        """Test reconstructing workflow state from events."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "my-workflow", {"key": "value"})
        writer.log_workflow_status("abc123", "running")
        writer.log_workflow_status("abc123", "done")

        reader = EventLogReader(temp_state_dir)
        workflow = reader.reconstruct_workflow_state("abc123")

        assert workflow is not None
        assert workflow.id == "abc123"
        assert workflow.name == "my-workflow"
        assert workflow.status.value == "done"
        assert workflow.metadata == {"key": "value"}

    def test_reconstruct_session_state(self, temp_state_dir: Path):
        """Test reconstructing session state from events."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")

        session = AgenticSessionHandle(
            id="sess-001",
            name="reviewer",
            workflow_id="abc123",
            environment_id="env-001",
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            title="Code Reviewer",
        )
        writer.log_session_created("abc123", session)
        writer.log_session_status("abc123", "reviewer", "running")
        writer.log_session_status("abc123", "reviewer", "done")

        reader = EventLogReader(temp_state_dir)
        restored = reader.reconstruct_session_state("abc123", "reviewer")

        assert restored is not None
        assert restored.id == "sess-001"
        assert restored.name == "reviewer"
        assert restored.status.value == "done"

    def test_reconstruct_environment_state(self, temp_state_dir: Path):
        """Test reconstructing environment state from events."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")

        env = AgenticEnvironmentHandle(
            id="env-001",
            env_type=AgenticEnvironmentType.WORKTREE,
            name="test-env",
            working_dir="/tmp/test",
            created_at=datetime.now(timezone.utc),
            base_commit="main",
        )
        writer.log_environment_created("abc123", env)

        reader = EventLogReader(temp_state_dir)
        restored = reader.reconstruct_environment_state("abc123", "env-001")

        assert restored is not None
        assert restored.id == "env-001"
        assert restored.env_type == AgenticEnvironmentType.WORKTREE
        assert restored.working_dir == "/tmp/test"

    def test_get_sessions_for_environment(self, temp_state_dir: Path):
        """Test getting sessions bound to environment."""
        writer = EventLogWriter(temp_state_dir)
        writer.log_workflow_created("abc123", "test")

        env = AgenticEnvironmentHandle(
            id="env-001",
            env_type=AgenticEnvironmentType.SHARED,
            name="shared",
            working_dir="/tmp/test",
            created_at=datetime.now(timezone.utc),
        )
        writer.log_environment_created("abc123", env)
        writer.log_session_bound_to_environment("abc123", "env-001", "reviewer")
        writer.log_session_bound_to_environment("abc123", "env-001", "fixer")
        writer.log_session_unbound_from_environment("abc123", "env-001", "fixer")

        reader = EventLogReader(temp_state_dir)
        sessions = reader.get_sessions_for_environment("abc123", "env-001")

        assert sessions == ["reviewer"]


class TestWorkflowIndex:
    """Tests for WorkflowIndex."""

    def test_add_and_resolve(self, temp_state_dir: Path):
        """Test adding and resolving workflow IDs."""
        index = WorkflowIndex(temp_state_dir)
        index.add("abc1234", "my-workflow")
        index.add("def5678", "other-workflow")

        # Exact match
        assert index.resolve_prefix("abc1234") == "abc1234"

        # Prefix match
        assert index.resolve_prefix("abc") == "abc1234"
        assert index.resolve_prefix("def") == "def5678"

    def test_resolve_ambiguous_prefix(self, temp_state_dir: Path):
        """Test resolving ambiguous prefix raises ValueError."""
        index = WorkflowIndex(temp_state_dir)
        index.add("abc1234", "workflow1")
        index.add("abc5678", "workflow2")

        with pytest.raises(ValueError, match="Ambiguous prefix"):
            index.resolve_prefix("abc")

    def test_resolve_not_found(self, temp_state_dir: Path):
        """Test resolving non-existent workflow returns None."""
        index = WorkflowIndex(temp_state_dir)
        index.add("abc1234", "workflow")

        assert index.resolve_prefix("xyz") is None

    def test_remove(self, temp_state_dir: Path):
        """Test removing workflow from index."""
        index = WorkflowIndex(temp_state_dir)
        index.add("abc1234", "workflow")

        assert index.resolve_prefix("abc") == "abc1234"

        index.remove("abc1234")

        assert index.resolve_prefix("abc") is None

    def test_list_all(self, temp_state_dir: Path):
        """Test listing all workflows."""
        index = WorkflowIndex(temp_state_dir)
        index.add("abc1234", "workflow1")
        index.add("def5678", "workflow2")

        all_workflows = index.list_all()

        assert all_workflows == {"abc1234": "workflow1", "def5678": "workflow2"}
