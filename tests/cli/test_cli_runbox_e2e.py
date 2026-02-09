"""End-to-end tests for runbox CLI integration.

These tests verify the actual runbox integration works correctly
by running the doeff CLI as a subprocess and checking output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skip(
    reason="Legacy CLI interpreter fixtures rely on pre-rust_vm program semantics."
)


def run_cli(
    *args: str, env_override: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run doeff CLI as subprocess with proper environment."""
    command = ["uv", "run", "doeff", "run", *args]
    pythonpath = str(PROJECT_ROOT)
    if "PYTHONPATH" in os.environ:
        pythonpath = f"{PROJECT_ROOT}{os.pathsep}{os.environ['PYTHONPATH']}"

    env = {
        "PYTHONPATH": pythonpath,
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DOEFF_DISABLE_DEFAULT_ENV": "1",
        # Disable profiling to reduce noise in output
        "DOEFF_DISABLE_PROFILE": "1",
    }
    if env_override:
        env.update(env_override)

    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def is_runbox_available() -> bool:
    """Check if runbox CLI is available in PATH."""
    return shutil.which("runbox") is not None


def get_runbox_records_dir() -> Path:
    """Get the runbox records directory."""
    home = Path.home()
    return home / ".local" / "share" / "runbox" / "records"


class TestRunboxIntegrationE2E:
    """End-to-end tests for runbox integration."""

    @pytest.mark.e2e
    @pytest.mark.skipif(not is_runbox_available(), reason="runbox CLI not installed")
    def test_runbox_record_created_on_run(self) -> None:
        """Test that runbox record is created when runbox is available."""
        result = run_cli(
            "--program",
            "tests.cli_assets.sample_program",
            "--interpreter",
            "tests.cli_assets.sync_interpreter",
        )

        # Should succeed
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Should have runbox messages in stderr
        assert "[runbox] Record stored:" in result.stderr
        assert "[runbox] Replay with: runbox replay" in result.stderr

        # Should have proper output
        assert "5" in result.stdout

    @pytest.mark.e2e
    @pytest.mark.skipif(not is_runbox_available(), reason="runbox CLI not installed")
    def test_runbox_record_has_valid_id(self) -> None:
        """Test that runbox record ID is valid format."""
        result = run_cli(
            "--program",
            "tests.cli_assets.sample_program",
            "--interpreter",
            "tests.cli_assets.sync_interpreter",
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Extract record ID from stderr
        for line in result.stderr.split("\n"):
            if "[runbox] Record stored:" in line:
                # Should contain rec_ prefix (runbox record ID format)
                assert "rec_" in line
                break
        else:
            pytest.fail("No runbox record ID found in output")

    @pytest.mark.e2e
    @pytest.mark.skipif(not is_runbox_available(), reason="runbox CLI not installed")
    def test_no_runbox_flag_skips_capture(self) -> None:
        """Test that --no-runbox flag skips record creation."""
        result = run_cli(
            "--program",
            "tests.cli_assets.sample_program",
            "--interpreter",
            "tests.cli_assets.sync_interpreter",
            "--no-runbox",
        )

        # Should succeed
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Should NOT have runbox messages
        assert "[runbox]" not in result.stderr

        # Should still have output
        assert "5" in result.stdout

    @pytest.mark.e2e
    @pytest.mark.skipif(not is_runbox_available(), reason="runbox CLI not installed")
    def test_runbox_with_c_flag(self) -> None:
        """Test runbox integration works with -c flag."""
        result = run_cli(
            "-c",
            "from doeff import Program; Program.pure(42)",
            "--interpreter",
            "tests.cli_assets.sync_interpreter",
        )

        # Should succeed
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Should have runbox messages
        assert "[runbox] Record stored:" in result.stderr
        assert "rec_" in result.stderr

    @pytest.mark.e2e
    @pytest.mark.skipif(not is_runbox_available(), reason="runbox CLI not installed")
    def test_runbox_with_json_format(self) -> None:
        """Test runbox messages don't interfere with JSON output."""
        result = run_cli(
            "--program",
            "tests.cli_assets.sample_program",
            "--interpreter",
            "tests.cli_assets.sync_interpreter",
            "--format",
            "json",
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Runbox messages should be in stderr, not stdout
        assert "[runbox]" in result.stderr
        assert "[runbox]" not in result.stdout

        # JSON output should be valid
        payload = json.loads(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 5

    @pytest.mark.e2e
    @pytest.mark.skipif(not is_runbox_available(), reason="runbox CLI not installed")
    def test_runbox_record_file_created(self) -> None:
        """Test that runbox record file is actually created on disk."""
        result = run_cli(
            "--program",
            "tests.cli_assets.sample_program",
            "--interpreter",
            "tests.cli_assets.sync_interpreter",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Extract record ID
        record_id = None
        for line in result.stderr.split("\n"):
            if "[runbox] Record stored:" in line:
                # Parse "rec_..." from the line
                parts = line.split("rec_")
                if len(parts) > 1:
                    record_id = "rec_" + parts[1].split()[0]
                    break

        assert record_id is not None, "Could not extract record ID"

        # Verify the record file exists
        records_dir = get_runbox_records_dir()
        record_file = records_dir / f"{record_id}.json"
        assert record_file.exists(), f"Record file {record_file} does not exist"

        # Verify it contains valid JSON with expected fields
        with open(record_file) as f:
            record_data = json.load(f)

        assert "command" in record_data
        assert "argv" in record_data["command"]
        assert "source" in record_data
        assert record_data["source"] == "doeff"

    @pytest.mark.e2e
    @pytest.mark.skipif(not is_runbox_available(), reason="runbox CLI not installed")
    def test_runbox_record_captures_git_state(self) -> None:
        """Test that runbox record captures git commit information."""
        result = run_cli(
            "--program",
            "tests.cli_assets.sample_program",
            "--interpreter",
            "tests.cli_assets.sync_interpreter",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Extract record ID
        record_id = None
        for line in result.stderr.split("\n"):
            if "[runbox] Record stored:" in line:
                parts = line.split("rec_")
                if len(parts) > 1:
                    record_id = "rec_" + parts[1].split()[0]
                    break

        assert record_id is not None

        # Verify git state is captured
        records_dir = get_runbox_records_dir()
        record_file = records_dir / f"{record_id}.json"

        with open(record_file) as f:
            record_data = json.load(f)

        # Should have git_state with commit
        assert "git_state" in record_data, "Record should contain git_state"
        assert "commit" in record_data["git_state"], "git_state should contain commit"
        # Commit should be a 40-character hex string
        commit = record_data["git_state"]["commit"]
        assert len(commit) == 40, f"Commit should be 40 chars, got {len(commit)}"
        assert all(c in "0123456789abcdef" for c in commit.lower()), "Commit should be hex"


class TestRunboxUnitIntegration:
    """Tests for runbox integration that don't require runbox CLI."""

    def test_is_runbox_available_returns_bool(self) -> None:
        """Test that is_runbox_available returns a boolean."""
        from doeff.cli.runbox import is_runbox_available

        result = is_runbox_available()
        assert isinstance(result, bool)

    def test_get_head_commit_returns_string_or_none(self) -> None:
        """Test that get_head_commit works in git repo."""
        from doeff.cli.runbox import get_head_commit

        result = get_head_commit()
        # In a git repo, should return a commit hash
        if result is not None:
            assert isinstance(result, str)
            assert len(result) == 40  # Git commit hashes are 40 chars

    def test_maybe_create_runbox_record_respects_skip_flag(self) -> None:
        """Test that skip_runbox=True prevents record creation."""
        from doeff.cli.runbox import maybe_create_runbox_record

        result = maybe_create_runbox_record(["test", "command"], skip_runbox=True)
        assert result is None
