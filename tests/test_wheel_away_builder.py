"""Failing cases for the build entry point (stored_wheel in tools/doeff_cargo_backend.py) handing a build to an off-machine builder instead of compiling
on the calling machine (the builder from the header note of tools/doeff_cargo_backend.py).

A machine that must not compile Rust itself (a shared worker host whose CPU is shared with other work) names a builder command in env DOEFF_WHEEL_BUILDER
(a JSON list of argv). When neither the local store nor the remote store has the key's wheel, the entry point runs that command instead of maturin / cargo,
verifies the one wheel it wrote with the RECORD hashes, and places it in the local store. When the builder fails, the entry point fails with the builder's
reason and never compiles on the calling machine.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import sys
import sysconfig
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
# The probe package and its wheels are the same as in tests/test_wheel_remote_store.py.
NATIVE = "probe/_native.cpython-314-x86_64-linux-gnu.so"
# A wheel without a CPython ABI, so the builder's wheel is usable whatever interpreter runs the tests.
WHEEL_NAME = "probe-0.1.0-py3-none-linux_x86_64.whl"


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _wheel_bytes(native: bytes, broken: bool = False, tag: str = "py3-none-linux_x86_64") -> bytes:
    """Contents of one probe wheel (with RECORD). broken = one whose native hash differs from RECORD."""
    files = (
        ("probe/__init__.py", b"from probe._native import *\n"),
        (NATIVE, native),
        ("probe-0.1.0.dist-info/METADATA", b"Metadata-Version: 2.1\nName: probe\nVersion: 0.1.0\n"),
        ("probe-0.1.0.dist-info/WHEEL", f"Wheel-Version: 1.0\nGenerator: fake\nRoot-Is-Purelib: false\nTag: {tag}\n".encode()),
    )
    record = "".join(
        f"{name},sha256={_digest(data + (b'!' if broken and name == NATIVE else b''))},{len(data)}\n" for name, data in files
    ) + "probe-0.1.0.dist-info/RECORD,,\n"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for member, data in files:
            info = zipfile.ZipInfo(member)
            info.external_attr = (0o755 if member.endswith(".so") else 0o644) << 16
            archive.writestr(info, data)
        archive.writestr("probe-0.1.0.dist-info/RECORD", record)
    return out.getvalue()


def _lines(log: Path) -> list[str]:
    """Lines of the call-record file (one line per call) (empty if absent)."""
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@dataclass(frozen=True)
class FakeCompile:
    """Fake build: each time it is called, write one line to the log (count = number of lines) and emit a wheel whose contents carry the locally-built marker."""

    log: Path

    @property
    def calls(self) -> int:
        return len(_lines(self.log))

    def __call__(self, target: Path, wheel_directory: str, config_settings: object) -> str:
        with self.log.open("a", encoding="utf-8") as out:
            out.write(f"{target}\n")
        (Path(wheel_directory) / WHEEL_NAME).write_bytes(_wheel_bytes(b"built here\n"))
        return WHEEL_NAME


def _checkout(root: Path) -> Path:
    """One working tree of a package with python-source (maturin's mixed layout)."""
    (root / "src").mkdir(parents=True)
    (root / "python" / "probe").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["maturin>=1.0,<2.0"]\nbuild-backend = "maturin"\n\n'
        '[project]\nname = "probe"\nversion = "0.1.0"\n\n[tool.maturin]\npython-source = "python"\nmodule-name = "probe._native"\n'
    )
    (root / "Cargo.toml").write_text('[package]\nname = "probe"\nversion = "0.1.0"\n\n[lib]\ncrate-type = ["cdylib"]\n')
    (root / "src" / "lib.rs").write_text("pub fn probe() {}\n")
    (root / "python" / "probe" / "__init__.py").write_text("from probe._native import *\n")
    return root




