#!/usr/bin/env python
"""
Example 04: Multi-Agent Workflow

Demonstrates parallel agent execution with doeff-preset integration:
1. Create multiple worktrees in parallel
2. Run agents in parallel (implementer + tester)
3. Merge branches
4. Run reviewer agent
5. Create PR

This example uses mock handlers and shows how Gather enables parallelism.
It also demonstrates merging preset_handlers with domain handlers for
slog display and configuration.

Run:
    cd packages/doeff-conductor
    uv run python examples/04_multi_agent.py
"""

from datetime import datetime, timezone
from pathlib import Path

from doeff_conductor import (
    Commit,
    CreatePR,
    # Effects
    CreateWorktree,
    # Types
    Issue,
    IssueStatus,
    MergeBranches,
    MergeStrategy,
    PRHandle,
    Push,
    ResolveIssue,
    RunAgent,
    WorktreeEnv,
    # Handler utility
    make_scheduled_handler,
    make_typed_handlers,
)

from doeff import EffectGenerator, Gather, default_handlers, do, run, slog
from doeff_preset import preset_handlers


# Mock handlers for demonstration
class MockHandlers:
    """Mock handlers that simulate parallel conductor operations."""

    def __init__(self):
        self._worktree_counter = 0
        self._commit_counter = 0

    def handle_create_worktree(self, effect: CreateWorktree) -> WorktreeEnv:
        """Simulate creating a worktree."""
        self._worktree_counter += 1
        issue_id = effect.issue.id if effect.issue else "no-issue"
        suffix = effect.suffix or f"wt{self._worktree_counter}"
        print(f"    Creating worktree: {issue_id}-{suffix}")
        return WorktreeEnv(
            id=f"wt-{self._worktree_counter:03d}",
            path=Path(f"/tmp/mock-worktree/{issue_id}-{suffix}"),
            branch=f"feat/{issue_id}-{suffix}",
            base_commit="abc1234",
            issue_id=issue_id,
        )

    def handle_merge_branches(self, effect: MergeBranches) -> WorktreeEnv:
        """Simulate merging branches."""
        branches = [env.branch for env in effect.envs]
        print(f"    Merging branches: {branches}")
        self._worktree_counter += 1
        return WorktreeEnv(
            id=f"wt-merged-{self._worktree_counter:03d}",
            path=Path(f"/tmp/mock-worktree/merged-{self._worktree_counter}"),
            branch="feat/merged",
            base_commit="merged123",
        )

    def handle_run_agent(self, effect: RunAgent) -> str:
        """Simulate running an agent."""
        name = effect.name or "unnamed"
        print(f"    Running agent '{name}' in {effect.env.branch}")
        return f"Agent '{name}' completed successfully."

    def handle_commit(self, effect: Commit) -> str:
        """Simulate creating a commit."""
        self._commit_counter += 1
        sha = f"commit{self._commit_counter:04d}"
        print(f"    Commit in {effect.env.branch}: {effect.message[:50]}")
        return sha

    def handle_push(self, effect: Push) -> bool:
        """Simulate pushing to remote."""
        print(f"    Pushed {effect.env.branch}")
        return True

    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
        """Simulate creating a PR."""
        return PRHandle(
            url="https://github.com/example/repo/pull/99",
            number=99,
            title=effect.title,
            branch=effect.env.branch,
            target=effect.target,
        )

    def handle_resolve_issue(self, effect: ResolveIssue) -> Issue:
        """Simulate resolving an issue."""
        return Issue(
            id=effect.issue.id,
            title=effect.issue.title,
            body=effect.issue.body,
            status=IssueStatus.RESOLVED,
            pr_url=effect.pr_url,
            resolved_at=datetime.now(timezone.utc),
        )


