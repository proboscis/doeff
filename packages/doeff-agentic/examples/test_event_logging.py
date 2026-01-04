#!/usr/bin/env python
"""
Test Event Logging (No Dependencies Required)

This script tests the JSONL event logging system without requiring
OpenCode or any external services. It simulates a complete workflow
and verifies state reconstruction.

Run:
    cd packages/doeff-agentic
    uv run python examples/test_event_logging.py
"""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import sys

from doeff_agentic import (
    EventLogWriter,
    EventLogReader,
    WorkflowIndex,
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticSessionHandle,
    AgenticSessionStatus,
)


def test_event_logging():
    """Test the event logging system end-to-end."""
    print("=" * 60)
    print("Testing Event Logging System")
    print("=" * 60)
    print()

    # Create a temporary state directory
    state_dir = Path(tempfile.mkdtemp())
    print(f"State directory: {state_dir}")
    print()

    # Initialize writer and reader
    writer = EventLogWriter(state_dir)
    reader = EventLogReader(state_dir)
    index = WorkflowIndex(state_dir)

    errors = []

    # --- Test 1: Create workflow ---
    print("Test 1: Create workflow")
    workflow_id = "abc1234"
    writer.log_workflow_created(workflow_id, "test-workflow", {"purpose": "testing"})
    index.add(workflow_id, "test-workflow")
    print(f"  Created workflow: {workflow_id}")

    workflows = reader.list_workflows()
    if workflow_id in workflows:
        print("  ✓ Workflow found in list")
    else:
        errors.append("Workflow not found in list")
        print("  ✗ Workflow not found in list")
    print()

    # --- Test 2: Create environment ---
    print("Test 2: Create environment")
    env = AgenticEnvironmentHandle(
        id="env-001",
        env_type=AgenticEnvironmentType.SHARED,
        name="shared-env",
        working_dir="/tmp/test",
        created_at=datetime.now(timezone.utc),
    )
    writer.log_environment_created(workflow_id, env)
    print(f"  Created environment: {env.id} ({env.env_type.value})")

    restored_env = reader.reconstruct_environment_state(workflow_id, env.id)
    if restored_env and restored_env.id == env.id:
        print("  ✓ Environment state reconstructed")
    else:
        errors.append("Failed to reconstruct environment state")
        print("  ✗ Failed to reconstruct environment state")
    print()

    # --- Test 3: Create session ---
    print("Test 3: Create session")
    session = AgenticSessionHandle(
        id="sess-001",
        name="reviewer",
        workflow_id=workflow_id,
        environment_id=env.id,
        status=AgenticSessionStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        title="Code Reviewer",
    )
    writer.log_session_created(workflow_id, session)
    writer.log_session_bound_to_environment(workflow_id, env.id, session.name)
    print(f"  Created session: {session.name} (id={session.id})")

    sessions = reader.list_sessions(workflow_id)
    if session.name in sessions:
        print("  ✓ Session found in list")
    else:
        errors.append("Session not found in list")
        print("  ✗ Session not found in list")
    print()

    # --- Test 4: Simulate message exchange ---
    print("Test 4: Simulate message exchange")
    writer.log_message_sent(workflow_id, "reviewer", "Review this code please", wait=True)
    writer.log_session_status(workflow_id, "reviewer", "running")
    print("  Sent user message, session now running")

    writer.log_message_complete(workflow_id, "reviewer", tokens=150)
    writer.log_session_status(workflow_id, "reviewer", "blocked")
    print("  Assistant responded, session blocked (waiting for input)")

    writer.log_message_sent(workflow_id, "reviewer", "approve", wait=False)
    writer.log_session_status(workflow_id, "reviewer", "running")
    print("  Sent approval, session running again")

    writer.log_session_status(workflow_id, "reviewer", "done")
    print("  Session completed")

    restored_session = reader.reconstruct_session_state(workflow_id, "reviewer")
    if restored_session and restored_session.status == AgenticSessionStatus.DONE:
        print("  ✓ Session status correctly reconstructed as DONE")
    else:
        errors.append("Session status not correctly reconstructed")
        print("  ✗ Session status not correctly reconstructed")
    print()

    # --- Test 5: Complete workflow ---
    print("Test 5: Complete workflow")
    writer.log_workflow_status(workflow_id, "done")
    print("  Workflow marked as done")

    workflow_state = reader.reconstruct_workflow_state(workflow_id)
    if workflow_state and workflow_state.status.value == "done":
        print("  ✓ Workflow status correctly reconstructed as DONE")
    else:
        errors.append("Workflow status not correctly reconstructed")
        print("  ✗ Workflow status not correctly reconstructed")
    print()

    # --- Test 6: Workflow index prefix resolution ---
    print("Test 6: Workflow index prefix resolution")
    resolved = index.resolve_prefix("abc")
    if resolved == workflow_id:
        print(f"  ✓ Prefix 'abc' resolved to '{resolved}'")
    else:
        errors.append("Prefix resolution failed")
        print(f"  ✗ Prefix resolution failed (got {resolved})")
    print()

    # --- Test 7: Read all events ---
    print("Test 7: Read workflow events")
    events = reader.read_workflow_events(workflow_id)
    print(f"  Total events: {len(events)}")
    for event in events:
        print(f"    - {event.event_type}")

    if len(events) >= 5:
        print("  ✓ Expected events recorded")
    else:
        errors.append("Not enough events recorded")
        print("  ✗ Not enough events recorded")
    print()

    # --- Summary ---
    print("=" * 60)
    if errors:
        print(f"FAILED: {len(errors)} error(s)")
        for err in errors:
            print(f"  - {err}")
        return 1
    else:
        print("SUCCESS: All tests passed!")
        return 0


if __name__ == "__main__":
    sys.exit(test_event_logging())
