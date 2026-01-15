#!/usr/bin/env python
"""
Example 03: Basic PR Workflow

Demonstrates the basic_pr template workflow:
1. Create worktree from issue
2. Run agent to implement issue
3. Commit and push changes
4. Create PR
5. Resolve issue

This example uses mock handlers for demonstration.

Run:
    cd packages/doeff-conductor
    uv run python examples/03_basic_pr_workflow.py
"""

from datetime import datetime, timezone
from pathlib import Path

from doeff import do, EffectGenerator, SyncRuntime
from doeff_conductor import (
    # Types
    Issue,
    IssueStatus,
    WorktreeEnv,
    PRHandle,
    # Effects
    CreateWorktree,
    RunAgent,
    Commit,
    Push,
    CreatePR,
    ResolveIssue,
    # Handler utility
    make_scheduled_handler,
)


# Mock handlers for demonstration (no real git/agent operations)
class MockHandlers:
    """Mock handlers that simulate conductor operations."""
    
    def __init__(self):
        self._worktree_counter = 0
        self._commit_counter = 0
        
    def handle_create_worktree(self, effect: CreateWorktree) -> WorktreeEnv:
        """Simulate creating a worktree."""
        self._worktree_counter += 1
        issue_id = effect.issue.id if effect.issue else "no-issue"
        suffix = effect.suffix or ""
        return WorktreeEnv(
            id=f"wt-{self._worktree_counter:03d}",
            path=Path(f"/tmp/mock-worktree/{issue_id}-{suffix}"),
            branch=f"feat/{issue_id}",
            base_commit="abc1234",
            issue_id=issue_id,
        )
    
    def handle_run_agent(self, effect: RunAgent) -> str:
        """Simulate running an agent."""
        print(f"  [Mock Agent] Processing in {effect.env.path}")
        print(f"  [Mock Agent] Prompt: {effect.prompt[:80]}...")
        return "Mock agent completed implementation successfully."
    
    def handle_commit(self, effect: Commit) -> str:
        """Simulate creating a commit."""
        self._commit_counter += 1
        sha = f"commit{self._commit_counter:04d}"
        print(f"  [Mock Git] Created commit {sha}: {effect.message}")
        return sha
    
    def handle_push(self, effect: Push) -> bool:
        """Simulate pushing to remote."""
        print(f"  [Mock Git] Pushed {effect.env.branch} to {effect.remote}")
        return True
    
    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
        """Simulate creating a PR."""
        return PRHandle(
            url=f"https://github.com/example/repo/pull/42",
            number=42,
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
def basic_pr_workflow(issue: Issue) -> EffectGenerator[PRHandle]:
    """Basic PR workflow: issue -> agent -> PR.
    
    Args:
        issue: The issue to implement.
        
    Returns:
        PRHandle for the created PR.
    """
    print(f"Starting basic_pr workflow for: {issue.title}")
    
    # Step 1: Create isolated worktree
    print("\n1. Creating worktree...")
    env: WorktreeEnv = yield CreateWorktree(issue=issue)
    print(f"   Worktree created: {env.path}")
    
    # Step 2: Run agent to implement the issue
    print("\n2. Running agent...")
    prompt = f"""
Implement the following issue:

# {issue.title}

{issue.body}

Please implement the changes needed to resolve this issue.
"""
    output: str = yield RunAgent(env=env, prompt=prompt)
    print(f"   Agent output: {output}")
    
    # Step 3: Commit and push changes
    print("\n3. Committing and pushing...")
    commit_msg = f"feat: {issue.title}\n\nResolves: {issue.id}"
    yield Commit(env=env, message=commit_msg)
    yield Push(env=env)
    
    # Step 4: Create PR
    print("\n4. Creating PR...")
    pr: PRHandle = yield CreatePR(
        env=env,
        title=issue.title,
        body=f"Implements {issue.id}",
    )
    print(f"   PR created: {pr.url}")
    
    # Step 5: Resolve the issue
    print("\n5. Resolving issue...")
    yield ResolveIssue(issue=issue, pr_url=pr.url)
    print("   Issue resolved!")
    
    return pr


def main():
    """Run the basic PR workflow example."""
    # Create a sample issue
    issue = Issue(
        id="ISSUE-001",
        title="Add user authentication",
        body="""
## Description
Implement user authentication with JWT tokens.

## Tasks
- Create login endpoint
- Create logout endpoint
- Add JWT token generation
- Add token validation middleware
""",
        status=IssueStatus.OPEN,
        labels=("feature", "security"),
    )
    
    # Set up mock handlers
    mock = MockHandlers()
    handlers = {
        CreateWorktree: make_scheduled_handler(mock.handle_create_worktree),
        RunAgent: make_scheduled_handler(mock.handle_run_agent),
        Commit: make_scheduled_handler(mock.handle_commit),
        Push: make_scheduled_handler(mock.handle_push),
        CreatePR: make_scheduled_handler(mock.handle_create_pr),
        ResolveIssue: make_scheduled_handler(mock.handle_resolve_issue),
    }
    
    # Run the workflow
    runtime = SyncRuntime(handlers=handlers)
    pr = runtime.run(basic_pr_workflow(issue))
    
    print(f"\n{'='*50}")
    print(f"Workflow completed! PR: {pr.url}")


if __name__ == "__main__":
    main()
