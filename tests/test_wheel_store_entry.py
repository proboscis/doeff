"""Failure cases of the entry point that builds and fetches Rust components (stored_wheel, wheel_from_store, editable_from_store in tools/doeff_cargo_backend.py)
(ADR-DOE-BUILD-001)。

Before building, the entry point always consults "the store keyed by the hash of the source contents". Here cargo and maturin are not fired; Compile is replaced with
a fake that counts how many times it was called, and we check that the wheel hook and the editable hook answer from the same store, that changing one Rust source
builds exactly once, and that a broken wheel is not silently used. The form built with a real maturin and cargo is in
tests/test_rust_build_target_outside_package.py。
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
NATIVE = "probe/_native.cpython-314-x86_64-linux-gnu.so"


def _backend() -> ModuleType:
    """Load the build entry point as a module (tools is not a package, so by path)."""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_entry_under_test", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks up its defining module from sys.modules (StoredWheel, WheelEntry of the entry point), so register it by name before loading.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class FakeCompile:
    """Fake compile: records the target it was given on every call (call count = number of records), puts the contents of the source's lib.rs into the native file, and, like maturin,
    emits a wheel containing the .py files of the python-source package of the working tree at build time (so the built contents can be read later)."""

    package: Path
    _mut_targets: list[Path] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self._mut_targets)

    @property
    def targets(self) -> tuple[Path, ...]:
        return tuple(self._mut_targets)

    def __call__(self, target: Path, wheel_directory: str, config_settings: object) -> str:
        self._mut_targets.append(target)
        native = (self.package / "src" / "lib.rs").read_bytes()
        python = tuple(
            (path.relative_to(self.package / "python").as_posix(), path.read_bytes())
            for path in sorted((self.package / "python" / "probe").rglob("*.py"))
        )
        files = (
            *python,
            (NATIVE, native),
            ("probe-0.1.0.dist-info/METADATA", b"Metadata-Version: 2.1\nName: probe\nVersion: 0.1.0\n"),
            ("probe-0.1.0.dist-info/WHEEL", b"Wheel-Version: 1.0\nGenerator: fake\nRoot-Is-Purelib: false\nTag: cp314-cp314-linux_x86_64\n"),
        )
        record = "".join(f"{name},sha256={_digest(data)},{len(data)}\n" for name, data in files) + "probe-0.1.0.dist-info/RECORD,,\n"
        name = "probe-0.1.0-cp314-cp314-linux_x86_64.whl"
        with zipfile.ZipFile(Path(wheel_directory) / name, "w") as archive:
            for member, data in files:
                info = zipfile.ZipInfo(member)
                info.external_attr = (0o755 if member.endswith(".so") else 0o644) << 16
                archive.writestr(info, data)
            archive.writestr("probe-0.1.0.dist-info/RECORD", record)
        return name


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """Pin the tool versions, point the store and the temporary dir at tmp, and make the entry point not consult the store outside the machine (the store test is tests/test_wheel_remote_store.py)."""
    module = _backend()
    monkeypatch.setattr(module, "_tool_versions", lambda: "maturin=fixed")
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "store"))
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", "")
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    return module


def _checkout(root: Path, rust: str = "pub fn probe() {}\n") -> Path:
    """One working tree of a package that has python-source (maturin's mixed layout)."""
    (root / "src").mkdir(parents=True)
    (root / "python" / "probe").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0.1.0"\n\n[tool.maturin]\npython-source = "python"\nmodule-name = "probe._native"\n'
    )
    (root / "Cargo.toml").write_text('[package]\nname = "probe"\nversion = "0.1.0"\n')
    (root / "src" / "lib.rs").write_text(rust)
    (root / "python" / "probe" / "__init__.py").write_text("from probe._native import *\n")
    return root


def _call(backend: ModuleType, hook: str, package: Path, out: Path, compile_wheel: FakeCompile) -> Path:
    """Call the entry point's hook in the package dir and return the path of the wheel it produced."""
    out.mkdir(parents=True, exist_ok=True)
    here = Path.cwd()
    os.chdir(package)
    try:
        name = getattr(backend, hook)(str(out), None, compile_wheel)
    finally:
        os.chdir(here)
    return out / name


