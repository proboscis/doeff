"""doeff-git: provider-agnostic git effects and handlers for doeff."""

from .effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR
from .exceptions import GitCommandError
from .handlers import (
    GitHubHandler,
    GitLocalHandler,
    MockGitRuntime,
    mock_handlers,
    production_handlers,
)
from .types import BranchRef, MergeStrategy, PRHandle

__all__ = [
    "BranchRef",
    "CreatePR",
    "GitCommandError",
    "GitCommit",
    "GitDiff",
    "GitHubHandler",
    "GitLocalHandler",
    "GitPull",
    "GitPush",
    "MergePR",
    "MergeStrategy",
    "MockGitRuntime",
    "PRHandle",
    "mock_handlers",
    "production_handlers",
]
