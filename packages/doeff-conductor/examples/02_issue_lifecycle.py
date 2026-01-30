#!/usr/bin/env python
"""
Example 02: Issue Lifecycle

Demonstrates the full issue lifecycle:
1. Create an issue
2. List issues
3. Get issue by ID
4. Resolve issue

Run:
    cd packages/doeff-conductor
    uv run python examples/02_issue_lifecycle.py
"""

from doeff_conductor import (
    CreateIssue,
    GetIssue,
    Issue,
    IssueHandler,
    IssueStatus,
    ListIssues,
    ResolveIssue,
    make_scheduled_handler,
)
from doeff_preset import preset_handlers

from doeff import EffectGenerator, SyncRuntime, do
from doeff.effects.writer import slog


@do
def issue_lifecycle_demo() -> EffectGenerator[Issue]:
    """Demonstrate the full issue lifecycle.
    
    Returns:
        The resolved issue.
    """
    # Step 1: Create an issue
    yield slog(step="create", msg="Creating issue...")
    issue: Issue = yield CreateIssue(
        title="Add login feature",
        body="""
## Description
Implement user login with OAuth2 support.

## Acceptance Criteria
- [ ] Support Google OAuth
- [ ] Support GitHub OAuth
- [ ] Store user sessions securely
""",
        labels=("feature", "auth"),
    )
    yield slog(step="create", msg=f"Created issue: {issue.id} - {issue.title}")

    # Step 2: List all open issues
    yield slog(step="list", msg="Listing open issues...")
    open_issues: list[Issue] = yield ListIssues(status=IssueStatus.OPEN)
    yield slog(step="list", count=len(open_issues), issues=[f"{i.id}: {i.title}" for i in open_issues])

    # Step 3: Get issue by ID
    yield slog(step="fetch", msg=f"Fetching issue {issue.id}...")
    fetched: Issue = yield GetIssue(id=issue.id)
    yield slog(step="fetch", msg=f"Issue body preview: {fetched.body[:100]}...")

    # Step 4: Resolve the issue
    yield slog(step="resolve", msg=f"Resolving issue {issue.id}...")
    resolved: Issue = yield ResolveIssue(
        issue=fetched,
        pr_url="https://github.com/example/repo/pull/123",
        result="Implemented OAuth2 login for Google and GitHub",
    )
    yield slog(step="resolve", status=resolved.status.value, pr_url=resolved.pr_url)

    return resolved


def main():
    """Run the issue lifecycle demo."""
    # Set up handlers
    issue_handler = IssueHandler()

    domain_handlers = {
        CreateIssue: make_scheduled_handler(issue_handler.handle_create_issue),
        ListIssues: make_scheduled_handler(issue_handler.handle_list_issues),
        GetIssue: make_scheduled_handler(issue_handler.handle_get_issue),
        ResolveIssue: make_scheduled_handler(issue_handler.handle_resolve_issue),
    }

    # Merge preset handlers (slog display) with domain handlers
    handlers = {**preset_handlers(), **domain_handlers}

    # Run the demo
    runtime = SyncRuntime(handlers=handlers)
    result = runtime.run(issue_lifecycle_demo())

    print(f"\nFinal issue state: {result.value.to_dict()}")


if __name__ == "__main__":
    main()
