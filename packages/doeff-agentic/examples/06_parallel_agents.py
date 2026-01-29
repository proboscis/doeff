"""
Example 06: Parallel Agents

Run multiple agents concurrently using core Spawn + Gather effects.

This demonstrates how to run multiple agents in parallel
and collect their results. Each agent runs in its own
session simultaneously.

Pattern:
    - Effects are blocking by default
    - Use Spawn to opt into concurrent execution
    - Use Gather to wait for all spawned tasks

Run:
    cd packages/doeff-agentic
    uv run python examples/06_parallel_agents.py
"""

from doeff_agentic import (
    AgenticCreateSession,
    AgenticGetMessages,
    AgenticMessage,
    AgenticSendMessage,
    with_visual_logging,
)
from doeff_agentic.opencode_handler import opencode_handler

from doeff import Gather, Spawn, do
from doeff.effects.writer import slog


def get_last_assistant_message(messages: list[AgenticMessage]) -> str:
    """Extract the last assistant message from a list of messages."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    return ""


@do
def run_agent(session_id: str, content: str):
    """Send message to agent and wait for completion, returning the response.

    This is a blocking operation - use Spawn() to run multiple agents concurrently.
    """
    yield AgenticSendMessage(session_id=session_id, content=content, wait=True)
    messages = yield AgenticGetMessages(session_id=session_id)
    return get_last_assistant_message(messages)


@do
def multi_perspective_analysis(topic: str):
    """Get multiple perspectives on a topic using parallel agents."""

    yield slog(status="analyzing", msg=f"Getting multiple perspectives on {topic}")

    # Create all sessions first
    tech_session = yield AgenticCreateSession(name="tech-analyst")
    biz_session = yield AgenticCreateSession(name="biz-analyst")
    ux_session = yield AgenticCreateSession(name="ux-analyst")

    # Spawn concurrent agent tasks
    # Each task sends a message and waits for completion (blocking)
    # Spawn makes them run concurrently
    yield slog(status="launching", msg="Launching parallel analysis agents")

    tech_task = yield Spawn(
        run_agent(
            tech_session.id,
            f"Analyze '{topic}' from a technical perspective. 3 bullet points. Then exit.",
        )
    )
    biz_task = yield Spawn(
        run_agent(
            biz_session.id,
            f"Analyze '{topic}' from a business perspective. 3 bullet points. Then exit.",
        )
    )
    ux_task = yield Spawn(
        run_agent(
            ux_session.id,
            f"Analyze '{topic}' from a user experience perspective. 3 bullet points. Then exit.",
        )
    )

    # Wait for all to complete and collect results
    yield slog(status="waiting", msg="Waiting for all agents to complete")
    tech_result, biz_result, ux_result = yield Gather(tech_task, biz_task, ux_task)

    perspectives = {
        "tech-analyst": tech_result,
        "biz-analyst": biz_result,
        "ux-analyst": ux_result,
    }

    # Synthesize results (single blocking call)
    yield slog(status="synthesizing", msg="Combining perspectives")

    combined = "\n\n".join([f"## {name}\n{text}" for name, text in perspectives.items()])

    synthesizer = yield AgenticCreateSession(name="synthesizer")
    synthesis = yield run_agent(
        synthesizer.id,
        f"Synthesize these perspectives into 3 key insights:\n\n{combined}\n\nThen exit.",
    )

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

        result = await runtime.run(with_visual_logging(multi_perspective_analysis(topic)))

        if result.is_err():
            print("\n=== Workflow Failed ===")
            print(result.format())  # Rich error info: effect path, python stack, K stack
        else:
            output = result.value

            print("\n" + "=" * 50)
            print("ANALYSIS RESULTS")
            print("=" * 50)

            for name, text in output["perspectives"].items():
                print(f"\n### {name}")
                print(text[:300])

            print("\n### Synthesis")
            print(output["synthesis"][:500])

    asyncio.run(main())
