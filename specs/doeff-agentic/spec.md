# doeff-agentic Specification

## Overview

doeff-agentic is the unified package for agent-based workflow orchestration. It provides a layered architecture for managing multi-agent workflows with proper environment isolation.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Workflow Layer                                │
│  - Orchestrates multiple agent sessions                          │
│  - doeff-flow integration (Checkpoint, Slog)                     │
│  - Workflow metadata, status, history                            │
│  - TUI/CLI operates at this level                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Environment Layer                             │
│  - Manages working directories / git worktrees                   │
│  - Handles state inheritance between agents                      │
│  - Provides isolation or sharing as needed                       │
│  - Types: worktree, inherited, copy-on-write, shared             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Session Layer                                 │
│  - OpenCode server API (primary)                                 │
│  - tmux (legacy fallback)                                        │
│  - Single agent session execution                                │
└─────────────────────────────────────────────────────────────────┘
```

## Layer Details

### Session Layer

The lowest layer, responsible for running individual agent sessions.

**Primary Backend: OpenCode**
- HTTP API for session management
- SSE for real-time events
- Transparent server auto-start

**Legacy Backend: tmux**
- For environments without OpenCode
- Limited feature support (no fork, polling-based)

### Environment Layer

Manages the working context in which agent sessions run.

**Environment Types:**

| Type | Description | Use Case |
|------|-------------|----------|
| `worktree` | Fresh git worktree at specific commit | Isolated work, parallel branches |
| `inherited` | Same directory as previous session | Sequential agents building on each other |
| `copy` | Copy of directory at point in time | Snapshot for comparison |
| `shared` | Multiple agents same directory | Careful! Race conditions possible |
| `container` | Isolated container filesystem | Full isolation (future) |

**Environment Lifecycle:**
1. Created explicitly or implicitly with session
2. Sessions bind to environments
3. Environment persists across sessions (if reused)
4. Cleanup on workflow completion or explicit delete

### Workflow Layer

Orchestrates multiple agent sessions across environments.

**Responsibilities:**
- Define agent execution order (sequential, parallel, conditional)
- Manage environment allocation
- Track workflow state and progress
- Integrate with doeff-flow for durability

---

## Effect Definitions

All effects use `Agentic` prefix.

### Environment Effects

```python
@dataclass(frozen=True)
class AgenticCreateEnvironment(EffectBase):
    """Create a new environment for agent sessions.
    
    Yields: AgenticEnvironmentHandle
    """
    type: str  # "worktree" | "inherited" | "copy" | "shared"
    name: str | None = None  # Human-readable name
    base_commit: str | None = None  # For worktree/copy types
    base_environment: str | None = None  # For inherited type
    working_dir: str | None = None  # Override working directory


@dataclass(frozen=True)
class AgenticGetEnvironment(EffectBase):
    """Get an existing environment by ID.
    
    Yields: AgenticEnvironmentHandle
    """
    environment_id: str


@dataclass(frozen=True)
class AgenticDeleteEnvironment(EffectBase):
    """Delete an environment and clean up resources.
    
    Yields: bool
    """
    environment_id: str
    force: bool = False  # Delete even if sessions are using it
```

### Session Effects

```python
@dataclass(frozen=True)
class AgenticCreateSession(EffectBase):
    """Create a new agent session in an environment.
    
    Yields: AgenticSessionHandle
    """
    name: str  # Required: identifier for this session within workflow
    environment_id: str | None = None  # None = create default environment
    title: str | None = None  # Display title
    agent: str | None = None  # Agent type
    model: str | None = None  # Model override


@dataclass(frozen=True)
class AgenticForkSession(EffectBase):
    """Fork an existing session at a specific message.
    
    Yields: AgenticSessionHandle
    Raises: UnsupportedOperationError (on tmux handler)
    """
    session_id: str
    name: str  # New session name
    message_id: str | None = None  # Fork point


@dataclass(frozen=True)
class AgenticGetSession(EffectBase):
    """Get an existing session by ID or name.
    
    Yields: AgenticSessionHandle
    """
    session_id: str | None = None
    name: str | None = None  # Can lookup by name within workflow


@dataclass(frozen=True)
class AgenticAbortSession(EffectBase):
    """Abort a running session.
    
    Yields: None
    """
    session_id: str


