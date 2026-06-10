"""Workspace effects for doeff-conductor.

Workspaces are logical mutable-state values. For the git medium family, a
workspace is identified by ``(repo, ref)`` while the worktree path used to
materialize it remains handler-private.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import ConductorEffectBase

if TYPE_CHECKING:
    from doeff_conductor.types import Issue, MergeStrategy, Workspace


@dataclass(frozen=True, kw_only=True)
class CreateWorkspace(ConductorEffectBase):
    """Create a logical workspace from a repository ref.

    Yields: Workspace
    """

    repo: str = "default"
    from_ref: str | None = None
    issue: "Issue | None" = None
    suffix: str | None = None
    name: str | None = None


@dataclass(frozen=True, kw_only=True)
class MergeWorkspaces(ConductorEffectBase):
    """Deterministically reconcile several workspaces.

    Git conflicts return a structured ``MergeWorkspacesResult`` rather than
    being swallowed or collapsed into an untyped exception.

    Yields: MergeWorkspacesResult
    """

    workspaces: "tuple[Workspace, ...]"
    strategy: "MergeStrategy | None" = None
    name: str | None = None


@dataclass(frozen=True, kw_only=True)
class DeleteWorkspace(ConductorEffectBase):
    """Release a workspace's site-local materialization.

    Yields: bool
    """

    workspace: "Workspace"
    force: bool = False

