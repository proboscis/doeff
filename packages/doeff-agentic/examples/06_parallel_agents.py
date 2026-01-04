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

from doeff import do
from doeff.effects.writer import slog

from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
    AgenticGather,
)
from doeff_agentic.handler import agentic_effectful_handlers


def get_assistant_response(messages):
    """Extract the latest assistant response from messages."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    return ""


@do
def multi_perspective_analysis(topic: str):
    """Get multiple perspectives on a topic using parallel agents."""

    yield slog(status="analyzing", msg=f"Getting multiple perspectives on {topic}")

    # Create all sessions first
    tech = yield AgenticCreateSession(
        name="tech-analyst",
        title="Technical Analyst",
    )
    biz = yield AgenticCreateSession(
        name="biz-analyst",
        title="Business Analyst",
    )
    ux = yield AgenticCreateSession(
        name="ux-analyst",
        title="UX Analyst",
    )

    yield slog(status="launching", msg="Launching parallel agents")

    # Send messages to all agents (non-blocking)
    yield AgenticSendMessage(
        session_id=tech.id,
        content=f"Analyze '{topic}' from a technical perspective. 3 bullet points. Then exit.",
        wait=False,  # Don't wait - send and continue
    )
    yield AgenticSendMessage(
        session_id=biz.id,
        content=f"Analyze '{topic}' from a business perspective. 3 bullet points. Then exit.",
        wait=False,
    )
    yield AgenticSendMessage(
        session_id=ux.id,
        content=f"Analyze '{topic}' from a user experience perspective. 3 bullet points. Then exit.",
        wait=False,
    )

    yield slog(status="waiting", msg="Waiting for all agents to complete")

    # Wait for all to complete
    yield AgenticGather(
        session_names=("tech-analyst", "biz-analyst", "ux-analyst"),
        timeout=120.0,
    )

    yield slog(status="collecting", msg="Collecting results")

    # Collect results
    perspectives = []

    tech_msgs = yield AgenticGetMessages(session_id=tech.id)
    perspectives.append(("Technical", get_assistant_response(tech_msgs)))

    biz_msgs = yield AgenticGetMessages(session_id=biz.id)
    perspectives.append(("Business", get_assistant_response(biz_msgs)))

    ux_msgs = yield AgenticGetMessages(session_id=ux.id)
    perspectives.append(("User", get_assistant_response(ux_msgs)))

    yield slog(status="synthesizing", msg="Combining perspectives")

    combined = "\n\n".join([f"## {name}\n{text}" for name, text in perspectives])

    # Create synthesizer
    synthesizer = yield AgenticCreateSession(
        name="synthesizer",
        title="Synthesis Agent",
    )

    yield AgenticSendMessage(
        session_id=synthesizer.id,
        content=f"Synthesize these perspectives into 3 key insights:\n\n{combined}\n\nThen exit.",
        wait=True,
    )

    synth_msgs = yield AgenticGetMessages(session_id=synthesizer.id)
    synthesis = get_assistant_response(synth_msgs)

    yield slog(status="complete", msg="Analysis complete")

    return {
        "perspectives": dict(perspectives),
        "synthesis": synthesis,
    }


if __name__ == "__main__":
    from doeff import run_sync

    topic = "AI code assistants"

    print(f"Starting multi-perspective analysis: {topic}")
    print()

    handlers = agentic_effectful_handlers(
        workflow_name=f"analysis-{topic.replace(' ', '-')}",
    )

    try:
        result = run_sync(multi_perspective_analysis(topic), handlers=handlers)

        print("\n" + "=" * 50)
        print("ANALYSIS RESULTS")
        print("=" * 50)

        for name, text in result["perspectives"].items():
            print(f"\n### {name} Perspective")
            print(text[:300])

        print("\n### Synthesis")
        print(result["synthesis"][:500])
    except Exception as e:
        print(f"Error: {e}")