def test_a_second_preparation_of_the_same_source_builds_nothing(backend: ModuleType, tmp_path: Path) -> None:
    # Failure case 1 (#3860): the second preparation of the same Rust source (new working tree, editable) never calls Compile. Before the fix, editable did not consult
    # the store and fired maturin every time (about 100 times a day for doeff-vm in the daily verification).
    first = _checkout(tmp_path / "wt-1")
    compile_first = FakeCompile(first)
    _call(backend, "editable_from_store", first, tmp_path / "out-1", compile_first)
    second = _checkout(tmp_path / "wt-2")
    compile_second = FakeCompile(second)
    _call(backend, "editable_from_store", second, tmp_path / "out-2", compile_second)
    assert (compile_first.calls, compile_second.calls) == (1, 0)


def test_the_wheel_hook_and_the_editable_hook_read_one_store(backend: ModuleType, tmp_path: Path) -> None:
    # There is one entrance: a wheel placed by the wheel hook is used, without building, by the editable hook of another working tree (and vice versa).
    first = _checkout(tmp_path / "wt-1")
    _call(backend, "wheel_from_store", first, tmp_path / "out-1", FakeCompile(first))
    second = _checkout(tmp_path / "wt-2")
    compile_second = FakeCompile(second)
    _call(backend, "editable_from_store", second, tmp_path / "out-2", compile_second)
    assert compile_second.calls == 0


def test_the_wheel_hook_takes_the_python_source_from_the_tree_and_only_the_build_products_from_the_store(
    backend: ModuleType, tmp_path: Path
) -> None:
    # Failure case: the key is only the Rust inputs (the .py of python-source is not in the key -- doeff-effect-analyzer does not
    # declare cache-keys and uses the default glob), so the wheel hook for a version with only Python changed pulls the earlier version's wheel from the store. Before the fix, the hook
    # copied it as is, and doeff-effect-analyzer installed through a git dependency claimed 38bd248df while carrying the Python of bba41285 (6 closing
    # tests of the merge queue went red). The hook takes only the build artifacts and dist-info from the store and installs the python-source files from the working tree (R2b) -- a rewritten
    # module has the working tree's contents, an added module is included, a removed module does not remain, and neither the working tree's __pycache__ nor the native left by the earlier layout is included.
    first = _checkout(tmp_path / "wt-1")
    (first / "python" / "probe" / "gone.py").write_text("GONE = 1\n")
    _call(backend, "wheel_from_store", first, tmp_path / "out-1", FakeCompile(first))
    second = _checkout(tmp_path / "wt-2")
    (second / "python" / "probe" / "__init__.py").write_text("VERSION = 2\n")
    (second / "python" / "probe" / "added.py").write_text("ADDED = 1\n")
    (second / "python" / "probe" / "__pycache__").mkdir()
    (second / "python" / "probe" / "__pycache__" / "added.cpython-314.pyc").write_bytes(b"\0stale")
    (second / "python" / NATIVE).write_bytes(b"left by an old layout\n")
    compile_second = FakeCompile(second)
    wheel = _call(backend, "wheel_from_store", second, tmp_path / "out-2", compile_second)
    assert compile_second.calls == 0, "Rust was rebuilt for a Python-only change"
    with zipfile.ZipFile(wheel) as archive:
        assert sorted(archive.namelist()) == sorted([
            "probe/__init__.py", "probe/added.py", NATIVE,
            "probe-0.1.0.dist-info/METADATA", "probe-0.1.0.dist-info/WHEEL", "probe-0.1.0.dist-info/RECORD",
        ])
        assert archive.read("probe/__init__.py") == b"VERSION = 2\n"
        assert archive.read("probe/added.py") == b"ADDED = 1\n"
        assert archive.read(NATIVE) == b"pub fn probe() {}\n"
        assert (archive.getinfo(NATIVE).external_attr >> 16) & 0o111, "the execute bit of the native extension module was lost"
    assert backend._wheel_problem(wheel) is None