@do
def multi_agent_workflow(issue: Issue) -> EffectGenerator[PRHandle]:
    """Multi-agent workflow: parallel implementation + tests + review.
    
    Uses slog for structured logging that gets displayed via preset_handlers.
    
    Args:
        issue: The issue to implement.
        
    Returns:
        PRHandle for the created PR.
    """
    yield slog(step="start", msg=f"Starting multi-agent workflow for: {issue.title}")

    # Step 1: Create parallel worktrees using Gather
    yield slog(step="worktrees", status="creating", msg="Creating parallel worktrees")
    impl_env, test_env = yield Gather(
        CreateWorktree(issue=issue, suffix="impl"),
        CreateWorktree(issue=issue, suffix="tests"),
    )
    yield slog(
        step="worktrees",
        status="created",
        impl_branch=impl_env.branch,
        test_branch=test_env.branch,
    )

    # Step 2: Run agents in parallel
    yield slog(step="agents", status="running", msg="Running agents in parallel")
    impl_prompt = f"Implement: {issue.title}"
    test_prompt = f"Write tests for: {issue.title}"

    impl_output, test_output = yield Gather(
        RunAgent(env=impl_env, prompt=impl_prompt, name="implementer"),
        RunAgent(env=test_env, prompt=test_prompt, name="tester"),
    )
    yield slog(
        step="agents",
        status="completed",
        implementer=impl_output[:50],
        tester=test_output[:50],
    )

    # Step 3: Commit changes in parallel
    yield slog(step="commit", status="committing", msg="Committing changes in parallel")
    yield Gather(
        Commit(env=impl_env, message=f"feat: implement {issue.title}"),
        Commit(env=test_env, message=f"test: add tests for {issue.title}"),
    )

    # Step 4: Merge branches
    yield slog(step="merge", status="merging", msg="Merging branches")
    merged_env: WorktreeEnv = yield MergeBranches(
        envs=(impl_env, test_env),
        strategy=MergeStrategy.MERGE,
    )
    yield slog(step="merge", status="merged", branch=merged_env.branch)

    # Step 5: Run review agent
    yield slog(step="review", status="running", msg="Running reviewer agent")
    review_output: str = yield RunAgent(
        env=merged_env,
        prompt=f"Review implementation of {issue.title}",
        name="reviewer",
    )
    yield slog(step="review", status="completed", output=review_output[:50])

    # Step 6: Final commit and push
    yield slog(step="finalize", status="committing", msg="Finalizing changes")
    yield Commit(env=merged_env, message=f"chore: finalize {issue.title}")
    yield Push(env=merged_env)

    # Step 7: Create PR
    yield slog(step="pr", status="creating", msg="Creating PR")
    pr: PRHandle = yield CreatePR(
        env=merged_env,
        title=issue.title,
        body=f"""
## Summary
Implements {issue.id}: {issue.title}

## Implementation Approach
- Implementer agent: Core functionality
- Tester agent: Comprehensive test coverage
- Reviewer agent: Final integration check

Generated by doeff-conductor multi_agent workflow
""",
    )
    yield slog(step="pr", status="created", url=pr.url)

    # Step 8: Resolve issue
    yield ResolveIssue(issue=issue, pr_url=pr.url)
    yield slog(step="done", msg="Workflow completed successfully")

    return pr


def main():
    """Run the multi-agent workflow example."""
    # Create a sample issue
    issue = Issue(
        id="ISSUE-042",
        title="Add caching layer",
        body="""
## Description
Implement a caching layer for database queries.

## Requirements
- Support TTL-based expiration
- Support cache invalidation
- Thread-safe implementation
""",
        status=IssueStatus.OPEN,
        labels=("feature", "performance"),
    )

    # Set up mock handlers for domain-specific effects
    mock = MockHandlers()
    mock_handlers = {
        CreateWorktree: make_scheduled_handler(mock.handle_create_worktree),
        MergeBranches: make_scheduled_handler(mock.handle_merge_branches),
        RunAgent: make_scheduled_handler(mock.handle_run_agent),
        Commit: make_scheduled_handler(mock.handle_commit),
        Push: make_scheduled_handler(mock.handle_push),
        CreatePR: make_scheduled_handler(mock.handle_create_pr),
        ResolveIssue: make_scheduled_handler(mock.handle_resolve_issue),
    }

    # Merge preset handlers with mock handlers
    # Preset provides: slog display (WriterTellEffect) + config (Ask preset.*)
    # Mock provides: conductor-specific effects (CreateWorktree, RunAgent, etc.)
    handlers = {**preset_handlers(), **mock_handlers}

    # Run the workflow - slog messages will be displayed via rich
    result = run(
        multi_agent_workflow(issue),
        handlers=[*make_typed_handlers(handlers), *default_handlers()],
    )

    print(f"\n{'='*50}")
    print(f"Workflow completed! PR: {result.value.url}")
    print(f"\nCaptured {len(result.log)} slog messages in writer log")


if __name__ == "__main__":
    main()
