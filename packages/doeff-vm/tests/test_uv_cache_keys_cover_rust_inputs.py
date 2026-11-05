"""Check that uv cache-keys fully cover the Rust build inputs of doeff-vm.

uv decides "whether to rebuild" by looking only at the files listed in [tool.uv] cache-keys of packages/doeff-vm/pyproject.toml.
If a Rust source is missing from the keys, then after an update that changes only that file,
`uv sync` / `uv run` keep using the old doeff_vm/*.so (from 2026-09-23 22:42 to 23:17 the pod's
controller was down for 35 minutes on a stale .so).

The input population is derived without writing crate names: following path = in the dependency tables of packages/doeff-vm/Cargo.toml
transitively, for the resulting set of crates, collect the Cargo.toml / Cargo.lock / build.rs /
pyproject.toml and library *.rs on disk. tests/ benches/ examples/ are locations for Cargo's non-library
targets and enter neither the cdylib maturin builds nor the build as a path dependency, so they are
excluded from the population.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import tomllib

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
PACKAGE_PATH = PACKAGE_DIR.relative_to(REPO_ROOT).as_posix()

# Files recognizable as build inputs by name (*.rs are picked up separately by extension).
BUILD_INPUT_NAMES = frozenset({"Cargo.toml", "Cargo.lock", "build.rs", "pyproject.toml"})
# Locations where Cargo automatically looks for non-library targets (integration tests, benches, examples).
NON_LIBRARY_TARGET_DIRS = frozenset({"tests", "benches", "examples"})
# Output locations of Cargo and maturin builds (not inputs).
BUILD_OUTPUT_DIRS = frozenset({"target"})
# Dependency tables that affect the build. [dev-dependencies] do not enter the library build.
DEPENDENCY_TABLES = ("dependencies", "build-dependencies")
_UNSUPPORTED_GLOB_CHARS = frozenset("[]{}")


# ---------------------------------------------------------------------------
# Pure decisions
# ---------------------------------------------------------------------------


def path_dependency_dirs(manifest: Mapping[str, Any], crate_dir: Path) -> list[Path]:
    """Return the directories of path = dependencies from the dependency tables of one Cargo.toml."""
    tables: list[Mapping[str, Any]] = [manifest.get(name, {}) for name in DEPENDENCY_TABLES]
    for target in manifest.get("target", {}).values():
        tables.extend(target.get(name, {}) for name in DEPENDENCY_TABLES)
    return [
        (crate_dir / spec["path"]).resolve()
        for table in tables
        for spec in table.values()
        if isinstance(spec, Mapping) and "path" in spec
    ]


def is_build_input(path_in_crate: str) -> bool:
    """Whether a path relative to the crate directory is a build input."""
    parts = PurePosixPath(path_in_crate).parts
    if parts[0] in NON_LIBRARY_TARGET_DIRS:
        return False
    return parts[-1] in BUILD_INPUT_NAMES or parts[-1].endswith(".rs")


def declared_file_globs(pyproject: Mapping[str, Any]) -> list[str]:
    """Return the globs of the keys that name files among [tool.uv] cache-keys (strings / { file = ... })."""
    keys = pyproject.get("tool", {}).get("uv", {}).get("cache-keys", [])
    globs: list[str] = []
    for key in keys:
        if isinstance(key, str):
            globs.append(key)
        elif isinstance(key, Mapping) and isinstance(key.get("file"), str):
            globs.append(key["file"])
    return globs


def _segment_matches(pattern: str, segment: str) -> bool:
    if not any(ch in pattern for ch in "*?"):
        return pattern == segment
    if segment == "..":
        return False
    if "**" in pattern:
        raise ValueError(f"'**' must be a whole path segment: {pattern!r}")
    regex = "".join(
        "[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in pattern
    )
    return re.fullmatch(regex, segment) is not None


def _segments_match(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        # Zero or more directories. uv walks down from the package directory, so '..' is not crossed.
        for taken in range(len(path) + 1):
            if taken > 0 and path[taken - 1] == "..":
                return False
            if _segments_match(rest, path[taken:]):
                return True
        return False
    return bool(path) and _segment_matches(head, path[0]) and _segments_match(rest, path[1:])


def glob_matches(pattern: str, path: str) -> bool:
    """Whether a normalized posix path matches a glob.

    '**' matches zero or more directories; '*' / '?' match only within one segment.
    [] and {} stop rather than be misinterpreted (extend here when they become needed).
    """
    unsupported = _UNSUPPORTED_GLOB_CHARS.intersection(pattern)
    if unsupported:
        raise ValueError(f"unsupported glob syntax {sorted(unsupported)} in {pattern!r}")
    return _segments_match(tuple(pattern.split("/")), tuple(path.split("/")))


def uncovered_inputs(
    inputs: Iterable[str], file_globs: Iterable[str], *, package_path: str
) -> list[str]:
    """Return the inputs that match no glob.

    inputs and file_globs are both written as paths relative to the package directory. Both sides are
    aligned to paths from the repo root (prefix package_path and normpath) before comparing, so
    '../doeff-vm/src/lib.rs' and 'src/lib.rs' are treated as the same file.
    """

    def anchored(relative: str) -> str:
        return posixpath.normpath(posixpath.join(package_path, relative))

    anchored_globs = [anchored(pattern) for pattern in file_globs]
    return sorted(
        path
        for path in inputs
        if not any(glob_matches(pattern, anchored(path)) for pattern in anchored_globs)
    )


# ---------------------------------------------------------------------------
# Part that reads the actual checkout
# ---------------------------------------------------------------------------


def input_crate_dirs(root_crate_dir: Path) -> list[Path]:
    """The root crate and the directories of crates reached by following path dependencies transitively (root first)."""
    ordered: list[Path] = []
    pending = [root_crate_dir.resolve()]
    while pending:
        crate_dir = pending.pop(0)
        if crate_dir in ordered:
            continue
        ordered.append(crate_dir)
        manifest = tomllib.loads((crate_dir / "Cargo.toml").read_text(encoding="utf-8"))
        pending.extend(path_dependency_dirs(manifest, crate_dir))
    return ordered


def source_files_under(crate_dir: Path) -> list[str]:
    """Files under a crate directory (posix paths relative to the crate). Build outputs and hidden dirs are not walked.

    Walk the disk without asking git - uv's build reads files on disk, and the tree the daily verification receives is not
    a git checkout (remote_check carries only a file roster and has no .git - on 2026-09-28 daily t97 saw
    128 reds from `git ls-files`).
    """
    found: list[str] = []
    for directory, subdirectories, files in os.walk(crate_dir):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in BUILD_OUTPUT_DIRS and not name.startswith(".")
        )
        found.extend(
            Path(os.path.relpath(Path(directory, name), crate_dir)).as_posix() for name in files
        )
    return sorted(found)


def tracked_build_inputs_by_crate() -> dict[str, list[str]]:
    """List of build inputs per crate (directory relative to the package)."""
    return {
        Path(os.path.relpath(crate_dir, PACKAGE_DIR)).as_posix(): sorted(
            Path(os.path.relpath(crate_dir / in_crate, PACKAGE_DIR)).as_posix()
            for in_crate in source_files_under(crate_dir)
            if is_build_input(in_crate)
        )
        for crate_dir in input_crate_dirs(PACKAGE_DIR)
    }


def _declared_globs() -> list[str]:
    pyproject = tomllib.loads((PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return declared_file_globs(pyproject)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def test_uv_cache_keys_cover_every_rust_build_input() -> None:
    inputs = [path for paths in tracked_build_inputs_by_crate().values() for path in paths]
    uncovered = uncovered_inputs(inputs, _declared_globs(), package_path=PACKAGE_PATH)
    assert not uncovered, (
        "Build inputs not listed in cache-keys of [tool.uv] in packages/doeff-vm/pyproject.toml exist."
        " If only such a file changes, uv does not rebuild doeff-vm and a stale .so remains:\n"
        + "\n".join(f"  {path}" for path in uncovered)
    )


def test_checker_reports_dependency_sources_when_their_glob_is_missing() -> None:
    """Calibration: with a synthetic key set lacking the glob for dependency crates' *.rs, exactly those *.rs show up as omissions."""
    by_crate = tracked_build_inputs_by_crate()
    crates_with_inputs = [crate for crate, paths in by_crate.items() if paths]
    assert "." in crates_with_inputs
    assert len(crates_with_inputs) >= 2, f"population has fewer than 2 crates: {by_crate}"
    dependency_crates = [crate for crate in by_crate if crate != "."]
    assert dependency_crates, "no path-dependency crate could be derived"
    synthetic = [*sorted(BUILD_INPUT_NAMES), "src/**/*.rs"] + [
        f"{crate}/{name}" for crate in dependency_crates for name in sorted(BUILD_INPUT_NAMES)
    ]
    expected = sorted(
        path
        for crate in dependency_crates
        for path in by_crate[crate]
        if path.endswith(".rs") and path != f"{crate}/build.rs"
    )
    assert expected, "no dependency-crate *.rs in the population - the check is running idle"
    inputs = [path for paths in by_crate.values() for path in paths]
    assert uncovered_inputs(inputs, synthetic, package_path=PACKAGE_PATH) == expected


def test_glob_semantics() -> None:
    assert glob_matches("a/src/**/*.rs", "a/src/lib.rs")
    assert glob_matches("a/src/**/*.rs", "a/src/vm/step.rs")
    assert not glob_matches("a/src/**/*.rs", "a/src/lib.rsx")
    assert not glob_matches("a/src/**/*.rs", "a/srcx/lib.rs")
    assert not glob_matches("a/src/*.rs", "a/src/vm/step.rs")
    assert not glob_matches("**/*.rs", "../outside/lib.rs")
    pkg = "packages/doeff-vm"
    assert uncovered_inputs(["src/lib.rs"], ["../doeff-vm/src/*.rs"], package_path=pkg) == []
    assert uncovered_inputs(["src/lib.rs"], ["./src/lib.rs"], package_path=pkg) == []
    assert uncovered_inputs(["../doeff-vm-core/src/lib.rs"], ["**/*.rs"], package_path=pkg) == [
        "../doeff-vm-core/src/lib.rs"
    ]
    assert (
        uncovered_inputs(
            ["../doeff-vm-core/src/vm/step.rs"],
            ["../doeff-vm-core/src/**/*.rs"],
            package_path=pkg,
        )
        == []
    )
