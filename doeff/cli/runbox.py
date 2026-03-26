"""Runbox CLI integration for automatic record capture.

When runbox CLI is installed, doeff run automatically captures execution
records before running programs, enabling reproducible execution via
`runbox replay <record-id>`.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def is_runbox_available() -> bool:
    return shutil.which("runbox") is not None


def get_head_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_uncommitted_diff() -> str | None:
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode == 0:
            diff = result.stdout.strip()
            return diff if diff else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_repo_url() -> str | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


@dataclass
class RunboxRecordResult:
    record_id: str
    success: bool
    error_message: str | None = None


def normalize_argv_for_replay(argv: Sequence[str]) -> list[str]:
    normalized = list(argv)
    if not normalized:
        return normalized
    argv0 = Path(normalized[0])
    if argv0.name == "__main__.py" and argv0.parent.name == "doeff":
        return [sys.executable, "-m", "doeff", *normalized[1:]]
    return normalized


def create_runbox_record(
    argv: Sequence[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    tags: list[str] | None = None,
) -> RunboxRecordResult | None:
    if not is_runbox_available():
        return None
    if cwd is None:
        cwd = os.getcwd()

    normalized_argv = normalize_argv_for_replay(argv)
    record: dict = {
        "command": {"argv": normalized_argv, "cwd": cwd},
        "source": "doeff",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if env:
        record["command"]["env"] = env

    commit = get_head_commit()
    if commit:
        git_state: dict = {"commit": commit}
        repo_url = get_repo_url()
        if repo_url:
            git_state["repo_url"] = repo_url
        diff = get_uncommitted_diff()
        if diff:
            git_state["diff"] = diff
        record["git_state"] = git_state

    if tags:
        record["tags"] = tags

    try:
        result = subprocess.run(
            ["runbox", "create", "record"],
            input=json.dumps(record),
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            first_line = output.split("\n")[0]
            if first_line.startswith("Created record: "):
                record_id = first_line.replace("Created record: ", "").strip()
            else:
                record_id = first_line
            return RunboxRecordResult(record_id=record_id, success=True)
        return RunboxRecordResult(
            record_id="", success=False,
            error_message=result.stderr.strip() or "Unknown error",
        )
    except subprocess.TimeoutExpired:
        return RunboxRecordResult(record_id="", success=False, error_message="Timeout")
    except (FileNotFoundError, OSError) as exc:
        return RunboxRecordResult(record_id="", success=False, error_message=str(exc))


def log_runbox_record(result: RunboxRecordResult) -> None:
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
    if skip_runbox or not is_runbox_available():
        return None
    if argv is None:
        argv = sys.argv
    result = create_runbox_record(argv, tags=tags)
    if result:
        log_runbox_record(result)
        return result.record_id if result.success else None
    return None
