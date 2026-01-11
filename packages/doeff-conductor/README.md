# doeff-conductor

Multi-agent workflow orchestration for doeff with issue-driven development, git worktree management, and DAG execution.

## Overview

doeff-conductor provides a unified orchestration layer combining:

- **Issue-driven agent workflows**: Create issues, dispatch agents, track progress
- **Git worktree management**: Isolated environments for each agent
- **Multi-agent DAG execution**: Parallel agents with merge points
- **Full CLI for monitoring and control**: run, ps, watch, attach, logs, stop

## Installation

```bash
pip install doeff-conductor
# or
uv add doeff-conductor
```

## Quick Start

### Basic PR Workflow

```python
from doeff import do
from doeff_conductor import CreateWorktree, RunAgent, CreatePR

@do
def basic_pr(issue):
    # Create isolated worktree for the issue
    env = yield CreateWorktree(issue=issue)
    
    # Run agent to implement the issue
    yield RunAgent(env=env, prompt=issue.body)
    
    # Create PR with changes
    pr = yield CreatePR(env=env, title=issue.title)
    return pr
```

### Multi-Agent Workflow

```python
from doeff import do
from doeff_conductor import CreateWorktree, RunAgent, MergeBranches, Gather

@do
def multi_agent_pr(issue):
    # Create parallel worktrees
    impl_env, test_env = yield Gather([
        CreateWorktree(issue=issue, suffix="impl"),
        CreateWorktree(issue=issue, suffix="tests"),
    ])
    
    # Run agents in parallel
    yield Gather([
        RunAgent(env=impl_env, prompt=issue.body),
        RunAgent(env=test_env, prompt=f"Write tests for: {issue.body}"),
    ])
    
    # Merge branches
    merged_env = yield MergeBranches([impl_env, test_env])
    
    # Review and create PR
    yield RunAgent(env=merged_env, prompt="Review and create PR")
```

## CLI Commands

```bash
# Workflow execution
conductor run <template|file> [--issue FILE] [--params JSON] [--watch]
conductor ps [--status running|blocked|done]
conductor show <workflow-id>
conductor watch <workflow-id> [--agent NAME]
conductor attach <workflow-id>[:<agent>]
conductor send <workflow-id>:<agent> <message>
conductor stop <workflow-id> [--agent NAME]
conductor logs <workflow-id>[:<agent>] [-f] [-n LINES]

# Issue management  
conductor issue create <title> [--body FILE|STRING] [--labels L1,L2]
conductor issue list [--status open|resolved|all]
conductor issue show <id>
conductor issue resolve <id> [--pr URL]

# Environment
conductor env list [--workflow ID]
conductor env cleanup [--dry-run] [--older-than DAYS]

# Templates
conductor template list
conductor template show <name>
conductor template new <name>
```

## Effects Catalog

### Worktree Effects

| Effect | Description |
|--------|-------------|
| `CreateWorktree(issue?, base_branch?, suffix?)` | Create git worktree |
| `MergeBranches([envs], strategy?)` | Merge multiple branches |
| `DeleteWorktree(env)` | Cleanup worktree |

### Issue Effects

| Effect | Description |
|--------|-------------|
| `CreateIssue(title, body, labels?)` | Create issue in vault |
| `ListIssues(status?, labels?)` | List issues |
| `GetIssue(id)` | Get issue by ID |
| `ResolveIssue(issue, result?)` | Mark issue resolved |

### Agent Effects

| Effect | Description |
|--------|-------------|
| `RunAgent(type, env, prompt, output_format?)` | Run agent to completion |
| `SpawnAgent(type, env, prompt)` | Start agent, don't wait |
| `SendMessage(agent_ref, message)` | Send to running agent |
| `WaitForStatus(agent_ref, status, timeout?)` | Wait for agent state |
| `CaptureOutput(agent_ref, lines?)` | Get agent output |

### Git Effects

| Effect | Description |
|--------|-------------|
| `Commit(env, message)` | Create commit |
| `Push(env, remote?, force?)` | Push branch |
| `CreatePR(env, title, body, target?)` | Create pull request |
| `MergePR(pr, strategy?)` | Merge PR |

## Templates

Pre-built workflow templates:

| Template | Description |
|----------|-------------|
| `basic_pr` | issue -> agent -> PR |
| `enforced_pr` | issue -> agent -> test -> fix loop -> PR |
| `reviewed_pr` | issue -> agent -> review -> PR |
| `multi_agent` | issue -> parallel agents -> merge -> PR |

## Architecture

```
+---------------------------------------------------------------------+
|                         doeff-conductor                              |
+---------------------------------------------------------------------+
|  CLI Layer                                                           |
|  - run, ps, show, watch, attach, stop, logs                         |
|  - issue create/list/show/resolve                                   |
|  - env list/cleanup                                                 |
|  - template list/show/run                                           |
+---------------------------------------------------------------------+
|  Effects                                                             |
|  +----------+ +----------+ +----------+ +----------+                |
|  | Worktree | |  Issue   | |  Agent   | |   Git    |                |
|  | Create   | | Create   | | RunAgent | | Commit   |                |
|  | Merge    | | List     | | Send     | | Push     |                |
|  | Delete   | | Resolve  | | Capture  | | CreatePR |                |
|  +----------+ +----------+ +----------+ +----------+                |
+---------------------------------------------------------------------+
|  Dependencies                                                        |
|  - doeff-agentic (session management, agent adapters)               |
|  - doeff-flow (trace observability)                                 |
|  - doeff (core effects, @do, run_sync)                              |
+---------------------------------------------------------------------+
```

## State Storage

Workflow state is stored at `~/.local/state/doeff-conductor/`:

```
~/.local/state/doeff-conductor/
├── workflows/
│   ├── wf-abc123/
│   │   ├── meta.json
│   │   ├── trace.jsonl
│   │   └── agents/
├── issues/
│   ├── ISSUE-001.md
│   └── ISSUE-002.md
└── index.json
```

## License

MIT
