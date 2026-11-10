"""Verify the build entry point of doeff's Rust packages (tools/doeff_cargo_backend.py).

The entry point puts cargo's target in a per-build temporary dir outside the package dir and removes it once the wheel is built.
It stops the disk from filling up with a target left in every working tree, and it ensures working trees with different contents
do not mix up builds; this is verified by placing a small bin crate in two places and building with a real maturin and cargo.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
BUILD_TIMEOUT_SECONDS = 300
TEMP_PREFIX = "doeff-cargo-target-"

# Like a PEP 517 frontend, call the build entry point's hook (argv[1]) in the package dir and print the name of the produced file on the last line.
_CALL_HOOK = (
    "import sys; sys.path.insert(0, '.'); import doeff_cargo_backend as b; "
    "hooks = {'build_wheel': b.build_wheel, 'build_editable': b.build_editable, 'build_sdist': b.build_sdist}; "
    "print(hooks[sys.argv[1]](sys.argv[2]))"
)


@dataclass(frozen=True)
class Checkouts:
    """A working tree cut earlier (its source is older) and one cut later."""

    newer: Path
    older: Path


@dataclass(frozen=True)
class BuiltWheel:
    """The wheel path that build_wheel produced, and the entry point's stderr (the one line saying used / built)."""

    wheel: Path
    log: str


def _write_checkout(root: Path, greeting: str, *, body: str | None = None) -> Path:
    """Create a package dir standing in for one working tree (same crate name; only the printed text differs).

    If body is given, it becomes the contents of main.rs (to make a crate that cannot be built).
    """
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[[bin]]\nname = "probe"\npath = "src/main.rs"\n'
    )
    match body:
        case None:
            (root / "src" / "main.rs").write_text(f'fn main() {{ println!("{greeting}"); }}\n')
        case str():
            (root / "src" / "main.rs").write_text(body)
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["maturin>=1.0,<2.0"]\nbuild-backend = "doeff_cargo_backend"\n'
        'backend-path = ["."]\n\n[project]\nname = "probe"\nversion = "0.1.0"\n\n'
        '[tool.maturin]\nbindings = "bin"\n'
    )
    (root / "doeff_cargo_backend.py").symlink_to(BACKEND)
    return root


def _age(root: Path, seconds: int) -> None:
    """Shift the timestamps of the source files into the past -- to stand in for a working tree cut earlier and built later."""
    for path in root.rglob("*"):
        stat = path.stat()
        os.utime(path, (stat.st_atime - seconds, stat.st_mtime - seconds), follow_symlinks=False)


def _two_checkouts(tmp_path: Path) -> Checkouts:
    """Create two working trees: B cut first (older source) and A cut afterwards."""
    older = _write_checkout(tmp_path / "wt-b", "from-B")
    _age(older, 3600)
    newer = _write_checkout(tmp_path / "wt-a", "from-A")
    return Checkouts(newer=newer, older=older)


def _build_env(temp_root: Path, target_dir: Path | None) -> list[str]:
    """The `env` prefix for the build process command: the child inherits this process's environment, and it is placed only when passing CARGO_TARGET_DIR removed (`env -u`),
    pointing the temporary dir location at temp_root.

    CARGO_BUILD_BUILD_DIR is removed too: node-h's worker points it at a per-worktree dir (`/cargo-build/{workspace-path-hash}`), which keeps
    the intermediates of two checkouts apart even when they share a target, so the shared-target counterexample would not reproduce there.
    A test that needs it passes it after this prefix."""
    # The wheel store is also a per-test dir (do not write to the user's ~/.cache, and tests must not pull each other's wheel of the same source).
    # The store outside the machine is not consulted (this test looks at the local build path -- the store test is tests/test_wheel_remote_store.py).
    settings = [f"TMPDIR={temp_root}", f"DOEFF_WHEEL_CACHE={temp_root / 'wheel-cache'}", "DOEFF_WHEEL_REMOTE="]
    match target_dir:
        case None:
            target: list[str] = []
        case Path() as given:
            target = [f"CARGO_TARGET_DIR={given}"]
    return ["env", "-u", "CARGO_TARGET_DIR", "-u", "CARGO_BUILD_BUILD_DIR", *settings, *target]


def _start_build(
    package: Path, wheel_dir: Path, env: list[str], hook: str = "build_wheel"
) -> subprocess.Popen[str]:
    """Start calling the build hook in a separate process as a frontend does (to try concurrent builds, it returns without waiting)."""
    wheel_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [*env, sys.executable, "-c", _CALL_HOOK, hook, str(wheel_dir)],
        cwd=package,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish_build(process: subprocess.Popen[str], wheel_dir: Path) -> Path:
    """Wait for the build to end and return the path of the produced wheel."""
    out, err = process.communicate(timeout=BUILD_TIMEOUT_SECONDS)
    assert process.returncode == 0, err
    return wheel_dir / out.strip().splitlines()[-1]


