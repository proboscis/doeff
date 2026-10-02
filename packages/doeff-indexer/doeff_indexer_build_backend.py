"""PEP 517 build backend wrapper for doeff-indexer.

This wraps maturin's backend to ensure the Rust CLI binary is built and bundled into the wheel
and editable installs. Both the CLI build and maturin's build use the cargo target given by
doeff_cargo_backend.cargo_target_dir (outside the package dir — agora-redesign #1493).
"""

import os
import shutil
import subprocess
from pathlib import Path

import maturin

# build をしない hook は、共通の口のもの(maturin へそのまま渡す)を使う。uv・pip はこの module の属性の名で hook を引くので、
# `X as X` の形で名指しの再 export にする(__all__ は置かない — DOEFF021)。
from doeff_cargo_backend import (
    ConfigSettings,
    cargo_target_dir,
)
from doeff_cargo_backend import get_requires_for_build_editable as get_requires_for_build_editable
from doeff_cargo_backend import get_requires_for_build_sdist as get_requires_for_build_sdist
from doeff_cargo_backend import get_requires_for_build_wheel as get_requires_for_build_wheel
from doeff_cargo_backend import prepare_metadata_for_build_editable as prepare_metadata_for_build_editable
from doeff_cargo_backend import prepare_metadata_for_build_wheel as prepare_metadata_for_build_wheel

_PROJECT_ROOT = Path(__file__).resolve().parent
_PYTHON_BIN_DIR = _PROJECT_ROOT / "python" / "doeff_indexer" / "bin"


def _is_windows() -> bool:
    return os.name == "nt"


def _exe_suffix() -> str:
    return ".exe" if _is_windows() else ""


def _cargo() -> str:
    return os.environ.get("CARGO", "cargo")


def _cargo_target() -> str | None:
    return os.environ.get("CARGO_BUILD_TARGET") or None


def _binary_source_path(target_dir: Path, binary_name: str) -> Path:
    profile_dir = "release"
    target = _cargo_target()
    if target:
        return target_dir / target / profile_dir / f"{binary_name}{_exe_suffix()}"
    return target_dir / profile_dir / f"{binary_name}{_exe_suffix()}"


def _ensure_cli_binary(target_dir: Path) -> None:
    """wheel に同梱する CLI の binary を、この build の target(作業木の外)で組んで python/ の下へ写す。"""
    if os.environ.get("DOEFF_INDEXER_SKIP_CLI_BUILD") == "1":
        return

    binary_name = "doeff-indexer"
    cmd = [_cargo(), "build", "--release", "--no-default-features", "--bin", binary_name]
    subprocess.check_call(cmd, cwd=_PROJECT_ROOT)

    source = _binary_source_path(target_dir, binary_name)
    if not source.exists():
        raise RuntimeError(f"Expected {binary_name} at {source}, but it was not built")

    _PYTHON_BIN_DIR.mkdir(parents=True, exist_ok=True)
    destination = _PYTHON_BIN_DIR / source.name
    shutil.copy2(source, destination)

    if not _is_windows():
        destination.chmod(destination.stat().st_mode | 0o111)


def build_wheel(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """wheel に CLI の binary を同梱して組むため(target は作業木の外)。"""
    with cargo_target_dir() as target_dir:
        _ensure_cli_binary(target_dir)
        return maturin.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_editable(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """editable で入れる時も CLI の binary を同梱するため(target は作業木の外)。"""
    with cargo_target_dir() as target_dir:
        _ensure_cli_binary(target_dir)
        return maturin.build_editable(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory: str, config_settings: ConfigSettings | None = None) -> str:
    """sdist を組む時も、maturin が cargo に作らせる target を作業木の外に置くため。"""
    with cargo_target_dir():
        return maturin.build_sdist(sdist_directory, config_settings)
