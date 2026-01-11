"""Tests for doeff-conductor effects."""

import pytest

from doeff_conductor.effects import (
    ConductorEffectBase,
    CreateWorktree,
    MergeBranches,
    DeleteWorktree,
    CreateIssue,
    ListIssues,
    GetIssue,
    ResolveIssue,
    RunAgent,
    SpawnAgent,
    SendMessage,
    WaitForStatus,
    CaptureOutput,
    Commit,
    Push,
    CreatePR,
    MergePR,
)


class TestEffectBase:
    """Tests for ConductorEffectBase."""

    def test_effect_base_protocol(self):
        """Test that effects follow the protocol."""
        effect = CreateWorktree()

        # Should have created_at attribute
        assert hasattr(effect, "created_at")

        # Should have with_created_at method
        new_effect = effect.with_created_at("test")
        assert new_effect.created_at == "test"

        # Should have intercept method
        intercepted = effect.intercept(lambda x: x)
        assert intercepted is effect  # No nested programs to intercept

        # Should have to_generator method
        gen = effect.to_generator()
        assert hasattr(gen, "__next__")


class TestWorktreeEffects:
    """Tests for worktree effects."""

    def test_create_worktree_defaults(self):
        """Test CreateWorktree with defaults."""
        effect = CreateWorktree()

        assert effect.issue is None
        assert effect.base_branch is None
        assert effect.suffix is None
        assert effect.name is None

    def test_create_worktree_with_suffix(self):
        """Test CreateWorktree with suffix."""
        effect = CreateWorktree(suffix="impl")
        assert effect.suffix == "impl"

    def test_merge_branches_requires_envs(self):
        """Test MergeBranches requires environments."""
        # This would be validated at runtime by the handler
        effect = MergeBranches(envs=())
        assert effect.envs == ()


class TestIssueEffects:
    """Tests for issue effects."""

    def test_create_issue_required_fields(self):
        """Test CreateIssue required fields."""
        effect = CreateIssue(title="Test", body="Body")
        assert effect.title == "Test"
        assert effect.body == "Body"
        assert effect.labels == ()

    def test_create_issue_with_labels(self):
        """Test CreateIssue with labels."""
        effect = CreateIssue(
            title="Feature",
            body="Description",
            labels=("feature", "enhancement"),
        )
        assert effect.labels == ("feature", "enhancement")

    def test_list_issues_filters(self):
        """Test ListIssues filter options."""
        from doeff_conductor.types import IssueStatus

        effect = ListIssues(status=IssueStatus.OPEN, labels=("bug",), limit=10)
        assert effect.status == IssueStatus.OPEN
        assert effect.labels == ("bug",)
        assert effect.limit == 10

    def test_get_issue_by_id(self):
        """Test GetIssue effect."""
        effect = GetIssue(id="ISSUE-001")
        assert effect.id == "ISSUE-001"


class TestAgentEffects:
    """Tests for agent effects."""

    def test_run_agent_required_fields(self):
        """Test RunAgent required fields."""
        from doeff_conductor.types import WorktreeEnv
        from pathlib import Path

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp"),
            branch="test",
            base_commit="abc",
        )

        effect = RunAgent(env=env, prompt="Do the thing")
        assert effect.prompt == "Do the thing"
        assert effect.agent_type == "claude"  # default

    def test_spawn_agent_name(self):
        """Test SpawnAgent with custom name."""
        from doeff_conductor.types import WorktreeEnv
        from pathlib import Path

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp"),
            branch="test",
            base_commit="abc",
        )

        effect = SpawnAgent(env=env, prompt="Start task", name="worker-1")
        assert effect.name == "worker-1"


class TestGitEffects:
    """Tests for git effects."""

    def test_commit_defaults(self):
        """Test Commit effect defaults."""
        from doeff_conductor.types import WorktreeEnv
        from pathlib import Path

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp"),
            branch="test",
            base_commit="abc",
        )

        effect = Commit(env=env, message="feat: add feature")
        assert effect.message == "feat: add feature"
        assert effect.all is True  # default

    def test_push_defaults(self):
        """Test Push effect defaults."""
        from doeff_conductor.types import WorktreeEnv
        from pathlib import Path

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp"),
            branch="test",
            base_commit="abc",
        )

        effect = Push(env=env)
        assert effect.remote == "origin"
        assert effect.force is False
        assert effect.set_upstream is True

    def test_create_pr_required_fields(self):
        """Test CreatePR required fields."""
        from doeff_conductor.types import WorktreeEnv
        from pathlib import Path

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp"),
            branch="test",
            base_commit="abc",
        )

        effect = CreatePR(env=env, title="Add feature")
        assert effect.title == "Add feature"
        assert effect.target == "main"
        assert effect.draft is False
