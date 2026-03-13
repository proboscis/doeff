fn dispatch_runtime_src() -> &'static str {
    include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/vm/dispatch.rs"))
}

fn step_runtime_src() -> &'static str {
    include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/vm/step.rs"))
}

fn vm_trace_runtime_src() -> &'static str {
    include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/vm/vm_trace.rs"))
}

#[test]
fn test_vm_trace_observer_removes_inline_trace_state_emits_from_vm_execution() {
    for (path, src) in [
        ("src/vm/dispatch.rs", dispatch_runtime_src()),
        ("src/vm/step.rs", step_runtime_src()),
        ("src/vm/vm_trace.rs", vm_trace_runtime_src()),
    ] {
        assert!(
            !src.contains("trace_state.emit_"),
            "VM-TRACE-OBSERVER-001 FAIL: {path} must not call trace_state.emit_* directly",
        );
    }
}

#[test]
fn test_vm_trace_observer_flushes_events_at_step_boundary() {
    let src = step_runtime_src();
    assert!(
        src.contains("observe_pending_trace_events"),
        "VM-TRACE-OBSERVER-001 FAIL: VM::step must flush pending trace events via observer",
    );
}
