"""Tests for doeff_flow.trace module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from doeff import do
from doeff.cesk import run_sync
from doeff.effects import Pure
from doeff_flow import run_workflow
from doeff_flow.trace import (
    LiveTrace,
    TraceFrame,
    _safe_repr,
    trace_observer,
    validate_workflow_id,
)


class TestValidateWorkflowId:
    """Tests for validate_workflow_id function."""

    def test_valid_alphanumeric(self):
        """Valid alphanumeric workflow IDs should pass."""
        assert validate_workflow_id("workflow001") == "workflow001"
        assert validate_workflow_id("MyWorkflow") == "MyWorkflow"
        assert validate_workflow_id("test123") == "test123"

    def test_valid_with_hyphen(self):
        """Valid workflow IDs with hyphens should pass."""
        assert validate_workflow_id("my-workflow") == "my-workflow"
        assert validate_workflow_id("test-123-abc") == "test-123-abc"

    def test_valid_with_underscore(self):
        """Valid workflow IDs with underscores should pass."""
        assert validate_workflow_id("my_workflow") == "my_workflow"
        assert validate_workflow_id("test_123_abc") == "test_123_abc"

    def test_invalid_with_spaces(self):
        """Workflow IDs with spaces should be rejected."""
        with pytest.raises(ValueError, match="Invalid workflow_id"):
            validate_workflow_id("my workflow")

    def test_invalid_with_special_chars(self):
        """Workflow IDs with special characters should be rejected."""
        with pytest.raises(ValueError, match="Invalid workflow_id"):
            validate_workflow_id("workflow@123")
        with pytest.raises(ValueError, match="Invalid workflow_id"):
            validate_workflow_id("workflow/test")
        with pytest.raises(ValueError, match="Invalid workflow_id"):
            validate_workflow_id("workflow..test")

    def test_empty_string(self):
        """Empty workflow ID should be rejected."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_workflow_id("")

    def test_too_long(self):
        """Workflow IDs longer than 255 characters should be rejected."""
        long_id = "a" * 256
        with pytest.raises(ValueError, match="too long"):
            validate_workflow_id(long_id)

    def test_max_length_valid(self):
        """Workflow IDs at exactly 255 characters should pass."""
        max_id = "a" * 255
        assert validate_workflow_id(max_id) == max_id


class TestSafeRepr:
    """Tests for _safe_repr function."""

    def test_short_repr(self):
        """Short representations should not be truncated."""
        assert _safe_repr("hello") == "'hello'"
        assert _safe_repr(123) == "123"
        assert _safe_repr([1, 2, 3]) == "[1, 2, 3]"

    def test_long_repr_truncation(self):
        """Long representations should be truncated."""
        long_string = "a" * 300
        result = _safe_repr(long_string, max_len=50)
        assert len(result) == 50
        assert result.endswith("...")

    def test_custom_max_len(self):
        """Custom max_len should be respected."""
        obj = list(range(100))
        result = _safe_repr(obj, max_len=30)
        assert len(result) == 30
        assert result.endswith("...")


class TestTraceFrame:
    """Tests for TraceFrame dataclass."""

    def test_creation(self):
        """TraceFrame should be creatable with all fields."""
        frame = TraceFrame(
            function="my_function",
            file="/path/to/file.py",
            line=42,
            code="result = yield Pure(10)",
        )
        assert frame.function == "my_function"
        assert frame.file == "/path/to/file.py"
        assert frame.line == 42
        assert frame.code == "result = yield Pure(10)"

    def test_optional_code(self):
        """TraceFrame code can be None."""
        frame = TraceFrame(
            function="my_function",
            file="/path/to/file.py",
            line=42,
            code=None,
        )
        assert frame.code is None


class TestLiveTrace:
    """Tests for LiveTrace dataclass."""

    def test_creation_minimal(self):
        """LiveTrace should be creatable with required fields."""
        trace = LiveTrace(
            workflow_id="wf-001",
            step=1,
            status="running",
            current_effect="Pure(10)",
            trace=[],
            started_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:01",
        )
        assert trace.workflow_id == "wf-001"
        assert trace.step == 1
        assert trace.status == "running"
        assert trace.error is None

    def test_creation_with_error(self):
        """LiveTrace should support error field."""
        trace = LiveTrace(
            workflow_id="wf-001",
            step=5,
            status="failed",
            current_effect=None,
            trace=[],
            started_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:05",
            error="KeyError: 'missing_key'",
        )
        assert trace.status == "failed"
        assert trace.error == "KeyError: 'missing_key'"


