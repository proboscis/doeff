"""
Issue handler for doeff-conductor.
"""

import functools
import re
import secrets
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from doeff import K, Resume, handler
from doeff.program import ProgramHandler
from doeff_conductor.effects.issue import CreateIssue, GetIssue, ListIssues, ResolveIssue
from doeff_conductor.exceptions import (
    ConductorStateWarning,
    IssueFileCorruptError,
    IssueNotFoundError,
)
from doeff_conductor.types import Issue, IssueStatus

__all__ = ["issue_handler"]


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


def _warn_corrupt_issue_file(path: Path, reason: object) -> None:
    """Warn about one corrupt issue file while allowing issue list to continue."""
    warnings.warn(
        f"Corrupt issue file skipped: {path}: {reason}",
        ConductorStateWarning,
        stacklevel=2,
    )


class _Frontmatter(NamedTuple):
    fields: dict[str, Any]
    body: str


def _parse_frontmatter(
    content: str,
    *,
    path: Path | None = None,
) -> _Frontmatter:
    """Parse YAML frontmatter from markdown content.

    Returns:
        _Frontmatter of (fields dict, body string)
    """
    if not content.startswith("---"):
        return _Frontmatter({}, content)

    # Find end of frontmatter
    end_match = re.search(r"\n---\n", content[3:])
    if not end_match:
        return _Frontmatter({}, content)

    frontmatter_str = content[3 : end_match.start() + 3]
    body = content[end_match.end() + 3 :].strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError as error:
        raise IssueFileCorruptError(
            path,
            f"Issue file frontmatter is invalid YAML: {error}",
        ) from error
    if not isinstance(frontmatter, dict):
        raise IssueFileCorruptError(
            path,
            "Issue file frontmatter must be a YAML mapping",
            raw_value=frontmatter,
        )

    return _Frontmatter(frontmatter, body)


def _parse_created_at(frontmatter: dict[str, Any], *, path: Path) -> datetime:
    """Parse created frontmatter without substituting malformed values."""
    raw_created = frontmatter.get("created")
    if raw_created is None:
        return datetime.now(timezone.utc)

    try:
        created_at = datetime.fromisoformat(str(raw_created))
    except (TypeError, ValueError) as error:
        raise IssueFileCorruptError(
            path,
            "Issue file has malformed created frontmatter",
            field="created",
            raw_value=raw_created,
        ) from error

    if created_at.tzinfo is None:
        return created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc)


def _format_frontmatter(data: dict) -> str:
    """Format dictionary as YAML frontmatter."""
    return "---\n" + yaml.dump(data, default_flow_style=False) + "---\n\n"


def _open_issues_dir(issues_dir: Path | None) -> Path:
    """The issue vault directory, created if missing: the given one, or the one the cwd holds."""
    vault = issues_dir or _get_issues_dir()
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def _issue_metadata(frontmatter: dict[str, Any]) -> dict[str, Any]:
    """The frontmatter fields an Issue keeps as metadata (all but the ones it has fields for)."""
    return {
        k: v
        for k, v in frontmatter.items()
        if k not in ("id", "title", "status", "labels", "created", "pr_url")
    }


def _create_issue(issues_dir: Path, effect: CreateIssue) -> Issue:
    """Write a new issue file with YAML frontmatter."""
    # Generate ID
    issue_id = _generate_issue_id()

    # Ensure unique ID
    while (issues_dir / f"{issue_id}.md").exists():
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
    file_path = issues_dir / f"{issue_id}.md"
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


def _list_issues(issues_dir: Path, effect: ListIssues) -> list[Issue]:
    """List the issues in the vault that match the effect's filters, newest first."""
    issues = []

    for file_path in issues_dir.glob("*.md"):
        try:
            content = file_path.read_text()
            frontmatter, body = _parse_frontmatter(content, path=file_path)

            if not frontmatter.get("id"):
                raise IssueFileCorruptError(
                    file_path,
                    "Issue file is missing required id frontmatter",
                )

            issue_status = IssueStatus(frontmatter.get("status", "open"))

            # Apply status filter
            if effect.status and issue_status != effect.status:
                continue

            # Parse labels
            labels = tuple(frontmatter.get("labels", []))

            # Apply labels filter (any match)
            if effect.labels and not any(label in labels for label in effect.labels):
                continue

            # Parse dates
            created_at = _parse_created_at(frontmatter, path=file_path)

            issue = Issue(
                id=frontmatter["id"],
                title=frontmatter.get("title", "Untitled"),
                body=body,
                status=issue_status,
                labels=labels,
                created_at=created_at,
                pr_url=frontmatter.get("pr_url"),
                metadata=_issue_metadata(frontmatter),
            )
            issues.append(issue)
        except (OSError, IssueFileCorruptError, KeyError, TypeError, ValueError) as error:
            _warn_corrupt_issue_file(file_path, error)
            continue

    # Sort by created date (newest first)
    issues.sort(key=lambda i: i.created_at, reverse=True)

    # Apply limit
    if effect.limit:
        issues = issues[: effect.limit]

    return issues


def _get_issue(issues_dir: Path, effect: GetIssue) -> Issue:
    """Read one issue by ID."""
    file_path = issues_dir / f"{effect.id}.md"

    if not file_path.exists():
        raise IssueNotFoundError(effect.id)

    content = file_path.read_text()
    frontmatter, body = _parse_frontmatter(content, path=file_path)

    if not frontmatter.get("id"):
        raise IssueFileCorruptError(
            file_path,
            "Issue file is missing required id frontmatter",
        )

    created_at = _parse_created_at(frontmatter, path=file_path)

    return Issue(
        id=frontmatter["id"],
        title=frontmatter.get("title", "Untitled"),
        body=body,
        status=IssueStatus(frontmatter.get("status", "open")),
        labels=tuple(frontmatter.get("labels", [])),
        created_at=created_at,
        pr_url=frontmatter.get("pr_url"),
        metadata=_issue_metadata(frontmatter),
    )


def _resolve_issue(issues_dir: Path, effect: ResolveIssue) -> Issue:
    """Mark an issue resolved in its file, with the PR and result it was resolved by."""
    file_path = issues_dir / f"{effect.issue.id}.md"

    if not file_path.exists():
        raise IssueNotFoundError(effect.issue.id)

    content = file_path.read_text()
    frontmatter, body = _parse_frontmatter(content, path=file_path)

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


def issue_handler(issues_dir: Path | None = None) -> ProgramHandler:
    """Build the handler for issue effects, keeping issues as markdown files with YAML frontmatter.

    Args:
        issues_dir: The issue vault directory. ``VAULT/Issues`` or ``.conductor/issues`` under
            the cwd if not provided. It is found and created on the first issue effect, so a
            program that performs none does not create it.
    """

    @functools.cache
    def vault() -> Path:
        return _open_issues_dir(issues_dir)

    @handler
    def answer_issue(effect: CreateIssue | ListIssues | GetIssue | ResolveIssue, k: K):
        """Answer one issue effect from the issue vault."""
        match effect:
            case CreateIssue():
                answer: object = _create_issue(vault(), effect)
            case ListIssues():
                answer = _list_issues(vault(), effect)
            case GetIssue():
                answer = _get_issue(vault(), effect)
            case ResolveIssue():
                answer = _resolve_issue(vault(), effect)
        return (yield Resume(k, answer))

    return answer_issue
