"""
Example 03: Sequential Agents

Chain multiple agents - output of one feeds into the next.

This demonstrates how to compose agent workflows where
the result of one agent is used as input to another.

Run:
    cd packages/doeff-agentic
    uv run python examples/03_sequential_agents.py
"""

from doeff import do
from doeff.effects.writer import slog

from doeff_agentic import AgentConfig, RunAgent
from doeff_agentic.handler import agentic_effectful_handlers


@do
def research_and_summarize(topic: str):
    """Two-agent workflow: research then summarize."""

    yield slog(status="researching", msg=f"Researching {topic}")

    research = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=f"Research {topic} and list 5 key points. Be concise. Then exit.",
        ),
        session_name="researcher",
    )

    yield slog(status="summarizing", msg="Creating summary")

    summary = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=f"Summarize this research in 2-3 sentences:\n{research}\n\nThen exit.",
        ),
        session_name="summarizer",
    )

    yield slog(status="complete", msg="Done!")

    return {"research": research, "summary": summary}


if __name__ == "__main__":
    from doeff import run_sync

    topic = "functional programming"

    print(f"Starting research workflow for: {topic}")
    print()

    handlers = agentic_effectful_handlers(
        workflow_name=f"research-{topic.replace(' ', '-')}",
    )

    try:
        result = run_sync(research_and_summarize(topic), handlers=handlers)
        print("\n=== Research ===")
        print(result["research"][:500])
        print("\n=== Summary ===")
        print(result["summary"][:500])
    except Exception as e:
        print(f"Error: {e}")
