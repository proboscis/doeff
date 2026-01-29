"""Tests for doeff-conductor types."""

from datetime import datetime, timezone
from pathlib import Path

from doeff_conductor.types import (
    AgentRef,
    Issue,
    IssueStatus,
    MergeStrategy,
    PRHandle,
    WorkflowHandle,
    WorkflowStatus,
    WorktreeEnv,
)


class TestIssue:
    """Tests for Issue type."""

    def test_create_issue(self):
        """Test creating an issue."""
        issue = Issue(
            id="ISSUE-001",
            title="Test issue",
            body="This is a test issue body",
            status=IssueStatus.OPEN,
            labels=("feature", "test"),
        )

        assert issue.id == "ISSUE-001"
        assert issue.title == "Test issue"
        assert issue.status == IssueStatus.OPEN
        assert "feature" in issue.labels

    def test_issue_to_dict(self):
        """Test issue serialization."""
        now = datetime.now(timezone.utc)
        issue = Issue(
            id="ISSUE-002",
            title="Serializable",
            body="Body",
            status=IssueStatus.IN_PROGRESS,
            created_at=now,
        )

        data = issue.to_dict()
        assert data["id"] == "ISSUE-002"
        assert data["status"] == "in_progress"
        assert data["created_at"] == now.isoformat()

    def test_issue_from_dict(self):
        """Test issue deserialization."""
        data = {
            "id": "ISSUE-003",
            "title": "From dict",
            "body": "Body",
            "status": "resolved",
            "labels": ["bug"],
            "created_at": "2025-01-01T00:00:00+00:00",
        }

        issue = Issue.from_dict(data)
        assert issue.id == "ISSUE-003"
        assert issue.status == IssueStatus.RESOLVED
        assert issue.labels == ("bug",)


class TestWorktreeEnv:
    """Tests for WorktreeEnv type."""

    def test_create_worktree_env(self):
        """Test creating a worktree environment."""
        env = WorktreeEnv(
            id="abc123",
            path=Path("/tmp/test"),
            branch="feature-branch",
            base_commit="deadbeef",
        )

        assert env.id == "abc123"
        assert env.path == Path("/tmp/test")
        assert env.branch == "feature-branch"

    def test_worktree_env_roundtrip(self):
        """Test worktree env serialization roundtrip."""
        env = WorktreeEnv(
            id="def456",
            path=Path("/tmp/worktree"),
            branch="test-branch",
            base_commit="cafebabe",
            issue_id="ISSUE-001",
        )

        data = env.to_dict()
        restored = WorktreeEnv.from_dict(data)

        assert restored.id == env.id
        assert restored.path == env.path
        assert restored.branch == env.branch
        assert restored.issue_id == env.issue_id


class TestWorkflowHandle:
    """Tests for WorkflowHandle type."""

    def test_workflow_status_is_terminal(self):
        """Test terminal status detection."""
        assert WorkflowStatus.DONE.is_terminal()
        assert WorkflowStatus.ERROR.is_terminal()
        assert WorkflowStatus.ABORTED.is_terminal()
        assert not WorkflowStatus.RUNNING.is_terminal()
        assert not WorkflowStatus.PENDING.is_terminal()
        assert not WorkflowStatus.BLOCKED.is_terminal()

    def test_workflow_handle_roundtrip(self):
        """Test workflow handle serialization roundtrip."""
        handle = WorkflowHandle(
            id="workflow123",
            name="test-workflow",
            status=WorkflowStatus.RUNNING,
            template="basic_pr",
            issue_id="ISSUE-001",
        )

        data = handle.to_dict()
        restored = WorkflowHandle.from_dict(data)

        assert restored.id == handle.id
        assert restored.status == handle.status
        assert restored.template == handle.template


class TestPRHandle:
    """Tests for PRHandle type."""

    def test_pr_handle_roundtrip(self):
        """Test PR handle serialization roundtrip."""
        pr = PRHandle(
            url="https://github.com/user/repo/pull/123",
            number=123,
            title="Test PR",
            branch="feature",
            target="main",
        )

        data = pr.to_dict()
        restored = PRHandle.from_dict(data)

        assert restored.url == pr.url
        assert restored.number == pr.number
        assert restored.title == pr.title


class TestAgentRef:
    """Tests for AgentRef type."""

    def test_agent_ref_roundtrip(self):
        """Test agent ref serialization roundtrip."""
        ref = AgentRef(
            id="session123",
            name="implementer",
            workflow_id="workflow456",
            env_id="env789",
            agent_type="claude",
        )

        data = ref.to_dict()
        restored = AgentRef.from_dict(data)

        assert restored.id == ref.id
        assert restored.name == ref.name
        assert restored.agent_type == ref.agent_type


class TestMergeStrategy:
    """Tests for MergeStrategy enum."""

    def test_merge_strategy_values(self):
        """Test merge strategy enum values."""
        assert MergeStrategy.MERGE.value == "merge"
        assert MergeStrategy.REBASE.value == "rebase"
        assert MergeStrategy.SQUASH.value == "squash"
