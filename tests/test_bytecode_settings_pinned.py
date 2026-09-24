"""Every test run has one bytecode setting — no writes, standard location (root conftest.py).

A developer's run and the daily run must give the same answer; the daily run sets
PYTHONDONTWRITEBYTECODE=1, so the suite pins both of Python's bytecode settings for test
bodies, fixtures, and the subprocesses they start.
"""

from __future__ import annotations

import os
import subprocess
import sys


def test_test_bodies_run_with_the_pinned_bytecode_settings() -> None:
    assert sys.dont_write_bytecode is True
    assert sys.pycache_prefix is None
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PYTHONPYCACHEPREFIX" not in os.environ


def test_subprocesses_inherit_the_pinned_bytecode_settings() -> None:
    child = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.dont_write_bytecode, sys.pycache_prefix)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.split() == ["True", "None"]
