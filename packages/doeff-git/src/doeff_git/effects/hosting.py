"""Hosting-platform git operation effects."""


from dataclasses import dataclass
from pathlib import Path

from doeff import EffectBase
from doeff_git.types import MergeStrategy, PRHandle


@dataclass(frozen=True, kw_only=True)
class CreatePR(EffectBase):
    """Create a pull request for a branch."""

    work_dir: Path
    title: str
    body: str | None = None
    target: str = "main"
    draft: bool = False
    labels: list[str] | None = None
    head: str | None = None


@dataclass(frozen=True, kw_only=True)
class MergePR(EffectBase):
    """Merge a pull request."""

    pr: PRHandle
    strategy: MergeStrategy | str | None = None
    delete_branch: bool = True


__all__ = [
    "CreatePR",
    "MergePR",
]
