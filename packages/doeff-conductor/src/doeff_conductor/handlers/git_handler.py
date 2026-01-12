"""
Git handler for doeff-conductor.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..exceptions import GitCommandError

if TYPE_CHECKING:
    from ..effects.git import Commit, CreatePR, MergePR, Push
    from ..types import MergeStrategy, PRHandle, WorktreeEnv


def _run_git(
    args: list[str],
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git/gh command. Raises GitCommandError on failure."""
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise GitCommandError.from_subprocess_error(e, cwd=str(cwd) if cwd else None) from e


class GitHandler:
    """Handler for git effects using git and gh CLI."""

    def __init__(self):
        pass

    def handle_commit(self, effect: Commit) -> str:
        """Stage changes and create a commit. Returns commit SHA."""
        worktree_path = effect.env.path

        if effect.all:
            _run_git(["git", "add", "-A"], cwd=str(worktree_path))

        _run_git(
            ["git", "commit", "-m", effect.message],
            cwd=str(worktree_path),
        )

        sha_result = _run_git(
            ["git", "rev-parse", "HEAD"],
            cwd=str(worktree_path),
        )

        return sha_result.stdout.strip()

    def handle_push(self, effect: Push) -> None:
        """Push branch to remote. Raises GitCommandError on failure."""
        worktree_path = effect.env.path

        args = ["git", "push"]

        if effect.set_upstream:
            args.extend(["-u", effect.remote, effect.env.branch])
        else:
            args.extend([effect.remote, effect.env.branch])

        if effect.force:
            args.insert(2, "--force")

        _run_git(args, cwd=str(worktree_path))

    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
        """Create a pull request using gh CLI."""
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

        result = _run_git(args, cwd=str(worktree_path))

        pr_url = result.stdout.strip()
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

    def handle_merge_pr(self, effect: MergePR) -> None:
        """Merge a pull request using gh CLI. Raises GitCommandError on failure."""
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

        _run_git(args)


__all__ = ["GitHandler"]
