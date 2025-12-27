"""
Run v0 - Reproducible execution records.

This module provides data models and utilities for creating reproducible
execution records. A Run captures exactly what is needed to reproduce
a command execution:

- exec: What to execute and how (argv, cwd, env, timeout)
- code_state: Which code state to use (repo, commit, optional patch)

Design principles:
- Purity: Run only contains execution info, no metadata
- Reproducibility: All fields are resolved/determined

Example:
    >>> from doeff.run_record import Run, create_run
    >>> run = create_run(
    ...     argv=["python", "-m", "mymodule"],
    ...     cwd=".",
    ...     repo_url="git@github.com:org/repo.git",
    ...     base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
    ... )
    >>> run.to_dict()
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "Run",
    "Exec",
    "CodeState",
    "Patch",
    "create_run",
    "generate_run_id",
    "validate_run",
    "ValidationError",
    "create_patch",
    "apply_patch",
    "reproduce_run",
]

# ULID constants
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_run_id() -> str:
    """Generate a unique run_id in ULID format with run_ prefix.

    Returns:
        A string like "run_01JFXYZ1234567890ABCDEF" (run_ + 26 ULID chars).
    """
    # ULID: 10 chars for timestamp (48 bits), 16 chars for randomness (80 bits)
    timestamp_ms = int(time.time() * 1000)

    # Encode timestamp (6 bytes = 48 bits -> 10 base32 chars)
    timestamp_chars = []
    for _ in range(10):
        timestamp_chars.append(_ULID_ALPHABET[timestamp_ms & 0x1F])
        timestamp_ms >>= 5
    timestamp_part = "".join(reversed(timestamp_chars))

    # Random part (10 bytes = 80 bits -> 16 base32 chars)
    random_bytes = random.getrandbits(80)
    random_chars = []
    for _ in range(16):
        random_chars.append(_ULID_ALPHABET[random_bytes & 0x1F])
        random_bytes >>= 5
    random_part = "".join(reversed(random_chars))

    return f"run_{timestamp_part}{random_part}"


class ValidationError(Exception):
    """Error raised when Run validation fails."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Validation failed: {'; '.join(errors)}")


