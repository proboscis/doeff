"""Failure cases of the build-input key (_with_input_key in tools/doeff_cargo_backend.py, ADR-DOE-BUILD-001 R4e).

doeff-linter's `--version` named only the commit its wheel was built at, and a downstream commit hook looked that commit up in its own doeff checkout. The store
shares one wheel per source, so after doeff's history was rewritten (or a wheel was built on a branch commit) the linter named a commit the checkout lacked, the
hook stopped every commit, and the fix it named (rebuild) fetched the same wheel again. The wheel now also names the key of its build inputs -- the answer of
scripts/doeff_linter_locked.py `key <commit>` (git object ids, so the same inputs give the same key under any commit). Here the checkouts are real git repos with
the real key script; cargo is replaced by a fake build that records what the entry passed to build.rs.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
LOCKED = Path("scripts") / "doeff_linter_locked.py"
COMMIT_ENV = "DOEFF_LINTER_BUILD_COMMIT"
KEY_ENV = "DOEFF_LINTER_BUILD_INPUT_KEY"
# The key script reads the crate at this fixed path, so the probe package sits there.
PACKAGE = Path("packages") / "doeff-linter"
NAMED = "probe_linter/NAMED"
WHEEL = "probe_linter-0.1.0-py3-none-linux_x86_64.whl"


def _backend() -> ModuleType:
    """Load the build entry point as a module (tools is not a package, so by path)."""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_input_key_under_test", BACKEND)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks up its defining module from sys.modules, so register it by name before loading.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _child_env(name: str) -> str:
    """The value of an environment variable as a child of the build sees it (empty if unset) -- build.rs runs as a child of cargo and reads the env it inherits."""
    listed = subprocess.run(["/usr/bin/env"], capture_output=True, text=True, check=True).stdout
    return next((line.split("=", 1)[1] for line in listed.splitlines() if line.startswith(f"{name}=")), "")


def fake_build(target: Path, wheel_directory: str, config_settings: object) -> str:
    """Fake build: like build.rs, embeds the commit and the build-input key a child of the build reads from their envs into the wheel (NAMED, one per line)."""
    named = f"{_child_env(COMMIT_ENV)}\n{_child_env(KEY_ENV)}".encode("utf-8")
    files = (
        (NAMED, named),
        ("probe_linter-0.1.0.dist-info/METADATA", b"Metadata-Version: 2.1\nName: probe-linter\nVersion: 0.1.0\n"),
        ("probe_linter-0.1.0.dist-info/WHEEL", b"Wheel-Version: 1.0\nGenerator: fake\nRoot-Is-Purelib: false\nTag: py3-none-linux_x86_64\n"),
    )
    record = "".join(f"{name},sha256={_digest(data)},{len(data)}\n" for name, data in files) + "probe_linter-0.1.0.dist-info/RECORD,,\n"
    with zipfile.ZipFile(Path(wheel_directory) / WHEEL, "w") as archive:
        for member, data in files:
            archive.writestr(member, data)
        archive.writestr("probe_linter-0.1.0.dist-info/RECORD", record)
    return WHEEL


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """Pin the tool versions, point the store and the temporary dir at tmp, and do not consult the remote store."""
    module = _backend()
    monkeypatch.setattr(module, "_tool_versions", lambda: "maturin=fixed")
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "store"))
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", "")
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    monkeypatch.delenv(COMMIT_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)
    return module


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    """Write the files (paths from the repo root), commit them, and return the commit."""
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def canonical(tmp_path: Path) -> Path:
    """The shared doeff checkout: the real key script, and a linter package that declares its build commit and build-input key like doeff-linter does."""
    repo = tmp_path / "canonical"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    pyproject = (
        '[project]\nname = "probe-linter"\nversion = "0.1.0"\n\n'
        '[tool.uv]\ncache-keys = [{ file = "pyproject.toml" }, { file = "Cargo.toml" }, { file = "src/**/*.rs" }]\n\n'
        f'[tool.doeff-cargo-backend]\nbuild-commit-env = "{COMMIT_ENV}"\n'
        f'build-input-key = {{ env = "{KEY_ENV}", script = "../../{LOCKED.as_posix()}" }}\n'
    )
    _commit(
        repo,
        {
            str(LOCKED): (REPO / LOCKED).read_text(encoding="utf-8"),
            f"{PACKAGE}/pyproject.toml": pyproject,
            f"{PACKAGE}/Cargo.toml": '[package]\nname = "probe-linter"\n',
            f"{PACKAGE}/src/main.rs": "fn main() {}\n",
        },
        "linter",
    )
    return repo


def _clone(canonical: Path, path: Path) -> Path:
    _git(canonical.parent, "clone", "-q", str(canonical), str(path))
    return path


def _key(repo: Path, commit: str) -> str:
    """The key the downstream hook asks its doeff checkout for (`doeff_linter_locked.py key <commit>`)."""
    return subprocess.run([sys.executable, "-I", "-B", str(LOCKED), "key", commit], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@dataclass(frozen=True)
class Named:
    """What one prepared wheel names: commit = where it was built; key = what it was built from; source = its origin (built = this call built it)."""

    commit: str
    key: str
    source: str


def _prepare(backend: ModuleType, checkout: Path) -> Named:
    """Prepare the checkout's linter through the entry (stored_wheel with the fake build)."""
    stored = backend.stored_wheel(checkout / PACKAGE, None, fake_build)
    with zipfile.ZipFile(stored.path) as archive:
        commit, key = archive.read(NAMED).decode("utf-8").split("\n")
    return Named(commit=commit, key=key, source=stored.source)


