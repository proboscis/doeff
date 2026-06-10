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

#### `Workspace`

Portable handle to a logical git workspace.

```python
from doeff_conductor import Workspace

workspace = Workspace(
    id="workspace-001",
    repo="default",
    ref="feat/issue-001",
    base_ref="main",
    issue_id="ISSUE-001",
)
```

Workspace materialization paths are handler-private. Use `Exec(workspace=...)` or
handler-specific APIs for filesystem work.

#### `AgentRef`

Reference to a running agent session.

```python
from doeff_conductor import AgentRef

ref = AgentRef(
    id="session-abc123",
    name="implementer",
    workflow_id="wf-001",
    workspace_id="wt-001",
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
    workspaces=("wt-001", "wt-002"),
    agents=("implementer", "reviewer"),
)
```

---

## Effects

### Workspace Effects

#### `CreateWorkspace`

Create a new git workspace environment.

```python
from doeff_conductor import CreateWorkspace

# Basic usage
workspace = yield CreateWorkspace()

# With issue
workspace = yield CreateWorkspace(issue=issue)

# With custom branch suffix
workspace = yield CreateWorkspace(issue=issue, suffix="impl")

# With specific base ref
workspace = yield CreateWorkspace(from_ref="develop")
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `issue` | `Issue \| None` | `None` | Issue to create workspace for |
| `from_ref` | `str \| None` | `None` | Base ref (default: main/master) |
| `suffix` | `str \| None` | `None` | Branch suffix for parallel workspaces |
| `name` | `str \| None` | `None` | Custom workspace name |

**Returns:** `Workspace`

#### `MergeWorkspaces`

Reconcile multiple workspaces.

```python
from doeff_conductor import MergeWorkspaces, MergeStrategy

# Basic merge
merge_result = yield MergeWorkspaces(workspaces=(workspace1, workspace2))
if not merge_result.merged:
    raise RuntimeError(merge_result.message)
merged = merge_result.workspace

# With strategy
merged = yield MergeWorkspaces(
    workspaces=(workspace1, workspace2, workspace3),
    strategy=MergeStrategy.SQUASH,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `workspaces` | `tuple[Workspace, ...]` | Required | Workspaces to reconcile |
| `strategy` | `MergeStrategy \| None` | `None` | Merge strategy |
| `name` | `str \| None` | `None` | Name for merged workspace |

**Returns:** `MergeWorkspacesResult`

#### `DeleteWorkspace`

Delete a workspace and clean up resources.

```python
from doeff_conductor import DeleteWorkspace

# Normal cleanup
yield DeleteWorkspace(workspace=workspace)

# Force delete (ignores uncommitted changes)
yield DeleteWorkspace(workspace=workspace, force=True)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `workspace` | `Workspace` | Required | Workspace to delete |
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

#### `Agent`

Run an agent to completion and return a schema-validated artifact.

```python
from doeff_conductor import Agent, AgentTask

schema = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}

artifact = yield Agent(
    AgentTask(
        run_id=issue.id,
        node_id="implement",
        attempt=0,
        env=workspace,
        prompt="Implement the login feature",
        result_schema=schema,
        verification_class="test-verifiable",
        agent_type="codex",
        name="implementer",
        timeout_seconds=300,
    )
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `task` | `AgentTask` | Required | Schema-validated task descriptor |

`AgentTask.env` is a logical `Workspace`. The handler materializes it through the workspace handler and keeps filesystem paths out of the effect payload.

**Returns:** `dict[str, object]` or another artifact object accepted by `result_schema`

---

### Git Effects

#### `Commit`

Create a commit in the workspace.

```python
from doeff_conductor import Commit

sha = yield Commit(
    workspace=workspace,
    message="feat: add login feature",
    all=True,  # Stage all changes
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `workspace` | `Workspace` | Required | Workspace to commit in |
| `message` | `str` | Required | Commit message |
| `all` | `bool` | `True` | Stage all changes |

**Returns:** `str` (commit SHA)

#### `Push`

Push branch to remote.

```python
from doeff_conductor import Push

yield Push(
    workspace=workspace,
    remote="origin",
    force=False,
    set_upstream=True,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `workspace` | `Workspace` | Required | Workspace to push |
| `remote` | `str` | `"origin"` | Remote name |
| `force` | `bool` | `False` | Force push |
| `set_upstream` | `bool` | `True` | Set upstream |

**Returns:** `bool`

#### `CreatePR`

Create a pull request.

```python
from doeff_conductor import CreatePR

pr = yield CreatePR(
    workspace=workspace,
    title="Add login feature",
    body="Implements OAuth2 login",
    target="main",
    draft=False,
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `workspace` | `Workspace` | Required | Workspace with changes |
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

#### `WorkspaceHandler`

Handles workspace effects.

```python
from doeff_conductor import WorkspaceHandler

handler = WorkspaceHandler(repo_path=Path("/path/to/repo"))

# Methods
handler.handle_create_workspace(effect)
handler.handle_merge_workspaces(effect)
handler.handle_delete_workspace(effect)
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

#### `list_workspaces`

List workspace workspaces.

```python
workspaces = api.list_workspaces()
# Filter by workflow
workspaces = api.list_workspaces(workflow_id="abc123")
```

#### `cleanup_workspaces`

Cleanup orphaned workspace workspaces.

```python
# Dry run
would_clean = api.cleanup_workspaces(dry_run=True)

# Actually clean
cleaned = api.cleanup_workspaces(older_than_days=7)
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

### Workspace Commands

```bash
# List workspaces
conductor workspace list [--workflow ID] [--json]

# Cleanup workspaces
conductor workspace cleanup [--dry-run] [--older-than DAYS] [--json]
```

### Template Commands

```bash
# List templates
conductor template list [--json]

# Show template source
conductor template show <name>
```
