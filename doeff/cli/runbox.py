"""Runbox CLI integration for automatic record capture.

When runbox CLI is installed, doeff run automatically captures execution
records before running programs, enabling reproducible execution via
`runbox replay <record-id>`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def is_runbox_available() -> bool:
    """Check if runbox CLI is installed and available in PATH."""
    return shutil.which("runbox") is not None


def get_head_commit() -> str | None:
    """Get the current git HEAD commit hash.

    Returns:
        40-character commit hash, or None if not in a git repository
        or git is not available.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_uncommitted_diff() -> str | None:
    """Get uncommitted changes as a git diff.

    Returns:
        Git diff string if there are uncommitted changes,
        empty string if working tree is clean,
        or None if not in a git repository.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,  # Diff can be large
            check=False,
        )
        if result.returncode == 0:
            diff = result.stdout.strip()
            return diff if diff else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_repo_url() -> str | None:
    """Get the git remote origin URL.

    Returns:
        Remote URL or None if not available.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


@dataclass
class RunboxRecordResult:
    """Result of creating a runbox record."""

    record_id: str
    success: bool
    error_message: str | None = None


def create_runbox_record(
    argv: Sequence[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    tags: list[str] | None = None,
) -> RunboxRecordResult | None:
    """Create a runbox record capturing the current execution context.

    This function is called BEFORE program execution to capture:
    - Full command line (argv)
    - Working directory
    - Git state (commit and uncommitted changes)
    - Environment variables (optional subset)
    - Timestamp

    Args:
        argv: Full command line arguments (typically sys.argv)
        cwd: Working directory (defaults to os.getcwd())
        env: Environment variables to capture (optional)
        tags: Optional tags for the record

    Returns:
        RunboxRecordResult with record_id if successful, None if runbox unavailable
    """
    if not is_runbox_available():
        return None

    if cwd is None:
        cwd = os.getcwd()

    # Build the record JSON
    record: dict = {
        "command": {
            "argv": list(argv),
            "cwd": cwd,
        },
        "source": "doeff",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # Add environment if provided
    if env:
        record["command"]["env"] = env

    # Add git state
    commit = get_head_commit()
    if commit:
        git_state: dict = {"commit": commit}

        # Add repo URL if available
        repo_url = get_repo_url()
        if repo_url:
            git_state["repo_url"] = repo_url

        # Add diff if working tree is dirty
        diff = get_uncommitted_diff()
        if diff:
            git_state["diff"] = diff

        record["git_state"] = git_state

    # Add tags
    if tags:
        record["tags"] = tags

    # Create the record via runbox CLI
    try:
        result = subprocess.run(
            ["runbox", "create", "record"],
            input=json.dumps(record),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        if result.returncode == 0:
            # Parse record ID from runbox output (format: "Created record: rec_xxx\n...")
            output = result.stdout.strip()
            first_line = output.split("\n")[0]
            # Extract record ID from "Created record: rec_xxx" format
            if first_line.startswith("Created record: "):
                record_id = first_line.replace("Created record: ", "").strip()
            else:
                record_id = first_line  # Fallback to full first line
            return RunboxRecordResult(record_id=record_id, success=True)
        error_msg = result.stderr.strip() or "Unknown error"
        return RunboxRecordResult(
            record_id="",
            success=False,
            error_message=error_msg,
        )

    except subprocess.TimeoutExpired:
        return RunboxRecordResult(
            record_id="",
            success=False,
            error_message="Timeout creating runbox record",
        )
    except (FileNotFoundError, OSError) as exc:
        return RunboxRecordResult(
            record_id="",
            success=False,
            error_message=str(exc),
        )


def log_runbox_record(result: RunboxRecordResult) -> None:
    """Log runbox record creation result to stderr.

    Args:
        result: The result from create_runbox_record
    """
    if result.success:
        print(f"[runbox] Record stored: {result.record_id}", file=sys.stderr)
        print(f"[runbox] Replay with: runbox replay {result.record_id}", file=sys.stderr)
    else:
        print(f"[runbox] Warning: Failed to create record: {result.error_message}", file=sys.stderr)


def maybe_create_runbox_record(
    argv: Sequence[str] | None = None,
    *,
    skip_runbox: bool = False,
    tags: list[str] | None = None,
) -> str | None:
    """Convenience function to optionally create a runbox record.

    Args:
        argv: Command line arguments (defaults to sys.argv)
        skip_runbox: If True, skip runbox integration entirely
        tags: Optional tags for the record

    Returns:
        Record ID if created successfully, None otherwise
    """
    if skip_runbox:
        return None

    if not is_runbox_available():
        return None

    if argv is None:
        argv = sys.argv

    result = create_runbox_record(argv, tags=tags)
    if result:
        log_runbox_record(result)
        return result.record_id if result.success else None

    return None
