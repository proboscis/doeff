"""doeff-git: provider-agnostic git effects and handlers for doeff."""
from importlib import import_module
from typing import TYPE_CHECKING

from ._effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR
from ._exceptions import GitCommandError
from ._handlers import (
    MockGitRuntime,
    git_local_handler,
    github_handler,
    mock_handlers,
    production_handlers,
)
from ._types import MergeStrategy, PRHandle

__all__ = [
    "CreatePR",
    "GitCommandError",
    "GitCommit",
    "GitDiff",
    "GitError",
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


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_git._exceptions import GitError as GitError

_LAZY_EXPORTS: dict[str, str] = {
    "GitError": "doeff_git._exceptions",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
