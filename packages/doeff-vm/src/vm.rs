//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

use crate::arena::SegmentArena;
use crate::continuation::Continuation;
use crate::effect::Effect;
use crate::error::VMError;
use crate::frame::Frame;
use crate::handler::{Handler, HandlerAction, HandlerEntry};
use crate::ids::{CallbackId, ContId, DispatchId, Marker, SegmentId};
use crate::segment::Segment;
use crate::step::{
    ControlPrimitive, Mode, PendingPython, PyCallOutcome, PythonCall, StepEvent, Yielded,
};
use crate::value::Value;

pub type Callback = Box<dyn FnOnce(Value, &mut VM) -> Mode + Send>;

#[derive(Debug, Clone)]
pub struct DispatchContext {
    pub dispatch_id: DispatchId,
    pub effect: Effect,
    pub handler_chain: Vec<Marker>,
    pub handler_idx: usize,
    pub k_user: Continuation,
    pub callsite_cont_id: ContId,
    pub prompt_seg_id: SegmentId,
    pub handler_seg_id: SegmentId,
    pub completed: bool,
    pub resume_pending: bool,
    pub resume_value: Option<Value>,
}

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
}

impl Default for RustStore {
    fn default() -> Self {
        Self::new()
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

    pub fn step(&mut self, py: Python<'_>) -> StepEvent {
        self.step_counter += 1;

        if self.debug.is_enabled() {
            self.debug_step_entry();
        }

        let result = match &self.mode {
            Mode::Deliver(_) | Mode::Throw(_) => self.step_deliver_or_throw(py),
            Mode::HandleYield(_) => self.step_handle_yield(py),
            Mode::Return(_) => self.step_return(py),
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
                },
                Yielded::Primitive(p) => match p {
                    ControlPrimitive::Pure(_) => "HandleYield(Pure)",
                    ControlPrimitive::Resume { .. } => "HandleYield(Resume)",
                    ControlPrimitive::Transfer { .. } => "HandleYield(Transfer)",
                    ControlPrimitive::WithHandler { .. } => "HandleYield(WithHandler)",
                    ControlPrimitive::Delegate => "HandleYield(Delegate)",
                    ControlPrimitive::GetContinuation => "HandleYield(GetContinuation)",
                },
                Yielded::Program(_) => "HandleYield(Program)",
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
                PendingPython::StartProgramFrame => "StartProgramFrame",
                PendingPython::StepUserGenerator { .. } => "StepUserGenerator",
                PendingPython::CallPythonHandler { .. } => "CallPythonHandler",
                PendingPython::StdlibContinuation { .. } => "StdlibContinuation",
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
                        Frame::PythonGenerator { started, .. } => {
                            if *started {
                                "PythonGenerator(started)"
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
                    PythonCall::GenNext { .. } => "GenNext",
                    PythonCall::GenSend { .. } => "GenSend",
                    PythonCall::GenThrow { .. } => "GenThrow",
                };
                eprintln!("[step {}] -> NeedsPython({})", self.step_counter, call_kind);
                return;
            }
        };
        if self.debug.level == DebugLevel::Trace {
            eprintln!("[step {}] -> {}", self.step_counter, result_kind);
        }
    }

