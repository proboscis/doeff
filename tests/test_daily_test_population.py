"""ADR-DOE-ENFORCE-001 R8: coverage of the daily full-verification population, the shape of failure names, and where test files live.

The daily full verification (gate.full in .agents/land-queue.toml) has three populations: root pytest, per-package
pytest (`make test-packages`), and per-Rust-crate cargo test (`make test-rust`). The declaration shape
(a separate processing stage per population) is held by the ADR deftest
test-adr-doe-enforce-001-daily-populations-are-separate-stages; this file holds the behavior itself:

- The package loop visits every expected set and every root outside a package's tests/ (the Makefile's PACKAGE_EXTRA_TEST_ROOTS),
  continues after a red, and names the failing packages at the end.
- A package's failure names are relative to the repo root and do not collide across packages (the daily tooling takes failure names
  verbatim from the summary lines and passes them to pytest as-is from the repo root).
- Test files (`.py` and `.hy` -- defined in the single place `_test_file_patterns`) are under some population's root or
  in the table of exclusions with reasons.
- Files named like tests (test_*.py, test_*.hy) match some collector's pattern (the root ini's python_files,
  doeff_hy_test_files, and the executable ADR pattern), and the Hy collector is not copied into conftest.py. Each row of the skip list
  (doeff_hy_test_skips) names a current Hy test file.
- A package has no pytest configuration of its own (if it did, the root ini and conftest would stop applying).

Live counterexample: the 2026-09-24 daily (snapshot f271ae39) stopped at `&&` on 3 root reds, and the package and
Rust populations did not run a single test while the ledger recorded coverage = complete. The `make test-packages`
loop also stopped at the first red package (`|| exit 1`), and failure names from running in a package dir
took the form `tests/test_cli.py::...`, so 4 pairs of same-named files collided (docs/design/test-population-ki-08ec/).
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import tomllib
from doeff_adr import DEFAULT_FILE_PATTERNS as ADR_FILE_PATTERNS
from doeff_adr._pytest_plugin import parse_hy_test_skips
from doeff_core_effects import EnvEntry, EnvMode, RunProcess, subprocess_handler

from doeff import run, with_handlers

REPO_ROOT = Path(__file__).resolve().parents[1]

# pytest's default python_files (if the root ini writes python_files, that is read instead).
_PYTEST_PYTHON_FILES = ("test_*.py", "*_test.py")
# Files "named like tests" -- if no collector collects them, not one of them runs in the daily.
_TEST_NAMED_SOURCES = ("test_*.py", "test_*.hy")


def _test_file_patterns(repo: Path) -> tuple[str, ...]:
    """The single definition of "what is a test file" -- the naming rules of the 3 routes pytest collects through. Each is a value the collector itself reads.

    - Python tests: the root ini's python_files (default is pytest's default).
    - Hy test files: the root ini's doeff_hy_test_files (the doeff-adr plugin's collector reads the same value; it collects
      both deftest files and scripts that run top to bottom).
    - executable ADR: the doeff-adr plugin's default pattern and the root ini's doeff_adr_hy_files.

    When only `.py` was counted, a `.hy` test outside the population did not turn red even if it did not run in the daily
    (#2542, packages/doeff-cluster/src/doeff_cluster/_sim/test_entries_on_sim.hy).
    When the Hy condition was copied into the test side as `test_*.hy`, test_*.hy files in dirs with no collecting conftest (the 6 in packages/doeff-hy/tests)
    were counted as "test files" while no collector collected them.
    """
    ini = _root_ini(repo)
    return (
        *ini.get("python_files", _PYTEST_PYTHON_FILES),
        *ini.get("doeff_hy_test_files", ()),
        *ADR_FILE_PATTERNS,
        *ini.get("doeff_adr_hy_files", ()),
    )


def _is_test_file(rel: str, patterns: tuple[str, ...]) -> bool:
    """Same matching as pytest: a pattern without `/` is matched against the file name, one with `/` against the path from the repo root."""
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(rel if "/" in p else name, p) for p in patterns)


# Dirs not walked = pytest's default norecursedirs + build outputs (target, __pycache__).
# The daily check's tree contains make sync outputs (.venv, target), and the remote trees up to 2026-10-10 (copies by remote_check -- sending only the git manifest
# files) had no .git either, so walk the file system rather than git (since 2026-10-10 the stage runs directly in the daily task's git working tree
# — ADR-DOE-ENFORCE-001 R10)。
# Measured 2026-09-25: the set of test-named files walked by this rule matched `git ls-files --cached --others --exclude-standard` both in a bare worktree (450 files) and
# in a built checkout (449 files).
_NOT_WALKED = (
    "*.egg",
    "*.egg-info",
    ".*",
    "_darcs",
    "build",
    "CVS",
    "dist",
    "node_modules",
    "venv",
    "{arch}",
    "target",
    "__pycache__",
)

# Table of exclusions for test-named files that belong to no population (path, or a dir prefix ending in `/` -> reason).
# State a reason when adding a row. A row matching no file at all turns red as a stale row.
# Design-record dirs are not excluded by a dir row -- copies and mock-ups of tests inside a record are placed under names that do not match a collector's pattern
# (`<name>.py.txt`). If excluded by a row, the make test-changed selection (which reads only the naming rule) runs the copies as tests, and it stays green even if a real
# test is placed only inside a record (blind A/B of the design review -- rows under docs/design/
# are in the same shape, and the 4 seat-home mock-ups were switched to .py.txt on 2026-10-12 and removed from the table).
# Rows inside a record are written, one path per file, only for files that a hash-sealed closed record names and so cannot be renamed (unlike a dir row,
# they cannot hide new tests -- dir rows are forbidden by test_exclusion_table_has_no_dir_row_over_a_record_dir). The record dirs are defined in one place, the root
# pyproject.toml `[tool.doeff] record-dirs`, and the make test-changed selection (scripts/run_changed_tests.py) also does not select files under it
# as tests. When the selection picked files inside a record, a change to a module that file imports turned red as "failed at collection"
# (blind A of the design review, measured).
EXCLUDED: dict[str, str] = {
    "packages/doeff-agents/conformance/": (
        "A black-box handover gate using real tmux / herdr panes (no real model). Whether tmux is"
        " usable on the daily host is unmeasured; whether to include it in a population is decided in a separate pass"
    ),
    "packages/doeff-agentic/examples/": "Example scripts (run via __main__)",
    "examples/test_llm_agent.py": (
        "An example pytest test that users copy next to the example llm_agent.py and run. tests/test_examples_run.py::test_example_tests_pass_with_only_the_example_files "
        "under the population root (testpaths) runs it daily in a dir outside the repo containing only the .py files of examples/ "
        "-- placed under the root, it would also run under the root conftest.py and pyproject.toml pytest settings, "
        "and would stop verifying that it passes with only the 4 core pieces and pytest"
    ),
    "examples/github_repo/": (
        "An example pytest test of an HTTP API example that users run with the README command `pytest examples/github_repo`. tests/test_examples_run.py::test_github_repo_example_tests_run_with_pytest_alone under the population root "
        "runs it daily exactly as that command does, in a child outside pytest "
        "with no plugins (the example dir's pytest.ini pins rootdir to the example dir) -- like test_llm_agent.py above, "
        "placed under the root it would also run under the root conftest.py and stop verifying that it passes with pytest alone"
    ),
    "examples/testing_without_mocks/": (
        "An example test of mock.patch and a doeff handler that users run with `pytest` per the README in the introductory article's example dir. The example is written pinned to the PyPI "
        " version (ADR-DOE-API-002 R3), so it is not run against the repo version; the daily CI (.github/workflows/core-install-from-pypi.yml)'s "
        "tools/verify_core_install.py --pypi runs it where the example dir is copied into a venv with only PyPI's doeff and pytest"
        " -- placed under the root it would run against the repo version and under the root conftest.py"
    ),
}


def _expected_packages(repo: Path) -> list[str]:
    """Expected package population -- every p for which `packages/<p>/tests` contains non-fixture test files.

    Not taken from the Makefile's actual run: if the expectation is taken from the implementation, when the Makefile shrinks the population
    the visited set and the expectation shrink together and stay green (blind B's counterexample).
    """
    files = _test_named_files(repo)
    return sorted(
        {
            parts[1]
            for parts in (path.split("/") for path in files)
            if len(parts) > 3 and parts[0] == "packages" and parts[2] == "tests"
        }
    )


def _test_named_files(repo: Path) -> list[str]:
    """Paths relative to the repo root of the repo's test files (matching `_test_file_patterns`, excluding those under fixtures)."""
    patterns = _test_file_patterns(repo)
    return sorted(
        rel
        for current, dirs, files in _walk(repo)
        for rel in (Path(current, name).relative_to(repo).as_posix() for name in files)
        if _is_test_file(rel, patterns) and "fixtures" not in rel.split("/")
    )


def _walk(repo: Path) -> Iterator[tuple[str, list[str], list[str]]]:
    """An os.walk that does not descend into `_NOT_WALKED` dirs."""
    for current, dirs, files in os.walk(repo):
        dirs[:] = sorted(
            name
            for name in dirs
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in _NOT_WALKED)
        )
        yield current, dirs, files


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_excluded(path: str, row: str) -> bool:
    return path == row or (row.endswith("/") and path.startswith(row))


def _root_ini(repo: Path) -> dict[str, Any]:
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["pytest"]["ini_options"]


def _root_testpaths(repo: Path) -> list[str]:
    return list(_root_ini(repo)["testpaths"])


def _record_dirs(repo: Path) -> list[str]:
    """Design-record dirs -- defined in the single place root pyproject.toml's `[tool.doeff] record-dirs` (the make test-changed selection reads the same value)."""
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return list(config["tool"]["doeff"]["record-dirs"])


def _package_extra_test_roots(repo: Path, overrides: tuple[str, ...] = ()) -> list[str]:
    """Roots that `make test-packages` runs outside a package's tests/ -- defined in the single place Makefile's PACKAGE_EXTRA_TEST_ROOTS.

    Do not copy the list into the test side; ask the real Makefile for the names (overrides are variable overrides on make's command line).
    """
    proc = subprocess.run(
        [
            "make",
            "-s",
            "--no-print-directory",
            "-f",
            str(REPO_ROOT / "Makefile"),
            "-C",
            str(repo),
            "print-package-extra-test-roots",
            *overrides,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout.split()


def _population_roots(repo: Path, overrides: tuple[str, ...] = ()) -> list[str]:
    """Roots of the daily population -- root testpaths, each package's tests, and roots outside a package's tests."""
    return [
        *_root_testpaths(repo),
        *(f"packages/{package}/tests" for package in _expected_packages(repo)),
        *_package_extra_test_roots(repo, overrides),
    ]


def _assert_every_test_file_belongs_to_a_daily_population(repo: Path, roots: list[str]) -> None:
    orphans = [
        path
        for path in _test_named_files(repo)
        if not any(_is_under(path, root) for root in roots)
        and not any(_is_excluded(path, row) for row in EXCLUDED)
    ]
    assert not orphans, (
        f"{len(orphans)} test files belong to no daily population and are not in the exclusion table: {orphans}"
        " -- put them under a population root (add a root outside a package's tests/ to the Makefile's PACKAGE_EXTRA_TEST_ROOTS), or"
        " list them in EXCLUDED in tests/test_daily_test_population.py with a reason (a green on a tree with no caller is invisible to the daily). "
        "Copies and mock-ups of tests inside a design record (root pyproject.toml `[tool.doeff] record-dirs`) should be "
        "renamed to a name that does not match a collector's pattern (append `.txt` to the original name: `test_x.py` -> `test_x.py.txt`, `test_x.hy` -> `test_x.hy.txt`)"
        "— ADR-DOE-ENFORCE-001 R8"
    )


def _assert_every_test_named_source_is_collected(repo: Path) -> None:
    """Files named like tests (test_*.py, test_*.hy) match some collector's pattern (excluding those under fixtures).

    A file that does not match is not collected by pytest even if it is under a population root, and not one of them runs in the daily (
    the 6 in packages/doeff-hy/tests were test_*.hy in a dir with no collecting conftest).
    """
    patterns = _test_file_patterns(repo)
    uncollected = sorted(
        rel
        for current, _dirs, files in _walk(repo)
        for rel in (Path(current, name).relative_to(repo).as_posix() for name in files)
        if _is_test_file(rel, _TEST_NAMED_SOURCES)
        and not _is_test_file(rel, patterns)
        and "fixtures" not in rel.split("/")
    )
    assert not uncollected, (
        f"{len(uncollected)} test files are collected by no collector: {uncollected} -- give them names matching a collector's pattern"
        " (the root ini's python_files, doeff_hy_test_files, or doeff-adr's executable ADR pattern), or"
        " add the collector's pattern to the ini (do not copy a collector into each package's conftest.py) -- ADR-DOE-ENFORCE-001 R8"
    )


def _named_failed_packages(output: str) -> list[str] | None:
    """The failing packages `make test-packages` named last (None if there is no `test-packages failed:` line)."""
    summary = [line for line in output.splitlines() if line.startswith("test-packages failed:")]
    return summary[-1].split(":", 1)[1].split() if summary else None


# Fake runner (placed in make's PACKAGE_UV_RUN) -- exits 1 only when one of its arguments hits a dir in DOEFF_FAKE_RUNNER_FAIL, and appends the rc it returned,
# cwd, and arguments as one tab-separated line to the record file. Arguments are resolved from the runner's cwd, so even a form that runs from the package dir
# counts as the same package. Written in sh: in Python, one round started Python 3.14t as many times as there are sessions (35), and at load 65-70
# one round took 4.75 seconds. Paths are resolved with cd and pwd -P (realpath -m is not on Mac).
_FAKE_RUNNER = """\
unset CDPATH
fail=$(cd -- "$DOEFF_FAKE_RUNNER_FAIL" 2>/dev/null && pwd -P)
rc=0
line=$(pwd -P)
for arg in "$@"; do
    line="$line\t$arg"
    if [ -n "$fail" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$fail" ]; then rc=1; fi
done
printf '%s\\t%s\\n' "$rc" "$line" >> "$DOEFF_FAKE_RUNNER_RECORD"
exit $rc
"""


@dataclass(frozen=True)
class _RunnerCall:
    """One call of the fake runner -- the rc it returned, and the cwd and arguments it received."""

    rc: int
    cwd: str
    argv: list[str]

    def dirs(self) -> list[Path]:
        """Those arguments that point to a dir, as physical paths resolved from the runner's cwd (independent of spelling, trailing `/`, and relative vs absolute)."""
        return [path for arg in self.argv if (path := Path(self.cwd, arg).resolve()).is_dir()]


@dataclass(frozen=True)
class _MakeRound:
    """One round of the real Makefile's `make test-packages` run with the fake runner."""

    calls: list[_RunnerCall]  # in the order the runner was called
    output: str
    exit_code: int
    timed_out: bool


def _run_test_packages(repo: Path, tmp: Path, fail: Path, *overrides: str) -> _MakeRound:
    """Run the real Makefile's test-packages on the repo with the fake runner (only the session that receives the fail dir turns red).

    overrides are variable overrides on make's command line. The runner and record are placed in tmp.
    """
    runner = tmp / "fake_runner.sh"
    runner.write_text(_FAKE_RUNNER, encoding="utf-8")
    record = tmp / "calls.tsv"
    # The child (make) is started by doeff's child-process answerer, and the fake runner's 2 names are layered on the current environment and passed.
    proc = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(
                argv=(
                    "make",
                    "-s",
                    "-f",
                    str(REPO_ROOT / "Makefile"),
                    "-C",
                    str(repo),
                    "test-packages",
                    f"PACKAGE_UV_RUN=sh {runner}",
                    *overrides,
                ),
                env=(
                    EnvEntry(name="DOEFF_FAKE_RUNNER_RECORD", value=str(record)),
                    EnvEntry(name="DOEFF_FAKE_RUNNER_FAIL", value=str(fail)),
                ),
                env_mode=EnvMode.EXTEND,
                timeout=120.0,
            ),
        )
    )
    lines = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
    return _MakeRound(
        calls=[
            _RunnerCall(rc=int(rc), cwd=cwd, argv=argv)
            for rc, cwd, *argv in (line.split("\t") for line in lines)
        ],
        output=proc.stdout + proc.stderr,
        exit_code=proc.exit_code,
        timed_out=proc.timed_out,
    )


@dataclass(frozen=True)
class _PackagesRound:
    """This repo's round -- only the first package (first) in expected-set name order was made red."""

    expected: list[str]
    first: str
    made: _MakeRound


@pytest.fixture(scope="module")
def packages_round(tmp_path_factory: pytest.TempPathFactory) -> _PackagesRound:
    """Run this repo's `make test-packages` round once per module, and let the 2 package-loop tests read its record together.

    Running it per test would run the same round (starting the runner once per package) twice.
    This round contains a red, so "rc 0 when everything is green" is checked by the model-tree test
    test_make_test_packages_is_green_when_every_package_is_green. This round's simulated outcome is the single one "only the first package is red";
    tests that check other outcomes (rc 5, 2 reds, timeout) run their own round with `_run_test_packages` -- if they piggybacked, the visiting test
    could no longer say what red the rc != 0 came from (the visiting test turns red -- blind B of the design review).
    """
    expected = _expected_packages(REPO_ROOT)
    assert expected, "no package with Python tests found under packages/*/tests"
    first = expected[0]
    made = _run_test_packages(
        REPO_ROOT,
        tmp_path_factory.mktemp("packages-round"),
        REPO_ROOT / "packages" / first / "tests",
    )
    return _PackagesRound(expected=expected, first=first, made=made)


def test_make_test_packages_is_green_when_every_package_is_green(tmp_path: Path) -> None:
    """In a model tree (packages a and b, and one root outside a package's tests/), rc is 0 and no failing package is named when every session is green.

    This repo's round makes the first package red, so only this test sees the green ending (if the summary line's if is rewritten as `[ ... ] && ...`, the recipe's last
    command returns 1 when green) -- before the round was shared by 2 tests, the seconds-flag test saw rc 0 in an all-green round.
    Visited roots are compared as resolved dirs (the Makefile spelling a root as `tests/` or an absolute path still counts as the same root -- blind A).
    """
    roots = ("extra/sim", "packages/a/tests", "packages/b/tests")
    for root in roots:
        (tmp_path / root).mkdir(parents=True)
        (tmp_path / root / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    made = _run_test_packages(
        tmp_path, tmp_path, tmp_path / "no-such-tests", "PACKAGE_EXTRA_TEST_ROOTS=extra/sim"
    )
    output = made.output
    assert not made.timed_out, f"make test-packages did not finish in 120 seconds\n{output[-2000:]}"
    visited = sorted(path for call in made.calls for path in call.dirs())
    assert visited == sorted((tmp_path / root).resolve() for root in roots), (
        f"did not visit every session of the model tree exactly once: {visited}\n{output[-2000:]}"
    )
    assert made.exit_code == 0, (
        f"every session is green yet make test-packages rc is {made.exit_code}"
        f" -- the daily stage packages would turn red on a green day -- R8\n{output[-2000:]}"
    )
    assert _named_failed_packages(output) is None, (
        f"every session is green yet a failing package was named: {_named_failed_packages(output)} -- R8\n{output[-2000:]}"
    )


def test_make_test_packages_visits_every_package_after_a_red(
    packages_round: _PackagesRound,
) -> None:
    """Even if only the first package in expected-set name order is made red, the loop visits all of them and names it at the end."""
    first, expected = packages_round.first, packages_round.expected
    calls, output = packages_round.made.calls, packages_round.made.output
    assert not packages_round.made.timed_out, (
        f"make test-packages did not finish in 120 seconds\n{output[-2000:]}"
    )

    packages_dir = (REPO_ROOT / "packages").resolve()
    reached = {
        path.parent.name
        for call in calls
        for path in call.dirs()
        if path.parent.parent == packages_dir and path.name == "tests"
    }

    assert reached == set(expected), (
        f"the packages make test-packages visited differ from expected: missing {sorted(set(expected) - reached)}"
        f" / extra {sorted(reached - set(expected))} (number of runner calls {len(calls)};"
        f" only the first {first} was made red) -- a missing package runs not a single test in the daily (stopped"
        f" after the red, or dropped from the population) -- ADR-DOE-ENFORCE-001 R8\n{output[-2000:]}"
    )
    extra_roots = _package_extra_test_roots(REPO_ROOT)
    passed = {path for call in calls for path in call.dirs()}
    missed_roots = [root for root in extra_roots if (REPO_ROOT / root).resolve() not in passed]
    assert not missed_roots, (
        f"make test-packages did not visit the roots outside a package's tests/ {missed_roots} (only the first {first}"
        f" was made red) -- that root's tests run not a single test in the daily -- R8\n{output[-2000:]}"
    )
    first_tests = (REPO_ROOT / "packages" / first / "tests").resolve()
    nonzero = [call for call in calls if call.rc != 0]
    assert [first_tests in call.dirs() for call in nonzero] == [True], (
        f"the sessions that returned non-zero in this round are not just the first package {first}: {[c.argv for c in nonzero]}"
        " -- the rc != 0 and naming checks below assume a round where \"only the first package is red\". Tests that check other simulated outcomes (rc 5, 2 reds,"
        " timeout) run their own round with `_run_test_packages` -- R8"
    )
    assert packages_round.made.exit_code != 0, (
        f"the first package {first} was made red yet make test-packages rc is 0 -- R8\n{output[-2000:]}"
    )
    named = _named_failed_packages(output)
    assert named == [first], (
        f"did not name only the failing package ({first}) last in the `test-packages failed:` line: "
        f"{named} -- R8\n{output[-2000:]}"
    )


def test_package_failure_names_are_repo_root_relative(tmp_path: Path) -> None:
    """In a model tree, run the real Makefile and real pytest and check that failure names are relative to the repo root.

    Packages a and b both have a same-named failure tests/test_cli.py::test_fail, and c is green. Running from
    the package dir would make both `FAILED tests/test_cli.py::test_fail` and collide.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nmarkers = ["e2e: model marker"]\n', encoding="utf-8"
    )
    for package in ("a", "b"):
        test_file = tmp_path / "packages" / package / "tests" / "test_cli.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    green = tmp_path / "packages" / "c" / "tests" / "test_ok.py"
    green.parent.mkdir(parents=True)
    green.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    # The model tree has neither the root conftest nor outside plugins -- do not bring in the outer run's settings. The child (make) is started by doeff's child-process
    # answerer, and these 3 names are layered on the current environment and passed.
    proc = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(
                argv=(
                    "make",
                    "-s",
                    "-f",
                    str(REPO_ROOT / "Makefile"),
                    "-C",
                    str(tmp_path),
                    "test-packages",
                    f"PACKAGE_UV_RUN={sys.executable} -m",
                ),
                env=(
                    EnvEntry(name="PYTEST_DISABLE_PLUGIN_AUTOLOAD", value="1"),
                    EnvEntry(name="PYTEST_ADDOPTS", value="-p no:cacheprovider"),
                    EnvEntry(name="PYTHONDONTWRITEBYTECODE", value="1"),
                ),
                env_mode=EnvMode.EXTEND,
                timeout=120.0,
            ),
        )
    )
    output = proc.stdout + proc.stderr
    assert not proc.timed_out, f"make test-packages did not finish in 120 seconds\n{output}"
    lines = output.splitlines()
    failed = sorted(line.split(" - ")[0] for line in lines if line.startswith("FAILED "))

    assert failed == [
        "FAILED packages/a/tests/test_cli.py::test_fail",
        "FAILED packages/b/tests/test_cli.py::test_fail",
    ], (
        f"package failure names are not relative to the repo root / collide across packages: {failed}"
        f" -- the daily tooling passes failure names to pytest from the repo root -- ADR-DOE-ENFORCE-001 R8\n{output}"
    )
    assert any("packages/c/tests/test_ok.py" in line for line in lines), (
        f"green package c after the red did not run -- R8\n{output}"
    )
    assert proc.exit_code != 0, f"rc is 0 although there are failing packages -- R8\n{output}"
    named = _named_failed_packages(output)
    assert named == ["a", "b"], (
        f"did not name the failing packages (a b) last in the `test-packages failed:` line: {named}"
        f" -- R8\n{output}"
    )


def test_every_test_file_belongs_to_a_daily_population() -> None:
    """Test files (.py, .hy) are under the daily population's roots (root testpaths, package tests, the Makefile's
    PACKAGE_EXTRA_TEST_ROOTS) or in the exclusion table."""
    _assert_every_test_file_belongs_to_a_daily_population(REPO_ROOT, _population_roots(REPO_ROOT))


def test_hy_test_outside_every_population_is_red_until_its_root_is_declared(
    tmp_path: Path,
) -> None:
    """Failing case: a dir that has only `.hy` tests outside a package's tests/ is red, and green once that dir is put in the real
    Makefile's PACKAGE_EXTRA_TEST_ROOTS (the shape of the #2542 sim dir).

    Under the rule that counted only `.py`, this `.hy` was not counted as a test file and stayed green even outside the population.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\ndoeff_hy_test_files = ["test_*.hy"]\n',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    sim = "packages/demo/src/demo/sim"
    hy_test = tmp_path / sim / "test_on_sim.hy"
    hy_test.parent.mkdir(parents=True)
    hy_test.write_text("(deftest test-on-sim (assert True))\n", encoding="utf-8")

    assert f"{sim}/test_on_sim.hy" in _test_named_files(tmp_path), (
        "a `.hy` test outside the population is not counted as a test file -- R8"
    )
    # The Makefile's default root (doeff-cluster's sim) is not in the model tree -- this dir is outside the population.
    with pytest.raises(AssertionError, match=re.escape(f"{sim}/test_on_sim.hy")):
        _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
    declared = _population_roots(tmp_path, (f"PACKAGE_EXTRA_TEST_ROOTS={sim}",))
    assert sim in declared
    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, declared)


