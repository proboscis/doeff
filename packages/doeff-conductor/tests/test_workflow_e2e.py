"""Workflow tests for doeff-conductor using shared mock handlers."""


from pathlib import Path
from typing import Any

from doeff_conductor import (
    Commit,
    CreateIssue,
    CreateWorkspace,
    DeleteWorkspace,
    Exec,
    GetIssue,
    IssueStatus,
    MergeWorkspaces,
    Push,
    ResolveIssue,
)
from doeff_conductor.handlers import mock_handlers, run_sync
from doeff_conductor.handlers.testing import MockConductorRuntime

from doeff import do


def _run_with_mock_handlers(program: Any, runtime: MockConductorRuntime):
    return run_sync(program, scheduled_handlers=mock_handlers(runtime=runtime))


class TestWorkflowE2E:
    """Workflow tests that previously required git/OpenCode now use shared mock handlers."""

    def test_issue_lifecycle_workflow(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def issue_lifecycle():
            issue = yield CreateIssue(
                title="Test Feature",
                body="Implement a test feature",
                labels=("feature", "test"),
            )

            retrieved = yield GetIssue(id=issue.id)
            assert retrieved.title == "Test Feature"
            assert retrieved.status == IssueStatus.OPEN

            resolved = yield ResolveIssue(
                issue=retrieved,
                pr_url="https://github.com/test/repo/pull/1",
            )
            assert resolved.status == IssueStatus.RESOLVED

            return resolved

        result = _run_with_mock_handlers(issue_lifecycle(), runtime)

        assert result.is_ok
        resolved_issue = result.value
        assert resolved_issue.status == IssueStatus.RESOLVED
        assert resolved_issue.pr_url == "https://github.com/test/repo/pull/1"
        assert len(list(runtime.issues_dir.glob("*.md"))) == 1

    def test_workspace_create_and_delete(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def workspace_workflow():
            workspace = yield CreateWorkspace(workspace_id="ws-test")
            workspace_path = runtime.resolve_path(workspace)
            assert workspace_path.exists()
            assert (workspace_path / ".git").exists()

            deleted = yield DeleteWorkspace(workspace=workspace, force=True)
            assert deleted

            return workspace.id

        result = _run_with_mock_handlers(workspace_workflow(), runtime)

        assert result.is_ok
        assert result.value == "ws-test"

    def test_workspace_with_commit(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def commit_workflow():
            workspace = yield CreateWorkspace(workspace_id="ws-feature")
            (runtime.resolve_path(workspace) / "feature.py").write_text("# New feature\n")

            sha = yield Commit(workspace=workspace, message="feat: add new feature")
            assert len(sha) == 40

            yield DeleteWorkspace(workspace=workspace, force=True)
            return sha

        result = _run_with_mock_handlers(commit_workflow(), runtime)

        assert result.is_ok
        assert len(result.value) == 40

    def test_full_issue_to_commit_workflow(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def full_workflow():
            issue = yield CreateIssue(
                title="Add greeting module",
                body="Create a hello.py that prints Hello World",
                labels=("feature",),
            )

            workspace = yield CreateWorkspace(issue=issue, workspace_id="ws-impl")
            (runtime.resolve_path(workspace) / "hello.py").write_text('print("Hello World")\n')

            sha = yield Commit(workspace=workspace, message=f"feat: {issue.title}")

            resolved = yield ResolveIssue(
                issue=issue,
                pr_url="https://github.com/test/repo/pull/1",
                result=f"Implemented in commit {sha[:7]}",
            )

            yield DeleteWorkspace(workspace=workspace, force=True)

            return {
                "issue_id": issue.id,
                "commit_sha": sha,
                "resolved": resolved.status == IssueStatus.RESOLVED,
            }

        result = _run_with_mock_handlers(full_workflow(), runtime)

        assert result.is_ok
        workflow_result = result.value
        assert workflow_result["issue_id"].startswith("ISSUE-")
        assert len(workflow_result["commit_sha"]) == 40
        assert workflow_result["resolved"] is True

    def test_three_task_merge_demo_runs_green_exec_gate(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def merge_workflow():
            workspace1 = yield CreateWorkspace(workspace_id="ws-feature1")
            (runtime.resolve_path(workspace1) / "feature1.py").write_text("# Feature 1\n")
            yield Commit(workspace=workspace1, message="feat: add feature1")

            workspace2 = yield CreateWorkspace(workspace_id="ws-feature2")
            (runtime.resolve_path(workspace2) / "feature2.py").write_text("# Feature 2\n")
            yield Commit(workspace=workspace2, message="feat: add feature2")

            workspace3 = yield CreateWorkspace(workspace_id="ws-feature3")
            (runtime.resolve_path(workspace3) / "feature3.py").write_text("# Feature 3\n")
            yield Commit(workspace=workspace3, message="feat: add feature3")

            merge_result = yield MergeWorkspaces(
                workspace_id="ws-merged",
                workspaces=(workspace1, workspace2, workspace3),
            )
            assert merge_result.workspace is not None
            merged = merge_result.workspace
            merged_path = runtime.resolve_path(merged)

            assert (merged_path / "feature1.py").exists()
            assert (merged_path / "feature2.py").exists()
            assert (merged_path / "feature3.py").exists()

            gate = yield Exec(
                cmd="test -f feature1.py && test -f feature2.py && test -f feature3.py",
                workspace=merged,
            )
            assert gate.passed
            assert Path(gate.log_path).exists()

            yield DeleteWorkspace(workspace=workspace1, force=True)
            yield DeleteWorkspace(workspace=workspace2, force=True)
            yield DeleteWorkspace(workspace=workspace3, force=True)
            yield DeleteWorkspace(workspace=merged, force=True)

            return merged.ref

        result = _run_with_mock_handlers(merge_workflow(), runtime)

        assert result.is_ok
        assert result.value == "conductor/ws-merged"

    def test_push_to_remote_workflow(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def push_workflow():
            workspace = yield CreateWorkspace(workspace_id="ws-push-test")
            (runtime.resolve_path(workspace) / "pushed.py").write_text("# Pushed\n")
            yield Commit(workspace=workspace, message="feat: push test")
            yield Push(workspace=workspace, set_upstream=True)
            yield DeleteWorkspace(workspace=workspace, force=True)
            return workspace.ref

        result = _run_with_mock_handlers(push_workflow(), runtime)

        assert result.is_ok
        assert result.value in runtime.pushed_branches


class TestTemplateE2E:
    """Template loading tests."""

    def test_template_imports(self):
        from doeff_conductor.templates import (
            get_available_templates,
            get_template,
            is_template,
        )

        templates = get_available_templates()
        assert "basic_pr" in templates
        assert "enforced_pr" in templates
        assert "reviewed_pr" in templates
        assert "multi_agent" in templates

        assert is_template("basic_pr")
        assert not is_template("nonexistent")

        func = get_template("basic_pr")
        assert callable(func)

    def test_basic_pr_template_structure(self):
        from doeff import ProgramBase
        from doeff_conductor.templates import basic_pr
        from doeff_conductor.types import Issue

        issue = Issue(
            id="TEST-001",
            title="Test Feature",
            body="Implement test feature",
            status=IssueStatus.OPEN,
        )

        program = basic_pr(issue)

        assert program is not None
        assert isinstance(program, ProgramBase)
