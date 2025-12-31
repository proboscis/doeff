"""End-to-end tests for doeff-flow: running workflows and observing traces."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from doeff import do
from doeff.cesk import run_sync
from doeff.effects import Pure
from doeff_flow import run_workflow, trace_observer
from doeff_flow.cli import cli


class TestE2EWorkflowExecution:
    """End-to-end tests for workflow execution with live tracing."""

    def test_simple_workflow_produces_complete_trace(self):
        """A simple workflow should produce a trace from start to completion."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def simple_workflow():
                a = yield Pure(10)
                b = yield Pure(20)
                c = yield Pure(30)
                return a + b + c

            result = run_workflow(
                simple_workflow(),
                workflow_id="e2e-simple",
                trace_dir=trace_dir,
            )

            # Verify execution succeeded
            assert result.is_ok
            assert result.value == 60

            # Verify trace file exists and has valid structure
            trace_file = trace_dir / "e2e-simple" / "trace.jsonl"
            assert trace_file.exists()

            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) >= 1

            # Verify trace progression
            traces = [json.loads(line) for line in lines]

            # First trace should be running
            assert traces[0]["status"] in ("running", "paused")
            assert traces[0]["step"] >= 1

            # Last trace should be completed
            assert traces[-1]["status"] == "completed"

            # All traces should have consistent workflow_id
            for trace in traces:
                assert trace["workflow_id"] == "e2e-simple"
                assert "started_at" in trace
                assert "updated_at" in trace
                assert "trace" in trace  # K stack frames

    def test_nested_workflow_captures_call_stack(self):
        """Nested @do functions should appear in the trace frames."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def inner_compute():
                x = yield Pure(5)
                return x * 2

            @do
            def middle_layer():
                y = yield inner_compute()
                return y + 1

            @do
            def outer_workflow():
                z = yield middle_layer()
                return z * 3

            result = run_workflow(
                outer_workflow(),
                workflow_id="e2e-nested",
                trace_dir=trace_dir,
            )

            assert result.is_ok
            assert result.value == 33  # ((5 * 2) + 1) * 3

            trace_file = trace_dir / "e2e-nested" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")
            traces = [json.loads(line) for line in lines]

            # Find a trace with nested frames (when inner_compute is executing)
            max_depth = max(len(t["trace"]) for t in traces)
            assert max_depth >= 2, "Should have nested call frames"

            # Verify function names appear in traces
            all_functions = set()
            for trace in traces:
                for frame in trace["trace"]:
                    all_functions.add(frame["function"])

            # At least outer_workflow should appear
            assert "outer_workflow" in all_functions or any(
                "outer" in f for f in all_functions
            )

    def test_workflow_failure_records_failed_status(self):
        """A failing workflow should have 'failed' status in final trace."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def failing_workflow():
                yield Pure(10)
                raise ValueError("Intentional test failure")

            result = run_workflow(
                failing_workflow(),
                workflow_id="e2e-failure",
                trace_dir=trace_dir,
            )

            # Verify execution failed
            assert result.is_err
            assert "Intentional test failure" in str(result.error)

            # Verify trace captures failure
            trace_file = trace_dir / "e2e-failure" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")
            last_trace = json.loads(lines[-1])

            assert last_trace["status"] == "failed"

    def test_concurrent_workflows_have_separate_traces(self):
        """Multiple concurrent workflows should write to separate trace files."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            results = {}

            @do
            def workflow_a():
                for i in range(3):
                    yield Pure(f"A-{i}")
                return "A-done"

            @do
            def workflow_b():
                for i in range(5):
                    yield Pure(f"B-{i}")
                return "B-done"

            def run_wf(wf, wf_id):
                results[wf_id] = run_workflow(
                    wf(), workflow_id=wf_id, trace_dir=trace_dir
                )

            # Run workflows concurrently
            t1 = threading.Thread(target=run_wf, args=(workflow_a, "concurrent-a"))
            t2 = threading.Thread(target=run_wf, args=(workflow_b, "concurrent-b"))

            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # Verify both succeeded
            assert results["concurrent-a"].is_ok
            assert results["concurrent-a"].value == "A-done"
            assert results["concurrent-b"].is_ok
            assert results["concurrent-b"].value == "B-done"

            # Verify separate trace files
            trace_a = trace_dir / "concurrent-a" / "trace.jsonl"
            trace_b = trace_dir / "concurrent-b" / "trace.jsonl"
            assert trace_a.exists()
            assert trace_b.exists()

            # Verify each trace only contains its own workflow_id
            for line in trace_a.read_text().strip().split("\n"):
                data = json.loads(line)
                assert data["workflow_id"] == "concurrent-a"

            for line in trace_b.read_text().strip().split("\n"):
                data = json.loads(line)
                assert data["workflow_id"] == "concurrent-b"


class TestE2ECLIIntegration:
    """End-to-end tests for CLI commands against real trace data."""

    def test_ps_lists_completed_workflow(self):
        """ps command should list a completed workflow."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def simple_workflow():
                return (yield Pure(42))

            run_workflow(
                simple_workflow(),
                workflow_id="cli-test-wf",
                trace_dir=trace_dir,
            )

            # Run ps command
            runner = CliRunner()
            result = runner.invoke(cli, ["ps", "--trace-dir", str(trace_dir)])

            assert result.exit_code == 0
            assert "cli-test-wf" in result.output
            assert "completed" in result.output

    def test_history_shows_workflow_steps(self):
        """history command should show execution steps."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def multi_step_workflow():
                a = yield Pure(1)
                b = yield Pure(2)
                c = yield Pure(3)
                return a + b + c

            run_workflow(
                multi_step_workflow(),
                workflow_id="history-test-wf",
                trace_dir=trace_dir,
            )

            # Run history command
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["history", "history-test-wf", "--trace-dir", str(trace_dir)],
            )

            assert result.exit_code == 0
            # Should show multiple steps
            assert "step" in result.output
            # Should show completed status in final entry
            assert "completed" in result.output

    def test_history_last_option(self):
        """history --last N should limit output."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def many_step_workflow():
                for i in range(20):
                    yield Pure(i)
                return "done"

            run_workflow(
                many_step_workflow(),
                workflow_id="history-last-test",
                trace_dir=trace_dir,
            )

            # Get full history
            runner = CliRunner()
            full_result = runner.invoke(
                cli,
                ["history", "history-last-test", "--trace-dir", str(trace_dir), "--last", "100"],
            )

            # Get limited history
            limited_result = runner.invoke(
                cli,
                ["history", "history-last-test", "--trace-dir", str(trace_dir), "--last", "3"],
            )

            full_lines = [l for l in full_result.output.strip().split("\n") if l.startswith("step")]
            limited_lines = [l for l in limited_result.output.strip().split("\n") if l.startswith("step")]

            assert len(limited_lines) == 3
            assert len(full_lines) > 3

    def test_cli_rejects_invalid_workflow_id(self):
        """CLI commands should reject invalid workflow IDs."""
        runner = CliRunner()

        # Test watch command with invalid ID
        result = runner.invoke(cli, ["watch", "../../../etc/passwd"])
        assert result.exit_code != 0
        assert "Invalid workflow_id" in result.output or "Error" in result.output

        # Test history command with invalid ID
        result = runner.invoke(cli, ["history", "invalid/path"])
        assert result.exit_code != 0