def test_test_named_copy_in_a_design_check_record_is_red_until_renamed(tmp_path: Path) -> None:
    """Failing case: placing a copy of a test name (test_*.py) inside a design-verification record is red, and green once renamed to a name that does not match a collector's pattern
    (`.py.txt`) (design verification).

    In the form that excluded docs/design-checks/ with an exclusion row (77e0eb3f9), the copies ran as tests in the make test-changed selection (copies whose names contain `.`
    fail at collection and give red 1), and it was green even with a real test placed only inside a record (blind A/B).
    """
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    copy = tmp_path / "docs" / "design-checks" / "lt-MODEL" / "evidence" / "scripts" / "test_scope.revision.py"
    copy.parent.mkdir(parents=True)
    copy.write_text("def test_scope():\n    pass\n", encoding="utf-8")

    with pytest.raises(AssertionError, match=re.escape(copy.relative_to(tmp_path).as_posix())):
        _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
    copy.rename(copy.with_name(f"{copy.name}.txt"))
    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))


def test_test_named_file_in_a_design_record_is_red_until_renamed(tmp_path: Path) -> None:
    """Failing case: placing a file named like a test (a `test_*.py` mock-up, or a `*_test.py` measurement script) inside a design-record dir is red, and green once renamed to a name that does not
    match a collector's pattern (`.py.txt`). A new test placed next to a file that has a named row in the exclusion table is also red.

    In the form that excluded record dirs with a dir row, the 4 mock-ups (scripts with no `def test_*` that run `t_*` from __main__) were
    collected as tests by the make test-changed selection (0 collected and "unmeasured"), and the daily was green even with a real test placed only inside a record.
    The record dir is a name in the model tree (the current tree has no record dir).
    """
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    named = next(Path(row) for row in EXCLUDED if not row.endswith("/"))
    candidates = (
        *(
            Path(record, "MODEL", *tail)
            for record in ("docs/records/",)
            for tail in (("model", "test_chain.py"), ("evidence", "scripts", "fork_test.py"))
        ),
        named.with_name("hidden_test.py"),
    )
    for path in (named, *candidates):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("def test_it():\n    pass\n", encoding="utf-8")

    with pytest.raises(AssertionError) as red:
        _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
    message = str(red.value)
    missing = [path.as_posix() for path in candidates if path.as_posix() not in message]
    assert not missing, f"not named as tests outside the population: {missing} -- R8"
    assert named.as_posix() not in message, f"even {named}, which the exclusion table names, has turned red"
    for path in candidates:
        (tmp_path / path).rename(tmp_path / f"{path}.txt")
    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))