@dataclass(frozen=True)
class AgenticDeleteSession(EffectBase):
    """Delete a session and all its data.
    
    Yields: bool
    """
    session_id: str
```

### Message Effects

```python
@dataclass(frozen=True)
class AgenticSendMessage(EffectBase):
    """Send a message to a session.
    
    Yields: AgenticMessageHandle
    """
    session_id: str
    content: str
    wait: bool = False  # If True, block until response complete
    agent: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class AgenticGetMessages(EffectBase):
    """Get messages from a session.
    
    Yields: list[AgenticMessage]
    """
    session_id: str
    limit: int | None = None
```

### Event Effects

```python
@dataclass(frozen=True)
class AgenticNextEvent(EffectBase):
    """Wait for next event from session.
    
    Handler manages SSE connection internally.
    
    Yields: AgenticEvent | AgenticEndOfEvents
    """
    session_id: str
    timeout: float | None = None


class AgenticEndOfEvents:
    """Sentinel indicating end of event stream."""
    pass
```

### Status & Capability Effects

```python
@dataclass(frozen=True)
class AgenticGetSessionStatus(EffectBase):
    """Get current session status.
    
    Yields: AgenticSessionStatus
    """
    session_id: str


@dataclass(frozen=True)
class AgenticSupportsCapability(EffectBase):
    """Check if current handler supports a capability.
    
    Yields: bool
    """
    capability: str  # "fork", "events", "worktree", etc.
```

---

## Data Types

```python
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AgenticEnvironmentType(Enum):
    WORKTREE = "worktree"
    INHERITED = "inherited"
    COPY = "copy"
    SHARED = "shared"
    CONTAINER = "container"


class AgenticSessionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    ERROR = "error"
    ABORTED = "aborted"


@dataclass
class AgenticEnvironmentHandle:
    """Handle to an environment."""
    id: str
    type: AgenticEnvironmentType
    name: str | None
    working_dir: str
    created_at: datetime
    base_commit: str | None = None


@dataclass
class AgenticSessionHandle:
    """Handle to an agent session."""
    id: str
    name: str  # Workflow-local identifier
    environment_id: str
    status: AgenticSessionStatus
    created_at: datetime
    title: str | None = None
    agent: str | None = None
    model: str | None = None


@dataclass
class AgenticMessageHandle:
    """Handle to a message."""
    id: str
    session_id: str
    role: str
    created_at: datetime


@dataclass
class AgenticMessage:
    """Full message content."""
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    parts: list[dict] | None = None


@dataclass
class AgenticEvent:
    """Event from session stream."""
    type: str
    session_id: str
    data: dict
    timestamp: datetime
```

---

## Workflow Identification

### Workflow ID
- Auto-generated hex ID: `sha256(name + timestamp)[:7]`
- Example: `a3f8b2c`
- Supports prefix matching (like git/docker)

### Session Naming
Each session within a workflow has:
- **id**: Global unique identifier (from OpenCode)
- **name**: Workflow-local identifier (user-provided, required)

```python
session = yield AgenticCreateSession(
    name="reviewer",  # Required: used for attach, logs, etc.
    title="Code Reviewer",
    environment_id=env.id,
)
```

### Fully Qualified Session Reference
```
<workflow-id>:<session-name>

Examples:
  a3f8b2c:reviewer
  a3f8b2c:fixer
  b7e1d4f:tester
```

---

## CLI Commands

### Workflow Commands

```bash
# List workflows
doeff-agentic ps [--status <status>] [--json]

# Output:
# WORKFLOW    STATUS     AGENTS                      UPDATED
# a3f8b2c     running    reviewer(done), fixer(run)  2m ago
# b7e1d4f     complete   tester(done)                1h ago
```

```bash
# Watch workflow progress
doeff-agentic watch <workflow-id>

# Shows live TUI with:
# - Workflow status
# - All agent sessions and their status
# - Current activity
```

```bash
# Stop workflow
doeff-agentic stop <workflow-id>
```

### Session Commands

```bash
# Attach to specific agent session
doeff-agentic attach <workflow-id>:<session-name>
doeff-agentic attach <session-id>

