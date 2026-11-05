"""Failure cases for the pre-registration entry point scripts/run_changed_tests.py (`make test-changed`).

Shape of t135: a commit that adds one public name to a `.hy` of doeff-core-effects and does not fix the neighboring `.pyi` passes green on the test the writer chooses
(test_sql_effects.hy), and only the contract test test_hy_module_stubs turns red. The entry point selects that test from the contract set, so
it stops red; removing a table row means that test is not selected (the table is effective); tests over the limit and a work tree without a venv are named
"unmeasured" and kept apart from red; and a malformed table stops the run -- all verified with a temporary model repo.

The model repo holds copies of real files (test_hy_module_stubs.py, the .hy and .pyi of doeff_core_effects, the source of the executable ADR pattern
definition) and a pyproject.toml containing only the table. The model has no environment of its own, and UV_PROJECT_ENVIRONMENT of uv points at the current test
environment (the entry point runs pytest with `uv run --no-sync --project <work tree>`). When it does not point, it becomes a "work tree without an environment".

Second pass (reverse dependencies): the entry point asks doeff-linter's `--affected-tests`. The tests do not call the real linter but swap in a stand-in
command (`--linter`) that returns a fixed answer, and verify the order contract set -> changed tests -> reverse dependencies (by distance) and that when the linter does not answer, the run goes on after naming it.
Failure cases for the linter-side selection itself are held by the Rust tests of doeff-linter (project::affected_tests).

Session boundaries: the entry point runs each of the same boundaries as the daily run (repo root, `packages/<name>/tests`, PACKAGE_EXTRA_TEST_ROOTS of the Makefile)
in a separate pytest process. It verifies that selecting two packages that each have `__init__.py` and `conftest.py` under `tests/` at the same time does not stop the run
(a single pytest run collided on the name `tests.conftest` and stopped with rc 2), that the total limit is shared among bundles and bundles whose turn never came are named unmeasured,
and that the bundle order keeps the contract set first.

Waiting for the machine's test slot: the root conftest of doeff takes the machine's test slot in pytest_configure (before collection) and
waits if it is occupied. It verifies that the entry point does not count the configure interval against the limit and shows the uncounted seconds on a separate line of the summary.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from doeff_core_effects import EnvEntry, EnvMode, RunProcess, subprocess_handler

from doeff import run, with_handlers

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run_changed_tests.py"
STUB_TEST = "packages/doeff-core-effects/tests/test_hy_module_stubs.py"
CORE_EFFECTS = "packages/doeff-core-effects/doeff_core_effects"
ADR_PLUGIN = "packages/doeff-adr/src/doeff_adr/_pytest_plugin.py"
#: Git author of the model repo (added to the child's environment -- inheriting the current environment is done by the child-process responder).
GIT_ENV = (
    EnvEntry(name="GIT_AUTHOR_NAME", value="model"),
    EnvEntry(name="GIT_AUTHOR_EMAIL", value="model@example.invalid"),
    EnvEntry(name="GIT_COMMITTER_NAME", value="model"),
    EnvEntry(name="GIT_COMMITTER_EMAIL", value="model@example.invalid"),
)


@dataclass(frozen=True)
class Row:
    """One row of the model table (path prefix and list of tests)."""

    prefix: str
    tests: tuple[str, ...]


@dataclass(frozen=True)
class Model:
    """The model repo, the commit before the change (the entry point's branch point), and a reverse-dependency stand-in that returns an empty answer (does not call the real linter)."""

    root: Path
    base: str
    linter: Path


#: The empty form of a reverse-dependency answer (JSON of doeff-linter's `--affected-tests`).
EMPTY_ANSWER = '{"tests": [], "hubs": [], "not_modules": [], "unreadable": [], "files_read": 0, "max_dependents": 40}'


@dataclass(frozen=True)
class RunnerOutput:
    returncode: int
    text: str
    stdout: str


def _git(root: Path, *args: str) -> str:
    """Run git in the model repo (fixed author name). The child is started by doeff's child-process responder (subprocess_handler),
    which overlays GIT_ENV on the current environment."""
    outcome = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(argv=("git", "-C", str(root), *args), env=GIT_ENV, env_mode=EnvMode.EXTEND),
        )
    )
    assert outcome.exit_code == 0, f"git {args} exited with rc {outcome.exit_code}: {outcome.stderr}"
    return outcome.stdout.strip()


def _uv_dir(kind: str) -> str:
    """The uv locations (`uv cache dir` / `uv python dir`) -- so that a child with a changed HOME uses the same locations as now."""
    proc = subprocess.run(
        ["uv", kind, "dir"], capture_output=True, text=True, timeout=30, check=True
    )
    return proc.stdout.strip()


def _pyproject(rows: tuple[Row, ...], record_dirs: tuple[str, ...] = ()) -> str:
    """The model pyproject.toml -- only the pytest rootdir mark, the contract test table, and the design-record dirs (`[tool.doeff] record-dirs`)."""
    table = "".join(
        f'\n[[tool.doeff.contract-tests]]\nprefix = "{row.prefix}"\n'
        f"tests = [{', '.join(f'{t!r}' for t in row.tests)}]\n".replace("'", '"')
        for row in rows
    )
    records = (
        f"\n[tool.doeff]\nrecord-dirs = [{', '.join(json.dumps(d) for d in record_dirs)}]\n"
        if record_dirs
        else ""
    )
    return '[tool.pytest.ini_options]\nmarkers = ["e2e: model marker"]\n' + records + table


