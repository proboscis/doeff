"""PR C: old CLI flags emit deprecation warnings guiding users to the
Hy-native form. The flags keep working for Python-style programs, so the
warning goes to stderr and the command still exits 0.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.cli

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    command = ["uv", "run", "python", "-m", "doeff", "run", *args]
    env = {
        "PYTHONPATH": str(PROJECT_ROOT),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DOEFF_DISABLE_DEFAULT_ENV": "1",
    }
    for key in ("UV_PROJECT_ENVIRONMENT", "UV_CACHE_DIR", "VIRTUAL_ENV"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class TestDeprecationWarnings:
    def test_interpreter_warns(self):
        # We don't rely on the program actually running — just that the
        # deprecation notice surfaces before execution.
        result = run_cli(
            "--program",
            "some.missing.program",
            "--interpreter",
            "some.missing.interpreter",
            "--format",
            "json",
        )
        # Whether the underlying program resolves or not, the warning must
        # appear on stderr alongside the Hy-native replacement example.
        assert "deprecated" in result.stderr.lower()
        assert "--hy" in result.stderr
        assert "--interpreter" in result.stderr

    def test_set_warns(self):
        result = run_cli(
            "--program",
            "some.missing.program",
            "--set",
            "value=99",
            "--format",
            "json",
        )
        assert "deprecated" in result.stderr.lower()
        assert "--set" in result.stderr
        # The warning should mention Local or DOEFF_ as replacements.
        assert "Local" in result.stderr or "DOEFF_" in result.stderr


class TestNoDeprecationForNewForms:
    def test_hy_does_not_warn(self):
        result = run_cli("--hy", "(import doeff [Pure]) (Pure 42)", "--format", "json")
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "deprecated" not in result.stderr.lower()
