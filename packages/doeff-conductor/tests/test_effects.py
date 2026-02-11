"""Tests for doeff-conductor effects."""

import pytest
from doeff_conductor.effects import (
    CaptureOutput,
    Commit,
    CreateIssue,
    CreatePR,
    CreateWorktree,
    DeleteWorktree,
    GetIssue,
    ListIssues,
    MergeBranches,
    MergePR,
    Push,
    ResolveIssue,
    RunAgent,
    SendMessage,
    SpawnAgent,
    WaitForStatus,
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

        # Effect should be a first-class doeff effect value
        from doeff import EffectBase

        assert isinstance(effect, EffectBase)


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
        effect = MergeBranches(envs=())
        assert effect.envs == ()

    def test_delete_worktree(self):
        from pathlib import Path

        from doeff_conductor.types import WorktreeEnv

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp/worktree"),
            branch="test-branch",
            base_commit="abc123",
        )

        effect = DeleteWorktree(env=env)
        assert effect.env == env
        assert effect.force is False

    def test_delete_worktree_force(self):
        from pathlib import Path

        from doeff_conductor.types import WorktreeEnv

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp/worktree"),
            branch="test-branch",
            base_commit="abc123",
        )

        effect = DeleteWorktree(env=env, force=True)
        assert effect.force is True


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
        effect = GetIssue(id="ISSUE-001")
        assert effect.id == "ISSUE-001"

    def test_resolve_issue(self):
        from datetime import datetime, timezone

        from doeff_conductor.types import Issue, IssueStatus

        issue = Issue(
            id="ISSUE-001",
            title="Test Issue",
            body="Body",
            status=IssueStatus.OPEN,
            labels=(),
            created_at=datetime.now(timezone.utc),
        )

        effect = ResolveIssue(issue=issue, pr_url="https://github.com/org/repo/pull/1")
        assert effect.issue == issue
        assert effect.pr_url == "https://github.com/org/repo/pull/1"

    def test_resolve_issue_with_result(self):
        from datetime import datetime, timezone

        from doeff_conductor.types import Issue, IssueStatus

        issue = Issue(
            id="ISSUE-002",
            title="Bug Fix",
            body="Fix the bug",
            status=IssueStatus.OPEN,
            labels=("bug",),
            created_at=datetime.now(timezone.utc),
        )

        effect = ResolveIssue(issue=issue, result="Fixed by refactoring")
        assert effect.result == "Fixed by refactoring"


class TestAgentEffects:
    """Tests for agent effects."""

    def test_run_agent_required_fields(self):
        """Test RunAgent required fields."""
        from pathlib import Path

        from doeff_conductor.types import WorktreeEnv

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
        from pathlib import Path

        from doeff_conductor.types import WorktreeEnv

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp"),
            branch="test",
            base_commit="abc",
        )

        effect = SpawnAgent(env=env, prompt="Start task", name="worker-1")
        assert effect.name == "worker-1"

    def test_send_message(self):
        from doeff_conductor.types import AgentRef

        agent_ref = AgentRef(
            id="session-001",
            name="agent-1",
            workflow_id="wf-001",
            env_id="env-001",
            agent_type="claude",
        )

        effect = SendMessage(agent_ref=agent_ref, message="Continue")
        assert effect.agent_ref == agent_ref
        assert effect.message == "Continue"
        assert effect.wait is False  # default is False

    def test_send_message_no_wait(self):
        from doeff_conductor.types import AgentRef

        agent_ref = AgentRef(
            id="session-001",
            name="agent-1",
            workflow_id="wf-001",
            env_id="env-001",
            agent_type="claude",
        )

        effect = SendMessage(agent_ref=agent_ref, message="Fire and forget", wait=False)
        assert effect.wait is False

    def test_wait_for_status(self):
        from doeff_agentic import AgenticSessionStatus
        from doeff_conductor.types import AgentRef

        agent_ref = AgentRef(
            id="session-001",
            name="agent-1",
            workflow_id="wf-001",
            env_id="env-001",
            agent_type="claude",
        )

        effect = WaitForStatus(
            agent_ref=agent_ref,
            target=AgenticSessionStatus.DONE,
            timeout=60.0,
        )
        assert effect.agent_ref == agent_ref
        assert effect.target == AgenticSessionStatus.DONE
        assert effect.timeout == 60.0

    def test_wait_for_status_defaults(self):
        from doeff_agentic import AgenticSessionStatus
        from doeff_conductor.types import AgentRef

        agent_ref = AgentRef(
            id="session-001",
            name="agent-1",
            workflow_id="wf-001",
            env_id="env-001",
            agent_type="claude",
        )

        effect = WaitForStatus(agent_ref=agent_ref, target=AgenticSessionStatus.DONE)
        assert effect.timeout is None
        assert effect.poll_interval == 1.0

    def test_capture_output(self):
        from doeff_conductor.types import AgentRef

        agent_ref = AgentRef(
            id="session-001",
            name="agent-1",
            workflow_id="wf-001",
            env_id="env-001",
            agent_type="claude",
        )

        effect = CaptureOutput(agent_ref=agent_ref, lines=50)
        assert effect.agent_ref == agent_ref
        assert effect.lines == 50

    def test_capture_output_defaults(self):
        from doeff_conductor.types import AgentRef

        agent_ref = AgentRef(
            id="session-001",
            name="agent-1",
            workflow_id="wf-001",
            env_id="env-001",
            agent_type="claude",
        )

        effect = CaptureOutput(agent_ref=agent_ref)
        assert effect.lines == 500  # default is 500


