"""Tests for git handler."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from doeff_conductor.effects.git import Commit, CreatePR, MergePR, Push
from doeff_conductor.exceptions import GitCommandError
from doeff_conductor.handlers.git_handler import GitHandler
from doeff_conductor.types import MergeStrategy, PRHandle, WorktreeEnv


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

        return repo_path

    @pytest.fixture
    def worktree_env(self, git_repo: Path) -> WorktreeEnv:
        return WorktreeEnv(
            id="test-env",
            path=git_repo,
            branch="main",
            base_commit="abc123",
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def handler(self) -> GitHandler:
        return GitHandler()

    def test_commit_with_changes(self, handler: GitHandler, worktree_env: WorktreeEnv):
        (worktree_env.path / "new_file.txt").write_text("new content")

        effect = Commit(env=worktree_env, message="Add new file", all=True)
        sha = handler.handle_commit(effect)

        assert sha is not None
        assert len(sha) == 40

    def test_commit_without_all_flag(self, handler: GitHandler, worktree_env: WorktreeEnv):
        (worktree_env.path / "staged.txt").write_text("staged content")
        subprocess.run(
            ["git", "add", "staged.txt"],
            cwd=worktree_env.path,
            check=True,
            capture_output=True,
        )

        effect = Commit(env=worktree_env, message="Add staged file", all=False)
        sha = handler.handle_commit(effect)

        assert sha is not None
        assert len(sha) == 40

    def test_commit_empty_raises(self, handler: GitHandler, worktree_env: WorktreeEnv):
        effect = Commit(env=worktree_env, message="Empty commit", all=True)

        with pytest.raises(GitCommandError):
            handler.handle_commit(effect)

    def test_push_basic(self, handler: GitHandler, worktree_env: WorktreeEnv, tmp_path: Path):
        remote_path = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote_path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_path)],
            cwd=worktree_env.path,
            check=True,
            capture_output=True,
        )

        effect = Push(env=worktree_env, set_upstream=True)
        handler.handle_push(effect)

        result = subprocess.run(
            ["git", "ls-remote", str(remote_path)],
            capture_output=True,
            text=True, check=False,
        )
        assert "refs/heads/main" in result.stdout

    def test_push_force(self, handler: GitHandler, worktree_env: WorktreeEnv, tmp_path: Path):
        remote_path = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote_path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_path)],
            cwd=worktree_env.path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=worktree_env.path,
            check=True,
            capture_output=True,
        )

        effect = Push(env=worktree_env, force=True)
        handler.handle_push(effect)

    def test_push_no_remote_raises(self, handler: GitHandler, worktree_env: WorktreeEnv):
        effect = Push(env=worktree_env)

        with pytest.raises(GitCommandError):
            handler.handle_push(effect)

    @patch("doeff_git.handlers.production._run_command")
    def test_create_pr_success(self, mock_run: MagicMock, handler: GitHandler, worktree_env: WorktreeEnv):
        mock_run.return_value = MagicMock(
            stdout="https://github.com/user/repo/pull/42\n",
            returncode=0,
        )

        effect = CreatePR(env=worktree_env, title="Test PR", body="PR body", target="main")
        pr = handler.handle_create_pr(effect)

        assert pr.url == "https://github.com/user/repo/pull/42"
        assert pr.number == 42
        assert pr.title == "Test PR"
        assert pr.branch == "main"
        assert pr.status == "open"

    @patch("doeff_git.handlers.production._run_command")
    def test_create_pr_draft(self, mock_run: MagicMock, handler: GitHandler, worktree_env: WorktreeEnv):
        mock_run.return_value = MagicMock(
            stdout="https://github.com/user/repo/pull/43\n",
            returncode=0,
        )

        effect = CreatePR(env=worktree_env, title="Draft PR", draft=True, target="main")
        handler.handle_create_pr(effect)

        call_args = mock_run.call_args[0][0]
        assert "--draft" in call_args

    @patch("doeff_git.handlers.production._run_command")
    def test_merge_pr_default_strategy(self, mock_run: MagicMock, handler: GitHandler):
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
        handler.handle_merge_pr(effect)

        call_args = mock_run.call_args[0][0]
        assert "--merge" in call_args

    @patch("doeff_git.handlers.production._run_command")
    def test_merge_pr_squash_strategy(self, mock_run: MagicMock, handler: GitHandler):
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
        handler.handle_merge_pr(effect)

        call_args = mock_run.call_args[0][0]
        assert "--squash" in call_args

    @patch("doeff_git.handlers.production._run_command")
    def test_merge_pr_rebase_strategy(self, mock_run: MagicMock, handler: GitHandler):
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
        handler.handle_merge_pr(effect)

        call_args = mock_run.call_args[0][0]
        assert "--rebase" in call_args

    @patch("doeff_git.handlers.production._run_command")
    def test_merge_pr_delete_branch(self, mock_run: MagicMock, handler: GitHandler):
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
        handler.handle_merge_pr(effect)

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