def _build(package: Path, wheel_dir: Path, env: list[str], hook: str = "build_wheel") -> Path:
    """Build one package with the hook and return the path of the produced file."""
    return _finish_build(_start_build(package, wheel_dir, env, hook), wheel_dir)


def _greeting(wheel: Path, scratch: Path) -> str:
    """Extract the probe binary contained in the wheel and run it to read which working tree's contents it was built from."""
    with zipfile.ZipFile(wheel) as archive:
        (member,) = [name for name in archive.namelist() if name.endswith("/scripts/probe")]
        archive.extract(member, scratch)
    binary = scratch / member
    binary.chmod(0o755)
    return subprocess.run([str(binary)], capture_output=True, text=True, check=True).stdout.strip()


def _leftovers(temp_root: Path) -> list[str]:
    """Names of the build targets left in the temporary dir location."""
    return sorted(path.name for path in temp_root.iterdir() if path.name.startswith(TEMP_PREFIX))


def test_a_shared_target_hands_the_older_checkout_the_other_checkouts_build(tmp_path: Path) -> None:
    """Counterexample: if two working trees share one target, working tree B, cut earlier, silently receives A's build.

    This is because cargo judges old versus new by the source's timestamp and names artifacts by the relative path within the workspace. This is why the entry point does not use a shared
    target and uses a per-build temporary dir.
    """
    checkouts = _two_checkouts(tmp_path)
    newer, older = checkouts.newer, checkouts.older
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, tmp_path / "shared-target")

    greetings = [
        _greeting(
            _build(package, tmp_path / "wheels" / package.name, env), tmp_path / "x" / package.name
        )
        for package in (newer, older)
    ]

    assert greetings == ["from-A", "from-A"]


@pytest.mark.parametrize("hook", ["build_wheel", "build_editable"])
def test_each_checkout_builds_its_own_wheel_and_leaves_no_target(tmp_path: Path, hook: str) -> None:
    """If left to the entry point, the two working trees each get a wheel of their own contents, and no target is left in the package or in the temporary location.

    uv sync installs a workspace member with build_editable, so check with both hooks.
    """
    checkouts = _two_checkouts(tmp_path)
    newer, older = checkouts.newer, checkouts.older
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)

    greetings = [
        _greeting(
            _build(package, tmp_path / "wheels" / package.name, env, hook),
            tmp_path / "x" / package.name,
        )
        for package in (newer, older)
    ]

    assert greetings == ["from-A", "from-B"]
    assert [package.name for package in (newer, older) if (package / "target").exists()] == []
    assert _leftovers(temp_root) == []


def test_two_builds_at_once_each_get_their_own_wheel(tmp_path: Path) -> None:
    """Even running the builds of two working trees at the same time, both succeed and each gets a wheel of its own contents."""
    checkouts = _two_checkouts(tmp_path)
    newer, older = checkouts.newer, checkouts.older
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)

    running = [
        (package, _start_build(package, tmp_path / "wheels" / package.name, env))
        for package in (newer, older)
    ]
    wheels = [
        _finish_build(process, tmp_path / "wheels" / package.name) for package, process in running
    ]

    assert [_greeting(wheel, tmp_path / "x" / wheel.parent.name) for wheel in wheels] == [
        "from-A",
        "from-B",
    ]
    assert _leftovers(temp_root) == []


def test_a_given_target_dir_is_used_and_kept(tmp_path: Path) -> None:
    """If the user passes CARGO_TARGET_DIR, the entry point builds there and does not remove it (an escape hatch for using incremental builds)."""
    newer = _two_checkouts(tmp_path).newer
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    given = tmp_path / "given-target"

    _build(newer, tmp_path / "wheels", _build_env(temp_root, given))

    assert (given / "release" / "probe").is_file()
    assert not (newer / "target").exists()
    assert _leftovers(temp_root) == []


def test_a_temporary_target_keeps_the_intermediates_out_of_the_given_build_dir(tmp_path: Path) -> None:
    """Even if the environment points CARGO_BUILD_BUILD_DIR somewhere (node-h's worker), the temporary-target build puts intermediates in the temporary dir and removes them. Failure case: if per-build
    intermediates remain at the pointed place, they eat the memory location at every uv sync."""
    newer = _two_checkouts(tmp_path).newer
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    build_dir = tmp_path / "build-dir"

    _build(newer, tmp_path / "wheels", [*_build_env(temp_root, None), f"CARGO_BUILD_BUILD_DIR={build_dir}"])

    assert not build_dir.exists()
    assert _leftovers(temp_root) == []


