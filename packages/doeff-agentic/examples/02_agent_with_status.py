"""
Example 02: Agent with Status Updates

Show workflow progress using slog (structured logging) with doeff-core-effects.

The slog status is:
- Displayed on stderr by the standard handler stack (run_program)
- Visible via doeff-agentic watch/ps commands

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

from doeff import do, slog


def get_last_assistant_message(messages: list[AgenticMessage]) -> str:
    """Extract the last assistant message from a list of messages."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    return ""


@do
def agent_with_status():
    """Workflow with status updates visible in the CLI."""
    yield slog(msg="Launching agent...", status="starting")

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

    yield slog(msg=f"Agent finished: {result[:50]}...", status="complete")
    return result


if __name__ == "__main__":
    import asyncio

    from _runtime import run_program

    async def main():
        print("Starting agent_with_status workflow...")
        print()
        print("You can watch the workflow in another terminal:")
        print("  doeff-agentic ps")
        print("  doeff-agentic watch <workflow-id>")
        print()
        # OpenCode provides: agent session management effects
        program = opencode_handler()(agent_with_status())
        try:
            output = await run_program(program)
        except Exception as e:
            print("\n=== Workflow Failed ===")
            print(f"Error: {e}")
        else:
            print("\n=== Agent Output ===")
            print(output[:500] if len(output) > 500 else output)

    asyncio.run(main())
