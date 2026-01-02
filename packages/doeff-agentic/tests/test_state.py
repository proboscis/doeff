"""Tests for doeff_agentic.state module."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from doeff_agentic.state import (
    StateManager,
    generate_workflow_id,
    get_default_state_dir,
)
from doeff_agentic.types import (
    AgentInfo,
    AgentStatus,
    WorkflowInfo,
    WorkflowStatus,
)


class TestGenerateWorkflowId:
    """Tests for workflow ID generation."""

    def test_generates_7_char_hex(self):
        """Test that IDs are 7 character hex strings."""
        wf_id = generate_workflow_id("test-workflow")
        assert len(wf_id) == 7
        assert all(c in "0123456789abcdef" for c in wf_id)

    def test_same_name_timestamp_gives_same_id(self):
        """Test deterministic ID generation."""
        ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        id1 = generate_workflow_id("test", ts)
        id2 = generate_workflow_id("test", ts)
        assert id1 == id2

    def test_different_names_give_different_ids(self):
        """Test different names produce different IDs."""
        ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        id1 = generate_workflow_id("workflow-a", ts)
        id2 = generate_workflow_id("workflow-b", ts)
        assert id1 != id2


class TestStateManager:
    """Tests for StateManager."""

    @pytest.fixture
    def temp_state_dir(self):
        """Create a temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def state_manager(self, temp_state_dir):
        """Create a StateManager with temp directory."""
        return StateManager(temp_state_dir)

    def test_write_and_read_workflow(self, state_manager):
        """Test writing and reading workflow metadata."""
        now = datetime.now(timezone.utc)
        workflow = WorkflowInfo(
            id="abc1234",
            name="test-workflow",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
            current_agent="test-agent",
        )

        state_manager.write_workflow_meta(workflow)
        loaded = state_manager.read_workflow("abc1234")

        assert loaded is not None
        assert loaded.id == "abc1234"
        assert loaded.name == "test-workflow"
        assert loaded.status == WorkflowStatus.RUNNING

    def test_write_and_read_agent(self, state_manager):
        """Test writing and reading agent state."""
        now = datetime.now(timezone.utc)

        # First write workflow meta
        workflow = WorkflowInfo(
            id="abc1234",
            name="test-workflow",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        state_manager.write_workflow_meta(workflow)

        # Write agent state
        agent = AgentInfo(
            name="my-agent",
            status=AgentStatus.RUNNING,
            session_name="doeff-abc1234-my-agent",
            pane_id="%42",
            started_at=now,
        )
        state_manager.write_agent_state("abc1234", agent)

        # Read back
        loaded = state_manager.read_agent("abc1234", "my-agent")
        assert loaded is not None
        assert loaded.name == "my-agent"
        assert loaded.status == AgentStatus.RUNNING
        assert loaded.pane_id == "%42"

    def test_prefix_matching(self, state_manager):
        """Test workflow ID prefix resolution."""
        now = datetime.now(timezone.utc)

        # Create two workflows
        wf1 = WorkflowInfo(
            id="abc1234",
            name="workflow-1",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        wf2 = WorkflowInfo(
            id="def5678",
            name="workflow-2",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        state_manager.write_workflow_meta(wf1)
        state_manager.write_workflow_meta(wf2)

        # Test exact match
        assert state_manager.resolve_prefix("abc1234") == "abc1234"

        # Test prefix match
        assert state_manager.resolve_prefix("abc") == "abc1234"
        assert state_manager.resolve_prefix("def") == "def5678"

        # Test not found
        assert state_manager.resolve_prefix("xyz") is None

    def test_ambiguous_prefix(self, state_manager):
        """Test that ambiguous prefixes raise an error."""
        now = datetime.now(timezone.utc)

        # Create workflows with same prefix
        wf1 = WorkflowInfo(
            id="aaa1111",
            name="workflow-1",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        wf2 = WorkflowInfo(
            id="aaa2222",
            name="workflow-2",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        state_manager.write_workflow_meta(wf1)
        state_manager.write_workflow_meta(wf2)

        # Should raise for ambiguous prefix
        with pytest.raises(ValueError, match="Ambiguous prefix"):
            state_manager.resolve_prefix("aaa")

        # Longer prefix should work
        assert state_manager.resolve_prefix("aaa1") == "aaa1111"

    def test_list_workflows(self, state_manager):
        """Test listing all workflows."""
        now = datetime.now(timezone.utc)

        # Create multiple workflows
        for i in range(3):
            wf = WorkflowInfo(
                id=f"wf{i}0000",
                name=f"workflow-{i}",
                status=WorkflowStatus.RUNNING,
                started_at=now,
                updated_at=now,
            )
            state_manager.write_workflow_meta(wf)

        workflows = state_manager.list_workflows()
        assert len(workflows) == 3

    def test_list_workflows_with_status_filter(self, state_manager):
        """Test filtering workflows by status."""
        now = datetime.now(timezone.utc)

        wf1 = WorkflowInfo(
            id="abc1234",
            name="running-wf",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        wf2 = WorkflowInfo(
            id="def5678",
            name="completed-wf",
            status=WorkflowStatus.COMPLETED,
            started_at=now,
            updated_at=now,
        )
        state_manager.write_workflow_meta(wf1)
        state_manager.write_workflow_meta(wf2)

        running = state_manager.list_workflows(status=[WorkflowStatus.RUNNING])
        assert len(running) == 1
        assert running[0].id == "abc1234"

    def test_delete_workflow(self, state_manager):
        """Test deleting a workflow."""
        now = datetime.now(timezone.utc)

        wf = WorkflowInfo(
            id="abc1234",
            name="to-delete",
            status=WorkflowStatus.COMPLETED,
            started_at=now,
            updated_at=now,
        )
        state_manager.write_workflow_meta(wf)

        assert state_manager.read_workflow("abc1234") is not None

        result = state_manager.delete_workflow("abc1234")
        assert result is True

        assert state_manager.read_workflow("abc1234") is None

    def test_append_and_read_trace(self, state_manager):
        """Test appending and reading trace entries."""
        now = datetime.now(timezone.utc)

        # Create workflow first
        wf = WorkflowInfo(
            id="abc1234",
            name="traced-wf",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        state_manager.write_workflow_meta(wf)

        # Append trace entries
        state_manager.append_trace("abc1234", {"step": 1, "effect": "RunAgent"})
        state_manager.append_trace("abc1234", {"step": 2, "effect": "Monitor"})

        # Read trace
        trace = state_manager.read_trace("abc1234")
        assert len(trace) == 2
        assert trace[0]["step"] == 1
        assert trace[1]["effect"] == "Monitor"


class TestGetDefaultStateDir:
    """Tests for default state directory."""

    def test_returns_path(self):
        """Test that get_default_state_dir returns a Path."""
        path = get_default_state_dir()
        assert isinstance(path, Path)
        assert "doeff-agentic" in str(path)
