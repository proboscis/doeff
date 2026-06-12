"""Guard-layer tests: move-only Continuation law (SPEC-VM-021).

These tests assert source-shape invariants that encode the constructive
move-only law restored by the K1 PR.  They are NOT functional tests —
they grep the Rust source for forbidden patterns.

If a test here fails, it means a code change has re-introduced shared
ownership on the continuation chain.  Do NOT weaken these assertions;
instead, fix the code to obey SPEC-VM-021.

Invariants checked:
  1. No Arc/Mutex on the chain field — one-shot is Option::take, not
     lock+take on a shared cell.
  2. No share_handle / clone_handle — there is no API to create a second
     reference to the same continuation.
  3. The VM does not store Continuation objects — it stores Py<PyK>
     handles (Python references, not chain copies).
  4. Frame does not store Continuation objects — the backup slot is a
     Py<PyK> handle.
"""

from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1]
CONTINUATION_RS = CORE_ROOT / "src/continuation.rs"
VM_RS = CORE_ROOT / "src/vm.rs"
FRAME_RS = CORE_ROOT / "src/frame.rs"
STEP_RS = CORE_ROOT / "src/vm/step.rs"
VALUE_RS = CORE_ROOT / "src/value.rs"


def _runtime_source(path: Path) -> str:
    """Read source up to the #[cfg(test)] boundary (runtime code only)."""
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


# -----------------------------------------------------------------------
# Invariant 1: chain field is plain Option, not Arc<Mutex<Option<...>>>
# -----------------------------------------------------------------------


def test_chain_field_is_plain_option() -> None:
    """Continuation.chain must be `Option<DetachedFiberChain>`, not Arc/Mutex."""
    source = _runtime_source(CONTINUATION_RS)

    # The chain field MUST be a plain Option (move-only by construction).
    assert "chain: Option<DetachedFiberChain>" in source, (
        "Continuation must own the chain as a plain Option<DetachedFiberChain> "
        "(SPEC-VM-021 invariant: one-shot is Option::take, not lock+take)."
    )

    # Forbidden: any Arc or Mutex wrapping the chain field.
    for forbidden in [
        "Arc<Mutex<Option<DetachedFiberChain>>>",
        "Arc<Mutex<",
        "Mutex<Option<DetachedFiberChain>>",
    ]:
        assert forbidden not in source, (
            f"Chain field must not use shared ownership: `{forbidden}` "
            f"violates SPEC-VM-021 invariant 1 (single owning location)."
        )


# -----------------------------------------------------------------------
# Invariant 2: no share_handle / clone_handle API
# -----------------------------------------------------------------------


def test_no_shared_handle_api() -> None:
    """There must be no way to create a second reference to a chain."""
    source = _runtime_source(CONTINUATION_RS)

    for forbidden in [
        "fn share_handle(",
        "fn clone_handle(",
        "fn fork_handle(",
        "impl Clone for Continuation",
    ]:
        assert forbidden not in source, (
            f"Continuation must not expose `{forbidden}` — "
            f"SPEC-VM-021 invariant 2 (clone_handle does not exist)."
        )


# -----------------------------------------------------------------------
# Invariant 3: one-shot via Option::take
# -----------------------------------------------------------------------


def test_one_shot_via_option_take() -> None:
    """Continuation::take must use self.chain.take() (Option::take)."""
    source = _runtime_source(CONTINUATION_RS)

    assert "fn take(&mut self) -> Option<DetachedFiberChain>" in source, (
        "Continuation must expose take() returning Option<DetachedFiberChain> "
        "(SPEC-VM-021 invariant 3: one-shot is Option::take)."
    )
    assert "self.chain.take()" in source, (
        "take() must delegate to self.chain.take() (plain Option::take, "
        "not lock().take() on a shared cell)."
    )


# -----------------------------------------------------------------------
# Invariant 4: VM does not store Continuation objects
# -----------------------------------------------------------------------


def test_vm_does_not_store_continuation() -> None:
    """VM struct must not have fields typed as Continuation or Option<Continuation>."""
    source = _runtime_source(VM_RS)

    for forbidden in [
        "Option<Continuation>",
        "pending_handler_chain_backup",
        ": Continuation",
    ]:
        assert forbidden not in source, (
            f"VM must not store Continuation objects (`{forbidden}`) — "
            f"SPEC-VM-021 invariant 4. Use Py<PyK> handles instead."
        )

    # Positive: the backup slot is a Py<PyK> handle.
    assert "pending_handler_k_handle" in source, (
        "VM must use a Py<PyK> handle (not Continuation) for exception recovery."
    )


def test_frame_does_not_store_continuation() -> None:
    """Frame::Program must not have a Continuation backup field."""
    source = _runtime_source(FRAME_RS)

    for forbidden in [
        "chain_backup",
        "chain_backup: Option<Continuation>",
    ]:
        assert forbidden not in source, (
            f"Frame must not store Continuation objects (`{forbidden}`) — "
            f"SPEC-VM-021 invariant 4. Use Py<PyK> handles instead."
        )

    # Positive: handler_k_handle is a Py<PyK>.
    assert "handler_k_handle" in source, (
        "Frame::Program must use handler_k_handle (Py<PyK>), "
        "not a Continuation backup."
    )


# -----------------------------------------------------------------------
# Invariant 5: Value::Continuation panics on clone (move-only)
# -----------------------------------------------------------------------


def test_value_continuation_panics_on_clone() -> None:
    """Value::Continuation clone impl must panic (SPEC-VM-021 move-only)."""
    source = _runtime_source(VALUE_RS)

    assert "Value::Continuation(_)" in source, (
        "Value must have a Continuation variant."
    )

    # The Clone impl for Value must panic for Continuation.
    assert "must not be cloned" in source or "SPEC-VM-021" in source, (
        "Value::Continuation clone must panic with a SPEC-VM-021 message."
    )
