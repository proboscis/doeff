"""
Example 04: Conditional Agent Flow

Branch based on agent output.

This demonstrates how to make decisions based on
what an agent returns and conditionally invoke other agents.

Run:
    cd packages/doeff-agentic
    uv run python examples/04_conditional_flow.py
"""

from doeff_agentic import (
    AgenticCreateSession,
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
def review_and_maybe_fix(code: str):
    """Review code and fix if issues are found."""

    yield slog(status="reviewing", msg="Reviewing code")

    # Agent 1: Reviewer
    reviewer = yield AgenticCreateSession(name="reviewer")
    yield AgenticSendMessage(
        session_id=reviewer.id,
        content=(
            f"Review this code. If it looks good, respond with just 'LGTM'. "
            f"Otherwise, list the issues briefly.\n\nCode:\n```python\n{code}\n```\n\n"
            "Then exit."
        ),
        wait=True,
    )
    messages = yield AgenticGetMessages(session_id=reviewer.id)
    review = get_last_assistant_message(messages)

    if "LGTM" in review:
        yield slog(status="approved", msg="Code looks good!")
        return {"status": "approved", "review": review}

    yield slog(status="fixing", msg="Issues found, fixing...")

    # Agent 2: Fixer (only invoked if issues found)
    fixer = yield AgenticCreateSession(name="fixer")
    yield AgenticSendMessage(
        session_id=fixer.id,
        content=(
            f"Fix these issues:\n{review}\n\n"
            f"Original code:\n```python\n{code}\n```\n\n"
            "Output only the fixed code. Then exit."
        ),
        wait=True,
    )
    messages = yield AgenticGetMessages(session_id=fixer.id)
    fixed = get_last_assistant_message(messages)

    yield slog(status="complete", msg="Fixes applied")

    return {"status": "fixed", "review": review, "fixed_code": fixed}


if __name__ == "__main__":
    import asyncio
    from doeff import AsyncRuntime

    async def main():
        # Code with issues to review
        sample_code = """
def calculate_average(numbers):
    total = 0
    for n in numbers:
        total = total + n
    return total / len(numbers)  # Bug: division by zero if empty
"""

        print("Starting code review workflow...")
        print()
        print("Code to review:")
        print(sample_code)
        print()

        handlers = opencode_handler()
        runtime = AsyncRuntime(handlers=handlers)

        try:
            result = await runtime.run(review_and_maybe_fix(sample_code))
            output = result.value
            print(f"\n=== Result: {output['status'].upper()} ===")
            if output["status"] == "fixed":
                print("\nFixed code:")
                print(output["fixed_code"][:500])
        except Exception as e:
            print(f"Error: {e}")

    asyncio.run(main())
