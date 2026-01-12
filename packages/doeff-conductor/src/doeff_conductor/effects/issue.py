"""
Issue effects for doeff-conductor.

Effects for managing issues in the vault:
- CreateIssue: Create a new issue
- ListIssues: List issues with filters
- GetIssue: Get issue by ID
- ResolveIssue: Mark issue as resolved
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import ConductorEffectBase

if TYPE_CHECKING:
    from ..types import Issue, IssueStatus


@dataclass(frozen=True, kw_only=True)
class CreateIssue(ConductorEffectBase):
    """Create a new issue in the vault.

    Creates a markdown file with YAML frontmatter in the issues directory.

    Yields: Issue

    Example:
        @do
        def create_feature():
            issue = yield CreateIssue(
                title="Add login feature",
                body="Implement OAuth2 login...",
                labels=["feature", "auth"],
            )
            return issue
    """

    title: str  # Issue title
    body: str  # Issue body (markdown)
    labels: tuple[str, ...] = ()  # Issue labels
    metadata: dict | None = None  # Additional metadata


@dataclass(frozen=True, kw_only=True)
class ListIssues(ConductorEffectBase):
    """List issues from the vault with optional filters.

    Yields: list[Issue]

    Example:
        @do
        def list_open():
            issues = yield ListIssues(status=IssueStatus.OPEN)
            return issues
    """

    status: IssueStatus | None = None  # Filter by status
    labels: tuple[str, ...] = ()  # Filter by labels (any match)
    limit: int | None = None  # Max issues to return


@dataclass(frozen=True, kw_only=True)
class GetIssue(ConductorEffectBase):
    """Get an issue by ID.

    Yields: Issue
    Raises: IssueNotFoundError if issue doesn't exist

    Example:
        @do
        def get_and_process():
            issue = yield GetIssue(id="ISSUE-001")
            return issue
    """

    id: str  # Issue ID


@dataclass(frozen=True, kw_only=True)
class ResolveIssue(ConductorEffectBase):
    """Mark an issue as resolved.

    Updates the issue status and optionally links the PR.

    Yields: Issue (updated)

    Example:
        @do
        def complete_workflow():
            pr = yield CreatePR(...)
            yield ResolveIssue(issue=issue, pr_url=pr.url)
    """

    issue: Issue  # Issue to resolve
    pr_url: str | None = None  # Associated PR URL
    result: str | None = None  # Resolution summary


__all__ = [
    "CreateIssue",
    "ListIssues",
    "GetIssue",
    "ResolveIssue",
]
