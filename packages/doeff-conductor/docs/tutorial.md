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
- Git (for workspace management)
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
from doeff import do
from doeff_conductor import (
    CreateWorkspace,
    Commit,
    DeleteWorkspace,
    Exec,
)
from doeff_conductor.handlers import mock_handlers, run_sync

@do
def hello_workflow():
    """A minimal workflow that creates a file."""
    # 1. Create an isolated workspace
    workspace = yield CreateWorkspace(workspace_id="tutorial-hello")
    print(f"Created workspace: {workspace.ref}")
    
    # 2. Make changes through Exec
    result = yield Exec(
        cmd="printf '%s\n' 'Hello, conductor!' > hello.txt",
        workspace=workspace,
    )
    if not result.passed:
        raise RuntimeError(result.log_path)
    
    # 3. Commit changes
    yield Commit(workspace=workspace, message="Add hello.txt")
    print("Committed changes")
    
    # 4. Cleanup
    yield DeleteWorkspace(workspace=workspace)
    print("Cleaned up")
    
    return "Done!"

# Run the workflow
if __name__ == "__main__":
    result = run_sync(hello_workflow(), scheduled_handlers=mock_handlers())
    if result.is_err():
        raise result.error
    print(f"Result: {result.value}")
```

### Step 2: Run the Workflow

```bash
python my_first_workflow.py
```

### Understanding the Code

1. **`@do` decorator**: Transforms a generator function into a doeff `Program`
2. **`yield Effect()`**: Execute an effect and get the result
3. **`Workspace`**: Handle to an isolated git workspace
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
from doeff_conductor import Issue, IssueStatus, basic_pr
from doeff_conductor.handlers import mock_handlers, run_sync

# Create an issue
issue = Issue(
    id="ISSUE-001",
    title="Add user authentication",
    body="Implement JWT-based authentication",
    status=IssueStatus.OPEN,
)

# Run with mock handlers (see examples for production handler setup)
result = run_sync(basic_pr(issue), scheduled_handlers=mock_handlers())
if result.is_err:
    raise result.error
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
conductor run ./my_workflow.hy --params '{"max_retries": 3}'
```

### Monitor Workflows

```bash
# List running workflows
conductor ps

# Show workflow details
conductor show abc123

# Watch real-time progress
conductor watch abc123

# Show progress events after a known sequence number
conductor show abc123 --since 10
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
# JSON-capable commands include ps, show, watch, issue list, and template list
conductor ps --json | jq '.[] | select(.status == "running")'
conductor issue list --json | jq 'length'
```

---

## 5. Custom Workflows

### Creating a Custom Workflow

```python
from doeff import do
from doeff_conductor import (
    Agent,
    AgentTask,
    CreateWorkspace,
    Exec,
    Commit,
    Push,
    CreatePR,
    Issue,
)

AGENT_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}

@do
def my_custom_workflow(issue: Issue, max_attempts: int = 3):
    """Custom workflow with retry logic."""
    
    # Create workspace
    workspace = yield CreateWorkspace(issue=issue, workspace_id=f"{issue.id.lower()}-impl")
    
    # Implementation with retries
    for attempt in range(1, max_attempts + 1):
        print(f"Attempt {attempt}/{max_attempts}")
        
        # Run implementation agent
        yield Agent(
            AgentTask(
                run_id=issue.id,
                node_id="implementer",
                attempt=attempt - 1,
                env=workspace,
                prompt=f"Implement: {issue.title}\n\n{issue.body}",
                result_schema=AGENT_SCHEMA,
                verification_class="test-verifiable",
                agent_type="codex",
                name="implementer",
            )
        )
        
        # Run tests through a deterministic Exec gate
        test_result = yield Exec(cmd="pytest --tb=short", workspace=workspace)
        test_passed = test_result.passed
        
        if test_passed:
            break
        elif attempt < max_attempts:
            # Run fix agent
            yield Agent(
                AgentTask(
                    run_id=issue.id,
                    node_id="fixer",
                    attempt=attempt - 1,
                    env=workspace,
                    prompt="Fix the failing tests",
                    result_schema=AGENT_SCHEMA,
                    verification_class="test-verifiable",
                    agent_type="codex",
                    name="fixer",
                )
            )
    
    if not test_passed:
        raise RuntimeError("Tests failed after all attempts")
    
    # Create PR
    yield Commit(workspace=workspace, message=f"feat: {issue.title}")
    yield Push(workspace=workspace)
    pr = yield CreatePR(workspace=workspace, title=issue.title)
    
    return pr
```

### Adding Custom Effects

