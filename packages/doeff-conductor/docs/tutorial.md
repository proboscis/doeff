# doeff-conductor Tutorial

A step-by-step guide to using doeff-conductor for multi-agent workflow orchestration.

## Table of Contents

1. [Installation](#1-installation)
2. [First Workflow](#2-first-workflow)
3. [Using Templates](#3-using-templates)
4. [Using the CLI](#4-using-the-cli)
5. [Custom Workflows](#5-custom-workflows)
6. [Advanced: Multi-Agent DAGs](#6-advanced-multi-agent-dags)

---

## 1. Installation

### Requirements

- Python 3.10+
- Git (for worktree management)
- OpenCode or Claude CLI (for agent effects)

### Install

```bash
# Using pip
pip install doeff-conductor

# Using uv (recommended)
uv add doeff-conductor
```

### Verify Installation

```bash
# Check CLI is available
conductor --help

# List available templates
conductor template list
```

---

## 2. First Workflow

Let's create a minimal workflow that demonstrates the core concepts.

### Step 1: Create a Simple Workflow

```python
# my_first_workflow.py
from doeff import default_handlers, do, run
from doeff_conductor import (
    CreateWorktree,
    Commit,
    DeleteWorktree,
    WorktreeHandler,
    GitHandler,
    make_scheduled_handler,
    make_typed_handlers,
)
from pathlib import Path

@do
def hello_workflow():
    """A minimal workflow that creates a file."""
    # 1. Create an isolated worktree
    env = yield CreateWorktree(suffix="hello")
    print(f"Created worktree: {env.path}")
    
    # 2. Make changes (normal Python)
    (env.path / "hello.txt").write_text("Hello, conductor!")
    
    # 3. Commit changes
    yield Commit(env=env, message="Add hello.txt")
    print("Committed changes")
    
    # 4. Cleanup
    yield DeleteWorktree(env=env)
    print("Cleaned up")
    
    return "Done!"

# Set up handlers
worktree_handler = WorktreeHandler(base_path=Path.cwd())
git_handler = GitHandler()

handlers = {
    CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
    Commit: make_scheduled_handler(git_handler.handle_commit),
    DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
}

# Run the workflow
if __name__ == "__main__":
    result = run(
        hello_workflow(),
        handlers=[*make_typed_handlers(handlers), *default_handlers()],
    )
    print(f"Result: {result}")
```

### Step 2: Run the Workflow

```bash
python my_first_workflow.py
```

### Understanding the Code

1. **`@do` decorator**: Transforms a generator function into a doeff `Program`
2. **`yield Effect()`**: Execute an effect and get the result
3. **`WorktreeEnv`**: Handle to an isolated git worktree
4. **Handlers**: Map effects to actual implementations

---

## 3. Using Templates

doeff-conductor provides pre-built workflow templates.

### List Available Templates

```bash
conductor template list
```

Output:
```
NAME          DESCRIPTION
basic_pr      Basic PR workflow: issue -> agent -> PR
enforced_pr   Enforced PR workflow: issue -> agent -> test -> fix loop -> PR
reviewed_pr   Reviewed PR workflow: issue -> agent -> review -> PR
multi_agent   Multi-agent PR workflow: issue -> parallel agents -> merge -> PR
```

### View Template Source

```bash
conductor template show basic_pr
```

### Using a Template Programmatically

```python
from doeff import default_handlers, run
from doeff_conductor import Issue, IssueStatus, basic_pr, make_typed_handlers

# Create an issue
issue = Issue(
    id="ISSUE-001",
    title="Add user authentication",
    body="Implement JWT-based authentication",
    status=IssueStatus.OPEN,
)

# Use the template
program = basic_pr(issue)

# Run with handlers (see full handler setup in examples)
# handlers = {...}
result = run(
    program,
    handlers=[*make_typed_handlers(handlers), *default_handlers()],
)
print(f"Created PR: {result.value.url}")
```

---

## 4. Using the CLI

The `conductor` CLI provides a convenient interface for workflow management.

### Create an Issue

```bash
# Simple issue
conductor issue create "Add login feature"

# With body from file
conductor issue create "Add caching" --body @description.md

# With labels
conductor issue create "Fix bug" --labels bug,urgent
```

### Run a Workflow

```bash
# Run template with issue file
conductor run basic_pr --issue ISSUE-001.md

# Run and watch progress
conductor run basic_pr --issue ISSUE-001.md --watch

# Run custom workflow file
conductor run ./my_workflow.py --params '{"max_retries": 3}'
```

### Monitor Workflows

```bash
# List running workflows
conductor ps

# Show workflow details
conductor show abc123

# Watch real-time progress
conductor watch abc123

# View logs
conductor logs abc123
```

### Manage Issues

```bash
# List issues
conductor issue list --status open

# Show issue details
conductor issue show ISSUE-001

# Resolve issue
conductor issue resolve ISSUE-001 --pr https://github.com/.../pull/42
```

### JSON Output for Scripting

```bash
# All commands support --json
conductor ps --json | jq '.[] | select(.status == "running")'
conductor issue list --json | jq 'length'
```

---

## 5. Custom Workflows

### Creating a Custom Workflow

```python
from doeff import do
from doeff_conductor import (
    CreateWorktree,
    RunAgent,
    Commit,
    Push,
    CreatePR,
    Issue,
)

@do
def my_custom_workflow(issue: Issue, max_attempts: int = 3):
    """Custom workflow with retry logic."""
    
    # Create worktree
    env = yield CreateWorktree(issue=issue)
    
    # Implementation with retries
    for attempt in range(1, max_attempts + 1):
        print(f"Attempt {attempt}/{max_attempts}")
        
        # Run implementation agent
        yield RunAgent(
            env=env,
            prompt=f"Implement: {issue.title}\n\n{issue.body}",
            name="implementer",
        )
        
        # Run tests (custom logic)
        test_passed = run_tests(env.path)
        
        if test_passed:
            break
        elif attempt < max_attempts:
            # Run fix agent
            yield RunAgent(
                env=env,
                prompt="Fix the failing tests",
                name="fixer",
            )
    
    if not test_passed:
        raise RuntimeError("Tests failed after all attempts")
    
    # Create PR
    yield Commit(env=env, message=f"feat: {issue.title}")
    yield Push(env=env)
    pr = yield CreatePR(env=env, title=issue.title)
    
    return pr

def run_tests(path):
    """Run tests and return success status."""
    import subprocess
    result = subprocess.run(
        ["pytest", "--tb=short"],
        cwd=path,
        capture_output=True,
    )
    return result.returncode == 0
```

### Adding Custom Effects

```python
from dataclasses import dataclass
from doeff_conductor.effects.base import ConductorEffectBase

@dataclass(frozen=True, kw_only=True)
class RunLinter(ConductorEffectBase):
    """Custom effect to run a linter."""
    env: WorktreeEnv
    command: str = "ruff check"

# Handler for custom effect
def handle_run_linter(effect: RunLinter) -> dict:
    import subprocess
    result = subprocess.run(
        effect.command.split(),
        cwd=effect.env.path,
        capture_output=True,
        text=True,
    )
    return {
        "passed": result.returncode == 0,
        "output": result.stdout + result.stderr,
    }

# Add to handlers
handlers[RunLinter] = handle_run_linter
```

---

## 6. Advanced: Multi-Agent DAGs

### Parallel Execution with Gather

```python
from doeff import do, Gather
from doeff_conductor import CreateWorktree, RunAgent, MergeBranches

@do
def parallel_implementation(issue):
    """Run multiple agents in parallel."""
    
    # Create worktrees in parallel
    impl_env, test_env = yield Gather(
        CreateWorktree(issue=issue, suffix="impl"),
        CreateWorktree(issue=issue, suffix="tests"),
    )
    
    # Run agents in parallel
    impl_output, test_output = yield Gather(
        RunAgent(env=impl_env, prompt="Implement the feature"),
        RunAgent(env=test_env, prompt="Write tests for the feature"),
    )
    
    # Merge results
    merged = yield MergeBranches(envs=(impl_env, test_env))
    
    return merged
```

### Complex DAG Patterns

```python
@do
def diamond_dag(issue):
    """
    Diamond-shaped DAG:
    
         [start]
           / \
          /   \
      [impl] [docs]
          \   /
           \ /
         [merge]
           |
        [review]
    """
    # Start: create base worktree
    base = yield CreateWorktree(issue=issue)
    
    # Parallel: implementation and documentation
    impl_env, docs_env = yield Gather(
        CreateWorktree(issue=issue, suffix="impl"),
        CreateWorktree(issue=issue, suffix="docs"),
    )
    
    yield Gather(
        RunAgent(env=impl_env, prompt="Implement feature"),
        RunAgent(env=docs_env, prompt="Write documentation"),
    )
    
    # Merge point
    merged = yield MergeBranches(envs=(impl_env, docs_env))
    
    # Final review
    yield RunAgent(env=merged, prompt="Final review")
    
    return merged
```

### Event-Driven Patterns

```python
from doeff_conductor import SpawnAgent, WaitForStatus, CaptureOutput

@do
def interactive_workflow(issue):
    """Workflow with human-in-the-loop."""
    env = yield CreateWorktree(issue=issue)
    
    # Spawn agent (non-blocking)
    ref = yield SpawnAgent(
        env=env,
        prompt="Start implementing, ask for clarification if needed",
    )
    
    # Wait for agent to reach blocked state
    status = yield WaitForStatus(
        agent_ref=ref,
        target=(AgenticSessionStatus.BLOCKED, AgenticSessionStatus.DONE),
        timeout=300,
    )
    
    if status == AgenticSessionStatus.BLOCKED:
        # Agent needs input - could prompt human here
        output = yield CaptureOutput(agent_ref=ref)
        print(f"Agent needs help: {output}")
        
        # Continue with additional context
        yield SendMessage(
            agent_ref=ref,
            message="Use OAuth2 for authentication",
        )
    
    # Wait for completion
    yield WaitForStatus(
        agent_ref=ref,
        target=AgenticSessionStatus.DONE,
    )
    
    return env
```

---

## Next Steps

1. **Explore Examples**: See [examples/](../examples/) for more patterns
2. **API Reference**: Check [api.md](./api.md) for complete documentation
3. **Migration Guide**: If coming from orch CLI, see [migration-from-orch.md](./migration-from-orch.md)

## Getting Help

- **Issues**: Report bugs or request features on GitHub
- **Examples**: Find working examples in the `examples/` directory
- **API Docs**: Comprehensive API reference in `docs/api.md`
