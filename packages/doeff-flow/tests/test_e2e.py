"""End-to-end tests for doeff-flow on the Rust VM runtime."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from doeff_flow import run_workflow
from doeff_flow.cli import cli

from doeff import (
    Ask,
    Delegate,
    Get,
    Pure,
    Put,
    Spawn,
    Wait,
    WithHandler,
    async_run,
    default_handlers,
    do,
)
from doeff import run as run_sync


def _read_trace_entries(trace_dir: Path, workflow_id: str) -> list[dict]:
    trace_file = trace_dir / workflow_id / "trace.jsonl"
    assert trace_file.exists()
    return [json.loads(line) for line in trace_file.read_text().splitlines() if line.strip()]


class TestWorkflowAndCli:
    """Integration tests across workflow execution and CLI views."""

    def test_ps_and_history_show_completed_workflow(self, tmp_path):
        @do
        def simple_workflow():
            return (yield Pure(42))

        result = run_workflow(
            simple_workflow(),
            workflow_id="cli-test-wf",
            trace_dir=tmp_path,
        )
        assert result.is_ok()
        assert result.value == 42

        runner = CliRunner()

        ps_result = runner.invoke(cli, ["ps", "--trace-dir", str(tmp_path)])
        assert ps_result.exit_code == 0
        assert "cli-test-wf" in ps_result.output
        assert "completed" in ps_result.output

        history_result = runner.invoke(
            cli,
            ["history", "cli-test-wf", "--trace-dir", str(tmp_path)],
        )
        assert history_result.exit_code == 0
        assert "Step" in history_result.output
        assert "completed" in history_result.output

    def test_history_last_limits_rows_for_appended_terminal_snapshots(self, tmp_path):
        @do
        def workflow():
            return (yield Pure("done"))

        for _ in range(5):
            result = run_workflow(
                workflow(),
                workflow_id="history-last-test",
                trace_dir=tmp_path,
            )
            assert result.is_ok()

        runner = CliRunner()
        limited_result = runner.invoke(
            cli,
            [
                "history",
                "history-last-test",
                "--trace-dir",
                str(tmp_path),
                "--last",
                "3",
            ],
        )
        assert limited_result.exit_code == 0
        completed_rows = [
            line for line in limited_result.output.splitlines() if "completed" in line
        ]
        assert len(completed_rows) == 3

    def test_concurrent_workflows_write_separate_trace_files(self, tmp_path):
        @do
        def workflow_a():
            return (yield Pure("A-done"))

        @do
        def workflow_b():
            return (yield Pure("B-done"))

        @do
        def run_workflow_a():
            return run_workflow(workflow_a(), workflow_id="concurrent-a", trace_dir=tmp_path)

        @do
        def run_workflow_b():
            return run_workflow(workflow_b(), workflow_id="concurrent-b", trace_dir=tmp_path)

        @do
        def run_all():
            t1 = yield Spawn(run_workflow_a())
            t2 = yield Spawn(run_workflow_b())
            result_a = yield Wait(t1)
            result_b = yield Wait(t2)
            return result_a, result_b

        result = run_sync(run_all(), handlers=default_handlers())

        assert result.is_ok()
        assert result.value[0].is_ok()
        assert result.value[1].is_ok()

        entries_a = _read_trace_entries(tmp_path, "concurrent-a")
        entries_b = _read_trace_entries(tmp_path, "concurrent-b")

        assert len(entries_a) == 1
        assert len(entries_b) == 1
        assert all(entry["workflow_id"] == "concurrent-a" for entry in entries_a)
        assert all(entry["workflow_id"] == "concurrent-b" for entry in entries_b)

    def test_cli_rejects_invalid_workflow_id(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["history", "invalid/path"])
        assert result.exit_code != 0
        assert "Invalid workflow_id" in result.output


class TestWithHandlerTracing:
    """VM-native tracing tests using WithHandler interception."""

    def test_sync_run_observes_effects_with_withhandler(self):
        captured_effects: list[object] = []

        def capturing_handler(effect, k):
            _ = k
            captured_effects.append(effect)
            yield Delegate()

        @do
        def workflow():
            yield Put("counter", 0)
            current = yield Get("counter")
            return current + 1

        result = run_sync(
            WithHandler(capturing_handler, workflow()),
            handlers=default_handlers(),
            store={},
        )

        assert result.is_ok()
        assert result.value == 1
        effect_names = [type(effect).__name__ for effect in captured_effects]
        assert "PyPut" in effect_names
        assert "PyGet" in effect_names

    @pytest.mark.asyncio
    async def test_async_run_observes_effects_with_withhandler(self):
        captured_effects: list[object] = []

        def capturing_handler(effect, k):
            _ = k
            captured_effects.append(effect)
            yield Delegate()

        @do
        def workflow():
            value = yield Ask("base")
            return value * 2

        result = await async_run(
            WithHandler(capturing_handler, workflow()),
            handlers=default_handlers(),
            env={"base": 15},
        )

        assert result.is_ok()
        assert result.value == 30
        assert any(type(effect).__name__ == "PyAsk" for effect in captured_effects)
