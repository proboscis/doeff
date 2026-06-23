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

## doeff-agentd Socket Convention

The daemon and Python client share one canonical socket convention:

- DB: `${XDG_STATE_HOME:-$HOME/.local/state}/doeff/agentd.sqlite`
- socket when `XDG_RUNTIME_DIR` is set: `$XDG_RUNTIME_DIR/doeff/agentd.sock`
- socket when `XDG_RUNTIME_DIR` is unset: `/tmp/doeff-agentd-${USER:-unknown}.sock`

Start the daemon with the same derived paths:

```bash
AGENTD_DB="${XDG_STATE_HOME:-$HOME/.local/state}/doeff/agentd.sqlite"
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
  AGENTD_SOCKET="$XDG_RUNTIME_DIR/doeff/agentd.sock"
else
  AGENTD_SOCKET="/tmp/doeff-agentd-${USER:-unknown}.sock"
fi
doeff-agentd --db "$AGENTD_DB" --socket "$AGENTD_SOCKET" --max-running 10 serve
```

`LazyAgentdClient` connects only to that expected socket. It does not probe
per-run temporary sockets or fall back to direct worker execution; if no daemon
is reachable, it raises an actionable error containing the exact start command.

## Features

- **Tmux abstraction**: Pythonic wrapper around tmux commands
- **Adapter pattern**: Protocol-based adapters for different agents
- **Status monitoring**: Detect agent state from pane output
- **Session lifecycle**: Create, monitor, send, capture, kill
- **Async support**: Both sync and async APIs
- **Context managers**: Safe cleanup on exceptions/cancellation

## Agent Launch Invariant

Agents are launched as live terminal sessions. The initial task prompt and all
follow-up prompts are delivered through the terminal transport after launch
(`tmux send-keys` today; another terminal backend such as zellij can provide the
same operation later).

Do not start coding agents in print, one-shot, SDK, or prompt-argv mode. In
particular, built-in adapters must not read `LaunchParams.prompt` while building
argv, and Claude/Codex prompt-mode flags or positional prompt arguments are
architecture violations. This keeps the agent process alive so doeff-agents can
validate structured results and send correction prompts in the same session.

## Supported Agents

- Claude Code (`claude`)
- OpenAI Codex (`codex`)
- Gemini CLI (`gemini`)

## License

MIT
