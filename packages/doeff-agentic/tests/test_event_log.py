"""Tests for JSONL event log management."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from doeff_agentic.event_log import (
    EventLogManager,
    WorkflowEvent,
    SessionEvent,
    WorkflowState,
    generate_workflow_id,
)
from doeff_agentic.types import (
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticWorkflowStatus,
)


class TestEventLogManager:
    """Tests for EventLogManager."""

    @pytest.fixture
    def temp_state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def manager(self, temp_state_dir):
        """Create EventLogManager with temp directory."""
        return EventLogManager(temp_state_dir)

    def test_generate_workflow_id(self):
        """Test workflow ID generation."""
        id1 = generate_workflow_id("test")
        id2 = generate_workflow_id("test")

        assert len(id1) == 7
        assert id1.isalnum()
        assert id1 != id2

    def test_log_workflow_created(self, manager, temp_state_dir):
        """Test logging workflow creation."""
        workflow_id = "abc1234"

        manager.log_workflow_created(
            workflow_id,
            name="Test Workflow",
            metadata={"key": "value"},
        )

        events = manager.read_workflow_events(workflow_id)
        assert len(events) == 1
        assert events[0].event_type == "workflow.created"
        assert events[0].data["name"] == "Test Workflow"
        assert events[0].data["metadata"]["key"] == "value"

    def test_log_workflow_status(self, manager):
        """Test logging workflow status change."""
        workflow_id = "abc1234"

        manager.log_workflow_created(workflow_id)
        manager.log_workflow_status(
            workflow_id, AgenticWorkflowStatus.DONE
        )

        events = manager.read_workflow_events(workflow_id)
        assert len(events) == 2
        assert events[1].event_type == "workflow.status"
        assert events[1].data["status"] == "done"

    def test_log_environment_created(self, manager):
        """Test logging environment creation."""
        workflow_id = "abc1234"

        env = AgenticEnvironmentHandle(
            id="env-123",
            env_type=AgenticEnvironmentType.WORKTREE,
            name="test-env",
            working_dir="/tmp/test",
            created_at=datetime.now(timezone.utc),
            base_commit="main",
        )

        manager.log_workflow_created(workflow_id)
        manager.log_environment_created(workflow_id, env)

        events = manager.read_workflow_events(workflow_id)
        assert len(events) == 2
        assert events[1].event_type == "environment.created"
        assert events[1].data["id"] == "env-123"
        assert events[1].data["env_type"] == "worktree"

    def test_log_session_created(self, manager):
        """Test logging session creation."""
        workflow_id = "abc1234"

        session = AgenticSessionHandle(
            id="sess-123",
            name="reviewer",
            workflow_id=workflow_id,
            environment_id="env-123",
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            title="Code Reviewer",
        )

        manager.log_workflow_created(workflow_id)
        manager.log_session_created(workflow_id, session)

        events = manager.read_workflow_events(workflow_id)
        assert len(events) == 2
        assert events[1].event_type == "session.created"
        assert events[1].data["name"] == "reviewer"

    def test_log_session_status(self, manager):
        """Test logging session status change."""
        workflow_id = "abc1234"

        manager.log_workflow_created(workflow_id)
        manager.log_session_status(
            workflow_id, "reviewer", AgenticSessionStatus.RUNNING
        )

        events = manager.read_workflow_events(workflow_id)
        assert len(events) == 2
        assert events[1].event_type == "session.status"
        assert events[1].data["status"] == "running"

    def test_log_message_sent(self, manager):
        """Test logging message sent event."""
        workflow_id = "abc1234"

        manager.log_workflow_created(workflow_id)
        manager.log_message_sent(
            workflow_id, "reviewer", "user", "Review this code"
        )

        session_events = manager.read_session_events(workflow_id, "reviewer")
        assert len(session_events) == 1
        assert session_events[0].event_type == "message.sent"
        assert session_events[0].data["role"] == "user"

    def test_reconstruct_workflow_state(self, manager):
        """Test reconstructing workflow state from events."""
        workflow_id = "abc1234"

        manager.log_workflow_created(workflow_id, name="Test")

        env = AgenticEnvironmentHandle(
            id="env-123",
            env_type=AgenticEnvironmentType.SHARED,
            name="shared",
            working_dir="/tmp/test",
            created_at=datetime.now(timezone.utc),
        )
        manager.log_environment_created(workflow_id, env)

        session = AgenticSessionHandle(
            id="sess-123",
            name="reviewer",
            workflow_id=workflow_id,
            environment_id="env-123",
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        manager.log_session_created(workflow_id, session)
        manager.log_session_status(
            workflow_id, "reviewer", AgenticSessionStatus.RUNNING
        )

        state = manager.reconstruct_workflow_state(workflow_id)

        assert state is not None
        assert state.name == "Test"
        assert state.status == AgenticWorkflowStatus.RUNNING
        assert "env-123" in state.environments
        assert "reviewer" in state.sessions
        assert state.sessions["reviewer"].status == AgenticSessionStatus.RUNNING

    def test_list_workflow_ids(self, manager):
        """Test listing workflow IDs."""
        manager.log_workflow_created("wf-001", name="First")
        manager.log_workflow_created("wf-002", name="Second")

        ids = manager.list_workflow_ids()

        assert "wf-001" in ids
        assert "wf-002" in ids

    def test_list_workflows_with_filter(self, manager):
        """Test listing workflows with status filter."""
        manager.log_workflow_created("wf-001", name="Running")

        manager.log_workflow_created("wf-002", name="Done")
        manager.log_workflow_status("wf-002", AgenticWorkflowStatus.DONE)

        running = manager.list_workflows(
            status=[AgenticWorkflowStatus.RUNNING]
        )
        done = manager.list_workflows(
            status=[AgenticWorkflowStatus.DONE]
        )

        assert len(running) == 1
        assert running[0].name == "Running"
        assert len(done) == 1
        assert done[0].name == "Done"

    def test_resolve_prefix(self, manager):
        """Test prefix resolution."""
        manager.log_workflow_created("abc1234", name="Test")

        assert manager.resolve_prefix("abc1234") == "abc1234"
        assert manager.resolve_prefix("abc") == "abc1234"
        assert manager.resolve_prefix("xyz") is None

    def test_resolve_prefix_ambiguous(self, manager):
        """Test ambiguous prefix raises error."""
        manager.log_workflow_created("abc1111", name="First")
        manager.log_workflow_created("abc2222", name="Second")

        with pytest.raises(ValueError, match="Ambiguous"):
            manager.resolve_prefix("abc")

    def test_delete_workflow(self, manager, temp_state_dir):
        """Test deleting a workflow."""
        manager.log_workflow_created("abc1234", name="Test")

        assert manager.resolve_prefix("abc") == "abc1234"

        result = manager.delete_workflow("abc1234")

        assert result is True
        assert manager.resolve_prefix("abc") is None


class TestWorkflowEvent:
    """Tests for WorkflowEvent."""

    def test_to_dict_and_back(self):
        """Test roundtrip serialization."""
        now = datetime.now(timezone.utc)
        original = WorkflowEvent(
            ts=now,
            event_type="workflow.created",
            data={"name": "Test", "id": "abc123"},
        )

        d = original.to_dict()
        restored = WorkflowEvent.from_dict(d)

        assert restored.event_type == original.event_type
        assert restored.data["name"] == "Test"


class TestSessionEvent:
    """Tests for SessionEvent."""

    def test_to_dict_and_back(self):
        """Test roundtrip serialization."""
        now = datetime.now(timezone.utc)
        original = SessionEvent(
            ts=now,
            event_type="message.sent",
            data={"role": "user", "preview": "Hello"},
        )

        d = original.to_dict()
        restored = SessionEvent.from_dict(d)

        assert restored.event_type == original.event_type
        assert restored.data["role"] == "user"
