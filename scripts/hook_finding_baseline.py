"""Compare the findings of the commit hook's doeff-linter (error) and semgrep (Python) with a per-rule x per-file baseline.

The mainline has findings that predate installing the hook (measured 2026-10-02: 721 doeff-linter errors in 296 files, 74 semgrep Python findings in
24 files). If the hook blocked all of them, every commit touching those files would be blocked. So the hook blocks only "findings that increased over the baseline"
(the same shape as the packages/doeff-cluster warning-baseline check scripts/doeff_cluster_warning_baseline.py and the Hy type gate).

The baseline (scripts/hook_finding_baseline/<tool>.json = {"version": version of the tool that counted, "counts": {rule: {path from repo root: count}}}) is
not an "allow list" but the reference for comparison. Keys do not depend on line numbers (a line shift alone does not turn it red). The baseline only moves downward:

  check <tool> [path ...]  For the measured files, red if the count is above the baseline (a new finding -- fix it). Also red if it is below
                         the baseline and the baseline has not been lowered (the change that fixed it lowers the baseline in the same commit -- forgetting lets
                         the next new finding in silently). Without path, all files of
                         the repo. doeff-linter looks for a binary with the same build-input key as the one the baseline names, in the land-arm
                         dev build -> the snapshot store, and calls it (scripts/doeff_linter_locked.py, #2906 -- the linter on the search path is not used).
                         If it is not in the store (between commits of a change that modifies the linter, a few minutes until land-arm rebuilds), it is neither red nor green:
                         it names "cannot be measured" and passes -- the hook does not build. --strict (daily) is red if the tree's linter build-input
                         key differs from the baseline key (the change that modified the linter did not lower). The semgrep version is determined only by uv.lock in the tree
                         (scripts/semgrep_locked.py, #2906), so if it differs from the baseline, the tree is red as "bumped the lock but did not recount".
  lower <tool>           Measure all files of the repo, lower the baseline to the current counts, and set the version to the tool's version (never raises, never adds new pairs).
                         Run and staged by the change that fixed things and the change that changed the tool version. Pairs that increased over the baseline at the current version cannot be lowered, so they are named.
                         doeff-linter uses what was passed by `--linter <binary>` (the change's own build of a modified linter) or the binary with the
                         build-input key of HEAD (stops and names it if not in the store).
  init <tool>            Only when there is no baseline file, measure all files of the repo and write it with the version (refuses if it exists -- not a way to raise).

Tools = doeff-linter (severity error only -- warning and info do not stop the hook) and semgrep (.semgrep.yaml; like the hook, excludes the rule
fixtures tests/semgrep/fixtures/). All files of the repo = the .py / .pyi tracked by git that are not symlinks (semgrep cannot measure
symlinks). Even when measuring all files, semgrep is limited to 2 cores (the default uses all cores and spiked the machine load).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# The neighboring modules (doeff_linter_locked, semgrep_locked) are loaded explicitly with this script's dir as the location. The script's dir entering sys.path is
# an implicit default; under PYTHONSAFEPATH=1 (present in the worker's shell) it does not, and the commit hook failed on import.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doeff_linter_locked import binary_key, dev_key, input_key, locate, same_key, searched
from semgrep_locked import locked_command, locked_version

BASELINE_DIR: str = "scripts/hook_finding_baseline"
TOOLS: tuple[str, ...] = ("doeff-linter", "semgrep")
SEMGREP_FIXTURES: str = "tests/semgrep/fixtures/"
SEMGREP_JOBS: str = "2"

# The JSON shape of the baseline file itself (rule -> file -> count). It is a value at the JSON boundary, so it is kept as a mapping.
Counts = dict[str, dict[str, int]]


@dataclass(frozen=True)
class Finding:
    """Baseline count and current count for one pair (rule, file)."""

    rule: str
    path: str
    baseline: int
    current: int


@dataclass(frozen=True)
class Baseline:
    """Baseline: the version of the tool that counted, and the rule x file counts (counts is a JSON-boundary value, so it is kept as a mapping)."""

    version: str
    counts: Counts


@dataclass(frozen=True)
class Comparison:
    """Outcome of the comparison: grown = pairs above the baseline (new findings), stale = pairs that decreased but whose baseline was not lowered."""

    grown: tuple[Finding, ...]
    stale: tuple[Finding, ...]


def repo_path(top: Path, reported: str) -> str:
    """Normalize paths reported by the tool (absolute or root-relative) to paths relative to the repo root (so keys do not depend on the worktree location)."""
    path: Path = Path(reported)
    if not path.is_absolute():
        return path.as_posix()
    resolved: Path = path.resolve()
    if not resolved.is_relative_to(top.resolve()):
        raise SystemExit(f"The tool reported a path outside the repo: {reported} (cannot be a baseline key)")
    return resolved.relative_to(top.resolve()).as_posix()


def tally(pairs: list[tuple[str, str]]) -> Counts:
    """Turn a sequence of (rule, path) into rule x file counts."""
    rules: list[str] = sorted({rule for rule, _ in pairs})
    return {
        rule: {path: count for path in sorted({p for r, p in pairs if r == rule}) if (count := pairs.count((rule, path)))}
        for rule in rules
    }


def linter_error_counts(report: list[dict[str, object]], top: Path) -> Counts:
    """From the doeff-linter JSON (a list of per-rule entries), count only severity-error findings by rule x file."""
    return tally(
        [
            (str(entry["rule"]), repo_path(top, str(violation["file"])))
            for entry in report
            if entry.get("severity") == "error" and isinstance(violations := entry.get("violations"), list)
            for violation in violations
        ]
    )


def semgrep_counts(report: dict[str, object], top: Path) -> Counts:
    """From the semgrep JSON results, count by rule (last name of check_id) x file."""
    results = report.get("results")
    if not isinstance(results, list):
        raise SystemExit("The semgrep JSON has no results list")
    return tally([(str(r["check_id"]).split(".")[-1], repo_path(top, str(r["path"]))) for r in results])


def _count(counts: Counts, rule: str, path: str) -> int:
    """Compare pairs absent from both baseline and current as 0 (absent = that rule has no finding in that file)."""
    return counts.get(rule, {}).get(path, 0)


def compare(baseline: Counts, current: Counts, measured: frozenset[str] | None) -> Comparison:
    """Compare the baseline and current counts per pair for the measured files (None = all files)."""
    pairs: set[tuple[str, str]] = {
        (rule, path)
        for rule in set(baseline) | set(current)
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
    """Lower the baseline to the current counts (never raise, do not add pairs absent from the baseline, drop pairs that reached 0)."""
    result: Counts = {
        rule: {
            path: min(count, _count(current, rule, path))
            for path, count in sorted(per_file.items())
            if min(count, _count(current, rule, path)) > 0
        }
        for rule, per_file in sorted(baseline.items())
    }
    return {rule: per_file for rule, per_file in result.items() if per_file}


# Dirs not entered when walking a tree without git (the tree the daily stage copied to zeus -- remote_check sends only the files in the git ls-files list and no .git):
# things made by check runs or the environment, not tracked by git (#2906).
UNTRACKED_DIRS: frozenset[str] = frozenset({".git", ".venv", "node_modules", "__pycache__"})


def _tracked_python(top: Path) -> list[str] | None:
    """The .py / .pyi tracked by git (None if not a git working tree)."""
    listed: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py", "*.pyi"], cwd=top, capture_output=True, text=True, check=False
    )
    return sorted(p for p in listed.stdout.split("\0") if p) if listed.returncode == 0 else None


def _walked_python(top: Path) -> list[str]:
    """The .py / .pyi of a tree without git (does not enter generated dirs) -- the daily stage tree is a copy of the files git tracks, so walking gives the same list."""
    def walk() -> Iterator[str]:
        for directory, subdirectories, files in os.walk(top):
            subdirectories[:] = sorted(d for d in subdirectories if d not in UNTRACKED_DIRS)
            yield from ((Path(directory) / f).relative_to(top).as_posix() for f in files if f.endswith((".py", ".pyi")))
    return sorted(walk())


def population(top: Path, tool: str) -> list[str]:
    """All files of the repo (the .py / .pyi tracked by git, not symlinks, and for semgrep excluding the rule fixtures). For a tree that is not a git working tree
    (the daily stage), builds the list by walking dirs and says so."""
    tracked: list[str] | None = _tracked_python(top)
    if tracked is None:
        print(f"{tool}: not a git work tree -- built the roster by walking the dir (does not enter {', '.join(sorted(UNTRACKED_DIRS))})",
              file=sys.stderr)
    return [
        path
        for path in (tracked if tracked is not None else _walked_python(top))
        if not (top / path).is_symlink() and not (tool == "semgrep" and path.startswith(SEMGREP_FIXTURES))
    ]


@dataclass(frozen=True)
class Instrument:
    """The tool used for measuring: the command to call and the tool version (the key written to and checked against the baseline)."""

    command: tuple[str, ...]
    version: str


def semgrep_instrument(top: Path) -> Instrument:
    """semgrep is called from the uv tool store at the version uv.lock decides (scripts/semgrep_locked.py, #2906) -- the semgrep in the search path or the worktree .venv
    had versions that diverged by machine and worktree state. The version is read from the lock; `--version` is not queried again (about 2 seconds each time)."""
    return Instrument(tuple(locked_command(top)), locked_version(top))


def linter_for_writing(top: Path, linter: Path | None) -> Instrument:
    """Write the baseline (init, lower) linter: the binary passed in (the change that modified the linter passes its own build) or the binary with the build-input key of HEAD
    (land-arm dev build -> snapshot store). If absent, name it and stop (do not count with the search-path linter and write a different key)."""
    if linter is not None:
        return Instrument((str(linter),), binary_key(top, linter))
    head: str | None = input_key(top, "HEAD")
    located = locate(top, head) if head is not None else None
    if located is None:
        raise SystemExit(f"No binary for the HEAD linter build-input ({head}) in {searched()} -- pass the built binary "
                         "with `--linter <path>`")
    return Instrument((str(located.binary),), binary_key(top, located.binary))


def linter_for_checking(top: Path, baseline: Baseline) -> Instrument | None:
    """The linter to compare with the baseline: the binary with the same build input as the one the baseline names (scripts/doeff_linter_locked.py, #2906). Inside the hook it does not
    build -- None if not in the store (the caller names it as "cannot be measured")."""
    located = locate(top, baseline.version)
    return Instrument((str(located.binary),), baseline.version) if located is not None else None


def read_baseline(file: Path) -> Baseline:
    """Read the baseline file (stop if the shape differs -- do not default to 0)."""
    raw = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), str) or not isinstance(raw.get("counts"), dict):
        raise SystemExit(f"The baseline file has the wrong shape (version and counts are required): {file}")
    return Baseline(version=raw["version"], counts=raw["counts"])


def measure(top: Path, tool: str, instrument: Instrument, paths: list[str]) -> Counts:
    """Run the tool once at the repo root to get rule x file counts (stop if it could not measure -- do not pass it as 0)."""
    if not paths:
        return {}
    if tool == "doeff-linter":
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            # --force-exclude: apply the root config's exclude (outside the rule scope -- see the note on [tool.doeff-linter] in pyproject.toml) even to named files.
            # Without it, exclude has no effect at this entry point, which measures named files.
            [*instrument.command, "--no-log", "--force-exclude", "--output-format", "json", *paths],
            cwd=top, capture_output=True, text=True, check=False,
        )
        if completed.returncode not in (0, 1):
            raise SystemExit(f"doeff-linter could not measure (rc {completed.returncode}): {completed.stderr.strip()}")
        report = json.loads(completed.stdout or "[]")
        if not isinstance(report, list):
            raise SystemExit("The doeff-linter JSON is not a list of entries")
        return linter_error_counts(report, top)
    completed = subprocess.run(
        [*instrument.command, "--metrics=off", "--disable-version-check", "--config", ".semgrep.yaml", "--json",
         "--quiet", "-j", SEMGREP_JOBS, *paths],
        cwd=top, capture_output=True, text=True, check=False,
    )
    report = json.loads(completed.stdout or "{}")
    if not isinstance(report, dict):
        raise SystemExit(f"semgrep could not measure (rc {completed.returncode}): {completed.stderr.strip()[:500]}")
    errors = report.get("errors")
    if errors:
        raise SystemExit(f"semgrep could not measure: {json.dumps(errors, ensure_ascii=False)[:500]}")
    return semgrep_counts(report, top)


def _line(finding: Finding) -> str:
    """Emit the one-line reason for red (rule, file, baseline and current counts)."""
    return f"  {finding.rule} {finding.path}: baseline {finding.baseline} → now {finding.current}"


def _write(file: Path, baseline: Baseline) -> None:
    """Write the baseline file in the same shape (key order, indentation)."""
    payload = {"version": baseline.version, "counts": baseline.counts}
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _unmeasured_hint(top: Path, baseline: Baseline) -> str:
    """What to do when "cannot be measured": if the land-arm dev build exists with a key different from the baseline, pulling in main (so the baseline aligns to that key) makes it measurable."""
    dev: str | None = dev_key(top)
    if dev is not None and not same_key(dev, baseline.version):
        return "merging main in makes it measurable with the land-arm dev build"
    return "wait for land-arm to rebuild the main linter"


USAGE: str = (
    "usage: hook_finding_baseline.py [--root <dir>] [--linter <built doeff-linter>] [--strict] "
    "check|lower|init doeff-linter|semgrep [path …]"
)
#: The flag value that turns "cannot be measured" red (3) -- used by the daily stage (the hook lets it pass; daily is where already-passed commits are measured, so it does not pass).
UNMEASURED_RC: int = 3


@dataclass(frozen=True)
class Request:
    """Entry arguments: the root (so tests can try a temporary repo), the linter binary used by lower and init (the change that modified the linter's own build),
    the value returned on "cannot be measured" (hook passes with 0; the daily stage returns UNMEASURED_RC with --strict), the verb, the tool, and files."""

    top: Path
    linter: Path | None
    unmeasured_rc: int
    command: str
    tool: str
    paths: list[str]


def parse(argv: list[str]) -> Request | None:
    """Parse the entry arguments (None if unreadable -- print usage and return 2). The root defaults to one level above this script's location."""
    top: Path = Path(__file__).resolve().parents[1]
    linter: Path | None = None
    unmeasured_rc: int = 0
    rest: list[str] = argv
    while rest[:1] == ["--strict"] or (rest[:1] in (["--root"], ["--linter"]) and len(rest) >= 2):
        if rest[0] == "--strict":
            unmeasured_rc, rest = UNMEASURED_RC, rest[1:]
            continue
        if rest[0] == "--root":
            top = Path(rest[1])
        else:
            linter = Path(rest[1]).resolve()
        rest = rest[2:]
    if len(rest) < 2 or rest[0] not in ("check", "lower", "init") or rest[1] not in TOOLS:
        return None
    return Request(top=top, linter=linter, unmeasured_rc=unmeasured_rc, command=rest[0], tool=rest[1], paths=rest[2:])


def main(argv: list[str]) -> int:
    """Three entries: check (commit hook), lower (the change that fixed things), init (one time when there is no baseline)."""
    request: Request | None = parse(argv)
    if request is None:
        print(USAGE, file=sys.stderr)
        return 2
    top, tool = request.top, request.tool
    baseline_file: Path = top / BASELINE_DIR / f"{tool}.json"
    if request.command != "init" and not baseline_file.is_file():
        print(f"No baseline file: {baseline_file} (do not default to 0)", file=sys.stderr)
        return 2
    if request.command == "check":
        return _check(top, tool, read_baseline(baseline_file), request.paths, request.unmeasured_rc)
    instrument: Instrument = semgrep_instrument(top) if tool == "semgrep" else linter_for_writing(top, request.linter)
    if request.command == "init":
        if baseline_file.exists():
            print(f"baseline file exists: {baseline_file} (init does not rewrite it -- use lower to lower it)", file=sys.stderr)
            return 2
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        _write(baseline_file, Baseline(version=instrument.version,
                                       counts=measure(top, tool, instrument, population(top, tool))))
        print(f"Wrote the baseline: {baseline_file} ({instrument.version})")
        return 0
    baseline: Baseline = read_baseline(baseline_file)
    current: Counts = measure(top, tool, instrument, population(top, tool))
    grown_now: tuple[Finding, ...] = compare(baseline.counts, current, None).grown
    _write(baseline_file, Baseline(version=instrument.version, counts=lowered(baseline.counts, current)))
    print(f"Lowered the baseline: {baseline_file} ({baseline.version} -> {instrument.version})")
    if grown_now:
        print(f"Pairs above the baseline at the current {tool} version (the lowering path does not raise -- fix them, or the rule owner decides how to handle them):", file=sys.stderr)
        print("\n".join(_line(f) for f in grown_now), file=sys.stderr)
    return 0


def _check(top: Path, tool: str, baseline: Baseline, paths: list[str], unmeasured_rc: int) -> int:
    """Entry for check: compare measured file counts with the baseline using the tool at the baseline's version (red = 1, pass = 0, cannot measure = unmeasured_rc)."""
    instrument: Instrument | None = semgrep_instrument(top) if tool == "semgrep" else linter_for_checking(top, baseline)
    if instrument is None:
        # doeff-linter: the binary for the baseline key is not in the store (a worktree cut from a slightly older main, between commits of a change that modifies the linter,
        # a few minutes until land-arm rebuilds). The hook passes without stopping -- the daily stage (--strict) measures the passed commit against the same baseline shape,
        # and makes the inability to measure itself red in daily (#2906).
        print(f"doeff-linter: not measurable (no binary for the baseline version {baseline.version} in either of {searched()} -- the hook "
              f"does not build). {_unmeasured_hint(top, baseline)}. A change that modifies the linter passes its own build with "
              "`uv run --no-project python scripts/hook_finding_baseline.py --linter <binary> lower doeff-linter` to recount the baseline"
              + (" (--strict: unmeasured is red)" if unmeasured_rc else ""), file=sys.stderr)
        return unmeasured_rc
    if instrument.version != baseline.version:
        # The semgrep version is decided only by uv.lock in the tree (independent of the machine) -- a difference means only a tree that bumped the lock without recounting the baseline.
        print(f"semgrep: baseline version {baseline.version} differs from the uv.lock version {instrument.version} -- the change that bumped the lock "
              "recounts the baseline with `uv run --no-project python scripts/hook_finding_baseline.py lower semgrep` and includes it in the same commit",
              file=sys.stderr)
        return 1
    if tool == "doeff-linter" and unmeasured_rc and (head := input_key(top, "HEAD")) is not None and not same_key(head, baseline.version):
        # Daily (--strict) only: even if it can be measured with the baseline-key binary, the tree's linter rules are different (the linter changed and the baseline was not recounted).
        # Measuring silently with old rules lets the cost of recounting pile up on the next change that changes the key (between 10-03 and 10-11, 28 commits changed linter input and
        # 0 commits wrote the baseline -- blind check A of the design verification). The hook does not stop (another change's linter change must not
        # stop every later commit). A tree without git has no HEAD key, so it is not compared.
        print(f"doeff-linter: the tree linter build input ({head}) differs from the baseline version ({baseline.version}) -- the change that modified the linter "
              "passes its own build with `uv run --no-project python scripts/hook_finding_baseline.py --linter <binary> lower doeff-linter` "
              "to recount the baseline, and fixes the findings that enter the verdict in the same change (--strict: do not silently measure with a linter of old rules)", file=sys.stderr)
        return 1
    checked: list[str] = (
        [p for p in paths if not (tool == "semgrep" and p.startswith(SEMGREP_FIXTURES))] if paths else population(top, tool)
    )
    comparison: Comparison = compare(baseline.counts, measure(top, tool, instrument, checked),
                                     frozenset(checked) if paths else None)
    if comparison.grown:
        print(f"{tool} findings increased over the baseline (fix new findings -- do not add them to the baseline):", file=sys.stderr)
        print("\n".join(_line(f) for f in comparison.grown), file=sys.stderr)
    if comparison.stale:
        print(f"{tool} findings dropped below the baseline but the baseline was not lowered -- "
              f"`uv run --no-project python scripts/hook_finding_baseline.py lower {tool}` and stage the result:", file=sys.stderr)
        print("\n".join(_line(f) for f in comparison.stale), file=sys.stderr)
    return 1 if comparison.grown or comparison.stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
