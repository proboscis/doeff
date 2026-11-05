#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Changed-tests check — the entry point to run locally before registering (`ai land request`).

The commit hook does not run tests (operator #1122, #794). This entry point is run by the author with `make test-changed` before registering,
and sits alongside the daily run. Selection and cutoff are defined in this file, in these points:

1. Changed files = the diff from the branch point (default `git merge-base HEAD origin/main`) to the working tree (both committed and
   uncommitted, including new untracked files). Tests run against the working tree contents, not the index (a partially staged commit would diverge).
2. Matching contract sets = among the `[[tool.doeff.contract-tests]]` rows of the root pyproject.toml (path prefix -> list of tests),
   the tests of rows whose prefix matches a changed file. The one reader of the table is `read_contract_table` here, which stops (rc 2) when
   the shape is wrong or a listed test file is missing. Stub cross-checks, dogfood registration and population checks hold modules as strings or walk the tree,
   so they cannot be selected by reverse import dependencies — hence a fixed set, placed first. For each package (uv workspace member) the table has either a set or
   a "reason no set is needed" (a `reason` row). A package with neither is named by `table_gaps` and turned red by the check that runs in the daily run and in the
   whole-repo row of the table (tests/test_contract_table_follows_packages.py).
3. Changed test files themselves (whether a file is a test follows the same naming rules as pytest's collectors — the root ini's python_files,
   doeff_hy_test_files, doeff_adr_hy_files and doeff-adr's DEFAULT_FILE_PATTERNS). However, files under `[tool.doeff] record-dirs`
   (design record dirs) of the root pyproject.toml are not counted as tests even if they match the naming rules (neither in 3 nor in filtering the answer of 4).
4. Reverse-dependency tests = test files that import (directly or indirectly; Hy also via require) a changed file are asked of doeff-linter's
   `--affected-tests` (second pass) and appended afterwards, nearest distance first (same distance in path order). The test-file naming rules are passed
   with `--test-pattern` using the same values as 3, and the answer is filtered by the same judgment as 3. A module with too many direct users (the shape of `doeff-hy.macros` and
   `doeff/__init__.py`) is not traversed by the linter, which returns its name, so it is named as "reverse dependencies too broad to follow". When the linter is missing or
   its answer is unreadable, no reverse dependencies are added and "reverse dependencies could not be measured" is named (not silently empty; kept separate from red).
   The linter command is decided in this order: what was passed with `--linter` -> the linter keyed by the working tree's build inputs (the same lookup as the commit hook,
   `which` of scripts/doeff_linter_locked.py; it does not build — a conversation Pod has no doeff-linter on PATH, only in the
   snapshot store prepared for the hook) -> doeff-linter on PATH.
5. The selected tests are run in separate pytest (`-m "not e2e"`) processes per the same pytest session boundary as the daily run (repo root, `packages/<name>/tests` per package, and
   roots outside a package's tests/ = the Makefile's PACKAGE_EXTRA_TEST_ROOTS). For the same reason that the daily
   `make test-packages` splits sessions per package, mixing tests of packages that have `__init__.py` and `conftest.py` in `tests/` into one session makes the `tests.conftest` names collide (ImportPathMismatchError), and pytest
   stops without running anything (6 of 12 commits in the #2658 sample). The order is bundled per session within each group (contract set -> changed tests ->
   reverse dependencies), and adjacent bundles of the same session become one process. Bundles run in order, passing the remainder of the total budget
   (default 60 seconds) to the next bundle — tests in a bundle that never got a turn are named "unmeasured". The budget does not count pytest's configure phase (the root
   conftest takes the machine's test slot — it waits if full); the uncounted seconds are shown on a separate line of the summary (the promise in the slot's source of truth
   hold_check_slot, "waiting is not counted against the 60 seconds of changed-scope verification"). Heartbeats are written to a pipe by ReportPlugin.

Output has three categories: ran (green), red, unmeasured. Files that did not finish, files with 0 collected tests, and a working tree without a venv are named
"unmeasured", separate from red. rc is 1 if any red, 2 if the table or inputs cannot be read, otherwise 0 (unmeasured does not block registration — it is only named).
However it ends, the last line of output is a single counts-only summary line (`land-focus-counts: selected=… ran=… red=… unmeasured=…
unmeasured_reverse=… rc=… seconds=… budget=…`, same spelling as the agora entry point) — for readers who count a day's unmeasured as real numbers.
No list of test names is included. This entry point writes neither to a file nor to a record service (the delivery channel is held separately).

