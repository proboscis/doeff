"""PEP 517 build backend wrapper for doeff-indexer.

This wraps maturin's backend to ensure the Rust CLI binary is built and bundled into the wheel
and editable installs. The wheel and the editable install both come from the keyed wheel store of
doeff_cargo_backend (the one entry for Rust builds — ADR-DOE-BUILD-001): this module only
supplies how to build on a miss (the CLI binary, then maturin), in the cargo target the store gives
(outside the package dir).
"""

import os
import shutil
import subprocess
from pathlib import Path

import maturin

# Hooks that do no building use the ones from the common entry point (passed straight to maturin). uv and pip look up hooks by this module's attribute names,
# so they are named re-exports in the `X as X` form (no __all__ is placed -- DOEFF021).
from doeff_cargo_backend import (
    ConfigSettings,
    cargo_target_dir,
    editable_from_store,
    wheel_from_store,
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
    """Build the CLI binary to bundle in the wheel with this build's target (outside the working tree) and copy it under python/."""
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


def _compile_with_cli(target: Path, wheel_directory: str, config_settings: ConfigSettings | None) -> str:
    """How to build when it is not in the store: build the CLI binary with this build's target, copy it under python/, and bundle it into the wheel with maturin."""
    _ensure_cli_binary(target)
    return maturin.build_wheel(wheel_directory, config_settings, None)


def build_wheel(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """To hand the wheel over from the store (if absent, build it with the CLI binary bundled and place it -- the target is outside the working tree)."""
    return wheel_from_store(wheel_directory, config_settings, _compile_with_cli)


def build_editable(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """To answer from the stored wheel even for an editable install (place the CLI binary and the extension module under python/)."""
    return editable_from_store(wheel_directory, config_settings, _compile_with_cli)


def build_sdist(sdist_directory: str, config_settings: ConfigSettings | None = None) -> str:
    """So that, when building an sdist too, the target that maturin has cargo create is placed outside the working tree."""
    with cargo_target_dir():
        return maturin.build_sdist(sdist_directory, config_settings)