class TestTraceObserver:
    """Tests for trace_observer context manager."""

    def test_creates_trace_file(self):
        """trace_observer should create the trace file directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def simple_workflow():
                return (yield Pure(42))

            with trace_observer("test-wf", trace_dir) as on_step:
                run_sync(simple_workflow(), on_step=on_step)

            trace_file = trace_dir / "test-wf" / "trace.jsonl"
            assert trace_file.exists()

    def test_writes_jsonl_entries(self):
        """trace_observer should write JSONL entries for each step."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def multi_step_workflow():
                a = yield Pure(10)
                b = yield Pure(20)
                c = yield Pure(30)
                return a + b + c

            with trace_observer("test-wf", trace_dir) as on_step:
                result = run_sync(multi_step_workflow(), on_step=on_step)

            assert result.value == 60

            trace_file = trace_dir / "test-wf" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")

            # Should have multiple entries (one per step)
            assert len(lines) > 1

            # Each line should be valid JSON
            for line in lines:
                data = json.loads(line)
                assert "workflow_id" in data
                assert "step" in data
                assert "status" in data
                assert data["workflow_id"] == "test-wf"

    def test_final_status_completed(self):
        """Final trace entry should have 'completed' status on success."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def simple_workflow():
                return (yield Pure(42))

            with trace_observer("test-wf", trace_dir) as on_step:
                run_sync(simple_workflow(), on_step=on_step)

            trace_file = trace_dir / "test-wf" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")
            last_entry = json.loads(lines[-1])

            assert last_entry["status"] == "completed"

    def test_captures_current_effect(self):
        """trace_observer should capture the current effect being processed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def simple_workflow():
                return (yield Pure(42))

            with trace_observer("test-wf", trace_dir) as on_step:
                run_sync(simple_workflow(), on_step=on_step)

            trace_file = trace_dir / "test-wf" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")

            # At least one entry should have a current_effect
            effects = [json.loads(line).get("current_effect") for line in lines]
            non_null_effects = [e for e in effects if e is not None]
            assert len(non_null_effects) > 0

    def test_invalid_workflow_id_rejected(self):
        """trace_observer should reject invalid workflow IDs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            with pytest.raises(ValueError, match="Invalid workflow_id"):
                with trace_observer("invalid/id", trace_dir):
                    pass

    def test_accepts_string_trace_dir(self):
        """trace_observer should accept string path for trace_dir."""
        with tempfile.TemporaryDirectory() as tmp_dir:

            @do
            def simple_workflow():
                return (yield Pure(42))

            with trace_observer("test-wf", tmp_dir) as on_step:  # String instead of Path
                result = run_sync(simple_workflow(), on_step=on_step)

            assert result.value == 42
            trace_file = Path(tmp_dir) / "test-wf" / "trace.jsonl"
            assert trace_file.exists()


class TestRunWorkflow:
    """Tests for run_workflow convenience wrapper."""

    def test_basic_execution(self):
        """run_workflow should execute the workflow and return result."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def simple_workflow():
                a = yield Pure(10)
                b = yield Pure(20)
                return a + b

            result = run_workflow(
                simple_workflow(),
                workflow_id="test-001",
                trace_dir=trace_dir,
            )

            assert result.is_ok
            assert result.value == 30

    def test_creates_trace_file(self):
        """run_workflow should create trace file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def simple_workflow():
                return (yield Pure(42))

            run_workflow(
                simple_workflow(),
                workflow_id="test-001",
                trace_dir=trace_dir,
            )

            trace_file = trace_dir / "test-001" / "trace.jsonl"
            assert trace_file.exists()

    def test_accepts_string_trace_dir(self):
        """run_workflow should accept string path for trace_dir."""
        with tempfile.TemporaryDirectory() as tmp_dir:

            @do
            def simple_workflow():
                return (yield Pure(42))

            result = run_workflow(
                simple_workflow(),
                workflow_id="test-001",
                trace_dir=tmp_dir,  # String instead of Path
            )

            assert result.is_ok
            assert result.value == 42

    def test_nested_workflow_trace(self):
        """run_workflow should capture nested @do function calls."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def inner_function():
                return (yield Pure(10))

            @do
            def outer_function():
                x = yield inner_function()
                return x * 2

            result = run_workflow(
                outer_function(),
                workflow_id="nested-test",
                trace_dir=trace_dir,
            )

            assert result.is_ok
            assert result.value == 20

            # Check trace has entries
            trace_file = trace_dir / "nested-test" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) > 0


