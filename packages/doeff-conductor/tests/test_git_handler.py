"""Tests for git handler."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from doeff_conductor import Commit, CreatePR, MergePR, Push
from doeff_conductor import GitCommandError
from doeff_conductor import git_handler
from doeff_conductor import MergeStrategy, PRHandle, Workspace
from doeff_git.effects import GitCommit
from doeff_git.handlers import production_handlers as git_production_handlers

from doeff import K, Resume, stacked_handlers
from doeff import handler as as_handler
from tests.effect_runner import Answer, answering, workspace_paths


class TestGitHandler:

    @pytest.fixture
    def git_repo(self, tmp_path: Path) -> Path:
        repo_path = tmp_path / "repo"
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

        (repo_path / "README.md").write_text("# Test Repo")
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )

        return repo_path

    @pytest.fixture
    def workspace(self, git_repo: Path) -> Workspace:
        return Workspace(
            id="test-workspace",
            repo="default",
            ref="main",
            base_ref="main",
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def handler(self, git_repo: Path, workspace: Workspace) -> Answer:
        return answering(
            stacked_handlers(
                git_production_handlers(),
                workspace_paths({workspace.id: git_repo}),
                git_handler(),
            )
        )

    def test_git_effects_are_performed_as_doeff_git_effects(
        self,
        workspace: Workspace,
        git_repo: Path,
    ):
        """The conductor git handler answers through whatever doeff-git handler is outside it."""
        performed: list[GitCommit] = []

        @as_handler
        def answer_git_commit(effect: GitCommit, k: K):
            performed.append(effect)
            return (yield Resume(k, "f" * 40))

        answer = answering(
            stacked_handlers(
                answer_git_commit,
                workspace_paths({workspace.id: git_repo}),
                git_handler(),
            )
        )
        (git_repo / "new_file.txt").write_text("new content")

        sha = answer(Commit(workspace=workspace, message="Add new file", all=True))

        assert sha == "f" * 40
        assert [(commit.work_dir, commit.message, commit.all) for commit in performed] == [
            (git_repo, "Add new file", True),
        ]

    def test_commit_with_changes(
        self,
        handler: Answer,
        workspace: Workspace,
        git_repo: Path,
    ):
        (git_repo / "new_file.txt").write_text("new content")

        effect = Commit(workspace=workspace, message="Add new file", all=True)
        sha = handler(effect)

        assert sha is not None
        assert len(sha) == 40

    def test_commit_without_all_flag(
        self,
        handler: Answer,
        workspace: Workspace,
        git_repo: Path,
    ):
        (git_repo / "staged.txt").write_text("staged content")
        subprocess.run(
            ["git", "add", "staged.txt"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        effect = Commit(workspace=workspace, message="Add staged file", all=False)
        sha = handler(effect)

        assert sha is not None
        assert len(sha) == 40

    def test_commit_empty_raises(self, handler: Answer, workspace: Workspace):
        effect = Commit(workspace=workspace, message="Empty commit", all=True)

        with pytest.raises(GitCommandError):
            handler(effect)

    def test_push_basic(
        self,
        handler: Answer,
        workspace: Workspace,
        git_repo: Path,
        tmp_path: Path,
    ):
        remote_path = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote_path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_path)],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        effect = Push(workspace=workspace, set_upstream=True)
        handler(effect)

        result = subprocess.run(
            ["git", "ls-remote", str(remote_path)],
            capture_output=True,
            text=True, check=False,
        )
        assert "refs/heads/main" in result.stdout

    def test_push_force(
        self,
        handler: Answer,
        workspace: Workspace,
        git_repo: Path,
        tmp_path: Path,
    ):
        remote_path = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote_path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_path)],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        effect = Push(workspace=workspace, force=True)
        handler(effect)

    def test_push_no_remote_raises(self, handler: Answer, workspace: Workspace):
        effect = Push(workspace=workspace)

        with pytest.raises(GitCommandError):
            handler(effect)

    @patch("doeff_git._commands._run_command")
    def test_create_pr_success(
        self,
        mock_run: MagicMock,
        handler: Answer,
        workspace: Workspace,
    ):
        mock_run.return_value = MagicMock(
            stdout="https://github.com/user/repo/pull/42\n",
            returncode=0,
        )

        effect = CreatePR(workspace=workspace, title="Test PR", body="PR body", target="main")
        pr = handler(effect)

        assert pr.url == "https://github.com/user/repo/pull/42"
        assert pr.number == 42
        assert pr.title == "Test PR"
        assert pr.branch == "main"
        assert pr.status == "open"

    @patch("doeff_git._commands._run_command")
    def test_create_pr_draft(
        self,
        mock_run: MagicMock,
        handler: Answer,
        workspace: Workspace,
    ):
        mock_run.return_value = MagicMock(
            stdout="https://github.com/user/repo/pull/43\n",
            returncode=0,
        )

        effect = CreatePR(workspace=workspace, title="Draft PR", draft=True, target="main")
        handler(effect)

        call_args = mock_run.call_args[0][0]
        assert "--draft" in call_args

    @patch("doeff_git._commands._run_command")
    def test_merge_pr_default_strategy(self, mock_run: MagicMock, handler: Answer):
        mock_run.return_value = MagicMock(returncode=0)
        pr = PRHandle(
            url="https://github.com/user/repo/pull/42",
            number=42,
            title="Test PR",
            branch="feature",
            target="main",
            status="open",
            created_at=datetime.now(timezone.utc),
        )

        effect = MergePR(pr=pr)
        handler(effect)

        call_args = mock_run.call_args[0][0]
        assert "--merge" in call_args

    @patch("doeff_git._commands._run_command")
    def test_merge_pr_squash_strategy(self, mock_run: MagicMock, handler: Answer):
        mock_run.return_value = MagicMock(returncode=0)
        pr = PRHandle(
            url="https://github.com/user/repo/pull/42",
            number=42,
            title="Test PR",
            branch="feature",
            target="main",
            status="open",
            created_at=datetime.now(timezone.utc),
        )

        effect = MergePR(pr=pr, strategy=MergeStrategy.SQUASH)
        handler(effect)

        call_args = mock_run.call_args[0][0]
        assert "--squash" in call_args

    @patch("doeff_git._commands._run_command")
    def test_merge_pr_rebase_strategy(self, mock_run: MagicMock, handler: Answer):
        mock_run.return_value = MagicMock(returncode=0)
        pr = PRHandle(
            url="https://github.com/user/repo/pull/42",
            number=42,
            title="Test PR",
            branch="feature",
            target="main",
            status="open",
            created_at=datetime.now(timezone.utc),
        )

        effect = MergePR(pr=pr, strategy=MergeStrategy.REBASE)
        handler(effect)

        call_args = mock_run.call_args[0][0]
        assert "--rebase" in call_args

    @patch("doeff_git._commands._run_command")
    def test_merge_pr_delete_branch(self, mock_run: MagicMock, handler: Answer):
        mock_run.return_value = MagicMock(returncode=0)
        pr = PRHandle(
            url="https://github.com/user/repo/pull/42",
            number=42,
            title="Test PR",
            branch="feature",
            target="main",
            status="open",
            created_at=datetime.now(timezone.utc),
        )

        effect = MergePR(pr=pr, delete_branch=True)
        handler(effect)

        call_args = mock_run.call_args[0][0]
        assert "--delete-branch" in call_args


class TestGitCommandError:

    def test_from_subprocess_error(self):
        error = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "push"],
            stderr="fatal: no remote configured",
        )

        git_error = GitCommandError.from_subprocess_error(error, cwd="/path/to/repo")

        assert git_error.command == ["git", "push"]
        assert git_error.returncode == 128
        assert "no remote configured" in git_error.stderr
        assert git_error.cwd == "/path/to/repo"

    def test_error_message_format(self):
        error = GitCommandError(
            command=["git", "push", "origin", "main"],
            returncode=1,
            stderr="error: failed to push",
            cwd="/repo",
        )

        assert "git push origin main" in str(error)
        assert "Exit code: 1" in str(error)
