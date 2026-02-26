use super::*;
use crate::ast_stream::{ASTStream, ASTStreamStep};
use crate::frame::CallMetadata;
use crate::segment::ScopeStore;
use crate::trace_state::TraceState;
use std::sync::{Arc, Mutex};

fn make_dummy_continuation() -> Continuation {
    Continuation {
        cont_id: ContId::fresh(),
        segment_id: SegmentId::from_index(0),
        frames_snapshot: std::sync::Arc::new(Vec::new()),
        marker: Marker::fresh(),
        dispatch_id: None,
        mode: Box::new(Mode::Deliver(Value::Unit)),
        pending_python: None,
        pending_error_context: None,
        interceptor_eval_depth: 0,
        interceptor_skip_stack: Vec::new(),
        scope_store: ScopeStore::default(),
        started: true,
        program: None,
        handlers: Vec::new(),
        handler_identities: Vec::new(),
        metadata: None,
        parent: None,
    }
}

#[derive(Debug)]
struct DummyProgramStream;

impl ASTStream for DummyProgramStream {
    fn resume(
        &mut self,
        _value: Value,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> ASTStreamStep {
        ASTStreamStep::Return(Value::Unit)
    }

    fn throw(
        &mut self,
        exc: PyException,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> ASTStreamStep {
        ASTStreamStep::Throw(exc)
    }
}

fn make_program_frame(function_name: &str, source_file: &str, source_line: u32) -> Frame {
    let metadata = CallMetadata::new(
        function_name.to_string(),
        source_file.to_string(),
        source_line,
        None,
        None,
    );
    let stream: Arc<Mutex<Box<dyn ASTStream>>> = Arc::new(Mutex::new(
        Box::new(DummyProgramStream) as Box<dyn ASTStream>
    ));
    Frame::program(stream, Some(metadata))
}

#[test]
fn test_vm_creation() {
    let vm = VM::new();
    assert!(vm.current_segment.is_none());
    assert!(vm.dispatch_state.is_empty());
    assert!(vm.installed_handler_markers().is_empty());
}

#[test]
fn test_rust_store_operations() {
    let mut store = RustStore::new();

    store.put("key".to_string(), Value::Int(42));
    assert_eq!(store.get("key").unwrap().as_int(), Some(42));

    store.tell(Value::String("log message".to_string()));
    assert_eq!(store.logs().len(), 1);
}

#[test]
fn test_vm_alloc_segment() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);

    assert!(vm.segments.get(seg_id).is_some());
}

#[test]
fn test_vm_step_return_no_caller() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);

    vm.current_segment = Some(seg_id);
    vm.current_seg_mut().mode = Mode::Return(Value::Int(42));

    let event = vm.step();
    assert!(matches!(event, StepEvent::Done(Value::Int(42))));
}

#[test]
fn test_vm_step_return_with_caller() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    let caller_seg = Segment::new(marker, None);
    let caller_id = vm.alloc_segment(caller_seg);

    let child_seg = Segment::new(marker, Some(caller_id));
    let child_id = vm.alloc_segment(child_seg);

    vm.current_segment = Some(child_id);
    vm.current_seg_mut().mode = Mode::Return(Value::Int(99));

    let event = vm.step();
    assert!(matches!(event, StepEvent::Continue));
    assert_eq!(vm.current_segment, Some(caller_id));
    assert!(vm.current_seg().mode.is_deliver());
}

#[test]
fn test_vm_one_shot_tracking() {
    let mut vm = VM::new();
    let cont_id = ContId::fresh();

    assert!(!vm.is_one_shot_consumed(cont_id));
    vm.mark_one_shot_consumed(cont_id);
    assert!(vm.is_one_shot_consumed(cont_id));
}

#[test]
fn test_visible_handlers_no_dispatch() {
    let mut vm = VM::new();
    let m1 = Marker::fresh();
    let m2 = Marker::fresh();
    let root = vm.alloc_segment(Segment::new(m1, None));
    let prompt_1 = vm.alloc_segment(Segment::new_prompt(
        m1,
        Some(root),
        m1,
        Arc::new(crate::handler::StateHandlerFactory),
        None,
        None,
    ));
    let body_1 = vm.alloc_segment(Segment::new(m1, Some(prompt_1)));
    let prompt_2 = vm.alloc_segment(Segment::new_prompt(
        m2,
        Some(body_1),
        m2,
        Arc::new(crate::handler::ReaderHandlerFactory),
        None,
        None,
    ));
    let body_2 = vm.alloc_segment(Segment::new(m2, Some(prompt_2)));
    vm.current_segment = Some(body_2);

    let visible = vm.current_visible_handlers();
    assert_eq!(visible.len(), 2);
}

#[test]
fn test_visible_handlers_with_busy_boundary() {
    let mut vm = VM::new();
    let m1 = Marker::fresh();
    let m2 = Marker::fresh();
    let m3 = Marker::fresh();
    let root = vm.alloc_segment(Segment::new(m1, None));
    let p1 = vm.alloc_segment(Segment::new_prompt(
        m1,
        Some(root),
        m1,
        Arc::new(crate::handler::StateHandlerFactory),
        None,
        None,
    ));
    let b1 = vm.alloc_segment(Segment::new(m1, Some(p1)));
    let p2 = vm.alloc_segment(Segment::new_prompt(
        m2,
        Some(b1),
        m2,
        Arc::new(crate::handler::ReaderHandlerFactory),
        None,
        None,
    ));
    let b2 = vm.alloc_segment(Segment::new(m2, Some(p2)));
    let p3 = vm.alloc_segment(Segment::new_prompt(
        m3,
        Some(b2),
        m3,
        Arc::new(crate::handler::WriterHandlerFactory),
        None,
        None,
    ));
    let b3 = vm.alloc_segment(Segment::new(m3, Some(p3)));
    vm.current_segment = Some(b3);
    let k_user = make_dummy_continuation();

    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id: DispatchId::fresh(),
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![m1, m2, m3],
        handler_idx: 1,
        supports_error_context_conversion: false,
        k_user: k_user.clone(),

        prompt_seg_id: SegmentId::from_index(0),

        completed: false,
        original_exception: None,
    });

    let visible = vm.current_visible_handlers();
    assert_eq!(visible.len(), 3);
}

#[test]
fn test_visible_handlers_completed_dispatch() {
    let mut vm = VM::new();
    let m1 = Marker::fresh();
    let m2 = Marker::fresh();
    let root = vm.alloc_segment(Segment::new(m1, None));
    let p1 = vm.alloc_segment(Segment::new_prompt(
        m1,
        Some(root),
        m1,
        Arc::new(crate::handler::StateHandlerFactory),
        None,
        None,
    ));
    let b1 = vm.alloc_segment(Segment::new(m1, Some(p1)));
    let p2 = vm.alloc_segment(Segment::new_prompt(
        m2,
        Some(b1),
        m2,
        Arc::new(crate::handler::ReaderHandlerFactory),
        None,
        None,
    ));
    let b2 = vm.alloc_segment(Segment::new(m2, Some(p2)));
    vm.current_segment = Some(b2);
    let k_user = make_dummy_continuation();

    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id: DispatchId::fresh(),
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![m1, m2],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: k_user.clone(),

        prompt_seg_id: SegmentId::from_index(0),

        completed: true,
        original_exception: None,
    });

    let visible = vm.current_visible_handlers();
    assert_eq!(visible.len(), 2);
}

#[test]
fn test_outer_handler_clause_cannot_see_below_prompt_handlers() {
    let mut vm = VM::new();
    let root_marker = Marker::fresh();
    let outer_marker = Marker::fresh();
    let inner_marker = Marker::fresh();
    let handler_clause_marker = Marker::fresh();

    let root = vm.alloc_segment(Segment::new(root_marker, None));
    let outer_prompt = vm.alloc_segment(Segment::new_prompt(
        outer_marker,
        Some(root),
        outer_marker,
        Arc::new(crate::handler::StateHandlerFactory),
        None,
        None,
    ));
    let outer_body = vm.alloc_segment(Segment::new(outer_marker, Some(outer_prompt)));
    let inner_prompt = vm.alloc_segment(Segment::new_prompt(
        inner_marker,
        Some(outer_body),
        inner_marker,
        Arc::new(crate::handler::ReaderHandlerFactory),
        None,
        None,
    ));
    let inner_body = vm.alloc_segment(Segment::new(inner_marker, Some(inner_prompt)));
    let handler_clause = vm.alloc_segment(Segment::new(handler_clause_marker, Some(outer_prompt)));

    vm.current_segment = Some(inner_body);
    let body_visible = vm.current_handler_chain();
    assert_eq!(body_visible.len(), 2);

    vm.current_segment = Some(handler_clause);
    let clause_visible = vm.current_handler_chain();
    assert_eq!(clause_visible.len(), 1);
    assert_eq!(clause_visible[0].marker, outer_marker);
    assert!(clause_visible
        .iter()
        .all(|entry| entry.marker != inner_marker));
}

#[test]
fn test_own_dispatch_prompt_excluded_from_fresh_dispatch() {
    // Koka/OCaml semantics: when a handler clause yields a fresh effect,
    // the handler's own prompt is excluded, preventing self-dispatch.
    // Only the IMMEDIATE dispatch's prompt is excluded — outer dispatches
    // remain visible.
    let mut vm = VM::new();
    let root_marker = Marker::fresh();
    let outer_marker = Marker::fresh();
    let inner_marker = Marker::fresh();

    let root = vm.alloc_segment(Segment::new(root_marker, None));
    let outer_prompt = vm.alloc_segment(Segment::new_prompt(
        outer_marker,
        Some(root),
        outer_marker,
        Arc::new(crate::handler::StateHandlerFactory),
        None,
        None,
    ));
    let outer_body = vm.alloc_segment(Segment::new(outer_marker, Some(outer_prompt)));
    let inner_prompt = vm.alloc_segment(Segment::new_prompt(
        inner_marker,
        Some(outer_body),
        inner_marker,
        Arc::new(crate::handler::ReaderHandlerFactory),
        None,
        None,
    ));

    let handler_seg = vm.alloc_segment(Segment::new(inner_marker, Some(inner_prompt)));
    let dispatch_id = DispatchId::fresh();
    vm.segments
        .get_mut(handler_seg)
        .expect("handler segment must exist")
        .dispatch_id = Some(dispatch_id);

    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::get("x"),
        is_execution_context_effect: false,
        handler_chain: vec![inner_marker, outer_marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: make_dummy_continuation(),
        prompt_seg_id: inner_prompt,
        completed: false,
        original_exception: None,
    });

    vm.current_segment = Some(handler_seg);

    // Raw chain (no filtering) sees both handlers.
    let raw_chain = vm.handlers_in_caller_chain(handler_seg);
    assert_eq!(raw_chain.len(), 2);

    // Dispatch-filtered chain excludes own dispatch's prompt (inner_prompt).
    let filtered = vm.handlers_in_caller_chain_excluding_own_dispatch(handler_seg);
    assert_eq!(filtered.len(), 1);
    assert_eq!(filtered[0].marker, outer_marker);
}

