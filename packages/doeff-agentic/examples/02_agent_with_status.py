"""
Example 02: Agent with Status Updates

Show workflow progress using slog (structured logging) with doeff-preset.

The slog status is:
- Displayed to console via rich (from preset_handlers)
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
from doeff_preset import preset_handlers

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
    from doeff import WithHandler, async_run, default_handlers

    async def main():
        print("Starting agent_with_status workflow...")
        print()
        print("You can watch the workflow in another terminal:")
        print("  doeff-agentic ps")
        print("  doeff-agentic watch <workflow-id>")
        print()

        # Merge preset handlers with opencode handlers
        # Preset provides: slog display (WriterTellEffect) + config (Ask preset.*)
        # OpenCode provides: agent session management effects
        program = WithHandler(
            preset_handlers(),
            WithHandler(
                opencode_handler(),
                agent_with_status(),
            ),
        )
        result = await async_run(program, handlers=default_handlers())

        if result.is_err():
            print("\n=== Workflow Failed ===")
            print(result.format())  # Rich error info: effect path, python stack, K stack
        else:
            print("\n=== Agent Output ===")
            output = result.value
            print(output[:500] if len(output) > 500 else output)
            print(f"\nCaptured {len(result.log)} slog messages in writer log")

    asyncio.run(main())