def test_every_test_named_file_is_collected_by_some_collector() -> None:
    """Every test_*.py and test_*.hy file matches some collector's pattern."""
    _assert_every_test_named_source_is_collected(REPO_ROOT)


def test_hy_test_without_a_collector_is_red_until_the_collector_is_declared(tmp_path: Path) -> None:
    """Failing case: placing one test_*.hy that no collector collects under the population root (root testpaths) is red,
    and green once a collector is pointed at it via the root ini's doeff_hy_test_files.

    In the #2577 form that copied the Hy condition into the test side as `test_*.hy`, this file counted as a "test file" even with no collector in the ini,
    and was green because it was under a population root -- the same shape as the 6 in packages/doeff-hy/tests.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    script = tmp_path / "tests" / "test_script.hy"
    script.write_text("(assert (= (+ 1 1) 2))\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")

    # It is under the population root -- the placement test stays green (whether it is collected is a separate matter).
    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
    with pytest.raises(AssertionError, match=re.escape("tests/test_script.hy")):
        _assert_every_test_named_source_is_collected(tmp_path)

    pyproject.write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\ndoeff_hy_test_files = ["test_*.hy"]\n',
        encoding="utf-8",
    )
    _assert_every_test_named_source_is_collected(tmp_path)


def test_hy_test_skips_name_hy_test_files_with_a_reason() -> None:
    """Each row of the skip list (the root ini's doeff_hy_test_skips) names a Hy test file in the current tree and has a reason.

    The list is read with the same function as the collector (the doeff-adr plugin) -- a row without a reason stops there. If the row of a fixed file
    remains, that file is green yet counted as a skip without running in the daily.
    """
    rows = parse_hy_test_skips(_root_ini(REPO_ROOT).get("doeff_hy_test_skips", ()))
    patterns = tuple(_root_ini(REPO_ROOT).get("doeff_hy_test_files", ()))
    stale = [
        row.path
        for row in rows.rows
        if not (REPO_ROOT / row.path).is_file() or not _is_test_file(row.path, patterns)
    ]
    assert not stale, (
        f"rows of doeff_hy_test_skips that are not a Hy test file: {stale} -- delete them or fix the path -- ADR-DOE-ENFORCE-001 R8"
    )


def test_no_conftest_copies_the_hy_test_collector() -> None:
    """The collector of Hy test files is the single place root ini's doeff_hy_test_files -- not copied into conftest.py.

    If a copy remains, test_*.hy outside that dir are counted as population tests while uncollected (before this test was added,
    7 conftest.py files wrote the same condition, and the 6 in packages/doeff-hy/tests, which have no conftest, did not run in the daily).
    """
    copies = sorted(
        Path(current, "conftest.py").relative_to(REPO_ROOT).as_posix()
        for current, _dirs, files in _walk(REPO_ROOT)
        if "conftest.py" in files
        and "DoeffAdrHyFile" in Path(current, "conftest.py").read_text(encoding="utf-8")
    )
    assert not copies, (
        f"conftest.py files that collect Hy test files themselves: {copies} -- move the collecting condition to the root ini's doeff_hy_test_files"
        " -- ADR-DOE-ENFORCE-001 R8"
    )


def test_exclusion_table_has_no_dir_row_over_a_record_dir() -> None:
    """The exclusion table has no row covering a design-record dir wholesale (the record dir, its parent, or a dir under it).

    A real test placed under a dir row is found by neither the population tests nor the stale-row test (blind B of the design review).
    Within a record, only per-file path rows for files that cannot be renamed may be excluded.
    """
    records = _record_dirs(REPO_ROOT)
    covering = [
        row
        for row in EXCLUDED
        if row.endswith("/") and any(record.startswith(row) or row.startswith(record) for record in records)
    ]
    assert not covering, (
        f"the exclusion table has a dir row covering a design-record dir: {covering} -- delete it and rename test-named files inside to `.py.txt`"
        " (only files that cannot be renamed get a per-file path row) -- ADR-DOE-ENFORCE-001 R8"
    )


def test_exclusion_table_has_no_stale_rows() -> None:
    """Each row of the exclusion table covers at least one test-named file in the current tree."""
    files = _test_named_files(REPO_ROOT)
    stale = [row for row in EXCLUDED if not any(_is_excluded(path, row) for path in files)]
    assert not stale, (
        f"stale rows in the exclusion table that match no file: {stale} -- delete them -- ADR-DOE-ENFORCE-001 R8"
    )


def _declares_pytest_config(directory: Path) -> str | None:
    """The name of the config file, if the directory has a setting that pytest's rootdir determination reads as an ini."""
    for name in ("pytest.ini", ".pytest.ini"):
        if (directory / name).is_file():
            return name
    pyproject = directory / "pyproject.toml"
    if pyproject.is_file():
        tool = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {})
        if "pytest" in tool:
            return "pyproject.toml [tool.pytest]"
    tox = directory / "tox.ini"
    if tox.is_file() and re.search(r"^\[pytest\]", tox.read_text(encoding="utf-8"), re.M):
        return "tox.ini [pytest]"
    setup_cfg = directory / "setup.cfg"
    if setup_cfg.is_file() and re.search(
        r"^\[tool:pytest\]", setup_cfg.read_text(encoding="utf-8"), re.M
    ):
        return "setup.cfg [tool:pytest]"
    return None


