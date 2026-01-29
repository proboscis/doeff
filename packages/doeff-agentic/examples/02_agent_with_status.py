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

from doeff_agentic import (
    AgenticCreateSession,
    AgenticGetMessages,
    AgenticMessage,
    AgenticSendMessage,
)
from doeff_agentic.opencode_handler import opencode_handler

from doeff import do
from doeff.effects.writer import slog


def get_last_assistant_message(messages: list[AgenticMessage]) -> str:
    """Extract the last assistant message from a list of messages."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    return ""


@do
def agent_with_status():
    """Workflow with status updates visible in the CLI."""
    yield slog(status="starting", msg="Launching agent...")

    # Create session
    session = yield AgenticCreateSession(name="counter")

    # Send message and wait for completion
    yield AgenticSendMessage(
        session_id=session.id,
        content="Count from 1 to 5, with a 1 second pause between each number. Then exit.",
        wait=True,
    )

    # Get messages
    messages = yield AgenticGetMessages(session_id=session.id)
    result = get_last_assistant_message(messages)

    yield slog(status="complete", msg=f"Agent finished: {result[:50]}...")
    return result


if __name__ == "__main__":
    import asyncio
    from doeff import AsyncRuntime

    async def main():
        print("Starting agent_with_status workflow...")
        print()
        print("You can watch the workflow in another terminal:")
        print("  doeff-agentic ps")
        print("  doeff-agentic watch <workflow-id>")
        print()

        handlers = opencode_handler()
        runtime = AsyncRuntime(handlers=handlers)

        try:
            result = await runtime.run(agent_with_status())
            print("\n=== Agent Output ===")
            output = result.value
            print(output[:500] if len(output) > 500 else output)
        except Exception as e:
            print(f"Error: {e}")

    asyncio.run(main())
