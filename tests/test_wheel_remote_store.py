"""Failing cases for the build entry point (stored_wheel in tools/doeff_cargo_backend.py) fetching a wheel absent from the local store from a remote store off the machine
(addendum to ADR-DOE-BUILD-001 R5; design docs/design/rust-wheel-distribution).

The remote store is the asset `<package>-<key>.whl` of doeff's GitHub release `rust-wheels`. The entry point looks in this order: local store -> remote store -> build. Here
the remote store is pointed at a local HTTP endpoint (127.0.0.1) so nothing goes out to the network. The empty-machine tests go through the real maturin
build path and count how many times the fake cargo / rustc placed first on PATH were called.
"""

from __future__ import annotations

import base64
import email.message
import functools
import hashlib
import http.server
import importlib.util
import io
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
NATIVE = "probe/_native.cpython-314-x86_64-linux-gnu.so"
WHEEL_NAME = "probe-0.1.0-cp314-cp314-linux_x86_64.whl"


def _backend() -> ModuleType:
    """Load the build entry point as a module (tools is not a package, so load it from the path)."""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_remote_under_test", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The dataclass looks up its defining module in sys.modules (the entry point's StoredWheel / WheelEntry), so register it by name before loading.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _wheel_bytes(native: bytes, broken: bool = False) -> bytes:
    """Contents of one probe wheel (with RECORD). broken = one whose native hash differs from RECORD."""
    files = (
        ("probe/__init__.py", b"from probe._native import *\n"),
        (NATIVE, native),
        ("probe-0.1.0.dist-info/METADATA", b"Metadata-Version: 2.1\nName: probe\nVersion: 0.1.0\n"),
        ("probe-0.1.0.dist-info/WHEEL", b"Wheel-Version: 1.0\nGenerator: fake\nRoot-Is-Purelib: false\nTag: cp314-cp314-linux_x86_64\n"),
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


@dataclass(frozen=True)
class RemoteStore:
    """Remote store as a local HTTP endpoint: url = head, root = dir to serve, log = file that gets one line per path requested."""

    url: str
    root: Path
    log: Path

    @property
    def requests(self) -> list[str]:
        return _lines(self.log)

    def put(self, slot: Path, data: bytes) -> None:
        (self.root / f"{slot.name}.whl").write_bytes(data)


@pytest.fixture
def remote(tmp_path: Path) -> Iterator[RemoteStore]:
    """HTTP endpoint serving a dir on a free port of 127.0.0.1 (counts the paths requested)."""
    root = tmp_path / "remote"
    root.mkdir()
    log = tmp_path / "remote-requests.log"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            with log.open("a", encoding="utf-8") as out:
                out.write(f"{self.path}\n")
            super().do_GET()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield RemoteStore(url=f"http://127.0.0.1:{server.server_address[1]}", root=root, log=log)
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote: RemoteStore) -> ModuleType:
    """Entry point with the tool version pinned, the store pointed at an empty tmp, and the remote store pointed at the local HTTP endpoint (an empty machine)."""
    module = _backend()
    monkeypatch.setattr(module, "_tool_versions", lambda: "maturin=fixed")
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "store"))
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", remote.url)
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    monkeypatch.delenv("DOEFF_WHEEL_REPORT", raising=False)
    return module


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


def _fake_rust_tools(bin_dir: Path, log: Path) -> None:
    """Place in bin_dir a cargo / rustc that, when called, append the name and args to the log and fail."""
    bin_dir.mkdir()
    for name in ("cargo", "rustc"):
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 1\n')
        tool.chmod(0o755)


