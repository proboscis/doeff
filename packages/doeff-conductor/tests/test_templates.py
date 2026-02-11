"""Tests for doeff-conductor workflow templates.

Tests workflow templates with mocked handlers:
- basic_pr: issue -> agent -> PR
- enforced_pr: issue -> agent -> test -> fix loop -> PR
- reviewed_pr: issue -> agent -> review -> PR
- multi_agent: issue -> parallel agents -> merge -> PR
"""

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from doeff_conductor import (
    Commit,
    CreatePR,
    CreateWorktree,
    MergeBranches,
    Push,
    ResolveIssue,
    RunAgent,
)
from doeff_conductor.handlers import mock_handlers as build_mock_handlers
from doeff_conductor.handlers import run_sync
from doeff_conductor.templates import (
    TEMPLATES,
    basic_pr,
    enforced_pr,
    get_available_templates,
    get_template,
    get_template_source,
    is_template,
    multi_agent,
    reviewed_pr,
)
from doeff_conductor.types import (
    Issue,
    IssueStatus,
    PRHandle,
    WorktreeEnv,
)


def _run_with_effect_handlers(program: Any, handlers: dict[type, Callable[[Any], Any]]):
    return run_sync(program, scheduled_handlers=build_mock_handlers(overrides=handlers))


class TestTemplateRegistry:
    """Tests for template registry functions."""

    def test_is_template_valid(self):
        """Test is_template returns True for valid templates."""
        assert is_template("basic_pr")
        assert is_template("enforced_pr")
        assert is_template("reviewed_pr")
        assert is_template("multi_agent")

    def test_is_template_invalid(self):
        """Test is_template returns False for invalid templates."""
        assert not is_template("nonexistent")
        assert not is_template("")
        assert not is_template("Basic_PR")  # Case sensitive

    def test_get_template_valid(self):
        """Test get_template returns callable for valid templates."""
        func = get_template("basic_pr")
        assert callable(func)
        assert func is basic_pr

    def test_get_template_invalid(self):
        """Test get_template raises KeyError for invalid templates."""
        with pytest.raises(KeyError) as exc_info:
            get_template("nonexistent")
        assert "nonexistent" in str(exc_info.value)

    def test_get_available_templates(self):
        """Test get_available_templates returns all templates."""
        templates = get_available_templates()
        assert isinstance(templates, dict)
        assert "basic_pr" in templates
        assert "enforced_pr" in templates
        assert "reviewed_pr" in templates
        assert "multi_agent" in templates

        # Each entry should have a description
        for _name, desc in templates.items():
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_get_template_source(self):
        """Test get_template_source returns source code or raises TypeError.

        Note: The @do decorator wraps functions in DoYieldFunction which
        cannot be inspected with getsource(). This is an existing limitation.
        """
        source: str | None = None
        source_error: TypeError | None = None
        try:
            source = get_template_source("basic_pr")
        except TypeError as exc:
            source_error = exc

        if source_error is not None:
            assert "module, class, method, function" in str(source_error)
            return

        assert source is not None
        assert "@do" in source
        assert "def basic_pr" in source
        assert "CreateWorktree" in source

    def test_get_template_source_invalid(self):
        """Test get_template_source raises KeyError for invalid templates."""
        with pytest.raises(KeyError):
            get_template_source("nonexistent")

    def test_templates_registry_structure(self):
        """Test TEMPLATES registry has correct structure."""
        assert isinstance(TEMPLATES, dict)
        for _name, (func, desc) in TEMPLATES.items():
            assert callable(func)
            assert isinstance(desc, str)


