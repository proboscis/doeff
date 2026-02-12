"""Git effects for doeff-conductor.

This module keeps conductor-specific git effects for backward compatibility.
New workflows should prefer direct use of ``doeff_git.effects``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

from doeff_git.effects import (
    CreatePR as GitCreatePR,
)
from doeff_git.effects import (
    GitCommit,
    GitDiff,
    GitPull,
    GitPush,
)
from doeff_git.effects import (
    MergePR as GitMergePR,
)

from doeff_conductor.effects.base import ConductorEffectBase

if TYPE_CHECKING:
    from doeff_conductor.types import MergeStrategy, PRHandle, WorktreeEnv


def _warn_deprecated(effect_name: str) -> None:
    replacement_map = {
        "Commit": "GitCommit",
        "Push": "GitPush",
        "CreatePR": "CreatePR",
        "MergePR": "MergePR",
    }
    replacement = replacement_map.get(effect_name, effect_name)
    warnings.warn(
        f"doeff_conductor.effects.git.{effect_name} is deprecated. "
        f"Use doeff_git.effects.{replacement} instead.",
        DeprecationWarning,
        stacklevel=3,
    )


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

    def __post_init__(self) -> None:
        _warn_deprecated("Commit")


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

    def __post_init__(self) -> None:
        _warn_deprecated("Push")


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
    labels: tuple[str, ...] | list[str] | None = None  # Labels to apply on create

    def __post_init__(self) -> None:
        _warn_deprecated("CreatePR")


@dataclass(frozen=True, kw_only=True)
class MergePR(ConductorEffectBase):
    """Merge a pull request.

    Merges the PR using the specified strategy.

    Yields: bool (True if merged successfully)
    """

    pr: PRHandle  # PR to merge
    strategy: MergeStrategy | None = None  # Merge strategy (default: merge)
    delete_branch: bool = True  # Delete source branch after merge

    def __post_init__(self) -> None:
        _warn_deprecated("MergePR")


# Generic aliases exposed for migration convenience.
GitCommitEffect = GitCommit
GitPushEffect = GitPush
GitPullEffect = GitPull
GitDiffEffect = GitDiff
GitCreatePREffect = GitCreatePR
GitMergePREffect = GitMergePR


__all__ = [
    "Commit",
    "CreatePR",
    "GitCommitEffect",
    "GitCreatePREffect",
    "GitDiffEffect",
    "GitMergePREffect",
    "GitPullEffect",
    "GitPushEffect",
    "MergePR",
    "Push",
]
