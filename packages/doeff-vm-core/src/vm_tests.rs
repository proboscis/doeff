use super::*;

#[test]
fn test_step_return_clears_current_segment_after_root_completion() {
    let mut vm = VM::new();
    let mut seg = Segment::new(Marker::fresh(), None);
    seg.state_store.insert("answer".to_string(), Value::Int(42));
    seg.writer_log.push(Value::Int(7));
    seg.mode = Mode::Return(Value::Int(42));

    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let event = vm.step();
    assert!(matches!(event, StepEvent::Done(Value::Int(42))));
    assert_eq!(vm.current_segment, None);
    assert_eq!(
        vm.final_state_entries().get("answer"),
        Some(&Value::Int(42))
    );
    assert_eq!(vm.final_log_entries(), vec![Value::Int(7)]);
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
fn test_resume_continuation_uses_captured_caller_instead_of_current_sibling_segment() {
    let mut vm = VM::new();

    let parent_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let child_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(parent_id)));
    let child_segment = vm
        .segments
        .get(child_id)
        .expect("child segment must exist for continuation capture");
    let continuation = Continuation::capture(child_segment, child_id, None);

    vm.free_segment(child_id);

    let sibling_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(parent_id)));
    assert_eq!(
        sibling_id, child_id,
        "freed child segment id should be reused by sibling allocation"
    );
    vm.current_segment = Some(sibling_id);

    let event = vm.handle_resume_continuation(continuation, Value::Unit);
    assert!(matches!(event, StepEvent::Continue));

    let resumed_seg_id = vm
        .current_segment
        .expect("resumed continuation should install a new current segment");
    let resumed_segment = vm
        .segments
        .get(resumed_seg_id)
        .expect("resumed continuation segment must exist");

    assert_eq!(
        resumed_segment.parent,
        Some(parent_id),
        "Resume must restore the continuation's captured caller, not the current sibling segment"
    );
    assert_ne!(
        resumed_segment.parent,
        Some(sibling_id),
        "Resume must not chain the resumed continuation under the current sibling segment"
    );
}

#[test]
fn test_dispatch_resume_uses_current_handler_segment_as_caller() {
    let mut vm = VM::new();

    let parent_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let child_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(parent_id)));
    let child_segment = vm
        .segments
        .get(child_id)
        .expect("child segment must exist for continuation capture");
    let dispatch_id = DispatchId::fresh();
    let continuation = Continuation::capture(child_segment, child_id, Some(dispatch_id));

    let handler_marker = Marker::fresh();
    let handler_seg = Segment::new(handler_marker, Some(parent_id));
    let handler_seg_id = vm.alloc_segment(handler_seg);
    vm.dispatch_state.start_dispatch(
        dispatch_id,
        crate::effect::make_get_execution_context_effect()
            .expect("test dispatch effect should be constructible"),
        continuation.clone(),
        None,
        crate::dispatch_state::ActiveHandlerContext {
            segment_id: handler_seg_id,
            continuation: continuation.clone(),
            marker: handler_marker,
            prompt_seg_id: parent_id,
        },
    );
    vm.current_segment = Some(handler_seg_id);

    let event = vm.handle_dispatch_resume(continuation, Value::Unit);
    assert!(matches!(event, StepEvent::Continue));

    let resumed_seg_id = vm
        .current_segment
        .expect("resumed continuation should install a new current segment");
    let resumed_segment = vm
        .segments
        .get(resumed_seg_id)
        .expect("resumed continuation segment must exist");

    assert_eq!(
        resumed_segment.parent,
        Some(handler_seg_id),
        "Dispatch Resume must return into the current handler segment"
    );
    assert_ne!(
        resumed_segment.parent,
        Some(parent_id),
        "Dispatch Resume must not restore the continuation's captured caller"
    );
}