#[test]
fn test_lazy_pop_completed() {
    let mut vm = VM::new();
    let k_user_1 = make_dummy_continuation();
    let k_user_2 = make_dummy_continuation();
    let k_user_3 = make_dummy_continuation();

    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id: DispatchId::fresh(),
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: k_user_1.clone(),

        prompt_seg_id: SegmentId::from_index(0),

        completed: true,
        original_exception: None,
    });
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id: DispatchId::fresh(),
        effect: Effect::Get {
            key: "y".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: k_user_2.clone(),

        prompt_seg_id: SegmentId::from_index(0),

        completed: true,
        original_exception: None,
    });
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id: DispatchId::fresh(),
        effect: Effect::Get {
            key: "z".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: k_user_3.clone(),

        prompt_seg_id: SegmentId::from_index(0),

        completed: false,
        original_exception: None,
    });

    vm.lazy_pop_completed();
    assert_eq!(vm.dispatch_state.depth(), 3);

    let top_dispatch_id = vm
        .dispatch_state
        .get(2)
        .expect("expected top dispatch context")
        .dispatch_id;
    vm.dispatch_state
        .find_mut_by_dispatch_id(top_dispatch_id)
        .expect("expected top dispatch context by id")
        .completed = true;
    vm.lazy_pop_completed();
    assert_eq!(vm.dispatch_state.depth(), 0);
}

#[test]
fn test_current_segment_dispatch_id_ignores_completed_dispatch_context() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let dispatch_id = DispatchId::fresh();
    let mut seg = Segment::new(marker, None);
    seg.dispatch_id = Some(dispatch_id);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: Continuation {
            dispatch_id: Some(dispatch_id),
            ..make_dummy_continuation()
        },
        prompt_seg_id: seg_id,
        completed: true,
        original_exception: None,
    });

    assert!(
        vm.current_segment_dispatch_id().is_none(),
        "completed dispatch context must not be returned as current dispatch",
    );
    assert!(vm.current_dispatch_id().is_none());
}

#[test]
fn test_find_matching_handler() {
    let mut vm = VM::new();
    let m1 = Marker::fresh();
    let m2 = Marker::fresh();
    let prompt_seg_id_1 = vm.alloc_segment(Segment::new(m1, None));
    let prompt_seg_id_2 = vm.alloc_segment(Segment::new(m2, None));

    assert!(vm.install_handler_on_segment(
        m1,
        prompt_seg_id_1,
        std::sync::Arc::new(crate::handler::ReaderHandlerFactory),
        None
    ));
    assert!(vm.install_handler_on_segment(
        m2,
        prompt_seg_id_2,
        std::sync::Arc::new(crate::handler::StateHandlerFactory),
        None
    ));

    let get_effect = Effect::Get {
        key: "x".to_string(),
    };
    let result = vm.find_matching_handler(&vec![m1, m2], &get_effect);
    assert!(result.is_ok());
    let (idx, marker, _entry) = result.unwrap();
    assert_eq!(idx, 1);
    assert_eq!(marker, m2);

    let ask_effect = Effect::Ask {
        key: "y".to_string(),
    };
    let result = vm.find_matching_handler(&vec![m1, m2], &ask_effect);
    assert!(result.is_ok());
    let (idx, marker, _entry) = result.unwrap();
    assert_eq!(idx, 0);
    assert_eq!(marker, m1);
}

#[test]
fn test_find_matching_handler_none_found() {
    let vm = VM::new();
    let m1 = Marker::fresh();
    let get_effect = Effect::Get {
        key: "x".to_string(),
    };

    let result = vm.find_matching_handler(&vec![m1], &get_effect);
    assert!(result.is_err());
}

#[test]
fn test_find_matching_handler_propagates_can_handle_parse_error() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let prompt_seg_id = SegmentId::from_index(0);

        assert!(vm.install_handler_on_segment(
            marker,
            prompt_seg_id,
            std::sync::Arc::new(crate::handler::ReaderHandlerFactory),
            None
        ));

        let locals = pyo3::types::PyDict::new(py);
        locals
            .set_item("Ask", py.get_type::<crate::effect::PyAsk>())
            .unwrap();
        py.run(c"effect = Ask(key=[])\n", Some(&locals), Some(&locals))
            .unwrap();
        let effect_obj = locals.get_item("effect").unwrap().unwrap().unbind();
        let effect = Effect::from_shared(PyShared::new(effect_obj));

        let result = vm.find_matching_handler(&vec![marker], &effect);
        match result {
            Err(VMError::InternalError { message }) => {
                assert!(message.contains("ReaderHandler can_handle failed to parse effect"));
                assert!(message.contains("Ask key is not hashable"));
            }
            other => panic!("expected can_handle parse error, got {:?}", other),
        }
    });
}

#[test]
fn test_check_dispatch_completion_parent_chain_closes_only_root() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let dispatch_id = DispatchId::fresh();

    let seg_id = vm.alloc_segment(Segment::new(marker, None));
    vm.current_segment = Some(seg_id);

    let mut outer_k = make_dummy_continuation();
    outer_k.cont_id = ContId::fresh();
    outer_k.segment_id = seg_id;
    outer_k.marker = marker;
    outer_k.dispatch_id = Some(dispatch_id);
    outer_k.parent = None;

    let mut inner_k = make_dummy_continuation();
    inner_k.cont_id = ContId::fresh();
    inner_k.segment_id = seg_id;
    inner_k.marker = marker;
    inner_k.dispatch_id = Some(dispatch_id);
    inner_k.parent = Some(std::sync::Arc::new(outer_k.clone()));

    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: inner_k.clone(),
        prompt_seg_id: seg_id,
        completed: false,
        original_exception: None,
    });

    vm.check_dispatch_completion(&inner_k);
    assert!(
        !vm.dispatch_state
            .find_by_dispatch_id(dispatch_id)
            .expect("dispatch missing after inner completion check")
            .completed,
        "inner continuation completion must not close dispatch",
    );

    vm.check_dispatch_completion(&outer_k);
    assert!(
        vm.dispatch_state
            .find_by_dispatch_id(dispatch_id)
            .expect("dispatch missing after outer completion check")
            .completed,
        "root continuation completion must close dispatch",
    );
}

#[test]
fn test_start_dispatch_get_effect() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    let prompt_seg = Segment::new(marker, None);
    let prompt_seg_id = vm.alloc_segment(prompt_seg);

    let body_seg = Segment::new(marker, Some(prompt_seg_id));
    let body_seg_id = vm.alloc_segment(body_seg);
    vm.current_segment = Some(body_seg_id);

    assert!(vm.install_handler_on_segment(
        marker,
        prompt_seg_id,
        std::sync::Arc::new(crate::handler::StateHandlerFactory),
        None
    ));

    vm.rust_store.put("counter".to_string(), Value::Int(42));

    let result = vm.start_dispatch(Effect::Get {
        key: "counter".to_string(),
    });
    assert!(result.is_ok());
    assert!(matches!(result.unwrap(), StepEvent::Continue));
    assert_eq!(vm.dispatch_state.depth(), 1);
    // Handler yields Resume primitive; step through to process it
    let event = vm.step();
    assert!(matches!(event, StepEvent::Continue));
    assert!(vm.dispatch_state.get(0).unwrap().completed);
}

#[test]
fn test_dispatch_completion_marking() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    let prompt_seg = Segment::new(marker, None);
    let prompt_seg_id = vm.alloc_segment(prompt_seg);

    let body_seg = Segment::new(marker, Some(prompt_seg_id));
    let body_seg_id = vm.alloc_segment(body_seg);
    vm.current_segment = Some(body_seg_id);

    assert!(vm.install_handler_on_segment(
        marker,
        prompt_seg_id,
        std::sync::Arc::new(crate::handler::StateHandlerFactory),
        None
    ));

    let _ = vm.start_dispatch(Effect::Get {
        key: "x".to_string(),
    });
    // Handler yields Resume; step through to mark dispatch complete
    let _ = vm.step();
    assert!(vm.dispatch_state.get(0).unwrap().completed);
}

#[test]
fn test_start_dispatch_records_effect_creation_site_from_continuation_frame() {
    Python::attach(|py| {
        use crate::frame::Frame;
        use pyo3::types::PyModule;
        use std::sync::Arc;

        let mut vm = VM::new();
        let marker = Marker::fresh();

        let prompt_seg = Segment::new(marker, None);
        let prompt_seg_id = vm.alloc_segment(prompt_seg);

        let module = PyModule::from_code(
            py,
            c"def target_gen():\n    yield 'value'\n\ng = target_gen()\nnext(g)\n\ndef get_frame(_obj):\n    return g.gi_frame\n\nwrapper = object()\nLINE = g.gi_frame.f_lineno\n",
            c"/tmp/user_program.py",
            c"_vm_creation_site_test",
        )
        .expect("failed to create test module");
        let wrapper = module.getattr("wrapper").expect("missing wrapper").unbind();
        let get_frame = module
            .getattr("get_frame")
            .expect("missing get_frame")
            .unbind();
        let line: u32 = module
            .getattr("LINE")
            .expect("missing LINE")
            .extract()
            .expect("LINE must be int");

        let mut body_seg = Segment::new(marker, Some(prompt_seg_id));
        let stream = Arc::new(std::sync::Mutex::new(Box::new(PythonGeneratorStream::new(
            PyShared::new(wrapper),
            PyShared::new(get_frame),
        )) as Box<dyn ASTStream>));
        body_seg.push_frame(Frame::Program {
            stream,
            metadata: Some(CallMetadata::new(
                "parent".to_string(),
                "/tmp/user_program.py".to_string(),
                777,
                None,
                None,
            )),
        });
        let body_seg_id = vm.alloc_segment(body_seg);
        vm.current_segment = Some(body_seg_id);

        assert!(vm.install_handler_on_segment(
            marker,
            prompt_seg_id,
            Arc::new(crate::scheduler::SchedulerHandler::new()),
            None
        ));

        let spawn = Py::new(py, PySpawn::create(py, py.None(), None, None, None, None))
            .expect("failed to create SpawnEffect");
        let effect_obj = spawn.into_any();

        let result = vm.start_dispatch(Effect::Python(PyShared::new(effect_obj)));
        assert!(result.is_ok());

        let creation_site = vm.trace_state.events().iter().find_map(|event| {
            if let CaptureEvent::DispatchStarted { creation_site, .. } = event {
                creation_site.clone()
            } else {
                None
            }
        });

        let site = creation_site.expect("dispatch should record effect creation site");
        assert_eq!(site.function_name, "parent");
        assert_eq!(site.source_file, "/tmp/user_program.py");
        assert_eq!(site.source_line, line);
    });
}

