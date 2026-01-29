"""Tests for worktree handler."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from doeff_conductor.effects.worktree import CreateWorktree, DeleteWorktree, MergeBranches
from doeff_conductor.handlers.worktree_handler import WorktreeHandler
from doeff_conductor.types import Issue, IssueStatus, MergeStrategy, WorktreeEnv


class TestWorktreeHandler:

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
    def worktree_base(self, tmp_path: Path) -> Path:
        base = tmp_path / "worktrees"
        base.mkdir()
        return base

    @pytest.fixture
    def handler(self, git_repo: Path, worktree_base: Path, monkeypatch) -> WorktreeHandler:
        monkeypatch.setattr(
            "doeff_conductor.handlers.worktree_handler._get_worktree_base_dir",
            lambda: worktree_base,
        )
        return WorktreeHandler(repo_path=git_repo)

    def test_create_worktree_basic(self, handler: WorktreeHandler):
        effect = CreateWorktree()

        env = handler.handle_create_worktree(effect)

        assert env.id is not None
        assert env.path.exists()
        assert env.branch.startswith("conductor-")
        assert env.base_commit is not None
        assert env.created_at is not None

    def test_create_worktree_with_issue(self, handler: WorktreeHandler):
        issue = Issue(
            id="ISSUE-123",
            title="Test Issue",
            body="Test body",
            status=IssueStatus.OPEN,
            labels=(),
            created_at=datetime.now(timezone.utc),
        )
        effect = CreateWorktree(issue=issue)

        env = handler.handle_create_worktree(effect)

        assert "issue_123" in env.branch
        assert env.issue_id == "ISSUE-123"

    def test_create_worktree_with_suffix(self, handler: WorktreeHandler):
        effect = CreateWorktree(suffix="impl")

        env = handler.handle_create_worktree(effect)

        assert "impl" in env.branch

    def test_create_worktree_with_name(self, handler: WorktreeHandler):
        effect = CreateWorktree(name="my-worktree")

        env = handler.handle_create_worktree(effect)

        assert env.path.name == "my-worktree"

    def test_create_worktree_returns_worktree_env(self, handler: WorktreeHandler):
        effect = CreateWorktree()

        env = handler.handle_create_worktree(effect)

        assert isinstance(env, WorktreeEnv)
        assert isinstance(env.path, Path)
        assert isinstance(env.created_at, datetime)

    def test_delete_worktree(self, handler: WorktreeHandler):
        create_effect = CreateWorktree()
        env = handler.handle_create_worktree(create_effect)
        assert env.path.exists()

        delete_effect = DeleteWorktree(env=env)
        result = handler.handle_delete_worktree(delete_effect)

        assert result is True
        assert not env.path.exists()

    def test_delete_worktree_force(self, handler: WorktreeHandler):
        create_effect = CreateWorktree()
        env = handler.handle_create_worktree(create_effect)

        (env.path / "dirty.txt").write_text("uncommitted")

        delete_effect = DeleteWorktree(env=env, force=True)
        result = handler.handle_delete_worktree(delete_effect)

        assert result is True

    def test_delete_worktree_nonexistent(self, handler: WorktreeHandler, worktree_base: Path):
        env = WorktreeEnv(
            id="nonexistent",
            path=worktree_base / "nonexistent",
            branch="nonexistent-branch",
            base_commit="abc123",
            created_at=datetime.now(timezone.utc),
        )

        delete_effect = DeleteWorktree(env=env)
        result = handler.handle_delete_worktree(delete_effect)

        assert result is False

    def test_merge_branches_two_worktrees(self, handler: WorktreeHandler):
        env1 = handler.handle_create_worktree(CreateWorktree(suffix="feature1"))
        (env1.path / "feature1.txt").write_text("Feature 1")
        subprocess.run(["git", "add", "."], cwd=env1.path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add feature1"],
            cwd=env1.path,
            check=True,
            capture_output=True,
        )

        env2 = handler.handle_create_worktree(CreateWorktree(suffix="feature2"))
        (env2.path / "feature2.txt").write_text("Feature 2")
        subprocess.run(["git", "add", "."], cwd=env2.path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add feature2"],
            cwd=env2.path,
            check=True,
            capture_output=True,
        )

        merge_effect = MergeBranches(envs=[env1, env2])
        merged = handler.handle_merge_branches(merge_effect)

        assert merged.path.exists()
        assert merged.branch.startswith("conductor-merged-")
        assert (merged.path / "feature1.txt").exists()
        assert (merged.path / "feature2.txt").exists()

    def test_merge_branches_empty_raises(self, handler: WorktreeHandler):
        effect = MergeBranches(envs=[])

        with pytest.raises(ValueError, match="No environments to merge"):
            handler.handle_merge_branches(effect)

    def test_merge_branches_with_strategy_squash(self, handler: WorktreeHandler):
        env1 = handler.handle_create_worktree(CreateWorktree(suffix="base"))
        (env1.path / "base.txt").write_text("Base")
        subprocess.run(["git", "add", "."], cwd=env1.path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add base"],
            cwd=env1.path,
            check=True,
            capture_output=True,
        )

        env2 = handler.handle_create_worktree(CreateWorktree(suffix="squash"))
        (env2.path / "squash.txt").write_text("Squash")
        subprocess.run(["git", "add", "."], cwd=env2.path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add squash"],
            cwd=env2.path,
            check=True,
            capture_output=True,
        )

        merge_effect = MergeBranches(envs=[env1, env2], strategy=MergeStrategy.SQUASH)
        merged = handler.handle_merge_branches(merge_effect)

        assert merged.path.exists()
        assert (merged.path / "squash.txt").exists()
