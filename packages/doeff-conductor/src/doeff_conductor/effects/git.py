"""
Git effects for doeff-conductor.

Effects for git operations:
- Commit: Create a commit
- Push: Push branch to remote
- CreatePR: Create a pull request
- MergePR: Merge a pull request
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import ConductorEffectBase

if TYPE_CHECKING:
    from ..types import MergeStrategy, PRHandle, WorktreeEnv


@dataclass(frozen=True, kw_only=True)
class Commit(ConductorEffectBase):
    """Create a commit in the worktree.

    Stages all changes and creates a commit with the given message.

    Yields: str (commit SHA)

    Example:
        @do
        def save_changes(env):
            sha = yield Commit(env=env, message="feat: add login feature")
            return sha
    """

    env: WorktreeEnv  # Worktree to commit in
    message: str  # Commit message
    all: bool = True  # Stage all changes (git add -A)


@dataclass(frozen=True, kw_only=True)
class Push(ConductorEffectBase):
    """Push branch to remote.

    Pushes the worktree's branch to the remote repository.

    Yields: bool (True if successful)

    Example:
        @do
        def publish_changes(env):
            yield Commit(env=env, message="feat: new feature")
            yield Push(env=env)
    """

    env: WorktreeEnv  # Worktree to push from
    remote: str = "origin"  # Remote name
    force: bool = False  # Force push
    set_upstream: bool = True  # Set upstream tracking


@dataclass(frozen=True, kw_only=True)
class CreatePR(ConductorEffectBase):
    """Create a pull request.

    Creates a PR from the worktree's branch to the target branch.

    Yields: PRHandle

    Example:
        @do
        def create_feature_pr(env, issue):
            yield Commit(env=env, message=f"feat: {issue.title}")
            yield Push(env=env)
            pr = yield CreatePR(
                env=env,
                title=issue.title,
                body=f"Resolves: {issue.id}",
            )
            return pr
    """

    env: WorktreeEnv  # Worktree with changes
    title: str  # PR title
    body: str | None = None  # PR body
    target: str = "main"  # Target branch
    draft: bool = False  # Create as draft PR


@dataclass(frozen=True, kw_only=True)
class MergePR(ConductorEffectBase):
    """Merge a pull request.

    Merges the PR using the specified strategy.

    Yields: bool (True if merged successfully)
    """

    pr: PRHandle  # PR to merge
    strategy: MergeStrategy | None = None  # Merge strategy (default: merge)
    delete_branch: bool = True  # Delete source branch after merge


__all__ = [
    "Commit",
    "Push",
    "CreatePR",
    "MergePR",
]