def test_no_package_declares_its_own_pytest_ini() -> None:
    """The rootdir of `pytest packages/<p>/tests` is the repo root, and the root ini and conftest apply.

    If a package (or its tests) has its own pytest configuration, rootdir moves there and the root ini
    (markers, timeout, etc.) and root conftest (deadline, acceptance) stop applying to the package population,
    and failure names stop being relative to the repo root (a side effect found by blind A).
    """
    declared = {
        str(directory.relative_to(REPO_ROOT)): found
        for package in _expected_packages(REPO_ROOT)
        for directory in (
            REPO_ROOT / "packages",
            REPO_ROOT / "packages" / package,
            REPO_ROOT / "packages" / package / "tests",
        )
        if (found := _declares_pytest_config(directory)) is not None
    }
    assert not declared, (
        f"a package population path has its own pytest configuration: {declared}"
        " -- move it to the root pyproject -- ADR-DOE-ENFORCE-001 R8"
    )


def test_every_daily_stage_declares_whether_it_runs_pytest() -> None:
    """In the daily full verification, a processing stage with the field pytest = true counts as a coverage gap if no session answers, and a stage without the field records
    "pytest run not declared". The runner does not refuse, so a forgotten field is turned red by this repo's declaration test."""
    full = tomllib.loads((REPO_ROOT / ".agents" / "land-queue.toml").read_text(encoding="utf-8"))["gate"]["full"]
    assert isinstance(full, list) and full, f"[gate] full is not a list of processing stages (a single string cannot carry the field pytest): {full!r}"
    missing = [stage.get("name") for stage in full if not isinstance(stage.get("pytest"), bool)]
    assert not missing, f"[gate] full processing stages {missing} lack the field pytest (true or false)"


