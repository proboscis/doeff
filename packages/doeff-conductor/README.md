# doeff-conductor

Multi-agent workflow orchestration for doeff with issue-driven development, git workspace management, and DAG execution.

## Overview

doeff-conductor provides a unified orchestration layer combining:

- **Issue-driven agent workflows**: Create issues, dispatch agents, track progress
- **Git workspace management**: Isolated workspaces for each agent
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
from doeff_conductor import Agent, AgentTask, CreateWorkspace, CreatePR

AGENT_SCHEMA = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}

@do
def basic_pr(issue):
    # Create isolated workspace for the issue
    workspace = yield CreateWorkspace(issue=issue)
    
    # Run agent to implement the issue
    yield Agent(AgentTask(
        run_id=issue.id,
        node_id="implement",
        attempt=0,
        env=workspace,
        prompt=issue.body,
        result_schema=AGENT_SCHEMA,
        verification_class="test-verifiable",
        agent_type="codex",
    ))
    
    # Create PR with changes
    pr = yield CreatePR(workspace=workspace, title=issue.title)
    return pr
```

### Multi-Agent Workflow

```python
from doeff import do, Gather
from doeff_conductor import Agent, AgentTask, CreateWorkspace, MergeWorkspaces

AGENT_SCHEMA = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}

@do
def multi_agent_pr(issue):
    # Create parallel workspaces
    impl_workspace, test_workspace = yield Gather(
        CreateWorkspace(issue=issue, suffix="impl"),
        CreateWorkspace(issue=issue, suffix="tests"),
    )
    
    # Run agents in parallel
    yield Gather(
        Agent(AgentTask(
            run_id=issue.id,
            node_id="implement",
            attempt=0,
            env=impl_workspace,
            prompt=issue.body,
            result_schema=AGENT_SCHEMA,
            verification_class="test-verifiable",
            agent_type="codex",
        )),
        Agent(AgentTask(
            run_id=issue.id,
            node_id="tests",
            attempt=0,
            env=test_workspace,
            prompt=f"Write tests for: {issue.body}",
            result_schema=AGENT_SCHEMA,
            verification_class="test-verifiable",
            agent_type="codex",
        )),
    )
    
    # Reconcile workspaces
    merge_result = yield MergeWorkspaces(workspaces=(impl_workspace, test_workspace))
    if not merge_result.merged or merge_result.workspace is None:
        raise RuntimeError(merge_result.message)
    merged_workspace = merge_result.workspace
    
    # Review and create PR
    yield Agent(AgentTask(
        run_id=issue.id,
        node_id="review",
        attempt=0,
        env=merged_workspace,
        prompt="Review and create PR",
        result_schema=AGENT_SCHEMA,
        verification_class="review",
        agent_type="codex",
    ))
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

# Workspace
conductor workspace list [--workflow ID]
conductor workspace cleanup [--dry-run] [--older-than DAYS]

# Templates
conductor template list
conductor template show <name>
conductor template new <name>
```

## Effects Catalog

### Workspace Effects

| Effect | Description |
|--------|-------------|
| `CreateWorkspace(issue?, from_ref?, suffix?)` | Create git workspace |
| `MergeWorkspaces(workspaces, strategy?)` | Reconcile multiple workspaces |
| `DeleteWorkspace(workspace)` | Cleanup workspace |

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
| `Agent(AgentTask(...))` | Run an agent to completion and return a schema-validated artifact |

### Git Effects

| Effect | Description |
|--------|-------------|
| `Commit(workspace, message)` | Create commit |
| `Push(workspace, remote?, force?)` | Push branch |
| `CreatePR(workspace, title, body, target?)` | Create pull request |
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
|  - workspace list/cleanup                                           |
|  - template list/show/run                                           |
+---------------------------------------------------------------------+
|  Effects                                                             |
|  +----------+ +----------+ +----------+ +----------+                |
|  | Workspace | |  Issue   | |  Agent   | |   Git    |                |
|  | Create   | | Create   | | Agent    | | Commit   |                |
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

## Testing

doeff-conductor includes comprehensive tests at multiple levels:

### Test Categories

| Test Type | Description | Requirements |
|-----------|-------------|--------------|
| Unit tests | Handler logic with mocks | None |
| Integration tests | Workflow execution | git |
| E2E tests (mock) | HTTP API interaction | None |
| E2E tests (real) | Full agent pipeline | OpenCode server |

### Running Tests

```bash
# Run all unit and mock-based tests
uv run pytest packages/doeff-conductor/tests/

# Run E2E tests (requires CONDUCTOR_E2E=1)
CONDUCTOR_E2E=1 uv run pytest packages/doeff-conductor/tests/

# Run only tests requiring OpenCode
CONDUCTOR_E2E=1 uv run pytest -m requires_opencode
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `CONDUCTOR_E2E` | Set to `1` to enable E2E tests |
| `CONDUCTOR_OPENCODE_URL` | OpenCode server URL (auto-detected if not set) |

See `tests/README.md` for detailed testing documentation.

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

## Documentation

- **[Examples](./examples/)** - Progressive examples from basic to advanced
- **[API Reference](./docs/api.md)** - Complete API documentation
- **[Tutorial](./docs/tutorial.md)** - Step-by-step getting started guide
- **[Migration from orch](./docs/migration-from-orch.md)** - Guide for orch CLI users

## Examples

| Example | Description |
|---------|-------------|
| [01_hello_workflow.py](./examples/01_hello_workflow.py) | Minimal workflow |
| [02_issue_lifecycle.py](./examples/02_issue_lifecycle.py) | Issue management |
| [03_basic_pr_workflow.py](./examples/03_basic_pr_workflow.py) | Complete PR workflow |
| [04_multi_agent.py](./examples/04_multi_agent.py) | Parallel agents |
| [05_custom_template.py](./examples/05_custom_template.py) | Custom templates |
| [06_api_usage.py](./examples/06_api_usage.py) | ConductorAPI usage |

## License

MIT
