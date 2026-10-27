"""doeff-git: provider-agnostic git effects and handlers for doeff."""

from .effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR
from .exceptions import GitCommandError
from .handlers import (
    MockGitRuntime,
    git_local_handler,
    github_handler,
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
    "GitPull",
    "GitPush",
    "MergePR",
    "MergeStrategy",
    "MockGitRuntime",
    "PRHandle",
    "git_local_handler",
    "github_handler",
    "mock_handlers",
    "production_handlers",
]
