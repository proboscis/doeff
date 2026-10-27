#!/usr/bin/env python
"""
Example 06: API Usage

Demonstrates programmatic access through run effects and conductor_state_handler:
1. List the workflow templates
2. List and query workflows
3. Manage workspaces
4. JSON output for scripting

A program yields run effects (ListWorkflows, GetWorkflow, RunWorkflow, StopWorkflow,
ListWorkspaces, CleanupWorkspaces, ...); conductor_state_handler answers them from the
state directory it is given. The ``conductor`` CLI commands are programs of the same
effects.

Run:
    cd packages/doeff-conductor
    uv run python examples/06_api_usage.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from doeff_conductor import (
    CleanupWorkspaces,
    Issue,
    IssueStatus,
    ListTemplates,
    ListWorkflows,
    ListWorkspaces,
    WorkflowStatus,
    conductor_state_handler,
)

from doeff import do, run


@do
def list_templates():
    """Program: the workflow templates by name with their descriptions."""
    return (yield ListTemplates())


@do
def list_workflows():
    """Program: every workflow, and the running or blocked ones."""
    every = yield ListWorkflows()
    active = yield ListWorkflows(status=(WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED))
    return every, active


@do
def workspace_overview():
    """Program: the materialized workspaces and what a 7-day cleanup would remove."""
    workspaces = yield ListWorkspaces()
    would_clean = yield CleanupWorkspaces(dry_run=True, older_than_days=7)
    return workspaces, would_clean


def demo_list_templates(state_dir: Path):
    """Demonstrate listing available templates."""
    print("\n" + "="*60)
    print("Available Templates")
    print("="*60 + "\n")

    templates = run(conductor_state_handler(state_dir)(list_templates()))
    for name, description in templates.items():
        print(f"  {name}:")
        print(f"    {description}")
        print()

    print("To run a template:")
    print("  run(conductor_state_handler(state_dir)(program_yielding(")
    print("      RunWorkflow(template_or_file='basic_pr', issue=issue))))")


def demo_workflow_management(state_dir: Path):
    """Demonstrate workflow queries through run effects."""
    print("\n" + "="*60)
    print("Workflow Management via Run Effects")
    print("="*60 + "\n")

    print(f"Using state directory: {state_dir}\n")

    print("1. Listing workflows (should be empty)...")
    every, active = run(conductor_state_handler(state_dir)(list_workflows()))
    print(f"   Found {len(every)} workflows ({len(active)} running or blocked)\n")

    # Note: Running a full workflow requires agents, so the run effects
    # are listed here without executing a workflow
    print("2. Run effects available:")
    print("   - RunWorkflow(template_or_file=template, issue=issue, params={})")
    print("   - ListWorkflows(status=(WorkflowStatus.RUNNING,))")
    print("   - GetWorkflow(workflow_id=workflow_id)")
    print("   - StopWorkflow(workflow_id=workflow_id)")
    print("   - ListWorkspaces()")
    print("   - CleanupWorkspaces(dry_run=True)")

    print("\n3. Workflow status types:")
    for status in WorkflowStatus:
        terminal = "terminal" if status.is_terminal() else "non-terminal"
        print(f"   - {status.value}: {terminal}")


def demo_workspace_management(state_dir: Path):
    """Demonstrate workspace management."""
    print("\n" + "="*60)
    print("Workspace Management")
    print("="*60 + "\n")

    workspaces, would_clean = run(conductor_state_handler(state_dir)(workspace_overview()))
    print("1. Listing workspaces...")
    print(f"   Found {len(workspaces)} workspaces")

    print("\n2. Workspace cleanup (dry run)...")
    print(f"   Would clean {len(would_clean)} workspaces older than 7 days")

    print("\n3. Workspace attributes:")
    print("   - id: Unique workspace identifier")
    print("   - repo: Repository name resolved by the handler")
    print("   - ref: Portable git ref")
    print("   - base_ref: Base ref workspace was created from")
    print("   - issue_id: Associated issue (optional)")


def demo_json_output(state_dir: Path):
    """Demonstrate JSON output for scripting/integration."""
    print("\n" + "="*60)
    print("JSON Output for Scripting")
    print("="*60 + "\n")

    # Create sample data structures
    issue = Issue(
        id="ISSUE-JSON-001",
        title="Test JSON serialization",
        body="Demonstrates JSON output",
        status=IssueStatus.OPEN,
        labels=("feature", "demo"),
        created_at=datetime.now(timezone.utc),
    )

    print("Issue as JSON:")
    print(json.dumps(issue.to_dict(), indent=2, default=str))

    print("\n\nTemplates as JSON:")
    templates = run(conductor_state_handler(state_dir)(list_templates()))
    print(json.dumps(templates, indent=2))


def main():
    """Run all API usage demonstrations."""
    print("\n" + "#"*60)
    print("#  doeff-conductor API Usage Examples")
    print("#"*60)

    state_dir = Path(mkdtemp(prefix="conductor-demo-"))
    demo_list_templates(state_dir)
    demo_workflow_management(state_dir)
    demo_workspace_management(state_dir)
    demo_json_output(state_dir)

    print("\n" + "="*60)
    print("API Usage Demo Complete!")
    print("="*60 + "\n")

    print("For more information:")
    print("  - See docs/api.md for full API reference")
    print("  - See docs/tutorial.md for step-by-step guide")
    print("  - Run 'conductor --help' for CLI documentation")


if __name__ == "__main__":
    main()
