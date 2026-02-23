"""Tests for new spec-compliant types in doeff_agentic.types module."""

from datetime import datetime, timezone

import pytest
from doeff_agentic.types import (
    AgenticEndOfEvents,
    AgenticEnvironmentHandle,
    # Enums
    AgenticEnvironmentType,
    # Events
    AgenticEvent,
    AgenticMessage,
    AgenticSessionHandle,
    AgenticSessionStatus,
    # Handles
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
)


class TestAgenticEnvironmentType:
    """Tests for AgenticEnvironmentType enum."""

    def test_enum_values(self):
        """Test that all environment types are defined."""
        assert AgenticEnvironmentType.WORKTREE.value == "worktree"
        assert AgenticEnvironmentType.INHERITED.value == "inherited"
        assert AgenticEnvironmentType.COPY.value == "copy"
        assert AgenticEnvironmentType.SHARED.value == "shared"


class TestAgenticSessionStatus:
    """Tests for AgenticSessionStatus enum."""

    def test_enum_values(self):
        """Test that all session statuses are defined."""
        assert AgenticSessionStatus.PENDING.value == "pending"
        assert AgenticSessionStatus.BOOTING.value == "booting"
        assert AgenticSessionStatus.RUNNING.value == "running"
        assert AgenticSessionStatus.BLOCKED.value == "blocked"
        assert AgenticSessionStatus.DONE.value == "done"
        assert AgenticSessionStatus.ERROR.value == "error"
        assert AgenticSessionStatus.ABORTED.value == "aborted"

    def test_is_terminal(self):
        """Test is_terminal helper."""
        assert AgenticSessionStatus.DONE.is_terminal() is True
        assert AgenticSessionStatus.ERROR.is_terminal() is True
        assert AgenticSessionStatus.ABORTED.is_terminal() is True
        assert AgenticSessionStatus.RUNNING.is_terminal() is False
        assert AgenticSessionStatus.BLOCKED.is_terminal() is False


class TestAgenticWorkflowStatus:
    """Tests for AgenticWorkflowStatus enum."""

    def test_enum_values(self):
        """Test that all workflow statuses are defined."""
        assert AgenticWorkflowStatus.PENDING.value == "pending"
        assert AgenticWorkflowStatus.RUNNING.value == "running"
        assert AgenticWorkflowStatus.DONE.value == "done"
        assert AgenticWorkflowStatus.ERROR.value == "error"
        assert AgenticWorkflowStatus.ABORTED.value == "aborted"

    def test_is_terminal(self):
        """Test is_terminal helper."""
        assert AgenticWorkflowStatus.DONE.is_terminal() is True
        assert AgenticWorkflowStatus.ERROR.is_terminal() is True
        assert AgenticWorkflowStatus.ABORTED.is_terminal() is True
        assert AgenticWorkflowStatus.RUNNING.is_terminal() is False


class TestAgenticWorkflowHandle:
    """Tests for AgenticWorkflowHandle."""

    def test_create_handle(self):
        """Test creating a workflow handle."""
        now = datetime.now(timezone.utc)
        handle = AgenticWorkflowHandle(
            id="a3f8b2c",
            name="PR Review",
            status=AgenticWorkflowStatus.RUNNING,
            created_at=now,
            metadata={"pr_url": "https://github.com/..."},
        )
        assert handle.id == "a3f8b2c"
        assert handle.name == "PR Review"
        assert handle.status == AgenticWorkflowStatus.RUNNING
        assert handle.metadata is not None
        assert handle.metadata["pr_url"] == "https://github.com/..."

    def test_to_dict(self):
        """Test converting to dict."""
        now = datetime.now(timezone.utc)
        handle = AgenticWorkflowHandle(
            id="a3f8b2c",
            name="Test",
            status=AgenticWorkflowStatus.DONE,
            created_at=now,
        )
        d = handle.to_dict()
        assert d["id"] == "a3f8b2c"
        assert d["status"] == "done"
        assert "created_at" in d

    def test_from_dict(self):
        """Test creating from dict."""
        d = {
            "id": "a3f8b2c",
            "name": "Test",
            "status": "running",
            "created_at": "2026-01-03T12:00:00+00:00",
        }
        handle = AgenticWorkflowHandle.from_dict(d)
        assert handle.id == "a3f8b2c"
        assert handle.status == AgenticWorkflowStatus.RUNNING