def test_the_wheel_hook_hands_over_the_stored_wheel_of_a_package_without_python_source(
    backend: ModuleType, tmp_path: Path
) -> None:
    # A package without python-source (a bin binary, or an extension without Python source) has nothing to install from the working tree, so the store's
    # wheel itself is handed over (same as editable -- R2).
    package = _checkout(tmp_path / "wt")
    (package / "pyproject.toml").write_text('[project]\nname = "probe"\nversion = "0.1.0"\n')
    wheel = _call(backend, "wheel_from_store", package, tmp_path / "out", FakeCompile(package))
    stored = backend.stored_wheel(package, None, FakeCompile(package)).path
    assert wheel.read_bytes() == stored.read_bytes()


@dataclass(frozen=True)
class EditableNative:
    """The native extension module that the editable wheel puts in the venv's artifact dir: data = contents, mode = permission bits."""

    data: bytes
    mode: int


def _editable_native(wheel: Path) -> EditableNative:
    """Read the native extension module inside the editable wheel."""
    with zipfile.ZipFile(wheel) as archive:
        info = archive.getinfo(f"__editable__.probe.native/{NATIVE}")
        return EditableNative(data=archive.read(info), mode=(info.external_attr >> 16) & 0o777)


def test_editable_puts_the_native_module_in_the_venv_and_finds_the_source_through_a_finder(backend: ModuleType, tmp_path: Path) -> None:
    # The editable wheel does not put the native extension module in the source tree; it puts it in the venv's __editable__.probe.native/ as wheel contents
    # (held by uv's RECORD -- it is not removed even by a copy that removes files not in the tree's list; the 2026-10-07 04:00 daily). The .pth adds python-source to the path and
    # starts the finder. The finder searches for probe in the order artifact dir -> working tree dir. RECORD matches the hashes of the files written.
    package = _checkout(tmp_path / "wt")
    wheel = _call(backend, "editable_from_store", package, tmp_path / "out", FakeCompile(package))
    assert not (package / "python" / NATIVE).exists(), "the native extension module was placed in the source tree"
    native = _editable_native(wheel)
    assert native.data == b"pub fn probe() {}\n"
    assert native.mode & 0o111, "the execute bit of the native extension module was lost"
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(archive.namelist())
        assert names == sorted([
            f"__editable__.probe.native/{NATIVE}", "__editable___probe_finder.py", "probe-0.1.0.dist-info/METADATA",
            "probe-0.1.0.dist-info/RECORD", "probe-0.1.0.dist-info/WHEEL", "probe.pth",
        ])
        assert archive.read("probe.pth").decode().splitlines() == [str((package / "python").resolve()), "import __editable___probe_finder"]
        finder = archive.read("__editable___probe_finder.py").decode()
        assert "PACKAGES = ('probe',)" in finder
        assert str(package) not in finder, "the finder holds the working tree's path (the source root is defined only on line 1 of the .pth)"
    assert backend._wheel_problem(wheel) is None


def test_one_changed_rust_file_builds_exactly_once(backend: ModuleType, tmp_path: Path) -> None:
    # Failure case 2: changing one .rs changes the key and builds exactly once (picks up the change in .rs). The second preparation of the same change does not build.
    _call(backend, "editable_from_store", (base := _checkout(tmp_path / "wt-base")), tmp_path / "out-base", FakeCompile(base))
    counts = []
    for name in ("wt-changed-1", "wt-changed-2"):
        package = _checkout(tmp_path / name, rust="pub fn probe() { let _ = 1; }\n")
        compile_wheel = FakeCompile(package)
        wheel = _call(backend, "editable_from_store", package, tmp_path / f"out-{name}", compile_wheel)
        counts = [*counts, compile_wheel.calls]
        assert _editable_native(wheel).data == b"pub fn probe() { let _ = 1; }\n"
    assert counts == [1, 0]


