"""Tests for doeff-flow trace effects and handler maps."""

from __future__ import annotations

import json
from pathlib import Path

from doeff_flow.effects import TraceAnnotate, TraceCapture, TracePush, TraceSnapshot
from doeff_flow.handlers import mock_handlers, production_handlers

from doeff import do, run_with_handler_map


def _read_entries(trace_dir: Path, workflow_id: str) -> list[dict]:
    trace_file = trace_dir / workflow_id / "trace.jsonl"
    assert trace_file.exists()
    return [json.loads(line) for line in trace_file.read_text().splitlines() if line.strip()]


@do
def _trace_program():
    yield TracePush(name="ingest", metadata={"request_id": "req-001"})
    yield TraceAnnotate(key="tenant", value="acme")
    yield TraceSnapshot(label="checkpoint")
    captured = yield TraceCapture(format="dict")
    return captured


def test_effect_exports():
    from doeff_flow.effects import TraceAnnotate as ImportedTraceAnnotate
    from doeff_flow.effects import TracePush as ImportedTracePush

    assert ImportedTracePush is TracePush
    assert ImportedTraceAnnotate is TraceAnnotate


def test_handler_exports():
    from doeff_flow.handlers import mock_handlers as imported_mock_handlers
    from doeff_flow.handlers import production_handlers as imported_production_handlers

    assert imported_production_handlers is production_handlers
    assert imported_mock_handlers is mock_handlers


def test_mock_handlers_capture_in_memory() -> None:
    result = run_with_handler_map(_trace_program(), mock_handlers())

    assert result.is_ok()
    entries = result.value
    assert isinstance(entries, list)
    assert len(entries) == 3
    assert entries[-1]["last_slog"]["label"] == "checkpoint"
    assert entries[-1]["last_slog"]["annotations"]["tenant"] == "acme"


def test_production_handlers_write_trace_file(tmp_path: Path) -> None:
    workflow_id = "trace-prod-test"
    result = run_with_handler_map(
        _trace_program(),
        production_handlers(workflow_id=workflow_id, trace_dir=tmp_path),
    )

    assert result.is_ok()
    captured = result.value
    assert isinstance(captured, list)
    assert len(captured) == 3

    trace_entries = _read_entries(tmp_path, workflow_id)
    assert len(trace_entries) == 3
    assert trace_entries[-1]["last_slog"]["label"] == "checkpoint"
    assert trace_entries[-1]["last_slog"]["annotations"]["request_id"] == "req-001"


def test_handler_swapping_changes_trace_side_effects(tmp_path: Path) -> None:
    workflow_id = "trace-side-effects"
    trace_file = tmp_path / workflow_id / "trace.jsonl"
    assert not trace_file.exists()

    mock_result = run_with_handler_map(_trace_program(), mock_handlers())
    assert mock_result.is_ok()
    assert not trace_file.exists()

    prod_result = run_with_handler_map(
        _trace_program(),
        production_handlers(workflow_id=workflow_id, trace_dir=tmp_path),
    )
    assert prod_result.is_ok()
    assert trace_file.exists()
