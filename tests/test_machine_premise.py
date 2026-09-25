"""Machine premises — root conftest ``machine_tool`` (agora-redesign #639, 2026-09-26).

A test whose tool does not start on this machine is skipped and named as "not
executed" in the check layer's own line format (dotfiles agentcli remote_check);
a tool that starts and then fails is red.  Every run below gives the child
process a PATH holding only a stand-in executable — the real codex is never
started, even on a machine that has one.

Counterexample that motivated this (daily run 2026-09-26): the pod had the
router's entry point for ``codex`` on PATH with no binary behind it, so
``shutil.which`` said "present" and a test that calls a real model went red
with exit 127 — a fact about the machine, reported as a fault in the code.

The check layer is read from this machine's dotfiles.  Where there is none (a
bare clone), the checks that would read the not-executed line back check the
plain summary line instead.
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

# Every stand-in counts its probes in $HOME/probes (the premise is probed once per session).
ROUTER_WITHOUT_BINARY = (
    "#!/bin/sh\n"
    'echo probe >> "$HOME/probes"\n'
    "echo 'codex router: looking for the binary' >&2\n"
    "echo 'codex: router entry point, no binary behind it' >&2\n"
    "exit 127\n"
)
STARTS_THEN_ANSWERS_WRONG = (
    "#!/bin/sh\n"
    'if [ "$1" = --version ]; then echo probe >> "$HOME/probes"; echo "codex 0.0"; exit 0; fi\n'
    "echo 'not json'\n"
)
STARTS_AND_ANSWERS = (
    "#!/bin/sh\n"
    'if [ "$1" = --version ]; then echo probe >> "$HOME/probes"; echo "codex 0.0"; exit 0; fi\n'
    "echo '{\"ok\": true}'\n"
)

SAMPLE_TEST = """
import json
import subprocess


def _answer(machine_tool):
    codex = machine_tool("codex")
    answer = subprocess.run([codex, "exec"], capture_output=True, text=True, check=True)
    return json.loads(answer.stdout)


def test_tool_answers_json(machine_tool):
    assert _answer(machine_tool) == {"ok": True}


def test_tool_answers_json_again(machine_tool):
    assert _answer(machine_tool) == {"ok": True}
"""
FIRST_NODEID = "test_premise.py::test_tool_answers_json"
SECOND_NODEID = "test_premise.py::test_tool_answers_json_again"
SUMMARY_WITHOUT_CHECK_LAYER = "skipped for a machine premise (no check layer here)"


@pytest.fixture(scope="module")
def check_layer() -> ModuleType | None:
    """The real check layer — the only reader of its own line format — or None on a bare clone."""
    if not REAL_CHECK_LAYER.exists():
        return None
    spec = importlib.util.spec_from_file_location("doeff_test_check_layer", REAL_CHECK_LAYER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass reads sys.modules
    spec.loader.exec_module(module)
    return module


def _stand_in_bin(directory: Path, codex: str | None, *, git: str | None = None) -> Path:
    """A PATH directory holding only the stand-in ``codex`` (or nothing, when None).

    ``git`` links this machine's git in as well, for the runs that ask for a checkout.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if codex is not None:
        tool = directory / "codex"
        tool.write_text(codex, encoding="utf-8")
        tool.chmod(0o755)
    if git is not None:
        (directory / "git").symlink_to(git)
    return directory


def _home(directory: Path, *, with_check_layer: bool) -> Path:
    """A HOME for the child; with the real check layer in its dotfiles when asked and present."""
    directory.mkdir(parents=True, exist_ok=True)
    if with_check_layer and REAL_CHECK_LAYER.exists():
        layer = directory / CHECK_LAYER_UNDER_HOME
        layer.parent.mkdir(parents=True, exist_ok=True)
        layer.symlink_to(REAL_CHECK_LAYER)
    return directory


def _probes(home: Path) -> int:
    """How many times the stand-in was probed (it appends one line per probe)."""
    counter = home / "probes"
    return len(counter.read_text(encoding="utf-8").splitlines()) if counter.exists() else 0


