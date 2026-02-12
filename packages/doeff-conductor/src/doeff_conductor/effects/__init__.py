"""
Effect definitions for doeff-conductor.

All effects for conductor orchestration:
- Worktree: CreateWorktree, MergeBranches, DeleteWorktree
- Issue: CreateIssue, ListIssues, GetIssue, ResolveIssue
- Agent: RunAgent, SpawnAgent, SendMessage, WaitForStatus, CaptureOutput
- Git: Commit, Push, CreatePR, MergePR
"""

from .agent import (
    CaptureOutput,
    RunAgent,
    SendMessage,
    SpawnAgent,
    WaitForStatus,
)
from .base import ConductorEffectBase
from .git import (
    Commit,
    CreatePR,
    GitCommitEffect,
    GitCreatePREffect,
    GitDiffEffect,
    GitMergePREffect,
    GitPullEffect,
    GitPushEffect,
    MergePR,
    Push,
)
from .issue import (
    CreateIssue,
    GetIssue,
    ListIssues,
    ResolveIssue,
)
from .worktree import (
    CreateWorktree,
    DeleteWorktree,
    MergeBranches,
)

__all__ = [
    "CaptureOutput",
    # Git
    "Commit",
    # Base
    "ConductorEffectBase",
    # Issue
    "CreateIssue",
    "CreatePR",
    "GitCommitEffect",
    "GitCreatePREffect",
    "GitDiffEffect",
    "GitMergePREffect",
    "GitPullEffect",
    "GitPushEffect",
    # Worktree
    "CreateWorktree",
    "DeleteWorktree",
    "GetIssue",
    "ListIssues",
    "MergeBranches",
    "MergePR",
    "Push",
    "ResolveIssue",
    # Agent
    "RunAgent",
    "SendMessage",
    "SpawnAgent",
    "WaitForStatus",
]
