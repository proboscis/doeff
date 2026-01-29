"""
Example 06: Parallel Agents

Run multiple agents concurrently using AgenticGather.

This demonstrates how to run multiple agents in parallel
and collect their results. Each agent runs in its own
session simultaneously.

Run:
    cd packages/doeff-agentic
    uv run python examples/06_parallel_agents.py
"""

from doeff_agentic import (
    AgenticCreateSession,
    AgenticGather,
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
def multi_perspective_analysis(topic: str):
    """Get multiple perspectives on a topic using parallel agents."""

    yield slog(status="analyzing", msg=f"Getting multiple perspectives on {topic}")

    # Create all sessions first
    tech_session = yield AgenticCreateSession(name="tech-analyst")
    biz_session = yield AgenticCreateSession(name="biz-analyst")
    ux_session = yield AgenticCreateSession(name="ux-analyst")

    # Send messages to all (don't wait)
    yield slog(status="launching", msg="Launching parallel analysis agents")

    yield AgenticSendMessage(
        session_id=tech_session.id,
        content=f"Analyze '{topic}' from a technical perspective. 3 bullet points. Then exit.",
        wait=False,
    )
    yield AgenticSendMessage(
        session_id=biz_session.id,
        content=f"Analyze '{topic}' from a business perspective. 3 bullet points. Then exit.",
        wait=False,
    )
    yield AgenticSendMessage(
        session_id=ux_session.id,
        content=f"Analyze '{topic}' from a user experience perspective. 3 bullet points. Then exit.",
        wait=False,
    )

    # Wait for all to complete
    yield slog(status="waiting", msg="Waiting for all agents to complete")

    final_sessions = yield AgenticGather(
        session_names=("tech-analyst", "biz-analyst", "ux-analyst"),
        timeout=300.0,
    )

    # Collect results
    yield slog(status="collecting", msg="Collecting results")

    perspectives = {}
    for name, session in final_sessions.items():
        messages = yield AgenticGetMessages(session_id=session.id)
        perspectives[name] = get_last_assistant_message(messages)

    # Synthesize results
    yield slog(status="synthesizing", msg="Combining perspectives")

    combined = "\n\n".join([f"## {name}\n{text}" for name, text in perspectives.items()])

    synthesizer = yield AgenticCreateSession(name="synthesizer")
    yield AgenticSendMessage(
        session_id=synthesizer.id,
        content=f"Synthesize these perspectives into 3 key insights:\n\n{combined}\n\nThen exit.",
        wait=True,
    )
    messages = yield AgenticGetMessages(session_id=synthesizer.id)
    synthesis = get_last_assistant_message(messages)

    yield slog(status="complete", msg="Analysis complete")

    return {
        "perspectives": perspectives,
        "synthesis": synthesis,
    }


if __name__ == "__main__":
    import asyncio
    from doeff import AsyncRuntime

    async def main():
        topic = "AI code assistants"

        print(f"Starting multi-perspective analysis: {topic}")
        print()

        handlers = opencode_handler()
        runtime = AsyncRuntime(handlers=handlers)

        try:
            result = await runtime.run(multi_perspective_analysis(topic))
            output = result.value

            print("\n" + "=" * 50)
            print("ANALYSIS RESULTS")
            print("=" * 50)

            for name, text in output["perspectives"].items():
                print(f"\n### {name}")
                print(text[:300])

            print("\n### Synthesis")
            print(output["synthesis"][:500])
        except Exception as e:
            print(f"Error: {e}")

    asyncio.run(main())
