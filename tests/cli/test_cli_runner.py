"""PR C: --runner flag dispatches execution to a named backend.

The default is ``doeff.runners.local`` which behaves identically to the
legacy ``doeff run`` path — build a Program and ``run()`` it. Custom
runners receive a ``RunContext`` describing the original invocation so
remote backends (k3s, docker) can reconstruct the command inside a pod.
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


class TestLocalRunnerByDefault:
    def test_hy_source_runs_with_default_local_runner(self):
        result = run_cli("--hy", "(import doeff [Pure]) (Pure 42)", "--format", "json")
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert '"result": 42' in result.stdout


class TestExplicitLocalRunner:
    def test_runner_flag_accepts_builtin_local(self):
        result = run_cli(
            "--hy",
            "(import doeff [Pure]) (Pure 42)",
            "--runner",
            "doeff.runners.local.run_local",
            "--format",
            "json",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert '"result": 42' in result.stdout


class TestCustomRunner:
    def test_custom_runner_receives_run_context(self):
        result = run_cli(
            "--hy",
            "(import doeff [Pure]) (Pure 999)",
            "--runner",
            "tests.cli.cli_runner_assets.ctx_spy_runner",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # The runner writes a JSON blob of the RunContext to stdout.
        payload = result.stdout.strip().splitlines()[-1]
        import json
        ctx = json.loads(payload)
        assert ctx["hy_source"] == "(import doeff [Pure]) (Pure 999)"
        assert ctx["program_ref"] is None
        assert ctx["py_source"] is None
        assert ctx["runner_ref"] == "tests.cli.cli_runner_assets.ctx_spy_runner"
        assert "raw_argv" in ctx


class TestRunnerErrors:
    def test_unknown_runner_shows_example(self):
        result = run_cli(
            "--hy",
            "(Pure 1)",
            "--runner",
            "not.a.real.module.fn",
        )
        assert result.returncode != 0
        msg = result.stderr + result.stdout
        # The error should show a complete working example.
        assert "--runner" in msg
        assert "doeff.runners.local" in msg

    def test_non_callable_runner_fails_with_runner_guidance(self):
        result = run_cli(
            "--hy",
            "(Pure 1)",
            "--runner",
            "tests.cli.cli_runner_assets.NOT_A_RUNNER",
        )
        assert result.returncode != 0
        msg = result.stderr + result.stdout
        assert "failed to import --runner" in msg
        assert "is not callable" in msg
