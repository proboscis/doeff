import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VM_RS_PATH = REPO_ROOT / "packages/doeff-vm-core/src/vm.rs"
WILDCARD_UNREACHABLE_PATTERN = re.compile(r"_\s*=>\s*unreachable!\s*\(")


def runtime_vm_source() -> str:
    return VM_RS_PATH.read_text(encoding="utf-8")


def test_vm_runtime_has_no_wildcard_unreachable_match_arms() -> None:
    matches = list(WILDCARD_UNREACHABLE_PATTERN.finditer(runtime_vm_source()))
    assert not matches, (
        "VM runtime must not use wildcard `_ => unreachable!(...)` match arms; "
        "list explicit enum variants to preserve compiler exhaustiveness checking."
    )
