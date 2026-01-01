# Agent Session Management (doeff-agents)

The `doeff-agents` package provides a Python API for managing coding agents
(Claude, Codex, Gemini) in isolated tmux sessions. It enables launching,
monitoring, and controlling agent sessions programmatically.

## Installation

```bash
pip install doeff-agents
# or
uv add doeff-agents
```

**Prerequisites:**
- Python 3.10+
- tmux (installed and available in PATH)
- At least one agent CLI (Claude, Codex, or Gemini)

## Quick Start

```python
from pathlib import Path
from doeff_agents import (
    AgentType, LaunchConfig, session_scope, monitor_session
)

config = LaunchConfig(
    agent_type=AgentType.CLAUDE,
    work_dir=Path.cwd(),
    prompt="Hello! What files are in this directory?",
)

with session_scope("my-session", config) as session:
    while not session.is_terminal:
        monitor_session(session)
```

## Core Concepts

### Session Lifecycle

Agent sessions follow a defined lifecycle:

```
PENDING → BOOTING → RUNNING → DONE
                  ↓
              BLOCKED (waiting for input)
                  ↓
              BLOCKED_API (rate limited)
                  ↓
              FAILED / EXITED / STOPPED
```

| Status | Description |
|--------|-------------|
| `PENDING` | Session created but not started |
| `BOOTING` | Agent is starting up |
| `RUNNING` | Agent is actively working |
| `BLOCKED` | Agent is waiting for user input |
| `BLOCKED_API` | Agent hit API rate limits |
| `DONE` | Agent completed successfully |
| `FAILED` | Agent encountered an error |
| `EXITED` | Agent process ended (shell prompt visible) |
| `STOPPED` | Session was explicitly killed |

### LaunchConfig

Configuration for launching an agent session:

```python
from doeff_agents import LaunchConfig, AgentType
from pathlib import Path

config = LaunchConfig(
    agent_type=AgentType.CLAUDE,     # Required: CLAUDE, CODEX, GEMINI, or CUSTOM
    work_dir=Path("/path/to/project"),  # Required: Working directory
    prompt="Fix the bug in main.py",    # Optional: Initial prompt
    profile="my-profile",               # Optional: Agent profile
    resume=False,                       # Optional: Resume previous session
    session_name=None,                  # Optional: Session to resume
)
```

### AgentSession

Represents a running agent session:

```python
session = launch_session("my-session", config)

session.session_name  # "my-session"
session.pane_id       # "%42" (tmux pane ID)
session.agent_type    # AgentType.CLAUDE
session.work_dir      # Path("/path/to/project")
session.status        # SessionStatus.RUNNING
session.is_terminal   # False (True when DONE, FAILED, EXITED, STOPPED)
```

## API Reference

### Session Management

#### `launch_session(session_name, config, *, ready_timeout=30.0)`

Launch a new agent session in tmux.

```python
from doeff_agents import launch_session, LaunchConfig, AgentType

config = LaunchConfig(
    agent_type=AgentType.CLAUDE,
    work_dir=Path.cwd(),
    prompt="Hello",
)

session = launch_session("my-session", config)
```

**Raises:**
- `AgentLaunchError`: If agent CLI is not available
- `AgentReadyTimeoutError`: If agent doesn't become ready within timeout
- `SessionAlreadyExistsError`: If session already exists

#### `session_scope(session_name, config, *, ready_timeout=30.0)`

Context manager for automatic cleanup:

```python
from doeff_agents import session_scope

with session_scope("my-session", config) as session:
    # Work with session
    pass
# Session is automatically stopped on exit
```

#### `monitor_session(session, *, on_status_change=None, on_pr_detected=None)`

Check session status and update if changed:

```python
from doeff_agents import monitor_session, SessionStatus

def on_change(old, new, output):
    print(f"Status: {old.value} -> {new.value}")

def on_pr(url):
    print(f"PR: {url}")

new_status = monitor_session(
    session,
    on_status_change=on_change,
    on_pr_detected=on_pr,
)
```

#### `send_message(session, message, *, enter=True)`

Send a message to the agent:

```python
from doeff_agents import send_message

send_message(session, "Continue with the next step")
send_message(session, "partial input", enter=False)  # Don't press Enter
```

#### `capture_output(session, lines=100)`

Capture pane output:

```python
from doeff_agents import capture_output

output = capture_output(session, lines=50)
print(output)
```

#### `stop_session(session)`

Stop a session:

```python
from doeff_agents import stop_session

stop_session(session)  # Sets status to STOPPED
```

#### `attach_session(session)`

Attach to a session (blocks until detached):

```python
from doeff_agents import attach_session

attach_session(session)  # Press Ctrl+B, D to detach
```

### Async API

#### `async_session_scope(session_name, config, *, ready_timeout=30.0)`

Async context manager:

```python
import asyncio
from doeff_agents import async_session_scope

async def main():
    async with async_session_scope("my-session", config) as session:
        # Async work here
        pass

asyncio.run(main())
```

#### `async_monitor_session(session, *, poll_interval=1.0, on_status_change=None, on_pr_detected=None)`

Async monitoring (awaits until terminal state):

