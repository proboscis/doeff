"""
Issue handler for doeff-conductor.
"""

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..exceptions import IssueNotFoundError

if TYPE_CHECKING:
    from ..effects.issue import CreateIssue, GetIssue, ListIssues, ResolveIssue
    from ..types import Issue


def _get_issues_dir() -> Path:
    """Get the default issues directory."""
    # Look for VAULT/Issues pattern or use local .conductor/issues
    cwd = Path.cwd()

    # Check for VAULT/Issues
    vault_issues = cwd / "VAULT" / "Issues"
    if vault_issues.exists():
        return vault_issues

    # Fallback to .conductor/issues in current dir
    issues_dir = cwd / ".conductor" / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)
    return issues_dir


def _generate_issue_id() -> str:
    """Generate a unique issue ID."""
    # Format: ISSUE-XXX where XXX is numeric
    return f"ISSUE-{secrets.randbelow(900) + 100:03d}"


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content.

    Returns:
        Tuple of (frontmatter dict, body string)
    """
    if not content.startswith("---"):
        return {}, content

    # Find end of frontmatter
    end_match = re.search(r"\n---\n", content[3:])
    if not end_match:
        return {}, content

    frontmatter_str = content[3 : end_match.start() + 3]
    body = content[end_match.end() + 3 :].strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError:
        frontmatter = {}

    return frontmatter, body


def _format_frontmatter(data: dict) -> str:
    """Format dictionary as YAML frontmatter."""
    return "---\n" + yaml.dump(data, default_flow_style=False) + "---\n\n"


class IssueHandler:
    """Handler for issue effects.

    Manages issues as markdown files with YAML frontmatter.
    """

    def __init__(self, issues_dir: Path | None = None):
        """Initialize handler.

        Args:
            issues_dir: Path to issues directory. Auto-detected if not provided.
        """
        self.issues_dir = issues_dir or _get_issues_dir()
        self.issues_dir.mkdir(parents=True, exist_ok=True)

    def handle_create_issue(self, effect: "CreateIssue") -> "Issue":
        """Handle CreateIssue effect.

        Creates a new issue file with YAML frontmatter.
        """
        from ..types import Issue, IssueStatus

        # Generate ID
        issue_id = _generate_issue_id()

        # Ensure unique ID
        while (self.issues_dir / f"{issue_id}.md").exists():
            issue_id = _generate_issue_id()

        now = datetime.now(timezone.utc)

        # Build frontmatter
        frontmatter = {
            "id": issue_id,
            "title": effect.title,
            "status": "open",
            "labels": list(effect.labels),
            "created": now.strftime("%Y-%m-%d"),
        }
        if effect.metadata:
            frontmatter.update(effect.metadata)

        # Write file
        file_path = self.issues_dir / f"{issue_id}.md"
        content = _format_frontmatter(frontmatter) + effect.body
        file_path.write_text(content)

        return Issue(
            id=issue_id,
            title=effect.title,
            body=effect.body,
            status=IssueStatus.OPEN,
            labels=effect.labels,
            created_at=now,
            metadata=effect.metadata or {},
        )

    def handle_list_issues(self, effect: "ListIssues") -> "list[Issue]":
        """Handle ListIssues effect.

        Lists issues from the issues directory with optional filters.
        """
        from ..types import Issue, IssueStatus

        issues = []

        for file_path in self.issues_dir.glob("*.md"):
            try:
                content = file_path.read_text()
                frontmatter, body = _parse_frontmatter(content)

                if not frontmatter.get("id"):
                    continue

                issue_status = IssueStatus(frontmatter.get("status", "open"))

                # Apply status filter
                if effect.status and issue_status != effect.status:
                    continue

                # Parse labels
                labels = tuple(frontmatter.get("labels", []))

                # Apply labels filter (any match)
                if effect.labels and not any(l in labels for l in effect.labels):
                    continue

                # Parse dates
                created_at = datetime.now(timezone.utc)
                if frontmatter.get("created"):
                    try:
                        created_at = datetime.fromisoformat(
                            str(frontmatter["created"])
                        ).replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pass

                issue = Issue(
                    id=frontmatter["id"],
                    title=frontmatter.get("title", "Untitled"),
                    body=body,
                    status=issue_status,
                    labels=labels,
                    created_at=created_at,
                    pr_url=frontmatter.get("pr_url"),
                    metadata={
                        k: v
                        for k, v in frontmatter.items()
                        if k not in ("id", "title", "status", "labels", "created", "pr_url")
                    },
                )
                issues.append(issue)
            except Exception:
                # Skip malformed files
                continue

        # Sort by created date (newest first)
        issues.sort(key=lambda i: i.created_at, reverse=True)

        # Apply limit
        if effect.limit:
            issues = issues[: effect.limit]

        return issues

    def handle_get_issue(self, effect: "GetIssue") -> "Issue":
        """Handle GetIssue effect.

        Gets an issue by ID.
        """
        from ..types import Issue, IssueStatus

        file_path = self.issues_dir / f"{effect.id}.md"

        if not file_path.exists():
            raise IssueNotFoundError(effect.id)

        content = file_path.read_text()
        frontmatter, body = _parse_frontmatter(content)

        if not frontmatter.get("id"):
            raise IssueNotFoundError(effect.id, f"Invalid issue file (missing id): {effect.id}")

        # Parse dates
        created_at = datetime.now(timezone.utc)
        if frontmatter.get("created"):
            try:
                created_at = datetime.fromisoformat(str(frontmatter["created"])).replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, TypeError):
                pass

        return Issue(
            id=frontmatter["id"],
            title=frontmatter.get("title", "Untitled"),
            body=body,
            status=IssueStatus(frontmatter.get("status", "open")),
            labels=tuple(frontmatter.get("labels", [])),
            created_at=created_at,
            pr_url=frontmatter.get("pr_url"),
            metadata={
                k: v
                for k, v in frontmatter.items()
                if k not in ("id", "title", "status", "labels", "created", "pr_url")
            },
        )

    def handle_resolve_issue(self, effect: "ResolveIssue") -> "Issue":
        """Handle ResolveIssue effect.

        Updates an issue's status to resolved.
        """
        from ..types import Issue, IssueStatus

        file_path = self.issues_dir / f"{effect.issue.id}.md"

        if not file_path.exists():
            raise IssueNotFoundError(effect.issue.id)

        content = file_path.read_text()
        frontmatter, body = _parse_frontmatter(content)

        # Update frontmatter
        frontmatter["status"] = "resolved"
        now = datetime.now(timezone.utc)
        frontmatter["resolved"] = now.strftime("%Y-%m-%d")

        if effect.pr_url:
            frontmatter["pr_url"] = effect.pr_url
        if effect.result:
            frontmatter["resolution"] = effect.result

        # Write updated file
        new_content = _format_frontmatter(frontmatter) + body
        file_path.write_text(new_content)

        # Return updated issue
        return Issue(
            id=effect.issue.id,
            title=effect.issue.title,
            body=effect.issue.body,
            status=IssueStatus.RESOLVED,
            labels=effect.issue.labels,
            created_at=effect.issue.created_at,
            resolved_at=now,
            pr_url=effect.pr_url or effect.issue.pr_url,
            metadata=frontmatter,
        )


__all__ = ["IssueHandler"]
