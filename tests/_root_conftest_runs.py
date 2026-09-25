"""Run a probe test file as its own pytest session under the repository's root conftest.

The root conftest.py owns machinery that only a whole session shows: the per-test deadline
method, the watchdog that ends a hung run, and the checks at the end of a session.  Its tests
therefore run an inner pytest on a probe file in a temporary directory, with a copy of the
root conftest.py beside the probe and the root pyproject.toml's pytest settings (``-c``), so
what is observed is the repository's own setup.

- ``--confcutdir`` is the temporary directory: with ``-c`` the default is the ini file's
  directory, and a probe outside it made pytest list every sibling of every parent directory
  from / down (about 120,000 entries under the macOS temporary directory — 20 s per run).
- Only pytest-timeout is loaded (no plugin autoloading), so a machine without bytecode does
  not compile unrelated plugins for every inner run.
- HOME is the temporary directory, so the inner run does not ask the agora toolchain to admit
  it (the root conftest's broad-run admission is not installed there).
- The copy of the root conftest sees the temporary directory as its checkout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Settings of the outer run that would change the inner run's deadlines.
_OUTER_SETTINGS = frozenset({"PYTEST_ADDOPTS", "PYTEST_TIMEOUT", "PYTEST_DEADLINE_SCALE_CAP"})


def run_under_root_conftest(
    directory: Path,
    probe_source: str,
    *,
    pytest_args: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    budget: float,
) -> subprocess.CompletedProcess[str]:
    """Run ``probe_source`` as test_probe.py in ``directory`` and return the finished process.

    The inner run gets ``budget`` seconds; a run still going then fails the calling test.
    """
    shutil.copyfile(REPO_ROOT / "conftest.py", directory / "conftest.py")
    (directory / "test_probe.py").write_text(probe_source, encoding="utf-8")
    inner_env = {name: value for name, value in os.environ.items() if name not in _OUTER_SETTINGS}
    inner_env |= {"HOME": str(directory), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    inner_env |= env or {}
    command = [
        sys.executable, "-m", "pytest",
        "-c", str(REPO_ROOT / "pyproject.toml"),
        "--rootdir", str(directory), "--confcutdir", str(directory),
        "-p", "pytest_timeout", "-p", "no:cacheprovider",
        "-q", *pytest_args, "test_probe.py",
    ]  # fmt: skip
    try:
        return subprocess.run(
            command,
            cwd=directory,
            env=inner_env,
            capture_output=True,
            text=True,
            timeout=budget,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        pytest.fail(
            f"the inner run did not end within {budget:.0f}s; output so far:\n{expired.stdout!r}"
        )
