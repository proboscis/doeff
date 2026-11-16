"""Failure cases of the build commit (_build_commit in tools/doeff_cargo_backend.py, ADR-DOE-BUILD-001 R4d).

doeff-linter's build.rs embeds the doeff commit it was built from, and a downstream commit hook looks that commit up in its own doeff checkout. Before the fix,
build.rs read git's HEAD, which the wheel store key did not see: a wheel built in a checkout whose HEAD was a commit nobody else had (not pushed) was handed to
every checkout with the same source, and their linter named a commit their doeff did not have (2026-11-12). Here the checkouts are real git repos (a canonical
one standing for the shared doeff, clones standing for worktrees); cargo is replaced by a fake build that records the commit the entry passed to build.rs.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
ENV = "DOEFF_LINTER_BUILD_COMMIT"
PACKAGE = Path("packages") / "probe-linter"
CLAIM = "probe_linter/BUILD_COMMIT"
WHEEL = "probe_linter-0.1.0-py3-none-linux_x86_64.whl"


def _backend() -> ModuleType:
    """Load the build entry point as a module (tools is not a package, so by path)."""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_build_commit_under_test", BACKEND)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks up its defining module from sys.modules, so register it by name before loading.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _child_env(name: str) -> str | None:
    """The value of an environment variable as a child of the build sees it (None if unset). build.rs runs as a child of cargo and reads the env it inherits,
    so the fake build reads it the same way; /usr/bin/env by absolute path, so the tests that empty PATH still read it."""
    listed = subprocess.run(["/usr/bin/env"], capture_output=True, text=True, check=True).stdout
    return next((line.split("=", 1)[1] for line in listed.splitlines() if line.startswith(f"{name}=")), None)


def fake_build(target: Path, wheel_directory: str, config_settings: object) -> str:
    """Fake build: like build.rs, embeds the commit a child of the build reads from env DOEFF_LINTER_BUILD_COMMIT into the wheel (CLAIM)."""
    claim = _child_env(ENV) or ""
    files = (
        (CLAIM, claim.encode("utf-8")),
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
    monkeypatch.delenv(ENV, raising=False)
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
    """The shared doeff checkout (what the downstream hook looks commits up in): a package that declares its build commit, then a docs-only commit."""
    repo = tmp_path / "canonical"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    pyproject = (
        '[project]\nname = "probe-linter"\nversion = "0.1.0"\n\n'
        '[tool.uv]\ncache-keys = [{ file = "pyproject.toml" }, { file = "Cargo.toml" }, { file = "src/**/*.rs" }]\n\n'
        f'[tool.doeff-cargo-backend]\nbuild-commit-env = "{ENV}"\n'
    )
    _commit(
        repo,
        {f"{PACKAGE}/pyproject.toml": pyproject, f"{PACKAGE}/Cargo.toml": '[package]\nname = "probe-linter"\n', f"{PACKAGE}/src/main.rs": "fn main() {}\n"},
        "linter",
    )
    _commit(repo, {"docs/note.md": "one\n"}, "docs")
    return repo


def _clone(canonical: Path, path: Path) -> Path:
    _git(canonical.parent, "clone", "-q", str(canonical), str(path))
    return path


def _has(repo: Path, commit: str) -> bool:
    """Whether the commit is in the repo (what the downstream hook asks its doeff checkout)."""
    return subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, capture_output=True, check=False).returncode == 0


@dataclass(frozen=True)
class Prepared:
    """One preparation of a checkout's linter through the entry: claim = the commit its wheel names; source = the wheel's origin (built = this call built it)."""

    claim: str
    source: str


def _prepare(backend: ModuleType, checkout: Path) -> Prepared:
    """Prepare the checkout's linter through the entry (stored_wheel with the fake build)."""
    stored = backend.stored_wheel(checkout / PACKAGE, None, fake_build)
    with zipfile.ZipFile(stored.path) as archive:
        return Prepared(claim=archive.read(CLAIM).decode("utf-8"), source=stored.source)


