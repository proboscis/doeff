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

from doeff import Effect, EffectGenerator, Pass, default_handlers, do, run, slog


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
    preset_handler = preset_handlers()
    create_issue_handler = make_scheduled_handler(issue_handler.handle_create_issue)
    list_issues_handler = make_scheduled_handler(issue_handler.handle_list_issues)
    get_issue_handler = make_scheduled_handler(issue_handler.handle_get_issue)
    resolve_issue_handler = make_scheduled_handler(issue_handler.handle_resolve_issue)

    @do
    def workflow_handler(effect: Effect, k):
        if isinstance(effect, CreateIssue):
            return (yield create_issue_handler(effect, k))
        if isinstance(effect, ListIssues):
            return (yield list_issues_handler(effect, k))
        if isinstance(effect, GetIssue):
            return (yield get_issue_handler(effect, k))
        if isinstance(effect, ResolveIssue):
            return (yield resolve_issue_handler(effect, k))
        yield Pass()

    # Run the demo
    result = run(
        issue_lifecycle_demo(),
        handlers=[preset_handler, workflow_handler, *default_handlers()],
    )

    print(f"\nFinal issue state: {result.value.to_dict()}")


if __name__ == "__main__":
    main()