def test_a_broken_stored_wheel_is_named_and_built_again(backend: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Failure case 3: when the store holds a wheel whose key matches but whose contents are missing (one zip member broken), print one naming line and rebuild
    # (do not silently use the broken one).
    first = _checkout(tmp_path / "wt-1")
    stored = backend.stored_wheel(first, None, FakeCompile(first)).path
    with zipfile.ZipFile(stored) as archive:
        kept = [(info, archive.read(info)) for info in archive.infolist() if info.filename != NATIVE]
    with zipfile.ZipFile(stored, "w") as archive:
        for info, data in kept:
            archive.writestr(info, data)
    capsys.readouterr()
    second = _checkout(tmp_path / "wt-2")
    compile_second = FakeCompile(second)
    again = backend.stored_wheel(second, None, compile_second)
    err = capsys.readouterr().err
    assert "stored wheel is corrupt" in err and str(stored) in err, err
    assert (compile_second.calls, again.built) == (1, True)
    assert backend._wheel_problem(again.path) is None


def test_the_store_is_one_flat_dir_per_package_and_key_with_a_used_mark(backend: ModuleType, tmp_path: Path) -> None:
    # The store layout is the same as the worker's wheel store, <package>-<key>/<wheel>, with a used marker .used (each use advances the dir's timestamp -- the worker's
    # cleanup removes only dirs unused for 7 days).
    package = _checkout(tmp_path / "wt")
    stored = backend.stored_wheel(package, None, FakeCompile(package)).path
    slot = stored.parent
    assert slot.parent == tmp_path / "store"
    assert slot.name.startswith("probe-") and len(slot.name) == len("probe-") + 32
    assert (slot / ".used").is_file()
    os.utime(slot, (0, 0))
    backend.stored_wheel(package, None, FakeCompile(package))
    assert slot.stat().st_mtime > 0


def test_a_miss_builds_in_a_temporary_target_and_removes_it(backend: ModuleType, tmp_path: Path) -> None:
    # The cargo target at build time is a per-build temporary dir (outside the working tree) and is removed after the build -- no target is left in the store.
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(package)
    backend.stored_wheel(package, None, compile_wheel)
    (target,) = compile_wheel.targets
    assert target.parent == tmp_path and not target.exists()
    assert not (package / "target").exists()


def test_the_entry_reports_the_stored_wheel_to_the_caller(backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Failure case (#3860 -- premise: the doeff-cluster worker and startup script only go through the entry point via `uv build --wheel`): if env DOEFF_WHEEL_REPORT
    # is set, each time it is called the entry point appends, as one line of JSON, the wheel in the store and whether it built (first time built, second time used -- the same store wheel).
    # Without writing it, the caller has no choice but to look up the store again with its own key.
    import json

    report = tmp_path / "report.jsonl"
    monkeypatch.setenv("DOEFF_WHEEL_REPORT", str(report))
    package = _checkout(tmp_path / "wt")
    _call(backend, "wheel_from_store", package, tmp_path / "out-1", FakeCompile(package))
    _call(backend, "wheel_from_store", package, tmp_path / "out-2", FakeCompile(package))
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert [row["built"] for row in rows] == [True, False], rows
    assert rows[0]["wheel"] == rows[1]["wheel"], rows
    assert Path(rows[0]["wheel"]).is_file() and Path(rows[0]["wheel"]).is_relative_to(tmp_path / "store"), rows
    assert {row["project"] for row in rows} == {"probe"}, rows


def test_the_store_answers_without_rustc_on_the_path_and_without_a_child_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Failure case (addendum to ADR-DOE-BUILD-001 R4, 2026-10-07 -- the sync of the slot of zeus's follower arm stopped with rc=1): the entry point
    # makes the key before consulting the store. If the output of `rustc -V` is in the key material, then in an environment without rustc on PATH (zeus's resident PATH), it fails at key creation even if the store
    # has a wheel of the same source. The key is made without spawning a child process, and the store's wheel is returned as is. Do not replace the tool version with
    # a fixed string (go through the real path that makes the key).
    module = _backend()
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "store"))
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", "")
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    no_tools = tmp_path / "no-tools"
    no_tools.mkdir()
    monkeypatch.setenv("PATH", str(no_tools))

    def no_child(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"the entry point spawned a child process: {args!r}")

    monkeypatch.setattr(subprocess, "run", no_child)
    package = _checkout(tmp_path / "wt")
    built = module.stored_wheel(package, None, FakeCompile(package))
    again_compile = FakeCompile(package)
    again = module.stored_wheel(package, None, again_compile)
    assert built.built and not again.built, (built, again)
    assert again.path == built.path and again.path.is_file(), (built, again)
    assert again_compile.calls == 0, again_compile.targets
