use super::*;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

#[derive(Debug)]
struct TestHandler {
    name: &'static str,
}

impl crate::kleisli::Kleisli for TestHandler {
    fn apply(&self, _py: Python<'_>, _args: Vec<Value>) -> Result<DoCtrl, VMError> {
        unreachable!("test handler apply should not run")
    }

    fn debug_info(&self) -> crate::kleisli::KleisliDebugInfo {
        crate::kleisli::KleisliDebugInfo {
            name: self.name.to_string(),
            file: None,
            line: None,
        }
    }
}

#[derive(Debug)]
struct ReturnStream {
    next: Option<IRStreamStep>,
}

impl IRStream for ReturnStream {
    fn resume(
        &mut self,
        _value: Value,
        _store: &mut VarStore,
        _scope: &mut crate::segment::ScopeStore,
    ) -> IRStreamStep {
        self.next
            .take()
            .expect("return stream must only be resumed once")
    }

    fn throw(
        &mut self,
        exc: PyException,
        _store: &mut VarStore,
        _scope: &mut crate::segment::ScopeStore,
    ) -> IRStreamStep {
        IRStreamStep::Throw(exc)
    }
}

fn named_handler(name: &'static str) -> KleisliRef {
    std::sync::Arc::new(TestHandler { name }) as KleisliRef
}

#[derive(Debug)]
struct CountingHandler {
    name: &'static str,
    can_handle: bool,
    can_handle_calls: Arc<AtomicUsize>,
}

impl crate::kleisli::Kleisli for CountingHandler {
    fn apply(&self, _py: Python<'_>, _args: Vec<Value>) -> Result<DoCtrl, VMError> {
        unreachable!("counting handler apply should not run")
    }

    fn debug_info(&self) -> crate::kleisli::KleisliDebugInfo {
        crate::kleisli::KleisliDebugInfo {
            name: self.name.to_string(),
            file: None,
            line: None,
        }
    }

    fn can_handle(&self, _effect: &DispatchEffect) -> Result<bool, VMError> {
        self.can_handle_calls.fetch_add(1, Ordering::Relaxed);
        Ok(self.can_handle)
    }
}

fn counting_handler(
    name: &'static str,
    can_handle: bool,
    can_handle_calls: Arc<AtomicUsize>,
) -> KleisliRef {
    Arc::new(CountingHandler {
        name,
        can_handle,
        can_handle_calls,
    }) as KleisliRef
}

fn return_stream(value: Value) -> IRStreamRef {
    IRStreamRef::new(Box::new(ReturnStream {
        next: Some(IRStreamStep::Return(value)),
    }))
}

fn push_program_frame(segment: &mut Segment, value: Value, handler_kind: Option<HandlerKind>) {
    segment.push_frame(Frame::Program {
        stream: return_stream(value),
        metadata: None,
        handler_kind,
        dispatch: None,
    });
}

fn alloc_prompt_boundary(
    vm: &mut VM,
    parent: Option<SegmentId>,
    handled_marker: Marker,
    handler: KleisliRef,
) -> SegmentId {
    vm.alloc_segment(Segment::new_prompt(
        Marker::fresh(),
        parent,
        handled_marker,
        handler,
    ))
}

fn test_dispatch_effect() -> DispatchEffect {
    Python::attach(|py| {
        // Use a plain Python object so is_execution_context_effect() returns false
        crate::py_shared::PyShared::new(py.None())
    })
}

fn install_pending_dispatch(
    vm: &mut VM,
    handler_seg_id: SegmentId,
    prompt_segment_id: SegmentId,
    origin_cont_id: ContId,
    origin: &Continuation,
    original_exception: Option<PyException>,
) {
    vm.set_pending_program_dispatch(
        handler_seg_id,
        ProgramDispatch {
            origin_cont_id,
            parent_origin_cont_id: None,
            handler_segment_id: handler_seg_id,
            prompt_segment_id,
            effect: test_dispatch_effect(),
            trace: DispatchDisplay {
                effect_site: None,
                handler_stack: Vec::new(),
                transfer_target_repr: None,
                result: EffectResult::Active,
                resumed_once: false,
                is_execution_context_effect: false,
            },
            origin_fiber_ids: origin.fibers().to_vec(),
            handler_fiber_ids: vec![handler_seg_id],
            original_exception,
        },
    );
}

