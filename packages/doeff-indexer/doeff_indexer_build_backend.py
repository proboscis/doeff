"""PEP 517 build backend wrapper for doeff-indexer.

This wraps maturin's backend to ensure the Rust CLI binary is built and bundled into the wheel
and editable installs.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

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


def _binary_source_path(binary_name: str) -> Path:
    profile_dir = "release"
    target = _cargo_target()
    if target:
        return _PROJECT_ROOT / "target" / target / profile_dir / f"{binary_name}{_exe_suffix()}"
    return _PROJECT_ROOT / "target" / profile_dir / f"{binary_name}{_exe_suffix()}"


@lru_cache(maxsize=1)
def _ensure_cli_binary() -> None:
    if os.environ.get("DOEFF_INDEXER_SKIP_CLI_BUILD") == "1":
        return

    binary_name = "doeff-indexer"
    cmd = [_cargo(), "build", "--release", "--no-default-features", "--bin", binary_name]
    subprocess.check_call(cmd, cwd=_PROJECT_ROOT)

    source = _binary_source_path(binary_name)
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
    _ensure_cli_binary()
    return _maturin().build_wheel(wheel_directory, config_settings, metadata_directory)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _ensure_cli_binary()
    return _maturin().build_editable(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory: str, config_settings: dict[str, Any] | None = None) -> str:
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