pytest results are received as per-line JSON by passing this same file's ReportPlugin as a pytest plugin
— so even when cut off, the outcomes of tests finished so far remain. stdlib only (invoked via `uv run --script`; the machine's python is not used).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from itertools import groupby
from pathlib import Path
from typing import Protocol

import tomllib

#: Environment variable pointing to the uv environment location (the entry point reads it once — RunEnvironment).
UV_PROJECT_ENVIRONMENT = "UV_PROJECT_ENVIRONMENT"
#: Environment variables not passed to the pytest child process (the outer `uv run --script` environment is not passed).
CHILD_DROPPED_ENV = frozenset({"VIRTUAL_ENV"})
#: pytest's default python_files (if the root ini sets them, those are read instead).
PYTEST_DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")
#: Source of the default patterns of doeff-adr's executable ADR (read here via ast — no second list is written).
ADR_PLUGIN_SOURCE = "packages/doeff-adr/src/doeff_adr/_pytest_plugin.py"
ADR_PATTERNS_NAME = "DEFAULT_FILE_PATTERNS"
#: Default location of the uv project environment (when UV_PROJECT_ENVIRONMENT is absent). If absent nothing can be measured — named unmeasured
#: (`uv run --no-sync` would create an empty environment if none exists, so it is checked before running).
DEFAULT_PROJECT_ENVIRONMENT = ".venv"
#: Hook that runs pytest with this file's ReportPlugin passed in (the entry point calls it from the `uv run --project` environment;
#: the next argument is the number of the pipe write end that receives the outcomes).
PYTEST_MODE = "--as-pytest"
DEFAULT_BUDGET_SECONDS = 60.0
#: Grace seconds from SIGTERM to SIGKILL after the budget.
KILL_GRACE_SECONDS = 3.0
#: Keys of a table row: either a contract set (prefix and list of tests) or an exemption for a package that needs no set (prefix and reason).
CONTRACT_ROW_KEYS = frozenset({"prefix", "tests"})
EXEMPTION_ROW_KEYS = frozenset({"prefix", "reason"})
#: pytest rcs that are tool errors rather than test outcomes (3 = internal error, 4 = command-line error) — stop instead of folding into unmeasured.
PYTEST_TOOL_FAILURES = frozenset({3, 4})
#: Last-resort command for the linter asked about reverse dependencies (doeff-linter on PATH — installed the same way as `make lint-doeff`).
DEFAULT_LINTER = "doeff-linter"
#: Entry point that locates the linter keyed by the working tree (source of the lookup used by the commit hook and the daily lint stage — no second lookup is written).
LOCKED_LINTER_SCRIPT = Path(__file__).resolve().with_name("doeff_linter_locked.py")
#: Upper limit in seconds for the command that locates the keyed linter (just a few git calls and a walk of the store — counted separately from the 60-second test budget).
LOCATE_TIMEOUT_SECONDS = 30.0
#: Upper limit in seconds to wait for the linter's answer (about 0.1 s with a warm doeff cache, about 0.2 s cold — counted separately from the 60-second test budget).
LINTER_TIMEOUT_SECONDS = 30.0
#: Makefile target that names the test roots outside a package's tests/ (source of the daily population roots — no second list is written).
EXTRA_ROOTS_TARGET = "print-package-extra-test-roots"
#: Session of the repo root (tests that fall under neither a package's tests/ nor the roots outside it — same as the daily root `pytest`).
ROOT_SESSION = "."
#: Head of the last line of the entry point's output (the counts-only summary) — same spelling as agora's pre-registration entry point (scripts/land_focus_gate.hy).
COUNTS_PREFIX = "land-focus-counts:"
#: Upper limit in seconds for asking the Makefile for the session roots (just echoes one variable — counted separately from the 60-second test budget).
MAKE_TIMEOUT_SECONDS = 30.0


class StopError(Exception):
    """The table, settings or git answer cannot be read — stop instead of falling back to a default (rc 2)."""


# ---------------------------------------------------------------------------
# Table of contract tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractRow:
    """One table row: a path prefix (empty = whole repo) and the list of tests that must run when it matches."""

    prefix: str
    tests: tuple[str, ...]

    def covers(self, path: str) -> bool:
        return self.prefix in {"", path} or (
            self.prefix.endswith("/") and path.startswith(self.prefix)
        )


@dataclass(frozen=True)
class ContractExemption:
    """One table row: a package that needs no contract set (prefix = that package's dir) and the reason it is not needed (a non-empty sentence).
    A row so that nothing is dropped silently — an exemption without a reason, or for a prefix that is not a package, stops at read time."""

    prefix: str
    reason: str


@dataclass(frozen=True)
class ContractTable:
    rows: tuple[ContractRow, ...]
    exemptions: tuple[ContractExemption, ...]


@dataclass(frozen=True)
class PackageUnits:
    """The units covered by the contract table — the dirs of root `[tool.uv.workspace]` members (prefixes of the form `packages/<name>/`, in name order).

    Every prefix of the per-package rows in the table is exactly a workspace member dir (a unit with a public shape, stubs and its own pyproject),
    so the same unit is counted. It reads the workspace declaration and writes no second list."""

    prefixes: tuple[str, ...]


@dataclass(frozen=True)
class TableGaps:
    """Drift between table and tree — prefixes (in name order) of packages that have neither a contract set nor an exemption with a reason."""

    uncovered: tuple[str, ...]


TableRow = ContractRow | ContractExemption


@dataclass(frozen=True)
class WorkspaceGlobs:
    """One list of globs (members or exclude) of `[tool.uv.workspace]`."""

    globs: tuple[str, ...]


def _test_path(test: str) -> str:
    """The path part of a test spec (`path` or `path::name`)."""
    return test.split("::", 1)[0]


def _workspace_globs(workspace: dict[str, object], key: str) -> WorkspaceGlobs:
    """Read one key of the glob lists of `[tool.uv.workspace]` (a missing key is an empty list — same meaning as uv; stop unless it is a list of strings)."""
    match workspace.get(key, []):
        case list() as values if all(isinstance(v, str) for v in values):
            return WorkspaceGlobs(globs=tuple(values))
        case other:
            raise StopError(f"{key} of [tool.uv.workspace] is not a list of strings: {other!r}")


def workspace_packages(repo: Path) -> PackageUnits:
    """From the dirs matching the members globs of root pyproject.toml's `[tool.uv.workspace]`, remove those matching exclude,
    and take those holding a pyproject.toml (what uv reads as a member) as package units. A repo with no workspace declaration has no
    members (same meaning as uv). A dir without pyproject.toml (the untracked remains of a retired crate) is not a package."""
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    match config.get("tool", {}).get("uv", {}).get("workspace"):
        case None:
            return PackageUnits(prefixes=())
        case dict() as workspace:
            pass
        case other:
            raise StopError(f"[tool.uv.workspace] is not a table: {other!r}")
    excluded = {
        path
        for pattern in _workspace_globs(workspace, "exclude").globs
        for path in repo.glob(pattern)
    }
    members = {
        path
        for pattern in _workspace_globs(workspace, "members").globs
        for path in repo.glob(pattern)
        if path.is_dir() and path not in excluded and (path / "pyproject.toml").is_file()
    }
    return PackageUnits(
        prefixes=tuple(sorted(f"{path.relative_to(repo).as_posix()}/" for path in members))
    )


def _parse_exemption(where: str, raw: dict[str, object], units: PackageUnits) -> ContractExemption:
    """Read an exemption row (prefix and reason) — stop on an empty reason or a prefix that is not a package unit."""
    match raw["prefix"], raw["reason"]:
        case str() as prefix, str() as reason if reason.strip():
            pass
        case _:
            raise StopError(
                f"{where}: an exemption row needs prefix as a string and reason as a non-empty sentence: {raw!r}"
            )
    if prefix not in units.prefixes:
        raise StopError(
            f"{where}: exemption prefix {prefix!r} is not a package (a uv workspace member dir)"
            f" — write exemptions per package (packages: {list(units.prefixes)})"
        )
    return ContractExemption(prefix=prefix, reason=reason.strip())


def _parse_row(index: int, raw: object, repo: Path, units: PackageUnits) -> TableRow:
    where = f"row {index + 1} of [[tool.doeff.contract-tests]]"
    match raw:
        case dict() if set(raw) == CONTRACT_ROW_KEYS:
            pass
        case dict() if set(raw) == EXEMPTION_ROW_KEYS:
            return _parse_exemption(where, raw, units)
        case dict():
            raise StopError(
                f"{where}: keys must be {sorted(CONTRACT_ROW_KEYS)} (contract set) or {sorted(EXEMPTION_ROW_KEYS)}"
                f" (exemption with a reason for a package that needs no set) only (actual {sorted(raw)})"
            )
        case _:
            raise StopError(f"{where}: not a table (prefix and tests, or prefix and reason): {raw!r}")
    match raw["prefix"], raw["tests"]:
        case str() as prefix, list() as tests if tests and all(isinstance(t, str) for t in tests):
            pass
        case _:
            raise StopError(f"{where}: prefix must be a string and tests a non-empty list of strings: {raw!r}")
    if prefix and not (
        (prefix.endswith("/") and (repo / prefix).is_dir())
        or (not prefix.endswith("/") and (repo / prefix).is_file())
    ):
        raise StopError(
            f"{where}: prefix {prefix!r} is missing from the tree (end a dir with `/`; write a file by its exact name)"
        )
    missing = [t for t in tests if not (repo / _test_path(t)).is_file()]
    if missing:
        raise StopError(f"{where} (prefix {prefix!r}): listed test file is missing from the tree: {missing}")
    if len(set(tests)) != len(tests):
        raise StopError(f"{where} (prefix {prefix!r}): the same test is written twice: {tests}")
    return ContractRow(prefix=prefix, tests=tuple(tests))


def read_contract_table(repo: Path) -> ContractTable:
    """Read `[[tool.doeff.contract-tests]]` of the root pyproject.toml (this is the one reader). Prefixes of contract rows and exemption rows
    must not overlap (writing both a set and an exemption for one package would silently decide which is true)."""
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        raise StopError(f"{pyproject} is missing")
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    match config.get("tool", {}).get("doeff", {}).get("contract-tests"):
        case list() as raw_rows if raw_rows:
            pass
        case None:
            raise StopError(f"{pyproject} has no [[tool.doeff.contract-tests]] table")
        case other:
            raise StopError(f"[[tool.doeff.contract-tests]] must be a non-empty list of tables: {other!r}")
    units = workspace_packages(repo)
    parsed = tuple(_parse_row(i, raw, repo, units) for i, raw in enumerate(raw_rows))
    prefixes = [row.prefix for row in parsed]
    duplicated = sorted({p for p in prefixes if prefixes.count(p) > 1})
    if duplicated:
        raise StopError(f"[[tool.doeff.contract-tests]] has two or more rows with the same prefix: {duplicated}")
    return ContractTable(
        rows=tuple(row for row in parsed if isinstance(row, ContractRow)),
        exemptions=tuple(row for row in parsed if isinstance(row, ContractExemption)),
    )


def table_gaps(table: ContractTable, units: PackageUnits) -> TableGaps:
    """Package units that have, in the table, neither a contract set (a row whose prefix is exactly that package's dir) nor an exemption with a reason.
    The whole-repo row ("") and rows for dirs inside a package are not counted as that package's set — only rows that decide,
    per package, "what verifies that package's whole public shape" are counted. A pure function (the caller passes the table and units it read)."""
    named = {row.prefix for row in table.rows} | {row.prefix for row in table.exemptions}
    return TableGaps(uncovered=tuple(p for p in units.prefixes if p not in named))


# ---------------------------------------------------------------------------
# Naming rules for test files (reads the same values as pytest's collectors)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestFilePatterns:
    """List of naming rules for test files (same values as pytest's collectors — the same values are also passed to the linter asked about reverse dependencies).

    record_dirs = design record dirs (`[tool.doeff] record-dirs` of the root pyproject.toml). Records are evidence at that point in time; even if
    a file with a test name exists there, it is not a test — files under these are not selected as tests even if they match the naming rules (conversely, the daily population check turns
    test-named files under these red and asks for a rename to `.py.txt`). Not passed to the linter; used on the side that filters the answer."""

    globs: tuple[str, ...]
    record_dirs: tuple[str, ...] = ()


def _adr_default_patterns(repo: Path) -> TestFilePatterns:
    """Read the default naming rules of executable ADRs from their source of truth, the doeff-adr source (so no second list is written)."""
    source = repo / ADR_PLUGIN_SOURCE
    if not source.is_file():
        raise StopError(f"{ADR_PLUGIN_SOURCE}, the source of the executable ADR patterns, is missing")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    values = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and [t.id for t in node.targets if isinstance(t, ast.Name)] == [ADR_PATTERNS_NAME]
    ]
    match values:
        case [value]:
            pass
        case _:
            raise StopError(f"{ADR_PLUGIN_SOURCE} does not have exactly one assignment of {ADR_PATTERNS_NAME}")
    try:
        patterns = ast.literal_eval(value)
    except ValueError as exc:
        raise StopError(f"{ADR_PATTERNS_NAME} in {ADR_PLUGIN_SOURCE} is not a literal") from exc
    match patterns:
        case tuple() if all(isinstance(p, str) for p in patterns):
            return TestFilePatterns(globs=patterns)
        case _:
            raise StopError(f"{ADR_PATTERNS_NAME} is not a tuple of strings: {patterns!r}")


