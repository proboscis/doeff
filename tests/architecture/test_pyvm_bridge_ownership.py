from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VM_SRC = ROOT / "packages" / "doeff-vm" / "src"
VM_CORE_SRC = ROOT / "packages" / "doeff-vm-core" / "src"


def test_pyvm_bridge_lives_only_in_driver_crate() -> None:
    assert (VM_SRC / "pyvm.rs").exists()
    assert not (VM_CORE_SRC / "pyvm.rs").exists()


def test_vm_core_lib_does_not_export_pyvm_module() -> None:
    src = (VM_CORE_SRC / "lib.rs").read_text(encoding="utf-8")
    assert "pub mod pyvm;" not in src


def test_driver_pyvm_has_no_cfg_disabled_duplicate_stubs() -> None:
    src = (VM_SRC / "pyvm.rs").read_text(encoding="utf-8")
    assert "#[cfg(any())]" not in src
