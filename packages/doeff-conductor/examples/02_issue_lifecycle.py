#!/usr/bin/env python
"""Example 02: issue lifecycle."""

from doeff import EffectGenerator, do

from doeff_conductor import CreateIssue, GetIssue, Issue, IssueStatus, ListIssues, ResolveIssue
from doeff_conductor.handlers import mock_handlers, run_sync


@do
def issue_lifecycle_demo() -> EffectGenerator[Issue]:
    issue: Issue = yield CreateIssue(
        title="Add login feature",
        body="Implement user login with OAuth2 support.",
        labels=("feature", "auth"),
    )

    open_issues: list[Issue] = yield ListIssues(status=IssueStatus.OPEN)
    if issue.id not in {open_issue.id for open_issue in open_issues}:
        raise RuntimeError(f"created issue not found in open issue list: {issue.id}")

    fetched: Issue = yield GetIssue(id=issue.id)
    return (yield ResolveIssue(
        issue=fetched,
        pr_url="https://github.com/example/repo/pull/123",
        result="Implemented OAuth2 login.",
    ))


def main() -> None:
    result = run_sync(issue_lifecycle_demo(), scheduled_handlers=mock_handlers())
    if result.is_err():
        raise result.error
    print(result.value.to_dict())


if __name__ == "__main__":
    main()
