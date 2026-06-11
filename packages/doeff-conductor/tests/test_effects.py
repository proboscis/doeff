"""Tests for doeff-conductor effects."""

import dataclasses

from doeff_conductor.effects import (
    Agent,
    AgentTask,
    Commit,
    CreateIssue,
    CreatePR,
    CreateWorkspace,
    DeleteWorkspace,
    GetIssue,
    ListIssues,
    MergePR,
    MergeWorkspaces,
    Push,
    ResolveIssue,
)


class TestEffectBase:
    """Tests for ConductorEffectBase."""

    def test_effect_base_protocol(self):
        """Test that effects follow the protocol."""
        effect = CreateWorkspace(workspace_id="ws-protocol")

        # Should have intercept method (no nested programs for conductor effects).
        intercepted = effect.intercept(lambda x: x)
        assert intercepted is effect

        # Effect should be a first-class doeff effect value
        from doeff import EffectBase

        assert isinstance(effect, EffectBase)


class TestWorkspaceEffects:
    """Tests for workspace effects."""

    def test_create_workspace_defaults(self):
        """Test CreateWorkspace with defaults."""
        effect = CreateWorkspace(workspace_id="ws-defaults")

        assert effect.workspace_id == "ws-defaults"
        assert effect.repo == "default"
        assert effect.issue is None
        assert effect.from_ref is None

    def test_create_workspace_requires_identity(self):
        """Workspace identity is mandatory — there is no random fallback."""
        import pytest

        with pytest.raises(TypeError):
            CreateWorkspace()  # type: ignore[call-arg]

    def test_merge_workspaces_requires_workspaces(self):
        effect = MergeWorkspaces(workspace_id="ws-merged", workspaces=())
        assert effect.workspaces == ()
        assert effect.workspace_id == "ws-merged"

    def test_merge_workspaces_requires_identity(self):
        import pytest

        with pytest.raises(TypeError):
            MergeWorkspaces(workspaces=())  # type: ignore[call-arg]

    def test_delete_workspace(self):
        from doeff_conductor.types import Workspace

        env = Workspace(
            id="test",
            repo="default",
            ref="test-branch",
            base_ref="main",
        )

        effect = DeleteWorkspace(workspace=env)
        assert effect.workspace == env
        assert effect.force is False

    def test_delete_workspace_force(self):
        from doeff_conductor.types import Workspace

        env = Workspace(
            id="test",
            repo="default",
            ref="test-branch",
            base_ref="main",
        )

        effect = DeleteWorkspace(workspace=env, force=True)
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

    def test_agent_task_requires_explicit_agent_type(self):
        """AgentTask has no worker identity default at call sites."""
        assert "agent_type" in AgentTask.__dataclass_fields__
        assert AgentTask.__dataclass_fields__["agent_type"].default is dataclasses.MISSING

    def test_agent_effect_wraps_task(self):
        """Test schema-validated Agent effect construction."""
        from doeff_conductor.types import Workspace

        env = Workspace(
            id="test",
            repo="default",
            ref="test",
            base_ref="main",
        )

        task = AgentTask(
            run_id="run-001",
            node_id="implement",
            attempt=0,
            env=env,
            prompt="Do the thing",
            result_schema={"type": "object"},
            verification_class="test-verifiable",
            agent_type="codex",
        )
        effect = Agent(task)

        assert effect.task is task
        assert task.agent_type == "codex"
        assert task.session_id == "run-001-implement-0"


class TestGitEffects:
    """Tests for git effects."""

    def test_commit_defaults(self):
        """Test Commit effect defaults."""
        from doeff_conductor.types import Workspace

        env = Workspace(
            id="test",
            repo="default",
            ref="test",
            base_ref="main",
        )

        effect = Commit(workspace=env, message="feat: add feature")
        assert effect.message == "feat: add feature"
        assert effect.all is True  # default

    def test_push_defaults(self):
        """Test Push effect defaults."""
        from doeff_conductor.types import Workspace

        env = Workspace(
            id="test",
            repo="default",
            ref="test",
            base_ref="main",
        )

        effect = Push(workspace=env)
        assert effect.remote == "origin"
        assert effect.force is False
        assert effect.set_upstream is True

    def test_create_pr_required_fields(self):
        """Test CreatePR required fields."""
        from doeff_conductor.types import Workspace

        env = Workspace(
            id="test",
            repo="default",
            ref="test",
            base_ref="main",
        )

        effect = CreatePR(workspace=env, title="Add feature")
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
            "CreateWorkspace",
            "MergeWorkspaces",
            "DeleteWorkspace",
            "CreateIssue",
            "ListIssues",
            "GetIssue",
            "ResolveIssue",
            "Agent",
            "AgentEffect",
            "AgentTask",
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

        production = production_handlers(
            workspace_handler=runtime,
            issue_handler=runtime,
            agent_handler=runtime,
            git_handler=runtime,
            exec_handler=runtime,
        )
        mocked = mock_handlers(runtime=runtime)

        assert callable(production)
        assert callable(mocked)