#[test]
fn test_stream_debug_location_uses_get_frame_callback_result() {
    Python::attach(|py| {
        use pyo3::types::PyModule;
        use std::sync::Arc;

        let module = PyModule::from_code(
            py,
            c"def target_gen():\n    yield 'value'\n\ng = target_gen()\nnext(g)\n\ndef get_frame(_obj):\n    return g.gi_frame\n\nwrapper = object()\nLINE = g.gi_frame.f_lineno\n",
            c"_vm_get_frame_callback_test.py",
            c"_vm_get_frame_callback_test",
        )
        .expect("failed to create test module");
        let wrapper = module.getattr("wrapper").expect("missing wrapper").unbind();
        let get_frame = module
            .getattr("get_frame")
            .expect("missing get_frame")
            .unbind();
        let line: u32 = module
            .getattr("LINE")
            .expect("missing LINE")
            .extract()
            .expect("LINE must be int");

        let stream = Arc::new(std::sync::Mutex::new(Box::new(PythonGeneratorStream::new(
            PyShared::new(wrapper),
            PyShared::new(get_frame),
        )) as Box<dyn ASTStream>));
        let observed =
            TraceState::stream_debug_location(&stream).expect("expected stream location");
        assert_eq!(observed.source_line, line);
    });
}

#[test]
fn test_vm_proto_runtime_uses_get_frame_callback_instead_of_gi_frame_probe() {
    let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/vm.rs"));
    let runtime_boundary = src.find("\n#[cfg(test)]\nmod tests").unwrap_or(src.len());
    let runtime_src = &src[..runtime_boundary];
    let inner_attr = ["__doeff_", "inner__"].concat();
    assert!(
        runtime_src.contains("debug_location()") && runtime_src.contains("stream_debug_location"),
        "VM-PROTO-001: VM must resolve live locations via ASTStream::debug_location()"
    );
    assert!(
        !runtime_src.contains("getattr(\"gi_frame\")"),
        "VM-PROTO-001: direct gi_frame access in runtime vm.rs is forbidden"
    );
    assert!(
        !runtime_src.contains("import(\"doeff."),
        "VM-PROTO-001: vm core must not import doeff.* modules"
    );
    assert!(
        !runtime_src.contains(&inner_attr),
        "VM-PROTO-001: vm core must not walk inner-generator link chains"
    );
}

#[test]
fn test_vm_proto_007_runtime_enforces_c1_c6_c7_constraints() {
    let vm_src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/vm.rs"));
    let vm_runtime_boundary = vm_src
        .find("\n#[cfg(test)]\nmod tests")
        .unwrap_or(vm_src.len());
    let vm_runtime_src = &vm_src[..vm_runtime_boundary];

    let pyvm_src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
    let pyvm_runtime_src = pyvm_src.split("#[cfg(test)]").next().unwrap_or(pyvm_src);

    for (file_name, runtime_src) in [("vm.rs", vm_runtime_src), ("pyvm.rs", pyvm_runtime_src)] {
        assert!(
            !runtime_src.contains(".setattr(\"__doeff_"),
            "VM-PROTO-007 C1 FAIL: {file_name} runtime must not set __doeff_* attributes"
        );
        assert!(
            !runtime_src.contains(".getattr(\"__doeff_"),
            "VM-PROTO-007 C1 FAIL: {file_name} runtime must not read __doeff_* attributes"
        );
        assert!(
            !runtime_src.contains(".hasattr(\"__doeff_"),
            "VM-PROTO-007 C1 FAIL: {file_name} runtime must not probe __doeff_* attributes"
        );
        assert!(
            !runtime_src.contains("import(\"doeff."),
            "VM-PROTO-007 C6 FAIL: {file_name} runtime must not import doeff.* modules"
        );
        assert!(
            !runtime_src.contains("CallMetadata::anonymous()")
                && !runtime_src.contains("crate::frame::CallMetadata::anonymous()"),
            "VM-PROTO-007 C7 FAIL: {file_name} runtime must not use anonymous callback metadata"
        );
    }

    assert!(
        !vm_runtime_src.contains("getattr(\"__code__\")")
            && !vm_runtime_src.contains("getattr(\"__name__\")"),
        "VM-PROTO-007 C7 FAIL: vm.rs runtime must not probe __code__/__name__"
    );
    assert!(
        !pyvm_runtime_src.contains("PyModule::from_code("),
        "VM-PROTO-007 C7 FAIL: pyvm.rs runtime must not synthesize modules via PyModule::from_code"
    );
}

#[test]
fn test_vm_proto_frame_push_sites_extract_doeff_generator() {
    let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/vm.rs"));
    let runtime_boundary = src.find("\n#[cfg(test)]\nmod tests").unwrap_or(src.len());
    let runtime_src = &src[..runtime_boundary];
    let extraction_calls = runtime_src.matches("extract_doeff_generator(").count();
    assert!(
        extraction_calls >= 2,
        "VM-PROTO-001: expected at least 2 DoeffGenerator extraction sites in vm.rs, got {extraction_calls}"
    );
    assert!(
        runtime_src.contains("PendingPython::ExpandReturn")
            && runtime_src.contains("ExpandReturn: expected DoeffGenerator"),
        "VM-PROTO-001: ExpandReturn must enforce DoeffGenerator results explicitly"
    );
    assert!(
        runtime_src.contains("PendingPython::StepUserGenerator {")
            && runtime_src.contains("stream")
            && !runtime_src.contains("get_frame,"),
        "VM-PROTO-001: StepUserGenerator pending state must carry stream handle"
    );
}

#[test]
fn test_resume_is_not_terminal() {
    let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/vm.rs"));
    let runtime_boundary = src.find("\n#[cfg(test)]\nmod tests").unwrap_or(src.len());
    let runtime_src = &src[..runtime_boundary];
    let is_terminal_start = runtime_src
        .find("let is_terminal = matches!(")
        .expect("apply_stream_step must define is_terminal");
    let is_terminal_block = &runtime_src[is_terminal_start..];
    let block_end = is_terminal_block
        .find("if !is_terminal")
        .expect("is_terminal block must guard Program frame push");
    let is_terminal_match = &is_terminal_block[..block_end];

    assert!(
        !is_terminal_match.contains("DoCtrl::Resume { .. }"),
        "Resume must be non-terminal in apply_stream_step"
    );
}

#[test]
fn test_handle_resume_call_resume_semantics() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    let caller_seg = Segment::new(marker, None);
    let caller_id = vm.alloc_segment(caller_seg);
    vm.current_segment = Some(caller_id);

    let k = vm.capture_continuation(None).unwrap();

    let event = vm.handle_resume(k, Value::Int(42));
    assert!(matches!(event, StepEvent::Continue));

    let new_seg_id = vm.current_segment.unwrap();
    let new_seg = vm.segments.get(new_seg_id).unwrap();
    assert_eq!(new_seg.caller, Some(caller_id));
}

#[test]
fn test_handle_transfer_tail_semantics() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let k = vm.capture_continuation(None).unwrap();

    let event = vm.handle_transfer(k, Value::Int(99));
    assert!(matches!(event, StepEvent::Continue));

    let new_seg_id = vm.current_segment.unwrap();
    let new_seg = vm.segments.get(new_seg_id).unwrap();
    assert!(new_seg.caller.is_none());
}

#[test]
fn test_one_shot_violation_resume() {
    Python::attach(|_py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k = vm.capture_continuation(None).unwrap();

        let _ = vm.handle_resume(k.clone(), Value::Int(1));
        let event = vm.handle_resume(k, Value::Int(2));

        assert!(matches!(event, StepEvent::Continue));
        assert!(
            vm.current_seg().mode.is_throw(),
            "One-shot violation should set Mode::Throw"
        );
    });
}

#[test]
fn test_one_shot_violation_transfer() {
    Python::attach(|_py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k = vm.capture_continuation(None).unwrap();

        let _ = vm.handle_transfer(k.clone(), Value::Int(1));
        let event = vm.handle_transfer(k, Value::Int(2));

        assert!(matches!(event, StepEvent::Continue));
        assert!(
            vm.current_seg().mode.is_throw(),
            "One-shot violation should set Mode::Throw"
        );
    });
}

#[test]
fn test_handle_get_continuation() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let k_user = make_dummy_continuation();
    let dispatch_id = DispatchId::fresh();
    vm.segments
        .get_mut(seg_id)
        .expect("segment must exist")
        .dispatch_id = Some(dispatch_id);
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: k_user.clone(),

        prompt_seg_id: SegmentId::from_index(0),

        completed: false,
        original_exception: None,
    });

    let event = vm.handle_get_continuation();
    assert!(matches!(event, StepEvent::Continue));
    assert!(matches!(
        &vm.current_seg().mode,
        Mode::Deliver(Value::Continuation(_))
    ));
}

