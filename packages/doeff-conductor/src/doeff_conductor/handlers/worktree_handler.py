"""
Worktree handler for doeff-conductor.

Handles CreateWorktree, MergeBranches, DeleteWorktree effects
by managing git worktrees.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..effects.worktree import CreateWorktree, DeleteWorktree, MergeBranches
    from ..types import Issue, MergeStrategy, WorktreeEnv


def _get_worktree_base_dir() -> Path:
    """Get base directory for worktrees."""
    # Use XDG data home or fallback
    xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(xdg_data) / "doeff-conductor" / "worktrees"


def _get_default_branch(repo_path: Path) -> str:
    """Get the default branch name (main or master)."""
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            check=True,
        )
        # refs/remotes/origin/main -> main
        return result.stdout.strip().split("/")[-1]
    except subprocess.CalledProcessError:
        # Fallback to checking if main or master exists
        for branch in ["main", "master"]:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
                capture_output=True,
                cwd=repo_path,
            )
            if result.returncode == 0:
                return branch
        return "main"


def _get_current_commit(repo_path: Path) -> str:
    """Get current HEAD commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_path,
        check=True,
    )
    return result.stdout.strip()


def _get_repo_root(path: Path | None = None) -> Path:
    """Get the git repository root directory."""
    cwd = path or Path.cwd()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=True,
    )
    return Path(result.stdout.strip())


class WorktreeHandler:
    """Handler for worktree effects.

    Manages git worktrees for isolated agent environments.
    """

    def __init__(self, repo_path: Path | None = None):
        """Initialize handler.

        Args:
            repo_path: Path to git repository. Defaults to current repo.
        """
        self.repo_path = repo_path or _get_repo_root()
        self.worktree_base = _get_worktree_base_dir()
        self.worktree_base.mkdir(parents=True, exist_ok=True)

    def handle_create_worktree(self, effect: CreateWorktree) -> WorktreeEnv:
        """Handle CreateWorktree effect.

        Creates a new git worktree with a dedicated branch.
        """
        from ..types import WorktreeEnv

        # Generate unique ID and branch name
        env_id = secrets.token_hex(4)
        base_branch = effect.base_branch or _get_default_branch(self.repo_path)

        # Build branch name
        branch_parts = ["conductor"]
        if effect.issue:
            branch_parts.append(effect.issue.id.lower().replace("-", "_"))
        if effect.suffix:
            branch_parts.append(effect.suffix)
        branch_parts.append(env_id[:7])
        branch_name = "-".join(branch_parts)

        # Determine worktree path
        if effect.name:
            worktree_path = self.worktree_base / effect.name
        else:
            worktree_path = self.worktree_base / env_id

        # Get base commit
        base_commit = _get_current_commit(self.repo_path)

        # Create the worktree
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_path),
                base_branch,
            ],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
        )

        return WorktreeEnv(
            id=env_id,
            path=worktree_path,
            branch=branch_name,
            base_commit=base_commit,
            issue_id=effect.issue.id if effect.issue else None,
            created_at=datetime.now(timezone.utc),
        )

    def handle_merge_branches(self, effect: MergeBranches) -> WorktreeEnv:
        """Handle MergeBranches effect.

        Merges multiple worktree branches into a new worktree.
        """
        from ..types import MergeStrategy, WorktreeEnv

        if not effect.envs:
            raise ValueError("No environments to merge")

        # Use first env as base
        base_env = effect.envs[0]
        env_id = secrets.token_hex(4)
        branch_name = f"conductor-merged-{env_id[:7]}"
        worktree_path = self.worktree_base / (effect.name or f"merged-{env_id}")

        # Create new worktree from base
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_path),
                base_env.branch,
            ],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
        )

        # Merge other branches
        strategy = effect.strategy or MergeStrategy.MERGE
        for env in effect.envs[1:]:
            if strategy == MergeStrategy.MERGE:
                subprocess.run(
                    ["git", "merge", env.branch, "--no-edit"],
                    cwd=worktree_path,
                    check=True,
                    capture_output=True,
                )
            elif strategy == MergeStrategy.REBASE:
                subprocess.run(
                    ["git", "rebase", env.branch],
                    cwd=worktree_path,
                    check=True,
                    capture_output=True,
                )
            elif strategy == MergeStrategy.SQUASH:
                subprocess.run(
                    ["git", "merge", "--squash", env.branch],
                    cwd=worktree_path,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"Merge {env.branch}"],
                    cwd=worktree_path,
                    check=True,
                    capture_output=True,
                )

        return WorktreeEnv(
            id=env_id,
            path=worktree_path,
            branch=branch_name,
            base_commit=base_env.base_commit,
            created_at=datetime.now(timezone.utc),
        )

    def handle_delete_worktree(self, effect: DeleteWorktree) -> bool:
        """Handle DeleteWorktree effect.

        Removes the worktree and cleans up.
        """
        import shutil

        worktree_path = effect.env.path

        # Remove git worktree reference
        args = ["git", "worktree", "remove"]
        if effect.force:
            args.append("--force")
        args.append(str(worktree_path))

        try:
            subprocess.run(
                args,
                cwd=self.repo_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            if effect.force:
                # Force remove directory
                shutil.rmtree(worktree_path, ignore_errors=True)
                # Prune worktree references
                subprocess.run(
                    ["git", "worktree", "prune"],
                    cwd=self.repo_path,
                    capture_output=True,
                )
            else:
                return False

        return True


__all__ = ["WorktreeHandler"]
