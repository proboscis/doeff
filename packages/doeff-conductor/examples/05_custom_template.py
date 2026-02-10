#!/usr/bin/env python
"""
Example 05: Custom Template

Demonstrates creating a custom workflow template:
1. Define a custom workflow with specific requirements
2. Add quality gates (test runs, linting)
3. Handle conditional logic based on results
4. Register as a reusable template

Run:
    cd packages/doeff-conductor
    uv run python examples/05_custom_template.py
"""

from dataclasses import dataclass
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
    PRHandle,
    Push,
    ResolveIssue,
    RunAgent,
    WorktreeEnv,
    # Handler utility
    make_scheduled_handler,
    make_typed_handlers,
)
from doeff_conductor.effects.base import ConductorEffectBase
from doeff_preset import preset_handlers

from doeff import EffectGenerator, default_handlers, do, run, slog


# Custom effect for running tests
@dataclass(frozen=True, kw_only=True)
class RunTests(ConductorEffectBase):
    """Custom effect to run tests."""
    env: WorktreeEnv
    command: str = "pytest"


@dataclass(frozen=True, kw_only=True)
class RunLinter(ConductorEffectBase):
    """Custom effect to run linter."""
    env: WorktreeEnv
    command: str = "ruff check"


# Result types
@dataclass
class TestResult:
    """Result of running tests."""
    passed: bool
    output: str
    failures: int = 0


@dataclass
class LintResult:
    """Result of running linter."""
    passed: bool
    output: str
    issues: int = 0


# Mock handlers
class CustomMockHandlers:
    """Mock handlers including custom effects."""

    def __init__(self, test_should_pass: bool = True, lint_should_pass: bool = True):
        self._worktree_counter = 0
        self._test_should_pass = test_should_pass
        self._lint_should_pass = lint_should_pass
        self._test_attempt = 0

    def handle_create_worktree(self, effect: CreateWorktree) -> WorktreeEnv:
        self._worktree_counter += 1
        issue_id = effect.issue.id if effect.issue else "custom"
        return WorktreeEnv(
            id=f"wt-{self._worktree_counter:03d}",
            path=Path(f"/tmp/custom-worktree/{issue_id}"),
            branch=f"feat/{issue_id}",
            base_commit="abc1234",
            issue_id=issue_id,
        )

    def handle_run_agent(self, effect: RunAgent) -> str:
        name = effect.name or "agent"
        print(f"    [{name}] Processing: {effect.prompt[:50]}...")
        return f"Agent '{name}' completed"

    def handle_run_tests(self, effect: RunTests) -> TestResult:
        self._test_attempt += 1
        # Simulate tests passing on second attempt
        should_pass = self._test_should_pass or self._test_attempt > 1
        print(f"    [Tests] Running {effect.command} (attempt {self._test_attempt})...")
        return TestResult(
            passed=should_pass,
            output="All tests passed!" if should_pass else "3 tests failed",
            failures=0 if should_pass else 3,
        )

    def handle_run_linter(self, effect: RunLinter) -> LintResult:
        print(f"    [Linter] Running {effect.command}...")
        return LintResult(
            passed=self._lint_should_pass,
            output="No issues found" if self._lint_should_pass else "5 issues found",
            issues=0 if self._lint_should_pass else 5,
        )

    def handle_commit(self, effect: Commit) -> str:
        print(f"    [Git] Committed: {effect.message[:50]}")
        return "commit123"

    def handle_push(self, effect: Push) -> bool:
        print(f"    [Git] Pushed {effect.env.branch}")
        return True

    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
        return PRHandle(
            url="https://github.com/example/repo/pull/55",
            number=55,
            title=effect.title,
            branch=effect.env.branch,
            target=effect.target,
        )

    def handle_resolve_issue(self, effect: ResolveIssue) -> Issue:
        return Issue(
            id=effect.issue.id,
            title=effect.issue.title,
            body=effect.issue.body,
            status=IssueStatus.RESOLVED,
            pr_url=effect.pr_url,
            resolved_at=datetime.now(timezone.utc),
        )