fn assert_int(value: Option<&Value>, expected: i64, context: &str) {
    assert!(
        matches!(value, Some(Value::Int(actual)) if *actual == expected),
        "{context}: expected Int({expected}), got {value:?}"
    );
}

fn execution_context_effect() -> DispatchEffect {
    crate::effect::make_get_execution_context_effect()
        .expect("test dispatch effect should be constructible")
}

fn effect_object(effect: &DispatchEffect) -> Py<PyAny> {
    Python::attach(|py| {
        dispatch_to_pyobject(py, effect)
            .map(|obj| obj.unbind())
            .expect("test effect must convert to Python")
    })
}

#[test]
fn test_handler_resolution_cache_skips_rechecking_inner_miss_for_same_segment() {
    let mut vm = VM::new();
    let skipped_calls = Arc::new(AtomicUsize::new(0));
    let selected_calls = Arc::new(AtomicUsize::new(0));

    let outer_prompt_id = alloc_prompt_boundary(
        &mut vm,
        None,
        Marker::fresh(),
        counting_handler("OuterSelected", true, selected_calls.clone()),
    );
    let inner_prompt_id = alloc_prompt_boundary(
        &mut vm,
        Some(outer_prompt_id),
        Marker::fresh(),
        counting_handler("InnerSkipped", false, skipped_calls.clone()),
    );
    let body_seg_id = vm.alloc_segment(Segment::new(Some(inner_prompt_id)));

    let effect = execution_context_effect();
    let effect_obj = effect_object(&effect);

    let (_, current_entries) =
        vm.collect_dispatch_handler_entries(body_seg_id, None, &HashSet::new());
    let effect_type_id =
        VM::effect_type_cache_key(&effect_obj).expect("effect type id should be available");

    assert!(vm
        .cached_current_chain_handler_resolution(
            body_seg_id,
            effect_type_id,
            &effect,
            &effect_obj,
            &current_entries,
        )
        .expect("cache lookup should succeed")
        .is_none());

    let selected = vm
        .first_matching_handler_in_entries(&current_entries, &effect, &effect_obj)
        .expect("initial handler scan should succeed")
        .expect("outer handler should match");
    vm.cache_current_chain_handler_resolution(body_seg_id, effect_type_id, selected.2);

    let cached = vm
        .cached_current_chain_handler_resolution(
            body_seg_id,
            effect_type_id,
            &effect,
            &effect_obj,
            &current_entries,
        )
        .expect("cached lookup should succeed")
        .expect("cached selection should resolve");

    assert_eq!(cached.2, outer_prompt_id);
    assert_eq!(skipped_calls.load(Ordering::Relaxed), 1);
    assert_eq!(selected_calls.load(Ordering::Relaxed), 2);
}