#[test]
fn test_dispatch_resume_keeps_handler_segment_on_prompt_boundary_chain() {
    let mut vm = VM::new();

    let root_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let captured_caller_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(root_id)));
    let effect_site_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(captured_caller_id)));
    let effect_site_segment = vm
        .segments
        .get(effect_site_id)
        .expect("effect-site segment must exist for continuation capture");
    let dispatch_id = DispatchId::fresh();
    let continuation =
        Continuation::capture(effect_site_segment, effect_site_id, Some(dispatch_id));

    let prompt_seg_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(root_id)));
    let handler_marker = Marker::fresh();
    let handler_seg = Segment::new(handler_marker, Some(prompt_seg_id));
    let handler_seg_id = vm.alloc_segment(handler_seg);
    vm.dispatch_state.start_dispatch(
        dispatch_id,
        crate::effect::make_get_execution_context_effect()
            .expect("test dispatch effect should be constructible"),
        continuation.clone(),
        None,
        crate::dispatch_state::ActiveHandlerContext {
            segment_id: handler_seg_id,
            continuation: continuation.clone(),
            marker: handler_marker,
            prompt_seg_id,
        },
    );
    vm.current_segment = Some(handler_seg_id);

    let event = vm.handle_dispatch_resume(continuation.clone(), Value::Unit);
    assert!(matches!(event, StepEvent::Continue));

    let resumed_seg_id = vm
        .current_segment
        .expect("resumed continuation should install a new current segment");
    let resumed_segment = vm
        .segments
        .get(resumed_seg_id)
        .expect("resumed continuation segment must exist");
    assert_eq!(resumed_segment.parent, Some(handler_seg_id));

    let handler_segment = vm
        .segments
        .get(handler_seg_id)
        .expect("handler segment must remain live while continuation runs");
    assert_eq!(
        handler_segment.parent,
        Some(prompt_seg_id),
        "Dispatch Resume must not rewrite the handler segment's caller chain during Resume"
    );

    vm.current_segment = Some(handler_seg_id);
    vm.current_seg_mut().mode = Mode::Deliver(Value::Unit);
    let event = vm.step();
    assert!(matches!(event, StepEvent::Continue));
    let handler_segment = vm
        .segments
        .get(handler_seg_id)
        .expect("handler segment must still exist after popping HandlerDispatch");
    assert_eq!(
        handler_segment.parent,
        Some(prompt_seg_id),
        "Handler completion must leave the prompt boundary caller unchanged"
    );
}

#[test]
fn test_transfer_throw_uses_captured_caller_instead_of_reused_sibling_segment() {
    let mut vm = VM::new();

    let parent_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let child_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(parent_id)));
    let child_segment = vm
        .segments
        .get(child_id)
        .expect("child segment must exist for continuation capture");
    let continuation = Continuation::capture(child_segment, child_id, None);

    vm.free_segment(child_id);

    let sibling_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(parent_id)));
    assert_eq!(
        sibling_id, child_id,
        "freed child segment id should be reused by sibling allocation"
    );
    vm.current_segment = Some(sibling_id);

    let event = vm.handle_transfer_throw(continuation, PyException::runtime_error("boom"));
    assert!(matches!(event, StepEvent::Continue));

    let resumed_seg_id = vm
        .current_segment
        .expect("thrown continuation should install a new current segment");
    let resumed_segment = vm
        .segments
        .get(resumed_seg_id)
        .expect("thrown continuation segment must exist");

    assert_eq!(
        resumed_segment.parent,
        Some(parent_id),
        "TransferThrow must restore the continuation's captured caller, not the reused sibling"
    );
    assert_ne!(
        resumed_segment.parent,
        Some(sibling_id),
        "TransferThrow must not chain the thrown continuation under the reused sibling"
    );
    assert!(matches!(
        resumed_segment.mode,
        Mode::Throw(PyException::RuntimeError { ref message, .. }) if message == "boom"
    ));
}

#[test]
fn test_eval_in_scope_uses_scope_chain_for_dynamic_handler_lookup() {
    let mut vm = VM::new();

    let scope_parent_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let scope_seg_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(scope_parent_id)));
    let scope_seg = vm
        .segments
        .get(scope_seg_id)
        .expect("scope segment must exist for continuation capture");
    let scope = Continuation::capture(scope_seg, scope_seg_id, None);

    let current_seg_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(scope_parent_id)));
    vm.current_segment = Some(current_seg_id);

    let expr = Python::attach(|py| PyShared::new(py.None()));
    let event = vm.handle_yield_eval_in_scope(expr, scope, std::collections::HashMap::new(), None);
    assert!(matches!(event, StepEvent::NeedsPython(_)));

    let child_seg_id = vm
        .current_segment
        .expect("EvalInScope should switch into a child segment");
    let child_seg = vm
        .segments
        .get(child_seg_id)
        .expect("EvalInScope child segment must exist");

    assert_eq!(
        child_seg.parent,
        Some(scope_seg_id),
        "EvalInScope child must inherit the scope continuation's caller chain for handler lookup"
    );
    assert_ne!(
        child_seg.parent,
        Some(current_seg_id),
        "EvalInScope child must not hide outer handlers behind the current handler segment"
    );
}

