"""Tests for doeff-conductor workflow templates.

Tests workflow templates with mocked handlers:
- basic_pr: issue -> agent -> PR
- enforced_pr: issue -> agent -> test -> fix loop -> PR
- reviewed_pr: issue -> agent -> review -> PR
- multi_agent: issue -> parallel agents -> merge -> PR
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from doeff_conductor import (
    AgentEffect,
    Commit,
    CreatePR,
    CreateWorkspace,
    MergeWorkspaces,
    Push,
    ResolveIssue,
)
from doeff_conductor.handlers import mock_handlers as build_mock_handlers
from doeff_conductor.handlers import run_sync
from doeff_conductor.types import (
    Issue,
    IssueStatus,
    MergeStatus,
    MergeWorkspacesResult,
    PRHandle,
    Workspace,
)


HandlerMap = dict[type[Any], object]


def _run_with_effect_handlers(program: Any, handlers: HandlerMap):
    return run_sync(program, scheduled_handlers=build_mock_handlers(overrides=handlers))


def _templates_module() -> Any:
    import doeff_conductor.templates as conductor_templates

    return conductor_templates


def _valid_agent_artifact(
    effect: AgentEffect,
    *,
    passed: bool = True,
    verdict: str = "PASS",
    summary: str = "ok",
    findings: list[str] | None = None,
) -> dict[str, Any]:
    properties = effect.task.result_schema.get("properties", {})
    if "passed" in properties:
        return {
            "passed": passed,
            "summary": summary,
            "failures": [] if passed else ["test_something failed"],
        }
    if "verdict" in properties:
        return {
            "verdict": verdict,
            "findings": [] if verdict == "PASS" else (findings or ["Missing error handling"]),
            "summary": summary,
        }
    return {"summary": summary, "files_changed": []}


class TestTemplateRegistry:
    """Tests for template registry functions."""

    def test_is_template_valid(self):
        """Test is_template returns True for valid templates."""
        assert _templates_module().is_template("basic_pr")
        assert _templates_module().is_template("enforced_pr")
        assert _templates_module().is_template("reviewed_pr")
        assert _templates_module().is_template("multi_agent")

    def test_is_template_invalid(self):
        """Test is_template returns False for invalid templates."""
        assert not _templates_module().is_template("nonexistent")
        assert not _templates_module().is_template("")
        assert not _templates_module().is_template("Basic_PR")  # Case sensitive

    def test_get_template_valid(self):
        """Test get_template returns callable for valid templates."""
        func = _templates_module().get_template("basic_pr")
        assert callable(func)
        assert func is _templates_module().basic_pr

    def test_get_template_invalid(self):
        """Test get_template raises KeyError for invalid templates."""
        with pytest.raises(KeyError) as exc_info:
            _templates_module().get_template("nonexistent")
        assert "nonexistent" in str(exc_info.value)

    def test_get_available_templates(self):
        """Test get_available_templates returns all templates."""
        templates = _templates_module().get_available_templates()
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
            source = _templates_module().get_template_source("basic_pr")
        except TypeError as exc:
            source_error = exc

        if source_error is not None:
            assert "module, class, method, function" in str(source_error)
            return

        assert source is not None
        assert "@do" in source
        assert "def basic_pr" in source
        assert "CreateWorkspace" in source

    def test_get_template_source_invalid(self):
        """Test get_template_source raises KeyError for invalid templates."""
        with pytest.raises(KeyError):
            _templates_module().get_template_source("nonexistent")

    def test_templates_registry_structure(self):
        """Test TEMPLATES registry has correct structure."""
        assert isinstance(_templates_module().TEMPLATES, dict)
        for _name, (func, desc) in _templates_module().TEMPLATES.items():
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
    def mock_workspace(self) -> Workspace:
        """Create a mock workspace."""
        return Workspace(
            id="workspace-test",
            repo="default",
            ref="feature/issue-001",
            base_ref="main",
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
        mock_workspace: Workspace,
        mock_pr: PRHandle,
        mock_issue: Issue,
    ) -> HandlerMap:
        """Create mock handlers for all effects."""

        def handle_create_workspace(effect: CreateWorkspace) -> Workspace:
            return mock_workspace

        def handle_merge_workspaces(effect: MergeWorkspaces) -> MergeWorkspacesResult:
            return MergeWorkspacesResult(status=MergeStatus.MERGED, workspace=mock_workspace)

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            return _valid_agent_artifact(effect, summary="All tests passed successfully")

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
            CreateWorkspace: handle_create_workspace,
            MergeWorkspaces: handle_merge_workspaces,
            AgentEffect: handle_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }


class TestBasicPRTemplate(MockHandlerFixtures):
    """Tests for basic_pr template."""

    def test_template_structure(self):
        """Verify basic_pr is a valid @do workflow."""
        assert callable(_templates_module().basic_pr)
        # Should have __wrapped__ from @do decorator
        assert hasattr(_templates_module().basic_pr, "__wrapped__") or callable(
            _templates_module().basic_pr
        )

    def test_template_returns_program(self, mock_issue: Issue):
        """Test that basic_pr returns a Program."""
        program = _templates_module().basic_pr(mock_issue)
        assert program is not None

    def test_template_with_mock_handlers(
        self,
        mock_issue: Issue,
        mock_handlers: dict,
        mock_pr: PRHandle,
    ):
        """Run basic_pr template with mocked effects."""
        result = _run_with_effect_handlers(_templates_module().basic_pr(mock_issue), mock_handlers)

        assert result.is_ok()
        pr = result.value
        assert pr.url == mock_pr.url
        assert pr.number == mock_pr.number

    def test_basic_pr_effects_sequence(self, mock_issue: Issue):
        """Verify basic_pr executes expected high-level step order."""
        calls: list[str] = []

        def handle_create_workspace(effect: CreateWorkspace) -> Workspace:
            calls.append("create_workspace")
            return Workspace(
                id="workspace-test",
                repo="default",
                ref="feature/issue-001",
                base_ref="main",
                issue_id=mock_issue.id,
                created_at=datetime.now(timezone.utc),
            )

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            calls.append("agent")
            return _valid_agent_artifact(effect)

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
            CreateWorkspace: handle_create_workspace,
            AgentEffect: handle_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }
        result = _run_with_effect_handlers(_templates_module().basic_pr(mock_issue), handlers)
        assert result.is_ok()
        assert calls == [
            "create_workspace",
            "agent",
            "commit",
            "push",
            "create_pr",
            "resolve_issue",
        ]


class TestEnforcedPRTemplate(MockHandlerFixtures):
    """Tests for enforced_pr template."""

    def test_template_structure(self):
        """Verify enforced_pr is a valid @do workflow."""
        assert callable(_templates_module().enforced_pr)

    def test_template_returns_program(self, mock_issue: Issue):
        """Test that enforced_pr returns a Program."""
        program = _templates_module().enforced_pr(mock_issue)
        assert program is not None

    def test_template_with_mock_handlers_passing_tests(
        self,
        mock_issue: Issue,
        mock_handlers: dict,
    ):
        """Run enforced_pr with tests that pass on first try."""
        result = _run_with_effect_handlers(
            _templates_module().enforced_pr(mock_issue), mock_handlers
        )
        assert result.is_ok()

    def test_template_with_failing_tests(
        self,
        mock_issue: Issue,
        mock_workspace: Workspace,
        mock_pr: PRHandle,
    ):
        """Test enforced_pr handles test failures correctly."""
        test_call_count = [0]

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            if effect.task.node_id == "test":
                test_call_count[0] += 1
                return _valid_agent_artifact(
                    effect,
                    passed=test_call_count[0] > 1,
                    summary="test run",
                )
            return _valid_agent_artifact(effect, summary="Implementation complete")

        def handle_create_workspace(e):
            return mock_workspace

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
            CreateWorkspace: handle_create_workspace,
            AgentEffect: handle_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(_templates_module().enforced_pr(mock_issue), handlers)
        assert result.is_ok()

    def test_template_fails_after_max_retries(
        self,
        mock_issue: Issue,
        mock_workspace: Workspace,
    ):
        """Test enforced_pr raises error after max retries."""

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            if effect.task.node_id == "test":
                return _valid_agent_artifact(effect, passed=False, summary="tests failed")
            return _valid_agent_artifact(effect, summary="Implementation complete")

        def handle_create_workspace(e):
            return mock_workspace

        handlers = {
            CreateWorkspace: handle_create_workspace,
            AgentEffect: handle_agent,
        }

        result = _run_with_effect_handlers(
            _templates_module().enforced_pr(mock_issue, max_retries=2), handlers
        )
        # Should fail with RuntimeError after max retries
        assert result.is_err()

    def test_custom_max_retries(self, mock_issue: Issue):
        """Test enforced_pr respects max_retries parameter."""
        program = _templates_module().enforced_pr(mock_issue, max_retries=5)
        assert program is not None

    def test_custom_test_command(self, mock_issue: Issue):
        """Test enforced_pr respects test_command parameter."""
        program = _templates_module().enforced_pr(mock_issue, test_command="npm test")
        assert program is not None


class TestReviewedPRTemplate(MockHandlerFixtures):
    """Tests for reviewed_pr template."""

    def test_template_structure(self):
        """Verify reviewed_pr is a valid @do workflow."""
        assert callable(_templates_module().reviewed_pr)

    def test_template_returns_program(self, mock_issue: Issue):
        """Test that reviewed_pr returns a Program."""
        program = _templates_module().reviewed_pr(mock_issue)
        assert program is not None

    def test_template_with_mock_handlers_approved(
        self,
        mock_issue: Issue,
        mock_workspace: Workspace,
        mock_pr: PRHandle,
    ):
        """Run reviewed_pr with review that approves on first try."""

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            if effect.task.node_id == "review":
                return _valid_agent_artifact(
                    effect,
                    verdict="PASS",
                    summary="Code looks good, well tested",
                )
            return _valid_agent_artifact(effect, summary="Implementation complete")

        def handle_create_workspace(e):
            return mock_workspace

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
            CreateWorkspace: handle_create_workspace,
            AgentEffect: handle_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(_templates_module().reviewed_pr(mock_issue), handlers)
        assert result.is_ok()

    def test_template_with_review_feedback(
        self,
        mock_issue: Issue,
        mock_workspace: Workspace,
        mock_pr: PRHandle,
    ):
        """Test reviewed_pr handles review feedback correctly."""
        review_count = [0]

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            if effect.task.node_id == "review":
                review_count[0] += 1
                if review_count[0] == 1:
                    return _valid_agent_artifact(
                        effect,
                        verdict="CHANGES_REQUESTED",
                        summary="Issues found",
                        findings=["Missing error handling"],
                    )
                return _valid_agent_artifact(
                    effect,
                    verdict="PASS",
                    summary="Issues addressed",
                )
            return _valid_agent_artifact(effect, summary="Implementation complete")

        def handle_create_workspace(e):
            return mock_workspace

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
            CreateWorkspace: handle_create_workspace,
            AgentEffect: handle_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(_templates_module().reviewed_pr(mock_issue), handlers)
        assert result.is_ok()

    def test_custom_max_reviews(self, mock_issue: Issue):
        """Test reviewed_pr respects max_reviews parameter."""
        program = _templates_module().reviewed_pr(mock_issue, max_reviews=5)
        assert program is not None


class TestMultiAgentTemplate(MockHandlerFixtures):
    """Tests for multi_agent template."""

    def test_template_structure(self):
        """Verify multi_agent is a valid @do workflow."""
        assert callable(_templates_module().multi_agent)

    def test_template_returns_program(self, mock_issue: Issue):
        """Test that multi_agent returns a Program."""
        program = _templates_module().multi_agent(mock_issue)
        assert program is not None

    def test_multi_agent_effects_include_spawn_and_gather(self, mock_issue: Issue):
        """Verify multi_agent executes both parallel branches and merge path."""
        calls: list[str] = []

        def handle_create_workspace(effect: CreateWorkspace) -> Workspace:
            calls.append(f"create_workspace:{effect.suffix}")
            return Workspace(
                id=f"workspace-{effect.suffix}",
                repo="default",
                ref=f"feature/{effect.suffix}",
                base_ref="main",
                issue_id=mock_issue.id,
                created_at=datetime.now(timezone.utc),
            )

        def handle_merge_workspaces(effect: MergeWorkspaces) -> MergeWorkspacesResult:
            calls.append("merge_workspaces")
            workspace = Workspace(
                id="workspace-merged",
                repo="default",
                ref="feature/merged",
                base_ref="main",
                issue_id=mock_issue.id,
                created_at=datetime.now(timezone.utc),
            )
            return MergeWorkspacesResult(status=MergeStatus.MERGED, workspace=workspace)

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            calls.append(f"agent:{effect.task.node_id}")
            return _valid_agent_artifact(effect)

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
            CreateWorkspace: handle_create_workspace,
            MergeWorkspaces: handle_merge_workspaces,
            AgentEffect: handle_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(_templates_module().multi_agent(mock_issue), handlers)
        assert result.is_ok()
        assert calls.count("create_pr") == 1
        assert calls.count("merge_workspaces") == 1
        assert calls.count("resolve_issue") == 1

    def test_template_with_mock_handlers(
        self,
        mock_issue: Issue,
        mock_workspace: Workspace,
        mock_pr: PRHandle,
    ):
        """Run multi_agent template with mocked effects.

        Gather/Spawn are handled by the local test harness.
        """

        def handle_create_workspace(e):
            return mock_workspace

        def handle_merge_workspaces(e):
            return MergeWorkspacesResult(status=MergeStatus.MERGED, workspace=mock_workspace)

        def handle_agent(e):
            return _valid_agent_artifact(e, summary="Done")

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
            CreateWorkspace: handle_create_workspace,
            MergeWorkspaces: handle_merge_workspaces,
            AgentEffect: handle_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
            ResolveIssue: handle_resolve_issue,
        }

        result = _run_with_effect_handlers(_templates_module().multi_agent(mock_issue), handlers)
        assert result.is_ok()


class TestTemplateDocumentation:
    """Tests for template documentation."""

    def test_basic_pr_docstring(self):
        """Verify basic_pr has a docstring."""
        assert _templates_module().basic_pr.__doc__ is not None
        assert "issue" in _templates_module().basic_pr.__doc__.lower()
        assert "agent" in _templates_module().basic_pr.__doc__.lower()
        assert "pr" in _templates_module().basic_pr.__doc__.lower()

    def test_enforced_pr_docstring(self):
        """Verify enforced_pr has a docstring."""
        assert _templates_module().enforced_pr.__doc__ is not None
        assert "test" in _templates_module().enforced_pr.__doc__.lower()
        assert (
            "retry" in _templates_module().enforced_pr.__doc__.lower()
            or "retries" in _templates_module().enforced_pr.__doc__.lower()
        )

    def test_reviewed_pr_docstring(self):
        """Verify reviewed_pr has a docstring."""
        assert _templates_module().reviewed_pr.__doc__ is not None
        assert "review" in _templates_module().reviewed_pr.__doc__.lower()

    def test_multi_agent_docstring(self):
        """Verify multi_agent has a docstring."""
        assert _templates_module().multi_agent.__doc__ is not None
        assert (
            "parallel" in _templates_module().multi_agent.__doc__.lower()
            or "multi" in _templates_module().multi_agent.__doc__.lower()
        )


class TestTemplateErrorHandling(MockHandlerFixtures):
    """Tests for template error handling."""

    def test_basic_pr_propagates_workspace_error(
        self,
        mock_issue: Issue,
    ):
        """Test that basic_pr propagates workspace creation errors."""
        from doeff_conductor.exceptions import WorkspaceError

        def handle_create_workspace(e):
            raise WorkspaceError(operation="create", message="Failed to create workspace")

        handlers = {
            CreateWorkspace: handle_create_workspace,
        }

        result = _run_with_effect_handlers(_templates_module().basic_pr(mock_issue), handlers)
        assert result.is_err()

    def test_basic_pr_propagates_agent_error(
        self,
        mock_issue: Issue,
        mock_workspace: Workspace,
    ):
        """Test that basic_pr propagates agent errors."""
        from doeff_conductor.exceptions import AgentError

        def handle_create_workspace(e):
            return mock_workspace

        def handle_agent(e):
            raise AgentError(operation="run", message="Agent crashed")

        handlers = {
            CreateWorkspace: handle_create_workspace,
            AgentEffect: handle_agent,
        }

        result = _run_with_effect_handlers(_templates_module().basic_pr(mock_issue), handlers)
        assert result.is_err()

    def test_basic_pr_propagates_pr_error(
        self,
        mock_issue: Issue,
        mock_workspace: Workspace,
    ):
        """Test that basic_pr propagates PR creation errors."""
        from doeff_conductor.exceptions import PRError

        def handle_create_workspace(e):
            return mock_workspace

        def handle_agent(e):
            return _valid_agent_artifact(e, summary="Done")

        def handle_commit(e):
            return "abc123"

        def handle_push(e):
            pass

        def handle_create_pr(e):
            raise PRError(operation="create", message="PR creation failed")

        handlers = {
            CreateWorkspace: handle_create_workspace,
            AgentEffect: handle_agent,
            Commit: handle_commit,
            Push: handle_push,
            CreatePR: handle_create_pr,
        }

        result = _run_with_effect_handlers(_templates_module().basic_pr(mock_issue), handlers)
        assert result.is_err()
