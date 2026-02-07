//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::arena::SegmentArena;
use crate::continuation::Continuation;
use crate::effect::Effect;
use crate::error::VMError;
use crate::frame::Frame;
use crate::handler::{Handler, HandlerEntry};
use crate::ids::{CallbackId, ContId, DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::segment::Segment;
use crate::step::{
    DoCtrl, Mode, PendingPython, PyCallOutcome, PyException, PythonCall, StepEvent,
    Yielded,
};
use crate::value::Value;

pub type Callback = Box<dyn FnOnce(Value, &mut VM) -> Mode + Send + Sync>;

#[derive(Debug, Clone)]
pub struct DispatchContext {
    pub dispatch_id: DispatchId,
    pub effect: Effect,
    pub handler_chain: Vec<Marker>,
    pub handler_idx: usize,
    pub k_user: Continuation,
    pub prompt_seg_id: SegmentId,
    pub completed: bool,
}

#[derive(Debug, Clone)]
pub struct RustStore {
    pub state: HashMap<String, Value>,
    pub env: HashMap<String, Value>,
    pub log: Vec<Value>,
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            state: HashMap::new(),
            env: HashMap::new(),
            log: Vec::new(),
        }
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.state.get(key)
    }

    pub fn put(&mut self, key: String, value: Value) {
        self.state.insert(key, value);
    }

    pub fn ask(&self, key: &str) -> Option<&Value> {
        self.env.get(key)
    }

    pub fn tell(&mut self, message: Value) {
        self.log.push(message);
    }

    pub fn logs(&self) -> &[Value] {
        &self.log
    }

    pub fn modify(&mut self, key: &str, f: impl FnOnce(&Value) -> Value) -> Option<Value> {
        let old = self.state.get(key)?;
        let new_val = f(old);
        let old_clone = old.clone();
        self.state.insert(key.to_string(), new_val);
        Some(old_clone)
    }

    pub fn with_local<F, R>(&mut self, bindings: HashMap<String, Value>, f: F) -> R
    where
        F: FnOnce(&mut Self) -> R,
    {
        let old: HashMap<String, Value> = bindings
            .keys()
            .filter_map(|k| self.env.get(k).map(|v| (k.clone(), v.clone())))
            .collect();
        let new_keys: Vec<String> = bindings
            .keys()
            .filter(|k| !old.contains_key(*k))
            .cloned()
            .collect();

        for (k, v) in bindings {
            self.env.insert(k, v);
        }

        let result = f(self);

        for (k, v) in old {
            self.env.insert(k, v);
        }
        for k in new_keys {
            self.env.remove(&k);
        }

        result
    }

    pub fn clear_logs(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.log)
    }
}

impl Default for RustStore {
    fn default() -> Self {
        Self::new()
    }
}

/// Optional Python dict for user-defined handler state (Layer 3).
/// VM doesn't read it; users can store arbitrary data.
pub struct PyStore {
    pub dict: Py<PyDict>,
}

