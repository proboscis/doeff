"""An entry point that pins the doeff-linter called by doeff's commit hook to a binary built from the same build inputs as the key the baseline file declares.

What was broken: the hook called `doeff-linter` on the search path (on zeus, each seat installs it by hand in ~/.cargo/bin), and if its version differed from the baseline's it
declared itself "unmeasurable" and passed. Every time a change touched the linter's source, the hook's linter item stopped nothing until someone
reinstalled the shared binary (the evening of 2026-10-02). land-arm automatically rebuilds the dev build whenever main's linter changes, but the hook did
not look at it.

Key = a sha256 over a tree set of build inputs only: the git object ids of packages/doeff-linter and the path dependencies of its Cargo.toml (dependencies, build-dependencies),
namely `src`, `data` (JSON pulled in with include_str!), `Cargo.toml`, `Cargo.lock`, and `build.rs`. It does not move for changes to tests or
READMEs only, and does move for changes to the src of a path dependency (doeff-indexer).

Search order (nothing is built inside the hook -- a cold build keeps the commit waiting 3-5 minutes and overlaps land-arm's rustc):
  1. The land-arm dev build (~/.local/share/doeff-linter-dev) -- compute the key from the commit in the record installed.json.
  2. The snapshot store (~/.cache/doeff-linter-snapshots -- the default of packages/doeff-linter/scripts/linter_snapshot.py) -- from the sha in the dir name.
  3. If neither has it, None (the caller names it "unmeasurable").
Stores where the key was written at build time (the record field input_key, the file input_key in a snapshot dir -- land-arm asks through the `key <commit>` entry and writes it) are
checked against that key without consulting git -- because doeff's daily lint stage runs on a tree without .git (a copy made by remote_check) (4th change of #2906).
The stores are at fixed places under HOME and are not replaced by environment variables (DOEFF004 -- tests point HOME at a temporary dir). On a machine where the snapshot tool was placed elsewhere with
XDG_CACHE_HOME or DOEFF_LINTER_SNAPSHOT_DIR, the snapshot is not found and the result is "unmeasurable" (it does not silently compare against a different version).
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib

CRATE: str = "packages/doeff-linter"
INPUT_ENTRIES: tuple[str, ...] = ("src", "data", "Cargo.toml", "Cargo.lock", "build.rs")
KEY_PREFIX: str = "doeff-linter build inputs "
#: A key ends with the 40-digit digest of the build inputs. Stores written before 2026-11-06 hold the same digest after the former prefix (a Japanese phrase for
#: "doeff-linter build inputs"), so keys are matched by that digest (same_key) -- a store built before the prefix changed is still found.
KEY_DIGEST = re.compile(r"(?:^|\s)([0-9a-f]{40})$")
BIN_NAME: str = "doeff-linter"
DEV_RECORD: str = "installed.json"
#: Keys written at build time -- the field name in land-arm's record (installed.json) and the file name inside a snapshot dir. If written, the check needs no git
#: (the tree of the daily stage is a copy without .git -- #2906). For stores that did not write one, the key is computed with git from the recorded commit as before.
RECORDED_KEY: str = "input_key"
# The built commit that the linter's `--version` declares (DOEFF_LINTER_COMMIT in build.rs). A +dirty one is not made a commit key.
BUILT_COMMIT = re.compile(r"\(doeff ([0-9a-f]{40})\)")


@dataclass(frozen=True)
class Located:
    """A binary whose key matched and where it came from (land-arm dev build / snapshot store) -- to put in the naming sentence."""

    binary: Path
    source: str


@dataclass(frozen=True)
class Candidate:
    """A binary in a store, the commit it was built from, the key written at build time (None if not written), and where it came from."""

    binary: Path
    commit: str
    recorded: str | None
    source: str


def _git(top: Path, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """To run git at the repo root (doeff's hook runs only inside doeff's repo, so the GIT_DIR git passes points at the same repo)."""
    return subprocess.run(["git", *args], cwd=top, input=stdin, capture_output=True, text=True, check=False)


def crates(top: Path, commit: str) -> tuple[str, ...] | None:
    """The linter crate of a commit and the dirs of the path dependencies of its Cargo.toml (from the repo root). None if git does not know the commit or Cargo.toml."""
    shown: subprocess.CompletedProcess[str] = _git(top, ["show", f"{commit}:{CRATE}/Cargo.toml"])
    if shown.returncode != 0:
        return None
    manifest: dict[str, object] = tomllib.loads(shown.stdout)
    paths: list[str] = [
        str(spec["path"])
        for section in ("dependencies", "build-dependencies")
        if isinstance(table := manifest.get(section), dict)
        for spec in table.values()
        if isinstance(spec, dict) and "path" in spec
    ]
    return (CRATE, *sorted(posixpath.normpath(posixpath.join(CRATE, path)) for path in paths))


def input_key(top: Path, commit: str) -> str | None:
    """The key of a commit's build inputs. None if git does not know the commit (not made a key -- never silently compared against a different key)."""
    found: tuple[str, ...] | None = crates(top, commit)
    if found is None:
        return None
    specs: list[str] = [f"{crate}/{entry}" for crate in found for entry in INPUT_ENTRIES]
    checked: subprocess.CompletedProcess[str] = _git(
        top, ["cat-file", "--batch-check=%(objectname)"], stdin="".join(f"{commit}:{spec}\n" for spec in specs),
    )
    answers: list[str] = checked.stdout.splitlines()
    if checked.returncode != 0 or len(answers) != len(specs):
        return None
    # An entry that is absent (such as a crate with no build.rs) goes into the key as "absent" -- the key moves if it is added later.
    objects: list[str] = [answer if not answer.endswith("missing") else "-" for answer in answers]
    lines: str = "\n".join(f"{spec} {obj}" for spec, obj in zip(specs, objects, strict=True))
    return KEY_PREFIX + hashlib.sha256(lines.encode("utf-8")).hexdigest()[:40]


