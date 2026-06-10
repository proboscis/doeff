#!/usr/bin/env python
"""Example 04: multi-agent workflow with scheduler-backed parallelism."""

from doeff_core_effects.scheduler import scheduled

from doeff_conductor import Issue, IssueStatus, multi_agent
from doeff_conductor.handlers import mock_handlers, run_sync


def main() -> None:
    issue = Issue(
        id="ISSUE-042",
        title="Add caching layer",
        body="Implement a TTL-based caching layer for database queries.",
        status=IssueStatus.OPEN,
        labels=("feature", "performance"),
    )

    result = run_sync(scheduled(multi_agent(issue)), scheduled_handlers=mock_handlers())
    if result.is_err():
        raise result.error

    print(f"Workflow completed. PR: {result.value.url}")


if __name__ == "__main__":
    main()
