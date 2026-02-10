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
from doeff_preset import preset_handlers

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
    from doeff import async_run, default_handlers
    from doeff_agentic.runtime import with_handler_maps

    async def main():
        print("Starting hello_agent workflow...")
        print("This will launch an OpenCode agent session.")
        print()

        # Merge preset handlers with opencode handlers
        # Preset provides: slog display (WriterTellEffect) + config (Ask preset.*)
        # OpenCode provides: agent session management effects
        program = with_handler_maps(
            hello_agent(),
            preset_handlers(),
            opencode_handler(),
        )
        result = await async_run(program, handlers=default_handlers())

        if result.is_err():
            print("\n=== Workflow Failed ===")
            print(result.format())  # Rich error info: effect path, python stack, K stack
        else:
            print("\n=== Agent Output ===")
            output = result.value
            print(output[:500] if len(output) > 500 else output)

    asyncio.run(main())
