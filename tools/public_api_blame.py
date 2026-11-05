"""Trace public names added without being recorded in the table back to the commit and card that added them, and emit report lines (a stage after the daily full verification).

Rule: tests/test_public_api_known_violations.py is run by neither the merge queue nor the commit-time hook. So a commit that adds a public name without
updating the table lands on main and only starts failing in the daily full verification. This tool prints each failing name, one per line, as

    public_api_blame: <package> <module>.<name> <first 12 chars of the adding commit> <card or request id> <subject>

to stdout; the daily task collects them as the stage's report (agora-controllers AfterReport) and attaches them to a red card. The adding commit is
the first hit of `git log -S<name> --reverse` (the commit that first added the name). Matching uses the same function as the test (unlisted_names).
Finally it prints `public_api_blame: 数え <N> 個` as the mark that the stage ran to the end (so a run with 0 report lines reads as "fixed").
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
REPORT_PREFIX = "public_api_blame: 足した "
COUNT_PREFIX = "public_api_blame: 数え "
_ID_RE = re.compile(r"\b(ki-[0-9a-f]{12}|lt-[0-9A-Z]{26})")


def _load_test_module() -> ModuleType:
    """Load the table-matching function from the test file (tests/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "test_public_api_known_violations", ROOT / "tests" / "test_public_api_known_violations.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def first_commit_adding(name: str, package_dir: str, repo: Path = ROOT) -> tuple[str, str, str]:
    """For the commit that first added the name: (first 12 chars, card or request id taken from the body, subject). An empty tuple if none is found."""
    out = subprocess.run(
        ["git", "log", f"-S{name}", "--reverse", "--format=%H%x1f%s%x1f%b%x1e", "--", f"packages/{package_dir}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    first = out.split("\x1e", 1)[0].strip()
    if not first:
        return ("", "", "")
    sha, subject, body = (first.split("\x1f") + ["", ""])[:3]
    found = _ID_RE.search(subject + "\n" + body)
    return (sha[:12], found.group(1) if found else "", subject)


def _report_line(package_dir: str, module: str, name: str, repo: Path) -> str:
    """Build the report line for one name: look up the adding commit and id and format one line with the same prefix spelling."""
    sha, card, subject = first_commit_adding(name, package_dir, repo)
    return f"{REPORT_PREFIX}{package_dir} {module}.{name} {sha or '(commit unknown)'} {card or '(no id)'} {subject}"


def report_lines(repo: Path = ROOT) -> list[str]:
    """Build report lines for violations added without being recorded in the table, across all packages (matching is the same single point as the test's unlisted_names)."""
    test = _load_test_module()
    table = test._read_table()
    allowed = test._read_allowed()
    return [
        _report_line(package["dir"], module, name, repo)
        for package in test.tool.inventory(repo, None, jobs=4, timeout=test._PROBE_SECONDS)
        for module, name in test.unlisted_names(package, table, allowed)
    ]


def main() -> int:
    """Print the report lines and the count line that marks the stage ran to the end."""
    lines = report_lines()
    for line in lines:
        print(line)
    print(f"{COUNT_PREFIX}{len(lines)} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
