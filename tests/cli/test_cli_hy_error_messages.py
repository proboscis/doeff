"""PR C: --hy + legacy flag combos must fail hard with a full example.

PR B already rejects the combination, but the original message was a
one-liner. PR C replaces it with a deliberately long error that shows
the user exactly how to rewrite their command into the Hy-native form.
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


class TestHyInterpreterConflict:
    def test_shows_rewrite_example(self):
        result = run_cli(
            "--hy",
            "(import doeff [Pure]) (Pure 1)",
            "--interpreter",
            "myapp.sim_interpreter",
        )
        assert result.returncode != 0
        msg = result.stderr + result.stdout
        # Must name the conflicting flags.
        assert "--hy" in msg
        assert "--interpreter" in msg
        # Must offer a complete, runnable replacement.
        assert "doeff run --hy" in msg
        # The example should demonstrate importing the handler inline.
        assert "(import" in msg
        # Must point at the design doc / migration notes.
        assert "migration" in msg.lower() or "see" in msg.lower()


class TestHyEnvConflict:
    def test_env_rewrite_example(self):
        result = run_cli(
            "--hy",
            "(Pure 1)",
            "--env",
            "myapp.env_dict",
        )
        assert result.returncode != 0
        msg = result.stderr + result.stdout
        assert "--env" in msg
        # Example shows lazy-ask composition for an env dict.
        assert "lazy-ask" in msg or "lazy_ask" in msg
        # And DOEFF_* env var alternative for secrets.
        assert "DOEFF_" in msg


class TestHySetConflict:
    def test_set_rewrite_shows_local_or_env_var(self):
        result = run_cli("--hy", "(Pure 1)", "--set", "model=gpt-4")
        assert result.returncode != 0
        msg = result.stderr + result.stdout
        assert "--set" in msg
        # Two alternatives should be shown.
        assert "Local" in msg
        assert "DOEFF_" in msg


class TestHyApplyTransformConflict:
    def test_apply_rewrite_shows_inline_composition(self):
        result = run_cli(
            "--hy",
            "(Pure 1)",
            "--apply",
            "myapp.transforms.double",
        )
        assert result.returncode != 0
        msg = result.stderr + result.stdout
        assert "--apply" in msg
        # Inline Hy composition via ->  or direct function call.
        assert "->" in msg or "(myapp" in msg or "import" in msg

    def test_transform_rewrite_shows_inline_composition(self):
        result = run_cli(
            "--hy",
            "(Pure 1)",
            "--transform",
            "myapp.transforms.memoize",
        )
        assert result.returncode != 0
        msg = result.stderr + result.stdout
        assert "--transform" in msg
        assert "->" in msg or "import" in msg
