"""Tests for doeff_flow.cli module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from doeff_flow.cli import cli


class TestPsCommand:
    """Tests for the ps command."""

    def test_no_workflows(self):
        """ps should report no workflows when directory is empty."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = CliRunner()
            result = runner.invoke(cli, ["ps", "--trace-dir", tmp_dir])
            assert result.exit_code == 0
            assert "No workflows found" in result.output

    def test_no_trace_dir(self):
        """ps should report no workflows when directory doesn't exist."""
        runner = CliRunner()
        result = runner.invoke(cli, ["ps", "--trace-dir", "/nonexistent/path"])
        assert result.exit_code == 0
        assert "No workflows found" in result.output

    def test_lists_workflows(self):
        """ps should list workflows with their status."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            # Create a mock workflow trace
            wf_dir = trace_dir / "wf-001"
            wf_dir.mkdir()
            trace_file = wf_dir / "trace.jsonl"
            trace_data = {
                "workflow_id": "wf-001",
                "step": 5,
                "status": "running",
                "current_effect": "Pure(10)",
                "trace": [],
                "started_at": "2025-01-01T00:00:00",
                "updated_at": "2025-01-01T00:00:01",
            }
            trace_file.write_text(json.dumps(trace_data) + "\n")

            runner = CliRunner()
            result = runner.invoke(cli, ["ps", "--trace-dir", tmp_dir])
            assert result.exit_code == 0
            assert "wf-001" in result.output
            assert "running" in result.output
            # Rich table format shows step in a column, not "step N"
            assert "5" in result.output

    def test_lists_multiple_workflows(self):
        """ps should list multiple workflows."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)

            for wf_id, status, step in [
                ("wf-001", "running", 5),
                ("wf-002", "completed", 10),
                ("wf-003", "failed", 3),
            ]:
                wf_dir = trace_dir / wf_id
                wf_dir.mkdir()
                trace_file = wf_dir / "trace.jsonl"
                trace_data = {
                    "workflow_id": wf_id,
                    "step": step,
                    "status": status,
                    "current_effect": None,
                    "trace": [],
                    "started_at": "2025-01-01T00:00:00",
                    "updated_at": "2025-01-01T00:00:01",
                }
                trace_file.write_text(json.dumps(trace_data) + "\n")

            runner = CliRunner()
            result = runner.invoke(cli, ["ps", "--trace-dir", tmp_dir])
            assert result.exit_code == 0
            assert "wf-001" in result.output
            assert "wf-002" in result.output
            assert "wf-003" in result.output


class TestHistoryCommand:
    """Tests for the history command."""

    def test_no_trace_file(self):
        """history should report when no trace exists."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = CliRunner()
            result = runner.invoke(cli, ["history", "nonexistent-wf", "--trace-dir", tmp_dir])
            assert result.exit_code == 0
            assert "No trace found" in result.output

    def test_shows_history(self):
        """history should show execution history."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            wf_dir = trace_dir / "wf-001"
            wf_dir.mkdir()
            trace_file = wf_dir / "trace.jsonl"

            # Write multiple trace entries
            entries = []
            for step in range(1, 6):
                entry = {
                    "workflow_id": "wf-001",
                    "step": step,
                    "status": "running" if step < 5 else "completed",
                    "current_effect": f"Pure({step * 10})" if step < 5 else None,
                    "trace": [],
                    "started_at": "2025-01-01T00:00:00",
                    "updated_at": f"2025-01-01T00:00:0{step}",
                }
                entries.append(json.dumps(entry))

            trace_file.write_text("\n".join(entries) + "\n")

            runner = CliRunner()
            result = runner.invoke(cli, ["history", "wf-001", "--trace-dir", tmp_dir])
            assert result.exit_code == 0
            # Rich table shows "Step" column header
            assert "Step" in result.output
            # Steps are shown as numbers in the table
            assert "1" in result.output
            assert "5" in result.output
            assert "completed" in result.output

    def test_last_option(self):
        """history --last N should limit output."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            wf_dir = trace_dir / "wf-001"
            wf_dir.mkdir()
            trace_file = wf_dir / "trace.jsonl"

            # Write 20 entries
            entries = []
            for step in range(1, 21):
                entry = {
                    "workflow_id": "wf-001",
                    "step": step,
                    "status": "running",
                    "current_effect": f"Pure({step})",
                    "trace": [],
                    "started_at": "2025-01-01T00:00:00",
                    "updated_at": "2025-01-01T00:00:01",
                }
                entries.append(json.dumps(entry))

            trace_file.write_text("\n".join(entries) + "\n")

            runner = CliRunner()
            result = runner.invoke(
                cli, ["history", "wf-001", "--trace-dir", tmp_dir, "--last", "3"]
            )
            assert result.exit_code == 0
            # Should only show last 3 entries (steps 18, 19, 20)
            # Check that step 18, 19, 20 are present but step 17 is not
            assert "18" in result.output
            assert "19" in result.output
            assert "20" in result.output
            # Step 17 should NOT be in the output
            # (we need to be careful - "17" might appear in other places)
            # Count rows in the table - with rich, data rows have │ separators
            data_rows = [l for l in result.output.split("\n") if "running" in l]
            assert len(data_rows) == 3


class TestWatchCommand:
    """Tests for the watch command (limited - watch is interactive)."""

    def test_help(self):
        """watch --help should show usage."""
        runner = CliRunner()
        result = runner.invoke(cli, ["watch", "--help"])
        assert result.exit_code == 0
        assert "Watch live effect trace" in result.output
        assert "--exit-on-complete" in result.output