@do
def quality_assured_pr(
    issue: Issue,
    max_fix_attempts: int = 3,
) -> EffectGenerator[PRHandle]:
    """Custom template: PR with quality gates.
    
    This workflow adds quality gates between implementation and PR:
    1. Create worktree
    2. Implement with agent
    3. Run linter - fail fast if issues
    4. Run tests - allow fix attempts
    5. Create PR only if all checks pass
    
    Args:
        issue: The issue to implement.
        max_fix_attempts: Maximum attempts to fix failing tests.
        
    Returns:
        PRHandle for the created PR.
        
    Raises:
        RuntimeError: If quality gates fail after max attempts.
    """
    yield slog(step="start", msg=f"Quality-Assured PR for: {issue.title}")

    # Step 1: Create worktree
    yield slog(step="worktree", status="creating")
    env: WorktreeEnv = yield CreateWorktree(issue=issue)

    # Step 2: Implement
    yield slog(step="implement", status="running")
    yield RunAgent(
        env=env,
        prompt=f"Implement: {issue.title}\n\n{issue.body}",
        name="implementer",
    )

    # Step 3: Run linter (fail fast)
    yield slog(step="lint", status="running")
    lint_result: LintResult = yield RunLinter(env=env)
    if not lint_result.passed:
        yield slog(step="lint", status="failed", issues=lint_result.issues)
        # Try auto-fix
        yield slog(step="lint", status="auto-fixing")
        yield RunAgent(
            env=env,
            prompt=f"Fix lint issues: {lint_result.output}",
            name="lint-fixer",
        )
        lint_result = yield RunLinter(env=env)
        if not lint_result.passed:
            raise RuntimeError(f"Lint check failed: {lint_result.output}")
    yield slog(step="lint", status="passed")

    # Step 4: Run tests with retry loop
    yield slog(step="test", status="running")
    for attempt in range(1, max_fix_attempts + 1):
        test_result: TestResult = yield RunTests(env=env)

        if test_result.passed:
            yield slog(step="test", status="passed", attempt=attempt)
            break

        yield slog(step="test", status="failed", attempt=attempt, failures=test_result.failures)

        if attempt < max_fix_attempts:
            yield slog(step="test", status="fixing")
            yield RunAgent(
                env=env,
                prompt=f"Fix test failures: {test_result.output}",
                name="test-fixer",
            )
    else:
        raise RuntimeError(f"Tests failed after {max_fix_attempts} attempts")

    # Step 5: Commit and push
    yield slog(step="commit", status="committing")
    yield Commit(env=env, message=f"feat: {issue.title}\n\nAll quality checks passed.")
    yield Push(env=env)
    yield slog(step="commit", status="pushed")

    # Step 6: Create PR
    yield slog(step="pr", status="creating")
    pr: PRHandle = yield CreatePR(
        env=env,
        title=f"[Quality Assured] {issue.title}",
        body=f"""
## Summary
Implements {issue.id}: {issue.title}

## Quality Gates Passed
- [x] Linter check
- [x] All tests passing

Generated by quality_assured_pr template
""",
    )

    # Step 7: Resolve issue
    yield ResolveIssue(issue=issue, pr_url=pr.url)

    yield slog(step="done", msg=f"SUCCESS! PR created: {pr.url}")

    return pr


def main():
    """Run the custom template example."""
    # Create a sample issue
    issue = Issue(
        id="ISSUE-055",
        title="Add rate limiting",
        body="""
## Description
Implement API rate limiting to prevent abuse.

## Requirements
- Token bucket algorithm
- Configurable limits per endpoint
- Return proper 429 responses
""",
        status=IssueStatus.OPEN,
        labels=("feature", "security"),
    )

    # Set up mock handlers (tests fail first time, pass second time)
    mock = CustomMockHandlers(test_should_pass=False, lint_should_pass=True)
    domain_handlers = {
        CreateWorktree: make_scheduled_handler(mock.handle_create_worktree),
        RunAgent: make_scheduled_handler(mock.handle_run_agent),
        RunTests: make_scheduled_handler(mock.handle_run_tests),
        RunLinter: make_scheduled_handler(mock.handle_run_linter),
        Commit: make_scheduled_handler(mock.handle_commit),
        Push: make_scheduled_handler(mock.handle_push),
        CreatePR: make_scheduled_handler(mock.handle_create_pr),
        ResolveIssue: make_scheduled_handler(mock.handle_resolve_issue),
    }

    # Merge preset handlers (slog display) with domain handlers
    handlers = {**preset_handlers(), **domain_handlers}

    # Run the workflow
    result = run(
        quality_assured_pr(issue),
        handlers=[*make_typed_handlers(handlers), *default_handlers()],
    )

    print(f"Final PR: {result.value.to_dict()}")


if __name__ == "__main__":
    main()
