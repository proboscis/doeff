"""Tests for runbox CLI integration."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from doeff.cli.runbox import (
    RunboxRecordResult,
    create_runbox_record,
    get_head_commit,
    get_repo_url,
    get_uncommitted_diff,
    is_runbox_available,
    log_runbox_record,
    maybe_create_runbox_record,
)


class TestIsRunboxAvailable:
    """Tests for is_runbox_available."""

    def test_returns_true_when_runbox_in_path(self) -> None:
        """Test that function returns True when runbox is available."""
        with patch("shutil.which", return_value="/usr/local/bin/runbox"):
            assert is_runbox_available() is True

    def test_returns_false_when_runbox_not_in_path(self) -> None:
        """Test that function returns False when runbox is not available."""
        with patch("shutil.which", return_value=None):
            assert is_runbox_available() is False


class TestGetHeadCommit:
    """Tests for get_head_commit."""

    def test_returns_commit_hash_on_success(self) -> None:
        """Test that function returns commit hash when in git repo."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc123def456\n"

        with patch("subprocess.run", return_value=mock_result):
            result = get_head_commit()
            assert result == "abc123def456"

    def test_returns_none_on_failure(self) -> None:
        """Test that function returns None when git command fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = get_head_commit()
            assert result is None

    def test_returns_none_on_timeout(self) -> None:
        """Test that function returns None on timeout."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = get_head_commit()
            assert result is None

    def test_returns_none_when_git_not_found(self) -> None:
        """Test that function returns None when git is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = get_head_commit()
            assert result is None


class TestGetUncommittedDiff:
    """Tests for get_uncommitted_diff."""

    def test_returns_diff_when_dirty(self) -> None:
        """Test that function returns diff when working tree is dirty."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "diff --git a/foo.py b/foo.py\n+added line"

        with patch("subprocess.run", return_value=mock_result):
            result = get_uncommitted_diff()
            assert result == "diff --git a/foo.py b/foo.py\n+added line"

    def test_returns_none_when_clean(self) -> None:
        """Test that function returns None when working tree is clean."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = get_uncommitted_diff()
            assert result is None

    def test_returns_none_on_failure(self) -> None:
        """Test that function returns None when git command fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = get_uncommitted_diff()
            assert result is None