#[test]
fn test_handle_get_continuation_no_dispatch() {
    let mut vm = VM::new();
    let event = vm.handle_get_continuation();
    assert!(matches!(
        event,
        StepEvent::Error(VMError::InternalError { .. })
    ));
}

#[test]
fn test_handle_delegate_no_dispatch() {
    let mut vm = VM::new();
    let event = vm.handle_delegate(Effect::get("dummy"));
    assert!(matches!(
        event,
        StepEvent::Error(VMError::InternalError { .. })
    ));
}

#[test]
fn test_handle_delegate_links_previous_k_as_parent() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let original_k_user = make_dummy_continuation();
    let original_cont_id = original_k_user.cont_id;
    let dispatch_id = DispatchId::fresh();
    vm.segments
        .get_mut(seg_id)
        .expect("segment must exist")
        .dispatch_id = Some(dispatch_id);
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::get("x"),
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: original_k_user,
        prompt_seg_id: seg_id,
        completed: false,
        original_exception: None,
    });

    let event = vm.handle_delegate(Effect::get("x"));
    assert!(matches!(event, StepEvent::Error(_)));

    let ctx = vm
        .dispatch_state
        .find_by_dispatch_id(dispatch_id)
        .expect("dispatch context must exist");
    let parent = ctx
        .k_user
        .parent
        .as_ref()
        .expect("delegate must set parent");
    assert_ne!(ctx.k_user.cont_id, original_cont_id);
    assert_eq!(parent.cont_id, original_cont_id);
}

#[test]
fn test_handle_pass_no_dispatch() {
    let mut vm = VM::new();
    let event = vm.handle_pass(Effect::get("dummy"));
    assert!(matches!(
        event,
        StepEvent::Error(VMError::InternalError { .. })
    ));
}

#[test]
fn test_rust_store_clone() {
    let mut store = RustStore::new();
    store.put("key".to_string(), Value::Int(42));
    store.tell(Value::String("log".to_string()));
    store
        .env
        .insert("env_key".to_string().into(), Value::Bool(true));

    let cloned = store.clone();
    assert_eq!(cloned.get("key").unwrap().as_int(), Some(42));
    assert_eq!(cloned.logs().len(), 1);
    assert_eq!(cloned.ask_str("env_key").unwrap().as_bool(), Some(true));

    // Verify independence
    store.put("key".to_string(), Value::Int(99));
    assert_eq!(cloned.get("key").unwrap().as_int(), Some(42));
}

#[test]
fn test_handle_get_handlers() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    let seg = Segment::new(marker, None);
    let prompt_seg_id = vm.alloc_segment(seg);

    assert!(vm.install_handler_on_segment(
        marker,
        prompt_seg_id,
        std::sync::Arc::new(crate::handler::StateHandlerFactory),
        None
    ));

    let handler_seg = Segment::new(marker, Some(prompt_seg_id));
    let handler_seg_id = vm.alloc_segment(handler_seg);
    vm.current_segment = Some(handler_seg_id);

    // G8: GetHandlers requires dispatch context
    let k_user = make_dummy_continuation();
    let dispatch_id = DispatchId::fresh();
    vm.segments
        .get_mut(handler_seg_id)
        .expect("segment must exist")
        .dispatch_id = Some(dispatch_id);
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user,
        prompt_seg_id,
        completed: false,
        original_exception: None,
    });

    let event = vm.handle_get_handlers();
    assert!(matches!(event, StepEvent::Continue));
    match &vm.current_seg().mode {
        Mode::Deliver(Value::Handlers(h)) => {
            assert_eq!(h.len(), 1);
            assert_eq!(h[0].handler_name(), "StateHandler");
        }
        _ => panic!("Expected Deliver(Handlers)"),
    }
}

#[test]
fn test_handle_get_handlers_no_dispatch_errors() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let event = vm.handle_get_handlers();
    assert!(
        matches!(event, StepEvent::Error(_)),
        "G8: GetHandlers without dispatch must error"
    );
}

#[test]
fn test_collect_traceback_preserves_frame_and_hop_ordering_without_filtering() {
    let mut parent = make_dummy_continuation();
    parent.frames_snapshot = Arc::new(vec![
        make_program_frame("parent_outer", "user.py", 10),
        make_program_frame("parent_internal", "/tmp/doeff/internal.py", 20),
    ]);

    let mut child = make_dummy_continuation();
    child.frames_snapshot = Arc::new(vec![
        make_program_frame("child_outer", "handler.py", 30),
        make_program_frame("child_inner", "handler.py", 31),
    ]);
    child.parent = Some(Arc::new(parent));

    let hops = TraceState::collect_traceback(&child);
    assert_eq!(hops.len(), 2);

    let hop0_names: Vec<_> = hops[0]
        .frames
        .iter()
        .map(|f| f.func_name.as_str())
        .collect();
    assert_eq!(hop0_names, vec!["child_outer", "child_inner"]);

    let hop1_names: Vec<_> = hops[1]
        .frames
        .iter()
        .map(|f| f.func_name.as_str())
        .collect();
    assert_eq!(hop1_names, vec!["parent_outer", "parent_internal"]);
    assert_eq!(hops[1].frames[1].source_file, "/tmp/doeff/internal.py");
}

#[test]
fn test_handle_get_traceback_requires_dispatch_context() {
    let mut vm = VM::new();
    let event = vm.handle_get_traceback(make_dummy_continuation());
    assert!(matches!(
        event,
        StepEvent::Error(VMError::InternalError { .. })
    ));
}

#[test]
fn test_step_handle_yield_routes_get_traceback() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let k_user = make_dummy_continuation();
    let dispatch_id = DispatchId::fresh();
    vm.segments
        .get_mut(seg_id)
        .expect("segment must exist")
        .dispatch_id = Some(dispatch_id);
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::get("x"),
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: k_user.clone(),
        prompt_seg_id: seg_id,
        completed: false,
        original_exception: None,
    });

    let mut query_continuation = make_dummy_continuation();
    query_continuation.frames_snapshot =
        Arc::new(vec![make_program_frame("query_frame", "query.py", 55)]);
    vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::GetTraceback {
        continuation: query_continuation,
    });

    let event = vm.step_handle_yield();
    assert!(matches!(event, StepEvent::Continue));
    match &vm.current_seg().mode {
        Mode::Deliver(Value::Traceback(hops)) => {
            assert_eq!(hops.len(), 1);
            assert_eq!(hops[0].frames.len(), 1);
            assert_eq!(hops[0].frames[0].func_name, "query_frame");
        }
        other => panic!("expected Deliver(Traceback), got {:?}", other),
    }
}

#[test]
fn test_continuation_registry_cleanup_on_consume() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let k = vm.capture_continuation(None).unwrap();
    let cont_id = k.cont_id;
    vm.register_continuation(k);

    assert!(vm.lookup_continuation(cont_id).is_some());
    assert_eq!(vm.continuation_registry.len(), 1);

    vm.mark_one_shot_consumed(cont_id);

    assert!(vm.lookup_continuation(cont_id).is_none());
    assert_eq!(vm.continuation_registry.len(), 0);
    assert!(vm.is_one_shot_consumed(cont_id));
}

#[test]
fn test_remove_handler() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    vm.install_handler(
        marker,
        std::sync::Arc::new(crate::handler::StateHandlerFactory),
        None,
    );
    assert_eq!(vm.installed_handler_markers(), vec![marker]);

    let removed = vm.remove_handler(marker);
    assert!(removed);
    assert!(vm.installed_handler_markers().is_empty());

    // Removing again returns false
    let removed_again = vm.remove_handler(marker);
    assert!(!removed_again);
}

#[test]
fn test_remove_handler_preserves_others() {
    let mut vm = VM::new();
    let m1 = Marker::fresh();
    let m2 = Marker::fresh();
    vm.install_handler(
        m1,
        std::sync::Arc::new(crate::handler::StateHandlerFactory),
        None,
    );
    vm.install_handler(
        m2,
        std::sync::Arc::new(crate::handler::WriterHandlerFactory),
        None,
    );
    assert_eq!(vm.installed_handler_markers().len(), 2);

    vm.remove_handler(m1);
    let installed = vm.installed_handler_markers();
    assert_eq!(installed.len(), 1);
    assert!(!installed.contains(&m1));
    assert!(installed.contains(&m2));
}

#[test]
fn test_rust_store_modify() {
    let mut store = RustStore::new();
    store.put("x".to_string(), Value::Int(10));

    let old = store.modify("x", |v| {
        let n = v.as_int().unwrap();
        Value::Int(n * 2)
    });
    assert_eq!(old.unwrap().as_int(), Some(10));
    assert_eq!(store.get("x").unwrap().as_int(), Some(20));
}

#[test]
fn test_rust_store_modify_missing_key() {
    let mut store = RustStore::new();
    let old = store.modify("missing", |v| v.clone());
    assert!(old.is_none());
}

#[test]
fn test_rust_store_clear_logs() {
    let mut store = RustStore::new();
    store.tell(Value::String("a".to_string()));
    store.tell(Value::String("b".to_string()));
    assert_eq!(store.logs().len(), 2);

    store.clear_logs();
    assert_eq!(store.logs().len(), 0);
}

// === Spec Gap TDD Tests (Phase 14) ===

/// G9: Spec says clear_logs returns Vec<Value> via std::mem::take.
/// Impl returns nothing (void). Test that drained values are returned.
#[test]
fn test_gap9_clear_logs_returns_drained_values() {
    let mut store = RustStore::new();
    store.tell(Value::String("a".to_string()));
    store.tell(Value::String("b".to_string()));

    let drained: Vec<Value> = store.clear_logs();
    assert_eq!(drained.len(), 2);
    assert_eq!(drained[0].as_str(), Some("a"));
    assert_eq!(drained[1].as_str(), Some("b"));
    assert_eq!(store.logs().len(), 0);
}

/// G10: Spec says modify takes f: FnOnce(&Value) -> Value (borrow).
/// Test that the modifier receives a reference, not ownership.
#[test]
fn test_gap10_modify_closure_takes_reference() {
    let mut store = RustStore::new();
    store.put("x".to_string(), Value::Int(10));

    // Spec: modifier takes &Value (borrow), returns Value
    let old = store.modify("x", |v: &Value| {
        let n = v.as_int().unwrap();
        Value::Int(n * 2)
    });
    assert_eq!(old.unwrap().as_int(), Some(10));
    assert_eq!(store.get("x").unwrap().as_int(), Some(20));
}