#[test]
fn test_handler_resolution_cache_invalidates_when_segment_topology_changes() {
    let mut vm = VM::new();
    let outer_calls = Arc::new(AtomicUsize::new(0));
    let inner_calls = Arc::new(AtomicUsize::new(0));

    let outer_prompt_id = alloc_prompt_boundary(
        &mut vm,
        None,
        Marker::fresh(),
        counting_handler("OuterHandler", true, outer_calls.clone()),
    );
    let body_seg_id = vm.alloc_segment(Segment::new(Some(outer_prompt_id)));

    let effect = execution_context_effect();
    let effect_obj = effect_object(&effect);
    let effect_type_id =
        VM::effect_type_cache_key(&effect_obj).expect("effect type id should be available");

    let (_, initial_entries) =
        vm.collect_dispatch_handler_entries(body_seg_id, None, &HashSet::new());
    let initial = vm
        .first_matching_handler_in_entries(&initial_entries, &effect, &effect_obj)
        .expect("initial handler scan should succeed")
        .expect("outer handler should match");
    vm.cache_current_chain_handler_resolution(body_seg_id, effect_type_id, initial.2);

    let inner_prompt_id = alloc_prompt_boundary(
        &mut vm,
        Some(outer_prompt_id),
        Marker::fresh(),
        counting_handler("InnerHandler", true, inner_calls.clone()),
    );
    let body_seg = vm
        .segments
        .get_mut(body_seg_id)
        .expect("body segment must exist for topology update");
    body_seg.parent = Some(inner_prompt_id);
    vm.touch_segment_topology_subtree(body_seg_id);

    let (_, updated_entries) =
        vm.collect_dispatch_handler_entries(body_seg_id, None, &HashSet::new());
    assert!(vm
        .cached_current_chain_handler_resolution(
            body_seg_id,
            effect_type_id,
            &effect,
            &effect_obj,
            &updated_entries,
        )
        .expect("cache lookup after topology change should succeed")
        .is_none());

    let updated = vm
        .first_matching_handler_in_entries(&updated_entries, &effect, &effect_obj)
        .expect("updated handler scan should succeed")
        .expect("new inner handler should match");

    assert_eq!(updated.2, inner_prompt_id);
    assert_eq!(outer_calls.load(Ordering::Relaxed), 1);
    assert_eq!(inner_calls.load(Ordering::Relaxed), 1);
}

