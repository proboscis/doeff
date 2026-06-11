"""Tests for the workspace handler's identity-bound, resume-stable contract."""

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from doeff_conductor.effects.git import Commit
from doeff_conductor.effects.workspace import CreateWorkspace, DeleteWorkspace, MergeWorkspaces
from doeff_conductor.handlers.git_handler import GitHandler
from doeff_conductor.handlers.workspace_handler import WorkspaceHandler, WorkspaceStateError
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


def _branches(repo_path: Path) -> set[str]:
    result = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def _git_exclude_path(worktree_path: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
        text=True,
    )
    exclude_path = Path(result.stdout.strip())
    if exclude_path.is_absolute():
        return exclude_path
    return worktree_path / exclude_path


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
    ) -> WorkspaceHandler:
        return WorkspaceHandler(repo_path=git_repo, workspace_base=workspace_base)

    def test_create_workspace_basic(self, handler: WorkspaceHandler) -> None:
        workspace = handler.handle_create_workspace(CreateWorkspace(workspace_id="ws-basic"))

        assert isinstance(workspace, Workspace)
        assert workspace.id == "ws-basic"
        assert workspace.repo == "default"
        assert workspace.ref == "conductor/ws-basic"
        assert workspace.base_ref in ("master", "main")
        assert "path" not in workspace.to_dict()
        assert handler.resolve_path(workspace).exists()

    def test_create_workspace_installs_runtime_state_exclude_idempotently(
        self,
        handler: WorkspaceHandler,
    ) -> None:
        workspace: Workspace = handler.handle_create_workspace(
            CreateWorkspace(workspace_id="ws-ignore")
        )
        materialized_path: Path = handler.resolve_path(workspace)

        status_before_runtime_state: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=materialized_path,
            check=True,
            capture_output=True,
            text=True,
        )
        assert status_before_runtime_state.stdout == ""
        assert not (materialized_path / ".gitignore").exists()

        exclude_path: Path = _git_exclude_path(materialized_path)
        exclude_lines: list[str] = exclude_path.read_text().splitlines()
        assert exclude_lines.count(".agent-home/") == 1

        same_workspace: Workspace = handler.handle_create_workspace(
            CreateWorkspace(workspace_id="ws-ignore")
        )
        assert handler.resolve_path(same_workspace) == materialized_path
        exclude_lines_after_second_init: list[str] = exclude_path.read_text().splitlines()
        assert exclude_lines_after_second_init.count(".agent-home/") == 1

        agent_state_path: Path = materialized_path / ".agent-home" / "session.json"
        agent_state_path.parent.mkdir()
        agent_state_path.write_text('{"session": "runtime"}')

        status: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=materialized_path,
            check=True,
            capture_output=True,
            text=True,
        )
        assert ".agent-home" not in status.stdout

    def test_workspace_auto_commit_excludes_runtime_agent_home(
        self,
        handler: WorkspaceHandler,
    ) -> None:
        workspace: Workspace = handler.handle_create_workspace(
            CreateWorkspace(workspace_id="ws-autocommit")
        )
        materialized_path: Path = handler.resolve_path(workspace)
        git_handler: GitHandler = GitHandler(workspace_resolver=handler.resolve_path)

        (materialized_path / "feature.txt").write_text("user-visible work\n")
        agent_state_path: Path = materialized_path / ".agent-home" / "session.json"
        agent_state_path.parent.mkdir()
        agent_state_path.write_text('{"session": "runtime"}')

        commit_sha: str = git_handler.handle_commit(
            Commit(
                workspace=workspace,
                message="Commit workspace changes",
                all=True,
            )
        )

        show_result: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "show", "--name-only", "--format=", commit_sha],
            cwd=materialized_path,
            check=True,
            capture_output=True,
            text=True,
        )
        committed_paths: list[str] = show_result.stdout.splitlines()
        assert "feature.txt" in committed_paths
        assert ".gitignore" not in committed_paths
        assert ".agent-home/session.json" not in committed_paths

    def test_create_workspace_requires_identity(self, handler: WorkspaceHandler) -> None:
        with pytest.raises(ValueError, match="non-empty workspace_id"):
            handler.handle_create_workspace(CreateWorkspace(workspace_id=""))

    def test_create_workspace_with_issue(self, handler: WorkspaceHandler) -> None:
        issue = Issue(
            id="ISSUE-123",
            title="Test Issue",
            body="Test body",
            status=IssueStatus.OPEN,
            labels=(),
            created_at=datetime.now(timezone.utc),
        )

        workspace = handler.handle_create_workspace(
            CreateWorkspace(issue=issue, workspace_id="issue-123-impl")
        )

        assert workspace.ref == "conductor/issue-123-impl"
        assert workspace.issue_id == "ISSUE-123"

    def test_delete_workspace(self, handler: WorkspaceHandler) -> None:
        workspace = handler.handle_create_workspace(CreateWorkspace(workspace_id="ws-delete"))
        materialized_path = handler.resolve_path(workspace)
        assert materialized_path.exists()

        result = handler.handle_delete_workspace(DeleteWorkspace(workspace=workspace))

        assert result is True
        assert not materialized_path.exists()

    def test_delete_workspace_preserves_uncommitted_user_changes(
        self,
        handler: WorkspaceHandler,
    ) -> None:
        workspace: Workspace = handler.handle_create_workspace(
            CreateWorkspace(workspace_id="ws-delete-dirty")
        )
        materialized_path: Path = handler.resolve_path(workspace)
        (materialized_path / "wip.txt").write_text("keep me\n")

        result: bool = handler.handle_delete_workspace(DeleteWorkspace(workspace=workspace))

        assert result is False
        assert materialized_path.exists()

    def test_delete_workspace_nonexistent(self, handler: WorkspaceHandler) -> None:
        workspace = Workspace(id="missing", repo="default", ref="missing", base_ref="main")

        result = handler.handle_delete_workspace(DeleteWorkspace(workspace=workspace))

        assert result is False

    def test_merge_workspaces_two_branches(self, handler: WorkspaceHandler) -> None:
        workspace1 = handler.handle_create_workspace(CreateWorkspace(workspace_id="ws-feature1"))
        _commit_file(handler.resolve_path(workspace1), "feature1.txt", "Feature 1", "feature1")

        workspace2 = handler.handle_create_workspace(CreateWorkspace(workspace_id="ws-feature2"))
        _commit_file(handler.resolve_path(workspace2), "feature2.txt", "Feature 2", "feature2")

        result = handler.handle_merge_workspaces(
            MergeWorkspaces(workspace_id="ws-merged", workspaces=(workspace1, workspace2)),
        )

        assert result.status is MergeStatus.MERGED
        assert result.workspace is not None
        merged_path = handler.resolve_path(result.workspace)
        assert result.workspace.ref == "conductor/ws-merged"
        assert (merged_path / "feature1.txt").exists()
        assert (merged_path / "feature2.txt").exists()
        assert result.log_path is not None
        assert Path(result.log_path).exists()

    def test_merge_workspaces_empty_raises(self, handler: WorkspaceHandler) -> None:
        effect = MergeWorkspaces(workspace_id="ws-merged", workspaces=())

        with pytest.raises(ValueError, match="No workspaces to merge"):
            handler.handle_merge_workspaces(effect)

    def test_merge_workspaces_with_strategy_squash(self, handler: WorkspaceHandler) -> None:
        workspace1 = handler.handle_create_workspace(CreateWorkspace(workspace_id="ws-base"))
        _commit_file(handler.resolve_path(workspace1), "base.txt", "Base", "base")

        workspace2 = handler.handle_create_workspace(CreateWorkspace(workspace_id="ws-squash"))
        _commit_file(handler.resolve_path(workspace2), "squash.txt", "Squash", "squash")

        result = handler.handle_merge_workspaces(
            MergeWorkspaces(
                workspace_id="ws-squash-merged",
                workspaces=(workspace1, workspace2),
                strategy=MergeStrategy.SQUASH,
            ),
        )

        assert result.merged
        assert result.workspace is not None
        assert (handler.resolve_path(result.workspace) / "squash.txt").exists()

    def test_merge_conflict_returns_structured_result(self, handler: WorkspaceHandler) -> None:
        workspace1 = handler.handle_create_workspace(CreateWorkspace(workspace_id="ws-left"))
        _commit_file(handler.resolve_path(workspace1), "shared.txt", "left\n", "left")

        workspace2 = handler.handle_create_workspace(CreateWorkspace(workspace_id="ws-right"))
        _commit_file(handler.resolve_path(workspace2), "shared.txt", "right\n", "right")

        result = handler.handle_merge_workspaces(
            MergeWorkspaces(workspace_id="ws-conflict", workspaces=(workspace1, workspace2)),
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
    ) -> None:
        app_repo = tmp_path / "app"
        docs_repo = tmp_path / "docs"
        _init_repo(app_repo)
        _init_repo(docs_repo)
        handler = WorkspaceHandler(
            repo_paths={"app": app_repo, "docs": docs_repo},
            workspace_base=workspace_base,
        )

        app_workspace = handler.handle_create_workspace(
            CreateWorkspace(repo="app", workspace_id="ws-task")
        )
        docs_workspace = handler.handle_create_workspace(
            CreateWorkspace(repo="docs", workspace_id="ws-task")
        )

        assert app_workspace.repo == "app"
        assert docs_workspace.repo == "docs"
        assert handler.resolve_path(app_workspace).exists()
        assert handler.resolve_path(docs_workspace).exists()