    fn step_deliver_or_throw(&mut self, py: Python<'_>) -> StepEvent {
        let seg_id = match self.current_segment {
            Some(id) => id,
            None => return StepEvent::Error(VMError::internal("no current segment")),
        };

        let segment = match self.segments.get_mut(seg_id) {
            Some(s) => s,
            None => return StepEvent::Error(VMError::invalid_segment("segment not found")),
        };

        if !segment.has_frames() {
            let value = match &self.mode {
                Mode::Deliver(v) => v.clone_ref(py),
                Mode::Throw(_) => {
                    if let Some(caller_id) = segment.caller {
                        self.current_segment = Some(caller_id);
                        return StepEvent::Continue;
                    } else {
                        return StepEvent::Error(VMError::internal("uncaught exception"));
                    }
                }
                _ => unreachable!(),
            };
            self.mode = Mode::Return(value);
            return StepEvent::Continue;
        }

        let frame = segment.pop_frame().unwrap();

        match frame {
            Frame::RustReturn { callback_id } => {
                let callback = match self.callbacks.remove(&callback_id) {
                    Some(cb) => cb,
                    None => return StepEvent::Error(VMError::internal("callback not found")),
                };

                match &self.mode {
                    Mode::Deliver(v) => {
                        self.mode = callback(v.clone_ref(py), self);
                        StepEvent::Continue
                    }
                    Mode::Throw(_) => StepEvent::Continue,
                    _ => unreachable!(),
                }
            }

            Frame::PythonGenerator { generator, started } => {
                if let (Mode::Deliver(v), Some(top)) = (&self.mode, self.dispatch_stack.last_mut())
                {
                    if self.current_segment == Some(top.handler_seg_id) && top.resume_pending {
                        top.resume_pending = false;
                        top.resume_value = Some(v.clone_ref(py));
                    }
                }
                self.pending_python = Some(PendingPython::StepUserGenerator {
                    generator: generator.clone_ref(py),
                });

                match &self.mode {
                    Mode::Deliver(v) => {
                        if started {
                            StepEvent::NeedsPython(PythonCall::GenSend {
                                gen: generator,
                                value: v.clone_ref(py),
                            })
                        } else {
                            StepEvent::NeedsPython(PythonCall::GenNext { gen: generator })
                        }
                    }
                    Mode::Throw(e) => StepEvent::NeedsPython(PythonCall::GenThrow {
                        gen: generator,
                        exc: e.exc_value.clone_ref(py),
                    }),
                    _ => unreachable!(),
                }
            }
        }
    }

    fn step_handle_yield(&mut self, py: Python<'_>) -> StepEvent {
        let yielded = match &self.mode {
            Mode::HandleYield(y) => y.clone_ref(py),
            _ => return StepEvent::Error(VMError::internal("invalid mode for handle_yield")),
        };

        match yielded {
            Yielded::Effect(effect) => match self.start_dispatch(effect) {
                Ok(event) => event,
                Err(e) => StepEvent::Error(e),
            },

            Yielded::Primitive(prim) => {
                use crate::step::ControlPrimitive;
                match prim {
                    ControlPrimitive::Pure(v) => {
                        self.mode = Mode::Deliver(v);
                        StepEvent::Continue
                    }
                    ControlPrimitive::Resume { k, value } => self.handle_resume(k, value),
                    ControlPrimitive::Transfer { k, value } => self.handle_transfer(k, value),
                    ControlPrimitive::WithHandler { handler, body } => {
                        self.handle_with_handler(handler, body)
                    }
                    ControlPrimitive::Delegate => self.handle_delegate(),
                    ControlPrimitive::GetContinuation => self.handle_get_continuation(),
                }
            }

            Yielded::Program(prog) => {
                self.pending_python = Some(PendingPython::StartProgramFrame);
                StepEvent::NeedsPython(PythonCall::StartProgram { program: prog })
            }

            Yielded::Unknown(_) => StepEvent::Error(VMError::internal("unknown yielded value")),
        }
    }

