"""
Worktree effects for doeff-conductor.

Effects for managing git worktree environments:
- CreateWorktree: Create a new git worktree
- MergeBranches: Merge multiple branches together
- DeleteWorktree: Delete a worktree and clean up
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import ConductorEffectBase

if TYPE_CHECKING:
    from ..types import Issue, MergeStrategy, WorktreeEnv


@dataclass(frozen=True, kw_only=True)
class CreateWorktree(ConductorEffectBase):
    """Create a new git worktree environment.

    Creates an isolated working directory with its own branch for agent work.

    Yields: WorktreeEnv

    Example:
        @do
        def my_workflow(issue):
            env = yield CreateWorktree(issue=issue)
            # env.path is now an isolated worktree
    """

    issue: Issue | None = None  # Issue to create worktree for
    base_branch: str | None = None  # Base branch (default: main/master)
    suffix: str | None = None  # Branch suffix for parallel worktrees
    name: str | None = None  # Custom worktree name


@dataclass(frozen=True, kw_only=True)
class MergeBranches(ConductorEffectBase):
    """Merge multiple worktree branches together.

    Creates a new worktree with all branches merged.

    Yields: WorktreeEnv (merged environment)

    Example:
        @do
        def parallel_workflow():
            impl_env = yield CreateWorktree(suffix="impl")
            test_env = yield CreateWorktree(suffix="tests")
            # ... run agents in parallel ...
            merged = yield MergeBranches(envs=[impl_env, test_env])
    """

    envs: tuple[WorktreeEnv, ...]  # Worktrees to merge
    strategy: MergeStrategy | None = None  # Merge strategy (default: MERGE)
    name: str | None = None  # Name for merged worktree


@dataclass(frozen=True, kw_only=True)
class DeleteWorktree(ConductorEffectBase):
    """Delete a worktree and clean up resources.

    Removes the worktree directory and prunes the git worktree reference.

    Yields: bool (True if deleted successfully)
    """

    env: WorktreeEnv  # Worktree to delete
    force: bool = False  # Force delete even if uncommitted changes


__all__ = [
    "CreateWorktree",
    "DeleteWorktree",
    "MergeBranches",
]
