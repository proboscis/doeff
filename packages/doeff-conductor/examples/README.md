# doeff-conductor Examples

This directory contains progressive examples demonstrating doeff-conductor usage.

## Overview

| Example | Description | Concepts |
|---------|-------------|----------|
| [01_hello_workflow.py](./01_hello_workflow.py) | Minimal workflow | Worktree, Commit, basic effects |
| [02_issue_lifecycle.py](./02_issue_lifecycle.py) | Issue management | Create, List, Get, Resolve issues |
| [03_basic_pr_workflow.py](./03_basic_pr_workflow.py) | Complete PR workflow | Agent, Push, CreatePR, templates |
| [04_multi_agent.py](./04_multi_agent.py) | Parallel agents | Gather, MergeBranches, parallelism |
| [05_custom_template.py](./05_custom_template.py) | Custom templates | Custom effects, quality gates |
| [06_api_usage.py](./06_api_usage.py) | ConductorAPI | Programmatic access, JSON output |

## Running Examples

```bash
# From the doeff-conductor package directory
cd packages/doeff-conductor

# Run a specific example
uv run python examples/01_hello_workflow.py

# Or run all examples
for f in examples/0*.py; do uv run python "$f"; done
```

## Example 01: Hello Workflow

**Concepts:** WorktreeEnv, CreateWorktree, Commit, DeleteWorktree

The simplest possible workflow:
```python
@do
def hello_workflow():
    env = yield CreateWorktree(suffix="hello")
    (env.path / "hello.txt").write_text("Hello!")
    yield Commit(env=env, message="Add hello.txt")
    yield DeleteWorktree(env=env)
    return "Done!"
```

## Example 02: Issue Lifecycle

**Concepts:** Issue, IssueStatus, CreateIssue, ListIssues, GetIssue, ResolveIssue

Shows the complete issue lifecycle from creation to resolution:
- Creating issues with YAML frontmatter
- Listing and filtering issues
- Linking issues to PRs on resolution

## Example 03: Basic PR Workflow

**Concepts:** RunAgent, Push, CreatePR, full workflow

Demonstrates the `basic_pr` template pattern:
1. Create isolated worktree for the issue
2. Run agent to implement the feature
3. Commit, push, and create PR
4. Resolve the issue

## Example 04: Multi-Agent Workflow

**Concepts:** Gather, parallel execution, MergeBranches

Shows how to run multiple agents in parallel:
- `Gather` for parallel effect execution
- Implementation + testing agents working simultaneously
- Merging branches from parallel work
- Reviewer agent for final check

## Example 05: Custom Template

**Concepts:** Custom effects, quality gates, retry logic

Demonstrates building custom workflow templates:
- Defining custom effects (RunTests, RunLinter)
- Quality gates that must pass before PR creation
- Retry logic for flaky tests
- Conditional agent invocation

## Example 06: API Usage

**Concepts:** ConductorAPI, programmatic access, JSON output

Shows the high-level API for:
- Running workflows programmatically
- Listing and querying workflows
- Managing environments
- JSON output for scripting/integration

## Key Patterns

### Handler Setup

```python
from doeff import SyncRuntime
from doeff_conductor import WorktreeHandler, GitHandler

worktree_handler = WorktreeHandler(base_path=Path.cwd())
git_handler = GitHandler()

handlers = {
    CreateWorktree: lambda e: worktree_handler.handle_create_worktree(e),
    Commit: lambda e: git_handler.handle_commit(e),
}

runtime = SyncRuntime(handlers=handlers)
result = runtime.run(my_workflow())
```

### Using Templates

```python
from doeff_conductor import basic_pr, Issue

issue = Issue(id="ISSUE-001", title="Add feature", body="...")
program = basic_pr(issue)
result = runtime.run(program)
```

### Parallel Execution

```python
from doeff import Gather

@do
def parallel_work():
    # Run multiple effects in parallel
    result_a, result_b = yield Gather(
        CreateWorktree(suffix="a"),
        CreateWorktree(suffix="b"),
    )
```

## Next Steps

1. Read [docs/tutorial.md](../docs/tutorial.md) for a step-by-step guide
2. Check [docs/api.md](../docs/api.md) for full API reference
3. See [docs/migration-from-orch.md](../docs/migration-from-orch.md) if migrating from orch CLI