class TestGitEffects:
    """Tests for git effects."""

    def test_commit_defaults(self):
        """Test Commit effect defaults."""
        from pathlib import Path

        from doeff_conductor.types import WorktreeEnv

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
        from pathlib import Path

        from doeff_conductor.types import WorktreeEnv

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
        from pathlib import Path

        from doeff_conductor.types import WorktreeEnv

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

    def test_merge_pr(self):
        from datetime import datetime, timezone

        from doeff_conductor.types import PRHandle

        pr = PRHandle(
            url="https://github.com/org/repo/pull/42",
            number=42,
            title="Feature PR",
            branch="feature-branch",
            target="main",
            status="open",
            created_at=datetime.now(timezone.utc),
        )

        effect = MergePR(pr=pr)
        assert effect.pr == pr
        assert effect.strategy is None
        assert effect.delete_branch is True  # default is True

    def test_merge_pr_with_strategy(self):
        from datetime import datetime, timezone

        from doeff_conductor.types import MergeStrategy, PRHandle

        pr = PRHandle(
            url="https://github.com/org/repo/pull/43",
            number=43,
            title="Squash PR",
            branch="squash-branch",
            target="main",
            status="open",
            created_at=datetime.now(timezone.utc),
        )

        effect = MergePR(pr=pr, strategy=MergeStrategy.SQUASH, delete_branch=True)
        assert effect.strategy == MergeStrategy.SQUASH
        assert effect.delete_branch is True

    def test_legacy_git_effects_are_deprecated(self):
        from pathlib import Path

        from doeff_conductor.types import WorktreeEnv

        env = WorktreeEnv(
            id="test",
            path=Path("/tmp"),
            branch="test",
            base_commit="abc",
        )

        with pytest.warns(
            DeprecationWarning,
            match="doeff_conductor\\.effects\\.git\\.Commit",
        ):
            _ = Commit(env=env, message="feat: deprecated")

    def test_generic_git_aliases_are_exported(self):
        from doeff_conductor.effects.git import (
            GitCommitEffect,
            GitCreatePREffect,
            GitDiffEffect,
            GitMergePREffect,
            GitPullEffect,
            GitPushEffect,
        )

        assert GitCommitEffect.__name__ == "GitCommit"
        assert GitPushEffect.__name__ == "GitPush"
        assert GitPullEffect.__name__ == "GitPull"
        assert GitDiffEffect.__name__ == "GitDiff"
        assert GitCreatePREffect.__name__ == "CreatePR"
        assert GitMergePREffect.__name__ == "MergePR"


class TestModuleExports:
    """Tests for package-level exports."""

    def test_effects_init_exports_all_effects(self):
        import doeff_conductor.effects as effects_module

        expected_names = {
            "ConductorEffectBase",
            "CreateWorktree",
            "MergeBranches",
            "DeleteWorktree",
            "CreateIssue",
            "ListIssues",
            "GetIssue",
            "ResolveIssue",
            "RunAgent",
            "SpawnAgent",
            "SendMessage",
            "WaitForStatus",
            "CaptureOutput",
            "Commit",
            "Push",
            "CreatePR",
            "MergePR",
        }

        assert expected_names.issubset(set(effects_module.__all__))
        for name in expected_names:
            assert hasattr(effects_module, name)

    def test_handlers_exports_include_production_and_mock_handlers(self, tmp_path):
        from doeff_conductor.handlers import mock_handlers, production_handlers
        from doeff_conductor.handlers.testing import MockConductorRuntime

        runtime = MockConductorRuntime(tmp_path)
        expected_effects = {
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
        }

        production = production_handlers(
            worktree_handler=runtime,
            issue_handler=runtime,
            agent_handler=runtime,
            git_handler=runtime,
        )
        mocked = mock_handlers(runtime=runtime)

        assert expected_effects.issubset(set(production))
        assert expected_effects.issubset(set(mocked))
