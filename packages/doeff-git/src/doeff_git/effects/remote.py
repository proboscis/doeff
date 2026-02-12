"""Remote git operation effects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class GitPush(EffectBase):
    """Push current branch to a remote."""

    work_dir: Path
    remote: str = "origin"
    force: bool = False
    set_upstream: bool = True
    branch: str | None = None


@dataclass(frozen=True, kw_only=True)
class GitPull(EffectBase):
    """Pull changes from a remote."""

    work_dir: Path
    remote: str = "origin"
    rebase: bool = False
    branch: str | None = None


__all__ = [
    "GitPull",
    "GitPush",
]
