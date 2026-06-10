"""Tests for workspace handler."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from doeff_conductor.effects.workspace import CreateWorkspace, DeleteWorkspace, MergeWorkspaces
from doeff_conductor.handlers.workspace_handler import WorkspaceHandler
from doeff_conductor.types import Issue, IssueStatus, MergeStatus, MergeStrategy, Workspace


def _init_repo(repo_path: Path) -> None:
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    (repo_path / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )


def _commit_file(path: Path, filename: str, content: str, message: str) -> None:
    (path / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=path, check=True, capture_output=True)


class TestWorkspaceHandler:
    @pytest.fixture
    def git_repo(self, tmp_path: Path) -> Path:
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        return repo_path

    @pytest.fixture
    def workspace_base(self, tmp_path: Path) -> Path:
        base = tmp_path / "workspaces"
        base.mkdir()
        return base

    @pytest.fixture
    def handler(
        self,
        git_repo: Path,
        workspace_base: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> WorkspaceHandler:
        monkeypatch.setattr(
            "doeff_conductor.handlers.workspace_handler._get_workspace_base_dir",
            lambda: workspace_base,
        )
        return WorkspaceHandler(repo_path=git_repo)

    def test_create_workspace_basic(self, handler: WorkspaceHandler) -> None:
        workspace = handler.handle_create_workspace(CreateWorkspace())

        assert isinstance(workspace, Workspace)
        assert workspace.repo == "default"
        assert workspace.ref.startswith("conductor-")
        assert workspace.base_ref == "master" or workspace.base_ref == "main"
        assert "path" not in workspace.to_dict()
        assert handler.resolve_path(workspace).exists()

    def test_create_workspace_with_issue(self, handler: WorkspaceHandler) -> None:
        issue = Issue(
            id="ISSUE-123",
            title="Test Issue",
            body="Test body",
            status=IssueStatus.OPEN,
            labels=(),
            created_at=datetime.now(timezone.utc),
        )

        workspace = handler.handle_create_workspace(CreateWorkspace(issue=issue))

        assert "issue_123" in workspace.ref
        assert workspace.issue_id == "ISSUE-123"

    def test_create_workspace_with_suffix(self, handler: WorkspaceHandler) -> None:
        workspace = handler.handle_create_workspace(CreateWorkspace(suffix="impl"))

        assert "impl" in workspace.ref

    def test_delete_workspace(self, handler: WorkspaceHandler) -> None:
        workspace = handler.handle_create_workspace(CreateWorkspace())
        materialized_path = handler.resolve_path(workspace)
        assert materialized_path.exists()

        result = handler.handle_delete_workspace(DeleteWorkspace(workspace=workspace))

        assert result is True
        assert not materialized_path.exists()

    def test_delete_workspace_nonexistent(self, handler: WorkspaceHandler) -> None:
        workspace = Workspace(id="missing", repo="default", ref="missing", base_ref="main")

        result = handler.handle_delete_workspace(DeleteWorkspace(workspace=workspace))

        assert result is False

    def test_merge_workspaces_two_branches(self, handler: WorkspaceHandler) -> None:
        workspace1 = handler.handle_create_workspace(CreateWorkspace(suffix="feature1"))
        _commit_file(handler.resolve_path(workspace1), "feature1.txt", "Feature 1", "feature1")

        workspace2 = handler.handle_create_workspace(CreateWorkspace(suffix="feature2"))
        _commit_file(handler.resolve_path(workspace2), "feature2.txt", "Feature 2", "feature2")

        result = handler.handle_merge_workspaces(
            MergeWorkspaces(workspaces=(workspace1, workspace2)),
        )

        assert result.status is MergeStatus.MERGED
        assert result.workspace is not None
        merged_path = handler.resolve_path(result.workspace)
        assert result.workspace.ref.startswith("conductor-merged-")
        assert (merged_path / "feature1.txt").exists()
        assert (merged_path / "feature2.txt").exists()
        assert result.log_path is not None
        assert Path(result.log_path).exists()

    def test_merge_workspaces_empty_raises(self, handler: WorkspaceHandler) -> None:
        effect = MergeWorkspaces(workspaces=())

        with pytest.raises(ValueError, match="No workspaces to merge"):
            handler.handle_merge_workspaces(effect)

    def test_merge_workspaces_with_strategy_squash(self, handler: WorkspaceHandler) -> None:
        workspace1 = handler.handle_create_workspace(CreateWorkspace(suffix="base"))
        _commit_file(handler.resolve_path(workspace1), "base.txt", "Base", "base")

        workspace2 = handler.handle_create_workspace(CreateWorkspace(suffix="squash"))
        _commit_file(handler.resolve_path(workspace2), "squash.txt", "Squash", "squash")

        result = handler.handle_merge_workspaces(
            MergeWorkspaces(workspaces=(workspace1, workspace2), strategy=MergeStrategy.SQUASH),
        )

        assert result.merged
        assert result.workspace is not None
        assert (handler.resolve_path(result.workspace) / "squash.txt").exists()

    def test_merge_conflict_returns_structured_result(self, handler: WorkspaceHandler) -> None:
        workspace1 = handler.handle_create_workspace(CreateWorkspace(suffix="left"))
        _commit_file(handler.resolve_path(workspace1), "shared.txt", "left\n", "left")

        workspace2 = handler.handle_create_workspace(CreateWorkspace(suffix="right"))
        _commit_file(handler.resolve_path(workspace2), "shared.txt", "right\n", "right")

        result = handler.handle_merge_workspaces(
            MergeWorkspaces(workspaces=(workspace1, workspace2)),
        )

        assert result.status is MergeStatus.CONFLICT
        assert result.workspace is not None
        assert result.conflicts
        assert result.conflicts[0].workspace == workspace2
        assert result.conflicts[0].files == ("shared.txt",)
        assert result.log_path is not None
        assert Path(result.log_path).exists()

    def test_two_repo_workspace_creation(
        self,
        tmp_path: Path,
        workspace_base: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app_repo = tmp_path / "app"
        docs_repo = tmp_path / "docs"
        _init_repo(app_repo)
        _init_repo(docs_repo)
        monkeypatch.setattr(
            "doeff_conductor.handlers.workspace_handler._get_workspace_base_dir",
            lambda: workspace_base,
        )
        handler = WorkspaceHandler(repo_paths={"app": app_repo, "docs": docs_repo})

        app_workspace = handler.handle_create_workspace(CreateWorkspace(repo="app", suffix="task"))
        docs_workspace = handler.handle_create_workspace(CreateWorkspace(repo="docs", suffix="task"))

        assert app_workspace.repo == "app"
        assert docs_workspace.repo == "docs"
        assert handler.resolve_path(app_workspace).exists()
        assert handler.resolve_path(docs_workspace).exists()

