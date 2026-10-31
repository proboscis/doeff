"""build の口(tools/doeff_cargo_backend.py の stored_wheel)が、手元の保存先に無い wheel を機体の外の置き場から取る失敗ケース
(ADR-DOE-BUILD-001 R5 の追補・設計 docs/design/rust-wheel-distribution)。

置き場は doeff の GitHub の release `rust-wheels` の asset `<package>-<鍵>.whl`。口は手元の保存先 → 置き場 → 組む、の順に引く。ここでは
置き場を手元の HTTP の口(127.0.0.1)に向け、網の外へ出ない。空の機体の検は本物の maturin の
組み方を通し、PATH の先頭に置いた偽の cargo・rustc が呼ばれた数を数える。
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
    """build の口を module として読む(tools は package でないので path から)。"""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_remote_under_test", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass は定義の module を sys.modules から引く(口の StoredWheel・WheelEntry)ので、読む前に名で登録する。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _wheel_bytes(native: bytes, broken: bool = False) -> bytes:
    """probe の wheel 1 つの中身(RECORD つき)。broken = native の hash を RECORD と違えた物。"""
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
    """呼ばれた記録の file(1 回 1 行)の行(無ければ空)。"""
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@dataclass(frozen=True)
class FakeCompile:
    """偽の組み方: 呼ばれるたびに log へ 1 行書き(度数 = 行の数)、手元で組んだ印の中身の wheel を出す。"""

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
    """手元の HTTP の口の置き場: url = 頭・root = 配る dir・log = 来た path を 1 行ずつ書く file。"""

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
    """127.0.0.1 の空いた port で dir を配る HTTP の口(来た path を数える)。"""
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
    """道具の版を固定し、保存先を空の tmp・置き場を手元の HTTP の口に向けた口(空の機体)。"""
    module = _backend()
    monkeypatch.setattr(module, "_tool_versions", lambda: "maturin=fixed")
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "store"))
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", remote.url)
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    monkeypatch.delenv("DOEFF_WHEEL_REPORT", raising=False)
    return module


def _checkout(root: Path) -> Path:
    """python-source を持つ(maturin の混ぜた並びの)package の作業木 1 つ。"""
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
    """呼ばれたら名と引数を log に 1 行足して落ちる cargo・rustc を bin_dir に置く。"""
    bin_dir.mkdir()
    for name in ("cargo", "rustc"):
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 1\n')
        tool.chmod(0o755)


def test_an_empty_machine_takes_the_wheel_from_the_remote_store_without_calling_cargo(
    backend: ModuleType, remote: RemoteStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 失敗ケース: 手元の保存先が空の機体で、置き場に同じ鍵の wheel が在れば、
    # 口は本物の組み方(maturin)を通しても cargo を 1 度も呼ばずに置き場の wheel を使う。直す前は置き場を引かずに maturin が cargo を
    # 撃った(空の機体の同期で doeff-linter を一から組んで +435 秒 — agora 3466059e5)。
    log = tmp_path / "rust-tools.log"
    _fake_rust_tools(tmp_path / "fake-bin", log)
    monkeypatch.setenv("PATH", str(tmp_path / "fake-bin"), prepend=os.pathsep)
    for name in ("CARGO", "RUSTC"):
        monkeypatch.delenv(name, raising=False)
    package = _checkout(tmp_path / "wt")
    slot = backend._wheel_slot(package, None)
    remote.put(slot, _wheel_bytes(b"from the remote store\n"))
    # maturin は cwd の package を組む(build の hook は package の dir で呼ばれる)。
    monkeypatch.chdir(package)
    stored = backend.stored_wheel(package, None, backend._maturin_wheel)
    assert _lines(log) == [], f"偽の cargo・rustc が呼ばれた: {_lines(log)}"
    assert (stored.source, stored.path.name, stored.path.parent) == ("remote", WHEEL_NAME, slot), stored
    assert backend._wheel_problem(stored.path) is None
    with zipfile.ZipFile(stored.path) as archive:
        assert archive.read(NATIVE) == b"from the remote store\n"


def test_a_local_hit_does_not_ask_the_remote(backend: ModuleType, remote: RemoteStore, tmp_path: Path) -> None:
    # 手元の保存先に在る鍵は、置き場を引かない(網を使うのは手元に無い時だけ)。
    package = _checkout(tmp_path / "wt")
    first = backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    asked = list(remote.requests)
    again = backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    assert (first.source, again.source, again.path) == ("built", "local", first.path)
    assert remote.requests == asked


def test_a_missing_remote_wheel_is_named_and_built(
    backend: ModuleType, remote: RemoteStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 置き場に無い鍵(404 — main に入る前の作業木・CI が上げる前の数分)は、名指しの 1 行を出して今どおり組む。
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
    # 置き場の wheel の RECORD の hash が中身と合わない時は、名指しの 1 行を出して使わず(手元の保存先にも置かず)組む。
    package = _checkout(tmp_path / "wt")
    slot = backend._wheel_slot(package, None)
    remote.put(slot, _wheel_bytes(b"tampered\n", broken=True))
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    stored = backend.stored_wheel(package, None, compile_wheel)
    err = capsys.readouterr().err
    assert (compile_wheel.calls, stored.source) == (1, "built")
    assert "hash が RECORD と違う" in err and f"{slot.name}.whl" in err, err
    with zipfile.ZipFile(stored.path) as archive:
        assert archive.read(NATIVE) == b"built here\n"
    assert sorted(path.name for path in slot.iterdir() if path.suffix == ".whl") == [WHEEL_NAME]


def test_an_unreachable_remote_is_named_and_built(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 置き場に繋がらない(網が無い・口が居ない)時も、名指しの 1 行を出して組む — 準備を止めない。
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", f"http://127.0.0.1:{closed}")
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    stored = backend.stored_wheel(package, None, compile_wheel)
    err = capsys.readouterr().err
    assert (compile_wheel.calls, stored.source) == (1, "built")
    assert f"127.0.0.1:{closed}" in err and "置き場" in err, err


def test_an_empty_remote_setting_does_not_ask_the_network(
    backend: ModuleType, remote: RemoteStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # env DOEFF_WHEEL_REMOTE を空にすると置き場を引かない(置き場へ上げる CI の手前の組み・網の無い場所)。
    monkeypatch.setenv("DOEFF_WHEEL_REMOTE", "")
    package = _checkout(tmp_path / "wt")
    remote.put(backend._wheel_slot(package, None), _wheel_bytes(b"from the remote store\n"))
    compile_wheel = FakeCompile(tmp_path / "compile.log")
    stored = backend.stored_wheel(package, None, compile_wheel)
    assert (compile_wheel.calls, stored.source, remote.requests) == (1, "built", [])


def test_the_default_remote_is_the_doeff_release(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # env が無ければ置き場は doeff の公開の release rust-wheels(鍵なしの download の URL — API の 1 時間 60 回の上限に掛からない)。
    # 網へは出ない: urlopen を、渡された URL を 404 の訳に書いて断る物に替え、名指しの 1 行に出た URL を読む。
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
    # 報告の 1 行は wheel の出どころ(remote・local・built)を書く。built の欄は読み手(doeff-cluster の native_wheel.reported_built)のために残す。
    report = tmp_path / "report.jsonl"
    monkeypatch.setenv("DOEFF_WHEEL_REPORT", str(report))
    package = _checkout(tmp_path / "wt")
    remote.put(backend._wheel_slot(package, None), _wheel_bytes(b"from the remote store\n"))
    backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    backend.stored_wheel(package, None, FakeCompile(tmp_path / "compile.log"))
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert [(row["source"], row["built"]) for row in rows] == [("remote", False), ("local", False)], rows
