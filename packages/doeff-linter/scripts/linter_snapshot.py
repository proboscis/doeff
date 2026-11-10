#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""A store of doeff-linter built from one doeff commit — built once per sha on a machine (#2001).

Each repo's (agora-controllers, merge-queue, and others) commit hook calls this one place instead of copying the build logic. It is written with only the standard library
(PEP 723 dependencies are empty) and runs with `uv run --script` even where the repo has no Python environment. Original implementation = agora-controllers'
`scripts/doeff_linter_snapshot.hy` (#1582).

What was broken (#1582): when the hook used doeff-linter from the repo's uv environment, that environment was a path
dependency on the sibling `../doeff` (the shared checkout); each time the shared checkout advanced, every seat that commits rebuilt the same sdist and queued on the same lock, and when cargo
read files in the middle of a fast-forward, old and new got mixed. The build input was a "moving checkout".

What it does:
  * Input = a cross-section extracted from doeff's git objects with `git archive <sha>` (packages/doeff-linter and its path dependency
    packages/doeff-indexer). It does not read working-tree files, so nothing gets mixed even in the middle of a fast-forward.
  * Store = `<store>/<40-digit sha>/doeff-linter` (the default store = $XDG_CACHE_HOME/doeff-linter-snapshots, or under ~/.cache if unset;
    override with env DOEFF_LINTER_SNAPSHOT_DIR). If it exists, it is returned without building.
  * Reuse by input key (over the 70 commits of pins 6ee873af3 to 697796697 on 2026-10-03 the linter's input never changed once, yet
    the daily machine rebuilt the same linter with release and lto at every pin [about 8 minutes each], and the test that fires the commit hook first was cut off at the 300-second limit):
    ask for the sha's build-input key (decided in one place = the `key <commit>` entry of doeff's scripts/doeff_linter_locked.py — the object ids of the crate and path-dependency
    src, data, Cargo.toml, Cargo.lock, and build.rs), and if the store has a linter of another sha that recorded the same key, copy it instead of building and
    return it. The key is written to the file `input_key` in the dir of the cross-section that was built or copied (the name read by doeff_linter_locked.py and dotfiles' doeff_linter_follow.py).
    For a dir built before keys were written, ask for the key from the sha in the dir's name and write it in. The `--version` of a copied linter names the commit that first built that input
    (same input means same linter). For a checkout with no key entry (old doeff), the key is not asked and it builds per sha.
  * Lock = flock on `<store>/<sha>.lock` — per sha, not per path. A second process building the same sha waits for the first and uses what was built.
  * Build method = `cargo build --release --locked`. The cargo target is a shared dir under the machine's cache root (`<cache root>/cargo-target/doeff-linter`;
    cache root = $XDG_CACHE_HOME, or ~/.cache if unset — the same single rule `cache_root` as the store default), and does not move even if the store (env
    DOEFF_LINTER_SNAPSHOT_DIR) is changed — even on a machine that keeps ~/.cache matched to the reader of the store, the large target goes to the cache
    declaration's location. It is not deleted after the build — dependency crates are reused across shas, and for a new sha
    only the doeff-linter and doeff-indexer crates and the link are built (before, a temporary dir was used and every build started cold; the first seat
    to commit after a new pin paid 9 minutes under high load). So that two processes building different shas at the same time do not
    overwrite the same release/ binary, building and copying are serialized one at a time with a flock on `<target>.lock`.
    build.rs has no .git in the cross-section, so the 40-digit sha is passed in env DOEFF_LINTER_BUILD_COMMIT. After confirming that the built binary's `--version` names exactly that sha
    (with no +dirty), it is placed by rename from a temporary name.
  * Names starting with GIT_ are removed from the env passed to git — when called inside another repo's git hook, GIT_DIR and the like that git passes win over `-C`,
    and git reads that repo's objects (#1517).

Usage: uv run --script packages/doeff-linter/scripts/linter_snapshot.py <doeff checkout> <commit>
  If it can build, prints the binary's path on one line and exits 0; if not, prints the reason on one line to stderr and exits 1 (the caller falls back to its own environment's linter),
  and exits 2 on a bad argument.
