"""
Example 05: Human-in-the-Loop

Pause workflow for human review.

This demonstrates how to create workflows that wait for
human input before continuing. The user can provide input via:
- doeff-agentic send <workflow-id> "approve"
- Attaching to the tmux session

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

from doeff_agentic import AgentConfig, RunAgent, WaitForUserInput
from doeff_agentic.handler import agentic_effectful_handlers


@do
def draft_with_approval(task: str):
    """Create a draft and wait for human approval."""

    yield slog(status="drafting", msg="Creating initial draft")

    draft = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=f"{task}\n\nCreate a draft. Then exit.",
        ),
        session_name="drafter",
    )

    yield slog(status="waiting-approval", msg="Draft ready for review")

    # Workflow pauses here - user reviews via CLI or tmux
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

    approval = yield WaitForUserInput(
        session_name="drafter",
        prompt="Review the draft. Reply: approve / revise <feedback> / reject",
        timeout=300,  # 5 minute timeout
    )

    if approval.lower().startswith("revise"):
        feedback = approval.replace("revise", "").strip(": ")
        yield slog(status="revising", msg=f"Revising based on: {feedback}")

        revised = yield RunAgent(
            config=AgentConfig(
                agent_type="claude",
                prompt=(
                    f"Revise based on this feedback:\n{feedback}\n\n"
                    f"Original draft:\n{draft}\n\n"
                    "Output the revised version. Then exit."
                ),
            ),
            session_name="reviser",
        )
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

    handlers = agentic_effectful_handlers(
        workflow_name="draft-approval",
    )

    try:
        result = run_sync(draft_with_approval(task), handlers=handlers)
        print(f"\n=== Result: {result['status'].upper()} ===")
        print(result["content"][:500])
    except Exception as e:
        print(f"Error: {e}")