#[test]
fn test_step_return_clears_current_segment_after_root_completion() {
    let mut vm = VM::new();
    let state_prompt_id = alloc_prompt_boundary(
        &mut vm,
        None,
        Marker::fresh(),
        named_handler("StateHandler"),
    );
    let writer_prompt_id = alloc_prompt_boundary(
        &mut vm,
        Some(state_prompt_id),
        Marker::fresh(),
        named_handler("WriterHandler"),
    );
    let mut seg = Segment::new(Some(writer_prompt_id));
    push_program_frame(&mut seg, Value::Int(42), None);

    let seg_id = vm.alloc_segment(seg);
    assert!(vm.write_handler_state_at(state_prompt_id, "answer".to_string(), Value::Int(42)));
    assert!(vm.append_handler_log_at(writer_prompt_id, Value::Int(7)));
    vm.current_segment = Some(seg_id);

    let value = loop {
        match vm.step() {
            StepEvent::Continue => {}
            StepEvent::Done(value) => break value,
            other => panic!("root completion should finish cleanly, got {other:?}"),
        }
    };

    assert!(matches!(value, Value::Int(42)));
    assert_eq!(vm.current_segment, None);
    let final_state = vm.final_state_entries();
    assert_int(final_state.get("answer"), 42, "final state snapshot");
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

    let parent_id = vm.alloc_segment(Segment::new(None));
    let child_id = vm.alloc_segment(Segment::new(Some(parent_id)));
    let child_segment = vm
        .segments
        .get(child_id)
        .expect("child segment must exist for continuation capture");
    let continuation = Continuation::capture(child_segment, child_id);

    vm.free_segment(child_id);

    let sibling_id = vm.alloc_segment(Segment::new(Some(parent_id)));
    assert_eq!(
        sibling_id, child_id,
        "freed child segment id should be reused by sibling allocation"
    );
    vm.current_segment = Some(sibling_id);

    let event = vm.handle_resume_continuation(OwnedControlContinuation::Started(continuation), Value::Unit);
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
fn test_dispatch_resume_inserts_resume_anchor_above_captured_caller() {
    let mut vm = VM::new();

    let parent_id = vm.alloc_segment(Segment::new(None));
    let child_id = vm.alloc_segment(Segment::new(Some(parent_id)));
    let child_segment = vm
        .segments
        .get(child_id)
        .expect("child segment must exist for continuation capture");
    let origin_cont_id = ContId::fresh();
    let continuation = Continuation::capture(child_segment, child_id);

    let handler_marker = Marker::fresh();
    let prompt_seg_id = alloc_prompt_boundary(
        &mut vm,
        Some(parent_id),
        handler_marker,
        named_handler("TestHandler"),
    );
    let mut handler_seg = Segment::new(Some(prompt_seg_id));
    push_program_frame(
        &mut handler_seg,
        Value::Unit,
        Some(HandlerKind::RustBuiltin),
    );
    let handler_seg_id = vm.alloc_segment(handler_seg);
    install_pending_dispatch(&mut vm, handler_seg_id, prompt_seg_id, origin_cont_id, &continuation, None);
    vm.current_segment = Some(handler_seg_id);

    let event = vm.handle_dispatch_resume(continuation, Value::Unit);
    assert!(matches!(event, StepEvent::Continue), "got event: {event:?}");

    let resumed_seg_id = vm
        .current_segment
        .expect("resumed continuation should install a new current segment");
    let resumed_segment = vm
        .segments
        .get(resumed_seg_id)
        .expect("resumed continuation segment must exist");
    let anchor_seg_id = resumed_segment
        .parent
        .expect("dispatch resume should insert an anchor segment");
    let anchor_seg = vm
        .segments
        .get(anchor_seg_id)
        .expect("dispatch resume anchor must exist");

    assert_ne!(
        anchor_seg_id, handler_seg_id,
        "Resume must route back through an anchor, not by rewriting the live handler segment"
    );
    assert_eq!(
        anchor_seg.parent,
        Some(parent_id),
        "Resume anchor must attach above the continuation's captured caller"
    );
    assert!(matches!(
        anchor_seg.frames.last(),
        Some(Frame::EvalReturn(continuation))
            if matches!(
                continuation.as_ref(),
                EvalReturnContinuation::ResumeToContinuation { .. }
            )
    ));
}

#[test]
fn test_dispatch_resume_keeps_handler_segment_on_prompt_boundary_chain() {
    let mut vm = VM::new();

    let root_id = vm.alloc_segment(Segment::new(None));
    let captured_caller_id = vm.alloc_segment(Segment::new(Some(root_id)));
    let effect_site_id = vm.alloc_segment(Segment::new(Some(captured_caller_id)));
    let effect_site_segment = vm
        .segments
        .get(effect_site_id)
        .expect("effect-site segment must exist for continuation capture");
    let origin_cont_id = ContId::fresh();
    let continuation = Continuation::capture(effect_site_segment, effect_site_id);

    let handler_marker = Marker::fresh();
    let prompt_seg_id = alloc_prompt_boundary(
        &mut vm,
        Some(root_id),
        handler_marker,
        named_handler("TestHandler"),
    );
    let mut handler_seg = Segment::new(Some(prompt_seg_id));
    push_program_frame(
        &mut handler_seg,
        Value::Unit,
        Some(HandlerKind::RustBuiltin),
    );
    let handler_seg_id = vm.alloc_segment(handler_seg);
    install_pending_dispatch(&mut vm, handler_seg_id, prompt_seg_id, origin_cont_id, &continuation, None);
    vm.current_segment = Some(handler_seg_id);

    let event = vm.handle_dispatch_resume(Continuation::from_fiber(continuation.fibers()[0], None), Value::Unit);
    assert!(matches!(event, StepEvent::Continue));

    let resumed_seg_id = vm
        .current_segment
        .expect("resumed continuation should install a new current segment");
    let resumed_segment = vm
        .segments
        .get(resumed_seg_id)
        .expect("resumed continuation segment must exist");
    let anchor_seg_id = resumed_segment
        .parent
        .expect("dispatch resume should insert an anchor segment");
    let anchor_seg = vm
        .segments
        .get(anchor_seg_id)
        .expect("dispatch resume anchor must exist");
    assert_eq!(anchor_seg.parent, Some(captured_caller_id));

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
    vm.mode = Mode::Deliver(Value::Unit);
    let event = vm.step();
    assert!(matches!(event, StepEvent::Continue));
    let handler_segment = vm
        .segments
        .get(handler_seg_id)
        .expect("handler segment must still exist after handler return");
    assert_eq!(
        handler_segment.parent,
        Some(prompt_seg_id),
        "Handler completion must leave the prompt boundary caller unchanged"
    );
}

#[test]
fn test_transfer_throw_uses_captured_caller_instead_of_reused_sibling_segment() {
    let mut vm = VM::new();

    let parent_id = vm.alloc_segment(Segment::new(None));
    let child_id = vm.alloc_segment(Segment::new(Some(parent_id)));
    let child_segment = vm
        .segments
        .get(child_id)
        .expect("child segment must exist for continuation capture");
    let continuation = Continuation::capture(child_segment, child_id);

    vm.free_segment(child_id);

    let sibling_id = vm.alloc_segment(Segment::new(Some(parent_id)));
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
}

#[test]
fn test_eval_in_scope_uses_scope_chain_for_dynamic_handler_lookup() {
    let mut vm = VM::new();

    let scope_parent_id = vm.alloc_segment(Segment::new(None));
    let scope_seg_id = vm.alloc_segment(Segment::new(Some(scope_parent_id)));
    let scope_seg = vm
        .segments
        .get(scope_seg_id)
        .expect("scope segment must exist for continuation capture");
    let scope = Continuation::capture(scope_seg, scope_seg_id);

    let current_seg_id = vm.alloc_segment(Segment::new(Some(scope_parent_id)));
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

    let outside_scope_id = vm.alloc_segment(Segment::new(None));
    let scheduler_seg_id = vm.alloc_segment(Segment::new(Some(outside_scope_id)));
    vm.current_segment = Some(scheduler_seg_id);

    let expr = Python::attach(|py| PyShared::new(py.None()));
    let continuation = OwnedControlContinuation::Pending(PendingContinuation::create_with_metadata(
        expr,
        Vec::new(),
        Vec::new(),
        None,
        Some(outside_scope_id),
    ));

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

    let outside_scope_id = vm.alloc_segment(Segment::new(None));
    let scheduler_seg_id = vm.alloc_segment(Segment::new(Some(outside_scope_id)));
    vm.current_segment = Some(scheduler_seg_id);

    let expr = Python::attach(|py| PyShared::new(py.None()));
    let handler = named_handler("SpawnHandler");
    let continuation = OwnedControlContinuation::Pending(PendingContinuation::create_with_metadata(
        expr,
        vec![handler],
        Vec::new(),
        None,
        Some(outside_scope_id),
    ));

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

    // The chain from resumed body → handler_body → prompt → anchor → scheduler → outside_scope
    // verifies that spawned tasks with handlers preserve the scope chain back to outside_scope.
    let anchor_seg_id = vm
        .parent_segment(prompt_seg_id)
        .expect("prompt must attach to a return anchor segment");
    assert_eq!(
        vm.parent_segment(anchor_seg_id),
        Some(scheduler_seg_id),
        "spawn return anchor must enter through the live caller chain"
    );
    assert_eq!(
        vm.parent_segment(handler_body_seg_id),
        Some(prompt_seg_id),
        "handler body must attach to its prompt boundary"
    );
    assert_eq!(
        vm.parent_segment(resumed_seg_id),
        Some(handler_body_seg_id),
        "spawned task body must attach to handler body segment"
    );
}

#[test]
fn test_dispatch_origins_derive_from_live_program_topology() {
    let mut vm = VM::new();

    let root_id = vm.alloc_segment(Segment::new(None));
    let effect_site_id = vm.alloc_segment(Segment::new(Some(root_id)));
    let effect_site_segment = vm
        .segments
        .get(effect_site_id)
        .expect("effect-site segment must exist for continuation capture");
    let origin_cont_id = ContId::fresh();
    let continuation = Continuation::capture(effect_site_segment, effect_site_id);

    let handler_marker = Marker::fresh();
    let prompt_seg_id = alloc_prompt_boundary(
        &mut vm,
        Some(root_id),
        handler_marker,
        named_handler("TestHandler"),
    );
    let handler_seg_id = vm.alloc_segment(Segment::new(Some(prompt_seg_id)));
    install_pending_dispatch(&mut vm, handler_seg_id, prompt_seg_id, origin_cont_id, &continuation, None);
    vm.current_segment = Some(handler_seg_id);

    let origins = vm.dispatch_origins();
    assert_eq!(origins.len(), 1);
    assert_eq!(origins[0].origin_cont_id, origin_cont_id);
    assert_eq!(origins[0].origin_fiber_ids, continuation.fibers());
}

#[test]
fn test_visible_scope_store_in_handler_sees_captured_local_scope() {
    let mut vm = VM::new();

    let root_id = vm.alloc_segment(Segment::new(None));
    let prompt_seg_id = alloc_prompt_boundary(
        &mut vm,
        Some(root_id),
        Marker::fresh(),
        named_handler("ReaderHandler"),
    );
    let effect_site_id = vm.alloc_segment(Segment::new(Some(prompt_seg_id)));
    let key = crate::py_key::HashedPyKey::from_test_string("config");
    let value = Value::Int(42);
    vm.push_lexical_scope_frame(
        effect_site_id,
        std::collections::HashMap::from([(key.clone(), value.clone())]),
    );

    let origin_cont_id = ContId::fresh();
    let captured = {
        let effect_site = vm
            .segments
            .get(effect_site_id)
            .expect("effect-site segment must exist for continuation capture");
        Continuation::capture(effect_site, effect_site_id)
    };
    vm.segments
        .get_mut(effect_site_id)
        .expect("captured effect-site segment must remain live")
        .parent = None;

    let handler_seg_id = vm.alloc_segment(Segment::new(Some(prompt_seg_id)));
    install_pending_dispatch(&mut vm, handler_seg_id, prompt_seg_id, origin_cont_id, &captured, None);

    let scope = vm.visible_scope_store(handler_seg_id);
    let resolved = scope
        .scope_bindings
        .iter()
        .rev()
        .find_map(|layer| layer.get(&key))
        .cloned();

    assert!(
        matches!(resolved, Some(Value::Int(42))),
        "handler scope reconstruction must include captured lexical bindings, got {resolved:?}"
    );
}

#[test]
fn test_write_scoped_var_nonlocal_updates_owner_through_captured_scope_chain() {
    let mut vm = VM::new();

    let root_id = vm.alloc_segment(Segment::new(None));
    let owner_seg_id = vm.alloc_segment(Segment::new(Some(root_id)));
    let child_seg_id = vm.alloc_segment(Segment::new(Some(owner_seg_id)));
    let var = vm.alloc_scoped_var_in_segment(owner_seg_id, Value::Int(10));

    let origin_cont_id = ContId::fresh();
    let captured = {
        let child_seg = vm
            .segments
            .get(child_seg_id)
            .expect("child segment must exist for continuation capture");
        Continuation::capture(child_seg, child_seg_id)
    };
    let prompt_seg_id = alloc_prompt_boundary(
        &mut vm,
        Some(root_id),
        Marker::fresh(),
        named_handler("StateHandler"),
    );
    let handler_seg_id = vm.alloc_segment(Segment::new(Some(prompt_seg_id)));
    install_pending_dispatch(&mut vm, handler_seg_id, prompt_seg_id, origin_cont_id, &captured, None);

    assert!(
        vm.write_scoped_var_nonlocal(handler_seg_id, var, Value::Int(20)),
        "WriteVarNonlocal must find the owner segment through the captured continuation"
    );
    assert!(
        matches!(
            vm.read_scoped_var_from(owner_seg_id, var),
            Some(Value::Int(20))
        ),
        "owner cell must reflect the nonlocal write"
    );
}

#[test]
fn test_independent_continuations_have_separate_consumed_state() {
    let mut continuation = Continuation::with_id(ContId::fresh(), SegmentId::from_index(0), None);
    let independent = Continuation::from_fiber(continuation.fibers()[0], None);

    assert!(!independent.consumed());
    assert!(!continuation.consumed());
    continuation.mark_consumed();

    assert!(continuation.consumed());
    // Independent continuation is unaffected — no shared state
    assert!(!independent.consumed());
}