def test_every_maturin_package_builds_through_the_backend() -> None:
    """Every doeff package built with maturin goes through the entry point (a new Rust package does not recreate a target in the working tree)."""
    maturin_packages = sorted(
        pyproject.parent
        for pyproject in REPO.glob("packages/*/pyproject.toml")
        if any(
            requirement.startswith("maturin")
            for requirement in tomllib.loads(pyproject.read_text())
            .get("build-system", {})
            .get("requires", [])
        )
    )
    backends = {
        package.name: tomllib.loads((package / "pyproject.toml").read_text())["build-system"]
        for package in maturin_packages
    }

    assert sorted(backends) == [
        "doeff-effect-analyzer",
        "doeff-indexer",
        "doeff-linter",
        "doeff-vm",
    ]
    assert {
        name: (system["build-backend"], system.get("backend-path"))
        for name, system in backends.items()
    } == {
        "doeff-effect-analyzer": ("doeff_cargo_backend", ["."]),
        "doeff-indexer": ("doeff_indexer_build_backend", ["."]),
        "doeff-linter": ("doeff_cargo_backend", ["."]),
        "doeff-vm": ("doeff_cargo_backend", ["."]),
    }
    assert [
        package.name
        for package in maturin_packages
        if (package / "doeff_cargo_backend.py").resolve() != BACKEND
    ] == []
    indexer_backend = REPO / "packages" / "doeff-indexer" / "doeff_indexer_build_backend.py"
    imported = {
        alias.name
        for node in ast.parse(indexer_backend.read_text()).body
        if isinstance(node, ast.ImportFrom) and node.module == "doeff_cargo_backend"
        for alias in node.names
    }
    # doeff-indexer's entry point also goes through the store entrances (wheel_from_store, editable_from_store) -- do not create a second entrance that fires maturin on its own
    # (ADR-DOE-BUILD-001)。
    assert {"cargo_target_dir", "wheel_from_store", "editable_from_store"} <= imported


def test_a_failed_build_leaves_no_target(tmp_path: Path) -> None:
    """A build that fails to compile also removes its temporary target on the way out (the disk does not shrink with every failure)."""
    broken = _write_checkout(tmp_path / "wt-broken", "", body="fn main() { this is not rust }\n")
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()

    process = _start_build(broken, tmp_path / "wheels", _build_env(temp_root, None))
    process.communicate(timeout=BUILD_TIMEOUT_SECONDS)

    assert process.returncode != 0
    assert not (broken / "target").exists()
    assert _leftovers(temp_root) == []


def test_a_build_sweeps_targets_left_by_killed_builds_but_not_live_ones(tmp_path: Path) -> None:
    """A build stopped by SIGTERM or SIGKILL does no cleanup. The next build removes only dirs whose owner process no longer exists."""
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    finished = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    dead_pid = int(finished.stdout.strip())
    killed = temp_root / f"{TEMP_PREFIX}{dead_pid}-left"
    running = temp_root / f"{TEMP_PREFIX}{os.getpid()}-running"
    foreign = temp_root / f"{TEMP_PREFIX}not-ours"
    for path in (killed, running, foreign):
        (path / "release").mkdir(parents=True)

    _build(_write_checkout(tmp_path / "wt-sweep", "sweep"), tmp_path / "wheels", _build_env(temp_root, None))

    assert _leftovers(temp_root) == sorted([running.name, foreign.name])