class TestWorkspaceResumeStability:
    """Same identity ⇒ same branch + same worktree, across process restarts."""

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

    def _handler(self, git_repo: Path, workspace_base: Path) -> WorkspaceHandler:
        """A fresh handler instance simulates a conductor process restart."""
        return WorkspaceHandler(repo_path=git_repo, workspace_base=workspace_base)

    def test_same_identity_twice_binds_same_branch_and_path(
        self, git_repo: Path, workspace_base: Path
    ) -> None:
        first_handler = self._handler(git_repo, workspace_base)
        first = first_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-ws"))
        first_path = first_handler.resolve_path(first)

        second_handler = self._handler(git_repo, workspace_base)
        second = second_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-ws"))
        second_path = second_handler.resolve_path(second)

        assert second.ref == first.ref == "conductor/run-ws"
        assert second_path == first_path
        # Exactly one branch was created for the identity.
        assert sum(1 for branch in _branches(git_repo) if branch == "conductor/run-ws") == 1

    def test_readoption_preserves_uncommitted_changes(
        self, git_repo: Path, workspace_base: Path
    ) -> None:
        first_handler = self._handler(git_repo, workspace_base)
        workspace = first_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-ws"))
        worktree = first_handler.resolve_path(workspace)
        (worktree / "wip.txt").write_text("uncommitted work\n")

        resumed_handler = self._handler(git_repo, workspace_base)
        readopted = resumed_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-ws"))

        readopted_path = resumed_handler.resolve_path(readopted)
        assert readopted_path == worktree
        assert readopted.ref == workspace.ref
        assert (readopted_path / "wip.txt").read_text() == "uncommitted work\n"

    def test_missing_worktree_rematerializes_from_branch_not_base(
        self, git_repo: Path, workspace_base: Path
    ) -> None:
        first_handler = self._handler(git_repo, workspace_base)
        workspace = first_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-ws"))
        worktree = first_handler.resolve_path(workspace)
        _commit_file(worktree, "work.txt", "committed work\n", "implement")

        # The site loses the worktree (e.g. cleanup) but the branch survives.
        shutil.rmtree(worktree)

        resumed_handler = self._handler(git_repo, workspace_base)
        resumed = resumed_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-ws"))

        resumed_path = resumed_handler.resolve_path(resumed)
        assert resumed_path == worktree
        assert resumed.ref == workspace.ref
        # Re-materialized FROM THE BRANCH: the committed work is present.
        # (Creation from the base ref would have produced a bare README tree.)
        assert (resumed_path / "work.txt").read_text() == "committed work\n"

    def test_worktree_without_branch_fails_loudly(
        self, git_repo: Path, workspace_base: Path
    ) -> None:
        handler = self._handler(git_repo, workspace_base)
        rogue_path = workspace_base / "default" / "run-ws"
        rogue_path.mkdir(parents=True)

        with pytest.raises(WorkspaceStateError, match="branch conductor/run-ws does not"):
            handler.handle_create_workspace(CreateWorkspace(workspace_id="run-ws"))

    def test_merge_identity_is_resume_stable(
        self, git_repo: Path, workspace_base: Path
    ) -> None:
        first_handler = self._handler(git_repo, workspace_base)
        left = first_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-left"))
        _commit_file(first_handler.resolve_path(left), "left.txt", "left\n", "left")
        right = first_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-right"))
        _commit_file(first_handler.resolve_path(right), "right.txt", "right\n", "right")

        first_result = first_handler.handle_merge_workspaces(
            MergeWorkspaces(workspace_id="run-merged", workspaces=(left, right))
        )
        assert first_result.status is MergeStatus.MERGED
        assert first_result.workspace is not None
        merged_path = first_handler.resolve_path(first_result.workspace)

        # The site loses the merged worktree; the merge branch survives.
        shutil.rmtree(merged_path)

        resumed_handler = self._handler(git_repo, workspace_base)
        resumed_left = resumed_handler.handle_create_workspace(
            CreateWorkspace(workspace_id="run-left")
        )
        resumed_right = resumed_handler.handle_create_workspace(
            CreateWorkspace(workspace_id="run-right")
        )
        second_result = resumed_handler.handle_merge_workspaces(
            MergeWorkspaces(workspace_id="run-merged", workspaces=(resumed_left, resumed_right))
        )

        assert second_result.status is MergeStatus.MERGED
        assert second_result.workspace is not None
        assert second_result.workspace.ref == first_result.workspace.ref
        resumed_merged_path = resumed_handler.resolve_path(second_result.workspace)
        assert resumed_merged_path == merged_path
        # Re-materialized from the merge branch with both merges intact;
        # re-applying the merges is a no-op.
        assert (resumed_merged_path / "left.txt").exists()
        assert (resumed_merged_path / "right.txt").exists()

    def test_merge_rerun_picks_up_new_commits_on_every_source(
        self, git_repo: Path, workspace_base: Path
    ) -> None:
        """Review finding F2: re-merging must re-apply ALL sources.

        The merge branch is created from source[0]'s tip on the first run;
        on resume it is re-adopted frozen at that old tip. A merge loop
        that skips source[0] silently drops its newer commits, so the
        resumed merged tree diverges from what a fresh run would produce.
        """
        first_handler = self._handler(git_repo, workspace_base)
        left = first_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-left"))
        _commit_file(first_handler.resolve_path(left), "left.txt", "left\n", "left")
        right = first_handler.handle_create_workspace(CreateWorkspace(workspace_id="run-right"))
        _commit_file(first_handler.resolve_path(right), "right.txt", "right\n", "right")

        first_result = first_handler.handle_merge_workspaces(
            MergeWorkspaces(workspace_id="run-merged", workspaces=(left, right))
        )
        assert first_result.status is MergeStatus.MERGED

        # AFTER the first merge, both sources gain new commits (e.g. a
        # resumed agent node re-ran on a journal cache miss).
        _commit_file(first_handler.resolve_path(left), "left2.txt", "left2\n", "left2")
        _commit_file(first_handler.resolve_path(right), "right2.txt", "right2\n", "right2")

        resumed_handler = self._handler(git_repo, workspace_base)
        resumed_left = resumed_handler.handle_create_workspace(
            CreateWorkspace(workspace_id="run-left")
        )
        resumed_right = resumed_handler.handle_create_workspace(
            CreateWorkspace(workspace_id="run-right")
        )
        second_result = resumed_handler.handle_merge_workspaces(
            MergeWorkspaces(workspace_id="run-merged", workspaces=(resumed_left, resumed_right))
        )

        assert second_result.status is MergeStatus.MERGED
        assert second_result.workspace is not None
        merged_path = resumed_handler.resolve_path(second_result.workspace)
        # BOTH sources' newer commits are in the resumed merged tree —
        # source[0] must not be frozen at its first-merge tip.
        assert (merged_path / "left2.txt").exists()
        assert (merged_path / "right2.txt").exists()
