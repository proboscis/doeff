"""Git effects for doeff-conductor."""

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
    from doeff_conductor.types import MergeStrategy, PRHandle, Workspace


@dataclass(frozen=True, kw_only=True)
class Commit(ConductorEffectBase):
    """Create a commit in the workspace.

    Stages all changes and creates a commit with the given message.

    Yields: str (commit SHA)

    Example:
        @do
        def save_changes(env):
            sha = yield Commit(workspace=workspace, message="feat: add login feature")
            return sha
    """

    workspace: "Workspace"  # Workspace to commit in
    message: str  # Commit message
    all: bool = True  # Stage all changes (git add -A)


@dataclass(frozen=True, kw_only=True)
class Push(ConductorEffectBase):
    """Push branch to remote.

    Pushes the workspace's ref to the remote repository.

    Yields: bool (True if successful)

    Example:
        @do
        def publish_changes(env):
            yield Commit(workspace=workspace, message="feat: new feature")
            yield Push(workspace=workspace)
    """

    workspace: "Workspace"  # Workspace to push from
    remote: str = "origin"  # Remote name
    force: bool = False  # Force push
    set_upstream: bool = True  # Set upstream tracking


@dataclass(frozen=True, kw_only=True)
class CreatePR(ConductorEffectBase):
    """Create a pull request.

    Creates a PR from the workspace's ref to the target branch.

    Yields: PRHandle

    Example:
        @do
        def create_feature_pr(workspace, issue):
            yield Commit(workspace=workspace, message=f"feat: {issue.title}")
            yield Push(workspace=workspace)
            pr = yield CreatePR(
                workspace=workspace,
                title=issue.title,
                body=f"Resolves: {issue.id}",
            )
            return pr
    """

    workspace: "Workspace"  # Workspace with changes
    title: str  # PR title
    body: str | None = None  # PR body
    target: str = "main"  # Target branch
    draft: bool = False  # Create as draft PR
    labels: tuple[str, ...] | list[str] | None = None  # Labels to apply on create


@dataclass(frozen=True, kw_only=True)
class MergePR(ConductorEffectBase):
    """Merge a pull request.

    Merges the PR using the specified strategy.

    Yields: bool (True if merged successfully)
    """

    pr: "PRHandle"  # PR to merge
    strategy: "MergeStrategy | None" = None  # Merge strategy (default: merge)
    delete_branch: bool = True  # Delete source branch after merge


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
