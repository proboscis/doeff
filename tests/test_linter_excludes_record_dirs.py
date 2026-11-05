"""Design-record dirs are outside doeff-linter's scope -- the two definitions (record-dirs and the linter's exclude) must not diverge.

Design records are evidence from a point in time: they hold measurement scripts and copies of what blind A/B reviewers wrote. Rewriting them would
change the record, so they are not subject to the rules applied to production code and tests (DOEFF002, 004, 006, 007, etc.). The record dirs are defined in one place, root pyproject.toml's
`[tool.doeff] record-dirs`, and the linter's `[tool.doeff-linter] exclude` excludes only when it matches a single path component (a dir name),
so the trailing names of record-dirs are mirrored into exclude.

- If a record dir is added without adding its trailing name to exclude, findings for scripts placed in that dir turn the daily lint (baseline comparison) red.
- Conversely, if a name added to exclude also exists as a dir outside the record dirs, the code there silently falls outside the rules.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Lines in exclude that are not record-dir names (input dirs of sample fixtures read by tool tests -- see the note in pyproject.toml).
FIXTURES = "fixtures"


def _pyproject() -> dict[str, Any]:
    """The mapping exactly as tomllib returns it (a TOML boundary value -- this test checks the shape of the two fields it uses)."""
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _record_dirs() -> list[str]:
    """Design-record dirs (paths from the repo root, ending in `/`)."""
    return list(_pyproject()["tool"]["doeff"]["record-dirs"])


def _excluded_names() -> set[str]:
    return set(_pyproject()["tool"]["doeff-linter"]["exclude"])


def _tracked_python() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py", "*.pyi"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [path for path in listed.stdout.split("\0") if path]


def test_every_record_dir_is_out_of_the_linters_scope() -> None:
    """The trailing name of each record dir is present in the linter's exclude."""
    names = {PurePosixPath(record).name for record in _record_dirs()}
    missing = sorted(names - _excluded_names())
    assert missing == [], (
        f"Design-record dirs {missing} are included in the linter's scope -- add"
        " their trailing names to [tool.doeff-linter] exclude in pyproject.toml (records cannot be rewritten, so they are not subject to the rules)"
    )


def test_no_excluded_name_hides_a_directory_outside_the_record_dirs() -> None:
    """Adding record-dir names to exclude must not put code outside the record dirs outside the rules."""
    record_names = {PurePosixPath(record).name for record in _record_dirs()}
    hidden = sorted(
        path
        for path in _tracked_python()
        if (set(PurePosixPath(path).parts[:-1]) & (_excluded_names() - {FIXTURES}) & record_names)
        and not any(path.startswith(record) for record in _record_dirs())
    )
    assert hidden == [], (
        f"Code in dirs sharing a name with a record dir is outside the linter's scope: {hidden[:5]} -- do not place"
        " dirs with that name outside the record dirs (if you must, widen the exclude mechanism to path prefixes)"
    )