class TestE2ETraceObserverComposability:
    """Test that trace_observer composes well with existing code."""

    def test_trace_observer_with_custom_env(self):
        """trace_observer should work with custom environment."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            from doeff.effects import AskEffect

            @do
            def workflow_with_env():
                config = yield AskEffect("config")
                return f"got-{config}"

            with trace_observer("env-test", trace_dir) as on_step:
                result = run_sync(
                    workflow_with_env(),
                    env={"config": "test-value"},
                    on_step=on_step,
                )

            assert result.is_ok
            assert result.value == "got-test-value"

            # Verify trace was written
            trace_file = trace_dir / "env-test" / "trace.jsonl"
            assert trace_file.exists()

    def test_trace_observer_with_state(self):
        """trace_observer should work with stateful workflows."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            from doeff.effects import StateGetEffect, StatePutEffect

            @do
            def stateful_workflow():
                yield StatePutEffect("counter", 0)
                for _ in range(3):
                    current = yield StateGetEffect("counter")
                    yield StatePutEffect("counter", current + 1)
                final = yield StateGetEffect("counter")
                return final

            with trace_observer("state-test", trace_dir) as on_step:
                result = run_sync(stateful_workflow(), on_step=on_step)

            assert result.is_ok
            assert result.value == 3

            # Verify trace was written with multiple steps
            trace_file = trace_dir / "state-test" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) > 5  # Multiple state operations

    def test_multiple_runs_append_to_trace(self):
        """Running same workflow_id multiple times should append to trace."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def simple_workflow():
                return (yield Pure(42))

            # First run
            run_workflow(
                simple_workflow(),
                workflow_id="append-test",
                trace_dir=trace_dir,
            )

            trace_file = trace_dir / "append-test" / "trace.jsonl"
            first_count = len(trace_file.read_text().strip().split("\n"))

            # Second run
            run_workflow(
                simple_workflow(),
                workflow_id="append-test",
                trace_dir=trace_dir,
            )

            second_count = len(trace_file.read_text().strip().split("\n"))

            # Trace should have grown
            assert second_count > first_count


class TestE2EPerformance:
    """Basic performance tests for trace observation."""

    def test_many_steps_workflow(self):
        """Workflow with many steps should complete without issues."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def many_steps():
                total = 0
                for i in range(100):
                    x = yield Pure(i)
                    total += x
                return total

            start = time.time()
            result = run_workflow(
                many_steps(),
                workflow_id="perf-test",
                trace_dir=trace_dir,
            )
            elapsed = time.time() - start

            assert result.is_ok
            assert result.value == sum(range(100))

            # Should complete in reasonable time (< 5 seconds)
            assert elapsed < 5.0, f"Took too long: {elapsed:.2f}s"

            # Verify trace has many entries
            trace_file = trace_dir / "perf-test" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) >= 100