```python
from dataclasses import dataclass
from doeff_conductor.effects.base import ConductorEffectBase

@dataclass(frozen=True, kw_only=True)
class RunLinter(ConductorEffectBase):
    """Custom effect to run a linter."""
    workspace: Workspace
    command: str = "ruff check"

# Handler for custom effect
def handle_run_linter(effect: RunLinter) -> dict:
    import subprocess
    result = subprocess.run(
        effect.command.split(),
        cwd=workspace_resolver(effect.workspace),
        capture_output=True,
        text=True,
    )
    return {
        "passed": result.returncode == 0,
        "output": result.stdout + result.stderr,
    }

# Add a new branch to your workflow handler
# if isinstance(effect, RunLinter):
#     return (yield run_linter_handler(effect, k))
```

---

## 6. Advanced: Multi-Agent DAGs

### Parallel Execution with Gather

```python
from doeff import do, Gather
from doeff_conductor import Agent, AgentTask, CreateWorkspace, MergeWorkspaces

AGENT_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}

@do
def parallel_implementation(issue):
    """Run multiple agents in parallel."""
    
    # Create workspaces in parallel
    impl_workspace, test_workspace = yield Gather(
        CreateWorkspace(issue=issue, workspace_id=f"{issue.id.lower()}-impl"),
        CreateWorkspace(issue=issue, workspace_id=f"{issue.id.lower()}-tests"),
    )
    
    # Run agents in parallel
    impl_output, test_output = yield Gather(
        Agent(AgentTask(
            run_id=issue.id,
            node_id="implement",
            attempt=0,
            env=impl_workspace,
            prompt="Implement the feature",
            result_schema=AGENT_SCHEMA,
            verification_class="test-verifiable",
            agent_type="codex",
        )),
        Agent(AgentTask(
            run_id=issue.id,
            node_id="tests",
            attempt=0,
            env=test_workspace,
            prompt="Write tests for the feature",
            result_schema=AGENT_SCHEMA,
            verification_class="test-verifiable",
            agent_type="codex",
        )),
    )
    
    # Merge results
    merge_result = yield MergeWorkspaces(
        workspace_id=f"{issue.id.lower()}-merged",
        workspaces=(impl_workspace, test_workspace),
    )
    if not merge_result.merged or merge_result.workspace is None:
        raise RuntimeError(merge_result.message)
    merged = merge_result.workspace
    
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
    # Start: create base workspace
    base = yield CreateWorkspace(issue=issue, workspace_id=f"{issue.id.lower()}-base")
    
    # Parallel: implementation and documentation
    impl_workspace, docs_workspace = yield Gather(
        CreateWorkspace(issue=issue, workspace_id=f"{issue.id.lower()}-impl"),
        CreateWorkspace(issue=issue, workspace_id=f"{issue.id.lower()}-docs"),
    )
    
    yield Gather(
        Agent(AgentTask(
            run_id=issue.id,
            node_id="implement",
            attempt=0,
            env=impl_workspace,
            prompt="Implement feature",
            result_schema=AGENT_SCHEMA,
            verification_class="test-verifiable",
            agent_type="codex",
        )),
        Agent(AgentTask(
            run_id=issue.id,
            node_id="docs",
            attempt=0,
            env=docs_workspace,
            prompt="Write documentation",
            result_schema=AGENT_SCHEMA,
            verification_class="test-verifiable",
            agent_type="codex",
        )),
    )
    
    # Merge point
    merge_result = yield MergeWorkspaces(
        workspace_id=f"{issue.id.lower()}-merged",
        workspaces=(impl_workspace, docs_workspace),
    )
    if not merge_result.merged or merge_result.workspace is None:
        raise RuntimeError(merge_result.message)
    merged = merge_result.workspace
    
    # Final review
    yield Agent(AgentTask(
        run_id=issue.id,
        node_id="review",
        attempt=0,
        env=merged,
        prompt="Final review",
        result_schema=AGENT_SCHEMA,
        verification_class="review",
        agent_type="codex",
    ))
    
    return merged
```

### Agent Boundary

Conductor workflows use `Agent(AgentTask(...))` as a completion boundary and
receive a schema-validated artifact. Interactive session controls live below the
conductor workflow effect layer.

---

## Next Steps

1. **Explore Examples**: See [examples/](../examples/) for more patterns
2. **API Reference**: Check [api.md](./api.md) for complete documentation
3. **Migration Guide**: If coming from orch CLI, see [migration-from-orch.md](./migration-from-orch.md)

## Getting Help

- **Issues**: Report bugs or request features on GitHub
- **Examples**: Find working examples in the `examples/` directory
- **API Docs**: Comprehensive API reference in `docs/api.md`