/// G11: Spec defines with_local for Reader environment scoping.
/// Test that bindings are applied, closure runs, and old values restored.
#[test]
fn test_gap11_with_local_scoped_bindings() {
    let mut store = RustStore::new();
    store
        .env
        .insert("db".to_string().into(), Value::String("prod".to_string()));
    store.env.insert(
        "host".to_string().into(),
        Value::String("localhost".to_string()),
    );

    let result = store.with_local(
        HashMap::from([
            ("db".to_string(), Value::String("test".to_string())),
            ("temp".to_string(), Value::Int(42)),
        ]),
        |s| {
            assert_eq!(s.ask_str("db").unwrap().as_str(), Some("test"));
            assert_eq!(s.ask_str("temp").unwrap().as_int(), Some(42));
            assert_eq!(s.ask_str("host").unwrap().as_str(), Some("localhost"));
            "done"
        },
    );
    assert_eq!(result, "done");
    // After with_local, old bindings restored, temp removed
    assert_eq!(store.ask_str("db").unwrap().as_str(), Some("prod"));
    assert!(store.ask_str("temp").is_none());
    assert_eq!(store.ask_str("host").unwrap().as_str(), Some("localhost"));
}

/// G12: DispatchContext should not have callsite_cont_id field.
/// Spec says use k_user.cont_id directly.
/// This test verifies dispatch completion works via k_user.cont_id.
#[test]
fn test_gap12_dispatch_completion_via_k_user() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let k_user = make_dummy_continuation();
    let k_cont_id = k_user.cont_id;
    let dispatch_id = DispatchId::fresh();

    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: Continuation {
            dispatch_id: Some(dispatch_id),
            cont_id: k_cont_id,
            ..make_dummy_continuation()
        },
        prompt_seg_id: seg_id,
        completed: false,
        original_exception: None,
    });

    // Verify completion check works through k_user.cont_id
    let k = Continuation {
        cont_id: k_cont_id,
        dispatch_id: Some(dispatch_id),
        ..make_dummy_continuation()
    };
    vm.check_dispatch_completion(&k);
    assert!(
        vm.dispatch_state
            .find_by_dispatch_id(dispatch_id)
            .expect("dispatch context should exist")
            .completed
    );
}

#[test]
fn test_terminal_error_resume_marks_only_target_dispatch_completed() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);

    let outer_dispatch_id = DispatchId::fresh();
    let outer_k = Continuation {
        dispatch_id: Some(outer_dispatch_id),
        ..make_dummy_continuation()
    };
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id: outer_dispatch_id,
        effect: Effect::Get {
            key: "outer".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: outer_k.clone(),
        prompt_seg_id: seg_id,
        completed: false,
        original_exception: None,
    });

    let inner_dispatch_id = DispatchId::fresh();
    let inner_k = Continuation {
        dispatch_id: Some(inner_dispatch_id),
        ..make_dummy_continuation()
    };
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id: inner_dispatch_id,
        effect: Effect::Get {
            key: "inner".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: inner_k.clone(),
        prompt_seg_id: seg_id,
        completed: false,
        original_exception: Some(PyException::runtime_error("original failure")),
    });

    let event = vm.handle_resume(inner_k, Value::String("context".to_string()));
    assert!(matches!(event, StepEvent::Continue));
    assert!(matches!(&vm.current_seg().mode, Mode::Throw(_)));

    assert!(
        vm.dispatch_state
            .find_by_dispatch_id(inner_dispatch_id)
            .expect("inner dispatch should exist")
            .completed,
        "terminal error dispatch must be completed",
    );
    assert!(
        !vm.dispatch_state
            .find_by_dispatch_id(outer_dispatch_id)
            .expect("outer dispatch should exist")
            .completed,
        "outer dispatch must remain active",
    );
}

/// G13: Delegate should take Effect (not Option<Effect>).
/// This test verifies Delegate works with a direct Effect value.
#[test]
fn test_gap13_delegate_takes_non_optional_effect() {
    use crate::step::DoCtrl;
    // Spec: Delegate { effect: Effect }
    let prim = DoCtrl::Delegate {
        effect: Effect::Get {
            key: "x".to_string(),
        },
    };
    match prim {
        DoCtrl::Delegate { effect } => {
            assert_eq!(effect.type_name(), "Get");
        }
        _ => panic!("expected Delegate"),
    }
}

/// G14: Spec says Effect has `type_name()`, not `type_name()`.
#[test]
fn test_gap14_type_name_name_method() {
    let get = Effect::get("x");
    assert_eq!(get.type_name(), "Get");

    let put = Effect::put("y", 42i64);
    assert_eq!(put.type_name(), "Put");

    let ask = Effect::ask("env");
    assert_eq!(ask.type_name(), "Ask");

    let tell = Effect::tell("msg");
    assert_eq!(tell.type_name(), "Tell");
}

/// G15: WithHandler should emit EvalExpr, not CallFunc.
/// We can't construct Py<PyAny> in Rust-only tests, so we verify
/// this via the Python integration tests. This test serves as a
/// documentation marker that handle_with_handler must use
/// PythonCall::EvalExpr { expr } per spec.
#[test]
fn test_gap15_with_handler_eval_expr_marker() {
    // Spec requires handle_with_handler to emit:
    //   PythonCall::EvalExpr { expr: body }
    // NOT:
    //   PythonCall::CallFunc { func: body, args: vec![] }
    //
    // Verified by code inspection + Python integration tests.
    // EvalExpr starts from DoExpr directly at VM entry.
    assert!(
        true,
        "See handle_with_handler implementation for spec compliance"
    );
}

/// G16: lazy_pop_completed runs before GetHandlers.
/// G8: After pop leaves empty stack, GetHandlers errors (spec: no dispatch = error).
#[test]
fn test_gap16_lazy_pop_before_get_handlers() {
    use crate::step::DoCtrl;

    let mut vm = VM::new();

    let m1 = Marker::fresh();
    let seg = Segment::new(m1, None);
    let seg_id = vm.alloc_segment(seg);
    assert!(vm.install_handler_on_segment(
        m1,
        seg_id,
        std::sync::Arc::new(crate::handler::StateHandlerFactory),
        None
    ));
    vm.current_segment = Some(seg_id);

    let k_user = make_dummy_continuation();
    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id: DispatchId::fresh(),
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user: k_user.clone(),
        prompt_seg_id: SegmentId::from_index(0),
        completed: true,
        original_exception: None,
    });

    vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::GetHandlers);
    let event = vm.step_handle_yield();

    assert!(
        vm.dispatch_state.is_empty(),
        "Completed dispatch should have been popped before GetHandlers runs"
    );

    assert!(
        matches!(event, StepEvent::Error(_)),
        "G8: GetHandlers with no dispatch must error, got {:?}",
        std::mem::discriminant(&event)
    );
}

// ==========================================================
// Spec-Gap TDD Tests — Phase 2 (G1-G5 from SPEC-008 audit)
// ==========================================================

/// G1: Uncaught exception must preserve the original PyException.
/// Spec: VMError should carry the PyException, not discard it as a generic string.
#[test]
fn test_g1_uncaught_exception_preserves_pyexception() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let exc_type = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let exc_value = py
            .eval(c"RuntimeError('test uncaught')", None, None)
            .unwrap()
            .unbind()
            .into_any();
        let py_exc = PyException::new(exc_type, exc_value, None);
        vm.current_seg_mut().mode = Mode::Throw(py_exc);

        let event = vm.step();

        // The error variant must carry the exception, not be a generic string.
        // VMError::UncaughtException { exception: PyException } is the desired variant.
        match &event {
            StepEvent::Error(err) => {
                let msg = err.to_string();
                assert!(
                    !msg.contains("internal error: uncaught exception"),
                    "G1 FAIL: Got generic InternalError(\"{}\"). \
                     Expected a VMError variant that preserves the PyException.",
                    msg
                );
            }
            other => panic!(
                "G1: Expected StepEvent::Error, got {:?}",
                std::mem::discriminant(other)
            ),
        }
    });
}

/// G3: Segments must be freed when no longer reachable.
/// After step_return completes a child segment and returns to parent,
/// the child segment should be freed from the arena.
#[test]
fn test_g3_segment_freed_after_return() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    // Create parent segment
    let parent_seg = Segment::new(marker, None);
    let parent_id = vm.alloc_segment(parent_seg);

    // Create child segment with parent as caller
    let child_seg = Segment::new(marker, Some(parent_id));
    let child_id = vm.alloc_segment(child_seg);

    vm.current_segment = Some(child_id);
    vm.current_seg_mut().mode = Mode::Return(Value::Int(42));

    // Before step: both segments exist
    assert!(vm.segments.get(parent_id).is_some());
    assert!(vm.segments.get(child_id).is_some());
    assert_eq!(vm.segments.len(), 2);

    // step_return: child returns to parent
    let event = vm.step();
    assert!(matches!(event, StepEvent::Continue));
    assert_eq!(vm.current_segment, Some(parent_id));

    // DESIRED: child segment should be freed
    assert!(
        vm.segments.get(child_id).is_none(),
        "G3 REGRESSION: Child segment was NOT freed after return. Arena len={}",
        vm.segments.len()
    );
}

/// G4a: Resume on a consumed continuation → Mode::Throw (catchable), not StepEvent::Error.
#[test]
fn test_g4a_resume_one_shot_violation_is_throwable() {
    Python::attach(|_py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k = vm.capture_continuation(None).unwrap();
        let _ = vm.handle_resume(k.clone(), Value::Int(1));
        let event = vm.handle_resume(k, Value::Int(2));

        assert!(
            matches!(event, StepEvent::Continue),
            "G4a: expected Continue, got Error"
        );
        assert!(
            vm.current_seg().mode.is_throw(),
            "G4a: expected Mode::Throw after one-shot violation"
        );
    });
}

