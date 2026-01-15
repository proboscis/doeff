#!/usr/bin/env python
"""
Example 04: Multi-Agent Workflow

Demonstrates parallel agent execution:
1. Create multiple worktrees in parallel
2. Run agents in parallel (implementer + tester)
3. Merge branches
4. Run reviewer agent
5. Create PR

This example uses mock handlers and shows how Gather enables parallelism.

Run:
    cd packages/doeff-conductor
    uv run python examples/04_multi_agent.py
"""

from datetime import datetime, timezone
from pathlib import Path

from doeff import do, EffectGenerator, SyncRuntime, Gather
from doeff_conductor import (
    # Types
    Issue,
    IssueStatus,
    WorktreeEnv,
    PRHandle,
    MergeStrategy,
    # Effects
    CreateWorktree,
    MergeBranches,
    RunAgent,
    Commit,
    Push,
    CreatePR,
    ResolveIssue,
)


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
    
    Args:
        issue: The issue to implement.
        
    Returns:
        PRHandle for the created PR.
    """
    print(f"Starting multi-agent workflow for: {issue.title}\n")
    
    # Step 1: Create parallel worktrees using Gather
    print("1. Creating parallel worktrees...")
    impl_env, test_env = yield Gather(
        CreateWorktree(issue=issue, suffix="impl"),
        CreateWorktree(issue=issue, suffix="tests"),
    )
    print(f"   Implementation worktree: {impl_env.branch}")
    print(f"   Testing worktree: {test_env.branch}")
    
    # Step 2: Run agents in parallel
    print("\n2. Running agents in parallel...")
    impl_prompt = f"Implement: {issue.title}"
    test_prompt = f"Write tests for: {issue.title}"
    
    impl_output, test_output = yield Gather(
        RunAgent(env=impl_env, prompt=impl_prompt, name="implementer"),
        RunAgent(env=test_env, prompt=test_prompt, name="tester"),
    )
    print(f"   Implementer: {impl_output}")
    print(f"   Tester: {test_output}")
    
    # Step 3: Commit changes in parallel
    print("\n3. Committing changes in parallel...")
    yield Gather(
        Commit(env=impl_env, message=f"feat: implement {issue.title}"),
        Commit(env=test_env, message=f"test: add tests for {issue.title}"),
    )
    
    # Step 4: Merge branches
    print("\n4. Merging branches...")
    merged_env: WorktreeEnv = yield MergeBranches(
        envs=(impl_env, test_env),
        strategy=MergeStrategy.MERGE,
    )
    print(f"   Merged into: {merged_env.branch}")
    
    # Step 5: Run review agent
    print("\n5. Running reviewer agent...")
    review_output: str = yield RunAgent(
        env=merged_env,
        prompt=f"Review implementation of {issue.title}",
        name="reviewer",
    )
    print(f"   Reviewer: {review_output}")
    
    # Step 6: Final commit and push
    print("\n6. Finalizing...")
    yield Commit(env=merged_env, message=f"chore: finalize {issue.title}")
    yield Push(env=merged_env)
    
    # Step 7: Create PR
    print("\n7. Creating PR...")
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
    
    # Step 8: Resolve issue
    yield ResolveIssue(issue=issue, pr_url=pr.url)
    
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
    
    # Set up mock handlers
    mock = MockHandlers()
    handlers = {
        CreateWorktree: lambda e: mock.handle_create_worktree(e),
        MergeBranches: lambda e: mock.handle_merge_branches(e),
        RunAgent: lambda e: mock.handle_run_agent(e),
        Commit: lambda e: mock.handle_commit(e),
        Push: lambda e: mock.handle_push(e),
        CreatePR: lambda e: mock.handle_create_pr(e),
        ResolveIssue: lambda e: mock.handle_resolve_issue(e),
    }
    
    # Run the workflow
    runtime = SyncRuntime(handlers=handlers)
    pr = runtime.run(multi_agent_workflow(issue))
    
    print(f"\n{'='*50}")
    print(f"Workflow completed! PR: {pr.url}")


if __name__ == "__main__":
    main()
