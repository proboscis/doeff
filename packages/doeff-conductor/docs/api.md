# doeff-conductor API Reference

Complete API documentation for doeff-conductor.

## Table of Contents

- [Types](#types)
- [Effects](#effects)
- [Handlers](#handlers)
- [Templates](#templates)
- [ConductorAPI](#conductorapi)
- [CLI Commands](#cli-commands)

---

## Types

### Enums

#### `IssueStatus`

Status of an issue in the vault.

```python
from doeff_conductor import IssueStatus

IssueStatus.OPEN          # New issue
IssueStatus.IN_PROGRESS   # Being worked on
IssueStatus.RESOLVED      # Completed
IssueStatus.CLOSED        # Closed without resolution
```

#### `WorkflowStatus`

Status of a conductor workflow.

```python
from doeff_conductor import WorkflowStatus

WorkflowStatus.PENDING    # Not yet started
WorkflowStatus.RUNNING    # In progress
WorkflowStatus.BLOCKED    # Waiting for input
WorkflowStatus.DONE       # Completed successfully
WorkflowStatus.ERROR      # Failed with error
WorkflowStatus.ABORTED    # Manually stopped

# Check if terminal
status.is_terminal()  # True for DONE, ERROR, ABORTED
```

#### `MergeStrategy`

Strategy for merging branches.

```python
from doeff_conductor import MergeStrategy

MergeStrategy.MERGE     # Standard merge commit
MergeStrategy.REBASE    # Rebase onto target
MergeStrategy.SQUASH    # Squash all commits
```

### Data Classes

#### `Issue`

Issue from the vault with YAML frontmatter.

```python
from doeff_conductor import Issue, IssueStatus

issue = Issue(
    id="ISSUE-001",
    title="Add login feature",
    body="## Description\nImplement OAuth2 login...",
    status=IssueStatus.OPEN,
    labels=("feature", "auth"),
    created_at=datetime.now(timezone.utc),
    metadata={"priority": "high"},
)

# Serialization
issue.to_dict()  # -> dict
Issue.from_dict(data)  # -> Issue
```

#### `WorktreeEnv`

Handle to a git worktree environment.

```python
from doeff_conductor import WorktreeEnv

env = WorktreeEnv(
    id="wt-001",
    path=Path("/path/to/worktree"),
    branch="feat/issue-001",
    base_commit="abc1234",
    issue_id="ISSUE-001",
)

# Access path for file operations
file_path = env.path / "src" / "file.py"
```

#### `AgentRef`

Reference to a running agent session.

```python
from doeff_conductor import AgentRef

ref = AgentRef(
    id="session-abc123",
    name="implementer",
    workflow_id="wf-001",
    env_id="wt-001",
    agent_type="claude",
)
```

#### `PRHandle`

Handle to a pull request.

```python
from doeff_conductor import PRHandle

pr = PRHandle(
    url="https://github.com/owner/repo/pull/42",
    number=42,
    title="Add login feature",
    branch="feat/issue-001",
    target="main",
    status="open",
)
```

#### `WorkflowHandle`

Handle to a conductor workflow instance.

```python
from doeff_conductor import WorkflowHandle, WorkflowStatus

handle = WorkflowHandle(
    id="abc1234",
    name="basic_pr",
    status=WorkflowStatus.RUNNING,
    template="basic_pr",
    issue_id="ISSUE-001",
    environments=("wt-001", "wt-002"),
    agents=("implementer", "reviewer"),
)
```

---

## Effects

### Worktree Effects

#### `CreateWorktree`

Create a new git worktree environment.

```python
from doeff_conductor import CreateWorktree

# Basic usage
env = yield CreateWorktree()

# With issue
env = yield CreateWorktree(issue=issue)

# With custom branch suffix
env = yield CreateWorktree(issue=issue, suffix="impl")

# With specific base branch
env = yield CreateWorktree(base_branch="develop")
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `issue` | `Issue \| None` | `None` | Issue to create worktree for |
| `base_branch` | `str \| None` | `None` | Base branch (default: main/master) |
| `suffix` | `str \| None` | `None` | Branch suffix for parallel worktrees |
| `name` | `str \| None` | `None` | Custom worktree name |

**Returns:** `WorktreeEnv`

#### `MergeBranches`

Merge multiple worktree branches together.

```python
from doeff_conductor import MergeBranches, MergeStrategy

# Basic merge
merged = yield MergeBranches(envs=(env1, env2))

# With strategy
merged = yield MergeBranches(
    envs=(env1, env2, env3),
    strategy=MergeStrategy.SQUASH,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `envs` | `tuple[WorktreeEnv, ...]` | Required | Worktrees to merge |
| `strategy` | `MergeStrategy \| None` | `None` | Merge strategy |
| `name` | `str \| None` | `None` | Name for merged worktree |

**Returns:** `WorktreeEnv`

#### `DeleteWorktree`

Delete a worktree and clean up resources.

```python
from doeff_conductor import DeleteWorktree

# Normal cleanup
yield DeleteWorktree(env=env)

# Force delete (ignores uncommitted changes)
yield DeleteWorktree(env=env, force=True)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `env` | `WorktreeEnv` | Required | Worktree to delete |
| `force` | `bool` | `False` | Force delete even if uncommitted |

**Returns:** `bool`

---

### Issue Effects

#### `CreateIssue`

Create a new issue in the vault.

```python
from doeff_conductor import CreateIssue

issue = yield CreateIssue(
    title="Add caching layer",
    body="## Description\nImplement Redis caching...",
    labels=("feature", "performance"),
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `title` | `str` | Required | Issue title |
| `body` | `str` | Required | Issue body (markdown) |
| `labels` | `tuple[str, ...]` | `()` | Issue labels |
| `metadata` | `dict \| None` | `None` | Additional metadata |

**Returns:** `Issue`

#### `ListIssues`

List issues from the vault with optional filters.

```python
from doeff_conductor import ListIssues, IssueStatus

# List all open issues
issues = yield ListIssues(status=IssueStatus.OPEN)

# Filter by labels
issues = yield ListIssues(labels=("feature",))

# Limit results
issues = yield ListIssues(limit=10)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `status` | `IssueStatus \| None` | `None` | Filter by status |
| `labels` | `tuple[str, ...]` | `()` | Filter by labels (any match) |
| `limit` | `int \| None` | `None` | Max issues to return |

**Returns:** `list[Issue]`

#### `GetIssue`

Get an issue by ID.

```python
from doeff_conductor import GetIssue

issue = yield GetIssue(id="ISSUE-001")
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `id` | `str` | Required | Issue ID |

**Returns:** `Issue`

**Raises:** `IssueNotFoundError` if issue doesn't exist

#### `ResolveIssue`

Mark an issue as resolved.

```python
from doeff_conductor import ResolveIssue

resolved = yield ResolveIssue(
    issue=issue,
    pr_url="https://github.com/owner/repo/pull/42",
    result="Implemented OAuth2 login",
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `issue` | `Issue` | Required | Issue to resolve |
| `pr_url` | `str \| None` | `None` | Associated PR URL |
| `result` | `str \| None` | `None` | Resolution summary |

**Returns:** `Issue` (updated)

---

### Agent Effects

#### `RunAgent`

Run an agent to completion.

```python
from doeff_conductor import RunAgent

output = yield RunAgent(
    env=env,
    prompt="Implement the login feature",
    agent_type="claude",
    name="implementer",
    timeout=300,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `env` | `WorktreeEnv` | Required | Environment to run in |
| `prompt` | `str` | Required | Initial prompt |
| `agent_type` | `str` | `"claude"` | Agent type |
| `name` | `str \| None` | `None` | Session name |
| `profile` | `str \| None` | `None` | Agent profile |
| `timeout` | `float \| None` | `None` | Timeout in seconds |

**Returns:** `str` (agent output)

#### `SpawnAgent`

Start an agent without waiting for completion.

```python
from doeff_conductor import SpawnAgent

ref = yield SpawnAgent(
    env=env,
    prompt="Review the code",
    name="reviewer",
)
# Continue immediately, agent runs in background
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `env` | `WorktreeEnv` | Required | Environment to run in |
| `prompt` | `str` | Required | Initial prompt |
| `agent_type` | `str` | `"claude"` | Agent type |
| `name` | `str \| None` | `None` | Session name |
| `profile` | `str \| None` | `None` | Agent profile |

**Returns:** `AgentRef`

#### `SendMessage`

Send a message to a running agent.

```python
from doeff_conductor import SendMessage

yield SendMessage(
    agent_ref=ref,
    message="Continue with step 2",
    wait=True,  # Wait for response
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `agent_ref` | `AgentRef` | Required | Agent to message |
| `message` | `str` | Required | Message content |
| `wait` | `bool` | `False` | Wait for response |

**Returns:** `None`

#### `WaitForStatus`

Wait for an agent to reach a specific status.

```python
from doeff_conductor import WaitForStatus
from doeff_agentic import AgenticSessionStatus

status = yield WaitForStatus(
    agent_ref=ref,
    target=AgenticSessionStatus.DONE,
    timeout=300,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `agent_ref` | `AgentRef` | Required | Agent to wait for |
| `target` | `Status \| tuple[Status]` | Required | Target status(es) |
| `timeout` | `float \| None` | `None` | Timeout in seconds |
| `poll_interval` | `float` | `1.0` | Poll interval |

**Returns:** `AgenticSessionStatus`

#### `CaptureOutput`

Capture output from an agent session.

```python
from doeff_conductor import CaptureOutput

output = yield CaptureOutput(
    agent_ref=ref,
    lines=500,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `agent_ref` | `AgentRef` | Required | Agent to capture from |
| `lines` | `int` | `500` | Number of lines |

**Returns:** `str`

---

### Git Effects

#### `Commit`

Create a commit in the worktree.

```python
from doeff_conductor import Commit

sha = yield Commit(
    env=env,
    message="feat: add login feature",
    all=True,  # Stage all changes
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `env` | `WorktreeEnv` | Required | Worktree to commit in |
| `message` | `str` | Required | Commit message |
| `all` | `bool` | `True` | Stage all changes |

**Returns:** `str` (commit SHA)

#### `Push`

Push branch to remote.

```python
from doeff_conductor import Push

yield Push(
    env=env,
    remote="origin",
    force=False,
    set_upstream=True,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `env` | `WorktreeEnv` | Required | Worktree to push |
| `remote` | `str` | `"origin"` | Remote name |
| `force` | `bool` | `False` | Force push |
| `set_upstream` | `bool` | `True` | Set upstream |

**Returns:** `bool`

#### `CreatePR`

Create a pull request.

```python
from doeff_conductor import CreatePR

pr = yield CreatePR(
    env=env,
    title="Add login feature",
    body="Implements OAuth2 login",
    target="main",
    draft=False,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `env` | `WorktreeEnv` | Required | Worktree with changes |
| `title` | `str` | Required | PR title |
| `body` | `str \| None` | `None` | PR body |
| `target` | `str` | `"main"` | Target branch |
| `draft` | `bool` | `False` | Create as draft |

**Returns:** `PRHandle`

#### `MergePR`

Merge a pull request.

```python
from doeff_conductor import MergePR, MergeStrategy

yield MergePR(
    pr=pr,
    strategy=MergeStrategy.SQUASH,
    delete_branch=True,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `pr` | `PRHandle` | Required | PR to merge |
| `strategy` | `MergeStrategy \| None` | `None` | Merge strategy |
| `delete_branch` | `bool` | `True` | Delete source branch |

**Returns:** `bool`

---

## Handlers

### Handler Classes

#### `WorktreeHandler`

Handles worktree effects.

```python
from doeff_conductor import WorktreeHandler

handler = WorktreeHandler(base_path=Path("/path/to/repo"))

# Methods
handler.handle_create_worktree(effect)
handler.handle_merge_branches(effect)
handler.handle_delete_worktree(effect)
```

#### `IssueHandler`

Handles issue effects.

```python
from doeff_conductor import IssueHandler

handler = IssueHandler(issues_dir=Path("/path/to/issues"))

# Methods
handler.handle_create_issue(effect)
handler.handle_list_issues(effect)
handler.handle_get_issue(effect)
handler.handle_resolve_issue(effect)
```

#### `AgentHandler`

Handles agent effects.

```python
from doeff_conductor import AgentHandler

handler = AgentHandler()

# Methods
handler.handle_run_agent(effect)
handler.handle_spawn_agent(effect)
handler.handle_send_message(effect)
handler.handle_wait_for_status(effect)
handler.handle_capture_output(effect)
```

#### `GitHandler`

Handles git effects.

```python
from doeff_conductor import GitHandler

handler = GitHandler()

# Methods
handler.handle_commit(effect)
handler.handle_push(effect)
handler.handle_create_pr(effect)
handler.handle_merge_pr(effect)
```

### Handler Utilities

```python
from doeff_conductor import (
    make_scheduled_handler,
    make_async_scheduled_handler,
    make_blocking_scheduled_handler,
    default_scheduled_handlers,
)

# Wrap sync handler for scheduled_handlers API
handler = make_scheduled_handler(sync_function)

# Get default handlers
handlers = default_scheduled_handlers()
```

---

## Templates

### Available Templates

```python
from doeff_conductor import (
    basic_pr,
    enforced_pr,
    reviewed_pr,
    multi_agent,
)
```

| Template | Description |
|----------|-------------|
| `basic_pr` | issue -> agent -> PR |
| `enforced_pr` | issue -> agent -> test -> fix loop -> PR |
| `reviewed_pr` | issue -> agent -> review -> PR |
| `multi_agent` | issue -> parallel agents -> merge -> PR |

### Template Utilities

```python
from doeff_conductor import (
    is_template,
    get_template,
    get_available_templates,
    get_template_source,
)

# Check if name is a template
is_template("basic_pr")  # True

# Get template function
func = get_template("basic_pr")

# List all templates
templates = get_available_templates()
# {"basic_pr": "Basic PR workflow...", ...}

# Get source code
source = get_template_source("basic_pr")
```

---

## ConductorAPI

High-level API for programmatic access.

```python
from doeff_conductor import ConductorAPI

api = ConductorAPI(state_dir="/path/to/state")  # Optional
```

### Methods

#### `run_workflow`

Run a workflow template or file.

```python
handle = api.run_workflow(
    "basic_pr",
    issue=issue,
    params={"max_retries": 3},
)
```

#### `list_workflows`

List workflows with optional status filter.

```python
workflows = api.list_workflows(
    status=[WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED]
)
```

#### `get_workflow`

Get workflow by ID or prefix.

```python
workflow = api.get_workflow("abc123")
# Also works with prefix: api.get_workflow("abc")
```

#### `watch_workflow`

Watch workflow progress (generator).

```python
for update in api.watch_workflow("abc123"):
    print(update["status"])
    if update["terminal"]:
        break
```

#### `stop_workflow`

Stop a workflow or specific agent.

```python
stopped = api.stop_workflow("abc123")
# Or specific agent
stopped = api.stop_workflow("abc123", agent="implementer")
```

#### `list_environments`

List worktree environments.

```python
environments = api.list_environments()
# Filter by workflow
environments = api.list_environments(workflow_id="abc123")
```

#### `cleanup_environments`

Cleanup orphaned worktree environments.

```python
# Dry run
would_clean = api.cleanup_environments(dry_run=True)

# Actually clean
cleaned = api.cleanup_environments(older_than_days=7)
```

---

## CLI Commands

### Workflow Commands

```bash
# Run workflow
conductor run <template|file> [--issue FILE] [--params JSON] [--watch] [--json]

# List workflows
conductor ps [--status STATUS] [--json]

# Show workflow details
conductor show <workflow-id> [--json]

# Watch workflow progress
conductor watch <workflow-id> [--json]

# Stop workflow
conductor stop <workflow-id> [--agent NAME] [--json]
```

### Issue Commands

```bash
# Create issue
conductor issue create <title> [--body STRING|@FILE] [--labels L1,L2] [--json]

# List issues
conductor issue list [--status STATUS] [--labels LABELS] [--json]

# Show issue
conductor issue show <id> [--json]

# Resolve issue
conductor issue resolve <id> [--pr URL] [--json]
```

### Environment Commands

```bash
# List environments
conductor env list [--workflow ID] [--json]

# Cleanup environments
conductor env cleanup [--dry-run] [--older-than DAYS] [--json]
```

### Template Commands

```bash
# List templates
conductor template list [--json]

# Show template source
conductor template show <name>
```