```python
from doeff_agents import async_monitor_session

final_status = await async_monitor_session(
    session,
    poll_interval=0.5,
    on_status_change=on_change,
)
```

### Adapters

#### Built-in Adapters

| Agent | Injection | Ready Pattern | Status Bar Lines |
|-------|-----------|---------------|------------------|
| Claude | ARG | None | 5 |
| Codex | ARG | None | 3 |
| Gemini | TMUX | `Type your message\|>` | 3 |

#### Custom Adapters

Register custom adapters for other agents:

```python
from doeff_agents import register_adapter, AgentType
from doeff_agents.adapters.base import InjectionMethod

class MyAgentAdapter:
    @property
    def agent_type(self) -> AgentType:
        return AgentType.CUSTOM

    def is_available(self) -> bool:
        return shutil.which("my-agent") is not None

    def launch_command(self, cfg) -> list[str]:
        return ["my-agent", cfg.prompt] if cfg.prompt else ["my-agent"]

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.ARG  # or InjectionMethod.TMUX

    @property
    def ready_pattern(self) -> str | None:
        return None  # Regex pattern for TMUX injection

    @property
    def status_bar_lines(self) -> int:
        return 3

register_adapter(AgentType.CUSTOM, MyAgentAdapter)
```

### Tmux Operations

Low-level tmux operations:

```python
from doeff_agents.tmux import (
    is_tmux_available,  # Check if tmux is installed
    is_inside_tmux,     # Check if running inside tmux
    has_session,        # Check if session exists
    list_sessions,      # List all session names
    new_session,        # Create a session
    send_keys,          # Send keys to pane
    capture_pane,       # Capture pane output
    kill_session,       # Kill a session
    attach_session,     # Attach to session
)
```

## CLI Reference

The `doeff-agents` command provides session management:

```bash
# Launch a new session
doeff-agents run --agent claude --work-dir . --prompt "Hello"
doeff-agents run -a codex -w /path/to/project -p "Fix bugs"

# List sessions
doeff-agents ps

# Watch a session (live monitoring)
doeff-agents watch my-session
doeff-agents watch my-session --interval 0.5

# Send a message
doeff-agents send my-session "Continue"

# Capture output
doeff-agents output my-session --lines 100

# Attach to session (interactive)
doeff-agents attach my-session

# Stop a session
doeff-agents stop my-session
```

## Status Detection

The monitor detects agent status from pane output using pattern matching:

### Completion Detection

```python
# Patterns that indicate completion (case-insensitive)
"task completed successfully"
"all tasks completed"
"session ended"
"goodbye"
```

### API Limit Detection

```python
# Patterns that indicate rate limiting
"cost limit reached"
"rate limit exceeded"
"quota exceeded"
"you've hit your limit"
```

### Failure Detection

```python
# Patterns that indicate failure
"fatal error"
"unrecoverable error"
"agent crashed"
"authentication failed"
```

### Exit Detection

The agent is considered exited when:
1. No agent UI patterns are visible (e.g., "↵ send", "? for shortcuts")
2. A shell prompt is visible (e.g., `$ `, `% `, `❯ `)

**Important:** Completion is checked before exit detection to avoid
misclassifying agents that show a shell prompt after saying "goodbye".

## Error Handling

```python
from doeff_agents import (
    AgentLaunchError,        # Agent CLI not available
    AgentReadyTimeoutError,  # Agent didn't become ready
    SessionAlreadyExistsError,  # Session name already in use
    TmuxError,               # General tmux error
    TmuxNotAvailableError,   # tmux not installed
)

try:
    session = launch_session("my-session", config)
except AgentLaunchError as e:
    print(f"Agent not available: {e}")
except SessionAlreadyExistsError:
    print("Session already exists, use a different name")
```

## Best Practices

### 1. Always Use Context Managers

Context managers ensure cleanup even on exceptions:

```python
# Good
with session_scope("my-session", config) as session:
    ...

# Avoid (requires manual cleanup)
session = launch_session("my-session", config)
try:
    ...
finally:
    stop_session(session)
```

### 2. Handle BLOCKED Status

When an agent is blocked, it's waiting for user input:

```python
while not session.is_terminal:
    monitor_session(session)

    if session.status == SessionStatus.BLOCKED:
        # Option 1: Send a follow-up message
        send_message(session, "Continue")

        # Option 2: Attach for interactive use
        attach_session(session)

        # Option 3: Stop the session
        break

    time.sleep(1)
```

### 3. Use Callbacks for Complex Logic

Callbacks keep monitoring code clean:

```python
def handle_status(old, new, output):
    if new == SessionStatus.BLOCKED_API:
        notify_team("Rate limited!")
    elif new == SessionStatus.FAILED:
        create_incident_ticket(output)

monitor_session(session, on_status_change=handle_status)
```

### 4. Unique Session Names

Generate unique names to avoid conflicts:

```python
import uuid
session_name = f"task-{uuid.uuid4().hex[:8]}"

# Or with timestamp
import time
session_name = f"task-{int(time.time())}"
```

## Related

- [Examples: Agent Session Examples](../examples/agents/README.md)
- [Workflow Observability](17-workflow-observability.md)
- orch: Go reference implementation