class TestGetRepoUrl:
    """Tests for get_repo_url."""

    def test_returns_url_on_success(self) -> None:
        """Test that function returns URL when remote exists."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "git@github.com:org/repo.git\n"

        with patch("subprocess.run", return_value=mock_result):
            result = get_repo_url()
            assert result == "git@github.com:org/repo.git"

    def test_returns_none_on_failure(self) -> None:
        """Test that function returns None when no remote configured."""
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = get_repo_url()
            assert result is None


class TestCreateRunboxRecord:
    """Tests for create_runbox_record."""

    def test_returns_none_when_runbox_unavailable(self) -> None:
        """Test that function returns None when runbox is not available."""
        with patch("doeff.cli.runbox.is_runbox_available", return_value=False):
            result = create_runbox_record(["uv run python", "script.py"])
            assert result is None

    def test_creates_record_successfully(self) -> None:
        """Test successful record creation."""
        mock_runbox_result = MagicMock()
        mock_runbox_result.returncode = 0
        mock_runbox_result.stdout = "Created record: rec_12345\n  Short ID: 12345"

        with (
            patch("doeff.cli.runbox.is_runbox_available", return_value=True),
            patch("doeff.cli.runbox.get_head_commit", return_value="abc123"),
            patch("doeff.cli.runbox.get_repo_url", return_value="git@github.com:org/repo.git"),
            patch("doeff.cli.runbox.get_uncommitted_diff", return_value=None),
            patch("subprocess.run", return_value=mock_runbox_result) as mock_run,
        ):
            result = create_runbox_record(["uv run python", "script.py"], cwd="/test/dir")

            assert result is not None
            assert result.success is True
            assert result.record_id == "rec_12345"

            # Verify the subprocess call
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["runbox", "create", "record"]

            # Parse the JSON input
            input_json = json.loads(call_args[1]["input"])
            assert input_json["command"]["argv"] == ["uv run python", "script.py"]
            assert input_json["command"]["cwd"] == "/test/dir"
            assert input_json["source"] == "doeff"
            assert input_json["git_state"]["commit"] == "abc123"
            assert input_json["git_state"]["repo_url"] == "git@github.com:org/repo.git"

    def test_includes_diff_when_dirty(self) -> None:
        """Test that diff is included when working tree is dirty."""
        mock_runbox_result = MagicMock()
        mock_runbox_result.returncode = 0
        mock_runbox_result.stdout = "Created record: rec_12345\n  Short ID: 12345"

        with (
            patch("doeff.cli.runbox.is_runbox_available", return_value=True),
            patch("doeff.cli.runbox.get_head_commit", return_value="abc123"),
            patch("doeff.cli.runbox.get_repo_url", return_value=None),
            patch("doeff.cli.runbox.get_uncommitted_diff", return_value="+added line"),
            patch("subprocess.run", return_value=mock_runbox_result) as mock_run,
        ):
            create_runbox_record(["uv run python", "script.py"])

            input_json = json.loads(mock_run.call_args[1]["input"])
            assert input_json["git_state"]["diff"] == "+added line"

    def test_handles_runbox_failure(self) -> None:
        """Test handling of runbox CLI failure."""
        mock_runbox_result = MagicMock()
        mock_runbox_result.returncode = 1
        mock_runbox_result.stderr = "Error: invalid JSON"

        with (
            patch("doeff.cli.runbox.is_runbox_available", return_value=True),
            patch("doeff.cli.runbox.get_head_commit", return_value="abc123"),
            patch("doeff.cli.runbox.get_repo_url", return_value=None),
            patch("doeff.cli.runbox.get_uncommitted_diff", return_value=None),
            patch("subprocess.run", return_value=mock_runbox_result),
        ):
            result = create_runbox_record(["uv run python", "script.py"])

            assert result is not None
            assert result.success is False
            assert result.error_message == "Error: invalid JSON"

    def test_handles_timeout(self) -> None:
        """Test handling of runbox CLI timeout."""
        with (
            patch("doeff.cli.runbox.is_runbox_available", return_value=True),
            patch("doeff.cli.runbox.get_head_commit", return_value="abc123"),
            patch("doeff.cli.runbox.get_repo_url", return_value=None),
            patch("doeff.cli.runbox.get_uncommitted_diff", return_value=None),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("runbox", 10)),
        ):
            result = create_runbox_record(["uv run python", "script.py"])

            assert result is not None
            assert result.success is False
            assert "Timeout" in result.error_message


class TestLogRunboxRecord:
    """Tests for log_runbox_record."""

    def test_logs_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test logging successful record creation."""
        result = RunboxRecordResult(record_id="rec_12345", success=True)
        log_runbox_record(result)

        captured = capsys.readouterr()
        assert "[runbox] Record stored: rec_12345" in captured.err
        assert "[runbox] Replay with: runbox replay rec_12345" in captured.err

    def test_logs_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test logging failed record creation."""
        result = RunboxRecordResult(
            record_id="", success=False, error_message="Connection refused"
        )
        log_runbox_record(result)

        captured = capsys.readouterr()
        assert "[runbox] Warning: Failed to create record: Connection refused" in captured.err


class TestMaybeCreateRunboxRecord:
    """Tests for maybe_create_runbox_record."""

    def test_returns_none_when_skip_runbox_true(self) -> None:
        """Test that function returns None when skip_runbox is True."""
        result = maybe_create_runbox_record(skip_runbox=True)
        assert result is None

    def test_returns_none_when_runbox_unavailable(self) -> None:
        """Test that function returns None when runbox is not available."""
        with patch("doeff.cli.runbox.is_runbox_available", return_value=False):
            result = maybe_create_runbox_record()
            assert result is None

    def test_returns_record_id_on_success(self) -> None:
        """Test that function returns record ID on success."""
        mock_result = RunboxRecordResult(record_id="rec_12345", success=True)

        with (
            patch("doeff.cli.runbox.is_runbox_available", return_value=True),
            patch("doeff.cli.runbox.create_runbox_record", return_value=mock_result),
            patch("doeff.cli.runbox.log_runbox_record"),
        ):
            result = maybe_create_runbox_record(["uv run python", "script.py"])
            assert result == "rec_12345"

    def test_returns_none_on_failure(self) -> None:
        """Test that function returns None on failure."""
        mock_result = RunboxRecordResult(record_id="", success=False, error_message="Error")

        with (
            patch("doeff.cli.runbox.is_runbox_available", return_value=True),
            patch("doeff.cli.runbox.create_runbox_record", return_value=mock_result),
            patch("doeff.cli.runbox.log_runbox_record"),
        ):
            result = maybe_create_runbox_record(["uv run python", "script.py"])
            assert result is None

    def test_uses_sys_argv_when_no_argv_provided(self) -> None:
        """Test that function uses sys.argv when no argv is provided."""
        mock_result = RunboxRecordResult(record_id="rec_12345", success=True)

        with (
            patch("doeff.cli.runbox.is_runbox_available", return_value=True),
            patch("doeff.cli.runbox.create_runbox_record", return_value=mock_result) as mock_create,
            patch("doeff.cli.runbox.log_runbox_record"),
            patch("sys.argv", ["doeff", "run", "--program", "test.prog"]),
        ):
            maybe_create_runbox_record()

            mock_create.assert_called_once_with(
                ["doeff", "run", "--program", "test.prog"], tags=None
            )
