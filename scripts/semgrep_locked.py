"""An entry point that pins the semgrep called by doeff's commit hook to the version the repo's uv.lock decides.

If the hook calls the semgrep on the search path or in the working tree's environment, the version drifts with how the machine was set up and the state of the working tree (2026-10-02 zeus: the shared
uv tool = 1.161.0, lock = 1.169.0. In a working tree without `.venv`, `uv run --no-sync --project <root>` made an empty `.venv` on the spot and
called the search-path one -- the baseline comparison declared itself "unmeasurable" because of the version difference and passed, stopping nothing). This reads the semgrep version in uv.lock
and calls it from uv's tool store as `uv tool run --from semgrep==<version> semgrep`. The version is decided only by the uv.lock in the tree, and the store is
one per machine (installed only once however many working trees there are; no `.venv` is made in the working tree).

Usage: uv run --no-project --python <version> python scripts/semgrep_locked.py <semgrep arguments...> (at the repo root. Returns semgrep's output and exit value as they are).
If uv.lock has no semgrep, name the reason and stop with 1 (never silently fall back to the search-path one). The pre-commit called by the plain commit hook decides its version
by the same reading (scripts/precommit_locked.py).

locked_version is the one place that reads the uv.lock version in doeff: the check measured by the Hy version (ADR-DOE-HY-008's
locked-hy-version, packages/doeff-hy/tests/test_bytecode_guard.py) also reads this file by path and calls it. The hook runs outside doeff's environment
(`uv run --no-project`), so the reader is written with only the standard library. If the reading is changed, the check measured by the Hy version in uv.lock is selected by [[tool.doeff.contract-tests]] in the root
pyproject.toml (tests/test_contract_table_follows_packages.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import tomllib

LOCK_FILE: str = "uv.lock"
PACKAGE: str = "semgrep"


def locked_version(top: Path, package: str = PACKAGE) -> str:
    """The version of the package (default semgrep) that uv.lock of tree top decides. If there is no lock, the package does not have exactly one row, or the row is not a version installable from an index
    (git, path, etc.), name the reason and stop (never fall back to a default version) -- every user installs `<package>==<version>` from the index, so
    installing with the version name of a row outside the index would measure something different from the lock."""
    lock: Path = top / LOCK_FILE
    if not lock.is_file():
        raise SystemExit(f"{lock} that decides the version of {package} is missing -- never falls back to a default version or the search-path {package}")
    parsed: dict[str, object] = tomllib.loads(lock.read_text(encoding="utf-8"))
    packages: object = parsed.get("package", [])
    rows: list[dict[str, object]] = (
        [entry for entry in packages if isinstance(entry, dict) and entry.get("name") == package]
        if isinstance(packages, list) else []
    )
    if len(rows) != 1:
        versions: list[object] = [row.get("version") for row in rows]
        raise SystemExit(f"the version of {package} in {lock} is not a single one ({versions}) -- not falling back to the default version or the search-path {package}")
    source: object = rows[0].get("source")
    if not (isinstance(source, dict) and "registry" in source):
        raise SystemExit(f"{lock} の {package} が index から入れる版でない({source})— `{package}==<版>` で lock と同じ物を入れられない")
    return str(rows[0]["version"])


def locked_command(top: Path, package: str = PACKAGE) -> list[str]:
    """The command that calls the package (default semgrep) at the uv.lock version from uv's tool store (depends on neither the working tree's `.venv` nor the search path). The tool's python is the version of the python running this
    script (the version the hook config declared with `uv run --python`) -- if not passed, uv would pick the tree's .python-version (3.14t)
    and semgrep's dependency ruamel-yaml-clib could not be built."""
    running: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    return ["uv", "tool", "run", "--python", running, "--from", f"{package}=={locked_version(top, package)}", package]


def main(argv: list[str]) -> int:
    """Replace the process, passing the arguments to the semgrep of the version in the uv.lock at the repo root (the current dir)."""
    command: list[str] = locked_command(Path.cwd())
    os.execvp(command[0], [*command, *argv])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
