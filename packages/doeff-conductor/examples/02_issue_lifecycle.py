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

from typing import Any, Callable

from doeff import do, EffectGenerator, SyncRuntime
from doeff.runtime import Resume
from doeff_conductor import (
    CreateIssue,
    ListIssues,
    GetIssue,
    ResolveIssue,
    IssueHandler,
    Issue,
    IssueStatus,
)


def sync_handler(fn: Callable[[Any], Any]) -> Callable:
    """Wrap a simple handler function to match SyncRuntime's expected signature."""
    def handler(effect: Any, env: Any, store: Any):
        result = fn(effect)
        return Resume(result, store)
    return handler


@do
def issue_lifecycle_demo() -> EffectGenerator[Issue]:
    """Demonstrate the full issue lifecycle.
    
    Returns:
        The resolved issue.
    """
    # Step 1: Create an issue
    print("Creating issue...")
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
    print(f"Created issue: {issue.id} - {issue.title}")
    
    # Step 2: List all open issues
    print("\nListing open issues...")
    open_issues: list[Issue] = yield ListIssues(status=IssueStatus.OPEN)
    for i in open_issues:
        print(f"  - {i.id}: {i.title} [{i.status.value}]")
    
    # Step 3: Get issue by ID
    print(f"\nFetching issue {issue.id}...")
    fetched: Issue = yield GetIssue(id=issue.id)
    print(f"Issue body:\n{fetched.body[:100]}...")
    
    # Step 4: Resolve the issue
    print(f"\nResolving issue {issue.id}...")
    resolved: Issue = yield ResolveIssue(
        issue=fetched,
        pr_url="https://github.com/example/repo/pull/123",
        result="Implemented OAuth2 login for Google and GitHub",
    )
    print(f"Issue resolved: {resolved.status.value}")
    print(f"Linked PR: {resolved.pr_url}")
    
    return resolved


def main():
    """Run the issue lifecycle demo."""
    # Set up handlers
    issue_handler = IssueHandler()
    
    handlers = {
        CreateIssue: sync_handler(issue_handler.handle_create_issue),
        ListIssues: sync_handler(issue_handler.handle_list_issues),
        GetIssue: sync_handler(issue_handler.handle_get_issue),
        ResolveIssue: sync_handler(issue_handler.handle_resolve_issue),
    }
    
    # Run the demo
    runtime = SyncRuntime(handlers=handlers)
    result = runtime.run(issue_lifecycle_demo())
    
    print(f"\nFinal issue state: {result.to_dict()}")


if __name__ == "__main__":
    main()