@dataclass(frozen=True)
class Patch:
    """Reference to a patch stored as a git blob.

    Attributes:
        ref: Git ref in refs/patches/ namespace (e.g., "refs/patches/run_...")
        sha256: SHA-256 hash of patch content (64 hex chars)
    """

    ref: str
    sha256: str

    def __post_init__(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError(errors)

    def validate(self) -> list[str]:
        """Validate patch fields."""
        errors: list[str] = []
        if not self.ref.startswith("refs/patches/"):
            errors.append(f"ref must start with 'refs/patches/', got '{self.ref}'")
        if not re.match(r"^[a-f0-9]{64}$", self.sha256):
            errors.append(f"sha256 must be 64 hex chars, got '{self.sha256}'")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {"ref": self.ref, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Patch:
        """Create from dictionary."""
        return cls(ref=data["ref"], sha256=data["sha256"])


@dataclass(frozen=True)
class CodeState:
    """Code state for reproduction.

    Attributes:
        repo_url: Clone-able repository URL
        base_commit: Full commit SHA (40 characters)
        patch: Optional patch for uncommitted changes
    """

    repo_url: str
    base_commit: str
    patch: Patch | None = None

    def __post_init__(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError(errors)

    def validate(self) -> list[str]:
        """Validate code state fields."""
        errors: list[str] = []
        if not self.repo_url:
            errors.append("repo_url cannot be empty")
        if not re.match(r"^[a-f0-9]{40}$", self.base_commit):
            errors.append(f"base_commit must be 40 hex chars, got '{self.base_commit}'")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "repo_url": self.repo_url,
            "base_commit": self.base_commit,
        }
        if self.patch is not None:
            result["patch"] = self.patch.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeState:
        """Create from dictionary."""
        patch = Patch.from_dict(data["patch"]) if "patch" in data else None
        return cls(
            repo_url=data["repo_url"],
            base_commit=data["base_commit"],
            patch=patch,
        )


@dataclass(frozen=True)
class Exec:
    """Execution parameters.

    Attributes:
        argv: Command and arguments (non-empty, fully resolved)
        cwd: Working directory relative to repo root
        env: Environment variables (all string values)
        timeout_sec: Execution timeout in seconds (0 = unlimited)
    """

    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    timeout_sec: int = 0

    def __post_init__(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError(errors)

    def validate(self) -> list[str]:
        """Validate exec fields."""
        errors: list[str] = []
        if not self.argv:
            errors.append("argv cannot be empty")
        for i, arg in enumerate(self.argv):
            if "{" in arg or "}" in arg:
                errors.append(f"argv[{i}] contains template variable: '{arg}'")
        if self.timeout_sec < 0:
            errors.append(f"timeout_sec must be >= 0, got {self.timeout_sec}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "argv": list(self.argv),
            "cwd": self.cwd,
            "env": dict(self.env),
            "timeout_sec": self.timeout_sec,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Exec:
        """Create from dictionary."""
        return cls(
            argv=tuple(data["argv"]),
            cwd=data["cwd"],
            env=data.get("env", {}),
            timeout_sec=data.get("timeout_sec", 0),
        )


@dataclass(frozen=True)
class Run:
    """Reproducible execution record.

    A Run captures exactly what is needed to reproduce a command execution.
    All fields are resolved/determined - no template variables or interactive input.

    Attributes:
        run_version: Schema version (always 0 for v0)
        run_id: Unique identifier in ULID format with run_ prefix
        exec: Execution parameters
        code_state: Code state for reproduction
    """

    run_version: int
    run_id: str
    exec: Exec
    code_state: CodeState

    def __post_init__(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError(errors)

    def validate(self) -> list[str]:
        """Validate run fields."""
        errors: list[str] = []
        if self.run_version != 0:
            errors.append(f"run_version must be 0, got {self.run_version}")
        if not re.match(r"^run_[0-9A-Z]{26}$", self.run_id):
            errors.append(f"run_id must match 'run_' + 26 ULID chars, got '{self.run_id}'")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_version": self.run_version,
            "run_id": self.run_id,
            "exec": self.exec.to_dict(),
            "code_state": self.code_state.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Run:
        """Create Run from dictionary (e.g., from JSON)."""
        return cls(
            run_version=data["run_version"],
            run_id=data["run_id"],
            exec=Exec.from_dict(data["exec"]),
            code_state=CodeState.from_dict(data["code_state"]),
        )

    def to_json(self, indent: int | None = 2) -> str:
        """Convert to JSON string."""
        import json

        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> Run:
        """Create Run from JSON string."""
        import json

        return cls.from_dict(json.loads(json_str))


def validate_run(data: dict[str, Any]) -> list[str]:
    """Validate a Run dictionary without creating the object.

    Returns:
        List of validation errors (empty if valid).
    """
    errors: list[str] = []

    # Check required fields
    for req in ["run_version", "run_id", "exec", "code_state"]:
        if req not in data:
            errors.append(f"missing required field: {req}")

    if errors:
        return errors

    # Validate run_version
    if data["run_version"] != 0:
        errors.append(f"run_version must be 0, got {data['run_version']}")

    # Validate run_id
    if not re.match(r"^run_[0-9A-Z]{26}$", data.get("run_id", "")):
        errors.append(f"run_id must match 'run_' + 26 ULID chars, got '{data.get('run_id', '')}'")

    # Validate exec
    exec_data = data.get("exec", {})
    if not exec_data.get("argv"):
        errors.append("exec.argv cannot be empty")
    for i, arg in enumerate(exec_data.get("argv", [])):
        if "{" in str(arg) or "}" in str(arg):
            errors.append(f"exec.argv[{i}] contains template variable: '{arg}'")
    if exec_data.get("timeout_sec", 0) < 0:
        errors.append(f"exec.timeout_sec must be >= 0, got {exec_data.get('timeout_sec')}")

    # Validate code_state
    code_state = data.get("code_state", {})
    if not code_state.get("repo_url"):
        errors.append("code_state.repo_url cannot be empty")
    if not re.match(r"^[a-f0-9]{40}$", code_state.get("base_commit", "")):
        errors.append(
            f"code_state.base_commit must be 40 hex chars, got '{code_state.get('base_commit', '')}'"
        )

    # Validate patch if present
    if "patch" in code_state:
        patch = code_state["patch"]
        if not patch.get("ref", "").startswith("refs/patches/"):
            errors.append(f"code_state.patch.ref must start with 'refs/patches/', got '{patch.get('ref', '')}'")
        if not re.match(r"^[a-f0-9]{64}$", patch.get("sha256", "")):
            errors.append(f"code_state.patch.sha256 must be 64 hex chars, got '{patch.get('sha256', '')}'")

    return errors


def create_run(
    argv: list[str] | tuple[str, ...],
    cwd: str,
    repo_url: str,
    base_commit: str,
    *,
    env: dict[str, str] | None = None,
    timeout_sec: int = 0,
    patch: Patch | None = None,
    run_id: str | None = None,
) -> Run:
    """Create a new Run record.

    Args:
        argv: Command and arguments
        cwd: Working directory relative to repo root
        repo_url: Clone-able repository URL
        base_commit: Full commit SHA (40 characters)
        env: Optional environment variables
        timeout_sec: Optional timeout in seconds (default 0 = unlimited)
        patch: Optional patch for uncommitted changes
        run_id: Optional run_id (auto-generated if not provided)

    Returns:
        A new Run record.
    """
    return Run(
        run_version=0,
        run_id=run_id or generate_run_id(),
        exec=Exec(
            argv=tuple(argv),
            cwd=cwd,
            env=env or {},
            timeout_sec=timeout_sec,
        ),
        code_state=CodeState(
            repo_url=repo_url,
            base_commit=base_commit,
            patch=patch,
        ),
    )


def create_patch(repo_path: Path | str, run_id: str) -> Patch | None:
    """Create a patch from uncommitted changes in a git repository.

    This stores the diff as a git blob and returns a Patch reference.

    Args:
        repo_path: Path to the git repository
        run_id: The run_id to use for the patch ref

    Returns:
        A Patch object if there are uncommitted changes, None otherwise.
    """
    repo_path = Path(repo_path)

    # Get the diff of uncommitted changes
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    diff_content = result.stdout
    if not diff_content.strip():
        # No uncommitted changes
        return None

    # Calculate SHA-256 of the patch content
    sha256_hash = hashlib.sha256(diff_content.encode()).hexdigest()

    # Store the diff as a git blob
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
        f.write(diff_content)
        temp_path = f.name

    try:
        # Hash the object
        result = subprocess.run(
            ["git", "hash-object", "-w", temp_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        blob_sha = result.stdout.strip()

        # Create the ref
        ref = f"refs/patches/{run_id}"
        subprocess.run(
            ["git", "update-ref", ref, blob_sha],
            cwd=repo_path,
            check=True,
        )
    finally:
        os.unlink(temp_path)

    return Patch(ref=ref, sha256=sha256_hash)


def apply_patch(repo_path: Path | str, patch: Patch) -> None:
    """Apply a patch from a git ref.

    Args:
        repo_path: Path to the git repository
        patch: The Patch to apply
    """
    repo_path = Path(repo_path)

    # Fetch the ref if needed
    subprocess.run(
        ["git", "fetch", "origin", patch.ref],
        cwd=repo_path,
        capture_output=True,
    )

    # Get the patch content from the blob
    result = subprocess.run(
        ["git", "cat-file", "-p", patch.ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    patch_content = result.stdout

    # Verify SHA-256
    actual_sha256 = hashlib.sha256(patch_content.encode()).hexdigest()
    if actual_sha256 != patch.sha256:
        raise ValueError(
            f"Patch SHA-256 mismatch: expected {patch.sha256}, got {actual_sha256}"
        )

    # Apply the patch
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
        f.write(patch_content)
        temp_path = f.name

    try:
        subprocess.run(
            ["git", "apply", temp_path],
            cwd=repo_path,
            check=True,
        )
    finally:
        os.unlink(temp_path)


def reproduce_run(run: Run, work_dir: Path | str) -> subprocess.CompletedProcess[str]:
    """Reproduce a run in a working directory.

    This clones the repository, checks out the base commit, applies any patch,
    and executes the command.

    Args:
        run: The Run to reproduce
        work_dir: Directory to use for the reproduction

    Returns:
        The result of the executed command.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Clone the repository
    subprocess.run(
        ["git", "clone", run.code_state.repo_url, str(work_dir)],
        check=True,
    )

    # Checkout the base commit
    subprocess.run(
        ["git", "checkout", run.code_state.base_commit],
        cwd=work_dir,
        check=True,
    )

    # Apply patch if present
    if run.code_state.patch:
        apply_patch(work_dir, run.code_state.patch)

    # Execute the command
    exec_cwd = work_dir / run.exec.cwd
    timeout = run.exec.timeout_sec if run.exec.timeout_sec > 0 else None

    env = os.environ.copy()
    env.update(run.exec.env)

    return subprocess.run(
        list(run.exec.argv),
        cwd=exec_cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
