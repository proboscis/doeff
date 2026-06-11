"""
Multi-agent PR workflow template.

Workflow: issue -> parallel agents -> merge -> PR

A workflow with parallel agents:
1. Create separate worktrees for implementation and tests
2. Run agents in parallel to implement and write tests
3. Merge the branches
4. Run a review on the merged code
5. Create PR
"""

from doeff import EffectGenerator, Gather, Spawn, do

from ..types import Issue, PRHandle, Workspace

ARTIFACT_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
    },
}


# Helper functions to wrap effects as Programs for Spawn
@do
def _create_workspace(issue: Issue, suffix: str) -> EffectGenerator[Workspace]:
    """Create a worktree (wrapper for Spawn compatibility)."""
    from ..effects import CreateWorkspace
    return (
        yield CreateWorkspace(issue=issue, workspace_id=f"{issue.id.lower()}-{suffix}")
    )


@do
def _run_agent(
    issue: Issue,
    env: Workspace,
    prompt: str,
    node_id: str,
) -> EffectGenerator[dict]:
    """Run an agent (wrapper for Spawn compatibility)."""
    from ..effects import Agent, AgentTask
    return (
        yield Agent(
            AgentTask(
                run_id=issue.id,
                node_id=node_id,
                attempt=0,
                env=env,
                prompt=prompt,
                result_schema=ARTIFACT_SCHEMA,
                verification_class="test-verifiable",
                agent_type="codex",
            )
        )
    )


@do
def _commit(env: Workspace, message: str) -> EffectGenerator[str]:
    """Create a commit (wrapper for Spawn compatibility)."""
    from ..effects import Commit
    return (yield Commit(workspace=env, message=message))


@do
def multi_agent(issue: Issue) -> EffectGenerator[PRHandle]:
    """Multi-agent PR workflow: issue -> parallel agents -> merge -> PR.

    Args:
        issue: The issue to implement

    Returns:
        PRHandle for the created PR
    """
    from ..effects import (
        Agent,
        AgentTask,
        Commit,
        CreatePR,
        MergeWorkspaces,
        Push,
        ResolveIssue,
    )

    # Step 1: Create parallel worktrees (spawn to get futures, then gather)
    impl_task = yield Spawn(_create_workspace(issue, "impl"))
    test_task = yield Spawn(_create_workspace(issue, "tests"))
    impl_env, test_env = yield Gather(impl_task, test_task)

    # Step 2: Run agents in parallel
    impl_prompt = f"""
Implement the following issue:

# {issue.title}

{issue.body}

Focus on implementing the core functionality.
Do NOT write tests - a separate agent will handle that.
"""

    test_prompt = f"""
Write tests for the following issue:

# {issue.title}

{issue.body}

Focus on writing comprehensive tests:
1. Unit tests for core functionality
2. Edge case tests
3. Integration tests if applicable

Do NOT implement the feature - just write the tests.
"""

    impl_agent_task = yield Spawn(_run_agent(issue, impl_env, impl_prompt, "implementer"))
    test_agent_task = yield Spawn(_run_agent(issue, test_env, test_prompt, "tester"))
    yield Gather(impl_agent_task, test_agent_task)

    # Step 3: Commit changes in parallel environments
    impl_commit_task = yield Spawn(_commit(impl_env, f"feat: implement {issue.title}"))
    test_commit_task = yield Spawn(_commit(test_env, f"test: add tests for {issue.title}"))
    yield Gather(impl_commit_task, test_commit_task)

    # Step 4: Reconcile workspaces
    merge_result = yield MergeWorkspaces(
        workspace_id=f"{issue.id.lower()}-merged",
        workspaces=(impl_env, test_env),
    )
    if not merge_result.merged or merge_result.workspace is None:
        raise RuntimeError(f"Workspace merge failed: {merge_result.message}")
    merged_env = merge_result.workspace

    # Step 5: Review and finalize
    review_prompt = f"""
Review the merged implementation and tests for:

# {issue.title}

{issue.body}

Check that:
1. Implementation is complete
2. Tests cover the implementation
3. Tests pass

If any fixes are needed, make them now.
"""
    yield Agent(
        AgentTask(
            run_id=issue.id,
            node_id="reviewer",
            attempt=0,
            env=merged_env,
            prompt=review_prompt,
            result_schema=ARTIFACT_SCHEMA,
            verification_class="review",
            agent_type="codex",
        )
    )

    # Step 6: Final commit and push
    yield Commit(workspace=merged_env, message=f"chore: finalize {issue.title}")
    yield Push(workspace=merged_env)

    # Step 7: Create PR
    pr = yield CreatePR(
        workspace=merged_env,
        title=issue.title,
        body=f"""
## Summary

Implements {issue.id}: {issue.title}

## Changes

{issue.body}

## Implementation Approach

This PR was created using the multi_agent workflow:
- **Implementer agent**: Focused on core functionality
- **Tester agent**: Focused on comprehensive test coverage
- **Reviewer agent**: Final integration and quality check

---
Generated by doeff-conductor multi_agent template
""",
    )

    # Step 8: Resolve the issue
    yield ResolveIssue(issue=issue, pr_url=pr.url)

    return pr


__all__ = ["multi_agent"]