#[test]
fn test_resume_unstarted_continuation_inserts_return_anchor_above_outside_scope() {
    let mut vm = VM::new();

    let outside_scope_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let scheduler_seg_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(outside_scope_id)));
    vm.current_segment = Some(scheduler_seg_id);

    let expr = Python::attach(|py| PyShared::new(py.None()));
    let continuation = Continuation::create_unstarted_with_metadata(
        expr,
        Vec::new(),
        None,
        Some(outside_scope_id),
    );

    let event = vm.handle_resume_continuation(continuation, Value::None);
    assert!(matches!(event, StepEvent::NeedsPython(_)));

    let resumed_seg_id = vm
        .current_segment
        .expect("resumed unstarted continuation should install a new task body segment");
    let resumed_seg = vm
        .segments
        .get(resumed_seg_id)
        .expect("resumed unstarted continuation segment must exist");

    let anchor_seg_id = resumed_seg
        .parent
        .expect("spawned task body must attach to a return anchor");
    let anchor_seg = vm
        .segments
        .get(anchor_seg_id)
        .expect("spawned task return anchor must exist");

    assert_eq!(
        anchor_seg.parent,
        Some(scheduler_seg_id),
        "Spawned task return anchor must enter through the live caller chain"
    );
    assert_ne!(
        anchor_seg.parent,
        Some(outside_scope_id),
        "Spawned task return anchor must not bypass the live caller chain"
    );
    assert!(matches!(
        anchor_seg.frames.last(),
        Some(Frame::EvalReturn(continuation))
            if matches!(
                continuation.as_ref(),
                EvalReturnContinuation::ReturnToContinuation { .. }
            )
    ));
}

#[test]
fn test_resume_unstarted_continuation_keeps_scope_parent_outside_handler_wrappers() {
    let mut vm = VM::new();

    let outside_scope_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let scheduler_seg_id = vm.alloc_segment(Segment::new(Marker::fresh(), Some(outside_scope_id)));
    vm.current_segment = Some(scheduler_seg_id);

    let expr = Python::attach(|py| PyShared::new(py.None()));
    let handler: KleisliRef = Python::attach(|py| {
        let callable = py
            .eval(c"lambda effect, k: None", None, None)
            .expect("lambda should compile");
        std::sync::Arc::new(
            PyKleisli::from_handler(py, callable.unbind()).expect("handler should coerce"),
        ) as KleisliRef
    });
    let continuation = Continuation::create_unstarted_with_metadata(
        expr,
        vec![handler],
        None,
        Some(outside_scope_id),
    );

    let event = vm.handle_resume_continuation(continuation, Value::None);
    assert!(matches!(event, StepEvent::NeedsPython(_)));

    let resumed_seg_id = vm
        .current_segment
        .expect("resumed unstarted continuation should install a new task body segment");
    let resumed_seg = vm
        .segments
        .get(resumed_seg_id)
        .expect("resumed unstarted continuation segment must exist");
    let handler_body_seg_id = resumed_seg
        .parent
        .expect("spawned task body must attach to a handler body segment");
    let handler_body_seg = vm
        .segments
        .get(handler_body_seg_id)
        .expect("handler body segment must exist");
    let prompt_seg_id = handler_body_seg
        .parent
        .expect("handler body must attach to a prompt segment");
    let prompt_seg = vm
        .segments
        .get(prompt_seg_id)
        .expect("prompt segment must exist");

    assert_eq!(
        prompt_seg.scope_parent,
        Some(outside_scope_id),
        "spawn prompt must retain the captured lexical scope"
    );
    assert_eq!(
        handler_body_seg.scope_parent,
        Some(outside_scope_id),
        "spawn handler body must not become a scope_parent bridge"
    );
    assert_eq!(
        resumed_seg.scope_parent,
        Some(outside_scope_id),
        "spawned task body must retain the captured lexical scope root"
    );
}

#[test]
#[should_panic(expected = "state has no context")]
fn test_dispatch_origin_scan_fails_fast_on_orphaned_segment_dispatch_index() {
    let mut vm = VM::new();
    let seg_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    vm.current_segment = Some(seg_id);

    vm.dispatch_state.bind_segment(seg_id, DispatchId::fresh());

    let _ = vm.dispatch_origins();
}

#[test]
fn test_consumed_continuation_stays_detectable_on_registry_entry() {
    let mut vm = VM::new();
    let seg_id = vm.alloc_segment(Segment::new(Marker::fresh(), None));
    let continuation = Continuation::with_id(ContId::fresh(), seg_id, None, None);
    let cont_id = continuation.cont_id;

    vm.register_continuation(continuation);
    assert_eq!(vm.continuation_count(), 1);

    let mut owned = vm
        .take_continuation(cont_id)
        .expect("registered continuation must be removable");
    owned.mark_consumed();
    vm.register_continuation(owned.clone_handle());

    assert!(vm
        .lookup_any_continuation(cont_id)
        .is_some_and(|k| k.consumed()));
    assert!(vm.lookup_continuation(cont_id).is_none());
    assert_eq!(vm.continuation_count(), 0);
}
