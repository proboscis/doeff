# doeff-agentic Examples

Progressive examples that teach you how to build agent workflows using the new effects API.

## Prerequisites

1. Install doeff-agentic:
   ```bash
   cd packages/doeff-agentic
   uv sync
   ```

2. Ensure you have an agent backend available:
   - **OpenCode** (recommended): `opencode --version`
   - **tmux** (fallback): `tmux -V`

## Effects API Overview

The new effects API uses a layered approach:

| Effect | Description |
|--------|-------------|
| `AgenticCreateSession` | Create a new agent session |
| `AgenticSendMessage` | Send a message to a session |
| `AgenticGetMessages` | Get messages from a session |
| `AgenticNextEvent` | Wait for next event (SSE) |
| `AgenticGather` | Wait for multiple sessions |
| `AgenticGetSessionStatus` | Get session status |

## Examples

### 01. Hello Agent
Minimal example - create a session and get a response.
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
doeff-agentic send <workflow-id> "approve"
```

### 06. Parallel Agents
Run multiple agents concurrently with AgenticGather.
```bash
uv run python examples/06_parallel_agents.py
```

### 07. PR Review Workflow
Complete production-style workflow combining all patterns.
```bash
uv run python examples/07_pr_review_workflow.py https://github.com/org/repo/pull/123
```

## Monitoring Workflows

While examples are running, you can monitor them:

```bash
# List all workflows
doeff-agentic ps

# Watch a specific workflow
doeff-agentic watch <workflow-id>

# Attach to agent's session
doeff-agentic attach <workflow-id>

# View agent output
doeff-agentic logs <workflow-id>

# Send a message to an agent
doeff-agentic send <workflow-id> "your message"

# Stop a workflow
doeff-agentic stop <workflow-id>
```

## Using the API

You can also use the Python API directly:

```python
from doeff import do
from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
)
from doeff_agentic.handler import agentic_effectful_handlers


@do
def my_workflow():
    # Create a session
    session = yield AgenticCreateSession(
        name="my-agent",
        title="My Agent",
    )
    
    # Send a message and wait for response
    yield AgenticSendMessage(
        session_id=session.id,
        content="Hello, agent!",
        wait=True,
    )
    
    # Get the response
    messages = yield AgenticGetMessages(session_id=session.id)
    
    # Find assistant's response
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    
    return "No response"


# Run the workflow
from doeff import run_sync

handlers = agentic_effectful_handlers(workflow_name="my-workflow")
result = run_sync(my_workflow(), handlers=handlers)
print(result)
```

## Migration from Legacy API

The legacy `RunAgent` effect is deprecated. Use the new effects instead:

### Before (Legacy)
```python
from doeff_agentic import RunAgent, AgentConfig

result = yield RunAgent(
    config=AgentConfig(
        agent_type="claude",
        prompt="Hello!",
    ),
    session_name="my-agent",
)
```

### After (New API)
```python
from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
)

session = yield AgenticCreateSession(name="my-agent")

yield AgenticSendMessage(
    session_id=session.id,
    content="Hello!",
    wait=True,
)

messages = yield AgenticGetMessages(session_id=session.id)
result = messages[-1].content  # Get last message
```

The new API provides:
- More control over session lifecycle
- Non-blocking message sending (`wait=False`)
- SSE event streaming (`AgenticNextEvent`)
- Session forking (OpenCode only)
- Parallel execution (`AgenticGather`, `AgenticRace`)
