"""The effect the conductor handlers use to find where a workspace is materialized.

A workspace's worktree path is site-local and handler-private (``effects.workspace``), so it is
not a workflow effect. The workspace handler answers ``WorkspacePath``; the agent, git and exec
handlers and the workspace journal installed inside it ask it for the path they run in.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from doeff_conductor.effects.base import ConductorEffectBase

if TYPE_CHECKING:
    from doeff_conductor.types import Workspace


@dataclass(frozen=True, kw_only=True)
class WorkspacePath(ConductorEffectBase):
    """Ask for the site-local path that materializes a workspace.

    Yields: Path
    """

    workspace: "Workspace"
