# doeff-agents Examples

This directory contains examples demonstrating the use of `doeff-agents` for managing
coding agents (Claude, Codex, Gemini) in tmux sessions.

## Examples

| File | Description |
|------|-------------|
| `01_basic_session.py` | Basic session launch and monitoring |
| `02_context_manager.py` | Using context managers for safe cleanup |
| `03_async_monitoring.py` | Async API usage with multiple sessions |
| `04_custom_adapter.py` | Creating a custom agent adapter |
| `05_status_callbacks.py` | Using callbacks for status changes and PR detection |
| `06_effects_api.py` | Effects-based API with handlers and programs |

## Prerequisites

Before running these examples, ensure:

1. **tmux is installed**:
   ```bash
   # macOS
   brew install tmux

   # Ubuntu/Debian
   apt install tmux
   ```

2. **Agent CLIs are available** (at least one):
   - Claude Code: `npm install -g @anthropic/claude-code`
   - Codex: Available via OpenAI
   - Gemini: Available via Google

3. **doeff-agents is installed**:
   ```bash
   pip install doeff-agents
   # or
   uv add doeff-agents
   ```

## Running Examples

```bash
# Run a specific example
python examples/agents/01_basic_session.py

# Or use the CLI directly
doeff-agents run --agent claude --work-dir . --prompt "Hello"
```

## CLI Quick Reference

```bash
# Launch a new session
doeff-agents run --agent claude --prompt "Fix the bug in main.py"

# List all sessions
doeff-agents ps

# Watch a session
doeff-agents watch my-session

# Send a message to a session
doeff-agents send my-session "Continue with the next step"

# Capture output
doeff-agents output my-session --lines 100

# Attach to a session (interactive)
doeff-agents attach my-session

# Stop a session
doeff-agents stop my-session
```