def test_the_same_inputs_name_the_same_key_after_the_history_is_rewritten(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # Failure case (2026-11-15): the shared doeff's history is rewritten (the same tree under a new commit). A wheel built before names a commit the
    # rewritten checkout lacks, so a hook that looks the commit up stops forever. The key it also names is the rewritten checkout's own key.
    before = _clone(canonical, tmp_path / "wt-before")
    old = _prepare(backend, before)
    assert old.commit == _git(before, "rev-parse", "HEAD")
    _git(canonical, "commit", "-q", "--amend", "-m", "linter (rewritten)")
    rewritten = _git(canonical, "rev-parse", "HEAD")
    _git(canonical, "reflog", "expire", "--expire=now", "--all")
    _git(canonical, "gc", "-q", "--prune=now")
    assert subprocess.run(["git", "cat-file", "-e", f"{old.commit}^{{commit}}"], cwd=canonical, capture_output=True, check=False).returncode != 0
    assert old.key == _key(canonical, rewritten), "the key names what was built, which the rewritten checkout still has"
    assert old.key.startswith("doeff-linter build inputs "), old
    after = _prepare(backend, _clone(canonical, tmp_path / "wt-after"))
    assert after == Named(commit=rewritten, key=old.key, source="built")


def test_a_wheel_built_on_a_branch_commit_names_the_landed_key(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # A linter change built on a branch commit, then landed as another commit (a rebase in the merge queue): the commits differ, the keys do not.
    change = {f"{PACKAGE}/src/main.rs": 'fn main() { println!("new rule"); }\n'}
    branch = _clone(canonical, tmp_path / "wt-branch")
    on_branch = _commit(branch, change, "linter change, branch")
    built = _prepare(backend, branch)
    landed = _commit(canonical, change, "linter change, landed")
    assert built.commit == on_branch != landed
    assert built.key == _key(canonical, landed) != _key(canonical, f"{landed}~1")


def test_a_build_carrying_changes_names_no_key(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # A `+dirty` build is no commit's inputs, so it names `unknown` (a downstream hook cannot match it and falls back) rather than the commit's key.
    checkout = _clone(canonical, tmp_path / "wt")
    (checkout / PACKAGE / "src" / "rule.rs").write_text("pub fn rule() {}\n")
    named = _prepare(backend, checkout)
    assert named.commit.endswith("+dirty")
    assert named.key == "unknown"


def test_a_caller_given_key_is_used_as_is_and_an_unknown_commit_names_no_key(
    backend: ModuleType, canonical: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The caller's key is used as is (as the caller's commit is); a caller's commit git does not know gets `unknown`, never another commit's key. The envs are restored.
    checkout = _clone(canonical, tmp_path / "wt")
    monkeypatch.setenv(KEY_ENV, "doeff-linter build inputs " + "c" * 40)
    assert _prepare(backend, checkout).key == "doeff-linter build inputs " + "c" * 40
    monkeypatch.delenv(KEY_ENV)
    monkeypatch.setenv(COMMIT_ENV, "a" * 40)
    assert _prepare(backend, checkout) == Named(commit="a" * 40, key="unknown", source="built")
    monkeypatch.delenv(COMMIT_ENV)
    assert _prepare(backend, checkout).key == _key(canonical, "HEAD")
    assert _child_env(COMMIT_ENV) == "" and _child_env(KEY_ENV) == ""


def test_without_the_key_script_the_key_is_unknown_and_the_store_key_is_still_made(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # An unpacked tree (no git, no script beside it) still makes its store key (ADR-DOE-BUILD-001 R4b) and names `unknown`.
    unpacked = tmp_path / "unpacked"
    shutil.copytree(canonical / PACKAGE, unpacked / PACKAGE)
    assert _prepare(backend, unpacked) == Named(commit="unknown", key="unknown", source="built")


def test_a_wheel_stored_before_the_key_was_embedded_is_not_reused(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # The key is in the store key: a wheel stored under the commit alone (before this change -- it names no key) is in another slot, so the entry builds anew.
    checkout = _clone(canonical, tmp_path / "wt")
    claim = backend._build_commit(checkout / PACKAGE)
    assert claim.key == _key(canonical, claim.value)
    without = backend.BuildCommit(env=claim.env, value=claim.value)
    assert backend._wheel_slot(checkout / PACKAGE, None, claim) != backend._wheel_slot(checkout / PACKAGE, None, without)


def test_doeff_linter_declares_the_key_env_its_build_rs_reads_and_names_it_in_version() -> None:
    # The real package: the declared script is the key's one definition, build.rs reads the declared env, and `--version` names the key on its second line.
    linter = REPO / PACKAGE
    declared = tomllib.loads((linter / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff-cargo-backend"]["build-input-key"]
    assert declared["env"] == KEY_ENV
    assert (linter / declared["script"]).resolve() == (REPO / LOCKED).resolve()
    assert f'std::env::var("{KEY_ENV}")' in (linter / "build.rs").read_text(encoding="utf-8")
    info = (linter / "src" / "build_info.rs").read_text(encoding="utf-8")
    assert '")\\ninputs: ", env!("DOEFF_LINTER_INPUT_KEY")' in info
    assert "long_version = doeff_linter::LONG_VERSION_TEXT" in (linter / "src" / "main.rs").read_text(encoding="utf-8")


def test_the_key_script_reads_the_named_key_before_the_commit(canonical: Path, tmp_path: Path) -> None:
    # The doeff side of the downstream check: a binary naming a commit git does not know is still keyed by the key on its second line; without that line (an
    # older binary) or with `unknown`, the commit decides as before, and an unknown commit gives the first line (unmeasurable).
    spec = importlib.util.spec_from_file_location("doeff_linter_locked_under_test", REPO / LOCKED)
    assert spec is not None
    assert spec.loader is not None
    locked = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = locked
    spec.loader.exec_module(locked)
    key = _key(canonical, "HEAD")
    gone = "d" * 40

    def binary(name: str, printed: str) -> Path:
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\ncat <<'END'\n{printed}\nEND\n")
        path.chmod(0o755)
        return path

    first = f"doeff-linter 0.2.0 (doeff {gone})"
    assert locked.binary_key(canonical, binary("named", f"{first}\ninputs: {key}")) == key
    assert locked.binary_key(canonical, binary("unknown", f"{first}\ninputs: unknown")) == first
    head = _git(canonical, "rev-parse", "HEAD")
    assert locked.binary_key(canonical, binary("older", f"doeff-linter 0.2.0 (doeff {head})")) == key
