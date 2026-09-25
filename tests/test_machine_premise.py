"""Machine premises — root conftest ``machine_tool`` (agora-redesign #639, 2026-09-26).

A test whose tool does not start on this machine is skipped and named as "not
executed" in the check layer's own line format (dotfiles agentcli remote_check);
a tool that starts and then fails is red.  Every run below puts a stand-in
executable on PATH — the real codex is never started.

Counterexample that motivated this (daily run 2026-09-26): the pod had the
router's entry point for ``codex`` on PATH with no binary behind it, so
``shutil.which`` said "present" and a test that calls a real model went red
with exit 127 — a fact about the machine, reported as a fault in the code.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_CONFTEST = REPO_ROOT / "conftest.py"
CHECK_LAYER_UNDER_HOME = Path("dotfiles") / "agentcli" / "src" / "agentcli" / "remote_check.py"
REAL_CHECK_LAYER = Path.home() / CHECK_LAYER_UNDER_HOME

CODEX_FILE = "packages/doeff-conductor/tests/test_agent_effect_c1.py"
CODEX_TEST = f"{CODEX_FILE}::test_real_codex_worker_returns_schema_valid_json_through_agent"

ROUTER_WITHOUT_BINARY = (
    "#!/bin/sh\necho 'codex: router entry point, no binary behind it' >&2\nexit 127\n"
)
STARTS_THEN_ANSWERS_WRONG = (
    '#!/bin/sh\nif [ "$1" = --version ]; then echo "codex stand-in 0.0"; exit 0; fi\n'
    "echo 'not json'\n"
)
STARTS_AND_ANSWERS = (
    '#!/bin/sh\nif [ "$1" = --version ]; then echo "codex stand-in 0.0"; exit 0; fi\n'
    "echo '{\"ok\": true}'\n"
)

SAMPLE_TEST = """
import json
import subprocess


def test_tool_answers_json(machine_tool):
    codex = machine_tool("codex")
    answer = subprocess.run([codex, "exec"], capture_output=True, text=True, check=True)
    assert json.loads(answer.stdout) == {"ok": True}
