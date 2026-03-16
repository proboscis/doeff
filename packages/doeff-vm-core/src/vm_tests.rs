use super::*;

#[test]
fn test_step_return_clears_current_segment_after_root_completion() {
    let mut vm = VM::new();
    let mut seg = Segment::new(Marker::fresh(), None);
    seg.mode = Mode::Return(Value::Int(42));

    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let event = vm.step();
    assert!(matches!(event, StepEvent::Done(Value::Int(42))));
    assert_eq!(vm.current_segment, None);
}

#[test]
fn test_receive_python_result_without_current_segment_returns_internal_error() {
    let mut vm = VM::new();

    let err = vm
        .receive_python_result(PyCallOutcome::Value(Value::Int(7)))
        .expect_err("inactive VM should reject Python outcomes");

    assert!(
        matches!(err, VMError::InternalError { .. }),
        "expected internal error, got {err:?}"
    );
    assert_eq!(
        err.to_string(),
        "internal error: receive_python_result called without current segment"
    );
}

#[test]
fn test_resume_uses_captured_caller_instead_of_current_sibling_segment() {
    let mut vm = VM::new();

    let parent_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let child_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(parent_id)));
    let child_segment = vm
        .segments
        .get(child_id)
        .expect("child segment must exist for continuation capture");
    let continuation = Continuation::capture(child_segment, child_id, None);

    vm.segments.free(child_id);

    let sibling_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(parent_id)));
    assert_eq!(
        sibling_id, child_id,
        "freed child segment id should be reused by sibling allocation"
    );
    vm.current_segment = Some(sibling_id);

    let event = vm.handle_resume(continuation, Value::Unit);
    assert!(matches!(event, StepEvent::Continue));

    let resumed_seg_id = vm
        .current_segment
        .expect("resumed continuation should install a new current segment");
    let resumed_segment = vm
        .segments
        .get(resumed_seg_id)
        .expect("resumed continuation segment must exist");

    assert_eq!(
        resumed_segment.caller,
        Some(parent_id),
        "Resume must restore the continuation's captured caller, not the current sibling segment"
    );
    assert_ne!(
        resumed_segment.caller,
        Some(sibling_id),
        "Resume must not chain the resumed continuation under the current sibling segment"
    );
}
