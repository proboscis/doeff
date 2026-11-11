"""Test of the doeff-linter store (packages/doeff-linter/scripts/linter_snapshot.py).

Simulates the build with a fake cargo (first on PATH) and extracts the snapshot from a real git repo. The script is fired in the entry shape each repo's hook calls
(`uv run --script <path> <checkout> <commit>`). For the cargo target store (#3520), the script is loaded as a module and only the answer of the function derived from the env
mapping is checked.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parent.parent / "packages" / "doeff-linter" / "scripts" / "linter_snapshot.py"


def _module(name: str, path: Path) -> ModuleType:
    """Load the script as a module and directly try the pure function (the one that derives the store from an env mapping)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SNAPSHOT = _module("linter_snapshot", SCRIPT)

# Fake cargo -- reads the snapshot's marker and places a linter that claims DOEFF_LINTER_BUILD_COMMIT (and FAKE_CARGO_SUFFIX). Counts how many times it built.
FAKE_CARGO = """#!/bin/sh
manifest=$5
marker=$(cat "$(dirname "$manifest")/marker")
mkdir -p "$CARGO_TARGET_DIR/release"
echo built >> "$FAKE_CARGO_COUNT"
echo "$CARGO_BUILD_BUILD_DIR" >> "$FAKE_CARGO_COUNT.build-dir"
sleep 1
printf '#!/bin/sh\\necho "doeff-linter 0.2.0 (doeff %s%s)"\\necho "%s"\\n' "$DOEFF_LINTER_BUILD_COMMIT" "$FAKE_CARGO_SUFFIX" "$marker" > "$CARGO_TARGET_DIR/release/doeff-linter"
chmod +x "$CARGO_TARGET_DIR/release/doeff-linter"
"""


