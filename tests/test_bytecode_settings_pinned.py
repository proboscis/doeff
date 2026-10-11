"""Every test run has one bytecode setting — no writes, standard location (root conftest.py).

A developer's run and the daily run must give the same answer; the daily run sets
PYTHONDONTWRITEBYTECODE=1, so the suite pins both of Python's bytecode settings for test
bodies, fixtures, and the subprocesses they start.

A test can still give a subprocess a bytecode directory of its own inside the checkout, which
the settings do not see; the land tool keeps a checkout's ignored files from one candidate to
the next, so that bytecode would be read by later runs.  The run therefore fails at its end
when bytecode files were written under the checkout during it (agora-redesign#639).

A test whose subprocesses need bytecode asks for the ``subprocess_bytecode`` fixture: its
subprocesses write and reuse bytecode in one directory of the run under pytest's temporary
directory, while the test body keeps the pinned settings (agora-redesign#2818).

A subprocess started with an environment of its own (EnvMode.REPLACE) does not get the pin, and
its first act is to write the bytecode of the interpreter's startup modules into the checkout.
The run clears that bytecode when its tests start and looks for it after every test, so the
test that wrote it is the one named — on a developer's tree, where an earlier run left the same
bytecode in place, as well as in the daily run (card ki-23c3280da1a5).
"""

from __future__ import annotations

import importlib.util
import os
import py_compile
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

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
    # The environment variables behind these settings (PYTHONDONTWRITEBYTECODE=1, no PYTHONPYCACHEPREFIX)
    # are observed where they act — in a subprocess — by the next test.
    assert sys.dont_write_bytecode is True
    assert sys.pycache_prefix is None


def test_subprocesses_inherit_the_pinned_bytecode_settings() -> None:
    child = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.dont_write_bytecode, sys.pycache_prefix)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.split() == ["True", "None"]


def test_a_test_that_asks_for_subprocess_bytecode_hands_its_subprocesses_this_runs_directory(
    subprocess_bytecode: Path,
) -> None:
    child = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.dont_write_bytecode, sys.pycache_prefix)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert child.stdout.split() == ["False", str(subprocess_bytecode)]
    assert (sys.dont_write_bytecode, sys.pycache_prefix) == (True, None), (
        "this process keeps the pin"
    )


def test_subprocess_bytecode_lands_in_the_runs_directory_and_not_beside_the_source(
    subprocess_bytecode: Path, tmp_path: Path
) -> None:
    (tmp_path / "probe_module.py").write_text("VALUE = 1\n")

    subprocess.run([sys.executable, "-c", "import probe_module"], cwd=tmp_path, check=True)

    assert list(tmp_path.rglob("*.pyc")) == []
    assert [path.name for path in subprocess_bytecode.rglob("probe_module.*.pyc")] == [
        f"probe_module.{sys.implementation.cache_tag}.pyc"
    ]


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


# The inner checkout's startup module is a sitecustomize on PYTHONPATH (the inner run's own interpreter is this
# repository's .venv, whose startup modules are the outer run's to watch).  The children start Python the way a
# child with an environment of its own (EnvMode.REPLACE) does: only the allowed names, so the conftest's
# PYTHONDONTWRITEBYTECODE=1 is not among them, and no -B.  -S keeps a child off this repository's .venv startup
# (it would write the outer run's startup bytecode); the child imports the inner startup module itself.
WRITERS_AMONG_BYSTANDERS = """
import os
import subprocess
import sys

import pytest


def start_python_with_an_environment_of_its_own():
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": os.environ["PYTHONPATH"]}
    subprocess.run([sys.executable, "-S", "-c", "import sitecustomize"], env=env, check=True)


def test_starts_python_with_an_environment_of_its_own():
    start_python_with_an_environment_of_its_own()


def test_runs_after_it():
    pass


@pytest.fixture(scope="class")
def python_started_for_the_class():
    start_python_with_an_environment_of_its_own()


class TestUnderAWiderFixture:
    def test_first(self, python_started_for_the_class):
        pass

    def test_last_of_the_session(self, python_started_for_the_class):
        pass
"""


@pytest.mark.parametrize(
    "tree_holds_startup_bytecode",
    [
        pytest.param(True, id="developer-tree"),  # an earlier run without the pin left it in place
        pytest.param(False, id="fresh-tree"),  # the daily run: a new checkout and .venv under the pin
    ],
)
def test_the_test_that_writes_startup_bytecode_is_named_not_the_last_test(
    tmp_path: Path, tree_holds_startup_bytecode: bool
) -> None:
    startup = tmp_path / "startup"
    startup.mkdir()
    source = startup / "sitecustomize.py"
    source.write_text("")
    if tree_holds_startup_bytecode:
        compiled = py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)))
        an_hour_ago = time.time() - 3600
        os.utime(compiled, (an_hour_ago, an_hour_ago))

    result = run_under_root_conftest(
        tmp_path, WRITERS_AMONG_BYSTANDERS, env={"PYTHONPATH": str(startup)}, budget=50.0
    )

    named = [line.split()[1] for line in result.stdout.splitlines() if line.startswith("ERROR ")]
    assert named == [
        "test_probe.py::test_starts_python_with_an_environment_of_its_own",
        "test_probe.py::TestUnderAWiderFixture::test_first",
    ], result.stdout
    assert f"startup/__pycache__/sitecustomize.{sys.implementation.cache_tag}.pyc" in result.stdout
    assert list(startup.rglob("*.pyc")) == [], "the run leaves the tree without the bytecode it named"
