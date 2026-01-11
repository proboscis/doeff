"""
Git handler for doeff-conductor.

Handles Commit, Push, CreatePR, MergePR effects
by executing git and gh commands.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from datetime import datetime, timezone

if TYPE_CHECKING:
    from ..effects.git import Commit, CreatePR, MergePR, Push
    from ..types import MergeStrategy, PRHandle, WorktreeEnv


class GitHandler:
    """Handler for git effects.

    Executes git commands and uses gh CLI for GitHub operations.
    """

    def __init__(self):
        """Initialize handler."""
        pass

    def handle_commit(self, effect: Commit) -> str:
        """Handle Commit effect.

        Stages changes and creates a commit.
        """
        worktree_path = effect.env.path

        # Stage changes
        if effect.all:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
            )

        # Create commit
        result = subprocess.run(
            ["git", "commit", "-m", effect.message],
            cwd=worktree_path,
            check=True,
            capture_output=True,
            text=True,
        )

        # Get commit SHA
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
            text=True,
        )

        return sha_result.stdout.strip()

    def handle_push(self, effect: Push) -> bool:
        """Handle Push effect.

        Pushes branch to remote.
        """
        worktree_path = effect.env.path

        args = ["git", "push"]

        if effect.set_upstream:
            args.extend(["-u", effect.remote, effect.env.branch])
        else:
            args.extend([effect.remote, effect.env.branch])

        if effect.force:
            args.insert(2, "--force")

        try:
            subprocess.run(
                args,
                cwd=worktree_path,
                check=True,
                capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
        """Handle CreatePR effect.

        Creates a pull request using gh CLI.
        """
        from ..types import PRHandle

        worktree_path = effect.env.path

        args = [
            "gh",
            "pr",
            "create",
            "--title",
            effect.title,
            "--base",
            effect.target,
            "--head",
            effect.env.branch,
        ]

        if effect.body:
            args.extend(["--body", effect.body])
        else:
            args.extend(["--body", ""])

        if effect.draft:
            args.append("--draft")

        result = subprocess.run(
            args,
            cwd=worktree_path,
            check=True,
            capture_output=True,
            text=True,
        )

        pr_url = result.stdout.strip()

        # Extract PR number from URL
        # Format: https://github.com/owner/repo/pull/123
        pr_number = int(pr_url.split("/")[-1])

        return PRHandle(
            url=pr_url,
            number=pr_number,
            title=effect.title,
            branch=effect.env.branch,
            target=effect.target,
            status="open",
            created_at=datetime.now(timezone.utc),
        )

    def handle_merge_pr(self, effect: MergePR) -> bool:
        """Handle MergePR effect.

        Merges a pull request using gh CLI.
        """
        from ..types import MergeStrategy

        args = ["gh", "pr", "merge", str(effect.pr.number)]

        strategy = effect.strategy or MergeStrategy.MERGE
        if strategy == MergeStrategy.MERGE:
            args.append("--merge")
        elif strategy == MergeStrategy.REBASE:
            args.append("--rebase")
        elif strategy == MergeStrategy.SQUASH:
            args.append("--squash")

        if effect.delete_branch:
            args.append("--delete-branch")

        try:
            subprocess.run(
                args,
                check=True,
                capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False


__all__ = ["GitHandler"]
