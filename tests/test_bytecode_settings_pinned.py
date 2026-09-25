"""Every test run has one bytecode setting — no writes, standard location (root conftest.py).

A developer's run and the daily run must give the same answer; the daily run sets
PYTHONDONTWRITEBYTECODE=1, so the suite pins both of Python's bytecode settings for test
bodies, fixtures, and the subprocesses they start.

A test can still give a subprocess a bytecode directory of its own inside the checkout, which
the settings do not see; the land tool keeps a checkout's ignored files from one candidate to
the next, so that bytecode would be read by later runs.  The run therefore fails at its end
when bytecode files were written under the checkout during it (agora-redesign#639).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from tests._root_conftest_runs import REPO_ROOT, run_under_root_conftest


def _root_conftest() -> ModuleType:
    """Import the ROOT conftest as a module (it is not importable by name)."""
    spec = importlib.util.spec_from_file_location("doeff_root_conftest", REPO_ROOT / "conftest.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _file_with_mtime(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    os.utime(path, (mtime, mtime))


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


def test_bytecode_written_during_the_run_is_named_and_older_bytecode_is_not(
    tmp_path: Path,
) -> None:
    started = 1_000_000.0
    _file_with_mtime(tmp_path / "pkg/__pycache__/new.cpython-314.pyc", started + 1)
    _file_with_mtime(tmp_path / "pkg/__pycache__/old.cpython-314.pyc", started - 60)
    _file_with_mtime(tmp_path / ".git/objects/in-git.pyc", started + 1)
    _file_with_mtime(tmp_path / "pkg/written.py", started + 1)

    written = _root_conftest().bytecode_written_since(tmp_path, started)

    assert written == ["pkg/__pycache__/new.cpython-314.pyc"]


SUBPROCESS_WITH_ITS_BYTECODE_IN_THE_CHECKOUT = """
import os
import subprocess
import sys
from pathlib import Path


def test_a_subprocess_keeps_its_bytecode_inside_the_checkout():
    env = {name: value for name, value in os.environ.items() if name != "PYTHONDONTWRITEBYTECODE"}
    env["PYTHONPYCACHEPREFIX"] = str(Path("in-tree-cache").resolve())
    subprocess.run([sys.executable, "-c", "import csv, json"], env=env, check=True)
"""


def test_a_run_that_writes_bytecode_into_the_checkout_fails(tmp_path: Path) -> None:
    # The inner run's checkout is tmp_path (where the copy of the root conftest sits), so the
    # bytecode it is made to write never reaches this repository's tree.
    result = run_under_root_conftest(
        tmp_path, SUBPROCESS_WITH_ITS_BYTECODE_IN_THE_CHECKOUT, budget=50.0
    )

    assert result.returncode != 0, result.stdout
    assert "bytecode file(s) were written under the checkout during this run" in result.stdout
    assert "in-tree-cache/" in result.stdout
