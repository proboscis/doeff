"""
Example 05: Human-in-the-Loop

Pause workflow for human review.

This demonstrates how to create workflows that wait for
human input before continuing. The user can provide input via:
- doeff-agentic send <workflow-id>:<session-name> "approve"
- Attaching to the session

Run:
    cd packages/doeff-agentic
    uv run python examples/05_human_in_loop.py

In another terminal, when the workflow is waiting:
    doeff-agentic send <workflow-id>:drafter "approve"
    # or
    doeff-agentic send <workflow-id>:drafter "revise: make it shorter"
"""

from doeff import do
from doeff.effects.writer import slog

from doeff_agentic import (
    AgenticCreateSession,
    AgenticEndOfEvents,
    AgenticGetMessages,
    AgenticNextEvent,
    AgenticSendMessage,
    AgenticSessionStatus,
)
from doeff_agentic.opencode_handler import opencode_handler


def get_last_assistant_message(messages: list) -> str:
    """Extract the last assistant message from a list of messages."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    return ""


@do
def wait_for_user_input(session_id: str, prompt: str, timeout: float = 300.0):
    """Wait for user input by monitoring session events.

    This is the new pattern replacing WaitForUserInput effect.
    It monitors the session for BLOCKED status, which indicates
    the agent is waiting for user input.
    """
    print(f"\n{prompt}")
    print("Waiting for input...")

    # Monitor events until we get user input or timeout
    import time

    start = time.time()
    while time.time() - start < timeout:
        event = yield AgenticNextEvent(session_id=session_id, timeout=5.0)

        if isinstance(event, AgenticEndOfEvents):
            break

        # Check if session became blocked (waiting for input)
        if event.event_type == "session.blocked":
            # Session is waiting - user needs to send input
            continue

        # Check if we got a new user message (input received)
        if event.event_type == "message.started" and event.data.get("role") == "user":
            messages = yield AgenticGetMessages(session_id=session_id)
            # Find the latest user message (the input)
            for msg in reversed(messages):
                if msg.role == "user":
                    return msg.content
            break

    # Timeout or end of events
    return None


@do
def draft_with_approval(task: str):
    """Create a draft and wait for human approval."""

    yield slog(status="drafting", msg="Creating initial draft")

    # Create drafter session
    drafter = yield AgenticCreateSession(name="drafter")

    # Send initial task
    yield AgenticSendMessage(
        session_id=drafter.id,
        content=f"{task}\n\nCreate a draft. Then exit.",
        wait=True,
    )

    messages = yield AgenticGetMessages(session_id=drafter.id)
    draft = get_last_assistant_message(messages)

    yield slog(status="waiting-approval", msg="Draft ready for review")

    # Show draft to user
    print("\n" + "=" * 50)
    print("DRAFT READY FOR REVIEW")
    print("=" * 50)
    print("\nDraft:")
    print(draft[:500])
    print("\n" + "=" * 50)
    print("To continue, run in another terminal:")
    print("  doeff-agentic send <workflow-id>:drafter 'approve'")
    print("  doeff-agentic send <workflow-id>:drafter 'revise: <feedback>'")
    print("  doeff-agentic send <workflow-id>:drafter 'reject'")
    print("=" * 50 + "\n")

    # Wait for user input
    approval = yield from wait_for_user_input(
        session_id=drafter.id,
        prompt="Review the draft. Reply: approve / revise <feedback> / reject",
        timeout=300.0,
    )

    if approval is None:
        yield slog(status="timeout", msg="No response received")
        return {"status": "timeout", "content": draft}

    if approval.lower().startswith("revise"):
        feedback = approval.replace("revise", "").strip(": ")
        yield slog(status="revising", msg=f"Revising based on: {feedback}")

        # Create reviser session
        reviser = yield AgenticCreateSession(name="reviser")
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
        revised = get_last_assistant_message(messages)
        return {"status": "revised", "content": revised}

    if approval.lower() == "reject":
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

    handlers = opencode_handler()

    try:
        result = run_sync(draft_with_approval(task), handlers=handlers)
        print(f"\n=== Result: {result['status'].upper()} ===")
        print(result["content"][:500])
    except Exception as e:
        print(f"Error: {e}")
