from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
VM_CORE_CARGO = ROOT / "packages" / "doeff-vm-core" / "Cargo.toml"
VM_CARGO = ROOT / "packages" / "doeff-vm" / "Cargo.toml"
CORE_EFFECTS_CARGO = ROOT / "packages" / "doeff-core-effects" / "Cargo.toml"
HANDLE_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "handle.rs"
PY_SHARED_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "py_shared.rs"


def test_vm_core_pyo3_dependency_is_optional() -> None:
    cargo = VM_CORE_CARGO.read_text(encoding="utf-8")
    assert re.search(r'^pyo3\s*=\s*\{[^\n}]*optional\s*=\s*true', cargo, re.MULTILINE), (
        "doeff-vm-core must make pyo3 optional so --no-default-features builds stay GIL-free"
    )


def test_vm_core_exposes_handle_foundation() -> None:
    assert HANDLE_RS.exists(), "Handle<T> foundation module must exist in doeff-vm-core"
    src = HANDLE_RS.read_text(encoding="utf-8")
    assert "pub trait HandleToken" in src
    assert "pub struct Handle<" in src
    assert "PhantomData<fn(T) -> T>" in src
    assert "Arc::try_unwrap" in src


def test_py_shared_uses_safe_unwrap_path() -> None:
    src = PY_SHARED_RS.read_text(encoding="utf-8")
    assert "Python::assume_attached()" not in src
    assert "Python::try_attach" in src
    assert "try_unwrap_token" in src


def test_bridge_crates_enable_python_bridge_feature() -> None:
    vm_cargo = VM_CARGO.read_text(encoding="utf-8")
    core_effects_cargo = CORE_EFFECTS_CARGO.read_text(encoding="utf-8")

    assert 'doeff-vm-core = { path = "../doeff-vm-core", features = ["python_bridge"] }' in vm_cargo
    assert (
        'doeff-vm-core = { path = "../doeff-vm-core", features = ["python_bridge"] }'
        in core_effects_cargo
    )
