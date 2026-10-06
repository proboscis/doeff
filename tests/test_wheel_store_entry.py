"""Rust の部品を組む・引く入口(tools/doeff_cargo_backend.py の stored_wheel・wheel_from_store・editable_from_store)の失敗ケース
(ADR-DOE-BUILD-001・agora-redesign #3860)。

入口は組む前に必ず「source の中身の hash を鍵にした保存先」を引く。ここでは cargo と maturin を撃たず、組み方(Compile)を
呼ばれた度数を数える偽の組み方に差し替え、wheel の hook と editable の hook が同じ保存先から答えるか・Rust の source を 1 つ
替えると 1 度だけ組むか・壊れた wheel を黙って使わないかを確かめる。本物の maturin と cargo で組む形は
tests/test_rust_build_target_outside_package.py。
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
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
    """build の口を module として読む(tools は package でないので path から)。"""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_entry_under_test", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass は定義の module を sys.modules から引く(口の StoredWheel・WheelEntry)ので、読む前に名で登録する。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class FakeCompile:
    """偽の組み方: 呼ばれるたびに渡された target を記録し(度数 = 記録の数)、source の lib.rs の中身を native の file に入れた wheel を
    出す(組んだ中身を後で読めるように)。"""

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
        files = (
            ("probe/__init__.py", b"from probe._native import *\n"),
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
    """道具の版を固定し、保存先と一時の dir を tmp に向けた口。"""
    module = _backend()
    monkeypatch.setattr(module, "_tool_versions", lambda: "rustc=fixed maturin=fixed")
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "store"))
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    return module


def _checkout(root: Path, rust: str = "pub fn probe() {}\n") -> Path:
    """python-source を持つ(maturin の混ぜた並びの)package の作業木 1 つ。"""
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
    """package の dir で入口の hook を呼び、出した wheel の path を返す。"""
    out.mkdir(parents=True, exist_ok=True)
    here = Path.cwd()
    os.chdir(package)
    try:
        name = getattr(backend, hook)(str(out), None, compile_wheel)
    finally:
        os.chdir(here)
    return out / name


def test_a_second_preparation_of_the_same_source_builds_nothing(backend: ModuleType, tmp_path: Path) -> None:
    # 失敗ケース 1(#3860): 同じ Rust の source の 2 度目の準備(新しい作業木・editable)は、組み方を 1 度も呼ばない。直す前の editable は
    # 保存先を引かずに毎回 maturin を撃った(日次の検証で doeff-vm を 1 日 約 100 度)。
    first = _checkout(tmp_path / "wt-1")
    compile_first = FakeCompile(first)
    _call(backend, "editable_from_store", first, tmp_path / "out-1", compile_first)
    second = _checkout(tmp_path / "wt-2")
    compile_second = FakeCompile(second)
    _call(backend, "editable_from_store", second, tmp_path / "out-2", compile_second)
    assert (compile_first.calls, compile_second.calls) == (1, 0)


def test_the_wheel_hook_and_the_editable_hook_read_one_store(backend: ModuleType, tmp_path: Path) -> None:
    # 入口は 1 つ: wheel の hook が置いた wheel を、別の作業木の editable の hook が組まずに使う(逆も同じ)。
    first = _checkout(tmp_path / "wt-1")
    _call(backend, "wheel_from_store", first, tmp_path / "out-1", FakeCompile(first))
    second = _checkout(tmp_path / "wt-2")
    compile_second = FakeCompile(second)
    _call(backend, "editable_from_store", second, tmp_path / "out-2", compile_second)
    assert compile_second.calls == 0


def test_editable_places_the_native_module_in_the_source_and_points_a_pth_at_it(backend: ModuleType, tmp_path: Path) -> None:
    # editable の wheel は maturin の editable と同じ形: native の拡張 module を python-source の下へ置き、その dir を指す .pth と
    # dist-info だけを持つ(Python の source は作業木の物を読む)。RECORD は書いた file の hash と合う。
    package = _checkout(tmp_path / "wt")
    wheel = _call(backend, "editable_from_store", package, tmp_path / "out", FakeCompile(package))
    placed = package / "python" / NATIVE
    assert placed.read_bytes() == b"pub fn probe() {}\n"
    assert os.access(placed, os.X_OK)
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(archive.namelist())
        assert names == ["probe-0.1.0.dist-info/METADATA", "probe-0.1.0.dist-info/RECORD", "probe-0.1.0.dist-info/WHEEL", "probe.pth"]
        assert archive.read("probe.pth").decode().strip() == str((package / "python").resolve())
    assert backend._wheel_problem(wheel) is None


def test_one_changed_rust_file_builds_exactly_once(backend: ModuleType, tmp_path: Path) -> None:
    # 失敗ケース 2: .rs を 1 つ替えると鍵が替わり 1 度だけ組む(.rs の変化を拾う)。同じ変更の 2 度目の準備は組まない。
    _call(backend, "editable_from_store", (base := _checkout(tmp_path / "wt-base")), tmp_path / "out-base", FakeCompile(base))
    counts = []
    for name in ("wt-changed-1", "wt-changed-2"):
        package = _checkout(tmp_path / name, rust="pub fn probe() { let _ = 1; }\n")
        compile_wheel = FakeCompile(package)
        _call(backend, "editable_from_store", package, tmp_path / f"out-{name}", compile_wheel)
        counts = [*counts, compile_wheel.calls]
        assert (package / "python" / NATIVE).read_bytes() == b"pub fn probe() { let _ = 1; }\n"
    assert counts == [1, 0]


def test_a_broken_stored_wheel_is_named_and_built_again(backend: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 失敗ケース 3: 鍵は合うが中身の欠けた wheel(zip の member を 1 つ壊した)が保存先に在る時は、名指しの 1 行を出して組み直す
    # (黙って壊れた物を使わない)。
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
    assert "保存先の wheel が壊れている" in err and str(stored) in err, err
    assert (compile_second.calls, again.built) == (1, True)
    assert backend._wheel_problem(again.path) is None


def test_the_store_is_one_flat_dir_per_package_and_key_with_a_used_mark(backend: ModuleType, tmp_path: Path) -> None:
    # 保存先の並びは worker の wheel の置き場と同じ <package>-<鍵>/<wheel> と使った印 .used(使うたびに dir の時刻が進む — worker の
    # 掃除が 7 日使われない dir だけを消す)。
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
    # 組む時の cargo の target は 1 回の build ごとの一時の dir(作業木の外)で、組み終えたら消える — 保存先に target を残さない。
    package = _checkout(tmp_path / "wt")
    compile_wheel = FakeCompile(package)
    backend.stored_wheel(package, None, compile_wheel)
    (target,) = compile_wheel.targets
    assert target.parent == tmp_path and not target.exists()
    assert not (package / "target").exists()
