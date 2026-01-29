"""
Example 07: PR Review Workflow (Complete)

Full production-style workflow combining all patterns:
- Status updates (slog)
- Sequential agents (review → fix)
- Conditional flow (fix only if issues found)
- Human-in-the-loop (optional approval)

This is the complete example from the design docs.

Run:
    cd packages/doeff-agentic
    uv run python examples/07_pr_review_workflow.py
"""

import time

from doeff_agentic import (
    AgenticCreateSession,
    AgenticEndOfEvents,
    AgenticGetMessages,
    AgenticMessage,
    AgenticNextEvent,
    AgenticSendMessage,
    AgenticTimeoutError,
    with_visual_logging,
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
def wait_for_user_input(session_id: str, prompt: str, timeout: float = 300.0):
    """Wait for user input by polling for new messages.

    Uses message-delta polling which is handler-agnostic
    (works with both OpenCode and tmux handlers).
    """
    print(f"\n{prompt}")
    print("Waiting for input...")

    # Track initial message count to detect new messages
    messages = yield AgenticGetMessages(session_id=session_id)
    initial_count = len(messages)
    # Track the last user message to avoid returning duplicates
    last_user_msg_id = None
    for msg in reversed(messages):
        if msg.role == "user":
            last_user_msg_id = msg.id
            break

    start = time.time()
    while time.time() - start < timeout:
        # Wait for any event (use as a "tick" mechanism)
        try:
            event = yield AgenticNextEvent(session_id=session_id, timeout=5.0)
        except AgenticTimeoutError:
            # Timeout on single event poll is OK, continue the loop
            continue

        if isinstance(event, AgenticEndOfEvents):
            break

        # Check for new user message (handler-agnostic approach)
        messages = yield AgenticGetMessages(session_id=session_id)
        if len(messages) > initial_count:
            # Find the newest user message
            for msg in reversed(messages):
                if msg.role == "user" and msg.id != last_user_msg_id:
                    return msg.content

    # Timeout or end of events
    return None


@do
def pr_review_workflow(pr_url: str, require_approval: bool = False):
    """Complete PR review workflow.

    1. Automated review to find issues
    2. If issues found, attempt to fix them
    3. Optionally wait for human approval
    4. Return final status

    Args:
        pr_url: URL of the pull request to review
        require_approval: Whether to require human approval before completing
    """

    yield slog(status="automated-review", msg="Starting automated review")

    # Phase 1: Automated Review
    review_session = yield AgenticCreateSession(
        name="review-agent",
        title="Code Reviewer",
    )

    yield AgenticSendMessage(
        session_id=review_session.id,
        content=(
            f"Review the PR at {pr_url}\n\n"
            "Check for:\n"
            "- Code style issues\n"
            "- Potential bugs\n"
            "- Missing tests\n"
            "- Documentation gaps\n\n"
            "List any issues found, or say 'LGTM' if everything looks good.\n"
            "Then exit."
        ),
        wait=True,
    )

    messages = yield AgenticGetMessages(session_id=review_session.id)
    review = get_last_assistant_message(messages)

    # Check if issues were found
    has_issues = "LGTM" not in review.upper()

    if has_issues:
        yield slog(
            status="issues-found",
            msg="Found issues in review",
            issues_count=review.count("\n- ") if "\n- " in review else 1,
        )

        # Phase 2: Fix Issues
        yield slog(status="fixing", msg="Attempting to fix issues")

        fix_session = yield AgenticCreateSession(name="fix-agent")

        yield AgenticSendMessage(
            session_id=fix_session.id,
            content=(
                f"The following issues were found in the PR:\n{review}\n\n"
                "For each issue:\n"
                "1. Explain what needs to change\n"
                "2. Show the fix (if code-related)\n"
                "3. Verify the fix is correct\n\n"
                "Then exit."
            ),
            wait=True,
        )

        messages = yield AgenticGetMessages(session_id=fix_session.id)
        fix_result = get_last_assistant_message(messages)

        yield slog(status="fixes-ready", msg="Fixes prepared")

        result = {
            "status": "fixes-prepared",
            "review": review,
            "fixes": fix_result,
        }
    else:
        yield slog(status="no-issues", msg="No issues found - LGTM!")
        result = {
            "status": "approved",
            "review": review,
            "fixes": None,
        }

    # Phase 3: Optional Human Approval
    if require_approval:
        yield slog(status="waiting-approval", msg="Waiting for human review")

        print("\n" + "=" * 60)
        print("REVIEW COMPLETE - AWAITING HUMAN APPROVAL")
        print("=" * 60)
        print(f"\nReview result: {result['status']}")
        if result["fixes"]:
            print(f"\nProposed fixes:\n{result['fixes'][:500]}...")
        print("\n" + "=" * 60)
        print("Run in another terminal:")
        print("  doeff-agentic send <workflow-id>:review-agent 'approve'")
        print("  doeff-agentic send <workflow-id>:review-agent 'reject'")
        print("=" * 60 + "\n")

        approval = yield from wait_for_user_input(
            session_id=review_session.id,
            prompt="Review complete. Enter 'approve' or 'reject':",
            timeout=600.0,
        )

        if approval and approval.lower().strip() == "reject":
            yield slog(status="rejected", msg="Review rejected by human")
            result["status"] = "rejected"
            result["human_decision"] = "rejected"
        else:
            yield slog(status="approved", msg="Review approved by human")
            result["human_decision"] = "approved"

    yield slog(status="complete", msg=f"Workflow complete: {result['status']}")

    return result


if __name__ == "__main__":
    import asyncio
    import sys

    from doeff import AsyncRuntime

    async def main():
        # Use a sample PR URL or accept from command line
        pr_url = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/example/repo/pull/123"
        require_approval = "--approve" in sys.argv

        print("=" * 60)
        print("PR REVIEW WORKFLOW")
        print("=" * 60)
        print(f"PR URL: {pr_url}")
        print(f"Require approval: {require_approval}")
        print()

        handlers = opencode_handler()
        runtime = AsyncRuntime(handlers=handlers)

        try:
            result = await runtime.run(with_visual_logging(pr_review_workflow(pr_url, require_approval)))

            if result.is_err():
                print("\n=== Workflow Failed ===")
                print(result.format())  # Rich error info: effect path, python stack, K stack
            else:
                output = result.value

                print("\n" + "=" * 60)
                print("WORKFLOW RESULT")
                print("=" * 60)
                print(f"Status: {output['status']}")
                print()

                print("Review:")
                print("-" * 40)
                print(output["review"][:500])

                if output["fixes"]:
                    print()
                    print("Fixes:")
                    print("-" * 40)
                    print(output["fixes"][:500])

                if "human_decision" in output:
                    print()
                    print(f"Human Decision: {output['human_decision']}")

        except KeyboardInterrupt:
            print("\nWorkflow interrupted")

    asyncio.run(main())
