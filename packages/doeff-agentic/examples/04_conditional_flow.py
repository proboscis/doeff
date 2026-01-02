"""
Example 04: Conditional Agent Flow

Branch based on agent output.

This demonstrates how to make decisions based on
what an agent returns and conditionally invoke other agents.

Run:
    cd packages/doeff-agentic
    uv run python examples/04_conditional_flow.py
"""

from doeff import do
from doeff.effects.writer import slog

from doeff_agentic import AgentConfig, RunAgent
from doeff_agentic.handler import agentic_effectful_handlers


@do
def review_and_maybe_fix(code: str):
    """Review code and fix if issues are found."""

    yield slog(status="reviewing", msg="Reviewing code")

    review = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=(
                f"Review this code. If it looks good, respond with just 'LGTM'. "
                f"Otherwise, list the issues briefly.\n\nCode:\n```python\n{code}\n```\n\n"
                "Then exit."
            ),
        ),
        session_name="reviewer",
    )

    if "LGTM" in review:
        yield slog(status="approved", msg="Code looks good!")
        return {"status": "approved", "review": review}

    yield slog(status="fixing", msg="Issues found, fixing...")

    fixed = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=(
                f"Fix these issues:\n{review}\n\n"
                f"Original code:\n```python\n{code}\n```\n\n"
                "Output only the fixed code. Then exit."
            ),
        ),
        session_name="fixer",
    )

    yield slog(status="complete", msg="Fixes applied")

    return {"status": "fixed", "review": review, "fixed_code": fixed}


if __name__ == "__main__":
    from doeff import run_sync

    # Code with issues to review
    sample_code = '''
def calculate_average(numbers):
    total = 0
    for n in numbers:
        total = total + n
    return total / len(numbers)  # Bug: division by zero if empty
'''

    print("Starting code review workflow...")
    print()
    print("Code to review:")
    print(sample_code)
    print()

    handlers = agentic_effectful_handlers(
        workflow_name="code-review",
    )

    try:
        result = run_sync(review_and_maybe_fix(sample_code), handlers=handlers)
        print(f"\n=== Result: {result['status'].upper()} ===")
        if result["status"] == "fixed":
            print("\nFixed code:")
            print(result["fixed_code"][:500])
    except Exception as e:
        print(f"Error: {e}")
