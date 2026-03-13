import re
from pathlib import Path

VM_RS_PATH = Path(__file__).resolve().parents[1] / "src/vm.rs"
VM_IMPL_DIR = Path(__file__).resolve().parents[1] / "src/vm"
WILDCARD_UNREACHABLE_PATTERN = re.compile(r"_\s*=>\s*unreachable!\s*\(")


def test_vm_runtime_has_no_wildcard_unreachable_match_arms() -> None:
    sources = [VM_RS_PATH]
    if VM_IMPL_DIR.exists():
        sources.extend(sorted(VM_IMPL_DIR.glob("*.rs")))
    source = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    matches = list(WILDCARD_UNREACHABLE_PATTERN.finditer(source))
    assert not matches, (
        "VM runtime must not use wildcard `_ => unreachable!(...)` match arms; "
        "list explicit enum variants to preserve compiler exhaustiveness checking."
    )
