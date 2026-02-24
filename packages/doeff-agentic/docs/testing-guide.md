# doeff-agentic Testing Guide

This guide shows how to test the doeff-agentic implementation at different levels.

## Testing Levels

| Level | Requires | What it tests |
|-------|----------|---------------|
| Unit Tests | Nothing | Effects, types, event logging |
| Event Log Tests | Nothing | JSONL persistence, state reconstruction |
| Mock Integration | Nothing | Workflow patterns with mock handlers |
| Full Integration | OpenCode | End-to-end with real agent sessions |

## 1. Unit Tests (No Dependencies)

Run the full test suite:

```bash
cd packages/doeff-agentic
uv run pytest tests/ -v
```

Run specific test files:

```bash
# Test effect definitions
uv run pytest tests/test_new_effects.py -v

# Test type definitions
uv run pytest tests/test_new_types.py -v

# Test event logging
uv run pytest tests/test_event_log.py -v

# Test state management
uv run pytest tests/test_state.py -v
```

## 2. Event Logging Tests (No Dependencies)

The event logging system can be tested completely standalone.

### Interactive Testing

```python
from pathlib import Path
import tempfile
from datetime import datetime, timezone

from doeff_agentic import (
    EventLogWriter,
    EventLogReader,
    WorkflowIndex,
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticSessionHandle,
    AgenticSessionStatus,
)

# Create a temporary state directory
state_dir = Path(tempfile.mkdtemp())
print(f"State directory: {state_dir}")

# Initialize writer and reader
writer = EventLogWriter(state_dir)
reader = EventLogReader(state_dir)

# --- Simulate a workflow ---

# 1. Create workflow
workflow_id = "abc1234"
writer.log_workflow_created(workflow_id, "test-workflow", {"purpose": "testing"})
print(f"Created workflow: {workflow_id}")

# 2. Create environment
env = AgenticEnvironmentHandle(
    id="env-001",
    env_type=AgenticEnvironmentType.SHARED,
    name="shared-env",
    working_dir="/tmp/test",
    created_at=datetime.now(timezone.utc),
)
writer.log_environment_created(workflow_id, env)
print(f"Created environment: {env.id}")

# 3. Create session
session = AgenticSessionHandle(
    id="sess-001",
    name="reviewer",
    workflow_id=workflow_id,
    environment_id=env.id,
    status=AgenticSessionStatus.PENDING,
    created_at=datetime.now(timezone.utc),
    title="Code Reviewer",
)
writer.log_session_created(workflow_id, session)
print(f"Created session: {session.name}")

# 4. Simulate message exchange
writer.log_message_sent(workflow_id, "reviewer", "Review this code", wait=True)
writer.log_session_status(workflow_id, "reviewer", "running")
writer.log_message_complete(workflow_id, "reviewer", tokens=150)
writer.log_session_status(workflow_id, "reviewer", "done")
print("Simulated message exchange")

# 5. Complete workflow
writer.log_workflow_status(workflow_id, "done")
print("Workflow completed")

# --- Read back and verify ---

# List workflows
workflows = reader.list_workflows()
print(f"\nWorkflows: {workflows}")

# Reconstruct workflow state
workflow_state = reader.reconstruct_workflow_state(workflow_id)
print(f"\nWorkflow state:")
print(f"  ID: {workflow_state.id}")
print(f"  Name: {workflow_state.name}")
print(f"  Status: {workflow_state.status.value}")

# Reconstruct session state
session_state = reader.reconstruct_session_state(workflow_id, "reviewer")
print(f"\nSession state:")
print(f"  ID: {session_state.id}")
print(f"  Name: {session_state.name}")
print(f"  Status: {session_state.status.value}")

# Read raw events
events = reader.read_workflow_events(workflow_id)
print(f"\nTotal events: {len(events)}")
for event in events:
    print(f"  {event.event_type}: {event.data}")
```

### Run the Test Script

```bash
cd packages/doeff-agentic
uv run python examples/test_event_logging.py
```

## 3. Mock Integration Tests (No Dependencies)

Test workflow patterns using mock handlers that don't require real agent sessions.

### Create a Mock Handler