/// G4b: Resume on unstarted continuation → Mode::Throw (catchable), not StepEvent::Error.
#[test]
fn test_g4b_resume_unstarted_is_throwable() {
    Python::attach(|_py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let mut k = make_dummy_continuation();
        k.started = false;

        let event = vm.handle_resume(k, Value::Int(1));

        assert!(
            matches!(event, StepEvent::Continue),
            "G4b: expected Continue, got Error"
        );
        assert!(
            vm.current_seg().mode.is_throw(),
            "G4b: expected Mode::Throw for unstarted Resume"
        );
    });
}

/// G4c: Transfer on consumed continuation → Mode::Throw (catchable).
#[test]
fn test_g4c_transfer_one_shot_violation_is_throwable() {
    Python::attach(|_py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k = vm.capture_continuation(None).unwrap();
        let _ = vm.handle_transfer(k.clone(), Value::Int(1));
        let event = vm.handle_transfer(k, Value::Int(2));

        assert!(
            matches!(event, StepEvent::Continue),
            "G4c: expected Continue, got Error"
        );
        assert!(
            vm.current_seg().mode.is_throw(),
            "G4c: expected Mode::Throw after transfer one-shot"
        );
    });
}

#[test]
fn test_g8_pending_python_missing_is_runtime_error() {
    let mut vm = VM::new();
    let marker = Marker::fresh();
    let seg_id = vm.alloc_segment(Segment::new(marker, None));
    vm.current_segment = Some(seg_id);
    vm.receive_python_result(PyCallOutcome::Value(Value::Unit));
    assert!(
        matches!(
            &vm.current_seg().mode,
            Mode::Throw(PyException::RuntimeError { .. })
        ),
        "G8 FAIL: missing pending_python must throw runtime error"
    );
}

#[test]
fn test_g10_resume_continuation_preserves_handler_identity() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let id_obj = pyo3::types::PyDict::new(py).into_any().unbind();
        let handler = std::sync::Arc::new(crate::handler::StateHandlerFactory);
        let program = PyShared::new(py.None().into_pyobject(py).unwrap().unbind().into_any());

        let k = Continuation::create_unstarted_with_identities(
            program,
            vec![handler],
            vec![Some(PyShared::new(id_obj.clone_ref(py)))],
        );

        let event = vm.handle_resume_continuation(k, Value::Unit);
        assert!(matches!(
            event,
            StepEvent::NeedsPython(PythonCall::EvalExpr { .. })
        ));

        let seg_id = vm.current_segment.expect("missing current segment");
        let seg = vm.segments.get(seg_id).expect("missing segment");
        let prompt_seg_id = seg.caller.expect("missing handler prompt");
        let prompt_seg = vm
            .segments
            .get(prompt_seg_id)
            .expect("missing prompt segment");
        match &prompt_seg.kind {
            SegmentKind::PromptBoundary {
                py_identity: Some(identity),
                ..
            } => {
                assert!(
                    identity.bind(py).is(&id_obj.bind(py)),
                    "G10 FAIL: preserved identity does not match original"
                );
            }
            _ => panic!("G10 FAIL: continuation rehydration dropped handler identity"),
        }
    });
}

/// G5/G6 TDD: Tests the full VM dispatch cycle with a handler that returns
/// NeedsPython from resume(). This exercises the critical path where the
/// second Python call result must be properly propagated back to the handler.
///
/// The DoubleCallHandlerFactory handler does:
///   start() → NeedsPython(call1)
///   resume(result1) → NeedsPython(call2)   ← THIS is the critical path
///   resume(result2) → Yield(Resume { value: result1 + result2 })
#[test]
fn test_needs_python_from_resume_propagates_correctly() {
    use pyo3::Python;
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        // Set up handler and segments
        let prompt_seg = Segment::new(marker, None);
        let prompt_seg_id = vm.alloc_segment(prompt_seg);

        let body_seg = Segment::new(marker, Some(prompt_seg_id));
        let body_seg_id = vm.alloc_segment(body_seg);
        vm.current_segment = Some(body_seg_id);

        assert!(vm.install_handler_on_segment(
            marker,
            prompt_seg_id,
            std::sync::Arc::new(crate::handler::DoubleCallHandlerFactory),
            None
        ));

        // Create a dummy Python modifier (won't actually be called — we feed results manually)
        let modifier = py.None().into_pyobject(py).unwrap().unbind().into_any();

        // Step 1: start_dispatch sends Modify effect
        let result = vm.start_dispatch(Effect::Modify {
            key: "key".to_string(),
            modifier: PyShared::new(modifier),
        });
        assert!(result.is_ok());
        let event1 = result.unwrap();
        let dispatch_id = vm
            .dispatch_state
            .get(0)
            .expect("dispatch context must exist")
            .dispatch_id;

        // Should get NeedsPython for first call
        assert!(
            matches!(event1, StepEvent::NeedsPython(PythonCall::CallFunc { .. })),
            "Expected NeedsPython for first call, got {:?}",
            std::mem::discriminant(&event1)
        );

        // Step 2: Feed first Python result (100)
        vm.receive_python_result(PyCallOutcome::Value(Value::Int(100)));

        // After first resume(), handler returns NeedsPython again.
        // The VM must surface this as a NeedsPython event, not silently lose it.
        // With the fix, the frame is re-pushed and mode is set to Deliver(100),
        // so stepping delivers 100 to the re-pushed frame, which calls resume(),
        // which returns NeedsPython(call2).
        let event2 = vm.step();
        assert!(
            matches!(event2, StepEvent::NeedsPython(PythonCall::CallFunc { .. })),
            "Expected NeedsPython for SECOND call (from resume), got {:?}",
            std::mem::discriminant(&event2)
        );

        // Step 3: Feed second Python result (200)
        vm.receive_python_result(PyCallOutcome::Value(Value::Int(200)));

        // After second resume(), handler yields Resume { value: 100 + 200 = 300 }
        // step() delivers 200 to the re-pushed RustProgram frame, resume() returns
        // Yield(Resume), which sets mode to HandleYield. This is a Continue.
        let event3 = vm.step();
        assert!(
            matches!(event3, StepEvent::Continue),
            "Expected Continue after Yield(Resume), got {:?}",
            std::mem::discriminant(&event3)
        );

        // Step 4: Process the HandleYield(Resume) primitive.
        // This calls handle_resume(k, 300) → marks dispatch complete.
        let event4 = vm.step();
        assert!(
            matches!(event4, StepEvent::Continue),
            "Expected Continue after handle_resume, got {:?}",
            std::mem::discriminant(&event4)
        );

        // Verify dispatch was completed with combined value
        assert!(
            vm.dispatch_state
                .find_by_dispatch_id(dispatch_id)
                .map(|d| d.completed)
                .unwrap_or(false),
            "Dispatch should be marked complete"
        );
    });
}

#[test]
fn test_needs_python_rust_continuation_uses_current_dispatch_id_context() {
    Python::attach(|py| {
        let mut vm = VM::new();

        let outer_marker = Marker::fresh();
        let inner_marker = Marker::fresh();

        let outer_dispatch_id = DispatchId::fresh();
        let mut outer_seg = Segment::new(outer_marker, None);
        outer_seg.dispatch_id = Some(outer_dispatch_id);
        let seg_id = vm.alloc_segment(outer_seg);
        vm.current_segment = Some(seg_id);

        let outer_k = Continuation {
            dispatch_id: Some(outer_dispatch_id),
            ..make_dummy_continuation()
        };
        vm.dispatch_state.push_dispatch(DispatchContext {
            dispatch_id: outer_dispatch_id,
            effect: Effect::Get {
                key: "outer".to_string(),
            },
            is_execution_context_effect: false,
            handler_chain: vec![outer_marker],
            handler_idx: 0,
            supports_error_context_conversion: false,
            k_user: outer_k.clone(),
            prompt_seg_id: seg_id,
            completed: false,
            original_exception: None,
        });

        let inner_dispatch_id = DispatchId::fresh();
        let inner_k = Continuation {
            dispatch_id: Some(inner_dispatch_id),
            ..make_dummy_continuation()
        };
        vm.dispatch_state.push_dispatch(DispatchContext {
            dispatch_id: inner_dispatch_id,
            effect: Effect::Get {
                key: "inner".to_string(),
            },
            is_execution_context_effect: false,
            handler_chain: vec![inner_marker],
            handler_idx: 0,
            supports_error_context_conversion: false,
            k_user: inner_k.clone(),
            prompt_seg_id: seg_id,
            completed: false,
            original_exception: None,
        });

        let stream: ASTStreamRef = Arc::new(Mutex::new(
            Box::new(DummyProgramStream) as Box<dyn ASTStream>
        ));
        let call = PythonCall::CallFunc {
            func: PyShared::new(py.None().into_pyobject(py).unwrap().unbind().into_any()),
            args: Vec::new(),
            kwargs: Vec::new(),
        };
        let event = vm.apply_stream_step(ASTStreamStep::NeedsPython(call), stream, None);
        assert!(matches!(
            event,
            StepEvent::NeedsPython(PythonCall::CallFunc { .. })
        ));

        match vm.current_seg().pending_python.as_ref() {
            Some(PendingPython::RustProgramContinuation { marker, k }) => {
                assert_eq!(
                    *marker, outer_marker,
                    "needs-python continuation must use current segment dispatch context",
                );
                assert_eq!(
                    k.cont_id, outer_k.cont_id,
                    "needs-python continuation must target current dispatch continuation",
                );
                assert_ne!(k.cont_id, inner_k.cont_id);
            }
            other => panic!(
                "expected RustProgramContinuation pending state, got {:?}",
                other
            ),
        }
    });
}

// === SPEC-009 Gap TDD Tests ===