def _backend() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_away_under_test", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """Entry point with the tool version pinned, an empty local store, and the remote store disabled (a key nobody has built yet)."""
    module = _backend()
    monkeypatch.setattr(module, "_tool_versions", lambda: "maturin=fixed")
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "store"))
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", "")
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    monkeypatch.delenv("DOEFF_WHEEL_REPORT", raising=False)
    monkeypatch.delenv("DOEFF_WHEEL_BUILDER", raising=False)
    return module


def _builder(tmp_path: Path, body: str) -> list[str]:
    """A builder command (argv) whose script body sees $1 = package dir, $2 = slot name, $3 = out dir; it logs each call to builder.log."""
    script = tmp_path / "builder.sh"
    script.write_text(f'#!/bin/sh\necho "$1 $2 $3 commit=${{DOEFF_LINTER_BUILD_COMMIT:-}} python=${{DOEFF_WHEEL_PYTHON:-}}" >> "{tmp_path}/builder.log"\n{body}\n')
    script.chmod(0o755)
    return [str(script)]


def _write_wheel(tmp_path: Path, data: bytes) -> str:
    """Script line that writes the given wheel bytes into the out dir ($3)."""
    source = tmp_path / "away.whl"
    source.write_bytes(data)
    return f'cp "{source}" "$3/{WHEEL_NAME}"'


def test_a_missing_wheel_is_built_by_the_named_builder_and_not_on_this_machine(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Failing case: with DOEFF_WHEEL_BUILDER set and the key in neither store, the entry point runs the builder, never the local compile, and stores the
    # builder's wheel under the key. Before the fix, the entry point ignored the builder and compiled locally (helios's agent-toolset-sync compiled
    # doeff-linter for 8 minutes on 2026-11-25 in the 5 minutes before CI uploaded the wheel).
    monkeypatch.setenv("DOEFF_WHEEL_BUILDER", json.dumps(_builder(tmp_path, _write_wheel(tmp_path, _wheel_bytes(b"built away\n")))))
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    stored = backend.stored_wheel(package, None, compile_wheel)
    slot = backend._wheel_slot(package, None, None)
    assert compile_wheel.calls == 0, "the local compile ran although a builder was named"
    assert (stored.source, stored.built, stored.path.parent, stored.path.name) == ("away", False, slot, WHEEL_NAME), stored
    with open(stored.path, "rb") as wheel:
        assert wheel.read() == _wheel_bytes(b"built away\n")
    assert (tmp_path / "builder.log").read_text(encoding="utf-8").split()[:2] == [str(package), slot.name]
    again = backend.stored_wheel(package, None, compile_wheel)
    assert (again.source, compile_wheel.calls) == ("local", 0)
    assert len((tmp_path / "builder.log").read_text(encoding="utf-8").splitlines()) == 1


def test_a_failing_builder_fails_the_build_without_compiling_here(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A builder that exits non-zero (no place to run, the run was taken away, the result could not be fetched) fails the build with its last line,
    # and nothing is compiled or stored on this machine.
    monkeypatch.setenv("DOEFF_WHEEL_BUILDER", json.dumps(_builder(tmp_path, 'echo "no capacity in the queue" >&2\nexit 3')))
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    with pytest.raises(RuntimeError, match="no capacity in the queue") as raised:
        backend.stored_wheel(package, None, compile_wheel)
    assert "code 3" in str(raised.value)
    assert compile_wheel.calls == 0
    assert not backend._wheel_slot(package, None, None).exists()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("exit 0", "wrote no wheel"),
        ("BROKEN", "differs from RECORD"),
    ],
)
def test_a_builder_result_that_is_not_one_intact_wheel_fails_the_build(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, reason: str
) -> None:
    # A builder that exits 0 but leaves no wheel, or a wheel whose contents differ from its RECORD, fails the build -- it is never stored or used silently.
    if body == "BROKEN":
        body = _write_wheel(tmp_path, _wheel_bytes(b"tampered\n", broken=True))
    monkeypatch.setenv("DOEFF_WHEEL_BUILDER", json.dumps(_builder(tmp_path, body)))
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    with pytest.raises(RuntimeError, match=reason):
        backend.stored_wheel(package, None, compile_wheel)
    assert compile_wheel.calls == 0
    assert not backend._wheel_slot(package, None, None).exists()


def test_the_builder_receives_the_build_commit_and_the_report_says_away(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The builder runs with the same build-commit env a local build would get (so the wheel names the same commit), and the report line names the origin "away"
    # with built = false (this machine did not compile).
    package = _checkout(tmp_path / "wt")
    with (package / "pyproject.toml").open("a", encoding="utf-8") as out:
        out.write('\n[tool.doeff-cargo-backend]\nbuild-commit-env = "DOEFF_LINTER_BUILD_COMMIT"\n')
    monkeypatch.setenv("DOEFF_LINTER_BUILD_COMMIT", "a" * 40)
    report = tmp_path / "report.jsonl"
    monkeypatch.setenv("DOEFF_WHEEL_REPORT", str(report))
    monkeypatch.setenv("DOEFF_WHEEL_BUILDER", json.dumps(_builder(tmp_path, _write_wheel(tmp_path, _wheel_bytes(b"built away\n")))))
    backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    assert (tmp_path / "builder.log").read_text(encoding="utf-8").split()[3] == f"commit={'a' * 40}"
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert [(row["source"], row["built"]) for row in rows] == [("away", False)], rows


def test_a_builder_setting_that_is_not_an_argv_list_is_refused(backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A malformed setting fails the build with the setting named -- it does not fall back to compiling here.
    monkeypatch.setenv("DOEFF_WHEEL_BUILDER", "agora spot run")
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    with pytest.raises(RuntimeError, match="DOEFF_WHEEL_BUILDER"):
        backend.stored_wheel(package, None, compile_wheel)
    assert compile_wheel.calls == 0
    assert os.environ["DOEFF_WHEEL_BUILDER"] == "agora spot run"


def test_the_builder_is_told_this_interpreter_and_a_wheel_for_another_abi_is_refused(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The builder learns the interpreter to build with (DOEFF_WHEEL_PYTHON -- the Python version and ABI in the key). A wheel it returns for another CPython ABI
    # fails the build instead of being stored under this key (it would fail at import).
    other = "cp27-cp27mu-linux_x86_64"
    wheel = _wheel_bytes(b"built away\n", tag=other)
    source = tmp_path / "away.whl"
    source.write_bytes(wheel)
    monkeypatch.setenv("DOEFF_WHEEL_BUILDER", json.dumps(_builder(tmp_path, f'cp "{source}" "$3/probe-0.1.0-{other}.whl"')))
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    with pytest.raises(RuntimeError, match="ABI cp27mu differs"):
        backend.stored_wheel(package, None, compile_wheel)
    threaded = "t" if sysconfig.get_config_var("Py_GIL_DISABLED") else ""
    assert (tmp_path / "builder.log").read_text(encoding="utf-8").split()[4] == f"python={sys.version_info.major}.{sys.version_info.minor}{threaded}"
    assert compile_wheel.calls == 0
    assert not backend._wheel_slot(package, None, None).exists()
