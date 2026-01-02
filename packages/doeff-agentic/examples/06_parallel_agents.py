"""
Example 06: Parallel Agents

Run multiple agents concurrently using Gather.

This demonstrates how to run multiple agents in parallel
and collect their results. Each agent runs in its own
tmux session simultaneously.

Note: This requires doeff's Gather effect to be properly
configured with the agentic handler.

Run:
    cd packages/doeff-agentic
    uv run python examples/06_parallel_agents.py
"""

from doeff import do
from doeff.effects.writer import slog

from doeff_agentic import AgentConfig, RunAgent
from doeff_agentic.handler import agentic_effectful_handlers


@do
def multi_perspective_analysis(topic: str):
    """Get multiple perspectives on a topic (sequential version).

    Note: True parallelism with Gather requires CESK handler integration.
    This example shows the sequential version which is simpler.
    """

    yield slog(status="analyzing", msg=f"Getting multiple perspectives on {topic}")

    perspectives = []

    # Technical perspective
    yield slog(status="technical", msg="Getting technical perspective")
    tech = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=f"Analyze '{topic}' from a technical perspective. 3 bullet points. Then exit.",
        ),
        session_name="tech-analyst",
    )
    perspectives.append(("Technical", tech))

    # Business perspective
    yield slog(status="business", msg="Getting business perspective")
    biz = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=f"Analyze '{topic}' from a business perspective. 3 bullet points. Then exit.",
        ),
        session_name="biz-analyst",
    )
    perspectives.append(("Business", biz))

    # User perspective
    yield slog(status="user", msg="Getting user perspective")
    user = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=f"Analyze '{topic}' from a user experience perspective. 3 bullet points. Then exit.",
        ),
        session_name="ux-analyst",
    )
    perspectives.append(("User", user))

    yield slog(status="synthesizing", msg="Combining perspectives")

    combined = "\n\n".join([f"## {name}\n{text}" for name, text in perspectives])

    synthesis = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=f"Synthesize these perspectives into 3 key insights:\n\n{combined}\n\nThen exit.",
        ),
        session_name="synthesizer",
    )

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