# The hook for the list of seconds that the daily pytest session prints at the end -- prints tests of 10 seconds or more with no cap on the count.
# With a cap (the old --durations=20), when more than 20 tests over 30 seconds exist in a package the rest do not appear in the log.
_DAILY_DURATIONS_FLAGS = ("--durations=0", "--durations-min=10")


def _stage_run(name: str) -> str:
    full = tomllib.loads((REPO_ROOT / ".agents" / "land-queue.toml").read_text(encoding="utf-8"))["gate"]["full"]
    return next(stage["run"] for stage in full if stage.get("name") == name)


def test_root_stage_lists_every_slow_test() -> None:
    """The pytest command of processing stage root prints tests of 10 seconds or more with no cap (leaving per-test seconds in the daily log)."""
    segments = [shlex.split(part) for part in _stage_run("root").split("&&")]
    pytest_args = [words[words.index("pytest") + 1 :] for words in segments if "pytest" in words]
    assert len(pytest_args) == 1, f"processing stage root does not have exactly one pytest command: {_stage_run('root')}"
    missing = [flag for flag in _DAILY_DURATIONS_FLAGS if flag not in pytest_args[0]]
    assert not missing, f"the pytest of processing stage root lacks {missing} -- tests over 30 seconds would not appear in the daily log: {pytest_args[0]}"


def test_make_test_packages_lists_every_slow_test(packages_round: _PackagesRound) -> None:
    """The shape of processing stage packages (passing only PACKAGE_UV_RUN to `make test-packages`): every package session
    prints tests of 10 seconds or more with no cap. Look at the arguments the fake runner received (the round is read together with the test above -- red is only the first package, and
    the test above checks that the loop visits all of them)."""
    assert "make test-packages PACKAGE_UV_RUN=" in _stage_run("packages"), _stage_run("packages")
    calls, output = packages_round.made.calls, packages_round.made.output
    assert not packages_round.made.timed_out, (
        f"make test-packages did not finish in 120 seconds\n{output[-2000:]}"
    )
    assert calls, f"make test-packages never called the runner\n{output[-2000:]}"
    short = [
        call.argv for call in calls if not all(flag in call.argv for flag in _DAILY_DURATIONS_FLAGS)
    ]
    assert not short, (
        f"{len(short)} / {len(calls)} package sessions' pytest lack {list(_DAILY_DURATIONS_FLAGS)}"
        f" (Makefile's PACKAGE_PYTEST_DURATIONS) -- tests over 30 seconds would not appear in the daily log: {short[:3]}"
    )