# Examples:
doeff-agentic attach a3f8b2c:reviewer
doeff-agentic attach a3f:reviewer  # Prefix match on workflow
```

```bash
# View session logs
doeff-agentic logs <workflow-id>                    # All sessions
doeff-agentic logs <workflow-id>:<session-name>    # Specific session
doeff-agentic logs --follow <workflow-id>          # Tail mode
```

### Environment Commands

```bash
# List environments
doeff-agentic env list [--workflow <id>]

# Output:
# ENV ID      TYPE       WORKING DIR                    SESSIONS
# env-abc     worktree   /tmp/doeff/worktrees/abc123    reviewer, fixer
# env-def     worktree   /tmp/doeff/worktrees/def456    tester
```

```bash
# Cleanup orphaned environments
doeff-agentic env cleanup [--dry-run]
```

### Removed Commands

- ~~`send`~~: Doesn't make sense for multi-agent workflows. Use workflow effects instead.

---

## TUI Design

The TUI operates at the workflow level, showing:

```
┌─ Workflow a3f8b2c: PR Review ────────────────────────────────────┐
│ Status: running                                                  │
│ Started: 5m ago                                                  │
├──────────────────────────────────────────────────────────────────┤
│ Agents:                                                          │
│   ● reviewer    [complete]  env-abc  "Found 3 issues"           │
│   ◐ fixer       [running]   env-abc  "Fixing issue 2/3..."      │
│   ○ tester      [pending]   env-def  -                          │
├──────────────────────────────────────────────────────────────────┤
│ Current Activity (fixer):                                        │
│ > Applying fix for unused import...                              │
│ > Removing line 42: import os                                    │
│ > Running tests to verify fix...                                 │
├──────────────────────────────────────────────────────────────────┤
│ [a] attach  [l] logs  [s] stop  [q] quit                        │
└──────────────────────────────────────────────────────────────────┘
```

**Key Bindings:**
- `a` + select agent → Attach to session
- `l` + select agent → View logs
- `s` → Stop workflow
- `Tab` → Cycle through agents
- `Enter` → Attach to selected agent

---

## State Management

### JSONL Event Logs

Each workflow has an event log:

```
~/.local/state/doeff-agentic/workflows/
├── a3f8b2c/
│   ├── workflow.jsonl      # Workflow-level events
│   ├── sessions/
│   │   ├── reviewer.jsonl  # Per-session events
│   │   ├── fixer.jsonl
│   │   └── tester.jsonl
│   └── environments/
│       ├── env-abc.jsonl
│       └── env-def.jsonl
└── b7e1d4f/
    └── ...
```

**Workflow Events:**
```jsonl
{"ts": "...", "type": "workflow.created", "id": "a3f8b2c", "name": "PR Review"}
{"ts": "...", "type": "environment.created", "id": "env-abc", "type": "worktree"}
{"ts": "...", "type": "session.created", "id": "sess-123", "name": "reviewer", "env": "env-abc"}
{"ts": "...", "type": "session.status", "name": "reviewer", "status": "running"}
{"ts": "...", "type": "session.status", "name": "reviewer", "status": "complete"}
{"ts": "...", "type": "workflow.status", "status": "complete"}
```

**Session Events:**
```jsonl
{"ts": "...", "type": "message.sent", "role": "user", "preview": "Review this..."}
{"ts": "...", "type": "message.chunk", "content": "Looking at the code..."}
{"ts": "...", "type": "message.complete", "tokens": 1234}
{"ts": "...", "type": "tool.call", "tool": "read", "args": {"path": "main.py"}}
```

---

## Handler Protocol

```python
from typing import Protocol

