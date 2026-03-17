from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
VM_STEP_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "vm" / "step.rs"


def test_current_interceptor_chain_tracks_visited_segments() -> None:
    source = VM_STEP_RS.read_text(encoding="utf-8")

    assert "fn current_interceptor_chain(&self)" in source
    assert "visited_segments" in source, (
        "current_interceptor_chain() must track visited segment ids so shared "
        "caller-chain tails are not re-walked for every dispatch origin."
    )
