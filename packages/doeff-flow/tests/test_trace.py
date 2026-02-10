"""Tests for doeff_flow.trace with VM-native tracing patterns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from doeff_flow import run_workflow
from doeff_flow.trace import (
    LiveTrace,
    TraceFrame,
    _safe_repr,
    get_default_trace_dir,
    validate_workflow_id,
    write_terminal_trace,
)

from doeff import Ask, Delegate, Get, Pure, Put, Resume, WithHandler, default_handlers, do
from doeff import run as run_sync


def _read_trace_entries(trace_dir: Path, workflow_id: str) -> list[dict]:
    trace_file = trace_dir / workflow_id / "trace.jsonl"
    assert trace_file.exists()
    return [json.loads(line) for line in trace_file.read_text().splitlines() if line.strip()]


class TestValidateWorkflowId:
    """Tests for workflow ID validation."""

    @pytest.mark.parametrize(
        "workflow_id",
        [
            "workflow001",
            "MyWorkflow",
            "test-123-abc",
            "test_123_abc",
            "a" * 255,
        ],
    )
    def test_accepts_safe_ids(self, workflow_id: str):
        assert validate_workflow_id(workflow_id) == workflow_id

    @pytest.mark.parametrize(
        "workflow_id",
        [
            "",
            "my workflow",
            "workflow@123",
            "workflow/test",
            "workflow..test",
            "a" * 256,
        ],
    )
    def test_rejects_unsafe_ids(self, workflow_id: str):
        with pytest.raises(ValueError, match=r"workflow_id|Invalid workflow_id"):
            validate_workflow_id(workflow_id)


def test_get_default_trace_dir_uses_override_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DOEFF_FLOW_TRACE_DIR", str(tmp_path / "override"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert get_default_trace_dir() == tmp_path / "override"


class TestSafeRepr:
    """Tests for _safe_repr helper."""

    def test_short_repr_is_unchanged(self):
        assert _safe_repr("hello") == "'hello'"
        assert _safe_repr(123) == "123"

    def test_long_repr_is_truncated(self):
        long_string = "a" * 300
        result = _safe_repr(long_string, max_len=50)
        assert len(result) == 50
        assert result.endswith("...")


def test_trace_dataclasses_keep_fields():
    frame = TraceFrame(
        function="my_function",
        file="/path/to/file.py",
        line=42,
        code="result = yield Pure(10)",
    )
    trace = LiveTrace(
        workflow_id="wf-001",
        step=1,
        status="running",
        current_effect="Pure(10)",
        trace=[frame],
        started_at="2025-01-01T00:00:00",
        updated_at="2025-01-01T00:00:01",
    )
    assert trace.trace[0].function == "my_function"
    assert trace.error is None
    assert trace.last_slog is None


class TestWriteTerminalTrace:
    """Tests for terminal trace writing."""

    def test_writes_completed_snapshot(self, tmp_path):
        @do
        def simple_workflow():
            a = yield Pure(10)
            b = yield Pure(20)
            return a + b

        result = run_sync(simple_workflow(), handlers=default_handlers())
        assert result.is_ok()
        write_terminal_trace("terminal-ok", tmp_path, result)

        entries = _read_trace_entries(tmp_path, "terminal-ok")
        assert len(entries) == 1
        assert entries[0]["status"] == "completed"
        assert entries[0]["result"] == "30"
        assert entries[0]["error"] is None

    def test_writes_failed_snapshot(self, tmp_path):
        @do
        def failing_workflow():
            yield Pure(10)
            raise ValueError("intentional failure")

        result = run_sync(failing_workflow(), handlers=default_handlers())
        assert result.is_err()
        write_terminal_trace("terminal-fail", tmp_path, result)

        entries = _read_trace_entries(tmp_path, "terminal-fail")
        assert len(entries) == 1
        assert entries[0]["status"] == "failed"
        assert "ValueError" in entries[0]["error"]


class TestRunWorkflow:
    """Tests for the run_workflow wrapper."""

    def test_runs_program_and_writes_trace(self, tmp_path):
        @do
        def simple_workflow():
            a = yield Pure(10)
            b = yield Pure(20)
            return a + b

        result = run_workflow(
            simple_workflow(),
            workflow_id="run-workflow-ok",
            trace_dir=tmp_path,
        )
        assert result.is_ok()
        assert result.value == 30

        entries = _read_trace_entries(tmp_path, "run-workflow-ok")
        assert len(entries) == 1
        assert entries[0]["status"] == "completed"

    def test_failed_run_writes_failed_trace(self, tmp_path):
        @do
        def failing_workflow():
            yield Pure(1)
            raise RuntimeError("boom")

        result = run_workflow(
            failing_workflow(),
            workflow_id="run-workflow-fail",
            trace_dir=tmp_path,
        )
        assert result.is_err()

        entries = _read_trace_entries(tmp_path, "run-workflow-fail")
        assert len(entries) == 1
        assert entries[0]["status"] == "failed"
        assert "RuntimeError" in entries[0]["error"]

    def test_multiple_runs_append_terminal_snapshots(self, tmp_path):
        @do
        def simple_workflow():
            return (yield Pure(42))

        run_workflow(simple_workflow(), workflow_id="append-test", trace_dir=tmp_path)
        run_workflow(simple_workflow(), workflow_id="append-test", trace_dir=tmp_path)

        entries = _read_trace_entries(tmp_path, "append-test")
        assert len(entries) == 2
        assert all(entry["status"] == "completed" for entry in entries)


class TestWithHandlerObservability:
    """Tests that replace removed CESK hook tracing with WithHandler."""

    def test_can_capture_effects_with_delegate(self):
        captured_effects: list[object] = []

        def capturing_handler(effect, k):
            _ = k
            captured_effects.append(effect)
            yield Delegate()

        @do
        def workflow():
            yield Put("counter", 0)
            current = yield Get("counter")
            yield Put("counter", current + 1)
            return current

        result = run_sync(
            WithHandler(capturing_handler, workflow()),
            handlers=default_handlers(),
            store={},
        )

        assert result.is_ok()
        assert result.value == 0
        effect_names = [type(effect).__name__ for effect in captured_effects]
        assert "PyPut" in effect_names
        assert "PyGet" in effect_names

    def test_can_modify_effect_result_with_resume(self):
        seen_ask = 0

        def override_ask_handler(effect, k):
            nonlocal seen_ask
            if type(effect).__name__ == "PyAsk" and seen_ask < 2:
                seen_ask += 1
                return (yield Resume(k, 5))
            yield Delegate()

        @do
        def workflow():
            a = yield Ask("a")
            b = yield Ask("b")
            return a + b

        result = run_sync(
            WithHandler(override_ask_handler, workflow()),
            handlers=default_handlers(),
            env={"a": 1, "b": 2},
        )

        assert result.is_ok()
        assert result.value == 10
