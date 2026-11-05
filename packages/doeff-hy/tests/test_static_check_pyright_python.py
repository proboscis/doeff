"""Failure case for the python that doeff-hy-check (run_pyright in doeff_hy._static_check) passes to pyright (daily doeff 2026-10-11 16:00).

pyright launches the --pythonpath python with `-I` (= -E -- does not read PYTHON* environment variables) to ask for the search path, so the caller's
PYTHONDONTWRITEBYTECODE=1 had no effect, and site startup wrote 6 .pyc files of modules the .pth reads (the .venv _virtualenv and __editable___doeff_vm_finder, and
the 4 in packages/doeff-hy/src/doeff_hy_bytecode_guard) into the checkout. In a run where a check going through run_pyright (*_static_types in doeff-cluster and
unknown_in_users in doeff_hy._static_stub) ran, the root conftest's cleanup check ERRORed.

- The python that run_pyright makes pyright launch is the caller-supplied python launched with `-B` (which works even under -I).
"""

import contextlib
import io
import shlex
import shutil
import sys
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # enables the Hy import hook

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not installed")


def _recording_python(tmp_path: Path, log: Path) -> Path:
    """A python that writes the arguments it was launched with to a log, one per line, and then launches this process's python with the same arguments (a watcher on the python that pyright launches)."""
    python = tmp_path / "recording-python"
    python.write_text(f'#!/bin/sh\necho "$@" >> {shlex.quote(str(log))}\nexec {shlex.quote(sys.executable)} -B "$@"\n')
    python.chmod(0o755)
    return python


@needs_pyright
def test_the_python_pyright_starts_for_doeff_hy_check_writes_no_bytecode(tmp_path: Path) -> None:
    from doeff_hy._static_check import main

    root = tmp_path / "repo"
    root.mkdir()
    (root / "probe.hy").write_text("(setv answer 1)\n", encoding="utf-8")
    log = tmp_path / "started.log"
    python = _recording_python(tmp_path, log)
    with contextlib.redirect_stdout(io.StringIO()):
        main(["--root", str(root), "--json", "--no-cache", "--python", str(python), str(root / "probe.hy")])
    started = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert started, "pyright never launched the --pythonpath python (the watcher is not working)"
    assert all(line.split()[:1] == ["-B"] for line in started), started