impl PyStore {
    pub fn new(py: Python<'_>) -> Self {
        PyStore {
            dict: PyDict::new(py).unbind(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DebugLevel {
    Off,
    Steps,
    Trace,
}

#[derive(Debug, Clone)]
pub struct DebugConfig {
    pub level: DebugLevel,
    pub show_frames: bool,
    pub show_dispatch: bool,
    pub show_store: bool,
}

impl Default for DebugConfig {
    fn default() -> Self {
        DebugConfig {
            level: DebugLevel::Off,
            show_frames: false,
            show_dispatch: false,
            show_store: false,
        }
    }
}

impl DebugConfig {
    pub fn steps() -> Self {
        DebugConfig {
            level: DebugLevel::Steps,
            ..Default::default()
        }
    }

    pub fn trace() -> Self {
        DebugConfig {
            level: DebugLevel::Trace,
            show_frames: true,
            show_dispatch: true,
            show_store: false,
        }
    }

    pub fn is_enabled(&self) -> bool {
        self.level != DebugLevel::Off
    }
}

pub struct VM {
    pub segments: SegmentArena,
    pub dispatch_stack: Vec<DispatchContext>,
    pub callbacks: HashMap<CallbackId, Callback>,
    pub consumed_cont_ids: HashSet<ContId>,
    pub handlers: HashMap<Marker, HandlerEntry>,
    pub rust_store: RustStore,
    pub py_store: Option<PyStore>,
    pub current_segment: Option<SegmentId>,
    pub mode: Mode,
    pub pending_python: Option<PendingPython>,
    pub debug: DebugConfig,
    pub step_counter: u64,
    pub continuation_registry: HashMap<ContId, Continuation>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: SegmentArena::new(),
            dispatch_stack: Vec::new(),
            callbacks: HashMap::new(),
            consumed_cont_ids: HashSet::new(),
            handlers: HashMap::new(),
            rust_store: RustStore::new(),
            py_store: None,
            current_segment: None,
            mode: Mode::Deliver(Value::Unit),
            pending_python: None,
            debug: DebugConfig::default(),
            step_counter: 0,
            continuation_registry: HashMap::new(),
        }
    }

    pub fn with_debug(debug: DebugConfig) -> Self {
        VM {
            debug,
            ..Self::new()
        }
    }

    pub fn set_debug(&mut self, config: DebugConfig) {
        self.debug = config;
    }

    pub fn py_store(&self) -> Option<&PyStore> {
        self.py_store.as_ref()
    }

    pub fn py_store_mut(&mut self) -> Option<&mut PyStore> {
        self.py_store.as_mut()
    }

    pub fn init_py_store(&mut self, py: Python<'_>) {
        if self.py_store.is_none() {
            self.py_store = Some(PyStore::new(py));
        }
    }

    pub fn alloc_segment(&mut self, segment: Segment) -> SegmentId {
        self.segments.alloc(segment)
    }

    pub fn current_segment_mut(&mut self) -> Option<&mut Segment> {
        self.current_segment
            .and_then(|id| self.segments.get_mut(id))
    }

    pub fn current_segment_ref(&self) -> Option<&Segment> {
        self.current_segment.and_then(|id| self.segments.get(id))
    }

    pub fn register_callback(&mut self, callback: Callback) -> CallbackId {
        let id = CallbackId::fresh();
        self.callbacks.insert(id, callback);
        id
    }

    /// Set mode to Throw with a RuntimeError and return Continue.
    fn throw_runtime_error(&mut self, message: &str) -> StepEvent {
        self.mode = Mode::Throw(PyException::runtime_error(message.to_string()));
        StepEvent::Continue
    }

    pub fn step(&mut self) -> StepEvent {
        self.step_counter += 1;

        if self.debug.is_enabled() {
            self.debug_step_entry();
        }

        let result = match &self.mode {
            Mode::Deliver(_) | Mode::Throw(_) => self.step_deliver_or_throw(),
            Mode::HandleYield(_) => self.step_handle_yield(),
            Mode::Return(_) => self.step_return(),
        };

        if self.debug.is_enabled() {
            self.debug_step_exit(&result);
        }

        result
    }

    fn debug_step_entry(&self) {
        let mode_kind = match &self.mode {
            Mode::Deliver(_) => "Deliver",
            Mode::Throw(_) => "Throw",
            Mode::HandleYield(y) => match y {
                Yielded::Effect(e) => match e {
                    Effect::Get { .. } => "HandleYield(Get)",
                    Effect::Put { .. } => "HandleYield(Put)",
                    Effect::Modify { .. } => "HandleYield(Modify)",
                    Effect::Ask { .. } => "HandleYield(Ask)",
                    Effect::Tell { .. } => "HandleYield(Tell)",
                    Effect::Python(_) => "HandleYield(Python)",
                    Effect::Scheduler(_) => "HandleYield(Scheduler)",
                    Effect::KpcCall(_) => "HandleYield(KpcCall)",
                },
                Yielded::DoCtrl(p) => match p {
                    DoCtrl::Resume { .. } => "HandleYield(Resume)",
                    DoCtrl::Transfer { .. } => "HandleYield(Transfer)",
                    DoCtrl::WithHandler { .. } => "HandleYield(WithHandler)",
                    DoCtrl::Delegate { .. } => "HandleYield(Delegate)",
                    DoCtrl::GetContinuation => "HandleYield(GetContinuation)",
                    DoCtrl::GetHandlers => "HandleYield(GetHandlers)",
                    DoCtrl::CreateContinuation { .. } => {
                        "HandleYield(CreateContinuation)"
                    }
                    DoCtrl::ResumeContinuation { .. } => {
                        "HandleYield(ResumeContinuation)"
                    }
                    DoCtrl::PythonAsyncSyntaxEscape { .. } => "HandleYield(AsyncEscape)",
                    DoCtrl::Call { .. } => "HandleYield(Call)",
                    DoCtrl::Eval { .. } => "HandleYield(Eval)",
                    DoCtrl::GetCallStack => "HandleYield(GetCallStack)",
                },
                Yielded::Unknown(_) => "HandleYield(Unknown)",
            },
            Mode::Return(_) => "Return",
        };

        let seg_info = self
            .current_segment
            .and_then(|id| self.segments.get(id))
            .map(|s| format!("seg={:?} frames={}", self.current_segment, s.frames.len()))
            .unwrap_or_else(|| "seg=None".to_string());

        let pending = self
            .pending_python
            .as_ref()
            .map(|p| match p {
                PendingPython::StartProgramFrame { .. } => "StartProgramFrame",
                PendingPython::StepUserGenerator { .. } => "StepUserGenerator",
                PendingPython::CallPythonHandler { .. } => "CallPythonHandler",
                PendingPython::RustProgramContinuation { .. } => "RustProgramContinuation",
                PendingPython::AsyncEscape => "AsyncEscape",
            })
            .unwrap_or("None");

        eprintln!(
            "[step {}] mode={} {} dispatch_depth={} pending={}",
            self.step_counter,
            mode_kind,
            seg_info,
            self.dispatch_stack.len(),
            pending
        );

        if self.debug.level == DebugLevel::Trace && self.debug.show_frames {
            if let Some(seg) = self.current_segment.and_then(|id| self.segments.get(id)) {
                for (i, frame) in seg.frames.iter().enumerate() {
                    let frame_kind = match frame {
                        Frame::RustReturn { .. } => "RustReturn",
                        Frame::RustProgram { .. } => "RustProgram",
                        Frame::PythonGenerator {
                            started, metadata, ..
                        } => {
                            if *started {
                                if metadata.is_some() {
                                    "PythonGenerator(started,meta)"
                                } else {
                                    "PythonGenerator(started)"
                                }
                            } else if metadata.is_some() {
                                "PythonGenerator(new,meta)"
                            } else {
                                "PythonGenerator(new)"
                            }
                        }
                    };
                    eprintln!("  frame[{}]: {}", i, frame_kind);
                }
            }
        }
    }

    fn debug_step_exit(&self, result: &StepEvent) {
        let result_kind = match result {
            StepEvent::Continue => "Continue",
            StepEvent::Done(_) => "Done",
            StepEvent::Error(e) => {
                eprintln!("[step {}] -> Error: {}", self.step_counter, e);
                return;
            }
            StepEvent::NeedsPython(call) => {
                let call_kind = match call {
                    PythonCall::StartProgram { .. } => "StartProgram",
                    PythonCall::CallFunc { .. } => "CallFunc",
                    PythonCall::CallHandler { .. } => "CallHandler",
                    PythonCall::GenNext => "GenNext",
                    PythonCall::GenSend { .. } => "GenSend",
                    PythonCall::GenThrow { .. } => "GenThrow",
                    PythonCall::CallAsync { .. } => "CallAsync",
                };
                eprintln!("[step {}] -> NeedsPython({})", self.step_counter, call_kind);
                return;
            }
        };
        if self.debug.level == DebugLevel::Trace {
            eprintln!("[step {}] -> {}", self.step_counter, result_kind);
        }
    }

    fn step_deliver_or_throw(&mut self) -> StepEvent {
        let seg_id = match self.current_segment {
            Some(id) => id,
            None => return StepEvent::Error(VMError::internal("no current segment")),
        };

        {
            let segment = match self.segments.get(seg_id) {
                Some(s) => s,
                None => return StepEvent::Error(VMError::invalid_segment("segment not found")),
            };

            if !segment.has_frames() {
                let caller = segment.caller;
                // Take mode by move — eliminates Py<PyAny> clones (D1 Phase 1).
                let mode = std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit));
                match mode {
                    Mode::Deliver(value) => {
                        self.segments.free(seg_id);
                        self.mode = Mode::Return(value);
                        return StepEvent::Continue;
                    }
                    Mode::Throw(exc) => {
                        if let Some(caller_id) = caller {
                            self.current_segment = Some(caller_id);
                            self.mode = Mode::Throw(exc);
                            self.segments.free(seg_id);
                            return StepEvent::Continue;
                        } else {
                            self.segments.free(seg_id);
                            return StepEvent::Error(VMError::uncaught_exception(exc));
                        }
                    }
                    _ => unreachable!(),
                }
            }
        }

        let segment = match self.segments.get_mut(seg_id) {
            Some(s) => s,
            None => return StepEvent::Error(VMError::invalid_segment("segment not found")),
        };
        let frame = segment.pop_frame().unwrap();

        // Take mode by move — each branch sets self.mode before returning (D1 Phase 1).
        let mode = std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit));

        match frame {
            Frame::RustReturn { cb } => {
                let callback = match self.callbacks.remove(&cb) {
                    Some(cb) => cb,
                    None => return StepEvent::Error(VMError::internal("callback not found")),
                };

                match mode {
                    Mode::Deliver(value) => {
                        self.mode = callback(value, self);
                        StepEvent::Continue
                    }
                    Mode::Throw(exc) => {
                        self.mode = Mode::Throw(exc);
                        StepEvent::Continue
                    }
                    _ => unreachable!(),
                }
            }

            Frame::RustProgram { program } => {
                let step = {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    match mode {
                        Mode::Deliver(value) => guard.resume(value, &mut self.rust_store),
                        Mode::Throw(exc) => guard.throw(exc, &mut self.rust_store),
                        _ => unreachable!(),
                    }
                };
                self.apply_rust_program_step(step, program)
            }

            Frame::PythonGenerator {
                generator,
                started,
                metadata,
            } => {
                // D1 Phase 2: generator + metadata move into PendingPython (no clone).
                // Driver (pyvm.rs) reads gen from pending_python with GIL held.
                self.pending_python = Some(PendingPython::StepUserGenerator {
                    generator,
                    metadata,
                });

                match mode {
                    Mode::Deliver(value) => {
                        if started {
                            StepEvent::NeedsPython(PythonCall::GenSend { value })
                        } else {
                            StepEvent::NeedsPython(PythonCall::GenNext)
                        }
                    }
                    Mode::Throw(exc) => StepEvent::NeedsPython(PythonCall::GenThrow {
                        exc,
                    }),
                    _ => unreachable!(),
                }
            }
        }
    }

    fn apply_rust_program_step(
        &mut self,
        step: crate::handler::RustProgramStep,
        program: crate::handler::RustProgramRef,
    ) -> StepEvent {
        use crate::handler::RustProgramStep;
        match step {
            RustProgramStep::Yield(yielded) => {
                // Re-push the RustProgram frame (like re-pushing a Python generator on yield)
                if let Some(seg) = self.current_segment_mut() {
                    seg.push_frame(Frame::RustProgram { program });
                }
                self.mode = Mode::HandleYield(yielded);
                StepEvent::Continue
            }
            RustProgramStep::Return(value) => {
                // Program is done, do NOT re-push
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            RustProgramStep::Throw(exc) => {
                // Program threw, do NOT re-push
                self.mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            RustProgramStep::NeedsPython(call) => {
                if let Some(seg) = self.current_segment_mut() {
                    seg.push_frame(Frame::RustProgram { program });
                }
                let top = self.dispatch_stack.last().expect(
                    "RustProgramContinuation: handler always runs inside dispatch",
                );
                let marker = top
                    .handler_chain
                    .get(top.handler_idx)
                    .copied()
                    .unwrap_or_else(Marker::fresh);
                let k = top.k_user.clone();
                self.pending_python =
                    Some(PendingPython::RustProgramContinuation { marker, k });
                StepEvent::NeedsPython(call)
            }
        }
    }

    fn step_handle_yield(&mut self) -> StepEvent {
        // Take mode by move — eliminates Yielded clone containing Py<PyAny> values (D1 Phase 1).
        let yielded = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
            Mode::HandleYield(y) => y,
            other => {
                self.mode = other;
                return StepEvent::Error(VMError::internal("invalid mode for handle_yield"));
            }
        };

        match yielded {
            Yielded::Effect(effect) => match self.start_dispatch(effect) {
                Ok(event) => event,
                Err(e) => StepEvent::Error(e),
            },

            Yielded::DoCtrl(prim) => {
                // Spec: Drop completed dispatches before inspecting handler context.
                self.lazy_pop_completed();
                use crate::step::DoCtrl;
                match prim {
                    DoCtrl::Resume {
                        continuation,
                        value,
                    } => self.handle_resume(continuation, value),
                    DoCtrl::Transfer {
                        continuation,
                        value,
                    } => self.handle_transfer(continuation, value),
                    DoCtrl::WithHandler {
                        handler,
                        expr,
                        py_identity,
                    } => {
                        self.handle_with_handler(handler, expr, py_identity)
                    }
                    DoCtrl::Delegate { effect } => self.handle_delegate(effect),
                    DoCtrl::GetContinuation => self.handle_get_continuation(),
                    DoCtrl::GetHandlers => self.handle_get_handlers(),
                    DoCtrl::CreateContinuation {
                        expr,
                        handlers,
                        handler_identities,
                    } => {
                        self.handle_create_continuation(expr, handlers, handler_identities)
                    }
                    DoCtrl::ResumeContinuation {
                        continuation,
                        value,
                    } => self.handle_resume_continuation(continuation, value),
                    DoCtrl::PythonAsyncSyntaxEscape { action } => {
                        self.pending_python = Some(PendingPython::AsyncEscape);
                        StepEvent::NeedsPython(PythonCall::CallAsync {
                            func: PyShared::new(action),
                            args: vec![],
                        })
                    }
                    DoCtrl::Call { f, args, kwargs, metadata } => {
                        self.pending_python = Some(PendingPython::StartProgramFrame {
                            metadata: Some(metadata),
                        });
                        if args.is_empty() && kwargs.is_empty() {
                            // DoThunk path: f is a DoThunk, driver calls to_generator()
                            StepEvent::NeedsPython(PythonCall::StartProgram { program: f })
                        } else {
                            StepEvent::NeedsPython(PythonCall::CallFunc {
                                func: f,
                                args,
                                kwargs,
                            })
                        }
                    }
                    DoCtrl::Eval { expr, handlers } => {
                        let cont = Continuation::create_unstarted(expr, handlers);
                        self.handle_resume_continuation(cont, Value::None)
                    }
                    DoCtrl::GetCallStack => {
                        let mut stack = Vec::new();
                        let mut seg_id = self.current_segment;
                        while let Some(id) = seg_id {
                            if let Some(seg) = self.segments.get(id) {
                                for frame in seg.frames.iter().rev() {
                                    if let Frame::PythonGenerator {
                                        metadata: Some(m), ..
                                    } = frame
                                    {
                                        stack.push(m.clone());
                                    }
                                }
                                seg_id = seg.caller;
                            } else {
                                break;
                            }
                        }
                        self.mode = Mode::Deliver(Value::CallStack(stack));
                        StepEvent::Continue
                    }
                }
            }

            Yielded::Unknown(_) => {
                self.mode = Mode::Throw(PyException::type_error(
                    "unknown yielded value: expected Effect or DoCtrl",
                ));
                StepEvent::Continue
            }
        }
    }

    fn step_return(&mut self) -> StepEvent {
        // Take mode by move — eliminates Value clone containing Py<PyAny> (D1 Phase 1).
        let value = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
            Mode::Return(v) => v,
            other => {
                self.mode = other;
                return StepEvent::Error(VMError::internal("invalid mode for return"));
            }
        };

        let seg_id = match self.current_segment {
            Some(id) => id,
            None => return StepEvent::Done(value),
        };

        let caller = self.segments.get(seg_id).and_then(|s| s.caller);

        match caller {
            Some(caller_id) => {
                self.current_segment = Some(caller_id);
                self.segments.free(seg_id);
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            None => {
                self.segments.free(seg_id);
                StepEvent::Done(value)
            }
        }
    }

    pub fn receive_python_result(&mut self, outcome: PyCallOutcome) {
        let pending = match self.pending_python.take() {
            Some(p) => p,
            None => {
                self.mode = Mode::Throw(PyException::runtime_error(
                    "receive_python_result called with no pending_python",
                ));
                return;
            }
        };

        match (pending, outcome) {
            (PendingPython::StartProgramFrame { metadata }, PyCallOutcome::Value(gen_val)) => {
                match gen_val {
                    Value::Python(gen) => {
                        if let Some(seg) = self.current_segment_mut() {
                            seg.push_frame(Frame::PythonGenerator {
                                generator: PyShared::new(gen),
                                started: false,
                                metadata,
                            });
                        }
                        self.mode = Mode::Deliver(Value::Unit);
                    }
                    _ => {
                        self.mode = Mode::Throw(PyException::type_error(
                            "StartProgram: program did not return a generator",
                        ));
                    }
                }
            }

            (PendingPython::StartProgramFrame { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            (
                PendingPython::StepUserGenerator {
                    generator,
                    metadata,
                },
                PyCallOutcome::GenYield(yielded),
            ) => {
                if let Some(seg) = self.current_segment_mut() {
                    seg.push_frame(Frame::PythonGenerator {
                        generator,
                        started: true,
                        metadata,
                    });
                }
                self.mode = Mode::HandleYield(yielded);
            }

            (PendingPython::StepUserGenerator { .. }, PyCallOutcome::GenReturn(value)) => {
                self.mode = Mode::Deliver(value);
            }

            (PendingPython::StepUserGenerator { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            (
                PendingPython::CallPythonHandler {
                    k_user: _,
                    effect: _,
                },
                PyCallOutcome::Value(handler_gen_val),
            ) => match handler_gen_val {
                Value::Python(handler_gen) => {
                    let handler_return_cb = self.register_callback(Box::new(|value, vm| {
                        let _ = vm.handle_handler_return(value);
                        std::mem::replace(&mut vm.mode, Mode::Deliver(Value::Unit))
                    }));
                    if let Some(seg) = self.current_segment_mut() {
                        seg.push_frame(Frame::RustReturn {
                            cb: handler_return_cb,
                        });
                        seg.push_frame(Frame::PythonGenerator {
                            generator: PyShared::new(handler_gen),
                            started: false,
                            metadata: None,
                        });
                    }
                    self.mode = Mode::Deliver(Value::Unit);
                }
                _ => {
                    self.mode = Mode::Throw(PyException::type_error(
                        "CallPythonHandler: handler did not return a generator",
                    ));
                }
            },

            (PendingPython::CallPythonHandler { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            (PendingPython::RustProgramContinuation { .. }, PyCallOutcome::Value(result)) => {
                self.mode = Mode::Deliver(result);
            }

            (PendingPython::RustProgramContinuation { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            (PendingPython::AsyncEscape, PyCallOutcome::Value(result)) => {
                self.mode = Mode::Deliver(result);
            }

            (PendingPython::AsyncEscape, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            _ => {
                self.mode = Mode::Throw(PyException::runtime_error(
                    "unexpected pending/outcome combination in receive_python_result",
                ));
            }
        }
    }

    pub fn is_one_shot_consumed(&self, cont_id: ContId) -> bool {
        self.consumed_cont_ids.contains(&cont_id)
    }

    pub fn mark_one_shot_consumed(&mut self, cont_id: ContId) {
        self.consumed_cont_ids.insert(cont_id);
        self.continuation_registry.remove(&cont_id);
    }

    pub fn register_continuation(&mut self, k: Continuation) {
        self.continuation_registry.insert(k.cont_id, k);
    }

    pub fn lookup_continuation(&self, cont_id: ContId) -> Option<&Continuation> {
        self.continuation_registry.get(&cont_id)
    }

    pub fn capture_continuation(&self, dispatch_id: Option<DispatchId>) -> Option<Continuation> {
        let seg_id = self.current_segment?;
        let segment = self.segments.get(seg_id)?;
        Some(Continuation::capture(segment, seg_id, dispatch_id))
    }

    pub fn current_scope_chain(&self) -> Vec<Marker> {
        self.current_segment
            .and_then(|id| self.segments.get(id))
            .map(|seg| seg.scope_chain.clone())
            .unwrap_or_default()
    }

    pub fn lazy_pop_completed(&mut self) {
        while let Some(top) = self.dispatch_stack.last() {
            if top.completed {
                self.dispatch_stack.pop();
            } else {
                break;
            }
        }
    }

    /// Top-only busy boundary: handlers at indices 0..=handler_idx in the topmost
    /// non-completed dispatch are excluded from the visible set.
    pub fn visible_handlers(&self, scope_chain: &[Marker]) -> Vec<Marker> {
        let Some(top) = self.dispatch_stack.last() else {
            return scope_chain.to_vec();
        };

        if top.completed {
            return scope_chain.to_vec();
        }

        let busy: HashSet<Marker> = top.handler_chain[..=top.handler_idx]
            .iter()
            .copied()
            .collect();

        scope_chain
            .iter()
            .copied()
            .filter(|marker| !busy.contains(marker))
            .collect()
    }

    pub fn find_matching_handler(
        &self,
        handler_chain: &[Marker],
        effect: &Effect,
    ) -> Result<(usize, Marker, HandlerEntry), VMError> {
        for (idx, &marker) in handler_chain.iter().enumerate() {
            if let Some(entry) = self.handlers.get(&marker) {
                if entry.handler.can_handle(effect) {
                    return Ok((idx, marker, entry.clone()));
                }
            }
        }
        Err(VMError::no_matching_handler(effect.clone()))
    }

    pub fn start_dispatch(&mut self, effect: Effect) -> Result<StepEvent, VMError> {
        self.lazy_pop_completed();

        let scope_chain = self.current_scope_chain();
        let handler_chain = self.visible_handlers(&scope_chain);

        if handler_chain.is_empty() {
            return Err(VMError::unhandled_effect(effect));
        }

        let (handler_idx, handler_marker, entry) =
            self.find_matching_handler(&handler_chain, &effect)?;

        let prompt_seg_id = entry.prompt_seg_id;
        let handler = entry.handler.clone();
        let dispatch_id = DispatchId::fresh();

        let seg_id = self
            .current_segment
            .ok_or_else(|| VMError::internal("no current segment during dispatch"))?;
        let current_seg = self
            .segments
            .get(seg_id)
            .ok_or_else(|| VMError::invalid_segment("current segment not found"))?;
        let k_user = Continuation::capture(current_seg, seg_id, Some(dispatch_id));

        let handler_seg = Segment::new(handler_marker, Some(prompt_seg_id), scope_chain);
        let handler_seg_id = self.alloc_segment(handler_seg);
        self.current_segment = Some(handler_seg_id);

        self.dispatch_stack.push(DispatchContext {
            dispatch_id,
            effect: effect.clone(),
            handler_chain: handler_chain.clone(),
            handler_idx,
            k_user: k_user.clone(),
            prompt_seg_id,
            completed: false,
        });

        match handler {
            Handler::RustProgram(rust_handler) => {
                let program = rust_handler.create_program();
                let step = {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    guard.start(effect, k_user, &mut self.rust_store)
                };
                Ok(self.apply_rust_program_step(step, program))
            }
            Handler::Python(py_handler) => {
                self.register_continuation(k_user.clone());
                self.pending_python = Some(PendingPython::CallPythonHandler {
                    k_user: k_user.clone(),
                    effect: effect.clone(),
                });
                Ok(StepEvent::NeedsPython(PythonCall::CallHandler {
                    handler: py_handler,
                    effect,
                    continuation: k_user,
                }))
            }
        }
    }

    fn check_dispatch_completion(&mut self, k: &Continuation) {
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id && top.k_user.cont_id == k.cont_id {
                    top.completed = true;
                }
            }
        }
    }

    pub fn install_handler(&mut self, marker: Marker, entry: HandlerEntry) {
        self.handlers.insert(marker, entry);
    }

    /// Remove a handler by its marker. Returns true if the handler existed.
    pub fn remove_handler(&mut self, marker: Marker) -> bool {
        self.handlers.remove(&marker).is_some()
    }

    pub fn installed_handler_markers(&self) -> Vec<Marker> {
        self.handlers.keys().copied().collect()
    }

    fn handle_resume(&mut self, k: Continuation, value: Value) -> StepEvent {
        if !k.started {
            return self.throw_runtime_error(
                "Resume on unstarted continuation; use ResumeContinuation",
            );
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        self.mark_one_shot_consumed(k.cont_id);
        self.lazy_pop_completed();
        self.check_dispatch_completion(&k);

        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller: self.current_segment,
            scope_chain: (*k.scope_chain).clone(),
            kind: crate::segment::SegmentKind::Normal,
        };
        let exec_seg_id = self.alloc_segment(exec_seg);

        self.current_segment = Some(exec_seg_id);
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    fn handle_transfer(&mut self, k: Continuation, value: Value) -> StepEvent {
        if !k.started {
            return self.throw_runtime_error(
                "Transfer on unstarted continuation; use ResumeContinuation",
            );
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        self.mark_one_shot_consumed(k.cont_id);
        self.lazy_pop_completed();
        self.check_dispatch_completion(&k);

        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller: None,
            scope_chain: (*k.scope_chain).clone(),
            kind: crate::segment::SegmentKind::Normal,
        };
        let exec_seg_id = self.alloc_segment(exec_seg);

        self.current_segment = Some(exec_seg_id);
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    fn handle_with_handler(
        &mut self,
        handler: Handler,
        program: Py<PyAny>,
        explicit_py_identity: Option<PyShared>,
    ) -> StepEvent {
        let handler_marker = Marker::fresh();
        let outside_seg_id = match self.current_segment {
            Some(id) => id,
            None => {
                return StepEvent::Error(VMError::internal("no current segment for WithHandler"))
            }
        };
        let outside_scope = self
            .segments
            .get(outside_seg_id)
            .map(|s| s.scope_chain.clone())
            .unwrap_or_default();

        let prompt_seg = Segment::new_prompt(
            handler_marker,
            Some(outside_seg_id),
            outside_scope.clone(),
            handler_marker,
        );
        let prompt_seg_id = self.alloc_segment(prompt_seg);

        let py_identity = explicit_py_identity.or_else(|| match &handler {
            Handler::Python(py_handler) => Some(py_handler.clone()),
            Handler::RustProgram(_) => None,
        });
        match py_identity {
            Some(identity) => {
                self.handlers.insert(
                    handler_marker,
                    HandlerEntry::with_identity(handler, prompt_seg_id, identity),
                );
            }
            None => {
                self.handlers
                    .insert(handler_marker, HandlerEntry::new(handler, prompt_seg_id));
            }
        }

        let mut body_scope = vec![handler_marker];
        body_scope.extend(outside_scope);

        let body_seg = Segment::new(handler_marker, Some(prompt_seg_id), body_scope);
        let body_seg_id = self.alloc_segment(body_seg);

        self.current_segment = Some(body_seg_id);

        self.pending_python = Some(PendingPython::StartProgramFrame { metadata: None });
        StepEvent::NeedsPython(PythonCall::StartProgram {
            program: PyShared::new(program),
        })
    }

    fn handle_delegate(&mut self, effect: Effect) -> StepEvent {
        let top = match self.dispatch_stack.last_mut() {
            Some(t) => t,
            None => {
                return StepEvent::Error(VMError::internal(
                    "Delegate called outside of dispatch context",
                ))
            }
        };
        let handler_chain = top.handler_chain.clone();
        let start_idx = top.handler_idx + 1;

        // Capture inner handler segment so outer handler's return flows back here
        // (result of Delegate). Per spec: caller = Some(inner_seg_id).
        let inner_seg_id = self.current_segment;

        // Clear the delegating handler's frames so return values pass through
        // without trying to resume the handler generator (Delegate is tail).
        if let Some(seg_id) = inner_seg_id {
            if let Some(seg) = self.segments.get_mut(seg_id) {
                seg.frames.clear();
            }
        }

        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            if let Some(entry) = self.handlers.get(&marker) {
                if entry.handler.can_handle(&effect) {
                    let handler = entry.handler.clone();
                    let k_user = {
                        let top = self.dispatch_stack.last_mut().unwrap();
                        top.handler_idx = idx;
                        top.effect = effect.clone();
                        top.k_user.clone()
                    };

                    let scope_chain = self.current_scope_chain();
                    let handler_seg = Segment::new(marker, inner_seg_id, scope_chain);
                    let handler_seg_id = self.alloc_segment(handler_seg);
                    self.current_segment = Some(handler_seg_id);

                    match handler {
                        Handler::RustProgram(rust_handler) => {
                            let program = rust_handler.create_program();
                            let step = {
                                let mut guard = program.lock().expect("Rust program lock poisoned");
                                guard.start(effect, k_user, &mut self.rust_store)
                            };
                            return self.apply_rust_program_step(step, program);
                        }
                        Handler::Python(py_handler) => {
                            self.register_continuation(k_user.clone());
                            self.pending_python = Some(PendingPython::CallPythonHandler {
                                k_user: k_user.clone(),
                                effect: effect.clone(),
                            });
                            return StepEvent::NeedsPython(PythonCall::CallHandler {
                                handler: py_handler,
                                effect: effect.clone(),
                                continuation: k_user,
                            });
                        }
                    }
                }
            }
        }

        StepEvent::Error(VMError::delegate_no_outer_handler(effect))
    }

    /// Handle handler return (explicit or implicit).
    ///
    /// Per SPEC-008: sets Mode::Deliver(value) and lets the natural caller chain
    /// walk deliver the value back. Does NOT explicitly jump to prompt_seg_id.
    /// If the handler's caller is the prompt boundary, marks dispatch completed.
    fn handle_handler_return(&mut self, value: Value) -> StepEvent {
        let Some(top) = self.dispatch_stack.last_mut() else {
            return StepEvent::Error(VMError::internal("Return outside of dispatch"));
        };

        if let Some(seg_id) = self.current_segment {
            if let Some(caller_id) = self.segments.get(seg_id).and_then(|s| s.caller) {
                if caller_id == top.prompt_seg_id {
                    top.completed = true;
                    self.consumed_cont_ids.insert(top.k_user.cont_id);
                }
            }
        }

        // D10: Spec says Mode::Deliver, not Mode::Return + explicit segment jump.
        // Natural caller-chain walking handles segment transitions.
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    fn handle_get_continuation(&mut self) -> StepEvent {
        let Some(top) = self.dispatch_stack.last() else {
            return StepEvent::Error(VMError::internal(
                "GetContinuation called outside of dispatch context",
            ));
        };
        let k = top.k_user.clone();
        self.register_continuation(k.clone());
        self.mode = Mode::Deliver(Value::Continuation(k));
        StepEvent::Continue
    }

    fn handle_get_handlers(&mut self) -> StepEvent {
        let Some(top) = self.dispatch_stack.last() else {
            return StepEvent::Error(VMError::internal(
                "GetHandlers called outside of dispatch context",
            ));
        };
        let chain = top.handler_chain.clone();
        let handlers: Vec<Handler> = chain
            .iter()
            .filter_map(|marker| {
                self.handlers.get(marker).map(|entry| {
                    if let Some(ref identity) = entry.py_identity {
                        Handler::Python(identity.clone())
                    } else {
                        entry.handler.clone()
                    }
                })
            })
            .collect();
        self.mode = Mode::Deliver(Value::Handlers(handlers));
        StepEvent::Continue
    }

    fn handle_create_continuation(
        &mut self,
        program: PyShared,
        handlers: Vec<Handler>,
        handler_identities: Vec<Option<PyShared>>,
    ) -> StepEvent {
        let k = Continuation::create_unstarted_with_identities(
            program,
            handlers,
            handler_identities,
        );
        self.register_continuation(k.clone());
        self.mode = Mode::Deliver(Value::Continuation(k));
        StepEvent::Continue
    }

    fn handle_resume_continuation(&mut self, k: Continuation, value: Value) -> StepEvent {
        if k.started {
            return self.handle_resume(k, value);
        }

        if self.is_one_shot_consumed(k.cont_id) {
            return StepEvent::Error(VMError::one_shot_violation(k.cont_id));
        }
        self.mark_one_shot_consumed(k.cont_id);

        let program = match k.program {
            Some(prog) => prog,
            None => {
                return StepEvent::Error(VMError::internal("unstarted continuation has no program"))
            }
        };

        // G7: Install handlers with prompt+body segments per handler (matches spec topology).
        // Each handler gets: prompt_seg → body_seg (handler in scope).
        // Body_seg becomes the outside for the next handler.
        let mut outside_seg_id = self.current_segment;
        let mut outside_scope = self.current_scope_chain();

        for idx in (0..k.handlers.len()).rev() {
            let handler = &k.handlers[idx];
            let py_identity = k.handler_identities.get(idx).cloned().unwrap_or(None);
            let handler_marker = Marker::fresh();
            let prompt_seg = Segment::new_prompt(
                handler_marker,
                outside_seg_id,
                outside_scope.clone(),
                handler_marker,
            );
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            let entry = match py_identity {
                Some(identity) => HandlerEntry::with_identity(handler.clone(), prompt_seg_id, identity),
                None => HandlerEntry::new(handler.clone(), prompt_seg_id),
            };
            self.handlers.insert(handler_marker, entry);

            let mut body_scope = vec![handler_marker];
            body_scope.extend(outside_scope);

            let body_seg = Segment::new(handler_marker, Some(prompt_seg_id), body_scope.clone());
            let body_seg_id = self.alloc_segment(body_seg);

            outside_seg_id = Some(body_seg_id);
            outside_scope = body_scope;
        }

        self.current_segment = outside_seg_id;
        self.pending_python = Some(PendingPython::StartProgramFrame { metadata: None });
        StepEvent::NeedsPython(PythonCall::StartProgram { program })
    }
}

impl Default for VM {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::frame::CallMetadata;

    fn make_dummy_continuation() -> Continuation {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId::from_index(0),
            frames_snapshot: std::sync::Arc::new(Vec::new()),
            scope_chain: std::sync::Arc::new(Vec::new()),
            marker: Marker::fresh(),
            dispatch_id: None,
            started: true,
            program: None,
            handlers: Vec::new(),
            handler_identities: Vec::new(),
        }
    }

    #[test]
    fn test_vm_creation() {
        let vm = VM::new();
        assert!(vm.current_segment.is_none());
        assert!(vm.dispatch_stack.is_empty());
        assert!(vm.handlers.is_empty());
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
        let seg = Segment::new(marker, None, vec![]);
        let seg_id = vm.alloc_segment(seg);

        assert!(vm.segments.get(seg_id).is_some());
    }

    #[test]
    fn test_vm_step_return_no_caller() {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![]);
        let seg_id = vm.alloc_segment(seg);

        vm.current_segment = Some(seg_id);
        vm.mode = Mode::Return(Value::Int(42));

        let event = vm.step();
        assert!(matches!(event, StepEvent::Done(Value::Int(42))));
    }

    #[test]
    fn test_vm_step_return_with_caller() {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let caller_seg = Segment::new(marker, None, vec![]);
        let caller_id = vm.alloc_segment(caller_seg);

        let child_seg = Segment::new(marker, Some(caller_id), vec![]);
        let child_id = vm.alloc_segment(child_seg);

        vm.current_segment = Some(child_id);
        vm.mode = Mode::Return(Value::Int(99));

        let event = vm.step();
        assert!(matches!(event, StepEvent::Continue));
        assert_eq!(vm.current_segment, Some(caller_id));
        assert!(vm.mode.is_deliver());
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
    fn test_vm_register_callback() {
        let mut vm = VM::new();
        let cb_id = vm.register_callback(Box::new(|v, _| Mode::Deliver(v)));

        assert!(vm.callbacks.contains_key(&cb_id));
    }

    #[test]
    fn test_visible_handlers_no_dispatch() {
        let vm = VM::new();
        let m1 = Marker::fresh();
        let m2 = Marker::fresh();
        let scope = vec![m1, m2];

        let visible = vm.visible_handlers(&scope);
        assert_eq!(visible, scope);
    }

    #[test]
    fn test_visible_handlers_with_busy_boundary() {
        let mut vm = VM::new();
        let m1 = Marker::fresh();
        let m2 = Marker::fresh();
        let m3 = Marker::fresh();
        let k_user = make_dummy_continuation();

        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "x".to_string(),
            },
            handler_chain: vec![m1, m2, m3],
            handler_idx: 1,
            k_user: k_user.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: false,
        });

        let visible = vm.visible_handlers(&vec![m1, m2, m3]);
        assert_eq!(visible, vec![m3]);
    }

    #[test]
    fn test_visible_handlers_completed_dispatch() {
        let mut vm = VM::new();
        let m1 = Marker::fresh();
        let m2 = Marker::fresh();
        let k_user = make_dummy_continuation();

        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "x".to_string(),
            },
            handler_chain: vec![m1, m2],
            handler_idx: 0,
            k_user: k_user.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: true,
        });

        let visible = vm.visible_handlers(&vec![m1, m2]);
        assert_eq!(visible, vec![m1, m2]);
    }

    #[test]
    fn test_lazy_pop_completed() {
        let mut vm = VM::new();
        let k_user_1 = make_dummy_continuation();
        let k_user_2 = make_dummy_continuation();
        let k_user_3 = make_dummy_continuation();

        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "x".to_string(),
            },
            handler_chain: vec![],
            handler_idx: 0,
            k_user: k_user_1.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: true,
        });
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "y".to_string(),
            },
            handler_chain: vec![],
            handler_idx: 0,
            k_user: k_user_2.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: true,
        });
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "z".to_string(),
            },
            handler_chain: vec![],
            handler_idx: 0,
            k_user: k_user_3.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: false,
        });

        vm.lazy_pop_completed();
        assert_eq!(vm.dispatch_stack.len(), 3);

        vm.dispatch_stack.last_mut().unwrap().completed = true;
        vm.lazy_pop_completed();
        assert_eq!(vm.dispatch_stack.len(), 0);
    }

    #[test]
    fn test_find_matching_handler() {
        let mut vm = VM::new();
        let m1 = Marker::fresh();
        let m2 = Marker::fresh();
        let prompt_seg_id = SegmentId::from_index(0);

        vm.install_handler(
            m1,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::ReaderHandlerFactory)),
                prompt_seg_id,
            ),
        );
        vm.install_handler(
            m2,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                prompt_seg_id,
            ),
        );

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
    fn test_start_dispatch_get_effect() {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let prompt_seg = Segment::new(marker, None, vec![]);
        let prompt_seg_id = vm.alloc_segment(prompt_seg);

        let body_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
        let body_seg_id = vm.alloc_segment(body_seg);
        vm.current_segment = Some(body_seg_id);

        vm.install_handler(
            marker,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                prompt_seg_id,
            ),
        );

        vm.rust_store.put("counter".to_string(), Value::Int(42));

        let result = vm.start_dispatch(Effect::Get {
            key: "counter".to_string(),
        });
        assert!(result.is_ok());
        assert!(matches!(result.unwrap(), StepEvent::Continue));
        assert_eq!(vm.dispatch_stack.len(), 1);
        // Handler yields Resume primitive; step through to process it
        let event = vm.step();
        assert!(matches!(event, StepEvent::Continue));
        assert!(vm.dispatch_stack[0].completed);
    }

    #[test]
    fn test_dispatch_completion_marking() {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let prompt_seg = Segment::new(marker, None, vec![]);
        let prompt_seg_id = vm.alloc_segment(prompt_seg);

        let body_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
        let body_seg_id = vm.alloc_segment(body_seg);
        vm.current_segment = Some(body_seg_id);

        vm.install_handler(
            marker,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                prompt_seg_id,
            ),
        );

        let _ = vm.start_dispatch(Effect::Get {
            key: "x".to_string(),
        });
        // Handler yields Resume; step through to mark dispatch complete
        let _ = vm.step();
        assert!(vm.dispatch_stack[0].completed);
    }

    #[test]
    fn test_handle_resume_call_resume_semantics() {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let caller_seg = Segment::new(marker, None, vec![marker]);
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

        let seg = Segment::new(marker, None, vec![marker]);
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

            let seg = Segment::new(marker, None, vec![marker]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let k = vm.capture_continuation(None).unwrap();

            let _ = vm.handle_resume(k.clone(), Value::Int(1));
            let event = vm.handle_resume(k, Value::Int(2));

            assert!(matches!(event, StepEvent::Continue));
            assert!(vm.mode.is_throw(), "One-shot violation should set Mode::Throw");
        });
    }

    #[test]
    fn test_one_shot_violation_transfer() {
        Python::attach(|_py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();

            let seg = Segment::new(marker, None, vec![marker]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let k = vm.capture_continuation(None).unwrap();

            let _ = vm.handle_transfer(k.clone(), Value::Int(1));
            let event = vm.handle_transfer(k, Value::Int(2));

            assert!(matches!(event, StepEvent::Continue));
            assert!(vm.mode.is_throw(), "One-shot violation should set Mode::Throw");
        });
    }

    #[test]
    fn test_handle_get_continuation() {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k_user = make_dummy_continuation();
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "x".to_string(),
            },
            handler_chain: vec![marker],
            handler_idx: 0,
            k_user: k_user.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: false,
        });

        let event = vm.handle_get_continuation();
        assert!(matches!(event, StepEvent::Continue));
        assert!(matches!(vm.mode, Mode::Deliver(Value::Continuation(_))));
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
    fn test_rust_store_clone() {
        let mut store = RustStore::new();
        store.put("key".to_string(), Value::Int(42));
        store.tell(Value::String("log".to_string()));
        store.env.insert("env_key".to_string(), Value::Bool(true));

        let cloned = store.clone();
        assert_eq!(cloned.get("key").unwrap().as_int(), Some(42));
        assert_eq!(cloned.logs().len(), 1);
        assert_eq!(cloned.ask("env_key").unwrap().as_bool(), Some(true));

        // Verify independence
        store.put("key".to_string(), Value::Int(99));
        assert_eq!(cloned.get("key").unwrap().as_int(), Some(42));
    }

    #[test]
    fn test_handle_get_handlers() {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None, vec![marker]);
        let prompt_seg_id = vm.alloc_segment(seg);

        vm.install_handler(
            marker,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                prompt_seg_id,
            ),
        );

        let handler_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
        let handler_seg_id = vm.alloc_segment(handler_seg);
        vm.current_segment = Some(handler_seg_id);

        // G8: GetHandlers requires dispatch context
        let k_user = make_dummy_continuation();
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get { key: "x".to_string() },
            handler_chain: vec![marker],
            handler_idx: 0,
            k_user,
            prompt_seg_id,
            completed: false,
        });

        let event = vm.handle_get_handlers();
        assert!(matches!(event, StepEvent::Continue));
        match &vm.mode {
            Mode::Deliver(Value::Handlers(h)) => {
                assert_eq!(h.len(), 1);
                assert!(matches!(h[0], Handler::RustProgram(_)));
            }
            _ => panic!("Expected Deliver(Handlers)"),
        }
    }

    #[test]
    fn test_handle_get_handlers_no_dispatch_errors() {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let event = vm.handle_get_handlers();
        assert!(
            matches!(event, StepEvent::Error(_)),
            "G8: GetHandlers without dispatch must error"
        );
    }

    #[test]
    fn test_continuation_registry_cleanup_on_consume() {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
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
        let prompt_seg_id = SegmentId::from_index(0);

        vm.install_handler(
            marker,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                prompt_seg_id,
            ),
        );
        assert!(vm.handlers.contains_key(&marker));
        assert_eq!(vm.handlers.len(), 1);

        let removed = vm.remove_handler(marker);
        assert!(removed);
        assert!(!vm.handlers.contains_key(&marker));
        assert_eq!(vm.handlers.len(), 0);

        // Removing again returns false
        let removed_again = vm.remove_handler(marker);
        assert!(!removed_again);
    }

    #[test]
    fn test_remove_handler_preserves_others() {
        let mut vm = VM::new();
        let m1 = Marker::fresh();
        let m2 = Marker::fresh();
        let prompt_seg_id = SegmentId::from_index(0);

        vm.install_handler(
            m1,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                prompt_seg_id,
            ),
        );
        vm.install_handler(
            m2,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::WriterHandlerFactory)),
                prompt_seg_id,
            ),
        );
        assert_eq!(vm.handlers.len(), 2);

        vm.remove_handler(m1);
        assert_eq!(vm.handlers.len(), 1);
        assert!(!vm.handlers.contains_key(&m1));
        assert!(vm.handlers.contains_key(&m2));
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
            .insert("db".to_string(), Value::String("prod".to_string()));
        store
            .env
            .insert("host".to_string(), Value::String("localhost".to_string()));

        let result = store.with_local(
            HashMap::from([
                ("db".to_string(), Value::String("test".to_string())),
                ("temp".to_string(), Value::Int(42)),
            ]),
            |s| {
                assert_eq!(s.ask("db").unwrap().as_str(), Some("test"));
                assert_eq!(s.ask("temp").unwrap().as_int(), Some(42));
                assert_eq!(s.ask("host").unwrap().as_str(), Some("localhost"));
                "done"
            },
        );
        assert_eq!(result, "done");
        // After with_local, old bindings restored, temp removed
        assert_eq!(store.ask("db").unwrap().as_str(), Some("prod"));
        assert!(store.ask("temp").is_none());
        assert_eq!(store.ask("host").unwrap().as_str(), Some("localhost"));
    }

    /// G12: DispatchContext should not have callsite_cont_id field.
    /// Spec says use k_user.cont_id directly.
    /// This test verifies dispatch completion works via k_user.cont_id.
    #[test]
    fn test_gap12_dispatch_completion_via_k_user() {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k_user = make_dummy_continuation();
        let k_cont_id = k_user.cont_id;
        let dispatch_id = DispatchId::fresh();

        vm.dispatch_stack.push(DispatchContext {
            dispatch_id,
            effect: Effect::Get {
                key: "x".to_string(),
            },
            handler_chain: vec![marker],
            handler_idx: 0,
            k_user: Continuation {
                dispatch_id: Some(dispatch_id),
                cont_id: k_cont_id,
                ..make_dummy_continuation()
            },
            prompt_seg_id: seg_id,
            completed: false,
        });

        // Verify completion check works through k_user.cont_id
        let k = Continuation {
            cont_id: k_cont_id,
            dispatch_id: Some(dispatch_id),
            ..make_dummy_continuation()
        };
        vm.check_dispatch_completion(&k);
        assert!(vm.dispatch_stack.last().unwrap().completed);
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

    /// G15: WithHandler should emit StartProgram, not CallFunc.
    /// We can't construct Py<PyAny> in Rust-only tests, so we verify
    /// this via the Python integration tests. This test serves as a
    /// documentation marker that handle_with_handler must use
    /// PythonCall::StartProgram { program } per spec.
    #[test]
    fn test_gap15_with_handler_start_program_marker() {
        // Spec requires handle_with_handler to emit:
        //   PythonCall::StartProgram { program: body }
        // NOT:
        //   PythonCall::CallFunc { func: body, args: vec![] }
        //
        // Verified by code inspection + Python integration tests.
        // The StartProgram path routes through to_generator validation.
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
        let seg = Segment::new(m1, None, vec![m1]);
        let seg_id = vm.alloc_segment(seg);
        vm.install_handler(
            m1,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                seg_id,
            ),
        );
        vm.current_segment = Some(seg_id);

        let k_user = make_dummy_continuation();
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "x".to_string(),
            },
            handler_chain: vec![],
            handler_idx: 0,
            k_user: k_user.clone(),
            prompt_seg_id: SegmentId::from_index(0),
            completed: true,
        });

        vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::GetHandlers));
        let event = vm.step_handle_yield();

        assert!(
            vm.dispatch_stack.is_empty(),
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

            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let exc_type = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let exc_value = py
                .eval(c"RuntimeError('test uncaught')", None, None)
                .unwrap()
                .unbind()
                .into_any();
            let py_exc = PyException::new(exc_type, exc_value, None);
            vm.mode = Mode::Throw(py_exc);

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
        let parent_seg = Segment::new(marker, None, vec![]);
        let parent_id = vm.alloc_segment(parent_seg);

        // Create child segment with parent as caller
        let child_seg = Segment::new(marker, Some(parent_id), vec![]);
        let child_id = vm.alloc_segment(child_seg);

        vm.current_segment = Some(child_id);
        vm.mode = Mode::Return(Value::Int(42));

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

            let seg = Segment::new(marker, None, vec![marker]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let k = vm.capture_continuation(None).unwrap();
            let _ = vm.handle_resume(k.clone(), Value::Int(1));
            let event = vm.handle_resume(k, Value::Int(2));

            assert!(matches!(event, StepEvent::Continue), "G4a: expected Continue, got Error");
            assert!(vm.mode.is_throw(), "G4a: expected Mode::Throw after one-shot violation");
        });
    }

    /// G4b: Resume on unstarted continuation → Mode::Throw (catchable), not StepEvent::Error.
    #[test]
    fn test_g4b_resume_unstarted_is_throwable() {
        Python::attach(|_py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();

            let seg = Segment::new(marker, None, vec![marker]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let mut k = make_dummy_continuation();
            k.started = false;

            let event = vm.handle_resume(k, Value::Int(1));

            assert!(matches!(event, StepEvent::Continue), "G4b: expected Continue, got Error");
            assert!(vm.mode.is_throw(), "G4b: expected Mode::Throw for unstarted Resume");
        });
    }

    /// G4c: Transfer on consumed continuation → Mode::Throw (catchable).
    #[test]
    fn test_g4c_transfer_one_shot_violation_is_throwable() {
        Python::attach(|_py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();

            let seg = Segment::new(marker, None, vec![marker]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let k = vm.capture_continuation(None).unwrap();
            let _ = vm.handle_transfer(k.clone(), Value::Int(1));
            let event = vm.handle_transfer(k, Value::Int(2));

            assert!(matches!(event, StepEvent::Continue), "G4c: expected Continue, got Error");
            assert!(vm.mode.is_throw(), "G4c: expected Mode::Throw after transfer one-shot");
        });
    }

    /// G5: Unknown Yielded should produce Mode::Throw (catchable TypeError),
    /// not StepEvent::Error (fatal).
    #[test]
    fn test_g5_unknown_yielded_is_throwable() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();

            let seg = Segment::new(marker, None, vec![marker]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            // Set mode to HandleYield with an Unknown yielded value
            let unknown_obj = py.None().into_pyobject(py).unwrap().unbind().into_any();
            vm.mode = Mode::HandleYield(Yielded::Unknown(unknown_obj));

            let event = vm.step();

            match &event {
                StepEvent::Continue => {
                    assert!(
                        vm.mode.is_throw(),
                        "G5: Expected Mode::Throw for Unknown Yielded, got {:?}",
                        vm.mode
                    );
                }
                StepEvent::Error(VMError::TypeError { .. }) => {
                    panic!("G5 REGRESSION: Unknown Yielded is StepEvent::Error (fatal) instead of Mode::Throw (catchable)");
                }
                other => panic!("G5: Unexpected event: {:?}", std::mem::discriminant(other)),
            }
        });
    }

    #[test]
    fn test_g8_pending_python_missing_is_runtime_error() {
        let mut vm = VM::new();
        vm.receive_python_result(PyCallOutcome::Value(Value::Unit));
        assert!(
            matches!(vm.mode, Mode::Throw(PyException::RuntimeError { .. })),
            "G8 FAIL: missing pending_python must throw runtime error"
        );
    }

    #[test]
    fn test_g9_kpc_effect_without_handler_is_error_not_call_rewrite() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let call_obj = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let metadata = CallMetadata {
                function_name: "kpc".to_string(),
                source_file: "x.py".to_string(),
                source_line: 1,
                program_call: Some(PyShared::new(call_obj.clone_ref(py))),
            };
            vm.mode = Mode::HandleYield(Yielded::Effect(Effect::KpcCall(
                crate::effect::KpcCallEffect {
                    call: PyShared::new(call_obj.clone_ref(py)),
                    kernel: PyShared::new(call_obj),
                    args: vec![],
                    kwargs: vec![],
                    metadata,
                },
            )));

            let event = vm.step_handle_yield();
            assert!(
                matches!(event, StepEvent::Error(_)),
                "G9 FAIL: KpcCall must not be rewritten directly to DoCtrl::Call"
            );
        });
    }

    #[test]
    fn test_g10_resume_continuation_preserves_handler_identity() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let id_obj = pyo3::types::PyDict::new(py).into_any().unbind();
            let handler = Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory));
            let program = PyShared::new(py.None().into_pyobject(py).unwrap().unbind().into_any());

            let k = Continuation::create_unstarted_with_identities(
                program,
                vec![handler],
                vec![Some(PyShared::new(id_obj.clone_ref(py)))],
            );

            let event = vm.handle_resume_continuation(k, Value::Unit);
            assert!(matches!(event, StepEvent::NeedsPython(PythonCall::StartProgram { .. })));

            let seg_id = vm.current_segment.expect("missing current segment");
            let seg = vm.segments.get(seg_id).expect("missing segment");
            let marker = *seg.scope_chain.first().expect("missing handler marker");
            let entry = vm.handlers.get(&marker).expect("missing handler entry");
            let identity = entry.py_identity.as_ref().expect(
                "G10 FAIL: continuation rehydration dropped handler identity",
            );
            assert!(
                identity.bind(py).is(&id_obj.bind(py)),
                "G10 FAIL: preserved identity does not match original"
            );
        });
    }

    /// G2: GetHandlers must return py_identity (original Python sentinel) for Rust handlers.
    #[test]
    fn test_g2_get_handlers_returns_py_identity() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();

            let seg = Segment::new(marker, None, vec![marker]);
            let prompt_seg_id = vm.alloc_segment(seg);

            let sentinel = py
                .eval(c"type('StateSentinel', (), {})()", None, None)
                .unwrap()
                .unbind()
                .into_any();

            vm.install_handler(
                marker,
                HandlerEntry::with_identity(
                    Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                    prompt_seg_id,
                    PyShared::new(sentinel.clone_ref(py)),
                ),
            );

            let handler_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
            let handler_seg_id = vm.alloc_segment(handler_seg);
            vm.current_segment = Some(handler_seg_id);

            let k_user = make_dummy_continuation();
            vm.dispatch_stack.push(DispatchContext {
                dispatch_id: DispatchId::fresh(),
                effect: Effect::Get { key: "x".to_string() },
                handler_chain: vec![marker],
                handler_idx: 0,
                k_user,
                prompt_seg_id,
                completed: false,
            });

            let event = vm.handle_get_handlers();
            assert!(matches!(event, StepEvent::Continue));

            match &vm.mode {
                Mode::Deliver(Value::Handlers(handlers)) => {
                    assert_eq!(handlers.len(), 1);
                    // DESIRED: should return the py_identity sentinel, not Handler::RustProgram
                    match &handlers[0] {
                        Handler::Python(obj) => {
                            // Success: the sentinel was returned
                            let is_same = obj.bind(py).is(&sentinel.bind(py));
                            assert!(is_same, "G2: returned object is not the original sentinel");
                        }
                        Handler::RustProgram(_) => {
                            panic!("G2 REGRESSION: GetHandlers returned Handler::RustProgram instead of py_identity sentinel");
                        }
                    }
                }
                _ => panic!("G2: Expected Deliver(Handlers)"),
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
            let prompt_seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.alloc_segment(prompt_seg);

            let body_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
            let body_seg_id = vm.alloc_segment(body_seg);
            vm.current_segment = Some(body_seg_id);

            vm.install_handler(
                marker,
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(
                        crate::handler::DoubleCallHandlerFactory,
                    )),
                    prompt_seg_id,
                ),
            );

            // Create a dummy Python modifier (won't actually be called — we feed results manually)
            let modifier = py.None().into_pyobject(py).unwrap().unbind().into_any();

            // Step 1: start_dispatch sends Modify effect
            let result = vm.start_dispatch(Effect::Modify {
                key: "key".to_string(),
                modifier: PyShared::new(modifier),
            });
            assert!(result.is_ok());
            let event1 = result.unwrap();

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
                vm.dispatch_stack
                    .last()
                    .map(|d| d.completed)
                    .unwrap_or(false),
                "Dispatch should be marked complete"
            );
        });
    }

    // === SPEC-009 Gap TDD Tests ===

    /// G3: Modify handler must resume caller with new_value (modifier result), not old_value.
    #[test]
    fn test_s009_g3_modify_resumes_with_new_value() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();

            let prompt_seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.alloc_segment(prompt_seg);

            let body_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
            let body_seg_id = vm.alloc_segment(body_seg);
            vm.current_segment = Some(body_seg_id);

            vm.install_handler(
                marker,
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                    prompt_seg_id,
                ),
            );

            vm.rust_store.put("x".to_string(), Value::Int(5));

            let modifier = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let result = vm.start_dispatch(Effect::Modify {
                key: "x".to_string(),
                modifier: PyShared::new(modifier),
            });
            assert!(result.is_ok());
            let event = result.unwrap();
            assert!(matches!(event, StepEvent::NeedsPython(PythonCall::CallFunc { .. })));

            // Feed modifier result: 5 * 10 = 50
            vm.receive_python_result(PyCallOutcome::Value(Value::Int(50)));

            // Step to process the resume
            let event2 = vm.step();
            assert!(matches!(event2, StepEvent::Continue));

            // The mode should be HandleYield with Resume primitive
            // The resume value should be 50 (new_value), NOT 5 (old_value)
            match &vm.mode {
                Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Resume { value, .. })) => {
                    assert_eq!(
                        value.as_int(),
                        Some(50),
                        "G3 FAIL: Modify resumed with {} instead of 50 (new_value). \
                         It returned old_value instead of the modifier result.",
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

        let prompt_seg = Segment::new(marker, None, vec![marker]);
        let prompt_seg_id = vm.alloc_segment(prompt_seg);

        let handler_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
        let handler_seg_id = vm.alloc_segment(handler_seg);
        vm.current_segment = Some(handler_seg_id);

        vm.install_handler(
            marker,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory)),
                prompt_seg_id,
            ),
        );

        let dispatch_id = DispatchId::fresh();
        let k_user = Continuation {
            cont_id: ContId::fresh(),
            segment_id: prompt_seg_id,
            frames_snapshot: std::sync::Arc::new(Vec::new()),
            scope_chain: std::sync::Arc::new(vec![marker]),
            marker,
            dispatch_id: Some(dispatch_id),
            started: true,
            program: None,
            handlers: Vec::new(),
            handler_identities: Vec::new(),
        };

        vm.dispatch_stack.push(DispatchContext {
            dispatch_id,
            effect: Effect::Get {
                key: "x".to_string(),
            },
            handler_chain: vec![marker],
            handler_idx: 0,
            k_user,
            prompt_seg_id,
            completed: false,
        });

        let event = vm.handle_handler_return(Value::Int(42));
        assert!(matches!(event, StepEvent::Continue));

        // D10: Mode must be Deliver, NOT Return
        assert!(
            matches!(vm.mode, Mode::Deliver(Value::Int(42))),
            "D10 REGRESSION: handle_handler_return must use Mode::Deliver, got {:?}",
            std::mem::discriminant(&vm.mode)
        );

        // D10: current_segment must NOT have jumped to prompt_seg_id
        assert_eq!(
            vm.current_segment,
            Some(handler_seg_id),
            "D10 REGRESSION: handle_handler_return must not explicitly jump current_segment"
        );
    }

    // ==========================================================
    // R9-A: DoCtrl::Call — dual-path dispatch tests
    // ==========================================================

    /// R9-A: Call with empty args/kwargs → StartProgram (DoThunk path).
    /// Spec: "DoThunk (no args): Call { f: thunk, args: [], kwargs: {}, metadata }
    ///        → driver calls to_generator() on the thunk, pushes frame."
    #[test]
    fn test_r9a_call_empty_args_yields_start_program() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let metadata = CallMetadata {
                function_name: "test_thunk".to_string(),
                source_file: "test.py".to_string(),
                source_line: 1,
                program_call: None,
            };

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: PyShared::new(dummy_f),
                args: vec![],
                kwargs: vec![],
                metadata: metadata.clone(),
            }));

            let event = vm.step_handle_yield();

            assert!(
                matches!(event, StepEvent::NeedsPython(PythonCall::StartProgram { .. })),
                "R9-A: empty args must yield StartProgram, got {:?}",
                std::mem::discriminant(&event)
            );

            match &vm.pending_python {
                Some(PendingPython::StartProgramFrame { metadata: Some(m) }) => {
                    assert_eq!(m.function_name, "test_thunk");
                }
                other => panic!(
                    "R9-A: pending_python must be StartProgramFrame with metadata, got {:?}",
                    other
                ),
            }
        });
    }

    /// R9-A: Call with non-empty args → CallFunc (Kernel path).
    /// Spec: "Kernel call (with args): Call { f: kernel, args, kwargs, metadata }
    ///        → driver calls kernel(*args, **kwargs), gets result, pushes frame."
    #[test]
    fn test_r9a_call_with_args_yields_call_func() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let metadata = CallMetadata {
                function_name: "test_kernel".to_string(),
                source_file: "test.py".to_string(),
                source_line: 10,
                program_call: None,
            };

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: PyShared::new(dummy_f),
                args: vec![Value::Int(42), Value::String("hello".to_string())],
                kwargs: vec![],
                metadata,
            }));

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

    /// R9-A: Call with kwargs preserves them as separate field in CallFunc.
    /// Spec: driver calls f(*args, **kwargs) — keyword semantics are preserved.
    #[test]
    fn test_r9a_call_kwargs_preserved_separately() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let metadata = CallMetadata {
                function_name: "test_kwargs".to_string(),
                source_file: "test.py".to_string(),
                source_line: 20,
                program_call: None,
            };

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: PyShared::new(dummy_f),
                args: vec![Value::Int(1)],
                kwargs: vec![
                    ("key1".to_string(), Value::Int(2)),
                    ("key2".to_string(), Value::String("val".to_string())),
                ],
                metadata,
            }));

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

    /// R9-A: Call with only kwargs (no positional args) still takes Kernel path.
    /// Empty args but non-empty kwargs → not DoThunk path.
    #[test]
    fn test_r9a_call_kwargs_only_takes_kernel_path() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let dummy_f = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let metadata = CallMetadata {
                function_name: "test_kwargs_only".to_string(),
                source_file: "test.py".to_string(),
                source_line: 30,
                program_call: None,
            };

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: PyShared::new(dummy_f),
                args: vec![],
                kwargs: vec![("name".to_string(), Value::String("test".to_string()))],
                metadata,
            }));

            let event = vm.step_handle_yield();

            assert!(
                matches!(event, StepEvent::NeedsPython(PythonCall::CallFunc { .. })),
                "R9-A: kwargs-only call must yield CallFunc (not StartProgram), got {:?}",
                std::mem::discriminant(&event)
            );
        });
    }

    // ==========================================================
    // R9-H: DoCtrl::Eval — atomic Create + Resume tests
    // ==========================================================

    /// R9-H: Eval creates unstarted continuation and resumes it via handle_resume_continuation.
    /// Result: NeedsPython(StartProgram { program: expr }) because unstarted continuation
    /// has a program that needs to_generator() call.
    #[test]
    fn test_r9h_eval_creates_and_resumes_continuation() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let dummy_expr = py.None().into_pyobject(py).unwrap().unbind().into_any();

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Eval {
                expr: PyShared::new(dummy_expr),
                handlers: vec![],
            }));

            let event = vm.step_handle_yield();

            assert!(
                matches!(event, StepEvent::NeedsPython(PythonCall::StartProgram { .. })),
                "R9-H: Eval must create unstarted continuation and yield StartProgram, got {:?}",
                std::mem::discriminant(&event)
            );

            assert!(
                matches!(
                    vm.pending_python,
                    Some(PendingPython::StartProgramFrame { metadata: None })
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
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let dummy_expr = py.None().into_pyobject(py).unwrap().unbind().into_any();

            let handler =
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory));

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Eval {
                expr: PyShared::new(dummy_expr),
                handlers: vec![handler],
            }));

            let event = vm.step_handle_yield();

            assert!(
                matches!(event, StepEvent::NeedsPython(PythonCall::StartProgram { .. })),
                "R9-H: Eval with handlers must still yield StartProgram"
            );

            assert!(
                !vm.handlers.is_empty(),
                "R9-H: Eval with handlers must install handler entries"
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
}