def _ini_strings(ini: dict[str, object], key: str, default: tuple[str, ...]) -> TestFilePatterns:
    """Read one naming-rule key of the root ini (stop instead of falling back to the default unless it is a list of strings)."""
    match ini.get(key, default):
        case list() | tuple() as values if all(isinstance(v, str) for v in values):
            return TestFilePatterns(globs=tuple(values))
        case other:
            raise StopError(f"{key} in [tool.pytest.ini_options] is not a list of strings: {other!r}")


def _record_dirs(config: dict[str, object]) -> tuple[str, ...]:
    """`[tool.doeff] record-dirs` of the root pyproject.toml (dirs end with `/`; empty if absent). A wrong shape stops instead of falling back to the default."""
    tool = config.get("tool", {})
    raw = tool.get("doeff", {}).get("record-dirs", []) if isinstance(tool, dict) else []
    match raw:
        case list() if all(isinstance(d, str) and d.endswith("/") for d in raw):
            return tuple(raw)
        case other:
            raise StopError(f"record-dirs in [tool.doeff] is not a list of dir strings ending with `/`: {other!r}")


def collector_patterns(repo: Path) -> TestFilePatterns:
    """Naming rules of the three routes pytest collects (Python and Hy tests, executable ADR), and the design record dirs where no tests are placed."""
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    ini = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    parts = (
        _ini_strings(ini, "python_files", PYTEST_DEFAULT_PYTHON_FILES),
        _ini_strings(ini, "doeff_hy_test_files", ()),
        _adr_default_patterns(repo),
        _ini_strings(ini, "doeff_adr_hy_files", ()),
    )
    return TestFilePatterns(
        globs=tuple(glob for part in parts for glob in part.globs), record_dirs=_record_dirs(config)
    )


def is_test_file(path: str, patterns: TestFilePatterns) -> bool:
    """Same as pytest's matching: a pattern without `/` is matched against the file name, one with `/` against the path from the repo root.
    Files under design record dirs are not tests even if they match the naming rules (an unrenamable measurement script inside a record was selected by a change to its import target
    and became a "failed while collecting" red — blind review A of the design verification)."""
    name = path.rsplit("/", 1)[-1]
    return (
        "fixtures" not in path.split("/")
        and not path.startswith(patterns.record_dirs)
        and any(fnmatch.fnmatchcase(path if "/" in p else name, p) for p in patterns.globs)
    )


