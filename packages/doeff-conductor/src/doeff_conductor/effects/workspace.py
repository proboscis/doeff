"""Workspace effects for doeff-conductor.

Workspaces are logical mutable-state values. For the git medium family, a
workspace is identified by ``(repo, ref)`` while the worktree path used to
materialize it remains handler-private.

Workspace identity is resume-stable by construction: every emitter derives
``workspace_id`` deterministically (the workflow runtime from
``(run_id, workspace-node identity)``), and the handlers are idempotent
ensure-style — the same identity always binds the same branch and the same
site-local worktree across process restarts.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import ConductorEffectBase

if TYPE_CHECKING:
    from doeff_conductor.types import Issue, MergeStrategy, Workspace


@dataclass(frozen=True, kw_only=True)
class CreateWorkspace(ConductorEffectBase):
    """Ensure the logical workspace with this identity exists.

    Idempotent: re-emitting the same ``workspace_id`` re-adopts the existing
    branch and worktree (uncommitted changes preserved); a missing worktree
    whose branch exists is re-materialized from the branch; creation from
    ``from_ref`` happens exactly once per identity lifetime.

    Yields: Workspace
    """

    workspace_id: str
    repo: str = "default"
    from_ref: str | None = None
    issue: "Issue | None" = None


@dataclass(frozen=True, kw_only=True)
class MergeWorkspaces(ConductorEffectBase):
    """Deterministically reconcile several workspaces.

    The merged workspace binds the same resume-stable identity discipline as
    ``CreateWorkspace``: ``workspace_id`` determines branch and worktree, and
    re-emitting the effect re-adopts them and re-applies the (idempotent)
    merges. Git conflicts return a structured ``MergeWorkspacesResult`` rather
    than being swallowed or collapsed into an untyped exception.

    Yields: MergeWorkspacesResult
    """

    workspace_id: str
    workspaces: "tuple[Workspace, ...]"
    strategy: "MergeStrategy | None" = None


@dataclass(frozen=True, kw_only=True)
class DeleteWorkspace(ConductorEffectBase):
    """Release a workspace's site-local materialization.

    Yields: bool
    """

    workspace: "Workspace"
    force: bool = False
