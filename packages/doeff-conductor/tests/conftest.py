"""
Pytest configuration and fixtures for doeff-conductor tests.

This module provides:
- Custom markers for E2E and OpenCode-dependent tests
- Fixtures for test repositories and worktrees
- Detection functions for external dependencies
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from doeff_conductor.handlers import run_sync

if TYPE_CHECKING:
    from doeff_conductor.types import Issue


# =============================================================================
# Markers
# =============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "e2e: tests that exercise external integrations",
    )
    config.addinivalue_line(
        "markers",
        "requires_opencode: tests that require a running OpenCode server",
    )
    config.addinivalue_line(
        "markers",
        "slow: tests that take a long time to run",
    )


# =============================================================================
# Environment Detection
# =============================================================================


def is_opencode_available() -> bool:
    """Check if OpenCode server is available.

    Checks:
    1. CONDUCTOR_OPENCODE_URL environment variable
    2. Default OpenCode port (4096)
    3. opencode binary in PATH
    """
    # Check if URL is explicitly provided
    url = os.environ.get("CONDUCTOR_OPENCODE_URL")
    if url:
        try:
            import httpx

            resp = httpx.get(f"{url}/global/health", timeout=2.0)
            return resp.status_code == 200 and resp.json().get("healthy", False)
        except Exception:
            return False

    # Check default port
    try:
        import httpx

        resp = httpx.get("http://127.0.0.1:4096/global/health", timeout=2.0)
        return resp.status_code == 200 and resp.json().get("healthy", False)
    except Exception:
        pass

    # Check if opencode binary exists (we can auto-start)
    return shutil.which("opencode") is not None


def is_e2e_enabled() -> bool:
    """Check if E2E tests are enabled.

    E2E tests run when:
    - CONDUCTOR_E2E=1 environment variable is set
    - Running with -m e2e marker explicitly
    """
    return os.environ.get("CONDUCTOR_E2E", "0") == "1"


def is_git_available() -> bool:
    """Check if git is available."""
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# =============================================================================
# Skip decorators
# =============================================================================


skip_without_e2e = pytest.mark.skipif(
    not is_e2e_enabled(),
    reason="E2E tests disabled. Set CONDUCTOR_E2E=1 to enable.",
)


skip_without_opencode = pytest.mark.skipif(
    not is_opencode_available(),
    reason="OpenCode not available. Start OpenCode server or install opencode CLI.",
)


skip_without_git = pytest.mark.skipif(
    not is_git_available(),
    reason="git not available",
)


# =============================================================================
# Git Repository Fixtures
# =============================================================================


def init_test_repo(path: Path) -> None:
    """Initialize a test git repository with initial commit."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    # Create initial commit
    (path / "README.md").write_text("# Test Repository\n\nThis is a test repo for E2E tests.\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def test_repo(tmp_path: Path) -> Path:
    """Fixture providing a test git repository.

    Creates a temporary git repository with:
    - Initial commit
    - README.md file
    - Configured git user
    """
    if not is_git_available():
        pytest.skip("git not available")

    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    init_test_repo(repo_path)
    return repo_path


@pytest.fixture
def worktree_base(tmp_path: Path) -> Path:
    """Fixture providing a temporary directory for worktrees."""
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    return worktrees


@pytest.fixture
def issues_dir(tmp_path: Path) -> Path:
    """Fixture providing a temporary issues directory."""
    issues = tmp_path / "issues"
    issues.mkdir()
    return issues


# =============================================================================
# OpenCode Fixtures
# =============================================================================


@pytest.fixture
def opencode_url() -> str | None:
    """Get OpenCode server URL if available."""
    url = os.environ.get("CONDUCTOR_OPENCODE_URL")
    if url:
        return url

    if is_opencode_available():
        return "http://127.0.0.1:4096"

    return None


# =============================================================================
# Test Issue Fixtures
# =============================================================================


@pytest.fixture
def sample_issue() -> Issue:
    """Fixture providing a sample Issue for testing."""
    from doeff_conductor.types import Issue, IssueStatus

    return Issue(
        id="TEST-001",
        title="Test Feature Implementation",
        body="Implement a test feature that:\n1. Creates a file\n2. Adds content\n3. Returns success",
        status=IssueStatus.OPEN,
        labels=("feature", "test"),
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_issue_file(issues_dir: Path, sample_issue: Issue) -> Path:
    """Fixture that writes a sample issue to the issues directory."""
    issue_path = issues_dir / f"{sample_issue.id}.md"
    content = f"""# {sample_issue.title}

## Status
{sample_issue.status.value}

## Labels
{", ".join(sample_issue.labels)}

## Body
{sample_issue.body}
"""
    issue_path.write_text(content)
    return issue_path


# =============================================================================
# Auto-skip collection
# =============================================================================


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip tests based on markers and environment."""
    for item in items:
        # Skip requires_opencode tests if OpenCode not available
        if "requires_opencode" in item.keywords:
            if not is_opencode_available():
                item.add_marker(
                    pytest.mark.skip(reason="OpenCode not available. Start server or install CLI.")
                )

        # Skip e2e tests if not enabled (unless running with -m e2e)
        if "e2e" in item.keywords:
            if not is_e2e_enabled():
                # Check if running with explicit e2e marker
                markexpr = config.getoption("-m", default=None)
                if markexpr is None or "e2e" not in str(markexpr):
                    item.add_marker(
                        pytest.mark.skip(
                            reason="E2E tests disabled. Set CONDUCTOR_E2E=1 or use -m e2e"
                        )
                    )


__all__ = [
    # Helper functions
    "init_test_repo",
    "is_e2e_enabled",
    "is_git_available",
    # Detection functions
    "is_opencode_available",
    # CESK API compatibility
    "run_sync",
    # Skip decorators
    "skip_without_e2e",
    "skip_without_git",
    "skip_without_opencode",
]
