"""
Example 05: Human-in-the-Loop

Pause workflow for human review.

This demonstrates how to create workflows that wait for
human input before continuing. The user can provide input via:
- doeff-agentic send <workflow-id> "approve"
- Attaching to the session

Run:
    cd packages/doeff-agentic
    uv run python examples/05_human_in_loop.py

In another terminal, when the workflow is waiting:
    doeff-agentic send <workflow-id> "approve"
    # or
    doeff-agentic send <workflow-id> "revise: make it shorter"
"""

from doeff import do
from doeff.effects.writer import slog

from doeff_agentic import (
    AgenticCreateSession,
    AgenticSendMessage,
    AgenticGetMessages,
    AgenticNextEvent,
    AgenticGetSessionStatus,
)
from doeff_agentic.types import AgenticSessionStatus
from doeff_agentic.handler import agentic_effectful_handlers


def get_assistant_response(messages):
    """Extract the latest assistant response from messages."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    return ""


@do
def draft_with_approval(task: str):
    """Create a draft and wait for human approval."""

    yield slog(status="drafting", msg="Creating initial draft")

    # Create drafter session
    drafter = yield AgenticCreateSession(
        name="drafter",
        title="Draft Agent",
    )

    yield AgenticSendMessage(
        session_id=drafter.id,
        content=f"{task}\n\nCreate a draft. Then wait for feedback.",
        wait=True,
    )

    messages = yield AgenticGetMessages(session_id=drafter.id)
    draft = get_assistant_response(messages)

    yield slog(status="waiting-approval", msg="Draft ready for review")

    # Display draft for user
    print("\n" + "=" * 50)
    print("DRAFT READY FOR REVIEW")
    print("=" * 50)
    print("\nDraft:")
    print(draft[:500])
    print("\n" + "=" * 50)
    print("To continue, run in another terminal:")
    print("  doeff-agentic send <workflow-id> 'approve'")
    print("  doeff-agentic send <workflow-id> 'revise: <feedback>'")
    print("  doeff-agentic send <workflow-id> 'reject'")
    print("=" * 50 + "\n")

    # Wait for session to become blocked (waiting for input)
    while True:
        status = yield AgenticGetSessionStatus(session_id=drafter.id)
        if status == AgenticSessionStatus.BLOCKED:
            break
        if status in (AgenticSessionStatus.DONE, AgenticSessionStatus.ERROR):
            break
        # Wait for next event
        yield AgenticNextEvent(session_id=drafter.id, timeout=5.0)

    # When user sends a message, the session will process it
    # Wait for the response
    yield AgenticNextEvent(session_id=drafter.id, timeout=300.0)  # 5 min timeout

    messages = yield AgenticGetMessages(session_id=drafter.id)
    approval = get_assistant_response(messages)

    if "revise" in approval.lower():
        feedback = approval
        yield slog(status="revising", msg=f"Revising based on feedback")

        # Create reviser session
        reviser = yield AgenticCreateSession(
            name="reviser",
            title="Revision Agent",
        )

        yield AgenticSendMessage(
            session_id=reviser.id,
            content=(
                f"Revise based on this feedback:\n{feedback}\n\n"
                f"Original draft:\n{draft}\n\n"
                "Output the revised version. Then exit."
            ),
            wait=True,
        )

        messages = yield AgenticGetMessages(session_id=reviser.id)
        revised = get_assistant_response(messages)
        return {"status": "revised", "content": revised}

    if "reject" in approval.lower():
        yield slog(status="rejected", msg="Draft rejected")
        return {"status": "rejected", "content": draft}

    yield slog(status="approved", msg="Draft approved!")
    return {"status": "approved", "content": draft}


if __name__ == "__main__":
    from doeff import run_sync

    task = "Write a haiku about programming"

    print("Starting human-in-the-loop workflow...")
    print(f"Task: {task}")
    print()

    handlers = agentic_effectful_handlers(
        workflow_name="draft-approval",
    )

    try:
        result = run_sync(draft_with_approval(task), handlers=handlers)
        print(f"\n=== Result: {result['status'].upper()} ===")
        print(result["content"][:500])
    except Exception as e:
        print(f"Error: {e}")
