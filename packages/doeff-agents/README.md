# doeff-agents

Agent session management for coding agents (Claude, Codex, Gemini) in tmux.

## Installation

```bash
pip install doeff-agents
```

## Quick Start

### Python API

```python
from doeff_agents import (
    session_scope, monitor_session, send_message,
    LaunchConfig, AgentType, SessionStatus
)
from pathlib import Path

config = LaunchConfig(
    agent_type=AgentType.CLAUDE,
    work_dir=Path("/path/to/project"),
    prompt="Read the README and summarize the project",
)

# Context manager ensures cleanup on exit/exception
with session_scope("my-agent-session", config) as session:
    while not session.is_terminal:
        new_status = monitor_session(session)
        if new_status:
            print(f"Status changed to: {new_status}")

        if session.status == SessionStatus.BLOCKED:
            send_message(session, "Continue with the next step")

        time.sleep(1)
```

### CLI

```bash
# Launch an agent
doeff-agents run --agent claude --work-dir . --prompt "Fix the bug"

# List sessions
doeff-agents ps

# Attach to a session
doeff-agents attach my-session

# Stop a session
doeff-agents stop my-session

# Monitor a session
doeff-agents watch my-session
```

## Features

- **Tmux abstraction**: Pythonic wrapper around tmux commands
- **Adapter pattern**: Protocol-based adapters for different agents
- **Status monitoring**: Detect agent state from pane output
- **Session lifecycle**: Create, monitor, send, capture, kill
- **Async support**: Both sync and async APIs
- **Context managers**: Safe cleanup on exceptions/cancellation

## Supported Agents

- Claude Code (`claude`)
- OpenAI Codex (`codex`)
- Gemini CLI (`gemini`)

## License

MIT