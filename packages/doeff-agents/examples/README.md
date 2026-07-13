# doeff-agents Examples

This directory contains examples demonstrating the use of `doeff-agents` for managing
coding agents (Claude, Codex, Gemini) in tmux sessions using the **doeff effects-based API**.

## Effects-Based Approach

All examples use the doeff effects system:
- `@do` decorator for composable generator functions
- `slog` effects for structured logging (displayed on stderr by `slog_handler`)
- `agent_effectful_handlers()` for real tmux or `mock_agent_handlers()` for testing
- `_runtime.run_program(...)` for execution — an example entry point that
  composes the standard handler stack (mirroring the CLI `default_interpreter`,
  including `slog_handler`) and executes it with `run(scheduled(...))`. There
  is deliberately no public bundled default stack (ADR-DOE-PRESET-001).

## Examples

| File | Description |
|------|-------------|
| `01_basic_session.py` | Basic session launch and monitoring with `@do` and fine-grained effects |
| `02_context_manager.py` | Using `with_session` bracket pattern for safe cleanup |
| `03_async_monitoring.py` | Multiple sessions and parallel-style execution patterns |
| `04_custom_adapter.py` | Creating custom agent adapters with the effects API |
| `05_status_callbacks.py` | Using `slog` effects instead of callbacks for event tracking |
| `06_effects_api.py` | Complete effects API demonstration with all patterns |

## Quick Start

```python
import asyncio
from pathlib import Path

from _runtime import run_program
from doeff import do, slog
from doeff_time import Delay

from doeff_agents import (
    AgentType,
    Capture,
    Launch,
    LaunchConfig,
    MockSessionScript,
    Monitor,
    SessionStatus,
    Stop,
    configure_mock_session,
    mock_agent_handlers,
)

@do
def my_agent_workflow(session_name: str, config: LaunchConfig):
    yield slog("start", session_name=session_name)

    handle = yield Launch(
        session_name,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        prompt=config.prompt,
    )

    try:
        while True:
            observation = yield Monitor(handle)
            yield slog("status", status=observation.status.value)

            if observation.is_terminal:
                break
            yield Delay(1.0)

        output = yield Capture(handle, lines=30)
        return {"status": observation.status.value, "output": output}
    finally:
        yield Stop(handle)

async def main():
    session_name = "my-session"

    # Configure mock session behavior
    configure_mock_session(
        session_name,
        MockSessionScript(observations=[
            (SessionStatus.RUNNING, "Working..."),
            (SessionStatus.DONE, "Complete!"),
        ]),
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Hello, world!",
    )

    # run_program composes the standard stack (incl. slog_handler stderr sink)
    # around the program; agent handlers go innermost via custom_handlers.
    result = await run_program(
        my_agent_workflow(session_name, config),
        custom_handlers=mock_agent_handlers(),
    )
    print(result)

asyncio.run(main())
```

## Using Real Tmux

For real tmux sessions, use `agent_effectful_handlers()` instead:

```python
from doeff_agents import agent_effectful_handlers

result = await run_program(
    my_agent_workflow("real-session", config),
    custom_handlers=agent_effectful_handlers(),  # Real tmux handlers
)
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
configure_mock_session(
    "test-session",
    MockSessionScript(observations=[
        (SessionStatus.RUNNING, "Working..."),
        (SessionStatus.BLOCKED, "Need input..."),
        (SessionStatus.DONE, "Complete!"),
    ]),
)

result = await run_program(
    my_workflow("test-session", config),
    custom_handlers=mock_agent_handlers(),
)
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
   pip install doeff doeff-core-effects doeff-agents
   # or
   uv add doeff doeff-core-effects doeff-agents
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
handle = yield Launch(
    session_name,
    agent_type=config.agent_type,
    work_dir=config.work_dir,
    prompt=config.prompt,
)
observation = yield Monitor(handle)
output = yield Capture(handle, lines=50)
yield Send(handle, "Continue")
yield Delay(1.0)
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
yield slog("launched", session_id=handle.session_id)
yield slog("status_change", old=old.value, new=new.value)
yield slog("complete", status=status.value, iterations=i)
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