class AgenticHandler(Protocol):
    """Protocol for agent session backends."""
    
    # Environment management
    def handle_create_environment(self, effect: AgenticCreateEnvironment) -> AgenticEnvironmentHandle: ...
    def handle_get_environment(self, effect: AgenticGetEnvironment) -> AgenticEnvironmentHandle: ...
    def handle_delete_environment(self, effect: AgenticDeleteEnvironment) -> bool: ...
    
    # Session management
    def handle_create_session(self, effect: AgenticCreateSession) -> AgenticSessionHandle: ...
    def handle_fork_session(self, effect: AgenticForkSession) -> AgenticSessionHandle: ...
    def handle_get_session(self, effect: AgenticGetSession) -> AgenticSessionHandle: ...
    def handle_abort_session(self, effect: AgenticAbortSession) -> None: ...
    def handle_delete_session(self, effect: AgenticDeleteSession) -> bool: ...
    
    # Messages
    def handle_send_message(self, effect: AgenticSendMessage) -> AgenticMessageHandle: ...
    def handle_get_messages(self, effect: AgenticGetMessages) -> list[AgenticMessage]: ...
    
    # Events
    def handle_next_event(self, effect: AgenticNextEvent) -> AgenticEvent | AgenticEndOfEvents: ...
    
    # Status
    def handle_get_session_status(self, effect: AgenticGetSessionStatus) -> AgenticSessionStatus: ...
    
    # Capabilities
    def supports_capability(self, capability: str) -> bool: ...
```

---

## Server Management

### OpenCode Handler

The OpenCode handler transparently manages the server:

1. On first effect, check if server is running (health endpoint)
2. If not running, spawn `opencode serve --hostname <host> --port <port>`
3. Wait for server to become ready
4. Cache connection for subsequent effects

```python
handlers = opencode_handler(
    # Auto-start with defaults
)

# Or connect to existing
handlers = opencode_handler(
    server_url="http://localhost:4096",
)
```

### tmux Handler (Legacy)

Limited feature support:
- No fork capability
- Polling-based events (simulated from output)
- No environment management beyond working directory

Raises `UnsupportedOperationError` for:
- `AgenticForkSession`
- Environment types other than "shared"

---

## Usage Examples

### Basic Multi-Agent Workflow

```python
from doeff import do
from doeff_agentic import (
    AgenticCreateEnvironment,
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticNextEvent,
    AgenticEndOfEvents,
    AgenticGetMessages,
)

@do
def pr_review_workflow(pr_url: str):
    # Create isolated environment
    env = yield AgenticCreateEnvironment(
        type="worktree",
        name="pr-review",
        base_commit="main",
    )
    
    # Agent 1: Reviewer
    reviewer = yield AgenticCreateSession(
        name="reviewer",
        environment_id=env.id,
        title="Code Reviewer",
    )
    
    yield AgenticSendMessage(
        session_id=reviewer.id,
        content=f"Review the PR at {pr_url}. List any issues found.",
    )
    
    # Wait for completion
    while True:
        event = yield AgenticNextEvent(reviewer.id)
        if isinstance(event, AgenticEndOfEvents) or event.type == "message.complete":
            break
    
    review = yield AgenticGetMessages(reviewer.id)
    issues = review[-1].content
    
    # Agent 2: Fixer (same environment - sees reviewer's context)
    fixer = yield AgenticCreateSession(
        name="fixer",
        environment_id=env.id,  # Same environment
        title="Issue Fixer",
    )
    
    yield AgenticSendMessage(
        session_id=fixer.id,
        content=f"Fix these issues:\n{issues}",
    )
    
    while True:
        event = yield AgenticNextEvent(fixer.id)
        if isinstance(event, AgenticEndOfEvents) or event.type == "message.complete":
            break
    
    return {"review": issues, "fixed": True}
```

### Parallel Agents in Isolated Environments

```python
@do
def multi_perspective_analysis(topic: str):
    # Create separate environments for each agent
    env_tech = yield AgenticCreateEnvironment(type="worktree", name="tech-analysis")
    env_biz = yield AgenticCreateEnvironment(type="worktree", name="biz-analysis")
    
    # Launch in parallel (both return immediately with wait=False)
    tech = yield AgenticCreateSession(name="tech", environment_id=env_tech.id)
    biz = yield AgenticCreateSession(name="biz", environment_id=env_biz.id)
    
    yield AgenticSendMessage(tech.id, f"Analyze {topic} from technical perspective", wait=False)
    yield AgenticSendMessage(biz.id, f"Analyze {topic} from business perspective", wait=False)
    
    # Wait for both (could use Gather effect)
    results = {}
    for session_name, session in [("tech", tech), ("biz", biz)]:
        while True:
            event = yield AgenticNextEvent(session.id)
            if isinstance(event, AgenticEndOfEvents) or event.type == "message.complete":
                break
        messages = yield AgenticGetMessages(session.id)
        results[session_name] = messages[-1].content
    
    return results
```
