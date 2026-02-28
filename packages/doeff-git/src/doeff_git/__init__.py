"""doeff-git: provider-agnostic git effects and handlers for doeff."""


__version__ = "0.1.0"

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
    "__version__",
    "mock_handlers",
    "production_handlers",
]
