"""Guard-layer tests: lexical scope via single parent chain.

Invariants:
  1. Fiber has a single `parent` chain for both dynamic and lexical lookup.
  2. VarStore owns the handler state heap and root lexical bindings.
  3. Spawn does not clone scope chains into per-task copies.
  4. Handler lookup and dispatch selection walk the parent chain.

Deleted tests:
  - test_scheduler_spawn_path_no_longer_requests_get_handlers:
    Rust scheduler module (doeff-core-effects/src/scheduler/mod.rs) no longer
    exists — scheduler is now a Python handler (doeff-core-effects/scheduler.py).
"""

from __future__ import annotations

from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1]
SEGMENT_RS = CORE_ROOT / "src" / "segment.rs"
VAR_STORE_RS = CORE_ROOT / "src" / "var_store.rs"
VM_DISPATCH_RS = CORE_ROOT / "src" / "vm" / "dispatch.rs"
VM_HANDLER_RS = CORE_ROOT / "src" / "vm" / "handler.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    runtime_source, _, _ = source.rpartition("\n#[cfg(test)]")
    if runtime_source:
        return runtime_source
    return source


def test_fiber_uses_single_parent_chain_for_scope_and_handler_visibility() -> None:
    source = _runtime_source(SEGMENT_RS)

    assert "pub parent: Option<FiberId>" in source, (
        "Fiber must keep a single parent chain that serves both dynamic and lexical lookup."
    )
    assert "scope_parent:" not in source, (
        "Separate lexical scope chains must not exist; walk Fiber.parent instead."
    )
    assert "variables:" not in source, (
        "Lexical variable storage must not move onto Fiber fields."
    )
    assert "struct ScopeStore" not in source, (
        "ScopeStore should not be defined in the segment module."
    )


def test_var_store_replaces_deleted_rust_store_module() -> None:
    source = _runtime_source(VAR_STORE_RS)

    assert "global_state:" in source, "VarStore must own the single handler state heap."
    assert "root_scope_bindings:" in source, (
        "VarStore must keep root lexical bindings for Ask/Local resolution."
    )


def test_spawn_reuses_live_fiber_chain_without_scope_cloning() -> None:
    dispatch_source = _runtime_source(VM_DISPATCH_RS)

    assert "clone_spawn_scope_chain" not in dispatch_source, (
        "Spawn must not clone lexical ancestry into per-task copies."
    )
    assert "scope_parent" not in dispatch_source, (
        "Spawn must not wire a separate lexical scope chain."
    )


def test_handler_lookup_walks_parent_chain() -> None:
    handler_source = _runtime_source(VM_HANDLER_RS)
    dispatch_source = _runtime_source(VM_DISPATCH_RS)

    assert "cursor = seg.parent;" in handler_source, (
        "Handler lookup must walk the live Fiber.parent chain."
    )
    assert "cursor = seg.parent;" in dispatch_source, (
        "Dispatch selection must scan the parent chain instead of separate scope links."
    )
