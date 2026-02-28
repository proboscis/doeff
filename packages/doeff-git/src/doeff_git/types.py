"""Shared git-domain types used by effects and handlers."""


from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class MergeStrategy(Enum):
    """Supported pull-request merge strategies."""

    MERGE = "merge"
    REBASE = "rebase"
    SQUASH = "squash"


@dataclass(frozen=True, kw_only=True)
class BranchRef:
    """Reference to a branch on a remote."""

    name: str
    remote: str = "origin"


@dataclass
class PRHandle:
    """Handle to a hosting pull request (or merge request)."""

    url: str
    number: int
    title: str
    branch: str
    target: str
    status: str = "open"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    work_dir: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "url": self.url,
            "number": self.number,
            "title": self.title,
            "branch": self.branch,
            "target": self.target,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "work_dir": str(self.work_dir) if self.work_dir else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PRHandle:
        """Rehydrate a PRHandle from serialized data."""
        raw_work_dir = data.get("work_dir")
        work_dir = Path(raw_work_dir) if raw_work_dir else None
        return cls(
            url=data["url"],
            number=data["number"],
            title=data["title"],
            branch=data["branch"],
            target=data["target"],
            status=data.get("status", "open"),
            created_at=datetime.fromisoformat(data["created_at"]),
            work_dir=work_dir,
        )


__all__ = [
    "BranchRef",
    "MergeStrategy",
    "PRHandle",
]