"""
SAMPLE_NODEID = "test_premise.py::test_tool_answers_json"


@pytest.fixture(scope="module")
def check_layer() -> ModuleType:
    """The real check layer — the only reader of its own line format (never a copy)."""
    if not REAL_CHECK_LAYER.exists():
        pytest.skip(f"machine premise unmet: no check layer at {REAL_CHECK_LAYER}")
    spec = importlib.util.spec_from_file_location("doeff_test_check_layer", REAL_CHECK_LAYER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass reads sys.modules
    spec.loader.exec_module(module)
    return module


def _stand_in_bin(directory: Path, codex: str | None) -> Path:
    """A PATH directory holding only the stand-in ``codex`` (or nothing, when None)."""
    directory.mkdir(parents=True, exist_ok=True)
    if codex is not None:
        tool = directory / "codex"
        tool.write_text(codex, encoding="utf-8")
        tool.chmod(0o755)
    return directory


def _home_with_check_layer(home: Path) -> Path:
    """A HOME whose dotfiles holds the real check layer, as on a machine of the fleet."""
    layer = home / CHECK_LAYER_UNDER_HOME
    layer.parent.mkdir(parents=True, exist_ok=True)
    layer.symlink_to(REAL_CHECK_LAYER)
    return home


def _run_pytest(
    tmp_path: Path, cwd: Path, home: Path, path_dir: Path, *args: str
) -> pytest.RunResult:
    """Run pytest in a new process that sees only the stand-in HOME and PATH.

    The stand-ins go to the child's environment alone: this process keeps its own
    PATH (its memory guard thread starts ``ps``).
    """
    started = time.monotonic()
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            f"--basetemp={tmp_path / 'inner-basetemp'}",
            *args,
        ],
        cwd=cwd,
        env={**os.environ, "HOME": str(home), "PATH": str(path_dir)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return pytest.RunResult(
        done.returncode,
        done.stdout.splitlines(),
        done.stderr.splitlines(),
        time.monotonic() - started,
    )


def _sample_machine(
    tmp_path: Path, *, codex: str | None, with_check_layer: bool
) -> pytest.RunResult:
    """Run a test that needs ``codex`` under the root conftest on a stand-in machine."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "conftest.py").write_text(ROOT_CONFTEST.read_text(encoding="utf-8"), "utf-8")
    (project / "test_premise.py").write_text(SAMPLE_TEST, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    if with_check_layer:
        _home_with_check_layer(home)
    path_dir = _stand_in_bin(tmp_path / "bin", codex)
    return _run_pytest(tmp_path, project, home, path_dir, "-q", "test_premise.py")


@pytest.mark.parametrize(
    ("codex", "said"),
    [(ROUTER_WITHOUT_BINARY, "exits 127"), (None, "is not on PATH")],
    ids=["router-without-binary", "not-on-path"],
)
def test_tool_that_does_not_start_is_skipped_and_named_not_executed(
    tmp_path: Path, check_layer: ModuleType, codex: str | None, said: str
) -> None:
    """The daily pod's shape: skipped, and one not-executed line the land tool can read."""
    result = _sample_machine(tmp_path, codex=codex, with_check_layer=True)

    result.assert_outcomes(skipped=1)
    assert result.ret == 0
    declarations = check_layer.parse_unexecuted_all(result.stdout.str())
    assert [declaration.kind for declaration in declarations] == ["tool-absent"]
    assert declarations[0].reason.startswith(f"{SAMPLE_NODEID}: codex")
    assert said in declarations[0].reason
    # The land tool must not retry this on the same machine: only another machine can answer.
    assert declarations[0].kind in check_layer.MACHINE_BOUND_UNEXECUTED_KINDS


def test_tool_that_starts_and_then_fails_is_red_and_not_declared(
    tmp_path: Path, check_layer: ModuleType
) -> None:
    """Only "does it start" is a premise: a wrong answer stays the test's own red."""
    result = _sample_machine(tmp_path, codex=STARTS_THEN_ANSWERS_WRONG, with_check_layer=True)

    result.assert_outcomes(failed=1)
    assert check_layer.parse_unexecuted_all(result.stdout.str()) == ()


def test_tool_that_starts_runs_the_test(tmp_path: Path, check_layer: ModuleType) -> None:
    """A machine that has the tool runs the test as usual and declares nothing."""
    result = _sample_machine(tmp_path, codex=STARTS_AND_ANSWERS, with_check_layer=True)

    result.assert_outcomes(passed=1)
    assert check_layer.parse_unexecuted_all(result.stdout.str()) == ()


def test_machine_without_the_check_layer_only_summarises_the_skip(
    tmp_path: Path, check_layer: ModuleType
) -> None:
    """A bare clone has no check layer: the skip is still named, in plain words only."""
    result = _sample_machine(tmp_path, codex=ROUTER_WITHOUT_BINARY, with_check_layer=False)

    result.assert_outcomes(skipped=1)
    assert result.ret == 0
    assert check_layer.parse_unexecuted_all(result.stdout.str()) == ()
    result.stdout.fnmatch_lines(
        [
            "1 test(s) skipped for a machine premise (no check layer here): "
            f"{SAMPLE_NODEID}: codex *exits 127*"
        ]
    )


def test_real_codex_worker_test_is_outside_the_daily_population(tmp_path: Path) -> None:
    """The daily selection (``-m 'not e2e'``) never reaches the test that calls a real model."""
    home = tmp_path / "home"
    home.mkdir()
    result = _run_pytest(
        tmp_path,
        REPO_ROOT,
        home,
        _stand_in_bin(tmp_path / "bin", ROUTER_WITHOUT_BINARY),
        "--collect-only",
        "-q",
        "-m",
        "not e2e",
        CODEX_FILE,
    )

    assert result.ret == 0, result.stdout.str() + result.stderr.str()
    assert f"{CODEX_FILE}::test_two_node_workflow_runs_on_scenario_stubs" in result.stdout.str()
    assert CODEX_TEST not in result.stdout.str()


def test_real_codex_worker_test_names_a_codex_that_does_not_start(
    tmp_path: Path, check_layer: ModuleType
) -> None:
    """In the e2e population, the real-codex test asks the machine premise, not ``which``."""
    result = _run_pytest(
        tmp_path,
        REPO_ROOT,
        _home_with_check_layer(tmp_path / "home"),
        _stand_in_bin(tmp_path / "bin", ROUTER_WITHOUT_BINARY),
        "-q",
        "-m",
        "e2e",
        CODEX_TEST,
    )

    assert result.ret == 0, result.stdout.str() + result.stderr.str()
    result.assert_outcomes(skipped=1)
    declarations = check_layer.parse_unexecuted_all(result.stdout.str())
    assert [declaration.kind for declaration in declarations] == ["tool-absent"]
    assert declarations[0].reason.startswith(f"{CODEX_TEST}: codex")
    assert "exits 127" in declarations[0].reason
