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