# ---------------------------------------------------------------------------
# Changed files and selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeSet:
    base: str
    files: tuple[str, ...]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise StopError(f"git {' '.join(args)} returned rc {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def changed_files(repo: Path, base: str | None) -> ChangeSet:
    """Files changed from the branch point to the working tree (including deletions; for renames both old and new names) plus new untracked files."""
    resolved = base if base is not None else _git(repo, "merge-base", "HEAD", "origin/main").strip()
    sha = _git(repo, "rev-parse", "--verify", f"{resolved}^{{commit}}").strip()
    tracked = _git(repo, "diff", "--name-only", "--no-renames", sha).splitlines()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return ChangeSet(base=sha, files=tuple(sorted({*tracked, *untracked} - {""})))


class Origin(Enum):
    CONTRACT = "contract set"
    CHANGED = "changed test"
    REVERSE = "reverse dependency"


@dataclass(frozen=True)
class Target:
    """One argument to pass to a pytest run, and the reason it was selected."""

    arg: str
    origin: Origin
    why: str


@dataclass(frozen=True)
class Selection:
    """The sequence of arguments to pass to pytest (run from the front; whatever is cut off by the budget behind becomes unmeasured)."""

    targets: tuple[Target, ...]


def _unique(targets: tuple[Target, ...]) -> Selection:
    """Never pass the same argument twice (keep the reason it was first selected)."""
    return Selection(
        targets=tuple(t for i, t in enumerate(targets) if t.arg not in {u.arg for u in targets[:i]})
    )


def select_targets(
    table: ContractTable, changes: ChangeSet, patterns: TestFilePatterns, repo: Path
) -> Selection:
    """Contract sets (in table order) first, changed test files (in name order) after — each argument only once."""
    contract = [
        Target(arg=test, origin=Origin.CONTRACT, why=f"prefix {row.prefix or '(whole repo)'}")
        for row in table.rows
        if any(row.covers(path) for path in changes.files)
        for test in row.tests
    ]
    changed = [
        Target(arg=path, origin=Origin.CHANGED, why="changed test file")
        for path in changes.files
        if is_test_file(path, patterns) and (repo / path).is_file()
    ]
    return _unique((*contract, *changed))


# ---------------------------------------------------------------------------
# Reverse dependencies (doeff-linter's `--affected-tests`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AffectedTest:
    """One test file the linter selected (the number of edges from the changed file, and the shortest path)."""

    path: str
    distance: int
    via: tuple[str, ...]


@dataclass(frozen=True)
class Hub:
    """A module the linter did not traverse because it has too many direct users."""

    module: str
    root: str
    dependents: int


@dataclass(frozen=True)
class Affected:
    """The linter's answer (measured)."""

    tests: tuple[AffectedTest, ...]
    hubs: tuple[Hub, ...]
    unreadable: tuple[str, ...]


@dataclass(frozen=True)
class AffectedUnmeasured:
    """Reverse dependencies could not be measured (linter missing, stopped, or its answer unreadable) — the reason is named."""

    reason: str


AffectedAnswer = Affected | AffectedUnmeasured


def _parse_affected_test(raw: object) -> AffectedTest:
    """Read one of the tests in the answer (ValueError if the shape is wrong — so that the whole answer becomes "could not be measured")."""
    match raw:
        case {"path": str() as path, "distance": int() as distance, "via": list() as via} if all(
            isinstance(step, str) for step in via
        ):
            return AffectedTest(path=path, distance=distance, via=tuple(via))
        case _:
            raise ValueError(f"the shape of one of tests is wrong: {raw!r}")


def _parse_hub(raw: object) -> Hub:
    """Read one of the hubs in the answer (so that modules not traversed for being too broad are named in the summary)."""
    match raw:
        case {"module": str() as module, "root": str() as root, "dependents": int() as dependents}:
            return Hub(module=module, root=root, dependents=dependents)
        case _:
            raise ValueError(f"the shape of one of hubs is wrong: {raw!r}")


def parse_affected(text: str) -> AffectedAnswer:
    """Read the JSON of `--affected-tests` — if the shape is wrong, treat it as "could not be measured" instead of falling back to a default."""
    try:
        match json.loads(text):
            case {
                "tests": list() as tests,
                "hubs": list() as hubs,
                "unreadable": list() as unreadable,
            }:
                return Affected(
                    tests=tuple(_parse_affected_test(t) for t in tests),
                    hubs=tuple(_parse_hub(h) for h in hubs),
                    unreadable=tuple(_parse_unreadable(u) for u in unreadable),
                )
            case other:
                return AffectedUnmeasured(f"the shape of the linter's answer is wrong: {str(other)[:200]}")
    except (json.JSONDecodeError, ValueError) as exc:
        return AffectedUnmeasured(f"linter answer unreadable: {exc}")


def _parse_unreadable(raw: object) -> str:
    """Path of a file whose dependencies could not be read (counted in the summary as a place where selection may have missed something)."""
    match raw:
        case {"path": str() as path}:
            return path
        case _:
            raise ValueError(f"the shape of one of unreadable is wrong: {raw!r}")


@dataclass(frozen=True)
class LinterCommand:
    """The command of the linter asked about reverse dependencies, and the reason to name when that command cannot be executed (including the paths searched)."""

    command: str
    absent: str


@dataclass(frozen=True)
class LockedLinter:
    """The linter keyed by the working tree's build inputs (path of the binary found in the store)."""

    binary: str


@dataclass(frozen=True)
class LockedLinterAbsent:
    """The keyed linter could not be found — a reason naming the store searched and the key."""

    reason: str


LockedAnswer = LockedLinter | LockedLinterAbsent


def locate_locked_linter(repo: Path) -> LockedAnswer:
    """Look for the linter keyed by the working tree's HEAD build inputs with the same lookup as the commit hook (`doeff_linter_locked.py which`).
    It does not build (a cold build takes 3-5 minutes and risks OOM in a conversation Pod — if absent, the caller names it as "could not be measured")."""
    try:
        proc = subprocess.run(
            [sys.executable, str(LOCKED_LINTER_SCRIPT), "which"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=LOCATE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return LockedLinterAbsent(f"could not look for the keyed linter: {exc}")
    binary = proc.stdout.strip()
    if proc.returncode == 0 and binary:
        return LockedLinter(binary)
    last = (proc.stderr.strip().splitlines() or [f"rc {proc.returncode}"])[-1]
    return LockedLinterAbsent(last[:600])


def pick_linter(explicit: str | None, repo: Path) -> LinterCommand:
    """Decide the linter command: what was passed with `--linter` -> the linter keyed by the working tree (no build) -> doeff-linter on PATH.
    The last two fall to PATH only when there is no keyed linter — so that, if that command is missing too, every path searched can be named."""
    if explicit is not None:
        return LinterCommand(
            command=explicit, absent=f"linter {explicit!r} not found (the command passed with --linter)"
        )
    match locate_locked_linter(repo):
        case LockedLinter(binary=binary):
            return LinterCommand(
                command=binary, absent=f"linter {binary!r} not found (it vanished from the keyed linter's store)"
            )
        case LockedLinterAbsent(reason=reason):
            return LinterCommand(
                command=DEFAULT_LINTER,
                absent=f"linter not found — {reason}; {DEFAULT_LINTER!r} is not on PATH either (pass one with --linter)",
            )


def ask_affected(
    linter: LinterCommand, repo: Path, changes: ChangeSet, patterns: TestFilePatterns
) -> AffectedAnswer:
    """Ask the linter's `--affected-tests` with the changed files and the test-file naming rules (same values as 3) (the only real I/O is here)."""
    command = [
        linter.command,
        "--root",
        str(repo),
        "--affected-tests",
        *changes.files,
        *(arg for pattern in patterns.globs for arg in ("--test-pattern", pattern)),
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=LINTER_TIMEOUT_SECONDS, check=False
        )
    except FileNotFoundError:
        return AffectedUnmeasured(linter.absent)
    except subprocess.TimeoutExpired:
        return AffectedUnmeasured(f"linter did not answer within {LINTER_TIMEOUT_SECONDS:g} seconds")
    if proc.returncode != 0:
        last = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
        return AffectedUnmeasured(f"linter stopped with rc {proc.returncode} — {last[:300]}")
    return parse_affected(proc.stdout)


def extend_with_reverse(
    selection: Selection, answer: AffectedAnswer, patterns: TestFilePatterns, repo: Path
) -> Selection:
    """Append reverse-dependency tests after the chosen sets, nearest distance first (same distance in path order) — arguments already present are not added.
    Whether something is a test file is filtered by the same judgment as 3. An answer that could not be measured adds nothing (named in the summary)."""
    match answer:
        case AffectedUnmeasured():
            return selection
        case Affected(tests=tests):
            pass
    reverse = tuple(
        Target(
            arg=test.path,
            origin=Origin.REVERSE,
            why=f"distance {test.distance}: {' → '.join(test.via)}",
        )
        for test in sorted(tests, key=lambda t: (t.distance, t.path))
        if is_test_file(test.path, patterns) and (repo / test.path).is_file()
    )
    return _unique((*selection.targets, *reverse))


# ---------------------------------------------------------------------------
# pytest session boundaries (same as the daily run)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRoots:
    """Among the daily session boundaries, the test roots outside a package's tests/ (the Makefile's PACKAGE_EXTRA_TEST_ROOTS, as paths from the root)."""

    extra: tuple[str, ...]


@dataclass(frozen=True)
class Batch:
    """The sequence of tests passed to one pytest process — session = the session root of those tests (one of ROOT_SESSION, `packages/<name>/tests`, or
    a root outside tests/)."""

    session: str
    targets: tuple[Target, ...]


def read_session_roots(repo: Path) -> SessionRoots:
    """Ask the source of truth of the daily population (the Makefile target) for the test roots outside a package's tests/ (the only real I/O is here). Stop
    if it cannot be read — silently making it 0 roots would mix tests of those roots into the repo root session and run them in a session different from the daily run."""
    try:
        proc = subprocess.run(
            ["make", "-s", "--no-print-directory", "-C", str(repo), EXTRA_ROOTS_TARGET],
            capture_output=True,
            text=True,
            timeout=MAKE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StopError(f"cannot run make {EXTRA_ROOTS_TARGET}: {exc}") from exc
    if proc.returncode != 0:
        raise StopError(f"make {EXTRA_ROOTS_TARGET} returned rc {proc.returncode}: {proc.stderr.strip()}")
    return SessionRoots(extra=tuple(root.rstrip("/") for root in proc.stdout.split()))


def session_of(arg: str, roots: SessionRoots) -> str:
    """The session root in which one test argument runs — same as the daily boundary: under a root outside tests/ it is that root; under `packages/<name>/tests/` it is
    that package's tests; any other test goes to the repo root (the daily root `pytest`)."""
    path = _test_path(arg)
    extra = next((root for root in roots.extra if path.startswith(f"{root}/")), None)
    if extra is not None:
        return extra
    match path.split("/"):
        case ["packages", name, "tests", _, *_]:
            return f"packages/{name}/tests"
        case _:
            return ROOT_SESSION


def batches_of(selection: Selection, roots: SessionRoots) -> tuple[Batch, ...]:
    """Split the selected sequence into process bundles while keeping the group order (contract set -> changed tests -> reverse dependencies). Within a group, bundle
    per session (session order is the order first seen in the group; within a bundle, the selection order), and adjacent bundles of the same session become one process.
    A pure function (the caller reads and passes the session roots)."""
    keyed = tuple((t, session_of(t.arg, roots)) for t in selection.targets)
    per_origin = tuple(
        Batch(
            session=session,
            targets=tuple(t for t, s in keyed if t.origin is origin and s == session),
        )
        for origin in Origin
        for session in dict.fromkeys(s for t, s in keyed if t.origin is origin)
    )
    return tuple(
        Batch(session=session, targets=tuple(t for batch in group for t in batch.targets))
        for session, group in groupby(per_origin, key=lambda batch: batch.session)
    )


# ---------------------------------------------------------------------------
# pytest outcomes (per-line JSON written by the plugin)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Collected:
    nodeids: tuple[str, ...]


@dataclass(frozen=True)
class ItemReport:
    nodeid: str
    when: str
    outcome: str


@dataclass(frozen=True)
class CollectError:
    nodeid: str


@dataclass(frozen=True)
class Configuring:
    """The beat at which pytest's main enters configure — the root conftest takes the machine's test slot (waiting if full) between this beat and
    SessionStarted (pytest_configure in conftest.py)."""


@dataclass(frozen=True)
class SessionStarted:
    """The beat at which all of configure has finished and the session has started (before collection)."""


Event = Collected | ItemReport | CollectError | Configuring | SessionStarted


def _parse_event(line: str) -> Event:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise StopError(f"a line of pytest outcomes is not JSON: {line[:200]!r}") from exc
    match payload:
        case {"kind": "configuring"}:
            return Configuring()
        case {"kind": "session_started"}:
            return SessionStarted()
        case {"kind": "collected", "nodeids": list() as nodeids}:
            return Collected(nodeids=tuple(str(n) for n in nodeids))
        case {
            "kind": "report",
            "nodeid": str() as nodeid,
            "when": str() as when,
            "outcome": str() as outcome,
        }:
            return ItemReport(nodeid=nodeid, when=when, outcome=outcome)
        case {"kind": "collect_error", "nodeid": str() as nodeid}:
            return CollectError(nodeid=nodeid)
        case other:
            raise StopError(f"cannot read a line of pytest outcomes: {other!r}")


class _Item(Protocol):
    nodeid: str


class _Session(Protocol):
    items: list[_Item]


class _Report(Protocol):
    nodeid: str
    when: str | None
    outcome: str
    failed: bool


@dataclass(frozen=True)
class ReportPlugin:
    """The plugin that the hook inside pytest passes to pytest — writes outcomes as one JSON line each to the pipe write end (report_fd) that the entry point passed on the command line
    (each line is closed individually so the lines so far remain even if cut off). The write end is a value read once by the child process's entry point
    (`_main_as_pytest`); environment variables are not read."""

    report_fd: int

    def _write_event(self, payload: dict[str, object]) -> None:
        """Write one outcome to the pipe (in the shape that the entry point's `_parse_event` reads)."""
        os.write(self.report_fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode())

    def pytest_cmdline_main(self, config: object) -> None:
        """Record the beat of entering configure — from here until the session starts (the wait for the machine's test slot), the entry point does not count against the budget.
        Command-line hooks are called first for plugins registered later, and the hook of pytest's main that starts configure and the session is a built-in registered
        before this plugin, so this beat comes before configure (return None and hand over to main)."""
        self._write_event({"kind": "configuring"})

    def pytest_sessionstart(self, session: _Session) -> None:
        """Record the beat at which the session started — from here the entry point re-counts the remainder of the budget."""
        self._write_event({"kind": "session_started"})

    def pytest_collection_finish(self, session: _Session) -> None:
        """Record which tests were collected (after -m deselection) — the denominator for naming tests that did not finish."""
        self._write_event({"kind": "collected", "nodeids": [item.nodeid for item in session.items]})

    def pytest_runtest_logreport(self, report: _Report) -> None:
        """Record the outcome of one stage (setup, call, teardown) of a test — a test whose teardown arrived is counted as "finished"."""
        self._write_event(
            {
                "kind": "report",
                "nodeid": report.nodeid,
                "when": report.when,
                "outcome": report.outcome,
            }
        )

    def pytest_collectreport(self, report: _Report) -> None:
        """Record files that failed while collecting — named as red."""
        if report.failed:
            self._write_event({"kind": "collect_error", "nodeid": report.nodeid})


# ---------------------------------------------------------------------------
# Running and classifying
# ---------------------------------------------------------------------------


class Verdict(Enum):
    GREEN = "ran"
    RED = "red"
    UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class TargetResult:
    target: Target
    verdict: Verdict
    detail: str


@dataclass(frozen=True)
class PytestRun:
    """One bundle that ran — seconds = seconds counted against the budget; waited = seconds of the configure phase not counted (waiting for the machine's test slot)."""

    events: tuple[Event, ...]
    returncode: int | None
    timed_out: bool
    seconds: float
    waited: float


@dataclass(frozen=True)
class NotReached:
    """A bundle for which pytest was not started because a previous bundle used up the total budget."""


BatchRun = PytestRun | NotReached


@dataclass(frozen=True)
class BatchOutcome:
    """One bundle, and how it went (it ran, or it never got a turn)."""

    batch: Batch
    run: BatchRun


def _belongs(nodeid: str, arg: str) -> bool:
    return (
        nodeid == arg
        or nodeid.startswith(arg + "::")
        or ("::" in arg and nodeid.startswith(arg + "["))
    )


def classify(target: Target, outcome: BatchRun, budget: float) -> TargetResult:
    """Classify one test into three (ran, red, unmeasured) from its bundle's run — so tests in a bundle that never got a turn are named unmeasured without mixing them into red."""
    match outcome:
        case NotReached():
            return TargetResult(
                target,
                Verdict.UNMEASURED,
                f"did not get a turn within the {budget:g}s budget (the previous bundle used it up)",
            )
        case PytestRun() as run:
            return _classify_run(target, run, budget)


def _classify_run(target: Target, run: PytestRun, budget: float) -> TargetResult:
    """Classify one test of a bundle that ran — so tests that did not finish collecting or were cut off are named unmeasured without mixing them into red."""
    collected = next((e for e in run.events if isinstance(e, Collected)), None)
    collect_errors = [
        e
        for e in run.events
        if isinstance(e, CollectError) and _belongs(e.nodeid, _test_path(target.arg))
    ]
    if collect_errors:
        return TargetResult(target, Verdict.RED, "failed while collecting")
    if collected is None:
        if run.timed_out:
            reason = f"did not finish collecting within the {budget:g}s budget"
        elif not any(isinstance(e, SessionStarted) for e in run.events):
            reason = (
                f"pytest stopped before starting a session (rc {run.returncode} — the wait for the machine's test slot exceeded the budget,"
                " or a configure error; read the output above)"
            )
        else:
            reason = f"pytest stopped before collecting (rc {run.returncode})"
        return TargetResult(target, Verdict.UNMEASURED, reason)
    items = tuple(n for n in collected.nodeids if _belongs(n, target.arg))
    if not items:
        return TargetResult(
            target, Verdict.UNMEASURED, "0 tests collected (e2e only, or not matched by the collectors)"
        )
    reports = [e for e in run.events if isinstance(e, ItemReport) and e.nodeid in items]
    failed = sorted({r.nodeid for r in reports if r.outcome == "failed"})
    finished = {r.nodeid for r in reports if r.when == "teardown"}
    unfinished = [n for n in items if n not in finished]
    if failed:
        return TargetResult(target, Verdict.RED, f"{len(failed)} red: {', '.join(failed)}")
    if unfinished:
        reason = (
            f"cut off at the {budget:g}s budget"
            if run.timed_out
            else f"pytest stopped midway (rc {run.returncode})"
        )
        return TargetResult(
            target,
            Verdict.UNMEASURED,
            f"{reason} — stopped at {len(items) - len(unfinished)}/{len(items)} tests",
        )
    return TargetResult(target, Verdict.GREEN, f"{len(items)} test(s)")


@dataclass(frozen=True)
class ChildClock:
    """The beats of one pytest process (the entry point's monotonic seconds) — to separate seconds counted against the budget from the uncounted configure phase.

    spawned = the beat it was started; configuring = the beat it entered configure; started = the beat the session started (None if not yet).
    The configure phase is not counted because the root conftest takes the machine's test slot (waiting if full) — the slot's source of truth hold_check_slot
    promises "waiting is not counted against the 60 seconds of changed-scope verification". From start to configure (uv, python,
    conftest import) and from session start onward are counted as before (so a child that stays stuck is also cut by the budget).

    WARNING: An uncounted span is also a span the entry point does not cut by time (_ChildProgress.wait_within waits only for the child's end or a beat while paused).
    So uncounted spans are limited to those whose budget is held by another owner — the slot wait in the configure phase (its upper limit is held at one point by the slot's source of truth, and when exceeded
    pytest ends declaring it did not run). If collection or the test body is added to an uncounted span, nobody cuts a stuck child and the entry point
    never ends (blind review A of the design verification — guarded by test_a_collection_over_the_budget_is_cut_not_excluded).
    When adding a span, first decide who owns that span's budget, and change paused and wait_within together."""

    spawned: float
    configuring: float | None
    started: float | None

    def mark(self, event: Event, at: float) -> ChildClock:
        """If the received outcome line is a configure or session beat, return a clock with that time recorded (other lines leave the clock unchanged)."""
        match event:
            case Configuring() if self.configuring is None:
                return ChildClock(spawned=self.spawned, configuring=at, started=self.started)
            case SessionStarted() if self.started is None:
                return ChildClock(spawned=self.spawned, configuring=self.configuring, started=at)
            case _:
                return self

    @property
    def paused(self) -> bool:
        """Whether in the configure phase — the budget clock is stopped and the entry point does not cut by time (the budget owner is the slot's source of truth — see the class note)."""
        return self.configuring is not None and self.started is None

    def counted(self, now: float) -> float:
        """Seconds counted against the budget — from start to configure, and from session start to now."""
        if self.configuring is None:
            return now - self.spawned
        if self.started is None:
            return self.configuring - self.spawned
        return (self.configuring - self.spawned) + (now - self.started)

    def waited(self, now: float) -> float:
        """Seconds not counted against the budget — the configure phase (including waiting for the machine's test slot)."""
        if self.configuring is None:
            return 0.0
        return (now if self.started is None else self.started) - self.configuring


@dataclass(frozen=True)
class Drained:
    """The outcome of a child whose pipe was fully read — all outcome lines, and the clock with the beats recorded."""

    events: tuple[Event, ...]
    clock: ChildClock


class _ChildProgress:
    """Progress of one pytest process — written by two threads, the pipe reader (outcome lines and beats) and the process waiter (the end), while
    the entry point's thread waits counting the seconds that count against the budget (the beat time is the monotonic of the entry-point side that received the line)."""

    def __init__(self, clock: ChildClock) -> None:
        self._changed = threading.Condition()
        self._mut_clock = clock
        self._mut_events: tuple[Event, ...] = ()
        self._mut_unreadable: StopError | None = None
        self._mut_exited = False
        self._mut_drained = False

    def read(self, fd: int) -> None:
        """Body of the reader thread: read line by line until all writers close the pipe's read end (all outcome lines the plugin writes).
        Remember the first unreadable line and keep reading (if reading stops, the writing child blocks on the full pipe)."""
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    self._receive(line, time.monotonic())
        with self._changed:
            self._mut_drained = True
            self._changed.notify_all()

    def _receive(self, line: str, at: float) -> None:
        """Receive one outcome line; if it is a beat, record it in the clock and wake the waiter."""
        try:
            event = _parse_event(line)
        except StopError as exc:
            with self._changed:
                self._mut_unreadable = self._mut_unreadable or exc
            return
        with self._changed:
            self._mut_events = (*self._mut_events, event)
            self._mut_clock = self._mut_clock.mark(event, at)
            self._changed.notify_all()

    def watch(self, proc: subprocess.Popen[bytes]) -> None:
        """Body of the waiter thread: wait for the process to end and wake the entry point's thread."""
        proc.wait()
        with self._changed:
            self._mut_exited = True
            self._changed.notify_all()

    def wait_within(self, budget: float) -> bool:
        """Wait until the process ends — return False when the seconds counted against the budget reach budget (the caller cuts it off). The configure phase is not counted,
        so wait without a time limit (the slot wait is bounded by the slot's source-of-truth limit; when exceeded pytest ends declaring it did not run).
        WARNING: Do not place the entry point's own limit in this span — doing so moves the decision to give up waiting from the slot's source of truth (the wait-limit .json and the declaration of not having run)
        to the entry point, and the smaller of two limits takes effect (blind review B of the design verification). To change the wait limit,
        change the source-of-truth value."""
        with self._changed:
            while not self._mut_exited:
                if self._mut_clock.paused:
                    self._changed.wait()
                    continue
                left = budget - self._mut_clock.counted(time.monotonic())
                if left <= 0:
                    return False
                self._changed.wait(left)
            return True

    def drain(self, grace: float) -> Drained:
        """After the process ends, wait up to grace seconds for the pipe to be fully read and return the outcomes — stop if it is not fully read (a child the test
        let escape the group holds the write end) or if an unreadable line existed."""
        with self._changed:
            if not self._changed.wait_for(lambda: self._mut_drained, timeout=grace):
                raise StopError(
                    "a process still holds the outcome pipe after pytest ended (a child the test let escape the group)"
                )
            if self._mut_unreadable is not None:
                raise self._mut_unreadable
            return Drained(events=self._mut_events, clock=self._mut_clock)


def _stop_group(proc: subprocess.Popen[bytes]) -> None:
    """Stop a pytest that exceeded the budget together with its process group (SIGTERM, then SIGKILL after the grace)."""
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@dataclass(frozen=True)
class Unset:
    """The environment variable is absent (an empty value is also read as absent, as in uv)."""

    name: str


@dataclass(frozen=True)
class Setting:
    """A non-empty value of an environment variable."""

    name: str
    value: str


EnvironmentSetting = Setting | Unset


@dataclass(frozen=True)
class EnvironmentEntry:
    """One environment variable to pass to the child process."""

    name: str
    value: str


@dataclass(frozen=True)
class RunEnvironment:
    """The environment the entry point read exactly once — functions inside do not read environment variables; they receive this value.

    uv_project_environment = the setting for the uv environment location (UV_PROJECT_ENVIRONMENT).
    child = the environment passed to the pytest child process (excluding the outer `uv run --script`'s VIRTUAL_ENV).
    """

    uv_project_environment: EnvironmentSetting
    child: tuple[EnvironmentEntry, ...]


def _setting(environ: Mapping[str, str], name: str) -> EnvironmentSetting:
    """Read one environment variable as a type: present (non-empty value) or absent."""
    match environ.get(name, ""):
        case "":
            return Unset(name=name)
        case value:
            return Setting(name=name, value=value)


def read_run_environment(environ: Mapping[str, str]) -> RunEnvironment:
    """At the one place of the entry point, read the needed values from the process environment (passed by the caller)."""
    return RunEnvironment(
        uv_project_environment=_setting(environ, UV_PROJECT_ENVIRONMENT),
        child=tuple(
            EnvironmentEntry(name=k, value=v)
            for k, v in sorted(environ.items())
            if k not in CHILD_DROPPED_ENV
        ),
    )


def project_environment(repo: Path, environment: RunEnvironment) -> Path:
    """The location of the environment that uv uses for `uv run --project <repo>` (UV_PROJECT_ENVIRONMENT or `<repo>/.venv`)."""
    match environment.uv_project_environment:
        case Setting(value=value):
            return repo / value
        case Unset():
            return repo / DEFAULT_PROJECT_ENVIRONMENT


def run_pytest(
    repo: Path, targets: tuple[Target, ...], budget: float, environment: RunEnvironment
) -> PytestRun:
    """Run one pytest with a budget in the working tree's uv environment and receive the outcomes the plugin wrote to the pipe (nothing is left in a file).
    The budget is counted excluding the configure phase (waiting for the machine's test slot) — ChildClock. The pipe's write-end number is passed on the child's command line."""
    read_fd, write_fd = os.pipe()
    env = {entry.name: entry.value for entry in environment.child}
    command = [
        "uv",
        "run",
        "--no-sync",
        "--project",
        str(repo),
        "python",
        str(Path(__file__).resolve()),
        PYTEST_MODE,
        str(write_fd),
        "-q",
        "-m",
        "not e2e",
        "--continue-on-collection-errors",
        *(t.arg for t in targets),
    ]
    progress = _ChildProgress(ChildClock(spawned=time.monotonic(), configuring=None, started=None))
    threading.Thread(target=progress.read, args=(read_fd,), daemon=True).start()
    proc = subprocess.Popen(
        command, cwd=repo, env=env, start_new_session=True, pass_fds=(write_fd,)
    )
    os.close(write_fd)
    threading.Thread(target=progress.watch, args=(proc,), daemon=True).start()
    timed_out = not progress.wait_within(budget)
    if timed_out:
        _stop_group(proc)
    ended = time.monotonic()
    returncode = None if timed_out else proc.returncode
    drained = progress.drain(KILL_GRACE_SECONDS)
    events = drained.events
    if not timed_out and (returncode in PYTEST_TOOL_FAILURES or not events):
        raise StopError(
            f"pytest stopped with rc {returncode}{' without returning' if not events else ' after returning'} test outcomes"
            " (a uv, conftest or command-line error) — read the output above"
        )
    return PytestRun(
        events=events,
        returncode=returncode,
        timed_out=timed_out,
        seconds=drained.clock.counted(ended),
        waited=drained.clock.waited(ended),
    )


def _run_batch(
    repo: Path,
    index: int,
    batches: tuple[Batch, ...],
    remaining: float,
    environment: RunEnvironment,
) -> BatchOutcome:
    """Run one bundle in one pytest process with the remainder of the total budget as its limit — do not start it if nothing remains
    (so that bundles behind what the previous bundles used up are named "unmeasured")."""
    batch = batches[index]
    if remaining <= 0:
        return BatchOutcome(batch=batch, run=NotReached())
    print(
        f"== pytest process {index + 1}/{len(batches)}: session {batch.session}"
        f" ({len(batch.targets)} test(s), {remaining:.1f}s left) ==",
        flush=True,  # print before pytest's output
    )
    return BatchOutcome(batch=batch, run=run_pytest(repo, batch.targets, remaining, environment))


def run_batches(
    repo: Path, batches: tuple[Batch, ...], budget: float, environment: RunEnvironment
) -> tuple[BatchOutcome, ...]:
    """Run bundles one at a time in order, sharing the total budget among them — the contract-set bundle always runs first.
    Each bundle receives the remainder after subtracting the seconds the previous bundles counted against the budget (excluding the configure phase = waiting for the machine's test slot — ChildClock)."""
    outcomes: tuple[BatchOutcome, ...] = ()
    for index in range(len(batches)):
        remaining = budget - _counted_seconds(outcomes)
        outcomes = (*outcomes, _run_batch(repo, index, batches, remaining, environment))
    return outcomes


def _counted_seconds(outcomes: tuple[BatchOutcome, ...]) -> float:
    """Total seconds that the bundles which ran counted against the budget (0 for bundles that never got a turn) — the part of the shared budget used, and the elapsed time in the summary."""
    return sum(o.run.seconds for o in outcomes if isinstance(o.run, PytestRun))


def _print_waits(outcomes: tuple[BatchOutcome, ...]) -> None:
    """Name the seconds of the configure phase not counted against the budget (waiting for the machine's test slot) on a line separate from the elapsed time — never silently drop the waited seconds."""
    total = sum(o.run.waited for o in outcomes if isinstance(o.run, PytestRun))
    per_session = ", ".join(
        f"session {o.batch.session} {o.run.waited:.1f}s"
        for o in outcomes
        if isinstance(o.run, PytestRun)
    )
    print(
        f"Configure time not counted against the budget (includes waiting for the machine's test slot): total {total:.1f}s ({per_session})"
    )


def _print_reverse(answer: AffectedAnswer, added: int) -> None:
    """Name how reverse dependencies were measured — show, without mixing into red, when they could not be measured and which modules were too broad to follow."""
    match answer:
        case AffectedUnmeasured(reason=reason):
            print(f"Reverse dependencies could not be measured — {reason} (not red; only contract sets and changed tests were selected)")
        case Affected(tests=tests, hubs=hubs, unreadable=unreadable):
            print(
                f"Reverse dependencies: added {added} test(s) (linter answered {len(tests)}; overlaps and non-test files excluded)"
            )
            for hub in hubs:
                print(
                    f"  module too widely depended on to trace: {hub.module} (root {hub.root or '.'}, direct dependents"
                    f" {hub.dependents}) — beyond that, the daily whole-repo run measures it"
                )
            if unreadable:
                print(
                    f"  {len(unreadable)} file(s) whose dependencies could not be read (selection beyond them may have missed tests):"
                    f" {', '.join(unreadable[:10])}"
                )


def _print_summary(
    results: tuple[TargetResult, ...], seconds: float, budget: float, reverse: AffectedAnswer
) -> None:
    """Print the summary split into three (ran, red, unmeasured) — unmeasured is named with reasons and not mixed into red. Also names how reverse dependencies were measured."""
    print()
    print(f"== Changed-tests summary (elapsed {seconds:.1f}s / budget {budget:g}s) ==")
    for verdict in Verdict:
        rows = [r for r in results if r.verdict is verdict]
        print(f"{verdict.value}: {len(rows)}")
        for r in rows:
            print(f"  {r.target.arg} — {r.detail} ({r.target.origin.value}, {r.target.why})")
    _print_reverse(reverse, sum(1 for r in results if r.target.origin is Origin.REVERSE))


@dataclass(frozen=True)
class Finished:
    """The outcome of one run of the entry point — the exit code and the result per selected test (the source of the counts summary; when stopped, all selected tests are unmeasured)."""

    rc: int
    results: tuple[TargetResult, ...]


@dataclass(frozen=True)
class RunCounts:
    """A summary of one run of the entry point as counts only (to count a day's unmeasured as real numbers; no list of contents is included).
    selected = ran + red + unmeasured. unmeasured_reverse = the reverse-dependency tests among the unmeasured. seconds = the entry point's wall clock."""

    selected: int
    ran: int
    red: int
    unmeasured: int
    unmeasured_reverse: int
    rc: int
    seconds: float
    budget: float


def counts_of(finished: Finished, seconds: float, budget: float) -> RunCounts:
    """Turn the outcome of the entry point into a counts-only summary — the source of a machine-readable single line (pure)."""
    results = finished.results
    return RunCounts(
        selected=len(results),
        ran=sum(1 for r in results if r.verdict is Verdict.GREEN),
        red=sum(1 for r in results if r.verdict is Verdict.RED),
        unmeasured=sum(1 for r in results if r.verdict is Verdict.UNMEASURED),
        unmeasured_reverse=sum(
            1
            for r in results
            if r.verdict is Verdict.UNMEASURED and r.target.origin is Origin.REVERSE
        ),
        rc=finished.rc,
        seconds=seconds,
        budget=budget,
    )


def counts_line(counts: RunCounts) -> str:
    """Make the counts summary the last line of the entry point's output (starts with COUNTS_PREFIX; fields are `name=count` separated by spaces — same spelling as agora's entry point)."""
    return (
        f"{COUNTS_PREFIX} selected={counts.selected} ran={counts.ran} red={counts.red}"
        f" unmeasured={counts.unmeasured} unmeasured_reverse={counts.unmeasured_reverse}"
        f" rc={counts.rc} seconds={counts.seconds:.1f} budget={counts.budget:g}"
    )


def main(environ: Mapping[str, str], argv: list[str] | None = None) -> int:
    """Pre-registration entry point: changed files -> contract sets + changed tests + reverse-dependency tests -> per-session pytest processes as in the daily run -> a summary
    split into three, and rc. However it ends, the last line of output is a single counts-only summary line (counts_line)."""
    started = time.monotonic()
    parser = argparse.ArgumentParser(
        description="Changed-tests check — before registering, run the contract test sets, changed tests and reverse-dependency tests within a 60-second budget"
    )
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="working tree to run (default = current dir)"
    )
    parser.add_argument("--base", help="branch point (default = git merge-base HEAD origin/main)")
    parser.add_argument(
        "--budget", type=float, default=DEFAULT_BUDGET_SECONDS, help="total wall-clock budget in seconds"
    )
    parser.add_argument(
        "--linter",
        default=None,
        help="command of the doeff-linter asked about reverse dependencies (default = the linter keyed by the working tree's build inputs (same lookup as the commit hook),"
        "otherwise doeff-linter on PATH)",
    )
    args = parser.parse_args(argv)
    finished = _run_entry(args, read_run_environment(environ))
    print(counts_line(counts_of(finished, time.monotonic() - started, args.budget)))
    return finished.rc


def _run_entry(args: argparse.Namespace, run_environment: RunEnvironment) -> Finished:
    """Body of the entry point — select and run, print the summary split into three, and return the outcome (rc and per-test results)."""
    try:
        repo = Path(_git(args.repo, "rev-parse", "--show-toplevel").strip())
        table = read_contract_table(repo)
        patterns = collector_patterns(repo)
        changes = changed_files(repo, args.base)
        roots = read_session_roots(repo)
    except StopError as exc:
        print(f"Changed tests: stopping — {exc}", file=sys.stderr)
        return Finished(rc=2, results=())
    print(
        f"Changed tests: running on working tree contents (not the index) — {repo}, base {changes.base[:12]}"
        f" and {len(changes.files)} file(s) changed from there to the working tree",
        flush=True,  # print before pytest's output
    )
    reverse: AffectedAnswer = (
        ask_affected(pick_linter(args.linter, repo), repo, changes, patterns)
        if changes.files
        else Affected(tests=(), hubs=(), unreadable=())
    )
    selection = extend_with_reverse(
        select_targets(table, changes, patterns, repo), reverse, patterns, repo
    )
    targets = selection.targets
    if not targets:
        print("No matching contract set, changed test file or reverse-dependency test — 0 tests to run")
        _print_reverse(reverse, 0)
        return Finished(rc=0, results=())
    environment = project_environment(repo, run_environment)
    if not (environment / "pyvenv.cfg").is_file():
        reason = f"uv environment ({environment}) not found in the working tree — create it with `uv sync --frozen --group dev`"
        unready = tuple(TargetResult(t, Verdict.UNMEASURED, reason) for t in targets)
        _print_summary(unready, 0.0, args.budget, reverse)
        return Finished(rc=0, results=unready)
    batches = batches_of(selection, roots)
    try:
        outcomes = run_batches(repo, batches, args.budget, run_environment)
    except StopError as exc:
        print(f"Changed tests: stopping — {exc}", file=sys.stderr)
        stopped = f"the entry point stopped — {exc}"
        return Finished(
            rc=2, results=tuple(TargetResult(t, Verdict.UNMEASURED, stopped) for t in targets)
        )
    results = tuple(
        classify(t, outcome.run, args.budget) for outcome in outcomes for t in outcome.batch.targets
    )
    _print_summary(results, _counted_seconds(outcomes), args.budget, reverse)
    print(
        f"pytest processes: {sum(1 for o in outcomes if isinstance(o.run, PytestRun))}/{len(outcomes)} ran"
        f" (sessions: {', '.join(o.batch.session for o in outcomes)})"
    )
    _print_waits(outcomes)
    return Finished(rc=1 if any(r.verdict is Verdict.RED for r in results) else 0, results=results)


def _main_as_pytest(argv: list[str]) -> int:
    """Entry point of the child process inside the working tree's environment: read the pipe write-end number at the head of the command line exactly once, and run
    pytest passing ReportPlugin (PYTHONPATH is not appended; environment variables are not read)."""
    # Only this hook runs inside the working tree's environment (the entry-point side is stdlib only, so it is not imported at the top of the module).
    import pytest

    match argv:
        case [fd, *pytest_args] if fd.isdigit():
            return int(pytest.main(pytest_args, plugins=[ReportPlugin(report_fd=int(fd))]))
        case _:
            print(f"after {PYTEST_MODE} comes the pipe number to write outcomes to: {argv[:1]!r}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(
        _main_as_pytest(sys.argv[2:])
        if sys.argv[1:2] == [PYTEST_MODE]
        else main(os.environ)  # the one place that reads the environment (passed inward as RunEnvironment)
    )