def stage(tmp_path: Path) -> Path:
    """Make a temporary doeff repo with the linter's 2 input dirs committed (marker = committed), and a fake cargo bin."""
    repo = tmp_path / "doeff"
    for rel in ("packages/doeff-linter", "packages/doeff-indexer"):
        (repo / rel).mkdir(parents=True)
    (repo / "packages/doeff-linter/Cargo.toml").write_text('[package]\nname = "doeff-linter"\n', encoding="utf-8")
    (repo / "packages/doeff-linter/marker").write_text("committed", encoding="utf-8")
    (repo / "packages/doeff-indexer/Cargo.toml").write_text('[package]\nname = "doeff-indexer"\n', encoding="utf-8")
    # Fire git in a minimal environment (`env -i` -- reads neither the outer GIT_* nor the user's git config).
    git_env = ["env", "-i", f"PATH={os.pathsep.join(os.get_exec_path())}", f"HOME={tmp_path}"]
    for argv in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
        subprocess.run([*git_env, "git", "-C", str(repo), *argv], check=True, capture_output=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cargo").write_text(FAKE_CARGO, encoding="utf-8")
    (bin_dir / "cargo").chmod(0o755)
    return repo


def environ(tmp_path: Path, suffix: str) -> list[str]:
    """The `env` prepended to the child command -- the child inherits this process's environment, puts the fake cargo first on PATH, and adds 4 names. The cache root
    (XDG_CACHE_HOME) is also pointed under tmp_path so the fake cargo does not write to the machine's real cargo target."""
    return [
        "env",
        f"PATH={os.pathsep.join([str(tmp_path / 'bin'), *os.get_exec_path()])}",
        f"DOEFF_LINTER_SNAPSHOT_DIR={tmp_path / 'store'}",
        f"XDG_CACHE_HOME={tmp_path / 'cache'}",
        f"FAKE_CARGO_COUNT={tmp_path / 'count'}",
        f"FAKE_CARGO_SUFFIX={suffix}",
    ]


def snapshot(repo: Path, env: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [*env, "uv", "run", "--script", str(SCRIPT), str(repo), "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_three_concurrent_calls_build_the_snapshot_once(tmp_path: Path) -> None:
    # Even if 3 callers ask for the same sha simultaneously, it is built only once, and all 3 receive the linter from the same store (the lock is per sha).
    # Does not read the working tree's uncommitted changes (marker = moving); builds from the snapshot of the commit object (committed).
    repo = stage(tmp_path)
    (repo / "packages/doeff-linter/marker").write_text("moving", encoding="utf-8")
    env = environ(tmp_path, "")
    procs = [snapshot(repo, env) for _ in range(3)]
    outs = [p.communicate(timeout=50) for p in procs]
    for proc, (_, err) in zip(procs, outs, strict=True):
        assert proc.returncode == 0, err
    paths = {out.strip() for out, _ in outs}
    assert len(paths) == 1, paths
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\n"
    printed = subprocess.run([next(iter(paths))], capture_output=True, text=True, check=True).stdout
    assert "committed" in printed
    assert "moving" not in printed
    # Temporary names and mid-build names do not remain.
    assert sorted(p.suffix for p in (tmp_path / "store").iterdir()) == ["", ".lock"]


def test_a_built_snapshot_is_reused_without_building(tmp_path: Path) -> None:
    # A sha already in the store returns the same path without building.
    repo = stage(tmp_path)
    env = environ(tmp_path, "")
    first = snapshot(repo, env).communicate(timeout=50)[0].strip()
    second = snapshot(repo, env).communicate(timeout=50)[0].strip()
    assert first == second
    assert first.endswith("/doeff-linter")
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\n"


def test_two_shas_build_into_one_kept_cargo_target(tmp_path: Path) -> None:
    # 2 different shas build in the same cargo target at the cache root (<XDG_CACHE_HOME>/cargo-target/doeff-linter), and the target remains after building --
    # to reuse dependency crates across shas (#2977). Counterexample: in the earlier form that built in a temporary dir and deleted it, the target does not remain and this test is red.
    # In the form before building next to the store (<store's parent>/cargo-target) (before #3520), there is also no target under the cache root and it is red.
    repo = stage(tmp_path)
    env = environ(tmp_path, "")
    first = snapshot(repo, env).communicate(timeout=50)[0].strip()
    (repo / "packages/doeff-linter/marker").write_text("second", encoding="utf-8")
    git_env = ["env", "-i", f"PATH={os.pathsep.join(os.get_exec_path())}", f"HOME={tmp_path}"]
    subprocess.run([*git_env, "git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "y"],
                   check=True, capture_output=True)
    second = snapshot(repo, env).communicate(timeout=50)[0].strip()
    assert first != second
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\nbuilt\n"
    kept = tmp_path / "cache" / "cargo-target" / "doeff-linter" / "release" / "doeff-linter"
    assert kept.exists()
    assert "second" in subprocess.run([str(kept)], capture_output=True, text=True, check=True).stdout


def test_the_build_dir_is_the_shared_target_even_when_the_environment_points_it_elsewhere(tmp_path: Path) -> None:
    # A machine can point cargo's intermediates at `<dir>/{workspace-path-hash}`; the cross-section is extracted into a new temporary dir per call,
    # so that hash changed every call and every build started cold (2026-11-11). Both builds must hand cargo the shared target as the build dir.
    # Counterexample: in the earlier form that passed the environment's value through, cargo receives the template and this test is red.
    repo = stage(tmp_path)
    env = [*environ(tmp_path, ""), f"CARGO_BUILD_BUILD_DIR={tmp_path / 'ram' / '{workspace-path-hash}'}"]
    snapshot(repo, env).communicate(timeout=50)
    (repo / "packages/doeff-linter/marker").write_text("second", encoding="utf-8")
    git_env = ["env", "-i", f"PATH={os.pathsep.join(os.get_exec_path())}", f"HOME={tmp_path}"]
    subprocess.run([*git_env, "git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "y"],
                   check=True, capture_output=True)
    snapshot(repo, env).communicate(timeout=50)
    shared = tmp_path / "cache" / "cargo-target" / "doeff-linter"
    assert (tmp_path / "count.build-dir").read_text(encoding="utf-8") == f"{shared}\n{shared}\n"


def test_the_cargo_target_follows_the_cache_root_not_the_snapshot_store(tmp_path: Path) -> None:
    # Failing case: even on a machine where DOEFF_LINTER_SNAPSHOT_DIR is kept at <HOME>/.cache to match the store reader and the cache root
    # (XDG_CACHE_HOME) points to a different disk, the cargo target goes under the cache root. Counterexample: in the earlier form that derived the target from next to the store
    # (<store's parent>/cargo-target), it would be <HOME>/.cache/cargo-target/doeff-linter and red.
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    pinned = {
        "HOME": str(home),
        "DOEFF_LINTER_SNAPSHOT_DIR": str(home / ".cache" / "doeff-linter-snapshots"),
        "XDG_CACHE_HOME": str(xdg),
    }
    assert SNAPSHOT.snapshot_store(pinned) == home / ".cache" / "doeff-linter-snapshots"
    assert SNAPSHOT.shared_cargo_target(pinned) == xdg / "cargo-target" / "doeff-linter"
    # On a machine with no cache root declaration, it is under HOME's .cache.
    unset = {"HOME": str(home), "DOEFF_LINTER_SNAPSHOT_DIR": str(home / ".cache" / "doeff-linter-snapshots")}
    assert SNAPSHOT.shared_cargo_target(unset) == home / ".cache" / "cargo-target" / "doeff-linter"


def test_a_linter_not_naming_the_sha_is_not_placed(tmp_path: Path) -> None:
    # Counterexample: if the built linter does not claim exactly the sha (the local-change marker +dirty), it is not placed in the store, prints one line with the reason, and exits with 1.
    repo = stage(tmp_path)
    proc = snapshot(repo, environ(tmp_path, "+dirty"))
    out, err = proc.communicate(timeout=50)
    assert proc.returncode == 1, (out, err)
    assert out == ""
    assert "the built linter does not name" in err
    assert [p for p in (tmp_path / "store").iterdir() if p.is_dir()] == []


KEY_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "doeff_linter_locked.py"


def commit_all(repo: Path, tmp_path: Path, message: str) -> str:
    """Commit all changes to the temporary doeff working tree and return its sha."""
    git_env = ["env", "-i", f"PATH={os.pathsep.join(os.get_exec_path())}", f"HOME={tmp_path}"]
    subprocess.run([*git_env, "git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run([*git_env, "git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message],
                   check=True, capture_output=True)
    return subprocess.run([*git_env, "git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def stage_with_key(tmp_path: Path) -> Path:
    """A temporary doeff at the stage with the build-input key entry (the real scripts/doeff_linter_locked.py) committed -- to exercise the reuse-by-key path."""
    repo = stage(tmp_path)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "doeff_linter_locked.py").write_text(KEY_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    commit_all(repo, tmp_path, "key")
    return repo


def test_a_sha_whose_linter_inputs_did_not_change_is_copied_without_building(tmp_path: Path) -> None:
    # Failing case: the sha of a commit that touches none of the linter's inputs (crate src, data, Cargo.toml, Cargo.lock, build.rs)
    # is copied without building if the store has a linter with the same input key -- do not rebuild the same linter on every pin. Counterexample: in the earlier form keyed only by sha,
    # it builds twice and is red. The copied linter claims the first commit that built it, and the same key is written to the dirs of both snapshots.
    repo = stage_with_key(tmp_path)
    env = environ(tmp_path, "")
    first = Path(snapshot(repo, env).communicate(timeout=50)[0].strip())
    first_sha = first.parent.name
    (repo / "README.md").write_text("change outside the linter's inputs\n", encoding="utf-8")
    second_sha = commit_all(repo, tmp_path, "outside")
    proc = snapshot(repo, env)
    out, err = proc.communicate(timeout=50)
    assert proc.returncode == 0, err
    second = Path(out.strip())
    assert second.parent.name == second_sha != first_sha
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\n"
    printed = subprocess.run([str(second), "--version"], capture_output=True, text=True, check=True).stdout
    assert first_sha in printed and "committed" in printed, printed
    keys = {(d / "input_key").read_text(encoding="utf-8") for d in (first.parent, second.parent)}
    assert len(keys) == 1 and next(iter(keys)).startswith("doeff-linter build inputs "), keys
    assert sorted(p.suffix for p in (tmp_path / "store").iterdir()) == ["", "", ".lock", ".lock"]


def test_a_dir_built_before_keys_were_written_is_keyed_from_its_sha_and_reused(tmp_path: Path) -> None:
    # A snapshot dir built in the form before the key file was written (what the daily machine's store has now) also gets its key looked up from the dir-name sha, matched, and the key written --
    # not rebuilt even the first time after the fix.
    repo = stage_with_key(tmp_path)
    env = environ(tmp_path, "")
    first = Path(snapshot(repo, env).communicate(timeout=50)[0].strip())
    (first.parent / "input_key").unlink()
    (repo / "README.md").write_text("change outside the linter's inputs\n", encoding="utf-8")
    commit_all(repo, tmp_path, "outside")
    proc = snapshot(repo, env)
    out, err = proc.communicate(timeout=50)
    assert proc.returncode == 0, err
    assert Path(out.strip()) != first
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\n"
    assert (first.parent / "input_key").read_text(encoding="utf-8").startswith("doeff-linter build inputs ")


def test_a_sha_whose_linter_inputs_changed_builds_again(tmp_path: Path) -> None:
    # The sha of a commit that touches an input (the crate's Cargo.toml) changes the key, so it is rebuilt (not copied).
    repo = stage_with_key(tmp_path)
    env = environ(tmp_path, "")
    first = snapshot(repo, env).communicate(timeout=50)[0].strip()
    (repo / "packages/doeff-linter/Cargo.toml").write_text('[package]\nname = "doeff-linter"\nversion = "0.2.1"\n', encoding="utf-8")
    (repo / "packages/doeff-linter/marker").write_text("changed", encoding="utf-8")
    commit_all(repo, tmp_path, "input")
    second = snapshot(repo, env).communicate(timeout=50)[0].strip()
    assert first != second
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\nbuilt\n"
    assert "changed" in subprocess.run([second], capture_output=True, text=True, check=True).stdout


def test_a_missing_commit_is_unavailable(tmp_path: Path) -> None:
    # Counterexample: a commit not in doeff is not built, and the reason is returned.
    repo = stage(tmp_path)
    env = environ(tmp_path, "")
    proc = subprocess.Popen(
        [*env, "uv", "run", "--script", str(SCRIPT), str(repo), "0" * 40],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out, err = proc.communicate(timeout=50)
    assert proc.returncode == 1, (out, err)
    assert "is not in doeff" in err
    assert not (tmp_path / "count").exists()


def lookup(repo: Path, env: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [*env, "uv", "run", "--script", str(SCRIPT), "--lookup", str(repo), "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_a_lookup_of_a_sha_not_in_the_store_answers_at_once_without_building(tmp_path: Path) -> None:
    # A commit hook asks with --lookup and must never build (a release / lto build of a new sha did not fit in the 600-second tool call under load 30).
    # A sha the store does not have is answered with the reason and exit 1, and cargo is not run. Counterexample: in the form without --lookup, the hook builds and this is red.
    repo = stage(tmp_path)
    proc = lookup(repo, environ(tmp_path, ""))
    out, err = proc.communicate(timeout=50)
    assert proc.returncode == 1, (out, err)
    assert out == ""
    assert "lookup only — not built" in err
    assert not (tmp_path / "count").exists()


def test_a_lookup_finds_a_placed_linter_and_a_same_key_copy(tmp_path: Path) -> None:
    # A linter placed ahead of time (the plain form) is found by the lookup, and a later sha with the same input key is copied (a copy is not a build).
    repo = stage_with_key(tmp_path)
    env = environ(tmp_path, "")
    built = snapshot(repo, env).communicate(timeout=50)[0].strip()
    found = lookup(repo, env).communicate(timeout=50)[0].strip()
    assert found == built
    (repo / "README.md").write_text("change outside the linter's inputs\n", encoding="utf-8")
    second_sha = commit_all(repo, tmp_path, "outside")
    proc = lookup(repo, env)
    out, err = proc.communicate(timeout=50)
    assert proc.returncode == 0, err
    assert Path(out.strip()).parent.name == second_sha
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\n"


def test_a_lookup_does_not_wait_for_a_sha_being_built(tmp_path: Path) -> None:
    # While another process holds the sha's lock (building it), the lookup answers "being built" at once instead of queueing behind the build.
    repo = stage(tmp_path)
    env = environ(tmp_path, "")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    store = tmp_path / "store"
    store.mkdir()
    with open(store / (sha + ".lock"), "w") as held:
        SNAPSHOT.fcntl.flock(held, SNAPSHOT.fcntl.LOCK_EX)
        proc = lookup(repo, env)
        out, err = proc.communicate(timeout=50)
    assert proc.returncode == 1, (out, err)
    assert "being built" in err
    assert not (tmp_path / "count").exists()