def _fake_linter(place: Path, answer: str, returncode: int = 0) -> Path:
    """The reverse-dependency stand-in: a command that writes the arguments it received, one per line, to an argv file in the same dir and returns a fixed answer (placed outside the model repo -- not counted as a changed file)."""
    place.mkdir(parents=True, exist_ok=True)
    answer_file = place / "answer.json"
    answer_file.write_text(answer, encoding="utf-8")
    script = place / "doeff-linter"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{place / "argv"}"\n'
        f'cat "{answer_file}"\n'
        "echo 'fake linter stderr' >&2\n"
        f"exit {returncode}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _make_model(
    tmp_path: Path,
    rows: tuple[Row, ...],
    extra: tuple[tuple[str, str], ...] = (),
    record_dirs: tuple[str, ...] = (),
) -> Model:
    """Build a model repo holding copies of the real contract test and the core-effects .hy / .pyi, and make the first commit the branch point.

    extra = files to include at the branch point (path from the root and content). The reverse-dependency stand-in returning an empty answer is placed outside the repo.
    record_dirs = design-record dirs of the model pyproject.
    """
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(
        REPO_ROOT / CORE_EFFECTS,
        root / CORE_EFFECTS,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # The Makefile is the source of definition where the entry point reads session boundaries (PACKAGE_EXTRA_TEST_ROOTS).
    for rel in (STUB_TEST, ADR_PLUGIN, "Makefile"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, root / rel)
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    (root / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(60)\n", encoding="utf-8"
    )
    for rel, text in extra:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / "pyproject.toml").write_text(_pyproject(rows, record_dirs), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return Model(
        root=root,
        base=_git(root, "rev-parse", "HEAD"),
        linter=_fake_linter(tmp_path / "linter-empty", EMPTY_ANSWER),
    )


def _add_public_name_without_stub(model: Model) -> None:
    """A diff of the t135 shape: add one public name to sql_effects.hy and commit without fixing sql_effects.pyi."""
    module = model.root / CORE_EFFECTS / "sql_effects.hy"
    module.write_text(
        module.read_text(encoding="utf-8") + "\n(val ADDED-WITHOUT-STUB 1)\n", encoding="utf-8"
    )
    _git(model.root, "commit", "-q", "-am", "add a public name without its stub")


def _run(
    model: Model,
    *extra: str,
    with_environment: bool = True,
    linter: Path | None = None,
    home: Path | None = None,
) -> RunnerOutput:
    """Run the entry point against the model repo with the same `uv run --script` as `make test-changed` (the branch point is the model's first commit).

    with_environment = whether to point at the current test environment (sys.prefix) as the model's environment with UV_PROJECT_ENVIRONMENT.
    linter = the reverse-dependency stand-in (default is the model's empty answer -- the real doeff-linter is not called).
    home = when given, `--linter` is not passed and HOME is this dir, going through the entry point's default lookup (the snapshot location).
    """
    # The child inherits this process's environment -- remove the two uv environment names (`env -u`), and point at the current environment only when with_environment.
    environment = [f"UV_PROJECT_ENVIRONMENT={sys.prefix}"] if with_environment else []
    # Even if HOME is changed, the uv cache and python locations are used as now (if the locations moved under an empty HOME, python would be searched for again).
    kept_uv = [
        f"{name}={value}"
        for name, value in (
            ("UV_CACHE_DIR", _uv_dir("cache")),
            ("UV_PYTHON_INSTALL_DIR", _uv_dir("python")),
        )
    ]
    proc = subprocess.run(
        [
            "env",
            "-u",
            "UV_PROJECT_ENVIRONMENT",
            "-u",
            "VIRTUAL_ENV",
            *environment,
            *([f"HOME={home}", *kept_uv] if home is not None else []),
            "uv",
            "run",
            "--script",
            str(RUNNER),
            "--repo",
            str(model.root),
            "--base",
            model.base,
            *(
                []
                if home is not None
                else ["--linter", str(linter if linter is not None else model.linter)]
            ),
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return RunnerOutput(
        returncode=proc.returncode, text=proc.stdout + proc.stderr, stdout=proc.stdout
    )


def _counts(output: RunnerOutput) -> str:
    """The last line of the entry point's output -- a summary of counts only. Whatever way it ends, exactly one such line appears as the last line."""
    lines = [line for line in output.stdout.splitlines() if line.strip()]
    assert lines, f"the entry point printed nothing (rc {output.returncode})\n{output.text[-3000:]}"
    assert lines[-1].startswith("land-focus-counts: "), lines[-5:]
    assert sum(1 for line in lines if line.startswith("land-focus-counts:")) == 1, lines
    return lines[-1]


def _summary(output: RunnerOutput) -> str:
    """The last summary section of the entry point's output (excluding pytest output)."""
    marker = "== Changed-tests summary"
    assert marker in output.text, f"no summary (rc {output.returncode})\n{output.text[-3000:]}"
    return output.text[output.text.index(marker) :]


CORE_ROW = Row(prefix="packages/doeff-core-effects/", tests=(STUB_TEST,))
REPO_ROW = Row(prefix="", tests=("tests/test_ok.py",))


# The selected stub test (test_hy_module_stubs.py of doeff-core-effects) runs from Python 3.12, which can read the syntax of arguments of stub types.
@pytest.mark.skipif(sys.version_info < (3, 12), reason="the selected stub test needs Python 3.12 or later")
def test_a_hy_name_without_its_stub_is_red_through_the_contract_set(tmp_path: Path) -> None:
    """Failure case (t135): a diff that adds a public name to .hy without fixing .pyi is red with rc 1 in the stub test selected from the contract set."""
    model = _make_model(tmp_path, (CORE_ROW, REPO_ROW))
    _add_public_name_without_stub(model)
    output = _run(model)
    summary = _summary(output)
    assert output.returncode == 1, f"expected red but rc {output.returncode}\n{output.text[-3000:]}"
    assert "\nred: 1" in summary, summary
    assert f"{STUB_TEST} — red" in summary, summary
    assert "[sql_effects]" in summary, summary
    assert "宣言に無い公開の名: ADDED_WITHOUT_STUB" in output.text, (
        "the added name does not appear in the pytest output"
    )
    assert "tests/test_ok.py — 1 test(s)" in summary, summary
    assert "running on working tree contents (not the index)" in output.text.splitlines()[0], output.text[
        :500
    ]


def test_the_stub_check_is_not_selected_without_its_table_row(tmp_path: Path) -> None:
    """Verifies the table is effective: removing the core-effects row means the stub test is not selected for the same diff, and it does not go red."""
    model = _make_model(tmp_path, (REPO_ROW,))
    _add_public_name_without_stub(model)
    output = _run(model)
    summary = _summary(output)
    assert STUB_TEST not in output.text, summary
    assert output.returncode == 0, output.text[-3000:]
    assert "ran: 1" in summary, summary
    assert "\nred: 0" in summary, summary


def test_a_set_over_the_budget_is_named_unmeasured_not_red(tmp_path: Path) -> None:
    """A set over the limit (8 seconds here): finished tests are "ran", unfinished tests are named "unmeasured", and rc is 0."""
    model = _make_model(
        tmp_path, (Row(prefix="", tests=("tests/test_ok.py", "tests/test_slow.py")),)
    )
    (model.root / "README").write_text("changed\n", encoding="utf-8")
    output = _run(model, "--budget", "8")
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "tests/test_ok.py — 1 test(s)" in summary, summary
    assert "unmeasured: 1" in summary, summary
    assert "tests/test_slow.py — cut off at the 8s budget" in summary, summary
    assert "\nred: 0" in summary, summary
    counts = _counts(output)
    assert " selected=2 ran=1 red=0 unmeasured=1 unmeasured_reverse=0 rc=0 " in counts, counts
    assert counts.endswith(" budget=8"), counts


def test_a_worktree_without_an_environment_names_every_selected_test_unmeasured(
    tmp_path: Path,
) -> None:
    """A work tree without a uv environment (venv): it does not pass silently and names all selected tests "unmeasured" (rc 0)."""
    model = _make_model(tmp_path, (CORE_ROW, REPO_ROW))
    _add_public_name_without_stub(model)
    output = _run(model, with_environment=False)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "unmeasured: 2" in summary, summary
    assert "uv environment" in summary, summary
    assert "not found" in summary, summary
    assert STUB_TEST in summary, summary
    assert "tests/test_ok.py" in summary, summary


def test_a_table_naming_a_missing_test_file_stops(tmp_path: Path) -> None:
    """If a test file listed in the table is not in the tree, stop (rc 2) -- do not silently turn a mistyped row into an empty set."""
    model = _make_model(tmp_path, (Row(prefix="", tests=("tests/test_gone.py",)),))
    output = _run(model)
    assert output.returncode == 2, output.text[-3000:]
    assert "tests/test_gone.py" in output.text, output.text
    # A run that stopped before selection also prints one count summary line (0 selected tests, rc 2) -- so that stopped runs are not missing from the daily counts.
    assert " selected=0 ran=0 red=0 unmeasured=0 unmeasured_reverse=0 rc=2 " in _counts(output)


def test_the_repository_table_reads_and_names_existing_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real table has the right shape and every test file it lists is in the tree (the reader is the same function as the entry point)."""
    spec = importlib.util.spec_from_file_location("run_changed_tests", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    # A dataclass looks up its defining module in sys.modules -- place it only for the duration of the test.
    monkeypatch.setitem(sys.modules, spec.name, runner)
    spec.loader.exec_module(runner)
    table = runner.read_contract_table(REPO_ROOT)
    assert table.rows, "the real table is empty"
    assert all(
        (REPO_ROOT / test.split("::", 1)[0]).is_file() for row in table.rows for test in row.tests
    )


# ---------------------------------------------------------------------------
# Second pass: append the reverse-dependency tests (doeff-linter's `--affected-tests`) after the contract set and changed tests, in order of distance
# ---------------------------------------------------------------------------

PASSING = "def test_it():\n    pass\n"


def _affected(path: str, distance: int) -> dict[str, object]:
    """One of the tests in the stand-in's answer (a route of two steps from the changed file to the test is enough)."""
    return {"path": path, "distance": distance, "via": ["tests/test_changed.py", path]}


def test_reverse_dependents_follow_the_contract_set_and_the_changed_test_by_distance(
    tmp_path: Path,
) -> None:
    """Failure case for ordering: contract set -> changed tests -> reverse dependencies (nearer distance first; same distance in path order). Even if the stand-in's answer is out of order,
    it removes overlaps (tests already in the contract set), things that are not test files, and things not in the tree, orders them in the fixed order, and names modules that were too broad.
    The linter receives the changed files and the naming rule of test files (the same value as the collector)."""
    model = _make_model(
        tmp_path,
        (REPO_ROW,),
        extra=(
            ("tests/test_near_a.py", PASSING),
            ("tests/test_near_b.py", PASSING),
            ("tests/test_far.py", PASSING),
            ("tests/helper.py", "X = 1\n"),
        ),
    )
    (model.root / "tests" / "test_changed.py").write_text(PASSING, encoding="utf-8")
    answer = {
        "tests": [
            _affected("tests/test_far.py", 2),
            _affected("tests/test_near_b.py", 1),
            _affected("tests/test_ok.py", 1),
            _affected("tests/helper.py", 1),
            _affected("tests/test_gone.py", 1),
            _affected("tests/test_near_a.py", 1),
        ],
        "hubs": [{"module": "macros", "root": "src", "dependents": 412, "distance": 0}],
        "not_modules": [],
        "unreadable": [],
        "files_read": 9,
        "max_dependents": 40,
    }
    linter = _fake_linter(tmp_path / "linter-answer", json.dumps(answer))
    output = _run(model, linter=linter)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "ran: 5" in summary, summary
    ran = [line.strip().split(" — ", 1)[0] for line in summary.splitlines() if " — 1 test(s)" in line]
    assert ran == [
        "tests/test_ok.py",
        "tests/test_changed.py",
        "tests/test_near_a.py",
        "tests/test_near_b.py",
        "tests/test_far.py",
    ], summary
    assert (
        "tests/test_far.py — 1 test(s) (reverse dependency, distance 2: tests/test_changed.py → tests/test_far.py)"
        in summary
    )
    assert "Reverse dependencies: added 3 test(s)" in summary, summary
    assert "module too widely depended on to trace: macros (root src, direct dependents 412)" in summary, (
        summary
    )
    argv = (linter.parent / "argv").read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--affected-tests") + 1] == "tests/test_changed.py", argv
    patterns = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--test-pattern"]
    assert patterns[:2] == ["test_*.py", "*_test.py"], argv
    assert "docs/adr/defadr_*.hy" in patterns, argv


@pytest.mark.parametrize(
    ("record_dirs", "selected"),
    [(("docs/design/",), False), ((), True)],
    ids=["record-dir-declared", "no-declaration-control"],
)
def test_a_test_named_file_under_a_record_dir_is_selected_neither_as_changed_nor_by_reverse_dependency(
    tmp_path: Path, record_dirs: tuple[str, ...], selected: bool
) -> None:
    """Failure case: a test-named file under a design-record dir (`[tool.doeff] record-dirs` in pyproject) is not selected whether changed or listed in the reverse-dependency answer.
    In the control with no declaration, the same file is selected, fails at collection and goes red (a measurement script in a record that cannot be renamed went
    red because of a change in what it imports -- blind A of design verification). Test-named files outside the records are selected as before regardless of the declaration."""
    record = "docs/design/lt-REC/evidence/probe_test.py"
    model = _make_model(
        tmp_path,
        (REPO_ROW,),
        extra=((record, "raise RuntimeError('collect fails')\n"), ("tests/test_outside.py", PASSING)),
        record_dirs=record_dirs,
    )
    (model.root / record).write_text("raise RuntimeError('collect fails')\n# changed\n", encoding="utf-8")
    (model.root / "tests" / "test_changed.py").write_text(PASSING, encoding="utf-8")
    answer = {
        "tests": [_affected(record, 1), _affected("tests/test_outside.py", 1)],
        "hubs": [],
        "not_modules": [],
        "unreadable": [],
        "files_read": 4,
        "max_dependents": 40,
    }
    linter = _fake_linter(tmp_path / "linter-answer", json.dumps(answer))
    output = _run(model, linter=linter)
    summary = _summary(output)
    assert ("probe_test.py" in summary) is selected, summary
    assert (output.returncode == 1) is selected, output.text[-3000:]
    assert "tests/test_outside.py — 1 test(s) (reverse dependency" in summary, summary
    assert "tests/test_changed.py — 1 test(s) (changed test" in summary, summary


@pytest.mark.parametrize(
    ("case", "answer", "returncode", "named"),
    [
        ("absent", None, 0, "not found"),
        ("stopped", EMPTY_ANSWER, 2, "stopped with rc 2 — fake linter stderr"),
        ("unreadable", "not json", 0, "linter answer unreadable"),
        ("wrong-shape", '{"tests": [{"path": 1}], "hubs": [], "unreadable": []}', 0, "unreadable"),
    ],
)
def test_an_unanswering_linter_is_named_and_the_run_goes_on(
    tmp_path: Path, case: str, answer: str | None, returncode: int, named: str
) -> None:
    """When the linter does not answer: do not add reverse dependencies, name "could not measure reverse dependencies" with the reason, run the contract set, and do not go red (rc 0)."""
    model = _make_model(tmp_path, (REPO_ROW,))
    (model.root / "README").write_text("changed\n", encoding="utf-8")
    linter = (
        tmp_path / "no-such-linter"
        if answer is None
        else _fake_linter(tmp_path / f"linter-{case}", answer, returncode)
    )
    output = _run(model, linter=linter)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "tests/test_ok.py — 1 test(s)" in summary, summary
    assert "\nred: 0" in summary, summary
    assert "Reverse dependencies could not be measured — " in summary, summary
    assert named in summary, summary


# ---------------------------------------------------------------------------
# How the linter command is decided: the conversation Pod has no doeff-linter on PATH and has it only in the snapshot location prepared for the commit hook
# -- the default lookup finds the linter keyed in that location
# ---------------------------------------------------------------------------

LOCKED_SCRIPT = REPO_ROOT / "scripts" / "doeff_linter_locked.py"


def _model_with_linter_crate(tmp_path: Path) -> Model:
    """A model holding only the mark of the linter crate (packages/doeff-linter/Cargo.toml) -- the key of the build input can be computed."""
    return _make_model(
        tmp_path,
        (REPO_ROW,),
        extra=(
            (
                "packages/doeff-linter/Cargo.toml",
                '[package]\nname = "doeff-linter"\nversion = "0.0.0"\n',
            ),
            ("tests/test_near.py", PASSING),
        ),
    )


def _input_key(model: Model) -> str:
    """The key of the model HEAD's build input (the entry of the source of definition doeff_linter_locked.py, `key`)."""
    proc = subprocess.run(
        [sys.executable, str(LOCKED_SCRIPT), "key"],
        cwd=model.root,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout.strip()


def _snapshot(home: Path, sha: str, key: str, answer: str) -> Path:
    """Put the stand-in linter in the snapshot location under HOME (~/.cache/doeff-linter-snapshots/<40-digit sha>/doeff-linter, and the key input_key written when it was built)."""

    place = home / ".cache" / "doeff-linter-snapshots" / sha
    linter = _fake_linter(place, answer)
    (place / "input_key").write_text(key + "\n", encoding="utf-8")
    return linter


SNAPSHOT_SHA = "a" * 40
OTHER_SHA = "b" * 40


def test_the_default_linter_is_the_snapshot_locked_to_the_worktrees_build_inputs(
    tmp_path: Path,
) -> None:
    """Default failure case: even without passing `--linter` and with no linter on PATH, if a linter keyed with the work tree's build-input key exists in the snapshot location,
    the entry point asks it for reverse dependencies and adds the answered tests (before the change, reverse dependencies could not be measured because "linter 'doeff-linter' not found")."""
    model = _model_with_linter_crate(tmp_path)
    (model.root / "README").write_text("changed\n", encoding="utf-8")
    home = tmp_path / "home"
    answer = {
        "tests": [_affected("tests/test_near.py", 1)],
        "hubs": [],
        "not_modules": [],
        "unreadable": [],
        "files_read": 1,
        "max_dependents": 40,
    }
    linter = _snapshot(home, SNAPSHOT_SHA, _input_key(model), json.dumps(answer))
    output = _run(model, home=home)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "Reverse dependencies could not be measured" not in summary, summary
    assert "tests/test_near.py — 1 test(s) (reverse dependency, distance 1" in summary, summary
    argv = (linter.parent / "argv").read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--affected-tests") + 1] == "README", argv


def test_an_explicit_linter_wins_over_the_snapshot(runner: ModuleType, tmp_path: Path) -> None:
    """A command passed with `--linter` is used even if a linter with a matching key exists in the location (do not silently replace what was passed)."""
    model = _model_with_linter_crate(tmp_path)
    home = tmp_path / "home"
    _snapshot(home, SNAPSHOT_SHA, _input_key(model), EMPTY_ANSWER)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(home))
        picked = runner.pick_linter("/explicit/doeff-linter", model.root)
    assert picked.command == "/explicit/doeff-linter"
    assert "the command passed with --linter" in picked.absent


def test_a_snapshot_with_another_key_is_not_used_and_the_search_is_named(
    runner: ModuleType, tmp_path: Path
) -> None:
    """A linter with a different key is not used (do not query silently with another version) -- it falls back to doeff-linter on PATH, and when absent the reason carries the searched location and
    the work tree's key."""
    model = _model_with_linter_crate(tmp_path)
    home = tmp_path / "home"
    _snapshot(home, OTHER_SHA, "doeff-linter build input " + "0" * 40, EMPTY_ANSWER)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(home))
        picked = runner.pick_linter(None, model.root)
    assert picked.command == runner.DEFAULT_LINTER
    assert _input_key(model) in picked.absent, picked.absent
    assert str(home / ".cache" / "doeff-linter-snapshots") in picked.absent, picked.absent
    assert "is not on PATH either" in picked.absent, picked.absent


def test_a_worktree_without_the_linter_crate_falls_back_to_path_and_names_it(
    runner: ModuleType, tmp_path: Path
) -> None:
    """A work tree without the linter crate (the key cannot be computed) does not look for a keyed linter, falls back to doeff-linter on PATH, and names the reason."""
    model = _make_model(tmp_path, (REPO_ROW,))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(tmp_path / "home"))
        picked = runner.pick_linter(None, model.root)
    assert picked.command == runner.DEFAULT_LINTER
    assert "linter not found — " in picked.absent, picked.absent


# ---------------------------------------------------------------------------
# A separate pytest process for each of the same session boundaries as the daily run
# ---------------------------------------------------------------------------


def _package_tests(name: str) -> tuple[tuple[str, str], ...]:
    """One package with both `__init__.py` and `conftest.py` in `tests/` (the shape of claude-code, cluster, conductor, and records in doeff).
    Mixing two packages of this shape into one pytest session makes both conftest files take the name `tests.conftest` and collide."""
    base = f"packages/{name}/tests"
    return (
        (f"{base}/__init__.py", ""),
        (
            f"{base}/conftest.py",
            f"import pytest\n\n\n@pytest.fixture\ndef owner():\n    return {name!r}\n",
        ),
        (f"{base}/test_owner.py", f"def test_owner(owner):\n    assert owner == {name!r}\n"),
    )


def _touch_test(model: Model, rel: str) -> None:
    """Add one line to a model test file (making that file a "changed test")."""
    path = model.root / rel
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")


ALPHA = "packages/doeff-alpha/tests/test_owner.py"
BETA = "packages/doeff-beta/tests/test_owner.py"


def test_tests_of_two_packages_with_their_own_conftest_both_run(tmp_path: Path) -> None:
    """Failure case (the shape of 6 of the 12 commits in the sample of #2658): a change that selects tests of two packages with `tests/__init__.py` and `conftest.py` at the same time.
    Passing them to one pytest run collided on the name `tests.conftest`, and the entry point stopped without running a single test (rc 2). Processes are split at the same
    session boundaries as the daily run, so everything runs in 3 processes in the order contract set (repo root) -> the 2 packages."""
    model = _make_model(
        tmp_path, (REPO_ROW,), extra=(*_package_tests("doeff-alpha"), *_package_tests("doeff-beta"))
    )
    _touch_test(model, ALPHA)
    _touch_test(model, BETA)
    output = _run(model)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "ImportPathMismatchError" not in output.text, output.text[-3000:]
    assert "ran: 3" in summary, summary
    assert f"{ALPHA} — 1 test(s) (changed test" in summary, summary
    assert f"{BETA} — 1 test(s) (changed test" in summary, summary
    assert (
        "pytest processes: 3/3 ran (sessions: ., packages/doeff-alpha/tests, packages/doeff-beta/tests)"
        in summary
    ), summary


def test_a_batch_left_without_budget_is_named_unmeasured(tmp_path: Path) -> None:
    """The total limit is shared among bundles: when the first contract-set bundle (repo root) uses up the limit (6 seconds here), the later package
    bundles do not start pytest and name their tests "unmeasured because their turn never came" (not red, rc 0)."""
    model = _make_model(
        tmp_path,
        (Row(prefix="", tests=("tests/test_slow.py",)),),
        extra=_package_tests("doeff-alpha"),
    )
    _touch_test(model, ALPHA)
    output = _run(model, "--budget", "6")
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "unmeasured: 2" in summary, summary
    assert "tests/test_slow.py — cut off at the 6s budget" in summary, summary
    assert f"{ALPHA} — did not get a turn within the 6s budget" in summary, summary
    assert "pytest processes: 1/2 ran" in summary, summary


#: Stand-in seconds for waiting while the slot is occupied -- longer than the limit (8 seconds), so an entry point that counts the wait against the limit always cuts off before collecting bundles.
SLOT_WAIT_SECONDS = 10


def _slot_wait_conftest(seconds: int) -> str:
    """The model root conftest -- at the beat where doeff's root conftest takes the machine's test slot (pytest_configure, before collection), wait `seconds` seconds as a stand-in
    for the wait while the slot is occupied."""
    return f"import time\n\n\ndef pytest_configure(config):\n    time.sleep({seconds})\n"


def test_the_wait_for_a_machine_check_slot_is_not_counted_in_the_budget(tmp_path: Path) -> None:
    """Failure case (the shape where a 19-second slot wait under load 70 cut off the root bundle): the slot is occupied and the root
    conftest's pytest_configure waits 10 seconds, longer than the limit (8 seconds here). An entry point that counted the wait against the limit cut off before collecting bundles and
    made the tests unmeasured. The entry point does not count the configure interval, so the bundle runs to the end with the remainder of the limit after the wait and goes green, and the uncounted seconds appear
    on a line separate from the elapsed time."""
    model = _make_model(
        tmp_path,
        (REPO_ROW,),
        extra=(("conftest.py", _slot_wait_conftest(SLOT_WAIT_SECONDS)),),
    )
    (model.root / "README").write_text("changed\n", encoding="utf-8")
    output = _run(model, "--budget", "8")
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "tests/test_ok.py — 1 test(s)" in summary, summary
    assert "unmeasured: 0" in summary, summary
    counted = re.search(r"elapsed ([0-9.]+)s / budget 8s", summary)
    assert counted is not None, summary
    assert float(counted.group(1)) < 8, summary
    waited = re.search(
        re.escape("Configure time not counted against the budget (includes waiting for the machine's test slot): total ")
        + r"([0-9.]+)"
        + re.escape("s (session . "),
        summary,
    )
    assert waited is not None, summary
    assert float(waited.group(1)) >= SLOT_WAIT_SECONDS, summary
    counts = _counts(output)
    assert " selected=1 ran=1 red=0 unmeasured=0 unmeasured_reverse=0 rc=0 " in counts, counts


#: Stand-in seconds for a test that stops at collection (waits at the top of the module) -- between the limit (6 seconds) and the 120 seconds of _run. For an entry point that does not count collection,
#: the entry point does not cut the child before these seconds have passed.
SLOW_COLLECTION_SECONDS = 40


def test_a_collection_over_the_budget_is_cut_not_excluded(tmp_path: Path) -> None:
    """Failure case (blind A of design verification): the only thing not counted against the limit is the configure interval (the slot wait whose limit is held by the slot's source of truth).
    Since the uncounted interval is also one that the entry point does not cut by time, an entry point that did not count even collection could not cut a child stopped at collection and
    kept waiting past the limit. A test that stops for 40 seconds at the top of the module is cut off at the limit of 6 seconds and becomes the unmeasured "did not finish collecting",
    and the collection seconds do not go into the uncounted seconds (the wait line)."""
    model = _make_model(
        tmp_path,
        (Row(prefix="", tests=("tests/test_slow_collect.py",)),),
        extra=(
            (
                "tests/test_slow_collect.py",
                f"import time\n\ntime.sleep({SLOW_COLLECTION_SECONDS})\n\n\ndef test_never():\n    pass\n",
            ),
        ),
    )
    (model.root / "README").write_text("changed\n", encoding="utf-8")
    output = _run(model, "--budget", "6")
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "tests/test_slow_collect.py — did not finish collecting within the 6s budget" in summary, summary
    waited = re.search(re.escape("(includes waiting for the machine's test slot): total ") + r"([0-9.]+)", summary)
    assert waited is not None, summary
    assert float(waited.group(1)) < SLOW_COLLECTION_SECONDS / 2, summary
    counts = _counts(output)
    assert " selected=1 ran=0 red=0 unmeasured=1 " in counts, counts
    seconds = re.search(r" seconds=([0-9.]+) ", counts)
    assert seconds is not None, counts
    assert float(seconds.group(1)) < SLOW_COLLECTION_SECONDS, counts


@pytest.fixture(scope="module")
def runner() -> Iterator[ModuleType]:
    """Load the entry point script as a module (a dataclass looks up its defining module in sys.modules -- place it only for the duration of the test)."""
    spec = importlib.util.spec_from_file_location("run_changed_tests", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module


SIM = "packages/doeff-cluster/src/doeff_cluster/_sim"


def test_each_test_goes_to_the_session_the_daily_run_uses(runner: ModuleType) -> None:
    """Session boundaries are the same as the daily run: under a root outside tests/ it is that root; under `packages/<name>/tests/` it is that package; otherwise the repo root.
    An argument with `::test-name` is also decided by the file path."""
    roots = runner.SessionRoots(extra=(SIM,))
    assert runner.session_of(f"{SIM}/test_local.hy", roots) == SIM
    assert runner.session_of(f"{SIM}/deep/test_x.hy::test_y", roots) == SIM
    assert runner.session_of("packages/doeff-records/tests/test_laws.hy", roots) == (
        "packages/doeff-records/tests"
    )
    assert runner.session_of("packages/doeff-records/tests/sub/test_a.py::test_b", roots) == (
        "packages/doeff-records/tests"
    )
    assert runner.session_of("packages/doeff-records/src/doeff_records/test_x.py", roots) == "."
    assert runner.session_of("tests/test_daily_test_population.py", roots) == "."
    assert runner.session_of("docs/adr/defadr_doeff_domain_001.hy::test_x", roots) == "."
    # Another dir that merely starts with a root's name is not that root.
    assert runner.session_of(f"{SIM}_other/test_x.hy", roots) == "."


def test_batches_keep_the_contract_set_first_and_merge_neighbours(runner: ModuleType) -> None:
    """Bundle order: keep the order of sets (contract set -> changed tests -> reverse dependencies), bundle per session within a set (session order is the order first
    seen within the set). Adjacent bundles of the same session become one process -- even if the contract set is in another package, it runs before reverse dependencies."""
    origin = runner.Origin

    def target(arg: str, kind: object) -> object:
        return runner.Target(arg=arg, origin=kind, why="test")

    selection = runner.Selection(
        targets=(
            target("tests/test_daily_test_population.py", origin.CONTRACT),
            target("packages/doeff-records/tests/test_static_stubs.py", origin.CONTRACT),
            target("packages/doeff-records/tests/test_changed.py", origin.CHANGED),
            target("packages/doeff-cluster/tests/test_near.py", origin.REVERSE),
            target("tests/test_far.py", origin.REVERSE),
            target("packages/doeff-cluster/tests/test_far.py", origin.REVERSE),
        )
    )
    batches = runner.batches_of(selection, runner.SessionRoots(extra=()))
    assert [(b.session, [t.arg for t in b.targets]) for b in batches] == [
        (".", ["tests/test_daily_test_population.py"]),
        (
            "packages/doeff-records/tests",
            [
                "packages/doeff-records/tests/test_static_stubs.py",
                "packages/doeff-records/tests/test_changed.py",
            ],
        ),
        (
            "packages/doeff-cluster/tests",
            [
                "packages/doeff-cluster/tests/test_near.py",
                "packages/doeff-cluster/tests/test_far.py",
            ],
        ),
        (".", ["tests/test_far.py"]),
    ]
    # With only one session there is one process (same as before).
    single = runner.Selection(targets=selection.targets[:1])
    assert len(runner.batches_of(single, runner.SessionRoots(extra=()))) == 1


def test_the_session_roots_are_read_from_the_makefile(runner: ModuleType) -> None:
    """The real tree: session roots can be read from PACKAGE_EXTRA_TEST_ROOTS of the Makefile (the source of definition of the daily population), and all are dirs in the tree."""
    roots = runner.read_session_roots(REPO_ROOT)
    assert roots.extra, "there are 0 roots outside tests/ -- the Makefile reading is broken"
    assert all((REPO_ROOT / root).is_dir() for root in roots.extra), roots


def test_the_budget_clock_stops_between_configure_and_the_session_start(runner: ModuleType) -> None:
    """The limit clock: counts from launch to configure (import of uv, python, conftest) and from the session start onward,
    and does not count the configure interval (the machine's test slot wait) -- the clock is stopped while waiting. Even if the beat line comes twice,
    the first beat is used, and the end line does not change the clock."""
    spawned = runner.ChildClock(spawned=100.0, configuring=None, started=None)
    assert (spawned.counted(102.0), spawned.waited(102.0), spawned.paused) == (2.0, 0.0, False)
    configuring = spawned.mark(runner.Configuring(), 102.0)
    assert (configuring.counted(130.0), configuring.waited(130.0), configuring.paused) == (
        2.0,
        28.0,
        True,
    )
    started = configuring.mark(runner.SessionStarted(), 121.0)
    assert (started.counted(130.0), started.waited(130.0), started.paused) == (11.0, 19.0, False)
    assert started.mark(runner.Configuring(), 125.0) == started
    assert started.mark(runner.Collected(nodeids=("tests/test_ok.py::test_ok",)), 125.0) == started


def test_the_counts_split_every_selected_test_once(runner: ModuleType) -> None:
    """Count summary: each selected test is counted as exactly one of ran, red, or unmeasured (selected = ran + red +
    unmeasured), and reverse-dependency tests among the unmeasured are counted separately. A line has only the leading mark and `name=count` (no list of test names)."""
    origin, verdict = runner.Origin, runner.Verdict
    results = tuple(
        runner.TargetResult(runner.Target(arg=arg, origin=kind, why="test"), outcome, "reason")
        for arg, kind, outcome in (
            ("tests/test_contract.py", origin.CONTRACT, verdict.GREEN),
            ("tests/test_changed.py", origin.CHANGED, verdict.RED),
            ("tests/test_changed_slow.py", origin.CHANGED, verdict.UNMEASURED),
            ("tests/test_far_a.py", origin.REVERSE, verdict.UNMEASURED),
            ("tests/test_far_b.py", origin.REVERSE, verdict.UNMEASURED),
            ("tests/test_near.py", origin.REVERSE, verdict.GREEN),
        )
    )
    counts = runner.counts_of(runner.Finished(rc=1, results=results), 61.04, 60.0)
    assert (counts.selected, counts.ran, counts.red, counts.unmeasured) == (6, 2, 1, 3)
    assert counts.unmeasured_reverse == 2
    assert counts.selected == counts.ran + counts.red + counts.unmeasured
    line = runner.counts_line(counts)
    assert line == (
        "land-focus-counts: selected=6 ran=2 red=1 unmeasured=3 unmeasured_reverse=2"
        " rc=1 seconds=61.0 budget=60"
    ), line
    assert "tests/" not in line
    empty = runner.counts_line(runner.counts_of(runner.Finished(rc=2, results=()), 0.31, 60.0))
    assert empty == (
        "land-focus-counts: selected=0 ran=0 red=0 unmeasured=0 unmeasured_reverse=0"
        " rc=2 seconds=0.3 budget=60"
    ), empty