def test_every_maturin_package_sdist_carries_the_backend_at_its_root(tmp_path: Path) -> None:
    """For people who build from the PyPI sdist, the entry point's file goes into the sdist root (the same place as pyproject) as an ordinary file.

    For packages with path-dependent crates (doeff-linter, doeff-vm), maturin moves the crate under <name>/,
    so it is copied to the root with the pyproject include. It also checks that building the sdist creates no target in the package.
    """
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)
    call_declared = (
        "import importlib, sys; sys.path.insert(0, '.'); "
        "print(importlib.import_module(sys.argv[1]).build_sdist(sys.argv[2]))"
    )
    missing: dict[str, list[str]] = {}
    for package in sorted(REPO.glob("packages/doeff-*/doeff_cargo_backend.py")):
        root = package.parent
        backend = tomllib.loads((root / "pyproject.toml").read_text())["build-system"][
            "build-backend"
        ]
        had_target = (root / "target").exists()
        out = tmp_path / "sdist" / root.name
        out.mkdir(parents=True)
        built = subprocess.run(
            [*env, sys.executable, "-c", call_declared, backend, str(out)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_SECONDS,
            check=False,  # so stderr can be shown in the assert message on failure
        )
        assert built.returncode == 0, built.stderr
        with tarfile.open(out / built.stdout.strip().splitlines()[-1]) as sdist:
            (top,) = {member.name.split("/", 1)[0] for member in sdist.getmembers()}
            regular = {member.name for member in sdist.getmembers() if member.isfile()}
        wanted = [f"{top}/pyproject.toml", f"{top}/doeff_cargo_backend.py", f"{top}/{backend}.py"]
        missing[root.name] = [name for name in wanted if name not in regular]
        assert (root / "target").exists() == had_target

    assert missing == {name: [] for name in missing}
    assert sorted(missing) == [
        "doeff-effect-analyzer",
        "doeff-indexer",
        "doeff-linter",
        "doeff-vm",
    ]
    assert _leftovers(temp_root) == []


def _build_logged(package: Path, wheel_dir: Path, env: list[str]) -> BuiltWheel:
    """Call build_wheel and return the produced wheel path and the entry point's stderr (the one line saying used / built)."""
    process = _start_build(package, wheel_dir, env)
    out, err = process.communicate(timeout=BUILD_TIMEOUT_SECONDS)
    assert process.returncode == 0, err
    return BuiltWheel(wheel=wheel_dir / out.strip().splitlines()[-1], log=err)


def test_the_same_source_in_a_fresh_checkout_reuses_the_wheel(tmp_path: Path) -> None:
    """Failure case: a working tree recreated with the pin raised has the same contents but different file timestamps and paths -- the entry point looks up
    the earlier wheel by content hash and does not fire cargo (the second time shows no "built" and creates no temporary target)."""
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)
    first = _write_checkout(tmp_path / "wt-first", "same")
    _age(first, 3600)
    built = _build_logged(first, tmp_path / "wheels-first", env)
    assert "wheel built" in built.log, built.log
    fresh = _write_checkout(tmp_path / "wt-fresh", "same")
    again = _build_logged(fresh, tmp_path / "wheels-fresh", env)
    assert "reusing wheel for identical source (no build)" in again.log, again.log
    assert "wheel built" not in again.log, again.log
    assert again.wheel.name == built.wheel.name
    assert _greeting(again.wheel, tmp_path / "scratch") == "same"
    assert _leftovers(temp_root) == []


def test_a_changed_source_builds_again(tmp_path: Path) -> None:
    """If the source contents change, the hash changes, the build is redone, and a wheel of the new contents is returned (the earlier wheel is not silently used)."""
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)
    _build_logged(_write_checkout(tmp_path / "wt-old", "before"), tmp_path / "wheels-old", env)
    changed = _build_logged(_write_checkout(tmp_path / "wt-new", "after"), tmp_path / "wheels-new", env)
    assert "wheel built" in changed.log, changed.log
    assert _greeting(changed.wheel, tmp_path / "scratch") == "after"


def test_an_editable_in_a_fresh_checkout_reuses_the_stored_wheel(tmp_path: Path) -> None:
    """Failure case: the editable hook also consults the store -- even if a working tree of the same contents is recreated and reinstalled as editable,
    the second time does not fire cargo. Before the fix, editable rebuilt in a temporary target every time (about 100 times a day for doeff-vm in the daily verification)."""
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)
    first = _write_checkout(tmp_path / "wt-first", "same")
    _age(first, 3600)
    process = _start_build(first, tmp_path / "wheels-first", env, "build_editable")
    _, built_log = process.communicate(timeout=BUILD_TIMEOUT_SECONDS)
    assert process.returncode == 0, built_log
    assert "wheel built" in built_log, built_log
    fresh = _write_checkout(tmp_path / "wt-fresh", "same")
    process = _start_build(fresh, tmp_path / "wheels-fresh", env, "build_editable")
    out, again_log = process.communicate(timeout=BUILD_TIMEOUT_SECONDS)
    assert process.returncode == 0, again_log
    assert "reusing wheel for identical source (no build)" in again_log, again_log
    assert "wheel built" not in again_log, again_log
    wheel = tmp_path / "wheels-fresh" / out.strip().splitlines()[-1]
    assert _greeting(wheel, tmp_path / "scratch") == "same"
    assert _leftovers(temp_root) == []


def test_the_wheel_hook_fills_the_store_the_editable_hook_reads(tmp_path: Path) -> None:
    """There is one entrance: a wheel that build_wheel built and stored is used, without building, by build_editable of another working tree."""
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)
    built = _build_logged(_write_checkout(tmp_path / "wt-wheel", "shared"), tmp_path / "wheels-wheel", env)
    assert "wheel built" in built.log, built.log
    process = _start_build(_write_checkout(tmp_path / "wt-editable", "shared"), tmp_path / "wheels-editable", env, "build_editable")
    _, log = process.communicate(timeout=BUILD_TIMEOUT_SECONDS)
    assert process.returncode == 0, log
    assert "reusing wheel for identical source (no build)" in log, log
    assert "wheel built" not in log, log