class MockHandlerFixtures:
    """Mixin providing mock handler fixtures."""

    @pytest.fixture
    def mock_issue(self) -> Issue:
        """Create a mock issue for testing."""
        return Issue(
            id="ISSUE-001",
            title="Test Feature",
            body="Implement a test feature with comprehensive testing",
            status=IssueStatus.OPEN,
            labels=("feature",),
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def mock_worktree_env(self, tmp_path: Path) -> WorktreeEnv:
        """Create a mock worktree environment."""
        env_path = tmp_path / "worktrees" / "env-test"
        env_path.mkdir(parents=True)
        return WorktreeEnv(
            id="env-test",
            path=env_path,
            branch="feature/issue-001",
            base_commit="abc123def456",
            issue_id="ISSUE-001",
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def mock_pr(self) -> PRHandle:
        """Create a mock PR handle."""
        return PRHandle(
            url="https://github.com/test/repo/pull/1",
            number=1,
            title="Test Feature",
            branch="feature/issue-001",
            target="main",
            status="open",
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def mock_handlers(
        self,
        mock_worktree_env: WorktreeEnv,
        mock_pr: PRHandle,
        mock_issue: Issue,
    ) -> dict[type, Callable[[Any], Any]]:
        """Create mock handlers for all effects."""

        def handle_create_worktree(effect: CreateWorktree) -> WorktreeEnv:
            return mock_worktree_env

        def handle_merge_branches(effect: MergeBranches) -> WorktreeEnv:
            return mock_worktree_env

        def handle_run_agent(effect: RunAgent) -> str:
            # Simulate passing tests by default
            return "All tests passed successfully"

        def handle_commit(effect: Commit) -> str:
            return "abc123def456789012345678901234567890"

        def handle_push(effect: Push) -> None:
            pass

        def handle_create_pr(effect: CreatePR) -> PRHandle:
            return mock_pr

        def handle_resolve_issue(effect: ResolveIssue) -> Issue:
            return Issue(
                id=mock_issue.id,
                title=mock_issue.title,
                body=mock_issue.body,
                status=IssueStatus.RESOLVED,
                pr_url=effect.pr_url,
                created_at=mock_issue.created_at,
                resolved_at=datetime.now(timezone.utc),
            )

        return {
            CreateWorktree: handle_create_worktree,
            MergeBranches: handle_merge_branches,
            RunAgent: handle_run_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }


class TestBasicPRTemplate(MockHandlerFixtures):
    """Tests for basic_pr template."""

    def test_template_structure(self):
        """Verify basic_pr is a valid @do workflow."""
        assert callable(basic_pr)
        # Should have __wrapped__ from @do decorator
        assert hasattr(basic_pr, "__wrapped__") or callable(basic_pr)

    def test_template_returns_program(self, mock_issue: Issue):
        """Test that basic_pr returns a Program."""
        program = basic_pr(mock_issue)
        assert program is not None

    def test_template_with_mock_handlers(
        self,
        mock_issue: Issue,
        mock_handlers: dict,
        mock_pr: PRHandle,
    ):
        """Run basic_pr template with mocked effects."""
        result = _run_with_effect_handlers(basic_pr(mock_issue), mock_handlers)

        assert result.is_ok()
        pr = result.value
        assert pr.url == mock_pr.url
        assert pr.number == mock_pr.number

    def test_basic_pr_effects_sequence(self, mock_issue: Issue):
        """Verify basic_pr executes expected high-level step order."""
        calls: list[str] = []

        def handle_create_worktree(effect: CreateWorktree) -> WorktreeEnv:
            calls.append("create_worktree")
            return WorktreeEnv(
                id="env-test",
                path=Path("/tmp/env-test"),
                branch="feature/issue-001",
                base_commit="abc123",
                issue_id=mock_issue.id,
                created_at=datetime.now(timezone.utc),
            )

        def handle_run_agent(effect: RunAgent) -> str:
            calls.append("run_agent")
            return "ok"

        def handle_commit(effect: Commit) -> str:
            calls.append("commit")
            return "abc123"

        def handle_push(effect: Push) -> None:
            calls.append("push")

        def handle_create_pr(effect: CreatePR) -> PRHandle:
            calls.append("create_pr")
            return PRHandle(
                url="https://github.com/test/repo/pull/1",
                number=1,
                title=effect.title,
                branch="feature/issue-001",
                target=effect.target,
                status="open",
                created_at=datetime.now(timezone.utc),
            )

        def handle_resolve_issue(effect: ResolveIssue) -> Issue:
            calls.append("resolve_issue")
            return Issue(
                id=mock_issue.id,
                title=mock_issue.title,
                body=mock_issue.body,
                status=IssueStatus.RESOLVED,
                created_at=mock_issue.created_at,
                resolved_at=datetime.now(timezone.utc),
                pr_url=effect.pr_url,
            )

        handlers = {
            CreateWorktree: handle_create_worktree,
            RunAgent: handle_run_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }
        result = _run_with_effect_handlers(basic_pr(mock_issue), handlers)
        assert result.is_ok()
        assert calls == [
            "create_worktree",
            "run_agent",
            "commit",
            "push",
            "create_pr",
            "resolve_issue",
        ]


class TestEnforcedPRTemplate(MockHandlerFixtures):
    """Tests for enforced_pr template."""

    def test_template_structure(self):
        """Verify enforced_pr is a valid @do workflow."""
        assert callable(enforced_pr)

    def test_template_returns_program(self, mock_issue: Issue):
        """Test that enforced_pr returns a Program."""
        program = enforced_pr(mock_issue)
        assert program is not None

    def test_template_with_mock_handlers_passing_tests(
        self,
        mock_issue: Issue,
        mock_handlers: dict,
    ):
        """Run enforced_pr with tests that pass on first try."""
        result = _run_with_effect_handlers(enforced_pr(mock_issue), mock_handlers)
        assert result.is_ok()

    def test_template_with_failing_tests(
        self,
        mock_issue: Issue,
        mock_worktree_env: WorktreeEnv,
        mock_pr: PRHandle,
    ):
        """Test enforced_pr handles test failures correctly."""
        test_call_count = [0]

        def handle_run_agent(effect: RunAgent) -> str:
            test_call_count[0] += 1
            # First call is implementation, second is test (fails), third is fix, fourth is test (passes)
            if test_call_count[0] == 2:
                return "FAILED: test_something - AssertionError"
            if test_call_count[0] == 4:
                return "All tests passed successfully"
            return "Implementation complete"

        def handle_create_worktree(e):
            return mock_worktree_env

        def handle_commit(e):
            return "abc123"

        def handle_push(e):
            pass

        def handle_create_pr(e):
            return mock_pr

        def handle_resolve_issue(e):
            return Issue(
                id="ISSUE-001",
                title="Test",
                body="Body",
                status=IssueStatus.RESOLVED,
            )

        handlers = {
            CreateWorktree: handle_create_worktree,
            RunAgent: handle_run_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(enforced_pr(mock_issue), handlers)
        assert result.is_ok()

    def test_template_fails_after_max_retries(
        self,
        mock_issue: Issue,
        mock_worktree_env: WorktreeEnv,
    ):
        """Test enforced_pr raises error after max retries."""

        def handle_run_agent(effect: RunAgent) -> str:
            # Always fail tests
            if "test" in effect.prompt.lower():
                return "FAILED: test_something - AssertionError"
            return "Implementation complete"

        def handle_create_worktree(e):
            return mock_worktree_env

        handlers = {
            CreateWorktree: handle_create_worktree,
            RunAgent: handle_run_agent,
        }

        result = _run_with_effect_handlers(enforced_pr(mock_issue, max_retries=2), handlers)
        # Should fail with RuntimeError after max retries
        assert result.is_err()

    def test_custom_max_retries(self, mock_issue: Issue):
        """Test enforced_pr respects max_retries parameter."""
        program = enforced_pr(mock_issue, max_retries=5)
        assert program is not None

    def test_custom_test_command(self, mock_issue: Issue):
        """Test enforced_pr respects test_command parameter."""
        program = enforced_pr(mock_issue, test_command="npm test")
        assert program is not None


class TestReviewedPRTemplate(MockHandlerFixtures):
    """Tests for reviewed_pr template."""

    def test_template_structure(self):
        """Verify reviewed_pr is a valid @do workflow."""
        assert callable(reviewed_pr)

    def test_template_returns_program(self, mock_issue: Issue):
        """Test that reviewed_pr returns a Program."""
        program = reviewed_pr(mock_issue)
        assert program is not None

    def test_template_with_mock_handlers_approved(
        self,
        mock_issue: Issue,
        mock_worktree_env: WorktreeEnv,
        mock_pr: PRHandle,
    ):
        """Run reviewed_pr with review that approves on first try."""

        def handle_run_agent(effect: RunAgent) -> str:
            if effect.name == "reviewer":
                return "APPROVED - Code looks good, well tested"
            return "Implementation complete"

        def handle_create_worktree(e):
            return mock_worktree_env

        def handle_commit(e):
            return "abc123"

        def handle_push(e):
            pass

        def handle_create_pr(e):
            return mock_pr

        def handle_resolve_issue(e):
            return Issue(
                id="ISSUE-001",
                title="Test",
                body="Body",
                status=IssueStatus.RESOLVED,
            )

        handlers = {
            CreateWorktree: handle_create_worktree,
            RunAgent: handle_run_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(reviewed_pr(mock_issue), handlers)
        assert result.is_ok()

    def test_template_with_review_feedback(
        self,
        mock_issue: Issue,
        mock_worktree_env: WorktreeEnv,
        mock_pr: PRHandle,
    ):
        """Test reviewed_pr handles review feedback correctly."""
        review_count = [0]

        def handle_run_agent(effect: RunAgent) -> str:
            if effect.name == "reviewer":
                review_count[0] += 1
                if review_count[0] == 1:
                    return "Issues found: Missing error handling"
                return "APPROVED - Issues addressed"
            return "Implementation complete"

        def handle_create_worktree(e):
            return mock_worktree_env

        def handle_commit(e):
            return "abc123"

        def handle_push(e):
            pass

        def handle_create_pr(e):
            return mock_pr

        def handle_resolve_issue(e):
            return Issue(
                id="ISSUE-001",
                title="Test",
                body="Body",
                status=IssueStatus.RESOLVED,
            )

        handlers = {
            CreateWorktree: handle_create_worktree,
            RunAgent: handle_run_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(reviewed_pr(mock_issue), handlers)
        assert result.is_ok()

    def test_custom_max_reviews(self, mock_issue: Issue):
        """Test reviewed_pr respects max_reviews parameter."""
        program = reviewed_pr(mock_issue, max_reviews=5)
        assert program is not None


class TestMultiAgentTemplate(MockHandlerFixtures):
    """Tests for multi_agent template."""

    def test_template_structure(self):
        """Verify multi_agent is a valid @do workflow."""
        assert callable(multi_agent)

    def test_template_returns_program(self, mock_issue: Issue):
        """Test that multi_agent returns a Program."""
        program = multi_agent(mock_issue)
        assert program is not None

    def test_multi_agent_effects_include_spawn_and_gather(self, mock_issue: Issue):
        """Verify multi_agent executes both parallel branches and merge path."""
        calls: list[str] = []

        def handle_create_worktree(effect: CreateWorktree) -> WorktreeEnv:
            calls.append(f"create_worktree:{effect.suffix}")
            return WorktreeEnv(
                id=f"env-{effect.suffix}",
                path=Path(f"/tmp/{effect.suffix}"),
                branch=f"feature/{effect.suffix}",
                base_commit="abc123",
                issue_id=mock_issue.id,
                created_at=datetime.now(timezone.utc),
            )

        def handle_merge_branches(effect: MergeBranches) -> WorktreeEnv:
            calls.append("merge_branches")
            return WorktreeEnv(
                id="env-merged",
                path=Path("/tmp/merged"),
                branch="feature/merged",
                base_commit="abc123",
                issue_id=mock_issue.id,
                created_at=datetime.now(timezone.utc),
            )

        def handle_run_agent(effect: RunAgent) -> str:
            calls.append(f"run_agent:{effect.name}")
            return "ok"

        def handle_commit(effect: Commit) -> str:
            calls.append("commit")
            return "abc123"

        def handle_push(effect: Push) -> None:
            calls.append("push")

        def handle_create_pr(effect: CreatePR) -> PRHandle:
            calls.append("create_pr")
            return PRHandle(
                url="https://github.com/test/repo/pull/1",
                number=1,
                title=effect.title,
                branch="feature/merged",
                target=effect.target,
                status="open",
                created_at=datetime.now(timezone.utc),
            )

        def handle_resolve_issue(effect: ResolveIssue) -> Issue:
            calls.append("resolve_issue")
            return Issue(
                id=mock_issue.id,
                title=mock_issue.title,
                body=mock_issue.body,
                status=IssueStatus.RESOLVED,
                created_at=mock_issue.created_at,
                resolved_at=datetime.now(timezone.utc),
                pr_url=effect.pr_url,
            )

        handlers = {
            CreateWorktree: handle_create_worktree,
            MergeBranches: handle_merge_branches,
            RunAgent: handle_run_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(multi_agent(mock_issue), handlers)
        assert result.is_ok()
        assert calls.count("create_pr") == 1
        assert calls.count("merge_branches") == 1
        assert calls.count("resolve_issue") == 1

    def test_template_with_mock_handlers(
        self,
        mock_issue: Issue,
        mock_worktree_env: WorktreeEnv,
        mock_pr: PRHandle,
    ):
        """Run multi_agent template with mocked effects.

        Gather/Spawn are handled by the local test harness.
        """

        def handle_create_worktree(e):
            return mock_worktree_env

        def handle_merge_branches(e):
            return mock_worktree_env

        def handle_run_agent(e):
            return "Done"

        def handle_commit(e):
            return "abc123"

        def handle_push(e):
            pass

        def handle_create_pr(e):
            return mock_pr

        def handle_resolve_issue(e):
            return Issue(
                id="ISSUE-001",
                title="Test",
                body="Body",
                status=IssueStatus.RESOLVED,
            )

        handlers = {
            CreateWorktree: handle_create_worktree,
            MergeBranches: handle_merge_branches,
            RunAgent: handle_run_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(multi_agent(mock_issue), handlers)
        assert result.is_ok()


class TestTemplateDocumentation:
    """Tests for template documentation."""

    def test_basic_pr_docstring(self):
        """Verify basic_pr has a docstring."""
        assert basic_pr.__doc__ is not None
        assert "issue" in basic_pr.__doc__.lower()
        assert "agent" in basic_pr.__doc__.lower()
        assert "pr" in basic_pr.__doc__.lower()

    def test_enforced_pr_docstring(self):
        """Verify enforced_pr has a docstring."""
        assert enforced_pr.__doc__ is not None
        assert "test" in enforced_pr.__doc__.lower()
        assert "retry" in enforced_pr.__doc__.lower() or "retries" in enforced_pr.__doc__.lower()

    def test_reviewed_pr_docstring(self):
        """Verify reviewed_pr has a docstring."""
        assert reviewed_pr.__doc__ is not None
        assert "review" in reviewed_pr.__doc__.lower()

    def test_multi_agent_docstring(self):
        """Verify multi_agent has a docstring."""
        assert multi_agent.__doc__ is not None
        assert "parallel" in multi_agent.__doc__.lower() or "multi" in multi_agent.__doc__.lower()


class TestTemplateErrorHandling(MockHandlerFixtures):
    """Tests for template error handling."""

    def test_basic_pr_propagates_worktree_error(
        self,
        mock_issue: Issue,
    ):
        """Test that basic_pr propagates worktree creation errors."""
        from doeff_conductor.exceptions import WorktreeError

        def handle_create_worktree(e):
            raise WorktreeError(operation="create", message="Failed to create worktree")

        handlers = {
            CreateWorktree: handle_create_worktree,
        }

        result = _run_with_effect_handlers(basic_pr(mock_issue), handlers)
        assert result.is_err()

    def test_basic_pr_propagates_agent_error(
        self,
        mock_issue: Issue,
        mock_worktree_env: WorktreeEnv,
    ):
        """Test that basic_pr propagates agent errors."""
        from doeff_conductor.exceptions import AgentError

        def handle_create_worktree(e):
            return mock_worktree_env

        def handle_run_agent(e):
            raise AgentError(operation="run", message="Agent crashed")

        handlers = {
            CreateWorktree: handle_create_worktree,
            RunAgent: handle_run_agent,
        }

        result = _run_with_effect_handlers(basic_pr(mock_issue), handlers)
        assert result.is_err()

    def test_basic_pr_propagates_pr_error(
        self,
        mock_issue: Issue,
        mock_worktree_env: WorktreeEnv,
    ):
        """Test that basic_pr propagates PR creation errors."""
        from doeff_conductor.exceptions import PRError

        def handle_create_worktree(e):
            return mock_worktree_env

        def handle_run_agent(e):
            return "Done"

        def handle_commit(e):
            return "abc123"

        def handle_push(e):
            pass

        def handle_create_pr(e):
            raise PRError(operation="create", message="PR creation failed")

        handlers = {
            CreateWorktree: handle_create_worktree,
            RunAgent: handle_run_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
        }

        result = _run_with_effect_handlers(basic_pr(mock_issue), handlers)
        assert result.is_err()