def binary_key(top: Path, binary: Path) -> str:
    """The key of a binary: the key of the build inputs of the commit that `--version` declares. When it declares none, is +dirty, or the commit is unknown to git, the first
    line of `--version` itself (matches no store's key, so it becomes "unmeasurable" -- never silently compared against a different version)."""
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        [str(binary), "--version"], cwd=top, capture_output=True, text=True, check=False,
    )
    lines: list[str] = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not lines:
        raise SystemExit(f"could not read the version of {binary} (rc {completed.returncode}): {completed.stderr.strip()[:300]}")
    named: str = lines[0].strip()
    built = BUILT_COMMIT.search(named)
    key: str | None = input_key(top, built.group(1)) if built is not None and "+dirty" not in named else None
    return key if key is not None else named


def dev_dir() -> Path:
    """The store of the land-arm dev build."""
    return Path.home() / ".local" / "share" / "doeff-linter-dev"


def snapshot_dir() -> Path:
    """The snapshot store (the default of packages/doeff-linter/scripts/linter_snapshot.py)."""
    return Path.home() / ".cache" / "doeff-linter-snapshots"


def _dev_candidate() -> list[Candidate]:
    """The land-arm dev build (only when the recorded commit and the binary are both present)."""
    record: Path = dev_dir() / DEV_RECORD
    binary: Path = dev_dir() / BIN_NAME
    if not (record.is_file() and binary.is_file()):
        return []
    raw: object = json.loads(record.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(commit := raw.get("commit"), str):
        return []
    recorded: object = raw.get(RECORDED_KEY)
    return [Candidate(binary, commit, recorded if isinstance(recorded, str) else None, "land-arm dev build")]


def _snapshot_candidates() -> list[Candidate]:
    """Binaries in the snapshot store (dir name = 40-digit sha) -- newest first (check the likely hits first)."""
    store: Path = snapshot_dir()
    if not store.is_dir():
        return []
    built: list[Path] = [d for d in store.iterdir() if re.fullmatch(r"[0-9a-f]{40}", d.name) and (d / BIN_NAME).is_file()]
    return [
        Candidate(d / BIN_NAME, d.name,
                  (d / RECORDED_KEY).read_text(encoding="utf-8").strip() if (d / RECORDED_KEY).is_file() else None,
                  "snapshot store")
        for d in sorted(built, key=lambda d: (d / BIN_NAME).stat().st_mtime, reverse=True)
    ]


def key_digest(key: str) -> str:
    """The digest a key ends with (the whole text when it ends with none -- such as the `--version` line binary_key falls back to)."""
    found = KEY_DIGEST.search(key)
    return found.group(1) if found is not None else key


def same_key(first: str | None, second: str | None) -> bool:
    """Whether two keys name the same build inputs (by digest, so a key written under the former prefix matches -- KEY_DIGEST). None matches nothing."""
    return first is not None and second is not None and key_digest(first) == key_digest(second)


def candidate_key(top: Path, candidate: Candidate) -> str | None:
    """The key of a binary in a store: the key written at build time if present (no git), otherwise computed with git from the built commit."""
    return candidate.recorded if candidate.recorded is not None else input_key(top, candidate.commit)


def locate(top: Path, key: str) -> Located | None:
    """Search for a binary whose key matches, in the order land-arm dev build -> snapshot store (nothing is built). None if absent."""
    for candidate in (*_dev_candidate(), *_snapshot_candidates()):
        if same_key(candidate_key(top, candidate), key):
            return Located(candidate.binary, candidate.source)
    return None


def searched() -> str:
    """The stores searched, to put in the naming sentence."""
    return f"land-arm dev build {dev_dir()}, snapshot store {snapshot_dir()}"


def dev_key(top: Path) -> str | None:
    """The key of the land-arm dev build (None if there is no dev build or git does not know the recorded commit) -- to say "it becomes measurable once merged"."""
    found: list[Candidate] = _dev_candidate()
    return candidate_key(top, found[0]) if found else None


def main(argv: list[str]) -> int:
    """Two entries (called at the repo root; the commit defaults to HEAD):
    `which [<commit>]` -- print on one line the path of the binary whose key matches the commit's build inputs (called by the shell hook --
    scripts/lint-doeff-cluster.sh). If absent, print the reason and exit 3 (the caller names it "unmeasurable").
    `key [<commit>]` -- print on one line the key of the commit's build inputs (written into the record of the version land-arm built -- the key's definition lives in this one place).
    Exit 3 if git does not know the commit."""
    if argv[:1] not in (["which"], ["key"]) or len(argv) > 2:
        print("usage: doeff_linter_locked.py which|key [<commit>]", file=sys.stderr)
        return 2
    top: Path = Path.cwd()
    commit: str = argv[1] if len(argv) == 2 else "HEAD"
    key: str | None = input_key(top, commit)
    if argv[0] == "key":
        if key is None:
            print(f"git cannot read the build inputs of the linter at {commit}", file=sys.stderr)
            return 3
        print(key)
        return 0
    located: Located | None = locate(top, key) if key is not None else None
    if located is None:
        print(f"no binary for the linter's build inputs ({key}) at {commit} in {searched()}", file=sys.stderr)
        return 3
    print(located.binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
