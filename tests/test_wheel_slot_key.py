"""Rust の package の wheel の置き場の鍵(tools/doeff_cargo_backend.py の _wheel_slot)を確かめる(agora-redesign #2969)。

置き場は同じ鍵の build で cargo を撃たずに wheel を写して返す(#2364)。鍵に組みを変える物が欠けていると、設定を変えて組んだ wheel や
ABI の違う Python の wheel を既定の build が黙って引く。ここでは cargo を撃たず、鍵を決める関数を直に呼ぶ。道具の版(rustc を呼ぶ)は
固定の文字に差し替える — 鍵の材料のうち、ここで確かめない物を揃えるため。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"

# 鍵に入る環境変数の名(どれも同じ source の build で中身を変える)。
BUILD_ENV = ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_PROFILE_RELEASE_STRIP", "CARGO_PROFILE_RELEASE_DEBUG", "CARGO_BUILD_TARGET", "MATURIN_PEP517_ARGS", "PYO3_PYTHON")
# 鍵に入らない環境変数の名(組む場所・並べる数・関係の無い物 — 作業木をまたいで wheel を共有する効き目を保つ)。
PLACE_ENV = ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR", "CARGO_BUILD_JOBS", "PATH")


def _backend() -> ModuleType:
    """build の口を module として読む(tools は package でないので path から)。"""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_under_test", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass は定義の module を sys.modules から引く(口の StoredWheel・WheelEntry)ので、読む前に名で登録する。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """道具の版を固定し、置き場を tmp に向け、鍵に入る環境変数を全部外した状態の口。"""
    module = _backend()
    monkeypatch.setattr(module, "_tool_versions", lambda: "rustc=fixed maturin=fixed")
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "wheels"))
    for name in (*BUILD_ENV, *PLACE_ENV):
        monkeypatch.delenv(name, raising=False)
    return module


@pytest.fixture
def package(tmp_path: Path) -> Path:
    """組みに効く file だけを持つ package の dir(中身は固定)。"""
    root = tmp_path / "pkg"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "probe"\nversion = "0.1.0"\n')
    (root / "Cargo.toml").write_text('[package]\nname = "probe"\nversion = "0.1.0"\n')
    (root / "src" / "lib.rs").write_text("pub fn probe() {}\n")
    return root


@pytest.mark.parametrize("name", BUILD_ENV)
def test_a_build_env_change_points_at_another_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    # 失敗ケース(#2969): 同じ source でも、組みを変える環境変数の値が違えば別の置き場を指す(既定の build が設定を変えた wheel を引かない)。
    plain = backend._wheel_slot(package, None)
    monkeypatch.setenv(name, "changed")
    assert backend._wheel_slot(package, None) != plain
    monkeypatch.setenv(name, "changed-again")
    assert backend._wheel_slot(package, None) != plain


def test_the_strip_setting_alone_splits_the_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # issue の受入: 同じ source で CARGO_PROFILE_RELEASE_STRIP だけを変えた 2 回の build は、別の置き場を指す。
    monkeypatch.setenv("CARGO_PROFILE_RELEASE_STRIP", "true")
    stripped = backend._wheel_slot(package, None)
    monkeypatch.setenv("CARGO_PROFILE_RELEASE_STRIP", "false")
    assert backend._wheel_slot(package, None) != stripped


def test_another_python_abi_points_at_another_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 失敗ケース(#2969): free-threading の cp314t と cp313 のように拡張 module の名の末尾が違う Python で組むと、別の置き場を指す。
    here = backend._wheel_slot(package, None)
    monkeypatch.setattr(backend.sysconfig, "get_config_var", lambda name: ".cpython-313-x86_64-linux-gnu.so" if name == "EXT_SUFFIX" else None)
    assert backend._wheel_slot(package, None) != here


@pytest.mark.parametrize("name", PLACE_ENV)
def test_a_place_only_env_keeps_the_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    # 組む場所・並べる数だけを変える値は鍵に入らない — 作業木ごとに target を替えても同じ置き場の wheel を共有する(#2364 の効き目)。
    plain = backend._wheel_slot(package, None)
    monkeypatch.setenv(name, "/somewhere/else")
    assert backend._wheel_slot(package, None) == plain


def test_the_same_inputs_give_the_same_slot_and_a_source_change_does_not(backend: ModuleType, package: Path) -> None:
    # 鍵は決定的(同じ材料なら同じ置き場)で、source の中身の違いは今どおり別の置き場。
    first = backend._wheel_slot(package, None)
    assert backend._wheel_slot(package, None) == first
    (package / "src" / "lib.rs").write_text("pub fn probe() { let _ = 1; }\n")
    assert backend._wheel_slot(package, None) != first


def test_env_values_do_not_appear_in_the_slot_path(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 値は鍵の hash に入るだけで、置き場の path に字面では出ない。
    monkeypatch.setenv("RUSTFLAGS", "-C secret-looking-flag")
    assert "secret-looking-flag" not in str(backend._wheel_slot(package, None))
    assert sys.platform  # 鍵の材料の 1 つ(機体)が読めること
