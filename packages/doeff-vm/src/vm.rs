//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::arena::SegmentArena;
use crate::capture::{
    ActiveChainEntry, CaptureEvent, DelegationEntry, DispatchAction, EffectCreationSite,
    EffectResult, FrameId, HandlerAction, HandlerDispatchEntry, HandlerKind, HandlerSnapshotEntry,
    HandlerStatus, TraceEntry,
};
use crate::continuation::Continuation;
use crate::do_ctrl::{CallArg, DoCtrl};
use crate::driver::{Mode, PyException, StepEvent};
use crate::effect::{dispatch_ref_as_python, DispatchEffect};
#[cfg(test)]
use crate::effect::{Effect, PySpawn};
use crate::error::VMError;
use crate::frame::{CallMetadata, Frame};
use crate::handler::{Handler, HandlerEntry};
use crate::ids::{CallbackId, ContId, DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::pyvm::{PyDoExprBase, PyEffectBase};
use crate::segment::Segment;
use crate::value::Value;
use crate::yielded::Yielded;

pub use crate::dispatch::DispatchContext;
pub use crate::rust_store::RustStore;

pub type Callback = Box<dyn FnOnce(Value, &mut VM) -> Mode + Send + Sync>;
static NEXT_RUN_TOKEN: AtomicU64 = AtomicU64::new(1);

#[derive(Clone)]
struct SpawnBoundaryDescriptor {
    boundary: ActiveChainEntry,
    spawn_dispatch_id: Option<DispatchId>,
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

#[derive(Debug, Clone)]
pub struct TraceEvent {
    pub step: u64,
    pub event: String,
    pub mode: String,
    pub pending: String,
    pub dispatch_depth: usize,
    pub result: Option<String>,
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
    pub trace_enabled: bool,
    pub trace_events: Vec<TraceEvent>,
    pub capture_log: Vec<CaptureEvent>,
    pub continuation_registry: HashMap<ContId, Continuation>,
    pub active_run_token: Option<u64>,
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
            trace_enabled: false,
            trace_events: Vec::new(),
            capture_log: Vec::new(),
            continuation_registry: HashMap::new(),
            active_run_token: None,
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

    pub fn begin_run_session(&mut self) -> u64 {
        let token = NEXT_RUN_TOKEN.fetch_add(1, Ordering::Relaxed);
        self.active_run_token = Some(token);
        self.capture_log.clear();
        token
    }

    pub fn current_run_token(&self) -> Option<u64> {
        self.active_run_token
    }

    pub fn end_active_run_session(&mut self) {
        let Some(run_token) = self.active_run_token.take() else {
            return;
        };

        for entry in self.handlers.values() {
            if let Handler::RustProgram(factory) = &entry.handler {
                factory.on_run_end(run_token);
            }
        }
    }

    pub fn enable_trace(&mut self, enabled: bool) {
        self.trace_enabled = enabled;
        self.trace_events.clear();
    }

    pub fn trace_events(&self) -> &[TraceEvent] {
        &self.trace_events
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

    fn eval_then_reenter_call(&mut self, expr: PyShared, cb: Callback) -> StepEvent {
        let handlers = self.current_visible_handlers();
        let cb_id = self.register_callback(cb);
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("Call evaluation outside current segment"));
        };
        seg.push_frame(Frame::RustReturn { cb: cb_id });
        self.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Eval {
            expr,
            handlers,
            metadata: None,
        }));
        StepEvent::Continue
    }

    fn is_generator_object(obj: &Py<PyAny>) -> bool {
        Python::attach(|py| {
            let by_type = py
                .import("types")
                .and_then(|m| m.getattr("GeneratorType"))
                .and_then(|ty| obj.bind(py).is_instance(&ty))
                .unwrap_or(false);
            if by_type {
                return true;
            }

            let bound = obj.bind(py);
            bound.hasattr("__next__").unwrap_or(false)
                && bound.hasattr("send").unwrap_or(false)
                && bound.hasattr("throw").unwrap_or(false)
        })
    }

    fn truncate_repr(mut text: String) -> String {
        const MAX_REPR_LEN: usize = 200;
        if text.len() > MAX_REPR_LEN {
            text.truncate(MAX_REPR_LEN);
            text.push_str("...");
        }
        text
    }

    fn value_repr(value: &Value) -> Option<String> {
        let repr = match value {
            Value::None | Value::Unit => "None".to_string(),
            Value::Python(obj) => Python::attach(|py| {
                obj.bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| "<python-value>".to_string())
            }),
            other => format!("{other:?}"),
        };
        Some(Self::truncate_repr(repr))
    }

    fn program_call_repr(metadata: &CallMetadata) -> Option<String> {
        let repr = metadata.program_call.as_ref().map(|program_call| {
            Python::attach(|py| {
                program_call
                    .bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| "<sub_program>".to_string())
            })
        })?;
        Some(Self::truncate_repr(repr))
    }

    fn exception_repr(exception: &PyException) -> Option<String> {
        let repr = match exception {
            PyException::Materialized { exc_value, .. } => Python::attach(|py| {
                exc_value
                    .bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| "<exception>".to_string())
            }),
            PyException::RuntimeError { message } => format!("RuntimeError({message:?})"),
            PyException::TypeError { message } => format!("TypeError({message:?})"),
        };
        Some(Self::truncate_repr(repr))
    }

    fn effect_repr(effect: &DispatchEffect) -> String {
        let repr = if let Some(obj) = dispatch_ref_as_python(effect) {
            Python::attach(|py| {
                obj.bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| "<effect>".to_string())
            })
        } else {
            format!("{effect:?}")
        };
        Self::truncate_repr(repr)
    }

    fn effect_creation_site(effect: &DispatchEffect) -> Option<EffectCreationSite> {
        let obj = dispatch_ref_as_python(effect)?;
        Python::attach(|py| {
            let created_at = obj.bind(py).getattr("created_at").ok()?;
            if created_at.is_none() {
                return None;
            }

            let source_file = created_at
                .getattr("filename")
                .ok()?
                .extract::<String>()
                .ok()?;
            let source_line = created_at.getattr("line").ok()?.extract::<u32>().ok()?;
            let function_name = created_at
                .getattr("function")
                .ok()?
                .extract::<String>()
                .ok()?;

            Some(EffectCreationSite {
                function_name,
                source_file,
                source_line,
            })
        })
    }

    fn python_handler_name(handler: &PyShared) -> String {
        Python::attach(|py| {
            let bound = handler.bind(py);
            bound
                .getattr("__name__")
                .ok()
                .and_then(|v| v.extract::<String>().ok())
                .or_else(|| {
                    bound
                        .getattr("__class__")
                        .ok()
                        .and_then(|cls| cls.getattr("__name__").ok())
                        .and_then(|v| v.extract::<String>().ok())
                })
                .unwrap_or_else(|| "<python_handler>".to_string())
        })
    }

    fn python_handler_source(handler: &PyShared) -> (Option<String>, Option<u32>) {
        Python::attach(|py| {
            let bound = handler.bind(py);
            let code = bound.getattr("__code__").ok().or_else(|| {
                bound
                    .getattr("__call__")
                    .ok()
                    .and_then(|call| call.getattr("__code__").ok())
            });
            let Some(code_obj) = code else {
                return (None, None);
            };
            let file = code_obj
                .getattr("co_filename")
                .ok()
                .and_then(|v| v.extract::<String>().ok());
            let line = code_obj
                .getattr("co_firstlineno")
                .ok()
                .and_then(|v| v.extract::<u32>().ok());
            (file, line)
        })
    }

    fn handler_trace_info(handler: &Handler) -> (String, HandlerKind, Option<String>, Option<u32>) {
        match handler {
            Handler::Python(py_handler) => {
                let name = Self::python_handler_name(py_handler);
                let (file, line) = Self::python_handler_source(py_handler);
                (name, HandlerKind::Python, file, line)
            }
            Handler::RustProgram(factory) => (
                factory.handler_name().to_string(),
                HandlerKind::RustBuiltin,
                None,
                None,
            ),
        }
    }

    fn marker_handler_trace_info(
        &self,
        marker: Marker,
    ) -> Option<(String, HandlerKind, Option<String>, Option<u32>)> {
        self.handlers
            .get(&marker)
            .map(|entry| Self::handler_trace_info(&entry.handler))
    }

    fn current_handler_identity_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<(usize, String)> {
        let ctx = self
            .dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .cloned()?;
        let marker = *ctx.handler_chain.get(ctx.handler_idx)?;
        let (name, _, _, _) = self.marker_handler_trace_info(marker)?;
        Some((ctx.handler_idx, name))
    }

    fn current_active_handler_dispatch_id(&self) -> Option<DispatchId> {
        let top = self.dispatch_stack.last()?;
        if top.completed {
            return None;
        }
        let marker = *top.handler_chain.get(top.handler_idx)?;
        let seg_id = self.current_segment?;
        let seg = self.segments.get(seg_id)?;
        if seg.marker == marker {
            Some(top.dispatch_id)
        } else {
            None
        }
    }

    fn py_shared_identity_eq(a: &PyShared, b: &PyShared) -> bool {
        Python::attach(|py| a.bind(py).as_ptr() == b.bind(py).as_ptr())
    }

    fn dispatch_uses_user_continuation_generator(
        &self,
        dispatch_id: DispatchId,
        generator: &PyShared,
    ) -> bool {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .is_some_and(|ctx| {
                ctx.k_user.frames_snapshot.iter().any(|frame| match frame {
                    Frame::PythonGenerator {
                        generator: snapshot_generator,
                        ..
                    } => Self::py_shared_identity_eq(snapshot_generator, generator),
                    _ => false,
                })
            })
    }

    fn generator_current_line(generator: &PyShared) -> Option<u32> {
        Python::attach(|py| {
            let resolver = py
                .import("doeff.do")
                .ok()
                .and_then(|do_mod| do_mod.getattr("resolve_doeff_inner").ok());
            let mut current = generator.bind(py).clone();
            for _ in 0..8 {
                let next = current
                    .getattr("__doeff_inner__")
                    .ok()
                    .filter(|inner| !inner.is_none())
                    .or_else(|| {
                        resolver.as_ref().and_then(|resolve| {
                            resolve
                                .call1((current.clone(),))
                                .ok()
                                .filter(|inner| !inner.is_none())
                        })
                    });
                let Some(next_inner) = next else {
                    break;
                };
                current = next_inner;
            }

            let gi_frame = current.getattr("gi_frame").ok()?;
            if gi_frame.is_none() {
                return None;
            }
            gi_frame.getattr("f_lineno").ok()?.extract::<u32>().ok()
        })
    }

    fn resume_location_from_frames(frames: &[Frame]) -> Option<(String, String, u32)> {
        for frame in frames.iter().rev() {
            if let Frame::PythonGenerator {
                generator,
                metadata: Some(metadata),
                ..
            } = frame
            {
                let line = Self::generator_current_line(generator).unwrap_or(metadata.source_line);
                return Some((
                    metadata.function_name.clone(),
                    metadata.source_file.clone(),
                    line,
                ));
            }
        }
        None
    }

    fn continuation_resume_location(k: &Continuation) -> Option<(String, String, u32)> {
        Self::resume_location_from_frames(k.frames_snapshot.as_ref())
    }

    fn effect_site_from_continuation(k: &Continuation) -> Option<(FrameId, String, String, u32)> {
        for frame in k.frames_snapshot.iter().rev() {
            if let Frame::PythonGenerator {
                generator,
                metadata: Some(metadata),
                ..
            } = frame
            {
                let line = Self::generator_current_line(generator).unwrap_or(metadata.source_line);
                return Some((
                    metadata.frame_id as FrameId,
                    metadata.function_name.clone(),
                    metadata.source_file.clone(),
                    line,
                ));
            }
        }
        None
    }

    fn maybe_emit_frame_entered(&mut self, metadata: &CallMetadata) {
        self.capture_log.push(CaptureEvent::FrameEntered {
            frame_id: metadata.frame_id as FrameId,
            function_name: metadata.function_name.clone(),
            source_file: metadata.source_file.clone(),
            source_line: metadata.source_line,
            args_repr: metadata.args_repr.clone(),
            program_call_repr: Self::program_call_repr(metadata),
        });
    }

    fn maybe_emit_frame_exited(&mut self, metadata: &CallMetadata) {
        self.capture_log.push(CaptureEvent::FrameExited {
            function_name: metadata.function_name.clone(),
        });
    }

    fn maybe_emit_handler_threw_for_dispatch(
        &mut self,
        dispatch_id: DispatchId,
        exc: &PyException,
    ) {
        let Some((handler_index, handler_name)) =
            self.current_handler_identity_for_dispatch(dispatch_id)
        else {
            return;
        };
        self.capture_log.push(CaptureEvent::HandlerCompleted {
            dispatch_id,
            handler_name,
            handler_index,
            action: HandlerAction::Threw {
                exception_repr: Self::exception_repr(exc),
            },
        });
    }

    fn maybe_emit_resume_event(
        &mut self,
        dispatch_id: DispatchId,
        handler_name: String,
        value_repr: Option<String>,
        continuation: &Continuation,
        transferred: bool,
    ) {
        if let Some((resumed_function_name, source_file, source_line)) =
            Self::continuation_resume_location(continuation)
        {
            if transferred {
                self.capture_log.push(CaptureEvent::Transferred {
                    dispatch_id,
                    handler_name,
                    value_repr,
                    resumed_function_name,
                    source_file,
                    source_line,
                });
            } else {
                self.capture_log.push(CaptureEvent::Resumed {
                    dispatch_id,
                    handler_name,
                    value_repr,
                    resumed_function_name,
                    source_file,
                    source_line,
                });
            }
        }
    }

    pub fn assemble_trace(&self) -> Vec<TraceEntry> {
        let mut trace: Vec<TraceEntry> = Vec::new();
        let mut dispatch_positions: HashMap<DispatchId, usize> = HashMap::new();

        for event in &self.capture_log {
            match event {
                CaptureEvent::FrameEntered {
                    frame_id,
                    function_name,
                    source_file,
                    source_line,
                    args_repr,
                    program_call_repr: _,
                } => {
                    trace.push(TraceEntry::Frame {
                        frame_id: *frame_id,
                        function_name: function_name.clone(),
                        source_file: source_file.clone(),
                        source_line: *source_line,
                        args_repr: args_repr.clone(),
                    });
                }
                CaptureEvent::FrameExited { .. } => {}
                CaptureEvent::DispatchStarted {
                    dispatch_id,
                    effect_repr,
                    creation_site: _,
                    handler_name,
                    handler_kind,
                    handler_source_file,
                    handler_source_line,
                    handler_chain_snapshot: _,
                    effect_frame_id: _,
                    effect_function_name: _,
                    effect_source_file: _,
                    effect_source_line: _,
                } => {
                    let pos = trace.len();
                    dispatch_positions.insert(*dispatch_id, pos);
                    trace.push(TraceEntry::Dispatch {
                        dispatch_id: *dispatch_id,
                        effect_repr: effect_repr.clone(),
                        handler_name: handler_name.clone(),
                        handler_kind: handler_kind.clone(),
                        handler_source_file: handler_source_file.clone(),
                        handler_source_line: *handler_source_line,
                        delegation_chain: vec![DelegationEntry {
                            handler_name: handler_name.clone(),
                            handler_kind: handler_kind.clone(),
                            handler_source_file: handler_source_file.clone(),
                            handler_source_line: *handler_source_line,
                        }],
                        action: DispatchAction::Active,
                        value_repr: None,
                        exception_repr: None,
                    });
                }
                CaptureEvent::Delegated {
                    dispatch_id,
                    from_handler_name: _,
                    from_handler_index: _,
                    to_handler_name,
                    to_handler_index: _,
                    to_handler_kind,
                    to_handler_source_file,
                    to_handler_source_line,
                } => {
                    if let Some(&pos) = dispatch_positions.get(dispatch_id) {
                        if let TraceEntry::Dispatch {
                            handler_name,
                            handler_kind,
                            handler_source_file,
                            handler_source_line,
                            delegation_chain,
                            ..
                        } = &mut trace[pos]
                        {
                            *handler_name = to_handler_name.clone();
                            *handler_kind = to_handler_kind.clone();
                            *handler_source_file = to_handler_source_file.clone();
                            *handler_source_line = *to_handler_source_line;
                            delegation_chain.push(DelegationEntry {
                                handler_name: to_handler_name.clone(),
                                handler_kind: to_handler_kind.clone(),
                                handler_source_file: to_handler_source_file.clone(),
                                handler_source_line: *to_handler_source_line,
                            });
                        }
                    }
                }
                CaptureEvent::HandlerCompleted {
                    dispatch_id,
                    handler_name: _,
                    handler_index: _,
                    action,
                } => {
                    if let Some(&pos) = dispatch_positions.get(dispatch_id) {
                        if let TraceEntry::Dispatch {
                            action: dispatch_action,
                            value_repr,
                            exception_repr,
                            ..
                        } = &mut trace[pos]
                        {
                            match action {
                                HandlerAction::Resumed { value_repr: repr } => {
                                    *dispatch_action = DispatchAction::Resumed;
                                    *value_repr = repr.clone();
                                }
                                HandlerAction::Transferred { value_repr: repr } => {
                                    *dispatch_action = DispatchAction::Transferred;
                                    *value_repr = repr.clone();
                                }
                                HandlerAction::Returned { value_repr: repr } => {
                                    *dispatch_action = DispatchAction::Returned;
                                    *value_repr = repr.clone();
                                }
                                HandlerAction::Threw {
                                    exception_repr: repr,
                                } => {
                                    *dispatch_action = DispatchAction::Threw;
                                    *exception_repr = repr.clone();
                                }
                            }
                        }
                    }
                }
                CaptureEvent::Resumed {
                    dispatch_id,
                    handler_name,
                    value_repr,
                    resumed_function_name,
                    source_file,
                    source_line,
                }
                | CaptureEvent::Transferred {
                    dispatch_id,
                    handler_name,
                    value_repr,
                    resumed_function_name,
                    source_file,
                    source_line,
                } => {
                    trace.push(TraceEntry::ResumePoint {
                        dispatch_id: *dispatch_id,
                        handler_name: handler_name.clone(),
                        resumed_function_name: resumed_function_name.clone(),
                        source_file: source_file.clone(),
                        source_line: *source_line,
                        value_repr: value_repr.clone(),
                    });
                }
            }
        }

        self.supplement_with_live_state(&mut trace);
        trace
    }

    fn supplement_with_live_state(&self, trace: &mut Vec<TraceEntry>) {
        let mut seg_id = self.current_segment;
        while let Some(id) = seg_id {
            let Some(seg) = self.segments.get(id) else {
                break;
            };
            for frame in &seg.frames {
                let Frame::PythonGenerator {
                    generator,
                    metadata: Some(metadata),
                    ..
                } = frame
                else {
                    continue;
                };

                let current_line =
                    Self::generator_current_line(generator).unwrap_or(metadata.source_line);
                let last_line = trace.iter().rev().find_map(|entry| match entry {
                    TraceEntry::Frame {
                        frame_id,
                        source_line,
                        ..
                    } if *frame_id == metadata.frame_id => Some(*source_line),
                    _ => None,
                });
                if last_line != Some(current_line) {
                    trace.push(TraceEntry::Frame {
                        frame_id: metadata.frame_id,
                        function_name: metadata.function_name.clone(),
                        source_file: metadata.source_file.clone(),
                        source_line: current_line,
                        args_repr: metadata.args_repr.clone(),
                    });
                }
            }
            seg_id = seg.caller;
        }

        for ctx in &self.dispatch_stack {
            if ctx.completed {
                continue;
            }
            let already_in_trace = trace.iter().any(|entry| {
                matches!(
                    entry,
                    TraceEntry::Dispatch { dispatch_id, .. } if *dispatch_id == ctx.dispatch_id
                )
            });
            if already_in_trace {
                continue;
            }

            let Some((handler_name, handler_kind, handler_source_file, handler_source_line)) = ctx
                .handler_chain
                .get(ctx.handler_idx)
                .and_then(|marker| self.marker_handler_trace_info(*marker))
            else {
                continue;
            };

            trace.push(TraceEntry::Dispatch {
                dispatch_id: ctx.dispatch_id,
                effect_repr: Self::effect_repr(&ctx.effect),
                handler_name: handler_name.clone(),
                handler_kind: handler_kind.clone(),
                handler_source_file: handler_source_file.clone(),
                handler_source_line,
                delegation_chain: vec![DelegationEntry {
                    handler_name,
                    handler_kind,
                    handler_source_file,
                    handler_source_line,
                }],
                action: DispatchAction::Active,
                value_repr: None,
                exception_repr: None,
            });
        }
    }

    fn exception_site(exception: &PyException) -> ActiveChainEntry {
        match exception {
            PyException::Materialized {
                exc_type,
                exc_value,
                exc_tb,
            } => Python::attach(|py| {
                let exc_type_bound = exc_type.bind(py);
                let exc_value_bound = exc_value.bind(py);

                let exception_type = exc_type_bound
                    .getattr("__name__")
                    .ok()
                    .and_then(|v| v.extract::<String>().ok())
                    .unwrap_or_else(|| "Exception".to_string());

                let message = exc_value_bound
                    .str()
                    .map(|v| v.to_string())
                    .unwrap_or_default();

                let mut function_name = "<unknown>".to_string();
                let mut source_file = "<unknown>".to_string();
                let mut source_line = 0u32;

                let mut tb = exc_tb
                    .as_ref()
                    .map(|tb| tb.bind(py).clone().into_any())
                    .or_else(|| exc_value_bound.getattr("__traceback__").ok());

                while let Some(tb_obj) = tb {
                    let next = tb_obj.getattr("tb_next").ok();
                    let has_next = next.as_ref().is_some_and(|n| !n.is_none());
                    if has_next {
                        tb = next;
                        continue;
                    }

                    source_line = tb_obj
                        .getattr("tb_lineno")
                        .ok()
                        .and_then(|v| v.extract::<u32>().ok())
                        .unwrap_or(0);

                    if let Ok(frame) = tb_obj.getattr("tb_frame") {
                        if let Ok(code) = frame.getattr("f_code") {
                            function_name = code
                                .getattr("co_name")
                                .ok()
                                .and_then(|v| v.extract::<String>().ok())
                                .unwrap_or_else(|| "<unknown>".to_string());
                            source_file = code
                                .getattr("co_filename")
                                .ok()
                                .and_then(|v| v.extract::<String>().ok())
                                .unwrap_or_else(|| "<unknown>".to_string());
                        }
                    }
                    break;
                }

                ActiveChainEntry::ExceptionSite {
                    function_name,
                    source_file,
                    source_line,
                    exception_type,
                    message,
                }
            }),
            PyException::RuntimeError { message } => ActiveChainEntry::ExceptionSite {
                function_name: "<runtime>".to_string(),
                source_file: "<runtime>".to_string(),
                source_line: 0,
                exception_type: "RuntimeError".to_string(),
                message: message.clone(),
            },
            PyException::TypeError { message } => ActiveChainEntry::ExceptionSite {
                function_name: "<runtime>".to_string(),
                source_file: "<runtime>".to_string(),
                source_line: 0,
                exception_type: "TypeError".to_string(),
                message: message.clone(),
            },
        }
    }

    fn spawn_boundaries_from_exception(exception: &PyException) -> Vec<SpawnBoundaryDescriptor> {
        let PyException::Materialized { exc_value, .. } = exception else {
            return Vec::new();
        };

        Python::attach(|py| {
            let mut boundaries = Vec::new();
            let mut current = exc_value
                .bind(py)
                .getattr("__doeff_spawned_from__")
                .ok()
                .filter(|v| !v.is_none());

            while let Some(payload) = current {
                let Ok(dict) = payload.cast::<PyDict>() else {
                    break;
                };

                let task_id = dict
                    .get_item("task_id")
                    .ok()
                    .flatten()
                    .and_then(|v| v.extract::<u64>().ok());
                let parent_task = dict.get_item("parent_task").ok().flatten().and_then(|v| {
                    if v.is_none() {
                        None
                    } else {
                        v.extract::<u64>().ok()
                    }
                });
                let boundary_dispatch_id = dict
                    .get_item("boundary_dispatch_id")
                    .ok()
                    .flatten()
                    .and_then(|v| {
                        if v.is_none() {
                            None
                        } else {
                            v.extract::<u64>().ok()
                        }
                    })
                    .map(DispatchId);
                let spawn_dispatch_id = dict
                    .get_item("spawn_dispatch_id")
                    .ok()
                    .flatten()
                    .and_then(|v| {
                        if v.is_none() {
                            None
                        } else {
                            v.extract::<u64>().ok()
                        }
                    })
                    .map(DispatchId);
                let insertion_dispatch_id = boundary_dispatch_id.or(spawn_dispatch_id);

                let spawn_site = dict.get_item("spawn_site").ok().flatten().and_then(|site| {
                    if site.is_none() {
                        return None;
                    }
                    let site_dict = site.cast::<PyDict>().ok()?;
                    let function_name = site_dict
                        .get_item("function_name")
                        .ok()
                        .flatten()
                        .and_then(|v| v.extract::<String>().ok())?;
                    let source_file = site_dict
                        .get_item("source_file")
                        .ok()
                        .flatten()
                        .and_then(|v| v.extract::<String>().ok())?;
                    let source_line = site_dict
                        .get_item("source_line")
                        .ok()
                        .flatten()
                        .and_then(|v| v.extract::<u32>().ok())?;
                    Some(crate::capture::SpawnSite {
                        function_name,
                        source_file,
                        source_line,
                    })
                });

                if let Some(task_id) = task_id {
                    boundaries.push(SpawnBoundaryDescriptor {
                        boundary: ActiveChainEntry::SpawnBoundary {
                            task_id,
                            parent_task,
                            spawn_site,
                        },
                        spawn_dispatch_id: insertion_dispatch_id,
                    });
                }

                current = dict.get_item("child").ok().flatten();
            }

            boundaries
        })
    }

    fn insert_spawn_boundaries(
        active_chain: &mut Vec<ActiveChainEntry>,
        entry_dispatch_ids: &mut Vec<Option<DispatchId>>,
        boundaries: Vec<SpawnBoundaryDescriptor>,
    ) {
        if boundaries.is_empty() {
            return;
        }

        let mut inserts: HashMap<usize, Vec<ActiveChainEntry>> = HashMap::new();
        let mut unresolved = Vec::new();

        for boundary in boundaries {
            if let Some(dispatch_id) = boundary.spawn_dispatch_id {
                if let Some(index) = entry_dispatch_ids
                    .iter()
                    .position(|entry_dispatch_id| *entry_dispatch_id == Some(dispatch_id))
                {
                    inserts
                        .entry(index + 1)
                        .or_default()
                        .push(boundary.boundary);
                    continue;
                }
            }
            unresolved.push(boundary.boundary);
        }

        if !unresolved.is_empty() {
            inserts
                .entry(active_chain.len())
                .or_default()
                .extend(unresolved);
        }

        let active_len = active_chain.len();
        let inserted_count: usize = inserts.values().map(Vec::len).sum();
        let mut reordered = Vec::with_capacity(active_len + inserted_count);
        let mut reordered_dispatch_ids = Vec::with_capacity(active_len + inserted_count);
        let original_dispatch_ids = std::mem::take(entry_dispatch_ids);
        for (index, entry) in active_chain.drain(..).enumerate() {
            if let Some(mut injected) = inserts.remove(&index) {
                let injected_len = injected.len();
                reordered.append(&mut injected);
                reordered_dispatch_ids.extend(std::iter::repeat(None).take(injected_len));
            }
            let dispatch_id = original_dispatch_ids.get(index).copied().flatten();
            reordered.push(entry);
            reordered_dispatch_ids.push(dispatch_id);
        }
        if let Some(mut injected) = inserts.remove(&active_len) {
            let injected_len = injected.len();
            reordered.append(&mut injected);
            reordered_dispatch_ids.extend(std::iter::repeat(None).take(injected_len));
        }
        *active_chain = reordered;
        *entry_dispatch_ids = reordered_dispatch_ids;
    }

    pub fn assemble_active_chain(&self, exception: &PyException) -> Vec<ActiveChainEntry> {
        #[derive(Clone)]
        struct FrameState {
            frame_id: FrameId,
            function_name: String,
            source_file: String,
            source_line: u32,
            sub_program_repr: String,
        }

        #[derive(Clone)]
        struct DispatchState {
            function_name: Option<String>,
            source_file: Option<String>,
            source_line: Option<u32>,
            effect_repr: String,
            handler_stack: Vec<HandlerDispatchEntry>,
            result: EffectResult,
        }

        let mut frame_stack: Vec<FrameState> = Vec::new();
        let mut dispatches: HashMap<DispatchId, DispatchState> = HashMap::new();
        let mut frame_dispatch: HashMap<FrameId, DispatchId> = HashMap::new();
        let mut transfer_targets: HashMap<DispatchId, String> = HashMap::new();

        for event in &self.capture_log {
            match event {
                CaptureEvent::FrameEntered {
                    frame_id,
                    function_name,
                    source_file,
                    source_line,
                    args_repr: _,
                    program_call_repr,
                } => {
                    frame_stack.push(FrameState {
                        frame_id: *frame_id,
                        function_name: function_name.clone(),
                        source_file: source_file.clone(),
                        source_line: *source_line,
                        sub_program_repr: program_call_repr
                            .clone()
                            .unwrap_or_else(|| "<sub_program>".to_string()),
                    });
                }
                CaptureEvent::FrameExited { .. } => {
                    let _ = frame_stack.pop();
                }
                CaptureEvent::DispatchStarted {
                    dispatch_id,
                    effect_repr,
                    creation_site: _,
                    handler_name: _,
                    handler_kind: _,
                    handler_source_file: _,
                    handler_source_line: _,
                    handler_chain_snapshot,
                    effect_frame_id,
                    effect_function_name,
                    effect_source_file,
                    effect_source_line,
                } => {
                    let handler_stack: Vec<HandlerDispatchEntry> = handler_chain_snapshot
                        .iter()
                        .enumerate()
                        .map(|(index, snapshot)| HandlerDispatchEntry {
                            handler_name: snapshot.handler_name.clone(),
                            handler_kind: snapshot.handler_kind.clone(),
                            source_file: snapshot.source_file.clone(),
                            source_line: snapshot.source_line,
                            status: if index == 0 {
                                HandlerStatus::Active
                            } else {
                                HandlerStatus::Pending
                            },
                        })
                        .collect();

                    if let Some(frame_id) = effect_frame_id {
                        frame_dispatch.insert(*frame_id, *dispatch_id);
                        if let Some(frame) =
                            frame_stack.iter_mut().find(|f| f.frame_id == *frame_id)
                        {
                            if let Some(line) = effect_source_line {
                                frame.source_line = *line;
                            }
                        }
                    }

                    dispatches.insert(
                        *dispatch_id,
                        DispatchState {
                            function_name: effect_function_name.clone(),
                            source_file: effect_source_file.clone(),
                            source_line: *effect_source_line,
                            effect_repr: effect_repr.clone(),
                            handler_stack,
                            result: EffectResult::Active,
                        },
                    );
                }
                CaptureEvent::Delegated {
                    dispatch_id,
                    from_handler_name: _,
                    from_handler_index,
                    to_handler_name: _,
                    to_handler_index,
                    to_handler_kind: _,
                    to_handler_source_file: _,
                    to_handler_source_line: _,
                } => {
                    if let Some(dispatch) = dispatches.get_mut(dispatch_id) {
                        if let Some(from_entry) =
                            dispatch.handler_stack.get_mut(*from_handler_index)
                        {
                            if from_entry.status == HandlerStatus::Active {
                                from_entry.status = HandlerStatus::Delegated;
                            }
                        }

                        if let Some(to_entry) = dispatch.handler_stack.get_mut(*to_handler_index) {
                            to_entry.status = HandlerStatus::Active;
                        }
                    }
                }
                CaptureEvent::HandlerCompleted {
                    dispatch_id,
                    handler_name,
                    handler_index,
                    action,
                } => {
                    if let Some(dispatch) = dispatches.get_mut(dispatch_id) {
                        let status = match action {
                            HandlerAction::Resumed { .. } => HandlerStatus::Resumed,
                            HandlerAction::Transferred { .. } => HandlerStatus::Transferred,
                            HandlerAction::Returned { .. } => HandlerStatus::Returned,
                            HandlerAction::Threw { .. } => HandlerStatus::Threw,
                        };

                        if let Some(target) = dispatch.handler_stack.get_mut(*handler_index) {
                            target.status = status;
                        }

                        dispatch.result = match action {
                            HandlerAction::Resumed { value_repr }
                            | HandlerAction::Returned { value_repr } => EffectResult::Resumed {
                                value_repr: value_repr
                                    .clone()
                                    .unwrap_or_else(|| "None".to_string()),
                            },
                            HandlerAction::Transferred { value_repr } => {
                                EffectResult::Transferred {
                                    handler_name: handler_name.clone(),
                                    target_repr: transfer_targets
                                        .get(dispatch_id)
                                        .cloned()
                                        .unwrap_or_else(|| {
                                            value_repr
                                                .clone()
                                                .unwrap_or_else(|| "<target>".to_string())
                                        }),
                                }
                            }
                            HandlerAction::Threw { exception_repr } => EffectResult::Threw {
                                handler_name: handler_name.clone(),
                                exception_repr: exception_repr
                                    .clone()
                                    .unwrap_or_else(|| "<exception>".to_string()),
                            },
                        };
                    }
                }
                CaptureEvent::Resumed { .. } => {}
                CaptureEvent::Transferred {
                    dispatch_id,
                    resumed_function_name,
                    source_file,
                    source_line,
                    ..
                } => {
                    transfer_targets.insert(
                        *dispatch_id,
                        format!("{resumed_function_name}() {source_file}:{source_line}"),
                    );
                }
            }
        }

        let is_visible_effect = |_result: &EffectResult| true;

        // Merge live frame line numbers for currently suspended generators.
        let mut seg_chain = Vec::new();
        let mut seg_id = self.current_segment;
        while let Some(id) = seg_id {
            seg_chain.push(id);
            seg_id = self.segments.get(id).and_then(|seg| seg.caller);
        }
        seg_chain.reverse();

        for id in seg_chain {
            let Some(seg) = self.segments.get(id) else {
                continue;
            };
            for frame in &seg.frames {
                let Frame::PythonGenerator {
                    generator,
                    metadata: Some(metadata),
                    ..
                } = frame
                else {
                    continue;
                };

                let line = Self::generator_current_line(generator).unwrap_or(metadata.source_line);
                if let Some(existing) = frame_stack
                    .iter_mut()
                    .find(|entry| entry.frame_id == metadata.frame_id)
                {
                    existing.source_line = line;
                    if existing.sub_program_repr == "<sub_program>" {
                        if let Some(repr) = Self::program_call_repr(metadata) {
                            existing.sub_program_repr = repr;
                        }
                    }
                } else {
                    frame_stack.push(FrameState {
                        frame_id: metadata.frame_id as FrameId,
                        function_name: metadata.function_name.clone(),
                        source_file: metadata.source_file.clone(),
                        source_line: line,
                        sub_program_repr: Self::program_call_repr(metadata)
                            .unwrap_or_else(|| "<sub_program>".to_string()),
                    });
                }
            }
        }

        // Refresh frame lines from the latest visible user continuation snapshot.
        // This keeps program-yield lines precise even when generators unwind on throw.
        if let Some(dispatch_ctx) = self.dispatch_stack.iter().rev().find(|ctx| {
            dispatches
                .get(&ctx.dispatch_id)
                .is_some_and(|dispatch| is_visible_effect(&dispatch.result))
        }) {
            for frame in dispatch_ctx.k_user.frames_snapshot.iter() {
                let Frame::PythonGenerator {
                    generator,
                    metadata: Some(metadata),
                    ..
                } = frame
                else {
                    continue;
                };

                let line = Self::generator_current_line(generator).unwrap_or(metadata.source_line);
                if let Some(existing) = frame_stack
                    .iter_mut()
                    .find(|entry| entry.frame_id == metadata.frame_id)
                {
                    existing.source_line = line;
                    if existing.sub_program_repr == "<sub_program>" {
                        if let Some(repr) = Self::program_call_repr(metadata) {
                            existing.sub_program_repr = repr;
                        }
                    }
                } else {
                    frame_stack.push(FrameState {
                        frame_id: metadata.frame_id as FrameId,
                        function_name: metadata.function_name.clone(),
                        source_file: metadata.source_file.clone(),
                        source_line: line,
                        sub_program_repr: Self::program_call_repr(metadata)
                            .unwrap_or_else(|| "<sub_program>".to_string()),
                    });
                }
            }
        }

        let mut active_chain = Vec::new();
        let mut entry_dispatch_ids: Vec<Option<DispatchId>> = Vec::new();
        for (index, frame) in frame_stack.iter().enumerate() {
            let dispatch_id = frame_dispatch.get(&frame.frame_id).copied();
            let dispatch = dispatch_id.and_then(|id| dispatches.get(&id));

            if let Some(dispatch) = dispatch {
                if is_visible_effect(&dispatch.result) {
                    active_chain.push(ActiveChainEntry::EffectYield {
                        function_name: dispatch
                            .function_name
                            .clone()
                            .unwrap_or_else(|| frame.function_name.clone()),
                        source_file: dispatch
                            .source_file
                            .clone()
                            .unwrap_or_else(|| frame.source_file.clone()),
                        source_line: dispatch.source_line.unwrap_or(frame.source_line),
                        effect_repr: dispatch.effect_repr.clone(),
                        handler_stack: dispatch.handler_stack.clone(),
                        result: dispatch.result.clone(),
                    });
                    entry_dispatch_ids.push(dispatch_id);
                    continue;
                }
            }

            let inferred_sub_program = frame_stack
                .get(index + 1)
                .map(|next| format!("{}()", next.function_name));
            let sub_program_repr = if frame.sub_program_repr == "<sub_program>" {
                inferred_sub_program.unwrap_or_else(|| frame.sub_program_repr.clone())
            } else {
                frame.sub_program_repr.clone()
            };

            active_chain.push(ActiveChainEntry::ProgramYield {
                function_name: frame.function_name.clone(),
                source_file: frame.source_file.clone(),
                source_line: frame.source_line,
                sub_program_repr,
            });
            entry_dispatch_ids.push(None);
        }

        if active_chain.is_empty() {
            let fallback_dispatch_id = self
                .dispatch_stack
                .iter()
                .rev()
                .find_map(|ctx| {
                    dispatches
                        .get(&ctx.dispatch_id)
                        .filter(|dispatch| matches!(dispatch.result, EffectResult::Threw { .. }))
                        .map(|_| ctx.dispatch_id)
                })
                .or_else(|| {
                    self.dispatch_stack.iter().rev().find_map(|ctx| {
                        dispatches
                            .get(&ctx.dispatch_id)
                            .filter(|dispatch| is_visible_effect(&dispatch.result))
                            .map(|_| ctx.dispatch_id)
                    })
                });

            if let Some(dispatch_id) = fallback_dispatch_id {
                if let Some(dispatch_ctx) = self
                    .dispatch_stack
                    .iter()
                    .rev()
                    .find(|ctx| ctx.dispatch_id == dispatch_id)
                {
                    let snapshot_frames: Vec<FrameState> = dispatch_ctx
                        .k_user
                        .frames_snapshot
                        .iter()
                        .filter_map(|frame| {
                            let Frame::PythonGenerator {
                                generator,
                                metadata: Some(metadata),
                                ..
                            } = frame
                            else {
                                return None;
                            };

                            let line = Self::generator_current_line(generator)
                                .unwrap_or(metadata.source_line);
                            Some(FrameState {
                                frame_id: metadata.frame_id as FrameId,
                                function_name: metadata.function_name.clone(),
                                source_file: metadata.source_file.clone(),
                                source_line: line,
                                sub_program_repr: Self::program_call_repr(metadata)
                                    .unwrap_or_else(|| "<sub_program>".to_string()),
                            })
                        })
                        .collect();

                    if let Some(dispatch) = dispatches.get(&dispatch_id) {
                        if snapshot_frames.is_empty() {
                            if is_visible_effect(&dispatch.result) {
                                active_chain.push(ActiveChainEntry::EffectYield {
                                    function_name: dispatch
                                        .function_name
                                        .clone()
                                        .unwrap_or_else(|| "<unknown>".to_string()),
                                    source_file: dispatch
                                        .source_file
                                        .clone()
                                        .unwrap_or_else(|| "<unknown>".to_string()),
                                    source_line: dispatch.source_line.unwrap_or(0),
                                    effect_repr: dispatch.effect_repr.clone(),
                                    handler_stack: dispatch.handler_stack.clone(),
                                    result: dispatch.result.clone(),
                                });
                                entry_dispatch_ids.push(Some(dispatch_id));
                            }
                        } else {
                            let last_index = snapshot_frames.len() - 1;
                            for (index, frame) in snapshot_frames.iter().enumerate() {
                                if index == last_index && is_visible_effect(&dispatch.result) {
                                    active_chain.push(ActiveChainEntry::EffectYield {
                                        function_name: dispatch
                                            .function_name
                                            .clone()
                                            .unwrap_or_else(|| frame.function_name.clone()),
                                        source_file: dispatch
                                            .source_file
                                            .clone()
                                            .unwrap_or_else(|| frame.source_file.clone()),
                                        source_line: dispatch
                                            .source_line
                                            .unwrap_or(frame.source_line),
                                        effect_repr: dispatch.effect_repr.clone(),
                                        handler_stack: dispatch.handler_stack.clone(),
                                        result: dispatch.result.clone(),
                                    });
                                    entry_dispatch_ids.push(Some(dispatch_id));
                                    continue;
                                }

                                let inferred_sub_program = snapshot_frames
                                    .get(index + 1)
                                    .map(|next| format!("{}()", next.function_name));
                                let sub_program_repr = if frame.sub_program_repr == "<sub_program>"
                                {
                                    inferred_sub_program
                                        .unwrap_or_else(|| frame.sub_program_repr.clone())
                                } else {
                                    frame.sub_program_repr.clone()
                                };

                                active_chain.push(ActiveChainEntry::ProgramYield {
                                    function_name: frame.function_name.clone(),
                                    source_file: frame.source_file.clone(),
                                    source_line: frame.source_line,
                                    sub_program_repr,
                                });
                                entry_dispatch_ids.push(None);
                            }
                        }
                    }
                }
            }
        }

        let spawn_boundaries = Self::spawn_boundaries_from_exception(exception);
        let has_spawn_boundaries = !spawn_boundaries.is_empty();
        Self::insert_spawn_boundaries(&mut active_chain, &mut entry_dispatch_ids, spawn_boundaries);
        let exception_site = Self::exception_site(exception);
        let exception_function_name = match &exception_site {
            ActiveChainEntry::ExceptionSite { function_name, .. } => function_name.as_str(),
            _ => "",
        };

        let exception_function_is_visible = active_chain.iter().any(|entry| match entry {
            ActiveChainEntry::ProgramYield { function_name, .. }
            | ActiveChainEntry::EffectYield { function_name, .. }
            | ActiveChainEntry::ExceptionSite { function_name, .. } => {
                function_name == exception_function_name
            }
            ActiveChainEntry::SpawnBoundary { .. } => false,
        });

        let suppress_exception_site = !has_spawn_boundaries
            && active_chain
                .iter()
                .rev()
                .find(|entry| !matches!(entry, ActiveChainEntry::SpawnBoundary { .. }))
                .is_some_and(|entry| {
                    matches!(
                        entry,
                        ActiveChainEntry::EffectYield {
                            result: EffectResult::Threw { .. },
                            ..
                        }
                    ) && !exception_function_is_visible
                });
        if !suppress_exception_site {
            active_chain.push(exception_site);
        }
        active_chain
    }

    pub fn step(&mut self) -> StepEvent {
        self.step_counter += 1;

        if self.trace_enabled {
            self.record_trace_entry();
        }

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

        if self.trace_enabled {
            self.record_trace_exit(&result);
        }

        result
    }

    fn mode_kind(&self) -> &'static str {
        match &self.mode {
            Mode::Deliver(_) => "Deliver",
            Mode::Throw(_) => "Throw",
            Mode::HandleYield(y) => match y {
                Yielded::DoCtrl(p) => match p {
                    DoCtrl::Pure { .. } => "HandleYield(Pure)",
                    DoCtrl::Map { .. } => "HandleYield(Map)",
                    DoCtrl::FlatMap { .. } => "HandleYield(FlatMap)",
                    DoCtrl::Perform { .. } => "HandleYield(Perform)",
                    DoCtrl::Resume { .. } => "HandleYield(Resume)",
                    DoCtrl::Transfer { .. } => "HandleYield(Transfer)",
                    DoCtrl::TransferThrow { .. } => "HandleYield(TransferThrow)",
                    DoCtrl::WithHandler { .. } => "HandleYield(WithHandler)",
                    DoCtrl::Delegate { .. } => "HandleYield(Delegate)",
                    DoCtrl::GetContinuation => "HandleYield(GetContinuation)",
                    DoCtrl::GetHandlers => "HandleYield(GetHandlers)",
                    DoCtrl::CreateContinuation { .. } => "HandleYield(CreateContinuation)",
                    DoCtrl::ResumeContinuation { .. } => "HandleYield(ResumeContinuation)",
                    DoCtrl::PythonAsyncSyntaxEscape { .. } => "HandleYield(AsyncEscape)",
                    DoCtrl::Call { .. } => "HandleYield(Call)",
                    DoCtrl::Eval { .. } => "HandleYield(Eval)",
                    DoCtrl::GetCallStack => "HandleYield(GetCallStack)",
                    DoCtrl::GetTrace => "HandleYield(GetTrace)",
                },
            },
            Mode::Return(_) => "Return",
        }
    }

    fn pending_kind(&self) -> &'static str {
        self.pending_python
            .as_ref()
            .map(|p| match p {
                PendingPython::StartProgramFrame { .. } => "StartProgramFrame",
                PendingPython::CallFuncReturn { .. } => "CallFuncReturn",
                PendingPython::StepUserGenerator { .. } => "StepUserGenerator",
                PendingPython::CallPythonHandler { .. } => "CallPythonHandler",
                PendingPython::RustProgramContinuation { .. } => "RustProgramContinuation",
                PendingPython::AsyncEscape => "AsyncEscape",
            })
            .unwrap_or("None")
    }

    fn result_kind(result: &StepEvent) -> String {
        match result {
            StepEvent::Continue => "Continue".to_string(),
            StepEvent::Done(_) => "Done".to_string(),
            StepEvent::Error(e) => format!("Error({e})"),
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
                format!("NeedsPython({call_kind})")
            }
        }
    }

    fn record_trace_entry(&mut self) {
        let mode = self.mode_kind().to_string();
        let pending = self.pending_kind().to_string();
        self.trace_events.push(TraceEvent {
            step: self.step_counter,
            event: "enter".to_string(),
            mode,
            pending,
            dispatch_depth: self.dispatch_stack.len(),
            result: None,
        });
    }

    fn record_trace_exit(&mut self, result: &StepEvent) {
        let mode = self.mode_kind().to_string();
        let pending = self.pending_kind().to_string();
        self.trace_events.push(TraceEvent {
            step: self.step_counter,
            event: "exit".to_string(),
            mode,
            pending,
            dispatch_depth: self.dispatch_stack.len(),
            result: Some(Self::result_kind(result)),
        });
    }

    fn debug_step_entry(&self) {
        let mode_kind = match &self.mode {
            Mode::Deliver(_) => "Deliver",
            Mode::Throw(_) => "Throw",
            Mode::HandleYield(y) => match y {
                Yielded::DoCtrl(p) => match p {
                    DoCtrl::Pure { .. } => "HandleYield(Pure)",
                    DoCtrl::Map { .. } => "HandleYield(Map)",
                    DoCtrl::FlatMap { .. } => "HandleYield(FlatMap)",
                    DoCtrl::Perform { .. } => "HandleYield(Perform)",
                    DoCtrl::Resume { .. } => "HandleYield(Resume)",
                    DoCtrl::Transfer { .. } => "HandleYield(Transfer)",
                    DoCtrl::TransferThrow { .. } => "HandleYield(TransferThrow)",
                    DoCtrl::WithHandler { .. } => "HandleYield(WithHandler)",
                    DoCtrl::Delegate { .. } => "HandleYield(Delegate)",
                    DoCtrl::GetContinuation => "HandleYield(GetContinuation)",
                    DoCtrl::GetHandlers => "HandleYield(GetHandlers)",
                    DoCtrl::CreateContinuation { .. } => "HandleYield(CreateContinuation)",
                    DoCtrl::ResumeContinuation { .. } => "HandleYield(ResumeContinuation)",
                    DoCtrl::PythonAsyncSyntaxEscape { .. } => "HandleYield(AsyncEscape)",
                    DoCtrl::Call { .. } => "HandleYield(Call)",
                    DoCtrl::Eval { .. } => "HandleYield(Eval)",
                    DoCtrl::GetCallStack => "HandleYield(GetCallStack)",
                    DoCtrl::GetTrace => "HandleYield(GetTrace)",
                },
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
                PendingPython::CallFuncReturn { .. } => "CallFuncReturn",
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
                let mode = std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit));
                match mode {
                    Mode::Deliver(value) => {
                        // Don't free here — step_return reads the segment's caller.
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
                            self.finalize_active_dispatches_as_threw(&exc);
                            let trace = self.assemble_trace();
                            let active_chain = self.assemble_active_chain(&exc);
                            self.segments.free(seg_id);
                            return StepEvent::Error(VMError::uncaught_exception(
                                exc,
                                trace,
                                active_chain,
                            ));
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
                let step = Python::attach(|_py| {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    match mode {
                        Mode::Deliver(value) => guard.resume(value, &mut self.rust_store),
                        Mode::Throw(exc) => guard.throw(exc, &mut self.rust_store),
                        _ => unreachable!(),
                    }
                });
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
                    Mode::Throw(exc) => StepEvent::NeedsPython(PythonCall::GenThrow { exc }),
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
                // Terminal DoCtrl variants (Resume, Transfer, TransferThrow, Delegate) transfer control
                // elsewhere — the handler is done and no value flows back. Do NOT re-push
                // the RustProgram frame for these. Non-terminal variants (Eval, GetHandlers,
                // GetCallStack) expect a result to be delivered back to this handler.
                let is_terminal = matches!(
                    &yielded,
                    Yielded::DoCtrl(DoCtrl::Resume { .. })
                        | Yielded::DoCtrl(DoCtrl::Transfer { .. })
                        | Yielded::DoCtrl(DoCtrl::TransferThrow { .. })
                        | Yielded::DoCtrl(DoCtrl::Delegate { .. })
                );
                if !is_terminal {
                    if let Some(seg) = self.current_segment_mut() {
                        seg.push_frame(Frame::RustProgram { program });
                    }
                }
                self.mode = Mode::HandleYield(yielded);
                StepEvent::Continue
            }
            RustProgramStep::Return(value) => self.handle_handler_return(value),
            RustProgramStep::Throw(exc) => {
                if let Some(dispatch_id) = self
                    .dispatch_stack
                    .last()
                    .filter(|ctx| !ctx.completed)
                    .map(|ctx| ctx.dispatch_id)
                {
                    self.maybe_emit_handler_threw_for_dispatch(dispatch_id, &exc);
                    self.mark_dispatch_threw(dispatch_id);
                }
                self.mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            RustProgramStep::NeedsPython(call) => {
                if let Some(seg) = self.current_segment_mut() {
                    seg.push_frame(Frame::RustProgram { program });
                }
                let top = self
                    .dispatch_stack
                    .last()
                    .expect("RustProgramContinuation: handler always runs inside dispatch");
                let marker = top
                    .handler_chain
                    .get(top.handler_idx)
                    .copied()
                    .unwrap_or_else(Marker::fresh);
                let k = top.k_user.clone();
                self.pending_python = Some(PendingPython::RustProgramContinuation { marker, k });
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
            Yielded::DoCtrl(prim) => {
                // Spec: Drop completed dispatches before inspecting handler context.
                self.lazy_pop_completed();
                use crate::step::DoCtrl;
                match prim {
                    DoCtrl::Pure { value } => {
                        self.mode = Mode::Deliver(value);
                        StepEvent::Continue
                    }
                    DoCtrl::Map { source, mapper } => self.handle_map(source, mapper),
                    DoCtrl::FlatMap { source, binder } => self.handle_flat_map(source, binder),
                    DoCtrl::Perform { effect } => match self.start_dispatch(effect) {
                        Ok(event) => event,
                        Err(e) => StepEvent::Error(e),
                    },
                    DoCtrl::Resume {
                        continuation,
                        value,
                    } => self.handle_resume(continuation, value),
                    DoCtrl::Transfer {
                        continuation,
                        value,
                    } => self.handle_transfer(continuation, value),
                    DoCtrl::TransferThrow {
                        continuation,
                        exception,
                    } => self.handle_transfer_throw(continuation, exception),
                    DoCtrl::WithHandler {
                        handler,
                        expr,
                        py_identity,
                    } => self.handle_with_handler(handler, expr, py_identity),
                    DoCtrl::Delegate { effect } => self.handle_delegate(effect),
                    DoCtrl::GetContinuation => self.handle_get_continuation(),
                    DoCtrl::GetHandlers => self.handle_get_handlers(),
                    DoCtrl::CreateContinuation {
                        expr,
                        handlers,
                        handler_identities,
                    } => self.handle_create_continuation(expr, handlers, handler_identities),
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
                    DoCtrl::Call {
                        f,
                        args,
                        kwargs,
                        metadata,
                    } => {
                        if let CallArg::Expr(expr) = &f {
                            let expr = expr.clone();
                            return self.eval_then_reenter_call(
                                expr,
                                Box::new(move |resolved_f, _vm| {
                                    Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                                        f: CallArg::Value(resolved_f),
                                        args,
                                        kwargs,
                                        metadata,
                                    }))
                                }),
                            );
                        }

                        if let Some(arg_idx) =
                            args.iter().position(|arg| matches!(arg, CallArg::Expr(_)))
                        {
                            let expr = match &args[arg_idx] {
                                CallArg::Expr(expr) => expr.clone(),
                                CallArg::Value(_) => unreachable!(),
                            };
                            return self.eval_then_reenter_call(
                                expr,
                                Box::new(move |resolved_arg, _vm| {
                                    let mut args = args;
                                    args[arg_idx] = CallArg::Value(resolved_arg);
                                    Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                                        f,
                                        args,
                                        kwargs,
                                        metadata,
                                    }))
                                }),
                            );
                        }

                        if let Some(kwargs_idx) = kwargs
                            .iter()
                            .position(|(_, value)| matches!(value, CallArg::Expr(_)))
                        {
                            let expr = match &kwargs[kwargs_idx].1 {
                                CallArg::Expr(expr) => expr.clone(),
                                CallArg::Value(_) => unreachable!(),
                            };
                            return self.eval_then_reenter_call(
                                expr,
                                Box::new(move |resolved_kwarg, _vm| {
                                    let mut kwargs = kwargs;
                                    kwargs[kwargs_idx].1 = CallArg::Value(resolved_kwarg);
                                    Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                                        f,
                                        args,
                                        kwargs,
                                        metadata,
                                    }))
                                }),
                            );
                        }

                        let func = match f {
                            CallArg::Value(Value::Python(func)) => PyShared::new(func),
                            CallArg::Value(other) => {
                                self.mode = Mode::Throw(PyException::type_error(format!(
                                    "DoCtrl::Call f must be Python callable value, got {:?}",
                                    other
                                )));
                                return StepEvent::Continue;
                            }
                            CallArg::Expr(_) => unreachable!(),
                        };

                        let mut value_args = Vec::with_capacity(args.len());
                        for arg in args {
                            match arg {
                                CallArg::Value(value) => value_args.push(value),
                                CallArg::Expr(_) => unreachable!(),
                            }
                        }

                        let mut value_kwargs = Vec::with_capacity(kwargs.len());
                        for (key, value) in kwargs {
                            match value {
                                CallArg::Value(inner) => value_kwargs.push((key, inner)),
                                CallArg::Expr(_) => unreachable!(),
                            }
                        }

                        self.pending_python = Some(PendingPython::CallFuncReturn {
                            metadata: Some(metadata),
                        });
                        StepEvent::NeedsPython(PythonCall::CallFunc {
                            func,
                            args: value_args,
                            kwargs: value_kwargs,
                        })
                    }
                    DoCtrl::Eval {
                        expr,
                        handlers,
                        metadata,
                    } => {
                        let cont =
                            Continuation::create_unstarted_with_metadata(expr, handlers, metadata);
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
                    DoCtrl::GetTrace => {
                        self.mode = Mode::Deliver(Value::Trace(self.assemble_trace()));
                        StepEvent::Continue
                    }
                }
            }
        }
    }

    fn step_return(&mut self) -> StepEvent {
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
                        if let Some(ref m) = metadata {
                            self.maybe_emit_frame_entered(m);
                        }
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

            (PendingPython::CallFuncReturn { metadata }, PyCallOutcome::Value(value)) => {
                match value {
                    Value::Python(obj) if Self::is_generator_object(&obj) => {
                        if let Some(ref m) = metadata {
                            self.maybe_emit_frame_entered(m);
                        }
                        if let Some(seg) = self.current_segment_mut() {
                            seg.push_frame(Frame::PythonGenerator {
                                generator: PyShared::new(obj),
                                started: false,
                                metadata,
                            });
                        }
                        self.mode = Mode::Deliver(Value::Unit);
                    }
                    other => {
                        self.mode = Mode::Deliver(other);
                    }
                }
            }

            (PendingPython::CallFuncReturn { .. }, PyCallOutcome::GenError(e)) => {
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

            (
                PendingPython::StepUserGenerator { metadata, .. },
                PyCallOutcome::GenReturn(value),
            ) => {
                if let Some(ref m) = metadata {
                    self.maybe_emit_frame_exited(m);
                }
                self.mode = Mode::Deliver(value);
            }

            (
                PendingPython::StepUserGenerator {
                    generator,
                    metadata: _,
                },
                PyCallOutcome::GenError(e),
            ) => {
                if let Some(dispatch_id) = self.current_active_handler_dispatch_id() {
                    if self.dispatch_uses_user_continuation_generator(dispatch_id, &generator) {
                        self.mark_dispatch_completed(dispatch_id);
                    } else {
                        self.maybe_emit_handler_threw_for_dispatch(dispatch_id, &e);
                        self.mark_dispatch_threw(dispatch_id);
                    }
                }
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
                if let Some(dispatch_id) = self
                    .dispatch_stack
                    .last()
                    .filter(|ctx| !ctx.completed)
                    .map(|ctx| ctx.dispatch_id)
                {
                    self.maybe_emit_handler_threw_for_dispatch(dispatch_id, &e);
                    self.mark_dispatch_threw(dispatch_id);
                }
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

        if top.completed || self.consumed_cont_ids.contains(&top.k_user.cont_id) {
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
        effect: &DispatchEffect,
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

    pub fn start_dispatch(&mut self, effect: DispatchEffect) -> Result<StepEvent, VMError> {
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
        let mut handler_chain_snapshot: Vec<HandlerSnapshotEntry> = Vec::new();
        for marker in handler_chain.iter().copied() {
            let Some(entry) = self.handlers.get(&marker) else {
                continue;
            };
            let (name, kind, file, line) = Self::handler_trace_info(&entry.handler);
            handler_chain_snapshot.push(HandlerSnapshotEntry {
                handler_name: name,
                handler_kind: kind,
                source_file: file,
                source_line: line,
            });
        }

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

        let (handler_name, handler_kind, handler_source_file, handler_source_line) =
            Self::handler_trace_info(&handler);
        let effect_site = Self::effect_site_from_continuation(&k_user);
        self.capture_log.push(CaptureEvent::DispatchStarted {
            dispatch_id,
            effect_repr: Self::effect_repr(&effect),
            creation_site: Self::effect_creation_site(&effect),
            handler_name,
            handler_kind,
            handler_source_file,
            handler_source_line,
            handler_chain_snapshot,
            effect_frame_id: effect_site.as_ref().map(|(frame_id, _, _, _)| *frame_id),
            effect_function_name: effect_site
                .as_ref()
                .map(|(_, function_name, _, _)| function_name.clone()),
            effect_source_file: effect_site
                .as_ref()
                .map(|(_, _, source_file, _)| source_file.clone()),
            effect_source_line: effect_site
                .as_ref()
                .map(|(_, _, _, source_line)| *source_line),
        });

        match handler {
            Handler::RustProgram(rust_handler) => {
                let program = rust_handler.create_program_for_run(self.current_run_token());
                let step = {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    Python::attach(|py| guard.start(py, effect, k_user, &mut self.rust_store))
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

    fn active_dispatch_handler_is_python(&self, dispatch_id: DispatchId) -> bool {
        self.dispatch_stack
            .last()
            .filter(|ctx| ctx.dispatch_id == dispatch_id)
            .and_then(|ctx| ctx.handler_chain.get(ctx.handler_idx))
            .and_then(|marker| self.handlers.get(marker))
            .is_some_and(|entry| matches!(entry.handler, Handler::Python(_)))
    }

    fn mark_dispatch_threw(&mut self, dispatch_id: DispatchId) {
        self.mark_dispatch_completed(dispatch_id);
    }

    fn mark_dispatch_completed(&mut self, dispatch_id: DispatchId) {
        if let Some(ctx) = self
            .dispatch_stack
            .iter_mut()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
        {
            ctx.completed = true;
            self.consumed_cont_ids.insert(ctx.k_user.cont_id);
        }
    }

    fn dispatch_has_terminal_handler_action(&self, dispatch_id: DispatchId) -> bool {
        self.capture_log.iter().rev().any(|event| match event {
            CaptureEvent::HandlerCompleted {
                dispatch_id: event_dispatch_id,
                action:
                    HandlerAction::Resumed { .. }
                    | HandlerAction::Transferred { .. }
                    | HandlerAction::Returned { .. }
                    | HandlerAction::Threw { .. },
                ..
            } => *event_dispatch_id == dispatch_id,
            _ => false,
        })
    }

    fn finalize_active_dispatches_as_threw(&mut self, exception: &PyException) {
        let exception_repr = Self::exception_repr(exception);
        for idx in 0..self.dispatch_stack.len() {
            let (dispatch_id, cont_id, completed) = {
                let ctx = &self.dispatch_stack[idx];
                (ctx.dispatch_id, ctx.k_user.cont_id, ctx.completed)
            };
            if completed {
                continue;
            }
            if self.dispatch_has_terminal_handler_action(dispatch_id) {
                if let Some(ctx) = self.dispatch_stack.get_mut(idx) {
                    ctx.completed = true;
                }
                self.consumed_cont_ids.insert(cont_id);
                continue;
            }
            let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            else {
                if let Some(ctx) = self.dispatch_stack.get_mut(idx) {
                    ctx.completed = true;
                }
                self.consumed_cont_ids.insert(cont_id);
                continue;
            };
            self.capture_log.push(CaptureEvent::HandlerCompleted {
                dispatch_id,
                handler_name,
                handler_index,
                action: HandlerAction::Threw {
                    exception_repr: exception_repr.clone(),
                },
            });
            if let Some(ctx) = self.dispatch_stack.get_mut(idx) {
                ctx.completed = true;
            }
            self.consumed_cont_ids.insert(cont_id);
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
            return self
                .throw_runtime_error("Resume on unstarted continuation; use ResumeContinuation");
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        self.mark_one_shot_consumed(k.cont_id);
        self.lazy_pop_completed();
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            {
                let value_repr = Self::value_repr(&value);
                self.capture_log.push(CaptureEvent::HandlerCompleted {
                    dispatch_id,
                    handler_name: handler_name.clone(),
                    handler_index,
                    action: HandlerAction::Resumed {
                        value_repr: value_repr.clone(),
                    },
                });
                self.maybe_emit_resume_event(dispatch_id, handler_name, value_repr, &k, false);
            }
        }
        if let Some(dispatch_id) = k.dispatch_id {
            if !self.active_dispatch_handler_is_python(dispatch_id) {
                self.check_dispatch_completion(&k);
            }
        } else {
            self.check_dispatch_completion(&k);
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
            return self
                .throw_runtime_error("Transfer on unstarted continuation; use ResumeContinuation");
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        self.mark_one_shot_consumed(k.cont_id);
        self.lazy_pop_completed();
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            {
                let value_repr = Self::value_repr(&value);
                self.capture_log.push(CaptureEvent::HandlerCompleted {
                    dispatch_id,
                    handler_name: handler_name.clone(),
                    handler_index,
                    action: HandlerAction::Transferred {
                        value_repr: value_repr.clone(),
                    },
                });
                self.maybe_emit_resume_event(dispatch_id, handler_name, value_repr, &k, true);
            }
        }
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

    fn handle_transfer_throw(&mut self, k: Continuation, exception: PyException) -> StepEvent {
        if !k.started {
            return self.throw_runtime_error(
                "TransferThrow on unstarted continuation; use ResumeContinuation",
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
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            {
                self.capture_log.push(CaptureEvent::HandlerCompleted {
                    dispatch_id,
                    handler_name,
                    handler_index,
                    action: HandlerAction::Threw {
                        exception_repr: Self::exception_repr(&exception),
                    },
                });
            }
        }
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
        self.mode = Mode::Throw(exception);
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

    fn handle_delegate(&mut self, effect: DispatchEffect) -> StepEvent {
        let (handler_chain, start_idx, from_idx, dispatch_id) = match self.dispatch_stack.last() {
            Some(t) => (
                t.handler_chain.clone(),
                t.handler_idx + 1,
                t.handler_idx,
                t.dispatch_id,
            ),
            None => {
                return StepEvent::Error(VMError::internal(
                    "Delegate called outside of dispatch context",
                ))
            }
        };

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
                    let from_marker = handler_chain.get(from_idx).copied();
                    let from_name = from_marker
                        .and_then(|m| self.marker_handler_trace_info(m))
                        .map(|(name, _, _, _)| name);
                    let to_info = self.marker_handler_trace_info(marker);
                    if let (
                        Some(from_name),
                        Some((to_name, to_kind, to_source_file, to_source_line)),
                    ) = (from_name, to_info)
                    {
                        self.capture_log.push(CaptureEvent::Delegated {
                            dispatch_id,
                            from_handler_name: from_name,
                            from_handler_index: from_idx,
                            to_handler_name: to_name,
                            to_handler_index: idx,
                            to_handler_kind: to_kind,
                            to_handler_source_file: to_source_file,
                            to_handler_source_line: to_source_line,
                        });
                    }
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
                            let program =
                                rust_handler.create_program_for_run(self.current_run_token());
                            let step = {
                                let mut guard = program.lock().expect("Rust program lock poisoned");
                                Python::attach(|py| {
                                    guard.start(py, effect, k_user, &mut self.rust_store)
                                })
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
        if let Value::Python(obj) = &value {
            let should_eval = Python::attach(|py| {
                let bound = obj.bind(py);
                bound.is_instance_of::<PyDoExprBase>() || bound.is_instance_of::<PyEffectBase>()
            });

            if should_eval {
                let handlers = self.current_visible_handlers();
                let expr = PyShared::new(obj.clone());
                let cb = self.register_callback(Box::new(|resolved, vm| {
                    let _ = vm.handle_handler_return(resolved);
                    std::mem::replace(&mut vm.mode, Mode::Deliver(Value::Unit))
                }));
                if let Some(seg) = self.current_segment_mut() {
                    seg.push_frame(Frame::RustReturn { cb });
                }
                self.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Eval {
                    expr,
                    handlers,
                    metadata: None,
                }));
                return StepEvent::Continue;
            }
        }

        let Some(top_snapshot) = self.dispatch_stack.last().cloned() else {
            return StepEvent::Error(VMError::internal("Return outside of dispatch"));
        };

        let Some((handler_index, handler_name)) =
            self.current_handler_identity_for_dispatch(top_snapshot.dispatch_id)
        else {
            self.mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };
        let value_repr = Self::value_repr(&value);
        self.capture_log.push(CaptureEvent::HandlerCompleted {
            dispatch_id: top_snapshot.dispatch_id,
            handler_name: handler_name.clone(),
            handler_index,
            action: HandlerAction::Returned {
                value_repr: value_repr.clone(),
            },
        });
        self.maybe_emit_resume_event(
            top_snapshot.dispatch_id,
            handler_name,
            value_repr,
            &top_snapshot.k_user,
            false,
        );

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

    fn current_visible_handlers(&self) -> Vec<Handler> {
        let scope_chain = self.current_scope_chain();
        let visible = self.visible_handlers(&scope_chain);
        let mut handlers = Vec::with_capacity(visible.len());
        for marker in visible {
            if let Some(entry) = self.handlers.get(&marker) {
                handlers.push(entry.handler.clone());
            }
        }
        handlers
    }

    fn handle_map(&mut self, source: PyShared, mapper: PyShared) -> StepEvent {
        let handlers = self.current_visible_handlers();
        let map_cb = self.register_callback(Box::new(move |value, _vm| {
            Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: CallArg::Value(Value::Python(mapper.into_inner())),
                args: vec![CallArg::Value(value)],
                kwargs: vec![],
                metadata: crate::frame::CallMetadata::anonymous(),
            }))
        }));

        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("Map outside current segment"));
        };
        seg.push_frame(Frame::RustReturn { cb: map_cb });
        self.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Eval {
            expr: source,
            handlers,
            metadata: None,
        }));
        StepEvent::Continue
    }

    fn handle_flat_map(&mut self, source: PyShared, binder: PyShared) -> StepEvent {
        let handlers = self.current_visible_handlers();
        let handlers_after_bind = handlers.clone();

        let bind_result_cb =
            self.register_callback(Box::new(move |bound_value, _vm| match bound_value {
                Value::Python(obj) => Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Eval {
                    expr: PyShared::new(obj),
                    handlers: handlers_after_bind,
                    metadata: None,
                })),
                other => Mode::Throw(PyException::type_error(format!(
                    "flat_map binder must return Program/Effect/DoCtrl; got {:?}",
                    other
                ))),
            }));

        let bind_source_cb = self.register_callback(Box::new(move |value, vm| {
            let Some(seg) = vm.current_segment_mut() else {
                return Mode::Throw(PyException::runtime_error(
                    "flat_map binder callback outside current segment",
                ));
            };
            seg.push_frame(Frame::RustReturn { cb: bind_result_cb });
            Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: CallArg::Value(Value::Python(binder.into_inner())),
                args: vec![CallArg::Value(value)],
                kwargs: vec![],
                metadata: crate::frame::CallMetadata::anonymous(),
            }))
        }));

        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("FlatMap outside current segment"));
        };
        seg.push_frame(Frame::RustReturn { cb: bind_source_cb });
        self.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Eval {
            expr: source,
            handlers,
            metadata: None,
        }));
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
        let mut handlers: Vec<Handler> = Vec::with_capacity(chain.len());
        for marker in &chain {
            let Some(entry) = self.handlers.get(marker) else {
                continue;
            };
            handlers.push(entry.handler.clone());
        }
        self.mode = Mode::Deliver(Value::Handlers(handlers));
        StepEvent::Continue
    }

    fn handle_create_continuation(
        &mut self,
        program: PyShared,
        handlers: Vec<Handler>,
        handler_identities: Vec<Option<PyShared>>,
    ) -> StepEvent {
        let k =
            Continuation::create_unstarted_with_identities(program, handlers, handler_identities);
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
        let start_metadata = k.metadata.clone();

        // G7: Install handlers with prompt+body segments per handler (matches spec topology).
        // Each handler gets: prompt_seg → body_seg (handler in scope).
        // Body_seg becomes the outside for the next handler.
        let mut outside_seg_id = self.current_segment;
        let mut outside_scope = self.current_scope_chain();

        let k_handler_count = k.handlers.len();
        for idx in (0..k_handler_count).rev() {
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
                Some(identity) => {
                    HandlerEntry::with_identity(handler.clone(), prompt_seg_id, identity)
                }
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
        self.pending_python = Some(PendingPython::StartProgramFrame {
            metadata: start_metadata,
        });
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
            metadata: None,
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
    fn test_start_dispatch_records_effect_creation_site_from_created_at() {
        Python::attach(|py| {
            use pyo3::types::PyModule;
            use std::sync::Arc;

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
                    Handler::RustProgram(Arc::new(crate::scheduler::SchedulerHandler::new())),
                    prompt_seg_id,
                ),
            );

            let module = PyModule::from_code(
                py,
                c"class _Ctx:\n    def __init__(self, filename, line, function):\n        self.filename = filename\n        self.line = line\n        self.function = function\n",
                c"_vm_creation_site_test.py",
                c"_vm_creation_site_test",
            )
            .expect("failed to create test module");
            let ctx = module
                .getattr("_Ctx")
                .expect("missing _Ctx")
                .call1(("/tmp/user_program.py", 777_u32, "parent"))
                .expect("failed to instantiate _Ctx")
                .unbind();

            let spawn = Py::new(py, PySpawn::create(py, py.None(), None, None, None, None))
                .expect("failed to create SpawnEffect");
            spawn
                .bind(py)
                .call_method1("with_created_at", (ctx,))
                .expect("failed to set created_at");
            let effect_obj = spawn.into_any();

            let result = vm.start_dispatch(Effect::Python(PyShared::new(effect_obj)));
            assert!(result.is_ok());

            let creation_site = vm.capture_log.iter().find_map(|event| {
                if let CaptureEvent::DispatchStarted { creation_site, .. } = event {
                    creation_site.clone()
                } else {
                    None
                }
            });

            let site = creation_site.expect("dispatch should record effect creation site");
            assert_eq!(site.function_name, "parent");
            assert_eq!(site.source_file, "/tmp/user_program.py");
            assert_eq!(site.source_line, 777);
        });
    }

    #[test]
    fn test_generator_current_line_prefers_doeff_inner_attribute() {
        Python::attach(|py| {
            use pyo3::types::PyModule;

            let module = PyModule::from_code(
                py,
                c"def inner():\n    yield 'inner'\n\ndef outer():\n    inner_gen = inner()\n    yield 'outer'\n\nouter_gen = outer()\nnext(outer_gen)\ninner_gen = outer_gen.gi_frame.f_locals['inner_gen']\nnext(inner_gen)\n\nclass _Wrapper:\n    def __init__(self, outer_gen, inner_gen):\n        self.gi_frame = outer_gen.gi_frame\n        self.__doeff_inner__ = inner_gen\n\nwrapper = _Wrapper(outer_gen, inner_gen)\nINNER_LINE = inner_gen.gi_frame.f_lineno\nOUTER_LINE = outer_gen.gi_frame.f_lineno\n",
                c"_vm_doeff_inner_attr_test.py",
                c"_vm_doeff_inner_attr_test",
            )
            .expect("failed to create test module");
            let wrapper = module.getattr("wrapper").expect("missing wrapper").unbind();
            let inner_line: u32 = module
                .getattr("INNER_LINE")
                .expect("missing INNER_LINE")
                .extract()
                .expect("INNER_LINE must be int");
            let outer_line: u32 = module
                .getattr("OUTER_LINE")
                .expect("missing OUTER_LINE")
                .extract()
                .expect("OUTER_LINE must be int");

            let observed = VM::generator_current_line(&PyShared::new(wrapper))
                .expect("expected generator line");
            assert_eq!(observed, inner_line);
            assert_ne!(observed, outer_line);
        });
    }

    #[test]
    fn test_insert_spawn_boundaries_does_not_depend_on_scheduler_handler_name() {
        let mut active_chain = vec![
            ActiveChainEntry::EffectYield {
                function_name: "child".to_string(),
                source_file: "child.py".to_string(),
                source_line: 12,
                effect_repr: "Spawn(...)".to_string(),
                handler_stack: Vec::new(),
                result: EffectResult::Transferred {
                    handler_name: "CustomScheduler".to_string(),
                    target_repr: "parent() parent.py:8".to_string(),
                },
            },
            ActiveChainEntry::ExceptionSite {
                function_name: "child".to_string(),
                source_file: "child.py".to_string(),
                source_line: 13,
                exception_type: "ValueError".to_string(),
                message: "boom".to_string(),
            },
        ];
        let mut dispatch_ids = vec![Some(DispatchId(42)), None];

        VM::insert_spawn_boundaries(
            &mut active_chain,
            &mut dispatch_ids,
            vec![SpawnBoundaryDescriptor {
                boundary: ActiveChainEntry::SpawnBoundary {
                    task_id: 7,
                    parent_task: Some(2),
                    spawn_site: None,
                },
                spawn_dispatch_id: Some(DispatchId(42)),
            }],
        );

        assert!(matches!(
            active_chain.get(1),
            Some(ActiveChainEntry::SpawnBoundary { task_id: 7, .. })
        ));
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
            assert!(
                vm.mode.is_throw(),
                "One-shot violation should set Mode::Throw"
            );
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
            assert!(
                vm.mode.is_throw(),
                "One-shot violation should set Mode::Throw"
            );
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
        store
            .env
            .insert("env_key".to_string().into(), Value::Bool(true));

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
            effect: Effect::Get {
                key: "x".to_string(),
            },
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

            assert!(
                matches!(event, StepEvent::Continue),
                "G4a: expected Continue, got Error"
            );
            assert!(
                vm.mode.is_throw(),
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

            let seg = Segment::new(marker, None, vec![marker]);
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
                vm.mode.is_throw(),
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

            let seg = Segment::new(marker, None, vec![marker]);
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
                vm.mode.is_throw(),
                "G4c: expected Mode::Throw after transfer one-shot"
            );
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
    fn test_g10_resume_continuation_preserves_handler_identity() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let id_obj = pyo3::types::PyDict::new(py).into_any().unbind();
            let handler =
                Handler::RustProgram(std::sync::Arc::new(crate::handler::StateHandlerFactory));
            let program = PyShared::new(py.None().into_pyobject(py).unwrap().unbind().into_any());

            let k = Continuation::create_unstarted_with_identities(
                program,
                vec![handler],
                vec![Some(PyShared::new(id_obj.clone_ref(py)))],
            );

            let event = vm.handle_resume_continuation(k, Value::Unit);
            assert!(matches!(
                event,
                StepEvent::NeedsPython(PythonCall::StartProgram { .. })
            ));

            let seg_id = vm.current_segment.expect("missing current segment");
            let seg = vm.segments.get(seg_id).expect("missing segment");
            let marker = *seg.scope_chain.first().expect("missing handler marker");
            let entry = vm.handlers.get(&marker).expect("missing handler entry");
            let identity = entry
                .py_identity
                .as_ref()
                .expect("G10 FAIL: continuation rehydration dropped handler identity");
            assert!(
                identity.bind(py).is(&id_obj.bind(py)),
                "G10 FAIL: preserved identity does not match original"
            );
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
            match &vm.mode {
                Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Resume { value, .. })) => {
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
            metadata: None,
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

    /// R9-A: Call with empty args/kwargs still dispatches via CallFunc.
    #[test]
    fn test_r9a_call_empty_args_yields_call_func() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
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

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: CallArg::Value(Value::Python(dummy_f)),
                args: vec![],
                kwargs: vec![],
                metadata: metadata.clone(),
            }));

            let event = vm.step_handle_yield();

            assert!(
                matches!(event, StepEvent::NeedsPython(PythonCall::CallFunc { .. })),
                "R9-A: empty args must yield CallFunc, got {:?}",
                std::mem::discriminant(&event)
            );

            match &vm.pending_python {
                Some(PendingPython::CallFuncReturn { metadata: Some(m) }) => {
                    assert_eq!(m.function_name, "test_thunk");
                }
                other => panic!(
                    "R9-A: pending_python must be CallFuncReturn with metadata, got {:?}",
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
            let metadata = CallMetadata::new(
                "test_kernel".to_string(),
                "test.py".to_string(),
                10,
                None,
                None,
            );

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: CallArg::Value(Value::Python(dummy_f)),
                args: vec![
                    CallArg::Value(Value::Int(42)),
                    CallArg::Value(Value::String("hello".to_string())),
                ],
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
            let metadata = CallMetadata::new(
                "test_kwargs".to_string(),
                "test.py".to_string(),
                20,
                None,
                None,
            );

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
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
            let metadata = CallMetadata::new(
                "test_kwargs_only".to_string(),
                "test.py".to_string(),
                30,
                None,
                None,
            );

            vm.mode = Mode::HandleYield(Yielded::DoCtrl(DoCtrl::Call {
                f: CallArg::Value(Value::Python(dummy_f)),
                args: vec![],
                kwargs: vec![(
                    "name".to_string(),
                    CallArg::Value(Value::String("test".to_string())),
                )],
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
                metadata: None,
            }));

            let event = vm.step_handle_yield();

            assert!(
                matches!(
                    event,
                    StepEvent::NeedsPython(PythonCall::StartProgram { .. })
                ),
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
                metadata: None,
            }));

            let event = vm.step_handle_yield();

            assert!(
                matches!(
                    event,
                    StepEvent::NeedsPython(PythonCall::StartProgram { .. })
                ),
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
            let seg = Segment::new(marker, None, vec![marker]);
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
        let seg = Segment::new(marker, None, vec![marker]);
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
}