def _run_pytest(
    tmp_path: Path,
    cwd: Path,
    home: Path,
    path_dir: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> pytest.RunResult:
    """Run pytest in a new process that sees only the stand-in HOME and PATH (and ``env``).

    The stand-ins go to the child's environment alone: this process keeps its own
    PATH (its memory guard thread starts ``ps``), and a real codex further down a
    prepended PATH can never be reached.
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
        env={**os.environ, **(env or {}), "HOME": str(home), "PATH": str(path_dir)},
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
    tmp_path: Path,
    *,
    codex: str | None,
    with_check_layer: bool,
    sample: str = SAMPLE_TEST,
    git: str | None = None,
) -> tuple[pytest.RunResult, Path]:
    """Run ``sample`` (by default two tests that need ``codex``) under the root conftest."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "conftest.py").write_text(ROOT_CONFTEST.read_text(encoding="utf-8"), "utf-8")
    (project / "test_premise.py").write_text(sample, encoding="utf-8")
    home = _home(tmp_path / "home", with_check_layer=with_check_layer)
    path_dir = _stand_in_bin(tmp_path / "bin", codex, git=git)
    return _run_pytest(tmp_path, project, home, path_dir, "-q", "test_premise.py"), home


def _checkout_sample(checkout: Path, commit: str) -> str:
    """A test that needs ``checkout`` holding ``commit`` — the shape of the custody copy test."""
    return (
        "from pathlib import Path\n\n\n"
        "def test_reads_the_pinned_contract(machine_checkout):\n"
        f"    checkout = machine_checkout(Path({str(checkout)!r}), {commit!r})\n"
        '    assert (checkout / "contract.json").exists()\n'
    )


def _git_checkout(directory: Path, git: str) -> tuple[Path, str]:
    """A fresh git checkout with one commit; returns its path and the commit."""
    directory.mkdir(parents=True)
    quiet = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def run(*args: str) -> str:
        """Run git in the checkout without this machine's own git config or hooks."""
        return subprocess.run(
            [git, "-C", str(directory), *args],
            env=quiet,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    run("init", "-q")
    (directory / "contract.json").write_text("{}\n", encoding="utf-8")
    run("add", "contract.json")
    run("-c", "user.name=premise", "-c", "user.email=premise@example.invalid", "commit", "-qm", "c")
    return directory, run("rev-parse", "HEAD")


def _declarations(check_layer: ModuleType, result: pytest.RunResult) -> tuple:
    """The not-executed lines of a run, read by the check layer itself."""
    return check_layer.parse_unexecuted_all(result.stdout.str())


@pytest.mark.parametrize(
    ("codex", "said"),
    [
        (ROUTER_WITHOUT_BINARY, "exits 127: codex: router entry point, no binary behind it"),
        (None, "codex is not on PATH"),
    ],
    ids=["router-without-binary", "not-on-path"],
)
def test_tool_that_does_not_start_is_skipped_and_named_not_executed(
    tmp_path: Path, check_layer: ModuleType | None, codex: str | None, said: str
) -> None:
    """The daily pod's shape: both tests skipped, named on one not-executed line."""
    result, home = _sample_machine(tmp_path, codex=codex, with_check_layer=True)

    result.assert_outcomes(skipped=2)
    assert result.ret == 0
    assert _probes(home) == (1 if codex is not None else 0)
    assert "looking for the binary" not in result.stdout.str()  # the last line, not the first
    if check_layer is None:
        result.stdout.fnmatch_lines(
            [
                f"2 test(s) {SUMMARY_WITHOUT_CHECK_LAYER}: {FIRST_NODEID}: *{said}; {SECOND_NODEID}: *"
            ]
        )
        return
    declarations = _declarations(check_layer, result)
    assert [declaration.kind for declaration in declarations] == ["tool-absent"]
    assert declarations[0].reason.startswith(f"{FIRST_NODEID}: ")
    assert said in declarations[0].reason
    # The land tool must not retry this on the same machine: only another machine can answer.
    assert declarations[0].kind in check_layer.MACHINE_BOUND_UNEXECUTED_KINDS


def test_tool_that_starts_and_then_fails_is_red_and_not_declared(
    tmp_path: Path, check_layer: ModuleType | None
) -> None:
    """Only "does it start" is a premise: a wrong answer stays the test's own red."""
    result, home = _sample_machine(tmp_path, codex=STARTS_THEN_ANSWERS_WRONG, with_check_layer=True)

    result.assert_outcomes(failed=2)
    assert _probes(home) == 1
    assert SUMMARY_WITHOUT_CHECK_LAYER not in result.stdout.str()
    if check_layer is not None:
        assert _declarations(check_layer, result) == ()


def test_tool_that_starts_runs_the_test(tmp_path: Path, check_layer: ModuleType | None) -> None:
    """A machine that has the tool runs the tests as usual and declares nothing."""
    result, home = _sample_machine(tmp_path, codex=STARTS_AND_ANSWERS, with_check_layer=True)

    result.assert_outcomes(passed=2)
    assert _probes(home) == 1
    assert SUMMARY_WITHOUT_CHECK_LAYER not in result.stdout.str()
    if check_layer is not None:
        assert _declarations(check_layer, result) == ()


def test_machine_without_the_check_layer_only_summarises_the_skip(
    tmp_path: Path, check_layer: ModuleType | None
) -> None:
    """A bare clone has no check layer: the skips are still named, in one plain line."""
    result, _ = _sample_machine(tmp_path, codex=ROUTER_WITHOUT_BINARY, with_check_layer=False)

    result.assert_outcomes(skipped=2)
    assert result.ret == 0
    result.stdout.fnmatch_lines(
        [f"2 test(s) {SUMMARY_WITHOUT_CHECK_LAYER}: {FIRST_NODEID}: *; {SECOND_NODEID}: *"]
    )
    if check_layer is not None:
        assert _declarations(check_layer, result) == ()


def test_real_codex_worker_test_is_outside_the_daily_population(tmp_path: Path) -> None:
    """The daily selection (``-m 'not e2e'``) never reaches the test that calls a real model."""
    result = _run_pytest(
        tmp_path,
        REPO_ROOT,
        _home(tmp_path / "home", with_check_layer=False),
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
    tmp_path: Path, check_layer: ModuleType | None
) -> None:
    """In the e2e population, the real-codex test asks the machine premise, not ``which``."""
    result = _run_pytest(
        tmp_path,
        REPO_ROOT,
        _home(tmp_path / "home", with_check_layer=True),
        _stand_in_bin(tmp_path / "bin", ROUTER_WITHOUT_BINARY),
        "-q",
        "-m",
        "e2e",
        CODEX_TEST,
    )

    assert result.ret == 0, result.stdout.str() + result.stderr.str()
    result.assert_outcomes(skipped=1)
    if check_layer is None:
        result.stdout.fnmatch_lines([f"1 test(s) {SUMMARY_WITHOUT_CHECK_LAYER}: {CODEX_TEST}: *"])
        return
    declarations = _declarations(check_layer, result)
    assert [declaration.kind for declaration in declarations] == ["tool-absent"]
    assert declarations[0].reason.startswith(f"{CODEX_TEST}: codex")
    assert "exits 127: codex: router entry point" in declarations[0].reason


# --- A premise other than a tool: a git checkout holding a pinned commit (依頼書 K) ----------

CHECKOUT_NODEID = "test_premise.py::test_reads_the_pinned_contract"
CUSTODY_FILE = "packages/doeff-agents/tests/test_sessionhost_acp_custody_lender_copy.py"
CUSTODY_TEST = f"{CUSTODY_FILE}::test_the_copy_is_the_custody_contract_at_the_pinned_commit"


def _assert_declared(
    result: pytest.RunResult, check_layer: ModuleType | None, kind: str, nodeid: str, said: str
) -> None:
    """One test skipped and named not-executed under ``kind`` (or in the summary on a bare clone)."""
    if check_layer is None:
        result.stdout.fnmatch_lines(
            [f"1 test(s) {SUMMARY_WITHOUT_CHECK_LAYER}: {nodeid}: *{said}*"]
        )
        return
    declarations = _declarations(check_layer, result)
    assert [declaration.kind for declaration in declarations] == [kind]
    assert declarations[0].reason.startswith(f"{nodeid}: ")
    assert said in declarations[0].reason
    # Machine-bound: the land tool looks for another machine instead of retrying this one.
    assert declarations[0].kind in check_layer.MACHINE_BOUND_UNEXECUTED_KINDS


def test_missing_checkout_is_skipped_and_named_premise_unmet(
    tmp_path: Path, check_layer: ModuleType | None, machine_tool
) -> None:
    """zeus's shape: no custody checkout — skipped and named premise-unmet, not red."""
    missing = tmp_path / "no-checkout"
    result, _ = _sample_machine(
        tmp_path,
        codex=None,
        with_check_layer=True,
        sample=_checkout_sample(missing, "0" * 40),
        git=machine_tool("git"),
    )

    result.assert_outcomes(skipped=1)
    assert result.ret == 0
    _assert_declared(result, check_layer, "premise-unmet", CHECKOUT_NODEID, f"checkout {missing}")


def test_checkout_without_the_pinned_commit_is_premise_unmet(
    tmp_path: Path, check_layer: ModuleType | None, machine_tool
) -> None:
    """A checkout that has not fetched the pinned commit is a machine premise, not red."""
    git = machine_tool("git")
    checkout, _ = _git_checkout(tmp_path / "checkout", git)
    result, _ = _sample_machine(
        tmp_path,
        codex=None,
        with_check_layer=True,
        sample=_checkout_sample(checkout, "1" * 40),
        git=git,
    )

    result.assert_outcomes(skipped=1)
    assert result.ret == 0
    _assert_declared(
        result, check_layer, "premise-unmet", CHECKOUT_NODEID, f"does not hold commit {'1' * 40}"
    )


def test_checkout_holding_the_pinned_commit_runs_the_test(
    tmp_path: Path, check_layer: ModuleType | None, machine_tool
) -> None:
    """When the premise holds the test runs (and whatever it finds is its own answer)."""
    git = machine_tool("git")
    checkout, commit = _git_checkout(tmp_path / "checkout", git)
    result, _ = _sample_machine(
        tmp_path,
        codex=None,
        with_check_layer=True,
        sample=_checkout_sample(checkout, commit),
        git=git,
    )

    result.assert_outcomes(passed=1)
    assert SUMMARY_WITHOUT_CHECK_LAYER not in result.stdout.str()
    if check_layer is not None:
        assert _declarations(check_layer, result) == ()


def test_checkout_premise_on_a_machine_without_git_names_the_tool(
    tmp_path: Path, check_layer: ModuleType | None
) -> None:
    """git itself is a tool premise: without it the checkout cannot be read (tool-absent)."""
    result, _ = _sample_machine(
        tmp_path,
        codex=None,
        with_check_layer=True,
        sample=_checkout_sample(tmp_path / "checkout", "0" * 40),
    )

    result.assert_outcomes(skipped=1)
    _assert_declared(result, check_layer, "tool-absent", CHECKOUT_NODEID, "git is not on PATH")


def test_each_unmet_premise_is_named_under_its_own_word(
    tmp_path: Path, check_layer: ModuleType | None, machine_tool
) -> None:
    """A tool and a checkout unmet in one run: one not-executed line per check-layer word."""
    missing = tmp_path / "no-checkout"
    sample = (
        SAMPLE_TEST
        + "\n\n"
        + _checkout_sample(missing, "0" * 40).replace(
            "from pathlib import Path\n\n\n", "from pathlib import Path\n"
        )
    )
    result, _ = _sample_machine(
        tmp_path,
        codex=ROUTER_WITHOUT_BINARY,
        with_check_layer=True,
        sample=sample,
        git=machine_tool("git"),
    )

    result.assert_outcomes(skipped=3)
    assert result.ret == 0
    if check_layer is None:
        result.stdout.fnmatch_lines([f"3 test(s) {SUMMARY_WITHOUT_CHECK_LAYER}: *"])
        return
    declarations = {d.kind: d.reason for d in _declarations(check_layer, result)}
    assert sorted(declarations) == ["premise-unmet", "tool-absent"]
    assert declarations["tool-absent"].startswith(f"{FIRST_NODEID}: codex")
    assert CHECKOUT_NODEID not in declarations["tool-absent"]
    assert declarations["premise-unmet"] == f"{CHECKOUT_NODEID}: checkout {missing} is missing"


def test_custody_copy_test_names_a_machine_without_the_custody_checkout(
    tmp_path: Path, check_layer: ModuleType | None, machine_tool
) -> None:
    """The real custody copy test on a machine without the checkout: rc 0, skipped, premise-unmet."""
    missing = tmp_path / "no-custody"
    result = _run_pytest(
        tmp_path,
        REPO_ROOT,
        _home(tmp_path / "home", with_check_layer=True),
        _stand_in_bin(tmp_path / "bin", None, git=machine_tool("git")),
        "-q",
        "-m",
        "not e2e",
        CUSTODY_FILE,
        env={"CUSTODY_CHECKOUT": str(missing)},
    )

    assert result.ret == 0, result.stdout.str() + result.stderr.str()
    result.assert_outcomes(passed=6, skipped=1)
    _assert_declared(result, check_layer, "premise-unmet", CUSTODY_TEST, f"checkout {missing}")
