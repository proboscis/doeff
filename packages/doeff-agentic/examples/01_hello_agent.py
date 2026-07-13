"""
Example 01: Hello Agent

Minimal example - launch a single agent and get output.

This is the simplest possible agent workflow using the new spec-compliant API.

Run:
    cd packages/doeff-agentic
    uv run python examples/01_hello_agent.py
"""

from doeff_agentic import (
    AgenticCreateSession,
    AgenticGetMessages,
    AgenticSendMessage,
)
from doeff_agentic.opencode_handler import opencode_handler

from doeff import do


@do
def hello_agent():
    """Simplest possible agent workflow."""
    # Create a session
    session = yield AgenticCreateSession(name="hello-agent")

    # Send a message and wait for completion
    yield AgenticSendMessage(
        session_id=session.id,
        content="Say hello and list 3 fun facts about Python. Then exit with /exit.",
        wait=True,
    )

    # Get the messages to extract the response
    messages = yield AgenticGetMessages(session_id=session.id)

    # Return the last assistant message
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content

    return "No response received"


if __name__ == "__main__":
    import asyncio

    from _runtime import run_program

    async def main():
        print("Starting hello_agent workflow...")
        print("This will launch an OpenCode agent session.")
        print()
        # OpenCode provides: agent session management effects
        program = opencode_handler()(hello_agent())
        try:
            output = await run_program(program)
        except Exception as e:
            print("\n=== Workflow Failed ===")
            print(f"Error: {e}")
        else:
            print("\n=== Agent Output ===")
            print(output[:500] if len(output) > 500 else output)

    asyncio.run(main())
