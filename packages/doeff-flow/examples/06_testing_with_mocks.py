"""
Testing Workflows with Mock Trace Handlers
==========================================

This example demonstrates how to use doeff-flow's in-memory mock trace handlers.
It wires handlers via `WithHandler`, executes a workflow, and asserts on captured
trace data without creating trace files.

Run this example:
    cd packages/doeff-flow
    uv run python examples/06_testing_with_mocks.py
"""


from pathlib import Path
from uuid import uuid4

from doeff_flow.effects import TraceAnnotate, TraceCapture, TracePush, TraceSnapshot
from doeff_flow.handlers import MockTraceRecorder, mock_handlers
from doeff_flow.trace import get_default_trace_dir

from doeff import WithHandler, default_handlers, do
from doeff import run as run_sync


@do
def workflow_under_test():
    """Workflow that emits trace effects and returns captured snapshots."""
    yield TracePush(name="load", metadata={"request_id": "req-001"})
    yield TraceAnnotate(key="tenant", value="acme")
    yield TraceSnapshot(label="checkpoint")
    captured = yield TraceCapture(format="dict")
    return captured


def main():
    print("=" * 60)
    print("Mock Trace Handler Example")
    print("=" * 60)

    workflow_id = f"mock-example-{uuid4().hex[:8]}"
    recorder = MockTraceRecorder(workflow_id=workflow_id)
    handlers = mock_handlers(recorder=recorder)
    trace_file = get_default_trace_dir() / workflow_id / "trace.jsonl"

    wrapped = WithHandler(handlers, workflow_under_test())
    result = run_sync(wrapped, handlers=default_handlers())
    if result.is_err():
        raise RuntimeError(f"Workflow unexpectedly failed: {result.error!r}")

    captured_entries = result.value
    assert isinstance(captured_entries, list)
    assert len(captured_entries) == 3

    last_entry = captured_entries[-1]
    last_slog = last_entry["last_slog"]
    assert last_slog is not None
    assert last_slog["label"] == "checkpoint"
    assert last_slog["annotations"]["tenant"] == "acme"
    assert last_slog["annotations"]["request_id"] == "req-001"

    # Mock handlers must avoid production trace file writes.
    assert not Path(trace_file).exists()

    print(f"Captured {len(captured_entries)} in-memory trace entries for {workflow_id}")
    print("Assertions passed: mock handler captured trace data without filesystem side effects.")


if __name__ == "__main__":
    main()