class TestSlogDetection:
    """Tests for slog (structured log) detection in traces."""

    def test_slog_captured_in_trace(self):
        """slog effects should be captured as last_slog in trace."""
        from doeff.effects.writer import slog

        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def workflow_with_slog():
                yield slog(status="starting", msg="Beginning workflow")
                result = yield Pure(42)
                yield slog(status="completed", msg=f"Result: {result}")
                return result

            result = run_workflow(
                workflow_with_slog(),
                workflow_id="slog-test",
                trace_dir=trace_dir,
            )

            assert result.is_ok
            assert result.value == 42

            # Check trace file for slog entries
            trace_file = trace_dir / "slog-test" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")

            # Find entries with last_slog
            slog_entries = []
            for line in lines:
                data = json.loads(line)
                if data.get("last_slog"):
                    slog_entries.append(data["last_slog"])

            assert len(slog_entries) >= 2
            # Check first slog
            assert slog_entries[0]["status"] == "starting"
            assert slog_entries[0]["msg"] == "Beginning workflow"
            # Check second slog
            assert slog_entries[1]["status"] == "completed"
            assert "Result: 42" in slog_entries[1]["msg"]

    def test_last_slog_field_in_livetrace(self):
        """LiveTrace should have last_slog field."""
        trace = LiveTrace(
            workflow_id="test-wf",
            step=1,
            status="running",
            current_effect="WriterTellEffect(...)",
            trace=[],
            started_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:01",
            last_slog={"status": "reviewing", "msg": "Checking PR"},
        )
        assert trace.last_slog == {"status": "reviewing", "msg": "Checking PR"}

    def test_last_slog_defaults_to_none(self):
        """LiveTrace.last_slog should default to None."""
        trace = LiveTrace(
            workflow_id="test-wf",
            step=1,
            status="running",
            current_effect=None,
            trace=[],
            started_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:01",
        )
        assert trace.last_slog is None

    def test_non_dict_slog_not_captured(self):
        """Non-dict slog messages should not be captured as last_slog."""
        from doeff.effects.writer import tell

        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def workflow_with_string_log():
                yield tell("This is a string message")
                result = yield Pure(42)
                return result

            result = run_workflow(
                workflow_with_string_log(),
                workflow_id="string-log-test",
                trace_dir=trace_dir,
            )

            assert result.is_ok

            # Check trace file - string logs should not appear in last_slog
            trace_file = trace_dir / "string-log-test" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")

            slog_entries = [
                json.loads(line).get("last_slog")
                for line in lines
                if json.loads(line).get("last_slog") is not None
            ]
            # String messages should not be captured
            assert len(slog_entries) == 0

    def test_slog_written_to_jsonl(self):
        """last_slog should be written to JSONL output."""
        from doeff.effects.writer import slog

        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            @do
            def workflow_with_slog():
                yield slog(status="testing", msg="Test message", extra_data=123)
                return (yield Pure("done"))

            run_workflow(
                workflow_with_slog(),
                workflow_id="jsonl-slog-test",
                trace_dir=trace_dir,
            )

            trace_file = trace_dir / "jsonl-slog-test" / "trace.jsonl"
            lines = trace_file.read_text().strip().split("\n")

            # Find entry with slog
            slog_entry = None
            for line in lines:
                data = json.loads(line)
                if data.get("last_slog"):
                    slog_entry = data["last_slog"]
                    break

            assert slog_entry is not None
            assert slog_entry["status"] == "testing"
            assert slog_entry["msg"] == "Test message"
            assert slog_entry["extra_data"] == 123
