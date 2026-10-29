"""Local git operation effects."""


from dataclasses import dataclass
from pathlib import Path

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class GitCommit(EffectBase):
    """Create a git commit in the working directory."""

    work_dir: Path
    message: str
    all: bool = True


@dataclass(frozen=True, kw_only=True)
class GitDiff(EffectBase):
    """Read current diff for a repository."""

    work_dir: Path
    staged: bool = False


__all__ = [
    "GitCommit",
    "GitDiff",
]
