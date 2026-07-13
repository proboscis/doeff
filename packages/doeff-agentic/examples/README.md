# doeff-agentic Examples

Progressive examples that teach you how to build agent workflows using the new spec-compliant API.

## Prerequisites

1. Install doeff-agentic:
   ```bash
   cd packages/doeff-agentic
   uv sync
   ```

2. Ensure you have OpenCode server available, or the CLI will auto-start one.

## New API Overview

These examples use the new spec-compliant API:

| Effect | Description |
|--------|-------------|
| `AgenticCreateSession` | Create a new agent session |
| `AgenticSendMessage` | Send a message to a session |
| `AgenticGetMessages` | Get messages from a session |
| `AgenticNextEvent` | Wait for next event from session |
| `AgenticGetSessionStatus` | Get current status of a session |
| `opencode_handler()` | Create an installer: `opencode_handler()(program)` wraps a program |

For parallel execution, use core doeff effects: `Spawn` + `Gather` (see example 06).

### Basic Pattern

```python
import asyncio

from _runtime import run_program  # examples/_runtime.py: standard handler stack + run(scheduled(...))
from doeff import do
from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
)
from doeff_agentic.opencode_handler import opencode_handler

@do
def my_workflow():
    # Create a session
    session = yield AgenticCreateSession(name="my-agent")
    
    # Send a message and wait for completion
    yield AgenticSendMessage(
        session_id=session.id,
        content="Your prompt here",
        wait=True,
    )
    
    # Get the response
    messages = yield AgenticGetMessages(session_id=session.id)
    return messages[-1].content

# Run the workflow
async def main():
    program = opencode_handler()(my_workflow())
    output = await run_program(program)
    print(output)

asyncio.run(main())
```

`run_program` composes the standard entry-point stack (mirroring the CLI
`default_interpreter`, including the `slog_handler` stderr sink) around the
program. There is deliberately no public bundled default stack
(ADR-DOE-PRESET-001) — outside the examples, compose the installers you need
explicitly.

### Error Handling

`run_program` returns the program's value and raises on failure. `run()`
prints the doeff traceback (effect path + handler chain) to stderr before
re-raising, so a plain try/except is enough:

```python
async def main():
    program = opencode_handler()(my_workflow())
    try:
        output = await run_program(program)
    except Exception as e:
        print("=== Workflow Failed ===")
        print(f"Error: {e}")  # doeff traceback already printed on stderr
    else:
        print(output)
```

## Examples

### 01. Hello Agent
Minimal example - launch a single agent and get output.
```bash
uv run python examples/01_hello_agent.py
```

### 02. Agent with Status
Show workflow progress using slog (structured logging).
```bash
uv run python examples/02_agent_with_status.py
# In another terminal:
doeff-agentic watch <workflow-id>
```

### 03. Sequential Agents
Chain multiple agents - output of one feeds into the next.
```bash
uv run python examples/03_sequential_agents.py
```

### 04. Conditional Flow
Branch based on agent output.
```bash
uv run python examples/04_conditional_flow.py
```

### 05. Human-in-the-Loop
Pause workflow for human review.
```bash
uv run python examples/05_human_in_loop.py
# When waiting, in another terminal:
doeff-agentic send <workflow-id>:drafter "approve"
```

### 06. Parallel Agents
Run multiple agents concurrently using core `Spawn` + `Gather` effects.

**Pattern**: Effects are blocking by default. Use `Spawn` to opt into concurrent execution:
```python
# Blocking helper that sends message and waits for response
@do
def run_agent(session_id: str, content: str):
    yield AgenticSendMessage(session_id=session_id, content=content, wait=True)
    messages = yield AgenticGetMessages(session_id=session_id)
    return get_last_assistant_message(messages)

# Spawn makes blocking operations concurrent
tech_task = yield Spawn(run_agent(tech_session.id, "Analyze from tech perspective..."))
biz_task = yield Spawn(run_agent(biz_session.id, "Analyze from business perspective..."))

# Gather waits for all and collects results
tech_result, biz_result = yield Gather(tech_task, biz_task)
```

```bash
uv run python examples/06_parallel_agents.py
```

### 07. PR Review Workflow
Complete production-style workflow combining all patterns.
```bash
uv run python examples/07_pr_review_workflow.py https://github.com/org/repo/pull/123
```

### 08. Testing with Mock Handlers
Effect-based deterministic workflow using `MockAgenticHandler` + installer composition.
```bash
uv run python examples/08_testing_with_mocks.py
```

## Monitoring Workflows

While examples are running, you can monitor them:

```bash
# List all workflows
doeff-agentic ps

# Watch a specific workflow
doeff-agentic watch <workflow-id>

# Attach to agent's session
doeff-agentic attach <workflow-id>:<session-name>

# View agent output
doeff-agentic logs <workflow-id>

# Send a message to a session
doeff-agentic send <workflow-id>:<session-name> "your message"

# Stop a workflow
doeff-agentic stop <workflow-id>
```

## Using the Python API

You can also use the Python API directly:

```python
from doeff_agentic.api import AgenticAPI

api = AgenticAPI()

# List workflows
workflows = api.list_workflows()

# Get workflow details
wf = api.get_workflow("a3f")

# Watch for updates
for update in api.watch("a3f"):
    print(update.workflow.status)

# Send message
api.send_message("a3f", "continue")
```

## Testing Without OpenCode

You can test the implementation without requiring OpenCode:

### Test Event Logging
```bash
uv run python examples/test_event_logging.py
```
This tests the JSONL event logging system - creates workflows, sessions, environments and verifies state reconstruction.

### Test Workflow Patterns
```bash
uv run python examples/test_mock_workflow.py
```
This demonstrates workflow patterns (sequential, conditional, parallel) using mock data without any external services.

### Test Effect-Based Mock Handler Wiring
```bash
uv run python examples/08_testing_with_mocks.py
```
This demonstrates `AgenticCreateSession -> AgenticSendMessage -> AgenticGetMessages` with
`MockAgenticHandler` and no external service.

### Run Unit Tests
```bash
uv run pytest tests/ -v
```
Runs the unit tests for effects, types, event logging, and state management.

For comprehensive testing documentation, see [docs/testing-guide.md](../docs/testing-guide.md).

## Migration from Legacy API

If you have existing code using the old API, here's how to migrate:

| Old API | New API |
|---------|---------|
| `RunAgent(config=AgentConfig(...))` | `AgenticCreateSession(name=...)` + `AgenticSendMessage(wait=True)` |
| `SendMessage(session_name, msg)` | `AgenticSendMessage(session_id, content)` |
| `WaitForStatus(session_name, status)` | Loop with `AgenticNextEvent` |
| `CaptureOutput(session_name)` | `AgenticGetMessages(session_id)` |
| `WaitForUserInput(session_name, prompt)` | Loop with `AgenticNextEvent` checking BLOCKED status |
| `StopAgent(session_name)` | `AgenticAbortSession(session_id)` |
| `agentic_effectful_handlers()` | `opencode_handler()` |
