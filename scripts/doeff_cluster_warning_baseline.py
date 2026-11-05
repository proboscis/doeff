"""Compare the number of warnings (major) in packages/doeff-cluster against a baseline per rule x per file.

error (critical) is stopped by lint-doeff-cluster.sh using doeff-linter's exit code. The linter exits 0 on warnings, so here they are
compared with the baseline file (packages/doeff-cluster/lint-warning-baseline.json = {rule: {path from the package dir: count}}):

  check [path ...]  For the measured files, red if the count is higher than the baseline (a new warning). Also red if it is lower but the baseline has not been lowered
                  (the change that fixes it lowers the baseline in the same commit -- if forgotten, the next new violation slips in silently). Without paths, the whole package.
  lower           Measure the whole package and lower the baseline to the current counts (never raise it, never add new pairs). Run and staged by the change that fixed things.

This is not a known-list that "lets things through": the baseline moves only downward. The linter runs in the package dir (same as lint-doeff-cluster.sh).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from doeff import run, with_handlers
from doeff_core_effects import subprocess_handler
from doeff_core_effects import ReadEnvironment

PACKAGE: str = "packages/doeff-cluster"
BASELINE_NAME: str = "lint-warning-baseline.json"
DECLARATION: str = "architecture.hy"
BASELINE_ENV: str = "DOEFF_CLUSTER_WARNING_BASELINE"

# The exact JSON shape of the baseline file (rule -> file -> count). It is a value at the JSON boundary, so it is kept as a mapping.
Counts = dict[str, dict[str, int]]


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    baseline: int
    current: int


def package_path(package_dir: Path, reported: str) -> str:
    """To align a path reported by the linter (sometimes absolute, sometimes relative to the package dir) to a path relative to the package dir.

    Keeps the key independent of where the worktree is placed (ddc164310 used the absolute path as the key, so in another worktree lower emptied the baseline and
    a check given paths passed without comparing anything -- measured). A path outside the package is named and stopped."""
    path: Path = Path(reported)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(package_dir.resolve()).as_posix()
    except ValueError:
        raise SystemExit(f"the linter reported a path outside the package: {reported} (cannot be a baseline key)") from None


def warning_counts(report: list[dict[str, object]], package_dir: Path) -> Counts:
    """From the linter's JSON (a list of pairs per rule), count only warnings by rule x file (path from the package dir)."""
    paths_by_rule: dict[str, list[str]] = {
        str(entry["rule"]): [package_path(package_dir, str(v["file"])) for v in violations]
        for entry in report
        if entry.get("severity") == "warning" and isinstance(violations := entry.get("violations"), list)
    }
    return {rule: {path: paths.count(path) for path in sorted(set(paths))} for rule, paths in paths_by_rule.items() if paths}


def _count(counts: Counts, rule: str, path: str) -> int:
    """To compare a pair absent from both the baseline and now as 0 (absent = that rule has no warning in that file)."""
    return counts.get(rule, {}).get(path, 0)


@dataclass(frozen=True)
class Comparison:
    """Outcome of the comparison: grown = pairs that increased over the baseline (new violations); stale = pairs that decreased but whose baseline was not lowered."""

    grown: tuple[Finding, ...]
    stale: tuple[Finding, ...]


def compare(baseline: Counts, current: Counts, measured: frozenset[str] | None) -> Comparison:
    """Compare the baseline with the current counts. measured = the set of measured files (None = the whole package was measured)."""
    rules: set[str] = set(baseline) | set(current)
    pairs: set[tuple[str, str]] = {
        (rule, path)
        for rule in rules
        for path in set(baseline.get(rule, {})) | set(current.get(rule, {}))
        if measured is None or path in measured
    }
    findings: list[Finding] = sorted(
        (Finding(rule, path, _count(baseline, rule, path), _count(current, rule, path)) for rule, path in pairs),
        key=lambda f: (f.rule, f.path),
    )
    return Comparison(
        grown=tuple(f for f in findings if f.current > f.baseline),
        stale=tuple(f for f in findings if f.current < f.baseline),
    )


def lowered(baseline: Counts, current: Counts) -> Counts:
    """Lower the baseline to the current counts (never raise it, do not add pairs absent from the baseline, delete pairs that reached 0)."""
    result: Counts = {
        rule: {
            path: min(count, _count(current, rule, path))
            for path, count in sorted(per_file.items())
            if min(count, _count(current, rule, path)) > 0
        }
        for rule, per_file in sorted(baseline.items())
    }
    return {rule: per_file for rule, per_file in result.items() if per_file}


