from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
CONTINUATION_RS = CORE_ROOT / "src/continuation.rs"
VM_DISPATCH_RS = CORE_ROOT / "src/vm/dispatch.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def _function_block(source: str, signature: str) -> str:
    start = source.find(signature)
    assert start != -1, f"missing function signature: {signature}"

    brace = source.find("{", start)
    assert brace != -1, f"missing opening brace for function: {signature}"

    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]

    raise AssertionError(f"unterminated function block: {signature}")


def test_continuation_has_no_arc_snapshot() -> None:
    """Continuation must use move semantics, not Arc snapshots."""
    source = _runtime_source(CONTINUATION_RS)

    assert "fibers: Vec<FiberId>" in source, (
        "Move semantics require Continuation to own detached FiberIds."
    )
    assert "consumed: bool" in source, (
        "Continuation must track one-shot consumption directly."
    )

    banned = (
        "segment_snapshot",
        "captured_segment_snapshot",
        "Arc<Segment>",
        "Arc<Fiber>",
        "parent: Option<Arc<Continuation>>",
        "fn parent(&self)",
        "fn set_parent(",
    )
    for needle in banned:
        assert needle not in source, (
            f"Move semantics must eliminate snapshot- and parent-chain storage: `{needle}`"
        )


def test_fiber_not_duplicated_after_capture() -> None:
    """A fiber exists in the chain OR in a continuation, never both."""
    source = _runtime_source(VM_DISPATCH_RS)
    capture_block = _function_block(
        source,
        "pub(crate) fn capture_live_continuation(",
    )

    assert ".parent = None" in capture_block, (
        "Capturing a continuation must detach the owned fiber from the active chain."
    )
    assert "set_parent(" not in source, (
        "Move semantics must merge owned FiberIds into one continuation, not nest continuations."
    )


def test_resume_reattaches_same_fiber() -> None:
    """Resume must reattach the original fiber, not create a new one."""
    source = _runtime_source(VM_DISPATCH_RS)

    assert "fn continuation_exec_segment(" not in source, (
        "Resume must not materialize a fresh execution segment from a snapshot."
    )
    assert "alloc_segment(exec_seg)" not in source, (
        "Resume must not allocate a replacement fiber during continuation activation."
    )

    enter_block = _function_block(
        source,
        "fn enter_or_reenter_continuation_segment_with_dispatch(",
    )
    assert "k.fibers()" in enter_block, (
        "Resume must reattach the owned FiberIds captured in the continuation."
    )
    assert "let Some(seg_id) = k.segment_id()" in enter_block, (
        "Resume must recover the original detached FiberId."
    )
    assert "self.current_segment = Some(seg_id);" in enter_block, (
        "Resume must reinstall the detached fiber as the current fiber."
    )