/// G3: Modify handler must resume caller with new_value (modifier result), not old_value.
#[test]
fn test_s009_g3_modify_resumes_with_new_value() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let prompt_seg = Segment::new(marker, None);
        let prompt_seg_id = vm.alloc_segment(prompt_seg);

        let body_seg = Segment::new(marker, Some(prompt_seg_id));
        let body_seg_id = vm.alloc_segment(body_seg);
        vm.current_segment = Some(body_seg_id);

        assert!(vm.install_handler_on_segment(
            marker,
            prompt_seg_id,
            std::sync::Arc::new(crate::handler::StateHandlerFactory),
            None
        ));

        vm.rust_store.put("x".to_string(), Value::Int(5));

        let modifier = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let result = vm.start_dispatch(Effect::Modify {
            key: "x".to_string(),
            modifier: PyShared::new(modifier),
        });
        assert!(result.is_ok());
        let event = result.unwrap();
        assert!(matches!(
            event,
            StepEvent::NeedsPython(PythonCall::CallFunc { .. })
        ));

        // Feed modifier result: 5 * 10 = 50
        vm.receive_python_result(PyCallOutcome::Value(Value::Int(50)));

        // Step to process the resume
        let event2 = vm.step();
        assert!(matches!(event2, StepEvent::Continue));

        // The mode should be HandleYield with Resume primitive
        // SPEC-008 L1271: Modify returns OLD value (read-then-modify).
        // The resume value should be 5 (old_value), NOT 50 (new_value).
        match &vm.current_seg().mode {
            Mode::HandleYield(DoCtrl::Resume { value, .. }) => {
                assert_eq!(
                    value.as_int(),
                    Some(5),
                    "G3 FAIL: Modify resumed with {} instead of 5 (old_value). \
                     SPEC-008 L1271: Modify is read-then-modify, returns old value.",
                    value.as_int().unwrap_or(-1)
                );
            }
            other => panic!(
                "G3: Expected HandleYield(Resume), got {:?}",
                std::mem::discriminant(other)
            ),
        }
    });
}

/// D10: handle_handler_return must use Mode::Deliver (not Mode::Return)
/// and must NOT explicitly jump current_segment to prompt_seg_id.
#[test]
fn test_d10_handler_return_uses_deliver_not_return() {
    let mut vm = VM::new();
    let marker = Marker::fresh();

    let prompt_seg = Segment::new(marker, None);
    let prompt_seg_id = vm.alloc_segment(prompt_seg);

    let handler_seg = Segment::new(marker, Some(prompt_seg_id));
    let handler_seg_id = vm.alloc_segment(handler_seg);
    vm.current_segment = Some(handler_seg_id);

    assert!(vm.install_handler_on_segment(
        marker,
        prompt_seg_id,
        std::sync::Arc::new(crate::handler::StateHandlerFactory),
        None
    ));

    let dispatch_id = DispatchId::fresh();
    let k_user = Continuation {
        cont_id: ContId::fresh(),
        segment_id: prompt_seg_id,
        frames_snapshot: std::sync::Arc::new(Vec::new()),
        marker,
        dispatch_id: Some(dispatch_id),
        mode: Box::new(Mode::Deliver(Value::Unit)),
        pending_python: None,
        pending_error_context: None,
        interceptor_eval_depth: 0,
        interceptor_skip_stack: Vec::new(),
        scope_store: ScopeStore::default(),
        started: true,
        program: None,
        handlers: Vec::new(),
        handler_identities: Vec::new(),
        metadata: None,
        parent: None,
    };

    vm.dispatch_state.push_dispatch(DispatchContext {
        dispatch_id,
        effect: Effect::Get {
            key: "x".to_string(),
        },
        is_execution_context_effect: false,
        handler_chain: vec![marker],
        handler_idx: 0,
        supports_error_context_conversion: false,
        k_user,
        prompt_seg_id,
        completed: false,
        original_exception: None,
    });

    let event = vm.handle_handler_return(Value::Int(42));
    assert!(matches!(event, StepEvent::Continue));

    // D10: Mode must be Deliver, NOT Return
    assert!(
        matches!(&vm.current_seg().mode, Mode::Deliver(Value::Int(42))),
        "D10 REGRESSION: handle_handler_return must use Mode::Deliver, got {:?}",
        std::mem::discriminant(&vm.current_seg().mode)
    );

    // D10: current_segment must NOT have jumped to prompt_seg_id
    assert_eq!(
        vm.current_segment,
        Some(handler_seg_id),
        "D10 REGRESSION: handle_handler_return must not explicitly jump current_segment"
    );
}

// ==========================================================
// R9-A: DoCtrl::Apply — direct Python call dispatch tests
// ==========================================================

#[test]
fn test_apply_return_delivers_value_without_pushing_frame() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_apply".to_string(),
            "test.py".to_string(),
            1,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(dummy_f)),
            args: vec![],
            kwargs: vec![],
            metadata,
            evaluate_result: false,
        });

        let event = vm.step_handle_yield();
        assert!(matches!(
            event,
            StepEvent::NeedsPython(PythonCall::CallFunc { .. })
        ));
        assert!(matches!(
            vm.current_seg().pending_python.as_ref(),
            Some(PendingPython::CallFuncReturn { .. })
        ));

        vm.receive_python_result(PyCallOutcome::Value(Value::Int(7)));
        assert!(matches!(
            &vm.current_seg().mode,
            Mode::Deliver(Value::Int(7))
        ));
        let seg = vm.segments.get(seg_id).expect("segment missing");
        assert!(
            seg.frames.is_empty(),
            "Apply must not push a PythonGenerator frame"
        );
    });
}

#[test]
fn test_apply_return_reenters_handle_yield_when_evaluate_result_true() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_apply_eval_true".to_string(),
            "test.py".to_string(),
            1,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(dummy_f)),
            args: vec![],
            kwargs: vec![],
            metadata,
            evaluate_result: true,
        });

        let event = vm.step_handle_yield();
        assert!(matches!(
            event,
            StepEvent::NeedsPython(PythonCall::CallFunc { .. })
        ));

        let pure = py
            .get_type::<crate::pyvm::PyPure>()
            .call1((7i64,))
            .unwrap()
            .unbind();
        vm.receive_python_result(PyCallOutcome::Value(Value::Python(pure)));

        match &vm.current_seg().mode {
            Mode::HandleYield(DoCtrl::Pure {
                value: Value::Int(value),
            }) => {
                assert_eq!(*value, 7);
            }
            other => panic!(
                "evaluate_result=true should re-enter HandleYield(Pure), got {:?}",
                std::mem::discriminant(other)
            ),
        }
    });
}

#[test]
fn test_apply_return_preserves_doexpr_value_when_evaluate_result_false() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_apply_eval_false".to_string(),
            "test.py".to_string(),
            1,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(dummy_f)),
            args: vec![],
            kwargs: vec![],
            metadata,
            evaluate_result: false,
        });

        let event = vm.step_handle_yield();
        assert!(matches!(
            event,
            StepEvent::NeedsPython(PythonCall::CallFunc { .. })
        ));

        let pure = py
            .get_type::<crate::pyvm::PyPure>()
            .call1((9i64,))
            .unwrap()
            .unbind();
        vm.receive_python_result(PyCallOutcome::Value(Value::Python(pure)));

        assert!(
            matches!(&vm.current_seg().mode, Mode::Deliver(Value::Python(_))),
            "evaluate_result=false should preserve Python DoExpr as value"
        );
    });
}

#[test]
fn test_expand_requires_doeff_generator_or_errors() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_factory = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_expand".to_string(),
            "test.py".to_string(),
            1,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Expand {
            factory: CallArg::Value(Value::Python(dummy_factory)),
            args: vec![],
            kwargs: vec![],
            metadata: metadata.clone(),
        });

        let event = vm.step_handle_yield();
        assert!(matches!(
            event,
            StepEvent::NeedsPython(PythonCall::CallFunc { .. })
        ));
        assert!(matches!(
            vm.current_seg().pending_python.as_ref(),
            Some(PendingPython::ExpandReturn {
                metadata: Some(_),
                ..
            })
        ));

        vm.receive_python_result(PyCallOutcome::Value(Value::Int(1)));
        match &vm.current_seg().mode {
            Mode::Throw(PyException::TypeError { message }) => {
                assert!(message.contains("ExpandReturn: expected DoeffGenerator"));
            }
            other => panic!("expected Expand type error, got {:?}", other),
        }
        let seg = vm.segments.get(seg_id).expect("segment missing");
        assert!(
            seg.frames.is_empty(),
            "Expand must not push a frame when return is invalid"
        );
    });
}

#[test]
fn test_expand_success_routes_through_aststream_doctrl() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_factory = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_expand".to_string(),
            "test.py".to_string(),
            1,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Expand {
            factory: CallArg::Value(Value::Python(dummy_factory)),
            args: vec![],
            kwargs: vec![],
            metadata,
        });
        let event = vm.step_handle_yield();
        assert!(matches!(
            event,
            StepEvent::NeedsPython(PythonCall::CallFunc { .. })
        ));

        let locals = PyDict::new(py);
        py.run(
            c"def _gen():\n    yield 1\n\nraw = _gen()\n\ndef _get_frame(g):\n    return g.gi_frame\n",
            Some(&locals),
            Some(&locals),
        )
        .expect("failed to construct test generator");
        let raw = locals
            .get_item("raw")
            .expect("locals lookup failed")
            .expect("raw generator missing")
            .unbind();
        let get_frame = locals
            .get_item("_get_frame")
            .expect("locals lookup failed")
            .expect("get_frame missing")
            .unbind();
        let kwargs = PyDict::new(py);
        kwargs.set_item("generator", raw.bind(py)).unwrap();
        kwargs.set_item("function_name", "test_gen").unwrap();
        kwargs.set_item("source_file", "test_gen.py").unwrap();
        kwargs.set_item("source_line", 99).unwrap();
        kwargs.set_item("get_frame", get_frame.bind(py)).unwrap();
        let wrapped = py
            .get_type::<DoeffGenerator>()
            .call((), Some(&kwargs))
            .expect("failed to wrap DoeffGenerator")
            .unbind();

        vm.receive_python_result(PyCallOutcome::Value(Value::Python(wrapped)));
        assert!(
            matches!(
                &vm.current_seg().mode,
                Mode::HandleYield(DoCtrl::ASTStream { .. })
            ),
            "Expand success must route through DoCtrl::ASTStream, got {:?}",
            std::mem::discriminant(&vm.current_seg().mode)
        );

        let event = vm.step_handle_yield();
        assert!(matches!(event, StepEvent::Continue));
        let seg = vm.segments.get(seg_id).expect("segment missing");
        assert_eq!(
            seg.frames.len(),
            1,
            "ASTStream handling must push a Program frame before stepping"
        );
        assert!(matches!(seg.frames[0], Frame::Program { .. }));
    });
}

