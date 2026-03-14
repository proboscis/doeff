from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
VM_TRACE_RS = CORE_ROOT / "src/vm/vm_trace.rs"
VM_STEP_RS = CORE_ROOT / "src/vm/step.rs"
VM_DISPATCH_RS = CORE_ROOT / "src/vm/dispatch.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def test_vm_trace_has_no_mutating_exception_enrichment_wrapper() -> None:
    source = _runtime_source(VM_TRACE_RS)

    assert "fn enrich_original_exception_with_context" not in source, (
        "Exception enrichment must not live behind a VM `&mut self` wrapper in vm_trace.rs. "
        "That wrapper can call assemble_active_chain(), which flushes pending trace events and "
        "violates the step-boundary-only trace observer contract."
    )


def test_exception_enrichment_callers_assemble_filtered_active_chain() -> None:
    step_source = _runtime_source(VM_STEP_RS)
    dispatch_source = _runtime_source(VM_DISPATCH_RS)

    for source in (step_source, dispatch_source):
        assert "let active_chain = self" in source and ".assemble_active_chain(Some(&" in source, (
            "Exception enrichment call sites must assemble the active chain explicitly before "
            "calling TraceState::enrich_original_exception_with_context."
        )
        assert (
            ".filter(|entry| !matches!(entry, ActiveChainEntry::ContextEntry { .. }))" in source
        ), "Exception enrichment must strip context entries before merging ExecutionContext data."
        assert "TraceState::enrich_original_exception_with_context(" in source, (
            "Exception enrichment call sites must call the pure TraceState helper directly."
        )