Tests = tests/test_linter_snapshot.py (3 concurrent processes build only once, working-tree changes are not read, a linter that does not name its sha is not placed,
a sha with unchanged input is copied without building, a sha with changed input is built, and cargo's target follows the cache root rather than the store).
"""


import fcntl
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Dirs extracted into the cross-section (doeff-linter and its Cargo.toml path dependency).
INPUT_DIRS = ("packages/doeff-linter", "packages/doeff-indexer")
MANIFEST = "packages/doeff-linter/Cargo.toml"
BIN_NAME = "doeff-linter"
STORE_ENV = "DOEFF_LINTER_SNAPSHOT_DIR"
BUILD_COMMIT_ENV = "DOEFF_LINTER_BUILD_COMMIT"
# A release / lto build on a cold target takes 3 to 5 minutes (measured on node-h 2026-09-29: 156 to 266 seconds).
BUILD_TIMEOUT_S = 1800
# Spelling of doeff's commit in the linter's `--version` (build.rs's DOEFF_LINTER_COMMIT).
COMMIT_PATTERN = re.compile(r"\(doeff ([0-9a-f]{40})(\+dirty)?\)")
# The entry that answers the build-input key (from the root of doeff's checkout — the one place that decides the key), and the name of the file that writes the key in the cross-section's dir
# (the same spelling as RECORDED_KEY in scripts/doeff_linter_locked.py).
KEY_SCRIPT = "scripts/doeff_linter_locked.py"
RECORDED_KEY = "input_key"
KEY_TIMEOUT_S = 60
SHA_NAME = re.compile(r"[0-9a-f]{40}")
# A key ends with the 40-digit digest of the build inputs; keys written before 2026-11-06 hold the same digest after the former (Japanese) prefix, so
# keys are matched by that digest (the same matching as same_key in scripts/doeff_linter_locked.py).
KEY_DIGEST = re.compile(r"(?:^|\s)([0-9a-f]{40})$")


@dataclass(frozen=True)
class SnapshotReady:
    """Path of the linter built from the sha's cross-section, and whether this call built it (False = it was already in the store)."""

    path: Path
    built: bool


@dataclass(frozen=True)
class SnapshotUnavailable:
    """Reason the store's linter could not be prepared (one line)."""

    reason: str


def git_environ(environ: dict[str, str]) -> dict[str, str]:
    """Env passed to the git that reads doeff — names starting with GIT_ are removed from the caller's env (see the header comment)."""
    return {name: value for name, value in environ.items() if not name.startswith("GIT_")}


def cache_root(environ: dict[str, str]) -> Path:
    """Decide the machine's cache root from env (XDG_CACHE_HOME, or .cache under HOME) — the store default and the cargo target are derived from this one place."""
    cache = environ.get("XDG_CACHE_HOME", "")
    return Path(cache) if cache else Path(environ.get("HOME", "~")).expanduser() / ".cache"


def snapshot_store(environ: dict[str, str]) -> Path:
    """Decide the store dir from env (DOEFF_LINTER_SNAPSHOT_DIR, or under the cache root)."""
    given = environ.get(STORE_ENV, "")
    if given:
        return Path(given)
    return cache_root(environ) / "doeff-linter-snapshots"


def names_commit(printed: str, sha: str) -> bool:
    """Whether the linter's `--version` output names exactly the sha (without the +dirty mark for local changes)."""
    found = COMMIT_PATTERN.search(printed)
    return bool(found and found.group(1) == sha and not found.group(2))


def resolve_commit(checkout: Path, rev: str, env: dict[str, str]) -> str | None:
    """Resolve rev to a 40-digit commit sha in doeff's git (None if absent)."""
    done = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--verify", "-q", rev + "^{commit}"],
        capture_output=True, text=True, check=False, env=env,
    )
    return done.stdout.strip() if done.returncode == 0 else None


