# Migration Guide: orch CLI to doeff-conductor

This guide helps users of the Go `orch` CLI migrate to `doeff-conductor`.

## Overview

`doeff-conductor` is the Python successor to `orch`, providing:

- **Effect-based workflows**: Composable, testable workflow definitions
- **Type safety**: Full Python type hints with pyright support
- **Extensibility**: Easy to add custom effects and handlers
- **Integration**: Part of the doeff ecosystem with full effect support

## Command Comparison

### Workflow Execution

| orch | conductor | Notes |
|------|-----------|-------|
| `orch run <template>` | `conductor run <template>` | Same syntax |
| `orch run --issue FILE` | `conductor run --issue FILE` | Same syntax |
| `orch run --watch` | `conductor run --watch` | Same syntax |
| `orch ps` | `conductor ps` | Same syntax |
| `orch show <id>` | `conductor show <id>` | Same syntax |
| `orch watch <id>` | `conductor watch <id>` | Same syntax |
| `orch attach <id>` | `conductor attach <id>` | Same syntax |
| `orch stop <id>` | `conductor stop <id>` | Same syntax |
| `orch logs <id>` | `conductor logs <id>` | Same syntax |

### Issue Management

| orch | conductor | Notes |
|------|-----------|-------|
| `orch issue create <title>` | `conductor issue create <title>` | Same syntax |
| `orch issue list` | `conductor issue list` | Same syntax |
| `orch issue open <id>` | `conductor issue show <id>` | Renamed to `show` |
| `orch issue resolve <id>` | `conductor issue resolve <id>` | Same syntax |

### Environment Management

| orch | conductor | Notes |
|------|-----------|-------|
| `orch env list` | `conductor env list` | Same syntax |
| `orch env cleanup` | `conductor env cleanup` | Same syntax |

### Template Management

| orch | conductor | Notes |
|------|-----------|-------|
| `orch template list` | `conductor template list` | Same syntax |
| `orch template show <name>` | `conductor template show <name>` | Same syntax |
| `orch template new <name>` | See Python templates | Defined in Python |

## Feature Comparison

| Feature | orch | conductor |
|---------|------|-----------|
| Language | Go | Python |
| Startup time | ~5ms | ~100-300ms |
| Templates | YAML-based | Python functions |
| Custom effects | Limited | Fully extensible |
| Type checking | Runtime | Static (pyright) |
| Parallel agents | Supported | Supported via Gather |
| Human-in-loop | Supported | Supported |
| JSON output | `--json` | `--json` |
| State storage | `~/.local/state/orch/` | `~/.local/state/doeff-conductor/` |

## Migration Steps

### 1. Install doeff-conductor

```bash
uv add doeff-conductor
# or
pip install doeff-conductor
```

### 2. Update CLI Usage

Most commands are identical. The main changes:

```bash
# orch
orch issue open ISSUE-001

# conductor
conductor issue show ISSUE-001
```

### 3. Migrate Templates

#### orch YAML Template

```yaml
# ~/.orch/templates/my_workflow.yaml
name: my_workflow
steps:
  - create_worktree:
      issue: "{{ issue }}"
  - run_agent:
      prompt: "{{ issue.body }}"
  - commit:
      message: "feat: {{ issue.title }}"
  - push: {}
  - create_pr:
      title: "{{ issue.title }}"
```

#### conductor Python Template

```python
# my_workflow.py
from doeff import do
from doeff_conductor import CreateWorktree, RunAgent, Commit, Push, CreatePR

@do
def my_workflow(issue):
    env = yield CreateWorktree(issue=issue)
    yield RunAgent(env=env, prompt=issue.body)
    yield Commit(env=env, message=f"feat: {issue.title}")
    yield Push(env=env)
    pr = yield CreatePR(env=env, title=issue.title)
    return pr
```

### 4. Migrate State Directory (Optional)

If you have existing workflows in `~/.local/state/orch/`, you can either:

1. **Start fresh**: Let conductor create new state in `~/.local/state/doeff-conductor/`
2. **Copy state**: Copy relevant workflow metadata

