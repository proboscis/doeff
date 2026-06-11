#!/usr/bin/env python
"""Example 03: basic PR workflow with mock handlers."""

from doeff_conductor import Issue, IssueStatus, basic_pr
from doeff_conductor.handlers import mock_handlers, run_sync


def main() -> None:
    issue = Issue(
        id="ISSUE-001",
        title="Add user authentication",
        body="Implement user authentication with JWT tokens.",
        status=IssueStatus.OPEN,
        labels=("feature", "security"),
    )

    result = run_sync(basic_pr(issue), scheduled_handlers=mock_handlers())
    if result.is_err():
        raise result.error

    print(f"Workflow completed. PR: {result.value.url}")


if __name__ == "__main__":
    main()
