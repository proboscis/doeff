"""
Example 02: Agent with Status Updates

Show workflow progress using slog (structured logging).

The slog status is visible via:
- doeff-agentic watch <workflow-id>
- doeff-agentic ps

Run:
    cd packages/doeff-agentic
    uv run python examples/02_agent_with_status.py

In another terminal:
    doeff-agentic watch <workflow-id>
"""

from doeff import do
from doeff.effects.writer import slog

from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
)
from doeff_agentic.handler import agentic_effectful_handlers


@do
def agent_with_status():
    """Workflow with status updates visible in the CLI."""
    yield slog(status="starting", msg="Launching agent...")

    # Create session
    session = yield AgenticCreateSession(
        name="counter",
        title="Counter Agent",
    )

    # Send message and wait
    yield AgenticSendMessage(
        session_id=session.id,
        content="Count from 1 to 5, with a 1 second pause between each number. Then exit.",
        wait=True,
    )

    # Get response
    messages = yield AgenticGetMessages(session_id=session.id)

    result = ""
    for msg in reversed(messages):
        if msg.role == "assistant":
            result = msg.content
            break

    yield slog(status="complete", msg=f"Agent finished: {result[:50]}...")
    return result


if __name__ == "__main__":
    from doeff import run_sync

    print("Starting agent_with_status workflow...")
    print()
    print("You can watch the workflow in another terminal:")
    print("  doeff-agentic ps")
    print("  doeff-agentic watch <workflow-id>")
    print()

    handlers = agentic_effectful_handlers(
        workflow_name="counter-example",
    )

    try:
        result = run_sync(agent_with_status(), handlers=handlers)
        print("\n=== Agent Output ===")
        print(result[:500] if len(result) > 500 else result)
    except Exception as e:
        print(f"Error: {e}")