def _run_linter(linter: str, package_dir: Path, paths: list[str]) -> list[dict[str, object]]:
    """To run the linter once in the package dir and get the JSON report (anything other than rc 0 / 1 is stopped as unmeasurable)."""
    arguments: list[str] = [linter, "--no-log", "--output-format", "json", *paths]
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        arguments, cwd=package_dir, capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise SystemExit(f"doeff-linter could not measure (rc {completed.returncode}): {completed.stderr.strip()}")
    report = json.loads(completed.stdout or "[]")
    if not isinstance(report, list):
        raise SystemExit("the JSON of doeff-linter is not a list of pairs")
    return report


def _package_paths(repo_paths: list[str]) -> list[str]:
    """To turn the paths from the repo root passed by the commit hook into paths from the package dir, and always add the declaration file."""
    prefix: str = f"{PACKAGE}/"
    inside: list[str] = [p[len(prefix):] for p in repo_paths if p.startswith(prefix) and p != f"{prefix}{DECLARATION}"]
    return [*inside, DECLARATION]


def _line(finding: Finding) -> str:
    """To emit one line of the reason for red (rule, file, baseline and current count)."""
    return f"  {finding.rule} {finding.path}: 基点 {finding.baseline} → 今 {finding.current}"


def main(argv: list[str]) -> int:
    """Two entry points: check (commit hook and make lint-doeff) and lower (for the change that fixed things)."""
    # The root of the location is passed by the caller (lint-doeff-cluster.sh) with --root. If absent, one above where this script lives. For the linter, the commit hook
    # passes with --linter the binary keyed by the inputs of the HEAD build (#2906), and make lint-doeff passes the linter found by the same search path that matched the package.
    # If absent, doeff-linter on the search path.
    top: Path = Path(__file__).resolve().parents[1]
    linter: str = "doeff-linter"
    while argv[:1] in (["--root"], ["--linter"]) and len(argv) >= 2:
        if argv[0] == "--root":
            top = Path(argv[1])
        else:
            linter = argv[1]
        argv = argv[2:]
    package_dir: Path = top / PACKAGE
    # Only the make entry point check swaps the baseline location (tries how the exit code propagates with a fake linter and an empty baseline).
    # Swapping the location is an environment variable value -- do not read os.environ directly; ask via ReadEnvironment under the real responder.
    entries = run(with_handlers([subprocess_handler], ReadEnvironment((BASELINE_ENV,))))
    configured = next((entry.value for entry in entries if entry.name == BASELINE_ENV), None)
    baseline_file: Path = Path(configured or package_dir / BASELINE_NAME)
    if not argv or argv[0] not in ("check", "lower"):
        print("usage: doeff_cluster_warning_baseline.py check [path ...] | lower", file=sys.stderr)
        return 2
    if not baseline_file.is_file():
        print(f"baseline file missing: {baseline_file} (not defaulted to 0)", file=sys.stderr)
        return 2
    baseline: Counts = json.loads(baseline_file.read_text(encoding="utf-8"))
    if argv[0] == "lower":
        current: Counts = warning_counts(_run_linter(linter, package_dir, []), package_dir)
        baseline_file.write_text(json.dumps(lowered(baseline, current), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        print(f"lowered the baseline: {baseline_file}")
        return 0
    paths: list[str] = _package_paths(argv[1:]) if len(argv) > 1 else []
    current = warning_counts(_run_linter(linter, package_dir, paths), package_dir)
    measured: frozenset[str] | None = frozenset(paths) if paths else None
    comparison: Comparison = compare(baseline, current, measured)
    if comparison.grown:
        print(f"warnings of {PACKAGE} grew over the baseline (fix new violations -- do not add them to the baseline):", file=sys.stderr)
        print("\n".join(_line(f) for f in comparison.grown), file=sys.stderr)
    if comparison.stale:
        print(f"warnings of {PACKAGE} decreased below the baseline but the baseline was not lowered -- "
              "run `uv run --no-project python scripts/doeff_cluster_warning_baseline.py lower` and stage:", file=sys.stderr)
        print("\n".join(_line(f) for f in comparison.stale), file=sys.stderr)
    return 1 if comparison.grown or comparison.stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
