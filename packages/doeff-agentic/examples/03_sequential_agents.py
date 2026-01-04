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

from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
)
from doeff_agentic.handler import agentic_effectful_handlers


def get_assistant_response(messages):
    """Extract the latest assistant response from messages."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    return ""


@do
def research_and_summarize(topic: str):
    """Two-agent workflow: research then summarize."""

    yield slog(status="researching", msg=f"Researching {topic}")

    # Create researcher session
    researcher = yield AgenticCreateSession(
        name="researcher",
        title="Research Agent",
    )

    yield AgenticSendMessage(
        session_id=researcher.id,
        content=f"Research {topic} and list 5 key points. Be concise. Then exit.",
        wait=True,
    )

    messages = yield AgenticGetMessages(session_id=researcher.id)
    research = get_assistant_response(messages)

    yield slog(status="summarizing", msg="Creating summary")

    # Create summarizer session
    summarizer = yield AgenticCreateSession(
        name="summarizer",
        title="Summary Agent",
    )

    yield AgenticSendMessage(
        session_id=summarizer.id,
        content=f"Summarize this research in 2-3 sentences:\n{research}\n\nThen exit.",
        wait=True,
    )

    messages = yield AgenticGetMessages(session_id=summarizer.id)
    summary = get_assistant_response(messages)

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