def test_a_wheel_built_on_an_unpushed_commit_does_not_name_it_to_other_checkouts(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # Failure case (2026-11-12): a worktree whose HEAD is a commit nobody else has builds the linter first; then every checkout with the same linter source took
    # that wheel and its linter named the unpushed commit, which the shared doeff did not have (the downstream hook stopped every commit as a broken environment).
    linter_change = _git(canonical, "log", "-1", "--format=%H", "--", str(PACKAGE))
    local = _clone(canonical, tmp_path / "wt-local")
    unpushed = _commit(local, {"docs/note.md": "two\n"}, "local only")
    assert _prepare(backend, local) == Prepared(claim=linter_change, source="built")
    other = _prepare(backend, _clone(canonical, tmp_path / "wt-other"))
    assert other.source == "local", "the same linter source is still built once and shared"
    assert other.claim != unpushed, other
    assert _has(canonical, other.claim), other


def test_a_linter_change_landed_as_another_commit_is_built_again(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # The unpushed commit itself changes the linter, and the same change then lands on the shared doeff as another commit (a rebase in the merge queue): the
    # contents match, so a key of contents alone hands the landed checkouts the wheel naming the unpushed commit. The commit is in the key, so they build their own.
    local = _clone(canonical, tmp_path / "wt-local")
    change = {f"{PACKAGE}/src/main.rs": 'fn main() { println!("new rule"); }\n'}
    unpushed = _commit(local, change, "linter change, local")
    assert _prepare(backend, local) == Prepared(claim=unpushed, source="built")
    landed = _commit(canonical, change, "linter change, landed")
    assert _prepare(backend, _clone(canonical, tmp_path / "wt-other")) == Prepared(claim=landed, source="built")


def test_uncommitted_changes_to_the_key_files_are_named_dirty(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # A build carrying changes the commit does not have names `<commit>+dirty` (as build.rs did), and does not share the clean wheel's key. A change outside the
    # key files (docs) changes neither.
    checkout = _clone(canonical, tmp_path / "wt")
    linter_change = _git(canonical, "log", "-1", "--format=%H", "--", str(PACKAGE))
    (checkout / "docs" / "note.md").write_text("edited\n")
    assert _prepare(backend, checkout) == Prepared(claim=linter_change, source="built")
    (checkout / PACKAGE / "src" / "rule.rs").write_text("pub fn rule() {}\n")
    assert _prepare(backend, checkout) == Prepared(claim=f"{linter_change}+dirty", source="built")


def test_a_caller_given_commit_is_used_as_is_and_keyed(backend: ModuleType, canonical: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The caller's value in the env (the automatic rebuild passes a 40-digit sha) is used as is, as build.rs does, and is part of the key; the env is restored after the build.
    checkout = _clone(canonical, tmp_path / "wt")
    monkeypatch.setenv(ENV, "a" * 40)
    assert _prepare(backend, checkout) == Prepared(claim="a" * 40, source="built")
    monkeypatch.setenv(ENV, "b" * 40)
    assert _prepare(backend, checkout) == Prepared(claim="b" * 40, source="built")
    monkeypatch.delenv(ENV)
    assert _prepare(backend, checkout).source == "built"
    assert _child_env(ENV) is None


def test_another_repos_git_env_does_not_decide_the_commit(backend: ModuleType, canonical: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Built inside another repo's git hook (its hook syncs the environment), git passes GIT_DIR of that repo; the entry still reads the package's own repo.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _git(elsewhere, "init", "-q")
    _commit(elsewhere, {"x": "x\n"}, "elsewhere")
    checkout = _clone(canonical, tmp_path / "wt")
    linter_change = _git(canonical, "log", "-1", "--format=%H", "--", str(PACKAGE))
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    assert _prepare(backend, checkout).claim == linter_change


def test_without_git_the_commit_is_unknown_and_the_key_is_still_made(backend: ModuleType, canonical: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A tree that is not a git checkout (an unpacked archive), or a machine without git on PATH: the commit is `unknown` (the word build.rs uses) and making the key does
    # not fail (ADR-DOE-BUILD-001 R4b -- the key is needed before the store lookup).
    unpacked = tmp_path / "unpacked"
    shutil.copytree(canonical / PACKAGE, unpacked / PACKAGE)
    assert _prepare(backend, unpacked) == Prepared(claim="unknown", source="built")
    checkout = _clone(canonical, tmp_path / "wt")
    no_tools = tmp_path / "no-tools"
    no_tools.mkdir()
    monkeypatch.setenv("PATH", str(no_tools))
    # Same source and the same unknown commit: the unpacked tree's wheel is reused.
    assert _prepare(backend, checkout) == Prepared(claim="unknown", source="local")


def test_a_package_without_the_declaration_keeps_its_key(backend: ModuleType, canonical: Path, tmp_path: Path) -> None:
    # Packages that embed no commit (doeff-vm and the rest) have no build commit, so their keys -- and the stored and remote wheels under them -- do not change.
    checkout = _clone(canonical, tmp_path / "wt")
    pyproject = checkout / PACKAGE / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().split("[tool.doeff-cargo-backend]")[0])
    assert backend._build_commit(checkout / PACKAGE) is None
    assert backend._wheel_slot(checkout / PACKAGE, None, None) == backend._wheel_slot(checkout / PACKAGE, None, backend._build_commit(checkout / PACKAGE))


def test_doeff_linter_declares_the_env_its_build_rs_reads() -> None:
    # The real package: the entry passes the commit in the env doeff-linter's build.rs reads (a renamed env would silently fall back to git's HEAD).
    import tomllib

    linter = REPO / "packages" / "doeff-linter"
    declared = tomllib.loads((linter / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff-cargo-backend"]["build-commit-env"]
    assert declared == ENV
    assert f'std::env::var("{ENV}")' in (linter / "build.rs").read_text(encoding="utf-8")
