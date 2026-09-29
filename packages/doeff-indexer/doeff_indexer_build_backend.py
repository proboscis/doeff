"""PEP 517 build backend wrapper for doeff-indexer.

This wraps maturin's backend to ensure the Rust CLI binary is built and bundled into the wheel
and editable installs. Both the CLI build and maturin's build use the cargo target given by
doeff_cargo_backend.cargo_target_dir (outside the package dir — agora-redesign #1493).
"""

import importlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from doeff_cargo_backend import cargo_target_dir

_PROJECT_ROOT = Path(__file__).resolve().parent
_PYTHON_BIN_DIR = _PROJECT_ROOT / "python" / "doeff_indexer" / "bin"


def _maturin() -> Any:
    return importlib.import_module("maturin")


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
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    with cargo_target_dir() as target_dir:
        _ensure_cli_binary(target_dir)
        return _maturin().build_wheel(wheel_directory, config_settings, metadata_directory)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    with cargo_target_dir() as target_dir:
        _ensure_cli_binary(target_dir)
        return _maturin().build_editable(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory: str, config_settings: dict[str, Any] | None = None) -> str:
    """sdist を組む時も、maturin が cargo に作らせる target を作業木の外に置くため。"""
    with cargo_target_dir():
        return _maturin().build_sdist(sdist_directory, config_settings)


def get_requires_for_build_wheel(config_settings: dict[str, Any] | None = None) -> list[str]:
    return _maturin().get_requires_for_build_wheel(config_settings)


def get_requires_for_build_editable(config_settings: dict[str, Any] | None = None) -> list[str]:
    return _maturin().get_requires_for_build_editable(config_settings)


def get_requires_for_build_sdist(config_settings: dict[str, Any] | None = None) -> list[str]:
    return _maturin().get_requires_for_build_sdist(config_settings)


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings: dict[str, Any] | None = None
) -> str:
    return _maturin().prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def prepare_metadata_for_build_editable(
    metadata_directory: str, config_settings: dict[str, Any] | None = None
) -> str:
    return _maturin().prepare_metadata_for_build_editable(metadata_directory, config_settings)