def extract_inputs(checkout: Path, sha: str, dest: Path, env: dict[str, str]) -> str | None:
    """Extract the sha's cross-section (INPUT_DIRS) from doeff's git objects into dest. Returns the failure reason (None = extracted)."""
    proc = subprocess.Popen(
        ["git", "-C", str(checkout), "archive", "--format=tar", sha, *INPUT_DIRS],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    assert proc.stdout is not None and proc.stderr is not None
    with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
        archive.extractall(dest, filter="data")
    err = proc.stderr.read()
    if proc.wait() == 0:
        return None
    return "git archive failed: " + err.decode("utf-8", "replace").strip()


def find_cargo(env: dict[str, str]) -> str | None:
    """cargo on PATH, or ~/.cargo/bin/cargo if absent."""
    on_path = shutil.which("cargo", path=env.get("PATH", os.defpath))
    if on_path:
        return on_path
    home_cargo = Path(env.get("HOME", "~")).expanduser() / ".cargo" / "bin" / "cargo"
    return str(home_cargo) if home_cargo.exists() else None


def shared_cargo_target(environ: dict[str, str]) -> Path:
    """The cargo target reused across shas (under the cache root; see the header comment) — it does not follow the store (DOEFF_LINTER_SNAPSHOT_DIR)."""
    return cache_root(environ) / "cargo-target" / BIN_NAME


def build_into(source: Path, sha: str, cargo_target: Path, target: Path, env: dict[str, str]) -> str | None:
    """Build the cross-section in the shared cargo target and copy the built binary to target (a temporary name). Returns the failure reason (None = built)."""
    cargo = find_cargo(env)
    if not cargo:
        return "cargo not found"
    cargo_target.mkdir(parents=True, exist_ok=True)
    with open(cargo_target.with_name(cargo_target.name + ".lock"), "w") as target_lock:
        fcntl.flock(target_lock, fcntl.LOCK_EX)
        done = subprocess.run(
            [cargo, "build", "--release", "--locked", "--manifest-path", str(source / MANIFEST)],
            capture_output=True, text=True, check=False, timeout=BUILD_TIMEOUT_S,
            env={**env, "CARGO_TARGET_DIR": str(cargo_target), BUILD_COMMIT_ENV: sha},
        )
        if done.returncode != 0:
            last = (done.stderr.strip().splitlines() or [""])[-1]
            return "cargo build failed: " + last
        shutil.copy2(cargo_target / "release" / BIN_NAME, target)
    printed = subprocess.run([str(target), "--version"], capture_output=True, text=True, check=False).stdout or ""
    if names_commit(printed, sha):
        return None
    return "the built linter does not name " + sha + ": " + printed.strip()


def inputs_key(checkout: Path, sha: str, env: dict[str, str]) -> str | None:
    """Ask the one place that decides the key (the `key` entry of the checkout's scripts/doeff_linter_locked.py) for the sha's build-input key — so that a linter with the same input
    is not rebuilt. If the entry is absent or does not answer, None (no reuse by key; it builds per sha)."""
    script = checkout / KEY_SCRIPT
    if not script.is_file():
        return None
    try:
        done = subprocess.run(
            [sys.executable, str(script), "key", sha],
            cwd=checkout, capture_output=True, text=True, check=False, timeout=KEY_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        return None
    key = done.stdout.strip()
    return key if done.returncode == 0 and key else None


def placed_key(checkout: Path, placed: Path, env: dict[str, str]) -> str | None:
    """The input key of a cross-section dir in the store — if written, that; if not (a dir built before keys were written), ask using the sha in the dir's name and write it in
    (the same matching as candidate_key in scripts/doeff_linter_locked.py — git is not consulted from then on)."""
    recorded = placed / RECORDED_KEY
    if recorded.is_file():
        return recorded.read_text(encoding="utf-8").strip()
    key = inputs_key(checkout, placed.name, env)
    if key is not None:
        recorded.write_text(key + "\n", encoding="utf-8")
    return key


def _digest(key: str | None) -> str | None:
    """The digest a key ends with (the whole key when it ends with none; None stays None)."""
    found = KEY_DIGEST.search(key) if key is not None else None
    return found.group(1) if found is not None else key


def same_inputs(checkout: Path, store: Path, key: str | None, env: dict[str, str]) -> Path | None:
    """To look in the store for a linter of another sha with the same input key (None if none)."""
    if key is None:
        return None
    for placed in sorted(store.iterdir()):
        if SHA_NAME.fullmatch(placed.name) and (placed / BIN_NAME).is_file() and _digest(placed_key(checkout, placed, env)) == _digest(key):
            return placed / BIN_NAME
    return None


def place(store: Path, sha: str, partial: Path, key: str | None) -> Path:
    """Place the linter at temporary name partial in the sha's cross-section dir and write the input key (key first, binary last by rename — a dir that has the binary
    also has the key)."""
    (store / sha).mkdir(exist_ok=True)
    if key is not None:
        (store / sha / RECORDED_KEY).write_text(key + "\n", encoding="utf-8")
    binary = store / sha / BIN_NAME
    os.replace(partial, binary)
    return binary


def snapshot_linter(checkout: Path, rev: str, environ: dict[str, str]) -> SnapshotReady | SnapshotUnavailable:
    """Return from the store the linter built from the cross-section of doeff's rev — if absent, take the sha's lock; if a linter with the same input exists, copy it; if not, build it only once
    (see the header comment)."""
    env = git_environ(environ)
    sha = resolve_commit(checkout, rev, env)
    if not sha:
        return SnapshotUnavailable("commit " + rev + " is not in doeff's " + str(checkout))
    store = snapshot_store(environ)
    binary = store / sha / BIN_NAME
    try:
        store.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return SnapshotUnavailable("cannot create store " + str(store) + ": " + str(error))
    with open(store / (sha + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binary.exists():
            return SnapshotReady(binary, built=False)
        partial = store / (sha + ".partial")
        key = inputs_key(checkout, sha, env)
        same = same_inputs(checkout, store, key, env)
        if same is not None:
            shutil.copy2(same, partial)
            return SnapshotReady(place(store, sha, partial, key), built=False)
        work = Path(tempfile.mkdtemp(prefix="doeff-linter-snapshot-" + sha[:12] + "-"))
        try:
            failure = extract_inputs(checkout, sha, work, env) or build_into(
                work, sha, shared_cargo_target(environ), partial, env
            )
            if failure:
                return SnapshotUnavailable(failure)
            return SnapshotReady(place(store, sha, partial, key), built=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str]) -> int:
    """If it can build, print the path and return 0; if not, print the reason on one line and return 1; a bad argument returns 2."""
    if len(argv) != 2:
        print("Usage: uv run --script linter_snapshot.py <doeff checkout> <commit>", file=sys.stderr)
        return 2
    got = snapshot_linter(Path(argv[0]), argv[1], dict(os.environ))
    if isinstance(got, SnapshotReady):
        print(got.path)
        return 0
    print("doeff-linter-snapshot: " + got.reason, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
