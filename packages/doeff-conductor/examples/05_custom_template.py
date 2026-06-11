#!/usr/bin/env python
"""Example 05: custom PR workflow with deterministic Exec gates."""

from doeff_conductor import (
    Agent,
    AgentTask,
    Commit,
    CreatePR,
    CreateWorkspace,
    Exec,
    Issue,
    IssueStatus,
    PRHandle,
    Push,
    ResolveIssue,
    Workspace,
)
from doeff_conductor.handlers import mock_handlers, run_sync

from doeff import EffectGenerator, do

CUSTOM_TEMPLATE_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
    },
}


@do
def quality_assured_pr(issue: Issue) -> EffectGenerator[PRHandle]:
    workspace: Workspace = yield CreateWorkspace(
        issue=issue,
        workspace_id=f"{issue.id.lower()}-quality-assured-pr",
    )
    yield Agent(
        AgentTask(
            run_id=issue.id,
            node_id="implementer",
            attempt=0,
            env=workspace,
            prompt=f"Implement: {issue.title}\n\n{issue.body}",
            result_schema=CUSTOM_TEMPLATE_SCHEMA,
            verification_class="test-verifiable",
            agent_type="codex",
            name="implementer",
        )
    )

    lint = yield Exec(cmd="printf '%s\n' 'lint ok'", workspace=workspace, timeout=10)
    if not lint.passed:
        raise RuntimeError(f"lint gate failed; see {lint.log_path}")

    tests = yield Exec(cmd="printf '%s\n' 'tests ok'", workspace=workspace, timeout=10)
    if not tests.passed:
        raise RuntimeError(f"test gate failed; see {tests.log_path}")

    yield Commit(workspace=workspace, message=f"feat: {issue.title}")
    yield Push(workspace=workspace)
    pr: PRHandle = yield CreatePR(
        workspace=workspace,
        title=f"[Quality Assured] {issue.title}",
        body=f"Implements {issue.id}. Gates: {lint.log_path}, {tests.log_path}",
    )
    yield ResolveIssue(issue=issue, pr_url=pr.url)
    return pr


def main() -> None:
    issue = Issue(
        id="ISSUE-055",
        title="Add rate limiting",
        body="Implement token-bucket API rate limiting.",
        status=IssueStatus.OPEN,
        labels=("feature", "security"),
    )

    result = run_sync(quality_assured_pr(issue), scheduled_handlers=mock_handlers())
    if result.is_err():
        raise result.error

    print(f"Workflow completed. PR: {result.value.url}")


if __name__ == "__main__":
    main()
