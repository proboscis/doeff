from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VM_CORE = ROOT / "packages" / "doeff-vm-core" / "src"
VM_RS = VM_CORE / "vm.rs"
CONTINUATION_RS = VM_CORE / "continuation.rs"
DISPATCH_RS = VM_CORE / "vm" / "dispatch.rs"
STEP_RS = VM_CORE / "vm" / "step.rs"
ARENA_RS = VM_CORE / "arena.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    runtime_source, _, _ = source.rpartition("\n#[cfg(test)]")
    return runtime_source if runtime_source else source


def _struct_body(source: str, name: str) -> str:
    match = re.search(rf"pub struct {name}\s*\{{(?P<body>.*?)\n\}}", source, re.DOTALL)
    assert match is not None, f"missing pub struct {name}"
    return match.group("body")


def test_g1_vm_has_no_orphan_queue_register() -> None:
    body = _struct_body(_runtime_source(VM_RS), "VM")

    assert "orphan_queue" not in body
    assert "OrphanQueue" not in body


def test_g1_continuation_owns_detached_fiber_chain_without_queue() -> None:
    source = _runtime_source(CONTINUATION_RS)
    body = _struct_body(source, "Continuation")

    # Guard the architectural intent: Continuation owns a chain, not a queue.
    # Arc<Mutex<Option<DetachedFiberChain>>> IS allowed — it backs the
    # share_handle() recovery path so the VM can recover from a handler
    # raising before consuming `k`. The lock+take pattern preserves one-shot
    # semantics (locked take returns None on second call). What is NOT
    # allowed is queue-style orphan tracking (Arc<Mutex<Vec<...>>>) or the
    # old OrphanQueue type.
    assert "DetachedFiberChain" in body
    assert "orphan_queue" not in body
    assert "OrphanQueue" not in source
    assert "Arc<Mutex<Vec" not in source
    assert ".push(head)" not in source


def test_g1_arena_exposes_explicit_detach_attach_operations() -> None:
    source = _runtime_source(ARENA_RS)

    assert "detach_chain" in source
    assert "attach_chain" in source
    assert "DetachedFiberChain" in source


def test_g1_slot_reclaim_carries_indices_not_fibers() -> None:
    """#497: a detached chain dropped without reattachment reports its
    arena slot indices through `SlotReclaimQueue` so the arena can reuse
    them. That channel is allocator bookkeeping, not fiber ownership: it
    must carry bare slot indices (usize) only — never fibers, chains, or
    continuations. Fibers still move into Continuation ownership at detach
    and drop normally with the chain (ISSUE-VM-001 G1 / SPEC-VM-021)."""
    arena_source = _runtime_source(ARENA_RS)
    body = _struct_body(arena_source, "SlotReclaimQueue")

    assert "Mutex<Vec<usize>>" in body
    assert "Fiber" not in body
    assert "Chain" not in body
    assert "Continuation" not in body


def test_g1_dispatch_and_step_do_not_construct_queue_backed_continuations() -> None:
    source = "\n".join(
        [
            _runtime_source(DISPATCH_RS),
            _runtime_source(STEP_RS),
        ]
    )

    assert "orphan_queue" not in source
    assert "Continuation::single" not in source
    assert not re.search(r"Continuation::new\([^;\n]*orphan_queue", source)