```python
from datetime import datetime, timezone
from doeff import WithHandler, default_handlers, do, run

from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticMessage,
    AgenticMessageHandle,
)


def mock_handler():
    """Create mock handlers for testing workflow patterns."""
    sessions = {}
    messages = {}
    msg_counter = [0]

    def handle_create_session(effect):
        session = AgenticSessionHandle(
            id=f"mock-sess-{len(sessions)}",
            name=effect.name,
            workflow_id="mock-workflow",
            environment_id="mock-env",
            status=AgenticSessionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            title=effect.title,
        )
        sessions[effect.name] = session
        messages[session.id] = []
        return session

    def handle_send_message(effect):
        session_id = effect.session_id
        msg_counter[0] += 1
        
        # Add user message
        user_msg = AgenticMessage(
            id=f"msg-{msg_counter[0]}",
            session_id=session_id,
            role="user",
            content=effect.content,
            created_at=datetime.now(timezone.utc),
        )
        messages[session_id].append(user_msg)
        
        # Simulate assistant response
        msg_counter[0] += 1
        assistant_msg = AgenticMessage(
            id=f"msg-{msg_counter[0]}",
            session_id=session_id,
            role="assistant",
            content=f"Mock response to: {effect.content[:50]}...",
            created_at=datetime.now(timezone.utc),
        )
        messages[session_id].append(assistant_msg)
        
        return AgenticMessageHandle(
            id=user_msg.id,
            session_id=session_id,
            role="user",
            created_at=user_msg.created_at,
        )

    def handle_get_messages(effect):
        return messages.get(effect.session_id, [])

    return {
        AgenticCreateSession: handle_create_session,
        AgenticSendMessage: handle_send_message,
        AgenticGetMessages: handle_get_messages,
    }


# Test a workflow with mock handlers
@do
def test_workflow():
    session = yield AgenticCreateSession(name="test-agent")
    yield AgenticSendMessage(
        session_id=session.id,
        content="Hello, world!",
        wait=True,
    )
    messages = yield AgenticGetMessages(session_id=session.id)
    return messages[-1].content


# Run the test
handlers = mock_handler()
program = WithHandler(handlers, test_workflow())
result = run(program, handlers=default_handlers())
print(f"Result: {result}")
```

### Run the Mock Test Script

```bash
cd packages/doeff-agentic
uv run python examples/test_mock_workflow.py
```

## 4. Full Integration Tests (Requires OpenCode)

For full integration tests with real agent sessions:

### Prerequisites

1. Install OpenCode:
   ```bash
   # Check if OpenCode is available
   which opencode
   opencode --version
   ```

2. The handler will auto-start OpenCode server if needed.

### Run Integration Example

```bash
cd packages/doeff-agentic
uv run python examples/01_hello_agent.py
```

### Monitor During Tests

In another terminal:
```bash
# List workflows
doeff-agentic ps

# Watch a workflow
doeff-agentic watch <workflow-id>

# View environment list
doeff-agentic env list
```

## 5. Testing the CLI

Test CLI commands:

```bash
cd packages/doeff-agentic

# List workflows (reads from ~/.local/state/doeff-agentic/)
uv run doeff-agentic ps

# List environments
uv run doeff-agentic env list

# JSON output for scripting
uv run doeff-agentic ps --json
uv run doeff-agentic env list --json
```

## 6. Testing Checklist

Before submitting changes, verify:

- [ ] `uv run pytest tests/ -v` passes (121 tests)
- [ ] `uv run python examples/test_event_logging.py` runs successfully
- [ ] `uv run python examples/test_mock_workflow.py` runs successfully
- [ ] `uv run doeff-agentic ps` works
- [ ] `uv run doeff-agentic env list` works

## Troubleshooting

### Import Errors

If you see import errors, ensure the package is installed:
```bash
cd packages/doeff-agentic
uv sync
```

### State Directory

The default state directory is `~/.local/state/doeff-agentic/`. You can inspect it:
```bash
ls -la ~/.local/state/doeff-agentic/workflows/
```

### Clean State

To start fresh:
```bash
rm -rf ~/.local/state/doeff-agentic/
```

### OpenCode Not Found

If OpenCode is not installed, the handler will fail to create sessions. Use mock handlers for testing workflow patterns without OpenCode.