```bash
# Create conductor state directory
mkdir -p ~/.local/state/doeff-conductor/

# Copy issues if using vault
cp -r ~/.orch/issues/* ~/.local/state/doeff-conductor/issues/
```

### 5. Update Scripts

#### orch Script

```bash
#!/bin/bash
orch issue create "New feature" --body @desc.md
orch run basic_pr --issue ISSUE-001.md --watch
```

#### conductor Script

```bash
#!/bin/bash
conductor issue create "New feature" --body @desc.md
conductor run basic_pr --issue ISSUE-001.md --watch
```

## New Capabilities in conductor

### 1. Python API

```python
from doeff_conductor import ConductorAPI

api = ConductorAPI()

# Run workflows programmatically
pr = api.run_workflow("basic_pr", issue=issue)

# Query workflows
running = api.list_workflows(status=[WorkflowStatus.RUNNING])
```

### 2. Custom Effects

```python
from dataclasses import dataclass
from doeff_conductor.effects.base import ConductorEffectBase

@dataclass(frozen=True, kw_only=True)
class MyCustomEffect(ConductorEffectBase):
    param: str

# Use in workflows
@do
def my_workflow():
    result = yield MyCustomEffect(param="value")
```

### 3. Type-Safe Workflows

```python
from doeff import do, EffectGenerator
from doeff_conductor import Issue, PRHandle

@do
def typed_workflow(issue: Issue) -> EffectGenerator[PRHandle]:
    # Type checker knows env is WorktreeEnv
    env = yield CreateWorktree(issue=issue)
    # Type checker knows pr is PRHandle
    pr = yield CreatePR(env=env, title=issue.title)
    return pr
```

### 4. Effect Composition

```python
from doeff import Gather

@do
def parallel_work():
    # Run multiple effects in parallel
    results = yield Gather(
        CreateWorktree(suffix="a"),
        CreateWorktree(suffix="b"),
        CreateWorktree(suffix="c"),
    )
```

## Breaking Changes

### Removed Commands

| orch | conductor | Alternative |
|------|-----------|-------------|
| `orch template new` | N/A | Create Python file |
| `orch config` | N/A | Use environment variables |

### Changed Defaults

| Behavior | orch | conductor |
|----------|------|-----------|
| State directory | `~/.local/state/orch/` | `~/.local/state/doeff-conductor/` |
| Template location | `~/.orch/templates/` | Python modules |
| Issue vault | `~/.orch/issues/` | Configurable |

### API Changes

```python
# orch (Go library)
wf := orch.NewWorkflow()
wf.RunAgent(prompt)

# conductor (Python)
@do
def workflow():
    yield RunAgent(env=env, prompt=prompt)
```

## Performance Considerations

| Aspect | orch | conductor |
|--------|------|-----------|
| CLI startup | ~5ms | ~100-300ms |
| Agent execution | Same | Same |
| Parallel operations | Thread-based | asyncio-capable |

For CLI-heavy scripts where startup time matters, consider:

1. Using the Python API directly instead of CLI
2. Batching operations
3. Using `--json` output for parsing instead of multiple calls

## Getting Help

- **Examples**: See `packages/doeff-conductor/examples/`
- **API Reference**: See `docs/api.md`
- **Tutorial**: See `docs/tutorial.md`
- **Issues**: Report on GitHub

## FAQ

### Q: Can I use conductor with existing orch workflows?

A: Not directly. Templates need to be rewritten in Python, but the concepts map directly.

### Q: Is conductor faster or slower than orch?

A: CLI startup is slower (~100ms vs ~5ms), but workflow execution speed is similar. For many workflows, the Python API avoids CLI overhead entirely.

### Q: Do I need to learn doeff to use conductor?

A: Basic usage requires minimal doeff knowledge. For custom workflows, understanding `@do` and `yield` is helpful. See the doeff documentation for details.

### Q: Can I mix orch and conductor?

A: Yes, they use separate state directories. You can migrate gradually.