def test_an_empty_machine_takes_the_wheel_from_the_remote_store_without_calling_cargo(
    backend: ModuleType, remote: RemoteStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Failing case: on a machine whose local store is empty, if the remote store has a wheel with the same key,
    # the entry point uses the remote store's wheel without calling cargo even once, even through the real build path (maturin). Before the fix, maturin fired cargo
    # without consulting the remote store (an empty-machine sync built doeff-linter from scratch, +435 seconds -- agora 3466059e5).
    log = tmp_path / "rust-tools.log"
    _fake_rust_tools(tmp_path / "fake-bin", log)
    monkeypatch.setenv("PATH", str(tmp_path / "fake-bin"), prepend=os.pathsep)
    for name in ("CARGO", "RUSTC"):
        monkeypatch.delenv(name, raising=False)
    package = _checkout(tmp_path / "wt")
    slot = backend._wheel_slot(package, None)
    remote.put(slot, _wheel_bytes(b"from the remote store\n"))
    # maturin builds the package in the cwd (the build hook is called in the package dir).
    monkeypatch.chdir(package)
    stored = backend.stored_wheel(package, None, backend._maturin_wheel)
    assert _lines(log) == [], f"the fake cargo / rustc was called: {_lines(log)}"
    assert (stored.source, stored.path.name, stored.path.parent) == ("remote", WHEEL_NAME, slot), stored
    assert backend._wheel_problem(stored.path) is None
    with zipfile.ZipFile(stored.path) as archive:
        assert archive.read(NATIVE) == b"from the remote store\n"


def test_a_local_hit_does_not_ask_the_remote(backend: ModuleType, remote: RemoteStore, tmp_path: Path) -> None:
    # A key present in the local store does not consult the remote store (the network is used only when it is not local).
    package = _checkout(tmp_path / "wt")
    first = backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    asked = list(remote.requests)
    again = backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    assert (first.source, again.source, again.path) == ("built", "local", first.path)
    assert remote.requests == asked


def test_a_missing_remote_wheel_is_named_and_built(
    backend: ModuleType, remote: RemoteStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A key absent from the remote store (404 -- a working tree before it lands on main, the few minutes before CI uploads) prints one named line and builds as before.
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    stored = backend.stored_wheel(package, None, compile_wheel)
    err = capsys.readouterr().err
    slot = backend._wheel_slot(package, None)
    assert (compile_wheel.calls, stored.source) == (1, "built")
    assert f"/{slot.name}.whl" in err and "404" in err, err
    assert remote.requests == [f"/{slot.name}.whl"]


def test_a_remote_wheel_that_fails_its_record_is_named_and_not_stored(
    backend: ModuleType, remote: RemoteStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # If the remote wheel's RECORD hash does not match its contents, print one named line and build without using it (and without placing it in the local store).
    package = _checkout(tmp_path / "wt")
    slot = backend._wheel_slot(package, None)
    remote.put(slot, _wheel_bytes(b"tampered\n", broken=True))
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    stored = backend.stored_wheel(package, None, compile_wheel)
    err = capsys.readouterr().err
    assert (compile_wheel.calls, stored.source) == (1, "built")
    assert "differs from RECORD" in err and f"{slot.name}.whl" in err, err
    with zipfile.ZipFile(stored.path) as archive:
        assert archive.read(NATIVE) == b"built here\n"
    assert sorted(path.name for path in slot.iterdir() if path.suffix == ".whl") == [WHEEL_NAME]


def test_an_unreachable_remote_is_named_and_built(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # When the remote store is unreachable (no network, no endpoint), also print one named line and build -- do not block preparation.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", f"http://127.0.0.1:{closed}")
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    stored = backend.stored_wheel(package, None, compile_wheel)
    err = capsys.readouterr().err
    assert (compile_wheel.calls, stored.source) == (1, "built")
    assert f"127.0.0.1:{closed}" in err and "remote store" in err, err


def test_an_empty_remote_setting_does_not_ask_the_network(
    backend: ModuleType, remote: RemoteStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Setting env DOEFF_WHEEL_REMOTE to empty skips the remote store (builds before the CI that uploads to the remote store, places without network).
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", "")
    package = _checkout(tmp_path / "wt")
    remote.put(backend._wheel_slot(package, None), _wheel_bytes(b"from the remote store\n"))
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    stored = backend.stored_wheel(package, None, compile_wheel)
    assert (compile_wheel.calls, stored.source, remote.requests) == (1, "built", [])


def test_the_default_remote_is_the_doeff_release(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Without the env, the remote store is doeff's public release rust-wheels (a keyless download URL -- not subject to the API's 60-per-hour limit).
    # Does not go out to the network: replace urlopen with one that refuses by writing the given URL as the reason of a 404, and read the URL that appears in the named line.
    monkeypatch.delenv("DOEFF_WHEEL_REMOTE")

    def not_found(request: urllib.request.Request | str, timeout: float | None = None) -> object:
        url = request if isinstance(request, str) else request.full_url
        raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)

    monkeypatch.setattr(backend.urllib.request, "urlopen", not_found)
    package = _checkout(tmp_path / "wt")
    backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    slot = backend._wheel_slot(package, None)
    assert f"https://github.com/proboscis/doeff/releases/download/rust-wheels/{slot.name}.whl" in capsys.readouterr().err


def test_the_report_names_where_the_wheel_came_from(
    backend: ModuleType, remote: RemoteStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The report line states where the wheel came from (remote / local / built). The built field is kept for the reader (doeff-cluster's native_wheel.reported_built).
    report = tmp_path / "report.jsonl"
    monkeypatch.setenv("DOEFF_WHEEL_REPORT", str(report))
    package = _checkout(tmp_path / "wt")
    remote.put(backend._wheel_slot(package, None), _wheel_bytes(b"from the remote store\n"))
    backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert [(row["source"], row["built"]) for row in rows] == [("remote", False), ("local", False)], rows