    fn step_return(&mut self, py: Python<'_>) -> StepEvent {
        let value = match &self.mode {
            Mode::Return(v) => v.clone_ref(py),
            _ => return StepEvent::Error(VMError::internal("invalid mode for return")),
        };

        let seg_id = match self.current_segment {
            Some(id) => id,
            None => return StepEvent::Done(value),
        };

        let caller = self.segments.get(seg_id).and_then(|s| s.caller);

        if let (Some(caller_id), Some(top)) = (caller, self.dispatch_stack.last_mut()) {
            if top.resume_pending && caller_id == top.handler_seg_id {
                top.resume_pending = false;
                top.resume_value = Some(value.clone_ref(py));
            }
        }

        match caller {
            Some(caller_id) => {
                self.current_segment = Some(caller_id);
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            None => StepEvent::Done(value),
        }
    }

    pub fn receive_python_result(&mut self, py: Python<'_>, outcome: PyCallOutcome) {
        let pending = match self.pending_python.take() {
            Some(p) => p,
            None => {
                self.mode = Mode::Deliver(Value::Unit);
                return;
            }
        };

        match (pending, outcome) {
            (PendingPython::StartProgramFrame, PyCallOutcome::Value(gen_obj)) => {
                if let Some(seg) = self.current_segment_mut() {
                    seg.push_frame(Frame::PythonGenerator {
                        generator: gen_obj,
                        started: false,
                    });
                }
                self.mode = Mode::Deliver(Value::Unit);
            }

            (PendingPython::StartProgramFrame, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            (PendingPython::StepUserGenerator { generator }, PyCallOutcome::GenYield(yielded)) => {
                if let Some(seg) = self.current_segment_mut() {
                    seg.push_frame(Frame::PythonGenerator {
                        generator,
                        started: true,
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
                PyCallOutcome::Value(handler_gen),
            ) => {
                let handler_return_cb = self.register_callback(Box::new(|value, vm| {
                    let _ = vm.handle_handler_return(value);
                    vm.mode.clone()
                }));
                if let Some(seg) = self.current_segment_mut() {
                    seg.push_frame(Frame::RustReturn {
                        callback_id: handler_return_cb,
                    });
                    seg.push_frame(Frame::PythonGenerator {
                        generator: handler_gen,
                        started: false,
                    });
                }
                self.mode = Mode::Deliver(Value::Unit);
            }

            (PendingPython::CallPythonHandler { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            (
                PendingPython::StdlibContinuation {
                    handler,
                    k,
                    context,
                },
                PyCallOutcome::Value(result),
            ) => {
                let value = Value::from_pyobject(&result.bind(py));
                let action = handler.continue_after_python(value, context, k, &mut self.rust_store);
                self.apply_handler_action(action);
            }

            (PendingPython::StdlibContinuation { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            _ => {
                self.mode = Mode::Deliver(Value::Unit);
            }
        }
    }

    pub fn is_one_shot_consumed(&self, cont_id: ContId) -> bool {
        self.consumed_cont_ids.contains(&cont_id)
    }

    pub fn mark_one_shot_consumed(&mut self, cont_id: ContId) {
        self.consumed_cont_ids.insert(cont_id);
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
            callsite_cont_id: k_user.cont_id,
            prompt_seg_id,
            handler_seg_id,
            completed: false,
            resume_pending: false,
            resume_value: None,
        });

        match handler {
            Handler::Stdlib(stdlib_handler) => {
                let action = stdlib_handler.handle(&effect, k_user, &mut self.rust_store);
                Ok(self.apply_handler_action(action))
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

    pub fn apply_handler_action(&mut self, action: HandlerAction) -> StepEvent {
        match action {
            HandlerAction::Resume { k, value } => {
                if self.is_one_shot_consumed(k.cont_id) {
                    return StepEvent::Error(VMError::one_shot_violation(k.cont_id));
                }
                self.mark_one_shot_consumed(k.cont_id);
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

            HandlerAction::Transfer { k, value } => {
                if self.is_one_shot_consumed(k.cont_id) {
                    return StepEvent::Error(VMError::one_shot_violation(k.cont_id));
                }
                self.mark_one_shot_consumed(k.cont_id);
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

            HandlerAction::Return { value } => {
                if let Some(top) = self.dispatch_stack.last() {
                    self.current_segment = Some(top.prompt_seg_id);
                }
                self.mode = Mode::Return(value);
                StepEvent::Continue
            }

            HandlerAction::NeedsPython {
                handler,
                call,
                k,
                context,
            } => {
                self.pending_python = Some(PendingPython::StdlibContinuation {
                    handler,
                    k,
                    context,
                });
                StepEvent::NeedsPython(call)
            }
        }
    }

    fn check_dispatch_completion(&mut self, k: &Continuation) {
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id && top.callsite_cont_id == k.cont_id {
                    top.completed = true;
                }
            }
        }
    }

    pub fn install_handler(&mut self, marker: Marker, entry: HandlerEntry) {
        self.handlers.insert(marker, entry);
    }

    pub fn installed_handler_markers(&self) -> Vec<Marker> {
        self.handlers.keys().copied().collect()
    }

    fn handle_resume(&mut self, k: Continuation, value: Value) -> StepEvent {
        if !k.started {
            return StepEvent::Error(VMError::internal(
                "Resume on unstarted continuation; use ResumeContinuation",
            ));
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return StepEvent::Error(VMError::one_shot_violation(k.cont_id));
        }
        self.mark_one_shot_consumed(k.cont_id);
        self.lazy_pop_completed();
        self.check_dispatch_completion(&k);

        if let (Some(dispatch_id), Some(top)) = (k.dispatch_id, self.dispatch_stack.last_mut()) {
            if top.dispatch_id == dispatch_id {
                top.resume_pending = true;
            }
        }

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
            return StepEvent::Error(VMError::internal(
                "Transfer on unstarted continuation; use ResumeContinuation",
            ));
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return StepEvent::Error(VMError::one_shot_violation(k.cont_id));
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

    fn handle_with_handler(&mut self, handler: Py<PyAny>, body: Py<PyAny>) -> StepEvent {
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

        self.handlers.insert(
            handler_marker,
            HandlerEntry::new(Handler::Python(handler), prompt_seg_id),
        );

        let mut body_scope = vec![handler_marker];
        body_scope.extend(outside_scope);

        let body_seg = Segment::new(handler_marker, Some(prompt_seg_id), body_scope);
        let body_seg_id = self.alloc_segment(body_seg);

        self.current_segment = Some(body_seg_id);

        self.pending_python = Some(PendingPython::StartProgramFrame);
        StepEvent::NeedsPython(PythonCall::CallFunc {
            func: body,
            args: vec![],
        })
    }

    fn handle_delegate(&mut self) -> StepEvent {
        let top = match self.dispatch_stack.last_mut() {
            Some(t) => t,
            None => {
                return StepEvent::Error(VMError::internal(
                    "Delegate called outside of dispatch context",
                ))
            }
        };

        let effect = top.effect.clone();
        let handler_chain = top.handler_chain.clone();
        let start_idx = top.handler_idx + 1;

        // Capture inner handler segment so outer handler's return flows back here
        // (result of Delegate). Per spec: caller = Some(inner_seg_id).
        let inner_seg_id = self.current_segment;

        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            if let Some(entry) = self.handlers.get(&marker) {
                if entry.handler.can_handle(&effect) {
                    let handler = entry.handler.clone();
                    let k_user = {
                        let top = self.dispatch_stack.last_mut().unwrap();
                        top.handler_idx = idx;
                        top.effect = effect.clone();
                        top.resume_pending = false;
                        top.resume_value = None;
                        top.k_user.clone()
                    };

                    let scope_chain = self.current_scope_chain();
                    let handler_seg = Segment::new(marker, inner_seg_id, scope_chain);
                    let handler_seg_id = self.alloc_segment(handler_seg);
                    self.current_segment = Some(handler_seg_id);

                    self.dispatch_stack.last_mut().unwrap().handler_seg_id = handler_seg_id;

                    match handler {
                        Handler::Stdlib(stdlib_handler) => {
                            let action =
                                stdlib_handler.handle(&effect, k_user, &mut self.rust_store);
                            return self.apply_handler_action(action);
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

    fn handle_handler_return(&mut self, value: Value) -> StepEvent {
        let Some(top) = self.dispatch_stack.last_mut() else {
            return StepEvent::Error(VMError::internal("Return outside of dispatch"));
        };

        let return_value = top.resume_value.take().unwrap_or(value);

        if let Some(seg_id) = self.current_segment {
            if let Some(caller_id) = self.segments.get(seg_id).and_then(|s| s.caller) {
                if caller_id == top.prompt_seg_id {
                    top.completed = true;
                    self.consumed_cont_ids.insert(top.callsite_cont_id);
                }
            }
        }

        self.current_segment = Some(top.prompt_seg_id);
        self.mode = Mode::Return(return_value);
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
}

impl Default for VM {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handler::StdlibHandler;

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
        Python::with_gil(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);

            vm.current_segment = Some(seg_id);
            vm.mode = Mode::Return(Value::Int(42));

            let event = vm.step(py);
            assert!(matches!(event, StepEvent::Done(Value::Int(42))));
        });
    }

    #[test]
    fn test_vm_step_return_with_caller() {
        Python::with_gil(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();

            let caller_seg = Segment::new(marker, None, vec![]);
            let caller_id = vm.alloc_segment(caller_seg);

            let child_seg = Segment::new(marker, Some(caller_id), vec![]);
            let child_id = vm.alloc_segment(child_seg);

            vm.current_segment = Some(child_id);
            vm.mode = Mode::Return(Value::Int(99));

            let event = vm.step(py);
            assert!(matches!(event, StepEvent::Continue));
            assert_eq!(vm.current_segment, Some(caller_id));
            assert!(vm.mode.is_deliver());
        });
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
            callsite_cont_id: k_user.cont_id,
            prompt_seg_id: SegmentId::from_index(0),
            handler_seg_id: SegmentId::from_index(0),
            completed: false,
            resume_pending: false,
            resume_value: None,
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
            callsite_cont_id: k_user.cont_id,
            prompt_seg_id: SegmentId::from_index(0),
            handler_seg_id: SegmentId::from_index(0),
            completed: true,
            resume_pending: false,
            resume_value: None,
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
            callsite_cont_id: k_user_1.cont_id,
            prompt_seg_id: SegmentId::from_index(0),
            handler_seg_id: SegmentId::from_index(0),
            completed: true,
            resume_pending: false,
            resume_value: None,
        });
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "y".to_string(),
            },
            handler_chain: vec![],
            handler_idx: 0,
            k_user: k_user_2.clone(),
            callsite_cont_id: k_user_2.cont_id,
            prompt_seg_id: SegmentId::from_index(0),
            handler_seg_id: SegmentId::from_index(0),
            completed: true,
            resume_pending: false,
            resume_value: None,
        });
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
            effect: Effect::Get {
                key: "z".to_string(),
            },
            handler_chain: vec![],
            handler_idx: 0,
            k_user: k_user_3.clone(),
            callsite_cont_id: k_user_3.cont_id,
            prompt_seg_id: SegmentId::from_index(0),
            handler_seg_id: SegmentId::from_index(0),
            completed: false,
            resume_pending: false,
            resume_value: None,
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
            HandlerEntry::new(Handler::Stdlib(StdlibHandler::Reader), prompt_seg_id),
        );
        vm.install_handler(
            m2,
            HandlerEntry::new(Handler::Stdlib(StdlibHandler::State), prompt_seg_id),
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
            HandlerEntry::new(Handler::Stdlib(StdlibHandler::State), prompt_seg_id),
        );

        vm.rust_store.put("counter".to_string(), Value::Int(42));

        let result = vm.start_dispatch(Effect::Get {
            key: "counter".to_string(),
        });
        assert!(result.is_ok());
        assert!(matches!(result.unwrap(), StepEvent::Continue));
        assert_eq!(vm.dispatch_stack.len(), 1);
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
            HandlerEntry::new(Handler::Stdlib(StdlibHandler::State), prompt_seg_id),
        );

        let _ = vm.start_dispatch(Effect::Get {
            key: "x".to_string(),
        });

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
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k = vm.capture_continuation(None).unwrap();
        let cont_id = k.cont_id;

        let _ = vm.handle_resume(k.clone(), Value::Int(1));
        let event = vm.handle_resume(k, Value::Int(2));

        match event {
            StepEvent::Error(VMError::OneShotViolation { cont_id: id }) => {
                assert_eq!(id, cont_id);
            }
            _ => panic!("Expected OneShotViolation error"),
        }
    }

    #[test]
    fn test_one_shot_violation_transfer() {
        let mut vm = VM::new();
        let marker = Marker::fresh();

        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k = vm.capture_continuation(None).unwrap();

        let _ = vm.handle_transfer(k.clone(), Value::Int(1));
        let event = vm.handle_transfer(k, Value::Int(2));

        assert!(matches!(
            event,
            StepEvent::Error(VMError::OneShotViolation { .. })
        ));
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
            callsite_cont_id: k_user.cont_id,
            prompt_seg_id: SegmentId::from_index(0),
            handler_seg_id: seg_id,
            completed: false,
            resume_pending: false,
            resume_value: None,
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
        let event = vm.handle_delegate();
        assert!(matches!(
            event,
            StepEvent::Error(VMError::InternalError { .. })
        ));
    }
}
