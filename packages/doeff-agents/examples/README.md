# doeff-agents Examples

This directory contains examples demonstrating the use of `doeff-agents` for managing
coding agents (Claude, Codex, Gemini) in tmux sessions using the **doeff effects-based API**.

## Effects-Based Approach

All examples use the doeff effects system with `AsyncRuntime`:
- `@do` decorator for composable generator functions
- `slog` effects for structured logging
- `preset_handlers()` for log display
- `agent_effectful_handlers()` for real tmux or `mock_agent_handlers()` for testing
- `AsyncRuntime` for execution

## Examples

| File | Description |
|------|-------------|
| `01_basic_session.py` | Basic session launch and monitoring with `@do` and fine-grained effects |
| `02_context_manager.py` | Using `with_session` bracket pattern for safe cleanup |
| `03_async_monitoring.py` | `AsyncRuntime` usage with multiple sessions and parallel execution |
| `04_custom_adapter.py` | Creating custom agent adapters with the effects API |
| `05_status_callbacks.py` | Using `slog` effects instead of callbacks for event tracking |
| `06_effects_api.py` | Complete effects API demonstration with all patterns |

## Quick Start

```python
import asyncio
from pathlib import Path

from doeff import AsyncRuntime, do
from doeff.effects.writer import slog
from doeff_preset import preset_handlers

from doeff_agents import (
    AgentType,
    Capture,
    MockSessionScript,
    Launch,
    LaunchConfig,
    Monitor,
    SessionStatus,
    Sleep,
    Stop,
    configure_mock_session,
    mock_agent_handlers,
)

@do
def my_agent_workflow(session_name: str, config: LaunchConfig):
    yield slog(step="start", session_name=session_name)
    
    handle = yield Launch(session_name, config)
    
    try:
        while True:
            observation = yield Monitor(handle)
            yield slog(step="status", status=observation.status.value)
            
            if observation.is_terminal:
                break
            yield Sleep(1.0)
        
        output = yield Capture(handle, lines=30)
        return {"status": observation.status.value, "output": output}
    finally:
        yield Stop(handle)

async def main():
    session_name = "my-session"
    
    # Configure mock session
    initial_store = {}
    configure_mock_session(
        initial_store,
        session_name,
        MockSessionScript([
            (SessionStatus.RUNNING, "Working..."),
            (SessionStatus.DONE, "Complete!"),
        ]),
    )
    
    # Create runtime with handlers
    handlers = {
        **preset_handlers(),
        **mock_agent_handlers(),
    }
    runtime = AsyncRuntime(handlers=handlers, initial_store=initial_store)
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Hello, world!",
    )
    
    result = await runtime.run(my_agent_workflow(session_name, config))
    print(result)

asyncio.run(main())
```

## Using Real Tmux

For real tmux sessions, use `agent_effectful_handlers()` instead:

```python
from doeff_agents import agent_effectful_handlers

handlers = {
    **preset_handlers(),
    **agent_effectful_handlers(),  # Real tmux handlers
}
runtime = AsyncRuntime(handlers=handlers)

result = await runtime.run(my_agent_workflow("real-session", config))
```

## Testing with Mock Handlers

All examples can run without real tmux using `mock_agent_handlers()`:

```python
from doeff_agents import (
    MockSessionScript,
    SessionStatus,
    configure_mock_session,
    mock_agent_handlers,
)

# Configure mock session behavior
initial_store = {}
configure_mock_session(
    initial_store,
    "test-session",
    MockSessionScript([
        (SessionStatus.RUNNING, "Working..."),
        (SessionStatus.BLOCKED, "Need input..."),
        (SessionStatus.DONE, "Complete!"),
    ]),
)

# Create runtime with mock handlers
handlers = {
    **preset_handlers(),
    **mock_agent_handlers(),
}
runtime = AsyncRuntime(handlers=handlers, initial_store=initial_store)

result = await runtime.run(my_workflow("test-session", config))
```

## Prerequisites

Before running examples with real tmux, ensure:

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

3. **doeff packages are installed**:
   ```bash
   pip install doeff doeff-preset doeff-agents
   # or
   uv add doeff doeff-preset doeff-agents
   ```

## Running Examples

```bash
# Run a specific example (uses mock handlers by default)
python packages/doeff-agents/examples/01_basic_session.py

# Or use the CLI directly (requires real tmux)
doeff-agents run --agent claude --work-dir . --prompt "Hello"
```

## Key Patterns

### Fine-Grained Effects

```python
handle = yield Launch(session_name, config)
observation = yield Monitor(handle)
output = yield Capture(handle, lines=50)
yield Send(handle, "Continue")
yield Sleep(1.0)
yield Stop(handle)
```

### High-Level Programs

```python
from doeff_agents import run_agent_to_completion, with_session

# Run to completion
result = yield from run_agent_to_completion(session_name, config)

# Bracket pattern (ensures cleanup)
output = yield from with_session(session_name, config, use_session_fn)
```

### Structured Logging with slog

```python
yield slog(step="launched", pane_id=handle.pane_id)
yield slog(step="status_change", old=old.value, new=new.value)
yield slog(step="complete", status=status.value, iterations=i)
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
