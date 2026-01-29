"""
Workflow Status with slog
=========================

This example demonstrates how to use slog (structured log) effects to emit
human-readable status messages from workflows. These status messages are
captured in the trace and displayed prominently in the CLI.

Run this example:
    cd packages/doeff-flow
    uv run python examples/07_workflow_status.py

Then in another terminal, watch the trace:
    doeff-flow watch pr-review-workflow

    # Or with status-only mode for simplified display:
    doeff-flow watch pr-review-workflow --status-only

The slog effect is designed for semantic workflow status that agents can emit,
making multi-agent orchestration observable.
"""

from time import sleep

from doeff_flow import run_workflow

from doeff import do
from doeff.effects import Pure
from doeff.effects.writer import slog

# =============================================================================
# Example: PR Review Workflow with Status Updates
# =============================================================================


@do
def run_automated_review(pr_url: str):
    """Simulate automated PR review."""
    yield slog(status="automated-review", msg=f"Starting review for {pr_url}")
    sleep(0.5)  # Simulate review time

    # Simulate finding some issues
    issues = ["unused import", "missing docstring", "type hint missing"]
    yield slog(status="review-complete", msg=f"Found {len(issues)} issues")
    return issues


@do
def run_fix_agent(issues: list[str]):
    """Simulate fix agent addressing issues."""
    yield slog(status="fixing", msg=f"Fixing {len(issues)} issues")

    fixed = []
    for i, issue in enumerate(issues):
        sleep(0.3)  # Simulate fix time
        yield slog(status="fixing", msg=f"Fixed: {issue} ({i+1}/{len(issues)})")
        fixed.append(issue)

    yield slog(status="fixes-complete", msg=f"All {len(fixed)} issues fixed")
    return fixed


@do
def run_outcome_check():
    """Simulate outcome verification."""
    yield slog(status="outcome-check", msg="Verifying PR outcomes")
    sleep(0.3)

    # Simulate verification passing
    yield slog(status="verified", msg="All checks passed")
    return {"passed": True, "issues": []}


@do
def pr_review_workflow(pr_url: str):
    """Complete PR review workflow with status updates.

    This workflow demonstrates the pattern used by doeff-agentic-pr-workflow:
    - Uses slog to emit human-readable status at each phase
    - Status is captured in traces for monitoring
    - Enables observing workflow progress in real-time
    """
    yield slog(status="starting", msg=f"PR Review Workflow for {pr_url}")

    # Phase 1: Automated Review
    issues = yield run_automated_review(pr_url)

    # Phase 2: Auto-fix if issues found
    if issues:
        yield run_fix_agent(issues)

    # Phase 3: Outcome check
    outcome = yield run_outcome_check()

    if outcome["passed"]:
        yield slog(status="waiting", msg="Ready for user review")
    else:
        yield slog(status="needs-attention", msg="Manual intervention required")

    return outcome


# =============================================================================
# Example: Data Pipeline with Status Updates
# =============================================================================


@do
def data_pipeline_with_status():
    """Data pipeline demonstrating slog status pattern."""
    yield slog(status="extracting", msg="Fetching data from source")
    data = yield Pure({"records": list(range(100))})
    sleep(0.2)

    yield slog(status="transforming", msg=f"Processing {len(data['records'])} records")
    transformed = yield Pure([x * 2 for x in data["records"]])
    sleep(0.2)

    yield slog(status="loading", msg="Writing to destination")
    sleep(0.3)

    yield slog(status="complete", msg=f"Pipeline finished: {len(transformed)} records")
    return {"count": len(transformed)}


# =============================================================================
# Main
# =============================================================================


def main():
    """Run the workflow status examples."""
    print("=" * 60)
    print("Workflow Status with slog Example")
    print("=" * 60)
    print()
    print("Watch the workflow in another terminal:")
    print("  doeff-flow watch pr-review-workflow")
    print("  doeff-flow watch pr-review-workflow --status-only")
    print()
    print("Starting PR Review Workflow...")
    print()

    result = run_workflow(
        pr_review_workflow("https://github.com/example/repo/pull/123"),
        workflow_id="pr-review-workflow",
    )

    if result.is_ok:
        print(f"Workflow completed successfully: {result.value}")
    else:
        print(f"Workflow failed: {result.error}")

    print()
    print("-" * 60)
    print("Starting Data Pipeline Workflow...")
    print()

    result2 = run_workflow(
        data_pipeline_with_status(),
        workflow_id="data-pipeline-status",
    )

    if result2.is_ok:
        print(f"Pipeline completed: {result2.value}")
    else:
        print(f"Pipeline failed: {result2.error}")


if __name__ == "__main__":
    main()
