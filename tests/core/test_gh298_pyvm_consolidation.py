from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VM_PYVM = ROOT / "packages" / "doeff-vm" / "src" / "pyvm.rs"
VM_CORE_PYVM = ROOT / "packages" / "doeff-vm-core" / "src" / "pyvm.rs"
VM_CORE_LIB = ROOT / "packages" / "doeff-vm-core" / "src" / "lib.rs"


def _runtime_src(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    return src.split("#[cfg(test)]", 1)[0]


def test_gh298_vm_core_has_no_pyvm_source_file() -> None:
    assert not VM_CORE_PYVM.exists()


def test_gh298_driver_pyvm_has_no_cfg_disabled_duplicate_base_stubs() -> None:
    runtime_src = _runtime_src(VM_PYVM)

    assert "Base pyclasses moved to doeff-vm-core" not in runtime_src
    assert "#[cfg(any())]" not in runtime_src
    assert "pub struct PyDoExprBase;" not in runtime_src
    assert "pub struct PyDoCtrlBase {" not in runtime_src
    assert "pub struct PyEffectBase {" not in runtime_src


def test_gh298_vm_core_lib_no_longer_exports_pyvm_module() -> None:
    src = VM_CORE_LIB.read_text(encoding="utf-8")

    assert "pub mod pyvm;" not in src
    assert "pub use pyvm::" not in src
