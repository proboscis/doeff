#!/usr/bin/env python
"""
Example 06: API Usage

Demonstrates using the ConductorAPI for programmatic access:
1. Run workflows via API
2. List and query workflows
3. Watch workflow progress
4. Manage workspaces

The API provides a high-level interface for conductor operations
without needing to set up handlers manually.

Run:
    cd packages/doeff-conductor
    uv run python examples/06_api_usage.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from doeff_conductor import (
    # API
    ConductorAPI,
    # Types
    Issue,
    IssueStatus,
    WorkflowStatus,
    # Templates
    get_available_templates,
)


def demo_list_templates():
    """Demonstrate listing available templates."""
    print("\n" + "="*60)
    print("Available Templates")
    print("="*60 + "\n")

    templates = get_available_templates()
    for name, description in templates.items():
        print(f"  {name}:")
        print(f"    {description}")
        print()

    print("To use a template:")
    print("  from doeff_conductor import basic_pr, Issue")
    print("  program = basic_pr(issue)")
    print("  result = run(program, handlers=[...])")


def demo_api_workflow_management():
    """Demonstrate workflow management via API."""
    print("\n" + "="*60)
    print("Workflow Management via API")
    print("="*60 + "\n")

    # Create API with temporary state directory
    state_dir = Path(mkdtemp(prefix="conductor-demo-"))
    api = ConductorAPI(state_dir=state_dir)

    print(f"Using state directory: {state_dir}\n")

    # Create sample issue
    issue = Issue(
        id="ISSUE-API-001",
        title="Demo API workflow",
        body="This is a demo issue for API usage example",
        status=IssueStatus.OPEN,
    )

    print("1. Listing workflows (should be empty)...")
    workflows = api.list_workflows()
    print(f"   Found {len(workflows)} workflows\n")

    # Note: Running a full workflow requires proper handlers,
    # so we'll demonstrate the API structure without execution
    print("2. API methods available:")
    print("   - api.run_workflow(template, issue=issue, params={})")
    print("   - api.list_workflows(status=[WorkflowStatus.RUNNING])")
    print("   - api.get_workflow(workflow_id)")
    print("   - api.watch_workflow(workflow_id)")
    print("   - api.stop_workflow(workflow_id)")
    print("   - api.list_workspaces()")
    print("   - api.cleanup_workspaces(dry_run=True)")

    print("\n3. Workflow status types:")
    for status in WorkflowStatus:
        terminal = "terminal" if status.is_terminal() else "non-terminal"
        print(f"   - {status.value}: {terminal}")


def demo_environment_management():
    """Demonstrate workspace management."""
    print("\n" + "="*60)
    print("Workspace Management")
    print("="*60 + "\n")

    # Create API
    state_dir = Path(mkdtemp(prefix="conductor-workspace-demo-"))
    api = ConductorAPI(state_dir=state_dir)

    print("1. Listing workspaces...")
    workspaces = api.list_workspaces()
    print(f"   Found {len(workspaces)} workspaces")

    print("\n2. Workspace cleanup (dry run)...")
    would_clean = api.cleanup_workspaces(dry_run=True, older_than_days=7)
    print(f"   Would clean {len(would_clean)} workspaces older than 7 days")

    print("\n3. Workspace attributes:")
    print("   - id: Unique workspace identifier")
    print("   - repo: Repository name resolved by the handler")
    print("   - ref: Portable git ref")
    print("   - base_ref: Base ref workspace was created from")
    print("   - issue_id: Associated issue (optional)")


def demo_json_output():
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
    templates = get_available_templates()
    print(json.dumps(templates, indent=2))


def main():
    """Run all API usage demonstrations."""
    print("\n" + "#"*60)
    print("#  doeff-conductor API Usage Examples")
    print("#"*60)

    demo_list_templates()
    demo_api_workflow_management()
    demo_environment_management()
    demo_json_output()

    print("\n" + "="*60)
    print("API Usage Demo Complete!")
    print("="*60 + "\n")

    print("For more information:")
    print("  - See docs/api.md for full API reference")
    print("  - See docs/tutorial.md for step-by-step guide")
    print("  - Run 'conductor --help' for CLI documentation")


if __name__ == "__main__":
    main()
