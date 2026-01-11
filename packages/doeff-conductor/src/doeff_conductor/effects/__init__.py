"""
Effect definitions for doeff-conductor.

All effects for conductor orchestration:
- Worktree: CreateWorktree, MergeBranches, DeleteWorktree
- Issue: CreateIssue, ListIssues, GetIssue, ResolveIssue
- Agent: RunAgent, SpawnAgent, SendMessage, WaitForStatus, CaptureOutput
- Git: Commit, Push, CreatePR, MergePR
"""

from .worktree import (
    CreateWorktree,
    MergeBranches,
    DeleteWorktree,
)
from .issue import (
    CreateIssue,
    ListIssues,
    GetIssue,
    ResolveIssue,
)
from .agent import (
    RunAgent,
    SpawnAgent,
    SendMessage,
    WaitForStatus,
    CaptureOutput,
)
from .git import (
    Commit,
    Push,
    CreatePR,
    MergePR,
)
from .base import ConductorEffectBase

__all__ = [
    # Base
    "ConductorEffectBase",
    # Worktree
    "CreateWorktree",
    "MergeBranches",
    "DeleteWorktree",
    # Issue
    "CreateIssue",
    "ListIssues",
    "GetIssue",
    "ResolveIssue",
    # Agent
    "RunAgent",
    "SpawnAgent",
    "SendMessage",
    "WaitForStatus",
    "CaptureOutput",
    # Git
    "Commit",
    "Push",
    "CreatePR",
    "MergePR",
]
