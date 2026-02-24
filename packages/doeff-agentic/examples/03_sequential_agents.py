"""
Example 03: Sequential Agents

Chain multiple agents - output of one feeds into the next.

This demonstrates how to compose agent workflows where
the result of one agent is used as input to another.

Run:
    cd packages/doeff-agentic
    uv run python examples/03_sequential_agents.py
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
def research_and_summarize(topic: str):
    """Two-agent workflow: research then summarize."""

    yield slog(status="researching", msg=f"Researching {topic}")

    # Agent 1: Researcher
    researcher = yield AgenticCreateSession(name="researcher")
    yield AgenticSendMessage(
        session_id=researcher.id,
        content=f"Research {topic} and list 5 key points. Be concise. Then exit.",
        wait=True,
    )
    messages = yield AgenticGetMessages(session_id=researcher.id)
    research = get_last_assistant_message(messages)

    yield slog(status="summarizing", msg="Creating summary")

    # Agent 2: Summarizer
    summarizer = yield AgenticCreateSession(name="summarizer")
    yield AgenticSendMessage(
        session_id=summarizer.id,
        content=f"Summarize this research in 2-3 sentences:\n{research}\n\nThen exit.",
        wait=True,
    )
    messages = yield AgenticGetMessages(session_id=summarizer.id)
    summary = get_last_assistant_message(messages)

    yield slog(status="complete", msg="Done!")

    return {"research": research, "summary": summary}


if __name__ == "__main__":
    import asyncio
    from doeff import WithHandler, async_run, default_handlers

    async def main():
        topic = "functional programming"

        print(f"Starting research workflow for: {topic}")
        print()

        # Merge preset handlers with opencode handlers
        # Preset provides: slog display (WriterTellEffect) + config (Ask preset.*)
        # OpenCode provides: agent session management effects
        program = WithHandler(
            preset_handlers(),
            WithHandler(
                opencode_handler(),
                research_and_summarize(topic),
            ),
        )
        result = await async_run(program, handlers=default_handlers())

        if result.is_err():
            print("\n=== Workflow Failed ===")
            print(result.format())  # Rich error info: effect path, python stack, K stack
        else:
            output = result.value
            print("\n=== Research ===")
            print(output["research"][:500])
            print("\n=== Summary ===")
            print(output["summary"][:500])

    asyncio.run(main())
