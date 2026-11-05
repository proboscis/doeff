"""Checks the key of the wheel slot for Rust packages (_wheel_slot in tools/doeff_cargo_backend.py).

The slot copies and returns a wheel without running cargo for a build with the same key (#2364). If the key lacks something that changes the build, the default build silently pulls a wheel built with different settings or a wheel for a Python with a different
ABI. Here cargo is not run; the function that decides the key is called directly. The tool version (maturin version) is
replaced with a fixed string -- to hold constant the key ingredients not checked here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"

# Names of environment variables that enter the key (each changes the output of a build from the same source).
BUILD_ENV = ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_PROFILE_RELEASE_STRIP", "CARGO_PROFILE_RELEASE_DEBUG", "CARGO_BUILD_TARGET", "MATURIN_PEP517_ARGS", "PYO3_PYTHON")
# Names of environment variables that do not enter the key (build location, parallelism, unrelated ones -- preserving the benefit of sharing wheels across working trees).
PLACE_ENV = ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR", "CARGO_BUILD_BUILD_DIR", "CARGO_BUILD_JOBS", "PATH")


def _backend() -> ModuleType:
    """Load the build backend as a module (tools is not a package, so by path)."""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_under_test", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass looks up the defining module in sys.modules (StoredWheel and WheelEntry of the backend), so register it by name before loading.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """The backend with the tool version pinned, the slot pointed at tmp, and every environment variable that enters the key removed."""
    module = _backend()
    monkeypatch.setattr(module, "_tool_versions", lambda: "maturin=fixed")
    monkeypatch.setenv("DOEFF_WHEEL_CACHE", str(tmp_path / "wheels"))
    for name in (*BUILD_ENV, *PLACE_ENV):
        monkeypatch.delenv(name, raising=False)
    return module


@pytest.fixture
def package(tmp_path: Path) -> Path:
    """A package dir holding only the files that affect the build (contents fixed)."""
    root = tmp_path / "pkg"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "probe"\nversion = "0.1.0"\n')
    (root / "Cargo.toml").write_text('[package]\nname = "probe"\nversion = "0.1.0"\n')
    (root / "src" / "lib.rs").write_text("pub fn probe() {}\n")
    return root


@pytest.mark.parametrize("name", BUILD_ENV)
def test_a_build_env_change_points_at_another_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    # Failure case (#2969): even with the same source, a different value of an environment variable that changes the build points to a different slot (the default build does not pull a wheel built with changed settings).
    plain = backend._wheel_slot(package, None)
    monkeypatch.setenv(name, "changed")
    assert backend._wheel_slot(package, None) != plain
    monkeypatch.setenv(name, "changed-again")
    assert backend._wheel_slot(package, None) != plain


def test_the_strip_setting_alone_splits_the_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Issue acceptance: two builds of the same source that differ only in CARGO_PROFILE_RELEASE_STRIP point to different slots.
    monkeypatch.setenv("CARGO_PROFILE_RELEASE_STRIP", "true")
    stripped = backend._wheel_slot(package, None)
    monkeypatch.setenv("CARGO_PROFILE_RELEASE_STRIP", "false")
    assert backend._wheel_slot(package, None) != stripped


def test_another_python_abi_points_at_another_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Failure case (#2969): building with a Python whose extension module name suffix differs, such as free-threading cp314t versus cp313, points to a different slot.
    here = backend._wheel_slot(package, None)
    # Name a suffix different from the running Python's -- naming cp313 on cp313 would leave the same slot (failed in the 3.13 row of the version matrix).
    current = backend.sysconfig.get_config_var("EXT_SUFFIX")
    other = next(s for s in (".cpython-313-x86_64-linux-gnu.so", ".cpython-314t-x86_64-linux-gnu.so") if s != current)
    monkeypatch.setattr(backend.sysconfig, "get_config_var", lambda name: other if name == "EXT_SUFFIX" else None)
    assert backend._wheel_slot(package, None) != here


def test_another_libc_points_at_another_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Failure case (addendum 2 to ADR-DOE-BUILD-001 R4): a wheel in the off-machine slot is built with CI's glibc. Machines with a different glibc
    # version point to a different slot -- an image with an older glibc would fetch it and fail at import.
    here = backend._wheel_slot(package, None)
    monkeypatch.setattr(backend.platform, "libc_ver", lambda: ("glibc", "2.17"))
    assert backend._wheel_slot(package, None) != here


@pytest.mark.parametrize("name", PLACE_ENV)
def test_a_place_only_env_keeps_the_slot(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    # Values that change only the build location or parallelism do not enter the key -- changing the target per working tree still shares the same slot's wheel (the benefit of #2364).
    plain = backend._wheel_slot(package, None)
    monkeypatch.setenv(name, "/somewhere/else")
    assert backend._wheel_slot(package, None) == plain


def test_the_same_inputs_give_the_same_slot_and_a_source_change_does_not(backend: ModuleType, package: Path) -> None:
    # The key is deterministic (same ingredients, same slot), and a difference in source contents still points to a different slot as before.
    first = backend._wheel_slot(package, None)
    assert backend._wheel_slot(package, None) == first
    (package / "src" / "lib.rs").write_text("pub fn probe() { let _ = 1; }\n")
    assert backend._wheel_slot(package, None) != first


def test_env_values_do_not_appear_in_the_slot_path(backend: ModuleType, package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The value only enters the key's hash and does not appear literally in the slot's path.
    monkeypatch.setenv("RUSTFLAGS", "-C secret-looking-flag")
    assert "secret-looking-flag" not in str(backend._wheel_slot(package, None))
    assert sys.platform  # one of the key ingredients (the machine) can be read