class TestAgenticEnvironmentHandle:
    """Tests for AgenticEnvironmentHandle."""

    def test_create_worktree_handle(self):
        """Test creating a worktree environment handle."""
        now = datetime.now(timezone.utc)
        handle = AgenticEnvironmentHandle(
            id="env-abc123",
            env_type=AgenticEnvironmentType.WORKTREE,
            name="pr-review",
            working_dir="/tmp/doeff/worktrees/env-abc123",
            created_at=now,
            base_commit="main",
        )
        assert handle.env_type == AgenticEnvironmentType.WORKTREE
        assert handle.base_commit == "main"

    def test_create_shared_handle(self):
        """Test creating a shared environment handle."""
        now = datetime.now(timezone.utc)
        handle = AgenticEnvironmentHandle(
            id="env-shared",
            env_type=AgenticEnvironmentType.SHARED,
            name="default",
            working_dir="/path/to/project",
            created_at=now,
        )
        assert handle.env_type == AgenticEnvironmentType.SHARED

    def test_to_dict_and_back(self):
        """Test roundtrip serialization."""
        now = datetime.now(timezone.utc)
        original = AgenticEnvironmentHandle(
            id="env-test",
            env_type=AgenticEnvironmentType.INHERITED,
            name="inherited-env",
            working_dir="/path/to/source",
            created_at=now,
            source_environment_id="env-source",
        )
        d = original.to_dict()
        restored = AgenticEnvironmentHandle.from_dict(d)
        assert restored.id == original.id
        assert restored.env_type == original.env_type
        assert restored.source_environment_id == "env-source"


class TestAgenticSessionHandle:
    """Tests for AgenticSessionHandle."""

    def test_create_handle(self):
        """Test creating a session handle."""
        now = datetime.now(timezone.utc)
        handle = AgenticSessionHandle(
            id="sess_abc123xyz",
            name="reviewer",
            workflow_id="a3f8b2c",
            environment_id="env-abc",
            status=AgenticSessionStatus.RUNNING,
            created_at=now,
            title="Code Reviewer",
            agent="code-review",
            model="claude-sonnet-4-20250514",
        )
        assert handle.id == "sess_abc123xyz"
        assert handle.name == "reviewer"
        assert handle.agent == "code-review"

    def test_to_dict_and_back(self):
        """Test roundtrip serialization."""
        now = datetime.now(timezone.utc)
        original = AgenticSessionHandle(
            id="sess_test",
            name="tester",
            workflow_id="abc",
            environment_id="env-test",
            status=AgenticSessionStatus.BLOCKED,
            created_at=now,
        )
        d = original.to_dict()
        restored = AgenticSessionHandle.from_dict(d)
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.status == AgenticSessionStatus.BLOCKED


class TestAgenticMessage:
    """Tests for AgenticMessage."""

    def test_create_message(self):
        """Test creating a message."""
        now = datetime.now(timezone.utc)
        msg = AgenticMessage(
            id="msg_123",
            session_id="sess_abc",
            role="assistant",
            content="Hello! I'm reviewing your code.",
            created_at=now,
            parts=[{"type": "text", "text": "Hello! I'm reviewing your code."}],
        )
        assert msg.role == "assistant"
        assert "reviewing" in msg.content

    def test_to_dict_and_back(self):
        """Test roundtrip serialization."""
        now = datetime.now(timezone.utc)
        original = AgenticMessage(
            id="msg_test",
            session_id="sess_test",
            role="user",
            content="Test content",
            created_at=now,
        )
        d = original.to_dict()
        restored = AgenticMessage.from_dict(d)
        assert restored.content == original.content
        assert restored.role == "user"


class TestAgenticEvent:
    """Tests for AgenticEvent."""

    def test_create_event(self):
        """Test creating an event."""
        now = datetime.now(timezone.utc)
        event = AgenticEvent(
            event_type="message.chunk",
            session_id="sess_abc",
            data={"content": "partial content"},
            timestamp=now,
        )
        assert event.event_type == "message.chunk"
        assert event.data["content"] == "partial content"

    def test_to_dict_and_back(self):
        """Test roundtrip serialization."""
        now = datetime.now(timezone.utc)
        original = AgenticEvent(
            event_type="session.done",
            session_id="sess_test",
            data={"result": "success"},
            timestamp=now,
        )
        d = original.to_dict()
        restored = AgenticEvent.from_dict(d)
        assert restored.event_type == original.event_type


class TestAgenticEndOfEvents:
    """Tests for AgenticEndOfEvents."""

    def test_create_end_of_events(self):
        """Test creating an end of events marker."""
        eoe = AgenticEndOfEvents(
            reason="session_done",
            final_status=AgenticSessionStatus.DONE,
        )
        assert eoe.reason == "session_done"
        assert eoe.final_status == AgenticSessionStatus.DONE

    def test_frozen(self):
        """Test that the dataclass is frozen."""
        eoe = AgenticEndOfEvents(reason="connection_closed")
        with pytest.raises(AttributeError):
            eoe.reason = "changed"  # type: ignore