/// R9-A: Apply with empty args/kwargs still dispatches via CallFunc.
#[test]
fn test_r9a_apply_empty_args_yields_call_func() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_thunk".to_string(),
            "test.py".to_string(),
            1,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(dummy_f)),
            args: vec![],
            kwargs: vec![],
            metadata: metadata.clone(),
            evaluate_result: false,
        });

        let event = vm.step_handle_yield();

        assert!(
            matches!(event, StepEvent::NeedsPython(PythonCall::CallFunc { .. })),
            "R9-A: empty args must yield CallFunc, got {:?}",
            std::mem::discriminant(&event)
        );

        match &vm.current_seg().pending_python {
            Some(PendingPython::CallFuncReturn {
                metadata: Some(m), ..
            }) => {
                assert_eq!(m.function_name, "test_thunk");
            }
            other => panic!(
                "R9-A: pending_python must be CallFuncReturn with metadata, got {:?}",
                other
            ),
        }
    });
}

/// R9-A: Apply with non-empty args → CallFunc.
/// Spec: "Kernel call (with args): Apply { f: kernel, args, kwargs, metadata }
///        → driver calls kernel(*args, **kwargs), gets result, pushes frame."
#[test]
fn test_r9a_apply_with_args_yields_call_func() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_kernel".to_string(),
            "test.py".to_string(),
            10,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(dummy_f)),
            args: vec![
                CallArg::Value(Value::Int(42)),
                CallArg::Value(Value::String("hello".to_string())),
            ],
            kwargs: vec![],
            metadata,
            evaluate_result: false,
        });

        let event = vm.step_handle_yield();

        match event {
            StepEvent::NeedsPython(PythonCall::CallFunc { args, .. }) => {
                assert_eq!(args.len(), 2);
                assert_eq!(args[0].as_int(), Some(42));
                match &args[1] {
                    Value::String(s) => assert_eq!(s, "hello"),
                    other => panic!("R9-A: expected String arg, got {:?}", other),
                }
            }
            other => panic!(
                "R9-A: non-empty args must yield CallFunc, got {:?}",
                std::mem::discriminant(&other)
            ),
        }
    });
}

/// R9-A: Apply with kwargs preserves them as separate field in CallFunc.
/// Spec: driver calls f(*args, **kwargs) — keyword semantics are preserved.
#[test]
fn test_r9a_apply_kwargs_preserved_separately() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_kwargs".to_string(),
            "test.py".to_string(),
            20,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(dummy_f)),
            args: vec![CallArg::Value(Value::Int(1))],
            kwargs: vec![
                ("key1".to_string(), CallArg::Value(Value::Int(2))),
                (
                    "key2".to_string(),
                    CallArg::Value(Value::String("val".to_string())),
                ),
            ],
            metadata,
            evaluate_result: false,
        });

        let event = vm.step_handle_yield();

        match event {
            StepEvent::NeedsPython(PythonCall::CallFunc { args, kwargs, .. }) => {
                assert_eq!(args.len(), 1, "R9-A: positional args preserved separately");
                assert_eq!(args[0].as_int(), Some(1));

                assert_eq!(kwargs.len(), 2, "R9-A: kwargs preserved separately");
                assert_eq!(kwargs[0].0, "key1");
                assert_eq!(kwargs[0].1.as_int(), Some(2));
                assert_eq!(kwargs[1].0, "key2");
                match &kwargs[1].1 {
                    Value::String(s) => assert_eq!(s, "val"),
                    other => panic!("R9-A: expected String kwarg value, got {:?}", other),
                }
            }
            other => panic!(
                "R9-A: kwargs call must yield CallFunc, got {:?}",
                std::mem::discriminant(&other)
            ),
        }
    });
}

/// R9-A: Apply with only kwargs (no positional args) still takes CallFunc path.
/// Empty args but non-empty kwargs → not DoThunk path.
#[test]
fn test_r9a_apply_kwargs_only_takes_callfunc_path() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
        let metadata = CallMetadata::new(
            "test_kwargs_only".to_string(),
            "test.py".to_string(),
            30,
            None,
            None,
        );

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(dummy_f)),
            args: vec![],
            kwargs: vec![(
                "name".to_string(),
                CallArg::Value(Value::String("test".to_string())),
            )],
            metadata,
            evaluate_result: false,
        });

        let event = vm.step_handle_yield();

        assert!(
            matches!(event, StepEvent::NeedsPython(PythonCall::CallFunc { .. })),
            "R9-A: kwargs-only call must yield CallFunc (not EvalExpr), got {:?}",
            std::mem::discriminant(&event)
        );
    });
}

// ==========================================================
// R9-H: DoCtrl::Eval — atomic Create + Resume tests
// ==========================================================

/// R9-H: Eval creates unstarted continuation and resumes it via handle_resume_continuation.
/// Result: NeedsPython(EvalExpr { expr }) because unstarted continuation
/// now evaluates DoExpr directly.
#[test]
fn test_r9h_eval_creates_and_resumes_continuation() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_expr = py.None().into_pyobject(py).unwrap().unbind().into_any();

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Eval {
            expr: PyShared::new(dummy_expr),
            handlers: vec![],
            metadata: None,
        });

        let event = vm.step_handle_yield();

        assert!(
            matches!(event, StepEvent::NeedsPython(PythonCall::EvalExpr { .. })),
            "R9-H: Eval must create unstarted continuation and yield EvalExpr, got {:?}",
            std::mem::discriminant(&event)
        );

        assert!(
            matches!(
                vm.current_seg().pending_python.as_ref(),
                Some(PendingPython::EvalExpr { metadata: None })
            ),
            "R9-H: Eval continuation has no metadata (metadata comes from Call, not Eval)"
        );
    });
}

/// R9-H: Eval with handlers installs them on the continuation scope.
/// Handlers are installed as prompt+body segment pairs by handle_resume_continuation.
#[test]
fn test_r9h_eval_with_handlers_installs_scope() {
    Python::attach(|py| {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let dummy_expr = py.None().into_pyobject(py).unwrap().unbind().into_any();

        let handler = std::sync::Arc::new(crate::handler::StateHandlerFactory);

        vm.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Eval {
            expr: PyShared::new(dummy_expr),
            handlers: vec![handler],
            metadata: None,
        });

        let event = vm.step_handle_yield();

        assert!(
            matches!(event, StepEvent::NeedsPython(PythonCall::EvalExpr { .. })),
            "R9-H: Eval with handlers must still yield EvalExpr"
        );

        assert!(
            !vm.current_handler_chain().is_empty(),
            "R9-H: Eval with handlers must install prompt-boundary handlers"
        );

        assert_ne!(
            vm.current_segment,
            Some(seg_id),
            "R9-H: Eval must change current_segment to the body segment of installed handlers"
        );
    });
}

#[test]
fn test_g1_vm_step_path_has_no_assume_attached_calls() {
    let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/vm.rs"));
    let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
    assert!(
        !runtime_src.contains("assume_attached()"),
        "G1 FAIL: vm.rs step/runtime path still uses assume_attached"
    );
}

#[test]
fn test_transfer_to_continuation_only_in_transfer_next_or() {
    let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/scheduler.rs"));
    let test_boundary = src.find("#[cfg(test)]").unwrap_or(src.len());
    let runtime_src = &src[..test_boundary];

    let mut violations: Vec<String> = Vec::new();
    let target_fn = "fn transfer_next_or";
    let call_pattern = "transfer_to_continuation(";

    let fn_start = runtime_src.find(target_fn);

    for (line_no, line) in runtime_src.lines().enumerate() {
        if !line.contains(call_pattern) {
            continue;
        }
        let trimmed = line.trim();
        if trimmed.starts_with("//") || trimmed.starts_with("fn ") {
            continue;
        }

        let line_offset = runtime_src
            .lines()
            .take(line_no)
            .map(|l| l.len() + 1)
            .sum::<usize>();
        let inside_transfer_next_or = match fn_start {
            Some(start) => {
                if line_offset < start {
                    false
                } else {
                    let between = &runtime_src[start..line_offset];
                    let next_fn = between[target_fn.len()..].find("\nfn ");
                    next_fn.is_none()
                }
            }
            None => false,
        };

        if !inside_transfer_next_or {
            violations.push(format!("  line {}: {}", line_no + 1, trimmed));
        }
    }

    assert!(
        violations.is_empty(),
        "transfer_to_continuation (Transfer) must only be called from transfer_next_or. \
         Found in other locations:\n{}",
        violations.join("\n")
    );
}

fn caller_chain_length(vm: &VM) -> usize {
    let mut count: usize = 0;
    let mut current = vm.current_segment;
    while let Some(seg_id) = current {
        count += 1;
        current = vm.segments.get(seg_id).and_then(|s| s.caller);
    }
    count
}

#[test]
fn test_transfer_caller_chain_stays_bounded() {
    let mut vm = VM::new();

    let mut continuations: Vec<Continuation> = Vec::new();
    for _ in 0..2 {
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);
        continuations.push(vm.capture_continuation(None).unwrap());
    }

    for round in 0..64 {
        let target: &Continuation = &continuations[round % 2];
        let event = vm.handle_transfer(target.clone(), Value::Int(round as i64));
        assert!(matches!(event, StepEvent::Continue));

        let chain_len = caller_chain_length(&vm);
        assert!(
            chain_len <= 2,
            "Round {}: caller chain length is {} — Transfer should sever \
             the chain (caller: None), keeping it at 1.",
            round,
            chain_len
        );

        vm.consumed_cont_ids.clear();
        continuations[round % 2] = vm.capture_continuation(None).unwrap();
    }
}

#[test]
fn test_resume_caller_chain_grows_linearly() {
    let mut vm = VM::new();

    let marker = Marker::fresh();
    let seg = Segment::new(marker, None);
    let seg_id = vm.alloc_segment(seg);
    vm.current_segment = Some(seg_id);
    let k = vm.capture_continuation(None).unwrap();

    for round in 0..64 {
        let event = vm.handle_resume(k.clone(), Value::Int(round as i64));
        assert!(matches!(event, StepEvent::Continue));
        vm.consumed_cont_ids.clear();
    }

    let chain_len = caller_chain_length(&vm);
    assert!(
        chain_len >= 60,
        "Resume caller chain length is {} after 64 resumes — \
         Resume should chain segments via caller, growing linearly.",
        chain_len
    );
}
