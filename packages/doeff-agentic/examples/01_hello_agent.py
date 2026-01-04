"""
Example 01: Hello Agent

Minimal example - launch a single agent and get output.

This is the simplest possible agent workflow using the new effects API.

Run:
    cd packages/doeff-agentic
    uv run python examples/01_hello_agent.py
"""

from doeff import do

from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
)
from doeff_agentic.handler import agentic_effectful_handlers


@do
def hello_agent():
    """Simplest possible agent workflow using new effects API."""
    # Create a session
    session = yield AgenticCreateSession(
        name="hello",
        title="Hello Agent",
    )

    # Send a message and wait for response
    yield AgenticSendMessage(
        session_id=session.id,
        content="Say hello and list 3 fun facts about Python. Then exit with /exit.",
        wait=True,
    )

    # Get the response
    messages = yield AgenticGetMessages(session_id=session.id)

    # Return assistant's response
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content

    return "No response received"


if __name__ == "__main__":
    from doeff import run_sync

    print("Starting hello_agent workflow...")
    print("This will launch an agent session.")
    print()

    handlers = agentic_effectful_handlers(
        workflow_id=None,  # Auto-generate
        workflow_name="hello-agent",
    )

    try:
        result = run_sync(hello_agent(), handlers=handlers)
        print("\n=== Agent Output ===")
        print(result[:500] if len(result) > 500 else result)
    except Exception as e:
        print(f"Error: {e}")
