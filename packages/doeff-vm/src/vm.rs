//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use pyo3::exceptions::{PyBaseException, PyException as PyStdException};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::arena::SegmentArena;
use crate::ast_stream::{ASTStream, ASTStreamRef, ASTStreamStep, PythonGeneratorStream};
use crate::capture::{
    ActiveChainEntry, CaptureEvent, DelegationEntry, DispatchAction, EffectCreationSite,
    EffectResult, FrameId, HandlerAction, HandlerDispatchEntry, HandlerKind, HandlerSnapshotEntry,
    HandlerStatus, TraceEntry, TraceFrame, TraceHop,
};
use crate::continuation::Continuation;
use crate::do_ctrl::{CallArg, DoCtrl, InterceptMode};
use crate::doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
use crate::driver::{Mode, PyException, StepEvent};
use crate::effect::{
    DispatchEffect, PyExecutionContext, PyGetExecutionContext, dispatch_ref_as_python,
    make_execution_context_object, make_get_execution_context_effect,
};
#[cfg(test)]
use crate::effect::{Effect, PySpawn};
use crate::error::VMError;
use crate::frame::{CallMetadata, Frame};
use crate::handler::{Handler, HandlerEntry, RustProgramInvocation};
use crate::ids::{CallbackId, ContId, DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::pyvm::{
    DoExprTag, PyDoCtrlBase, PyDoExprBase, PyEffectBase, PyPure, classify_yielded_for_vm,
    doctrl_to_pyexpr_for_vm,
};
use crate::segment::Segment;
use crate::value::Value;

pub use crate::dispatch::DispatchContext;
pub use crate::rust_store::RustStore;

pub type Callback = Box<dyn FnOnce(Value, &mut VM) -> Mode + Send + Sync>;
static NEXT_RUN_TOKEN: AtomicU64 = AtomicU64::new(1);

#[derive(Debug)]
struct RustProgramStream {
    program: crate::handler::ASTStreamProgramRef,
}

impl ASTStream for RustProgramStream {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        Python::attach(|_py| {
            let mut guard = self.program.lock().expect("Rust program lock poisoned");
            guard.resume(value, store)
        })
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        Python::attach(|_py| {
            let mut guard = self.program.lock().expect("Rust program lock poisoned");
            guard.throw(exc, store)
        })
    }
}

fn rust_program_as_stream(program: crate::handler::ASTStreamProgramRef) -> ASTStreamRef {
    Arc::new(std::sync::Mutex::new(
        Box::new(RustProgramStream { program }) as Box<dyn ASTStream>,
    ))
}

const EXECUTION_CONTEXT_ATTR: &str = "doeff_execution_context";
const MISSING_UNKNOWN: &str = "[MISSING] <unknown>";
const MISSING_SUB_PROGRAM: &str = "[MISSING] <sub_program>";
const MISSING_TARGET: &str = "[MISSING] <target>";
const MISSING_EXCEPTION: &str = "[MISSING] <exception>";
const MISSING_EXCEPTION_TYPE: &str = "[MISSING] Exception";
const MISSING_NONE_REPR: &str = "[MISSING] None";

#[derive(Debug, Clone, Copy)]
enum GenErrorSite {
    EvalExpr,
    CallFuncReturn,
    ExpandReturnHandler,
    ExpandReturnProgram,
    StepUserGeneratorConverted,
    StepUserGeneratorDirect,
    RustProgramContinuation,
    AsyncEscape,
}

impl GenErrorSite {
    fn allows_error_conversion(self) -> bool {
        matches!(
            self,
            GenErrorSite::EvalExpr
                | GenErrorSite::CallFuncReturn
                | GenErrorSite::ExpandReturnProgram
                | GenErrorSite::StepUserGeneratorConverted
        )
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

#[derive(Debug, Clone)]
pub struct TraceEvent {
    pub step: u64,
    pub event: String,
    pub mode: String,
    pub pending: String,
    pub dispatch_depth: usize,
    pub result: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ModeFormatVerbosity {
    Compact,
    Verbose,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ContinuationActivationKind {
    Resume,
    Transfer,
}

impl ContinuationActivationKind {
    fn unstarted_error_message(self) -> &'static str {
        match self {
            ContinuationActivationKind::Resume => {
                "Resume on unstarted continuation; use ResumeContinuation"
            }
            ContinuationActivationKind::Transfer => {
                "Transfer on unstarted continuation; use ResumeContinuation"
            }
        }
    }

    fn handler_action(self, value_repr: Option<String>) -> HandlerAction {
        match self {
            ContinuationActivationKind::Resume => HandlerAction::Resumed { value_repr },
            ContinuationActivationKind::Transfer => HandlerAction::Transferred { value_repr },
        }
    }

    fn is_transferred(self) -> bool {
        matches!(self, ContinuationActivationKind::Transfer)
    }

    fn caller_segment(self, current_segment: Option<SegmentId>) -> Option<SegmentId> {
        match self {
            ContinuationActivationKind::Resume => current_segment,
            ContinuationActivationKind::Transfer => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ForwardKind {
    Delegate,
    Pass,
}

impl ForwardKind {
    fn outside_dispatch_error(self) -> &'static str {
        match self {
            ForwardKind::Delegate => "Delegate called outside of dispatch context",
            ForwardKind::Pass => "Pass called outside of dispatch context",
        }
    }

    fn missing_handler_context(self) -> &'static str {
        match self {
            ForwardKind::Delegate => "handle_delegate",
            ForwardKind::Pass => "handle_pass",
        }
    }
}

#[derive(Clone)]
struct ActiveChainFrameState {
    frame_id: FrameId,
    function_name: String,
    source_file: String,
    source_line: u32,
    sub_program_repr: String,
}

#[derive(Clone)]
struct ActiveChainDispatchState {
    function_name: Option<String>,
    source_file: Option<String>,
    source_line: Option<u32>,
    effect_repr: String,
    is_execution_context_effect: bool,
    handler_stack: Vec<HandlerDispatchEntry>,
    result: EffectResult,
}

struct ActiveChainAssemblyState {
    frame_stack: Vec<ActiveChainFrameState>,
    dispatches: HashMap<DispatchId, ActiveChainDispatchState>,
    frame_dispatch: HashMap<FrameId, DispatchId>,
    transfer_targets: HashMap<DispatchId, String>,
}

#[derive(Clone)]
pub struct InterceptorEntry {
    interceptor: PyShared,
    types: PyShared,
    mode: InterceptMode,
    metadata: CallMetadata,
}

impl ActiveChainAssemblyState {
    fn new() -> Self {
        Self {
            frame_stack: Vec::new(),
            dispatches: HashMap::new(),
            frame_dispatch: HashMap::new(),
            transfer_targets: HashMap::new(),
        }
    }
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
    pub interceptors: HashMap<Marker, InterceptorEntry>,
    pub interceptor_callbacks: HashMap<CallbackId, Marker>,
    pub interceptor_call_metadata: HashMap<CallbackId, CallMetadata>,
    pub interceptor_eval_callbacks: HashSet<CallbackId>,
    pub interceptor_eval_depth: usize,
    pub interceptor_skip_stack: Vec<Marker>,
    pub rust_store: RustStore,
    pub py_store: Option<PyStore>,
    pub current_segment: Option<SegmentId>,
    pub mode: Mode,
    pub pending_error_context: Option<PyException>,
    pub pending_python: Option<PendingPython>,
    pub debug: DebugConfig,
    pub step_counter: u64,
    pub trace_enabled: bool,
    pub trace_events: Vec<TraceEvent>,
    pub capture_log: Vec<CaptureEvent>,
    pub continuation_registry: HashMap<ContId, Continuation>,
    pub active_run_token: Option<u64>,
}

#[path = "vm_debug.rs"]
mod vm_debug;
#[path = "vm_dispatch.rs"]
mod vm_dispatch;
#[path = "vm_interceptor.rs"]
mod vm_interceptor;
#[path = "vm_trace.rs"]
mod vm_trace;

impl VM {
    pub fn new() -> Self {
        VM {
            segments: SegmentArena::new(),
            dispatch_stack: Vec::new(),
            callbacks: HashMap::new(),
            consumed_cont_ids: HashSet::new(),
            handlers: HashMap::new(),
            interceptors: HashMap::new(),
            interceptor_callbacks: HashMap::new(),
            interceptor_call_metadata: HashMap::new(),
            interceptor_eval_callbacks: HashSet::new(),
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
            rust_store: RustStore::new(),
            py_store: None,
            current_segment: None,
            mode: Mode::Deliver(Value::Unit),
            pending_error_context: None,
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
        self.interceptors.clear();
        self.interceptor_callbacks.clear();
        self.interceptor_call_metadata.clear();
        self.interceptor_eval_callbacks.clear();
        self.interceptor_eval_depth = 0;
        self.interceptor_skip_stack.clear();
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
            entry.handler.on_run_end(run_token);
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
        self.mode = Mode::HandleYield(DoCtrl::Eval {
            expr,
            handlers,
            metadata: None,
        });
        StepEvent::Continue
    }

    fn invoke_rust_program(&mut self, invocation: RustProgramInvocation) -> StepEvent {
        let program = invocation
            .factory
            .create_program_for_run(self.current_run_token());
        let stream = rust_program_as_stream(program.clone());
        let step = {
            let mut guard = program.lock().expect("Rust program lock poisoned");
            Python::attach(|py| {
                guard.start(
                    py,
                    *invocation.effect,
                    invocation.continuation,
                    &mut self.rust_store,
                )
            })
        };
        self.apply_stream_step(step, stream, None)
    }

    fn evaluate(&mut self, ir_node: DoCtrl) -> StepEvent {
        self.mode = Mode::HandleYield(ir_node);
        self.step_handle_yield()
    }

    fn merged_metadata_from_doeff(
        inherited: Option<CallMetadata>,
        function_name: String,
        source_file: String,
        source_line: u32,
    ) -> Option<CallMetadata> {
        match inherited {
            Some(metadata) => Some(metadata),
            None => Some(CallMetadata::new(
                function_name,
                source_file,
                source_line,
                None,
                None,
            )),
        }
    }

    fn extract_doeff_generator(
        value: Py<PyAny>,
        inherited_metadata: Option<CallMetadata>,
        context: &str,
    ) -> Result<(ASTStreamRef, Option<CallMetadata>), PyException> {
        Python::attach(|py| {
            let bound = value.bind(py);
            let wrapped: PyRef<'_, DoeffGenerator> = bound.extract().map_err(|_| {
                let ty = bound
                    .get_type()
                    .name()
                    .map(|n| n.to_string())
                    .unwrap_or_else(|_| MISSING_UNKNOWN.to_string());
                PyException::type_error(format!("{context}: expected DoeffGenerator, got {ty}"))
            })?;

            if !wrapped.get_frame.bind(py).is_callable() {
                return Err(PyException::type_error(format!(
                    "{context}: DoeffGenerator.get_frame must be callable"
                )));
            }

            let stream = Arc::new(std::sync::Mutex::new(Box::new(PythonGeneratorStream::new(
                PyShared::new(wrapped.generator.clone_ref(py)),
                PyShared::new(wrapped.get_frame.clone_ref(py)),
            )) as Box<dyn ASTStream>));
            Ok((
                stream,
                Self::merged_metadata_from_doeff(
                    inherited_metadata,
                    wrapped.factory_function_name().to_string(),
                    wrapped.factory_source_file().to_string(),
                    wrapped.factory_source_line(),
                ),
            ))
        })
    }

    fn is_execution_context_effect(effect: &DispatchEffect) -> bool {
        let Some(obj) = dispatch_ref_as_python(effect) else {
            return false;
        };
        Python::attach(|py| {
            obj.bind(py)
                .extract::<PyRef<'_, PyGetExecutionContext>>()
                .is_ok()
        })
    }

    fn dispatch_supports_error_context_conversion(&self, dispatch_id: DispatchId) -> bool {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .is_some_and(|ctx| ctx.supports_error_context_conversion)
    }

    fn effect_creation_site_from_continuation(k: &Continuation) -> Option<EffectCreationSite> {
        let (_, function_name, source_file, source_line) = Self::effect_site_from_continuation(k)?;
        Some(EffectCreationSite {
            function_name,
            source_file,
            source_line,
        })
    }

    fn handler_trace_info(handler: &Handler) -> (String, HandlerKind, Option<String>, Option<u32>) {
        let info = handler.handler_debug_info();
        let kind = if handler.py_identity().is_some() {
            HandlerKind::Python
        } else {
            HandlerKind::RustBuiltin
        };
        (info.name, kind, info.file, info.line)
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

    fn dispatch_uses_user_continuation_stream(
        &self,
        dispatch_id: DispatchId,
        stream: &ASTStreamRef,
    ) -> bool {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .is_some_and(|ctx| {
                ctx.k_user.frames_snapshot.iter().any(|frame| match frame {
                    Frame::Program {
                        stream: snapshot_stream,
                        ..
                    } => Arc::ptr_eq(snapshot_stream, stream),
                    _ => false,
                })
            })
    }

    fn active_error_dispatch_original_exception(&self) -> Option<PyException> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| !ctx.completed && ctx.original_exception.is_some())
            .and_then(|ctx| ctx.original_exception.clone())
    }

    fn original_exception_for_dispatch(&self, dispatch_id: DispatchId) -> Option<PyException> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .and_then(|ctx| ctx.original_exception.clone())
    }

    fn same_materialized_exception(lhs: &PyException, rhs: &PyException) -> bool {
        match (lhs, rhs) {
            (
                PyException::Materialized {
                    exc_value: lhs_value,
                    ..
                },
                PyException::Materialized {
                    exc_value: rhs_value,
                    ..
                },
            ) => Python::attach(|py| lhs_value.bind(py).as_ptr() == rhs_value.bind(py).as_ptr()),
            _ => false,
        }
    }

    fn set_exception_cause(effect_err: &PyException, cause: &PyException) {
        if Self::same_materialized_exception(effect_err, cause) {
            return;
        }
        let PyException::Materialized { exc_value, .. } = effect_err else {
            return;
        };

        Python::attach(|py| {
            let _ = exc_value
                .bind(py)
                .setattr("__cause__", cause.value_clone_ref(py));
        });
    }

    fn is_base_exception_not_exception(exception: &PyException) -> bool {
        let PyException::Materialized { exc_value, .. } = exception else {
            return false;
        };
        Python::attach(|py| {
            let bound = exc_value.bind(py);
            bound.is_instance_of::<PyBaseException>() && !bound.is_instance_of::<PyStdException>()
        })
    }

    fn mode_after_generror(
        &mut self,
        site: GenErrorSite,
        exception: PyException,
        conversion_hint: bool,
    ) -> Mode {
        let active_dispatch_id = self.current_active_handler_dispatch_id();
        let allow_handler_context_conversion = conversion_hint
            || active_dispatch_id.is_some_and(|dispatch_id| {
                self.dispatch_supports_error_context_conversion(dispatch_id)
                    && matches!(
                        site,
                        GenErrorSite::RustProgramContinuation
                            | GenErrorSite::StepUserGeneratorDirect
                    )
            });
        let in_get_execution_context_dispatch = active_dispatch_id
            .and_then(|dispatch_id| {
                self.dispatch_stack
                    .iter()
                    .rev()
                    .find(|ctx| ctx.dispatch_id == dispatch_id)
            })
            .is_some_and(|ctx| ctx.is_execution_context_effect);

        if !site.allows_error_conversion() && !allow_handler_context_conversion {
            if let Some(original) = self.active_error_dispatch_original_exception() {
                Self::set_exception_cause(&exception, &original);
            }
            return Mode::Throw(exception);
        }

        if Self::is_base_exception_not_exception(&exception) {
            return Mode::Throw(exception);
        }

        if let Some(original) = self.active_error_dispatch_original_exception() {
            if !allow_handler_context_conversion || in_get_execution_context_dispatch {
                Self::set_exception_cause(&exception, &original);
                return Mode::Throw(exception);
            }
        }

        match make_get_execution_context_effect() {
            Ok(effect) => {
                self.pending_error_context = Some(exception.clone());
                Mode::HandleYield(DoCtrl::Perform { effect })
            }
            Err(_) => Mode::Throw(exception),
        }
    }

    fn stream_debug_location(stream: &ASTStreamRef) -> Option<crate::ast_stream::StreamLocation> {
        let guard = stream.lock().expect("ASTStream lock poisoned");
        guard.debug_location()
    }

    fn resume_location_from_frames(frames: &[Frame]) -> Option<(String, String, u32)> {
        for frame in frames.iter().rev() {
            if let Frame::Program {
                stream,
                metadata: Some(metadata),
            } = frame
            {
                if let Some(location) = Self::stream_debug_location(stream) {
                    return Some((
                        metadata.function_name.clone(),
                        location.source_file,
                        location.source_line,
                    ));
                }
                return Some((
                    metadata.function_name.clone(),
                    metadata.source_file.clone(),
                    metadata.source_line,
                ));
            }
        }
        None
    }

    fn continuation_resume_location(k: &Continuation) -> Option<(String, String, u32)> {
        Self::resume_location_from_frames(k.frames_snapshot.as_ref())
    }

    fn is_internal_source_file(source_file: &str) -> bool {
        let normalized = source_file.replace('\\', "/").to_lowercase();
        normalized == "_effect_wrap" || normalized.contains("/doeff/")
    }

    fn effect_site_from_continuation(k: &Continuation) -> Option<(FrameId, String, String, u32)> {
        let mut fallback: Option<(FrameId, String, String, u32)> = None;

        for frame in k.frames_snapshot.iter().rev() {
            if let Frame::Program {
                stream,
                metadata: Some(metadata),
            } = frame
            {
                let fallback_candidate = (
                    metadata.frame_id as FrameId,
                    metadata.function_name.clone(),
                    metadata.source_file.clone(),
                    metadata.source_line,
                );
                let candidate = match Self::stream_debug_location(stream) {
                    Some(location) => (
                        metadata.frame_id as FrameId,
                        metadata.function_name.clone(),
                        location.source_file,
                        location.source_line,
                    ),
                    None => fallback_candidate,
                };

                if fallback.is_none() {
                    fallback = Some(candidate.clone());
                }
                if !Self::is_internal_source_file(&candidate.2) {
                    return Some(candidate);
                }
            }
        }

        fallback
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
                            self.segments.reparent_children(seg_id, Some(caller_id));
                            self.current_segment = Some(caller_id);
                            self.mode = Mode::Throw(exc);
                            self.segments.free(seg_id);
                            return StepEvent::Continue;
                        } else {
                            self.finalize_active_dispatches_as_threw(&exc);
                            let trace = self.assemble_trace();
                            let active_chain = self.assemble_active_chain(&exc);
                            self.segments.reparent_children(seg_id, None);
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
                        if let Some(metadata) = self.interceptor_call_metadata.remove(&cb) {
                            self.maybe_emit_frame_exited(&metadata);
                        }
                        if self.interceptor_eval_callbacks.remove(&cb) {
                            self.interceptor_eval_depth =
                                self.interceptor_eval_depth.saturating_sub(1);
                        }
                        self.interceptor_callbacks.remove(&cb);
                        self.mode = callback(value, self);
                        StepEvent::Continue
                    }
                    Mode::Throw(exc) => {
                        if let Some(metadata) = self.interceptor_call_metadata.remove(&cb) {
                            self.maybe_emit_frame_exited(&metadata);
                        }
                        if self.interceptor_eval_callbacks.remove(&cb) {
                            self.interceptor_eval_depth =
                                self.interceptor_eval_depth.saturating_sub(1);
                        }
                        if let Some(marker) = self.interceptor_callbacks.remove(&cb) {
                            self.pop_interceptor_skip(marker);
                        }
                        self.mode = Mode::Throw(exc);
                        StepEvent::Continue
                    }
                    _ => unreachable!(),
                }
            }

            Frame::Program { stream, metadata } => {
                let step = {
                    let mut guard = stream.lock().expect("ASTStream lock poisoned");
                    match mode {
                        Mode::Deliver(value) => guard.resume(value, &mut self.rust_store),
                        Mode::Throw(exc) => guard.throw(exc, &mut self.rust_store),
                        _ => unreachable!(),
                    }
                };
                self.apply_stream_step(step, stream, metadata)
            }
        }
    }

    fn apply_stream_step(
        &mut self,
        step: ASTStreamStep,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        match step {
            ASTStreamStep::Yield(yielded) => self.handle_stream_yield(yielded, stream, metadata),
            ASTStreamStep::Return(value) => {
                if let Some(ref m) = metadata {
                    self.maybe_emit_frame_exited(m);
                }
                self.handle_handler_return(value)
            }
            ASTStreamStep::Throw(exc) => {
                if let Some(original) = self.active_error_dispatch_original_exception() {
                    Self::set_exception_cause(&exc, &original);
                }
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
            ASTStreamStep::NeedsPython(call) => {
                if matches!(
                    &call,
                    PythonCall::GenNext | PythonCall::GenSend { .. } | PythonCall::GenThrow { .. }
                ) {
                    self.pending_python =
                        Some(PendingPython::StepUserGenerator { stream, metadata });
                    return StepEvent::NeedsPython(call);
                }

                let Some(seg) = self.current_segment_mut() else {
                    return StepEvent::Error(VMError::internal(
                        "current_segment_mut() returned None in apply_stream_step \
                         (NeedsPython rust continuation)",
                    ));
                };
                seg.push_frame(Frame::Program { stream, metadata });
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

    fn handle_stream_yield(
        &mut self,
        yielded: DoCtrl,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let chain = Arc::new(self.current_interceptor_chain());
        self.mode = self.continue_interceptor_chain_mode(yielded, stream, metadata, chain, 0);
        StepEvent::Continue
    }

    fn finalize_stream_yield_mode(
        &mut self,
        yielded: DoCtrl,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
    ) -> Mode {
        // Terminal DoCtrl variants (Transfer, TransferThrow, Pass) transfer control
        // elsewhere — the handler is done and no value flows back. Do NOT re-push
        // the Program frame for those. Resume and ResumeThrow are non-terminal.
        let is_terminal = matches!(
            &yielded,
            DoCtrl::Transfer { .. } | DoCtrl::TransferThrow { .. } | DoCtrl::Pass { .. }
        );
        if !is_terminal {
            match self.current_segment_mut() {
                Some(seg) => seg.push_frame(Frame::Program { stream, metadata }),
                None => {
                    return Mode::Throw(PyException::runtime_error(
                        "current_segment_mut() returned None in apply_stream_step \
                         (Yield non-terminal)",
                    ));
                }
            }
        }
        Mode::HandleYield(yielded)
    }

    fn step_handle_yield(&mut self) -> StepEvent {
        // Take mode by move — eliminates DoCtrl clone containing Py<PyAny> values (D1 Phase 1).
        let yielded = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
            Mode::HandleYield(y) => y,
            other => {
                self.mode = other;
                return StepEvent::Error(VMError::internal("invalid mode for handle_yield"));
            }
        };

        // Spec: Drop completed dispatches before inspecting handler context.
        self.lazy_pop_completed();
        match yielded {
            DoCtrl::Pure { value } => self.handle_yield_pure(value),
            DoCtrl::Map {
                source,
                mapper,
                mapper_meta,
            } => self.handle_yield_map(source, mapper, mapper_meta),
            DoCtrl::FlatMap {
                source,
                binder,
                binder_meta,
            } => self.handle_yield_flat_map(source, binder, binder_meta),
            DoCtrl::Perform { effect } => self.handle_yield_effect(effect),
            DoCtrl::Resume {
                continuation,
                value,
            } => self.handle_yield_resume(continuation, value),
            DoCtrl::Transfer {
                continuation,
                value,
            } => self.handle_yield_transfer(continuation, value),
            DoCtrl::TransferThrow {
                continuation,
                exception,
            } => self.handle_yield_transfer_throw(continuation, exception),
            DoCtrl::ResumeThrow {
                continuation,
                exception,
            } => self.handle_yield_resume_throw(continuation, exception),
            DoCtrl::WithHandler {
                handler,
                expr,
                py_identity,
            } => self.handle_yield_with_handler(handler, expr, py_identity),
            DoCtrl::WithIntercept {
                interceptor,
                expr,
                types,
                mode,
                metadata,
            } => self.handle_yield_with_intercept(interceptor, expr, types, mode, metadata),
            DoCtrl::Delegate { effect } => self.handle_yield_delegate(effect),
            DoCtrl::Pass { effect } => self.handle_yield_pass(effect),
            DoCtrl::GetContinuation => self.handle_yield_get_continuation(),
            DoCtrl::GetHandlers => self.handle_yield_get_handlers(),
            DoCtrl::GetTraceback { continuation } => self.handle_yield_get_traceback(continuation),
            DoCtrl::CreateContinuation {
                expr,
                handlers,
                handler_identities,
            } => self.handle_yield_create_continuation(expr, handlers, handler_identities),
            DoCtrl::ResumeContinuation {
                continuation,
                value,
            } => self.handle_yield_resume_continuation(continuation, value),
            DoCtrl::PythonAsyncSyntaxEscape { action } => {
                self.handle_yield_python_async_syntax_escape(action)
            }
            // PendingPython::CallFuncReturn is set in handle_yield_apply.
            DoCtrl::Apply {
                f,
                args,
                kwargs,
                metadata,
            } => self.handle_yield_apply(f, args, kwargs, metadata),
            // PendingPython::ExpandReturn is set in handle_yield_expand.
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                metadata,
            } => self.handle_yield_expand(factory, args, kwargs, metadata),
            DoCtrl::Eval {
                expr,
                handlers,
                metadata,
            } => self.handle_yield_eval(expr, handlers, metadata),
            DoCtrl::GetCallStack => self.handle_yield_get_call_stack(),
            DoCtrl::GetTrace => self.handle_yield_get_trace(),
        }
    }

    fn handle_yield_pure(&mut self, value: Value) -> StepEvent {
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    fn handle_yield_map(
        &mut self,
        source: PyShared,
        mapper: PyShared,
        mapper_meta: CallMetadata,
    ) -> StepEvent {
        self.handle_map(source, mapper, mapper_meta)
    }

    fn handle_yield_flat_map(
        &mut self,
        source: PyShared,
        binder: PyShared,
        binder_meta: CallMetadata,
    ) -> StepEvent {
        self.handle_flat_map(source, binder, binder_meta)
    }

    fn handle_yield_effect(&mut self, effect: DispatchEffect) -> StepEvent {
        match self.start_dispatch(effect) {
            Ok(event) => event,
            Err(e) => StepEvent::Error(e),
        }
    }

    fn handle_yield_resume(&mut self, continuation: Continuation, value: Value) -> StepEvent {
        self.handle_resume(continuation, value)
    }

    fn handle_yield_transfer(&mut self, continuation: Continuation, value: Value) -> StepEvent {
        self.handle_transfer(continuation, value)
    }

    fn handle_yield_transfer_throw(
        &mut self,
        continuation: Continuation,
        exception: PyException,
    ) -> StepEvent {
        self.handle_transfer_throw(continuation, exception)
    }

    fn handle_yield_resume_throw(
        &mut self,
        continuation: Continuation,
        exception: PyException,
    ) -> StepEvent {
        self.handle_transfer_throw_non_terminal(continuation, exception)
    }

    fn handle_yield_with_handler(
        &mut self,
        handler: Handler,
        expr: Py<PyAny>,
        py_identity: Option<PyShared>,
    ) -> StepEvent {
        self.handle_with_handler(handler, expr, py_identity)
    }

    fn handle_yield_delegate(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_delegate(effect)
    }

    fn handle_yield_pass(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_pass(effect)
    }

    fn handle_yield_get_continuation(&mut self) -> StepEvent {
        self.handle_get_continuation()
    }

    fn handle_yield_get_handlers(&mut self) -> StepEvent {
        self.handle_get_handlers()
    }

    fn handle_yield_get_traceback(&mut self, continuation: Continuation) -> StepEvent {
        self.handle_get_traceback(continuation)
    }

    fn handle_yield_create_continuation(
        &mut self,
        expr: PyShared,
        handlers: Vec<Handler>,
        handler_identities: Vec<Option<PyShared>>,
    ) -> StepEvent {
        self.handle_create_continuation(expr, handlers, handler_identities)
    }

    fn handle_yield_resume_continuation(
        &mut self,
        continuation: Continuation,
        value: Value,
    ) -> StepEvent {
        self.handle_resume_continuation(continuation, value)
    }

    fn handle_yield_python_async_syntax_escape(&mut self, action: Py<PyAny>) -> StepEvent {
        self.pending_python = Some(PendingPython::AsyncEscape);
        StepEvent::NeedsPython(PythonCall::CallAsync {
            func: PyShared::new(action),
            args: vec![],
        })
    }

    fn handle_yield_apply(
        &mut self,
        f: CallArg,
        args: Vec<CallArg>,
        kwargs: Vec<(String, CallArg)>,
        metadata: CallMetadata,
    ) -> StepEvent {
        if let CallArg::Expr(expr) = &f {
            let expr = expr.clone();
            return self.eval_then_reenter_call(
                expr,
                Box::new(move |resolved_f, _vm| {
                    Mode::HandleYield(DoCtrl::Apply {
                        f: CallArg::Value(resolved_f),
                        args,
                        kwargs,
                        metadata,
                    })
                }),
            );
        }

        if let Some((arg_idx, expr)) = Self::first_expr_arg(&args) {
            return self.eval_then_reenter_call(
                expr,
                Box::new(move |resolved_arg, _vm| {
                    let mut args = args;
                    args[arg_idx] = CallArg::Value(resolved_arg);
                    Mode::HandleYield(DoCtrl::Apply {
                        f,
                        args,
                        kwargs,
                        metadata,
                    })
                }),
            );
        }

        if let Some((kwargs_idx, expr)) = Self::first_expr_kwarg(&kwargs) {
            return self.eval_then_reenter_call(
                expr,
                Box::new(move |resolved_kwarg, _vm| {
                    let mut kwargs = kwargs;
                    kwargs[kwargs_idx].1 = CallArg::Value(resolved_kwarg);
                    Mode::HandleYield(DoCtrl::Apply {
                        f,
                        args,
                        kwargs,
                        metadata,
                    })
                }),
            );
        }

        let func = match f {
            CallArg::Value(Value::Python(func)) => PyShared::new(func),
            CallArg::Value(Value::PythonHandlerCallable(func)) => PyShared::new(func),
            CallArg::Value(Value::RustProgramInvocation(invocation)) => {
                return self.invoke_rust_program(invocation);
            }
            CallArg::Value(other) => {
                self.mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Apply f must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
            CallArg::Expr(_) => unreachable!(),
        };

        self.pending_python = Some(PendingPython::CallFuncReturn {
            metadata: Some(metadata),
        });
        StepEvent::NeedsPython(PythonCall::CallFunc {
            func,
            args: Self::collect_value_args(args),
            kwargs: Self::collect_value_kwargs(kwargs),
        })
    }

    fn handle_yield_expand(
        &mut self,
        factory: CallArg,
        args: Vec<CallArg>,
        kwargs: Vec<(String, CallArg)>,
        metadata: CallMetadata,
    ) -> StepEvent {
        if let CallArg::Expr(expr) = &factory {
            let expr = expr.clone();
            return self.eval_then_reenter_call(
                expr,
                Box::new(move |resolved_factory, _vm| {
                    Mode::HandleYield(DoCtrl::Expand {
                        factory: CallArg::Value(resolved_factory),
                        args,
                        kwargs,
                        metadata,
                    })
                }),
            );
        }

        if let Some((arg_idx, expr)) = Self::first_expr_arg(&args) {
            return self.eval_then_reenter_call(
                expr,
                Box::new(move |resolved_arg, _vm| {
                    let mut args = args;
                    args[arg_idx] = CallArg::Value(resolved_arg);
                    Mode::HandleYield(DoCtrl::Expand {
                        factory,
                        args,
                        kwargs,
                        metadata,
                    })
                }),
            );
        }

        if let Some((kwargs_idx, expr)) = Self::first_expr_kwarg(&kwargs) {
            return self.eval_then_reenter_call(
                expr,
                Box::new(move |resolved_kwarg, _vm| {
                    let mut kwargs = kwargs;
                    kwargs[kwargs_idx].1 = CallArg::Value(resolved_kwarg);
                    Mode::HandleYield(DoCtrl::Expand {
                        factory,
                        args,
                        kwargs,
                        metadata,
                    })
                }),
            );
        }

        let (func, handler_return) = match factory {
            CallArg::Value(Value::Python(factory)) => (PyShared::new(factory), false),
            CallArg::Value(Value::PythonHandlerCallable(factory)) => (PyShared::new(factory), true),
            CallArg::Value(Value::RustProgramInvocation(invocation)) => {
                return self.invoke_rust_program(invocation);
            }
            CallArg::Value(other) => {
                self.mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Expand factory must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
            CallArg::Expr(_) => unreachable!(),
        };

        self.pending_python = Some(PendingPython::ExpandReturn {
            metadata: Some(metadata),
            handler_return,
        });
        StepEvent::NeedsPython(PythonCall::CallFunc {
            func,
            args: Self::collect_value_args(args),
            kwargs: Self::collect_value_kwargs(kwargs),
        })
    }

    fn handle_yield_eval(
        &mut self,
        expr: PyShared,
        handlers: Vec<Handler>,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let cont = Continuation::create_unstarted_with_metadata(expr, handlers, metadata);
        self.handle_resume_continuation(cont, Value::None)
    }

    fn handle_yield_get_call_stack(&mut self) -> StepEvent {
        let mut stack = Vec::new();
        let mut seg_id = self.current_segment;
        while let Some(id) = seg_id {
            if let Some(seg) = self.segments.get(id) {
                for frame in seg.frames.iter().rev() {
                    if let Frame::Program {
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

    fn handle_yield_get_trace(&mut self) -> StepEvent {
        self.mode = Mode::Deliver(Value::Trace(self.assemble_trace()));
        StepEvent::Continue
    }

    fn first_expr_arg(args: &[CallArg]) -> Option<(usize, PyShared)> {
        let arg_idx = args
            .iter()
            .position(|arg| matches!(arg, CallArg::Expr(_)))?;
        let CallArg::Expr(expr) = &args[arg_idx] else {
            unreachable!();
        };
        Some((arg_idx, expr.clone()))
    }

    fn first_expr_kwarg(kwargs: &[(String, CallArg)]) -> Option<(usize, PyShared)> {
        let kwargs_idx = kwargs
            .iter()
            .position(|(_, value)| matches!(value, CallArg::Expr(_)))?;
        let CallArg::Expr(expr) = &kwargs[kwargs_idx].1 else {
            unreachable!();
        };
        Some((kwargs_idx, expr.clone()))
    }

    fn collect_value_args(args: Vec<CallArg>) -> Vec<Value> {
        let mut values = Vec::with_capacity(args.len());
        for arg in args {
            match arg {
                CallArg::Value(value) => values.push(value),
                CallArg::Expr(_) => unreachable!(),
            }
        }
        values
    }

    fn collect_value_kwargs(kwargs: Vec<(String, CallArg)>) -> Vec<(String, Value)> {
        let mut values = Vec::with_capacity(kwargs.len());
        for (key, value) in kwargs {
            match value {
                CallArg::Value(inner) => values.push((key, inner)),
                CallArg::Expr(_) => unreachable!(),
            }
        }
        values
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
                self.segments.reparent_children(seg_id, Some(caller_id));
                self.current_segment = Some(caller_id);
                self.segments.free(seg_id);
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            None => {
                self.segments.reparent_children(seg_id, None);
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

        match pending {
            PendingPython::EvalExpr { metadata } => {
                self.receive_eval_expr_result(metadata, outcome)
            }
            PendingPython::CallFuncReturn { metadata } => {
                self.receive_call_func_result(metadata, outcome)
            }
            PendingPython::ExpandReturn {
                metadata,
                handler_return,
            } => self.receive_expand_result(metadata, handler_return, outcome),
            PendingPython::StepUserGenerator { stream, metadata } => {
                self.receive_step_user_generator_result(stream, metadata, outcome)
            }
            PendingPython::RustProgramContinuation { marker, k } => {
                self.receive_rust_program_result(marker, k, outcome)
            }
            PendingPython::AsyncEscape => self.receive_async_escape_result(outcome),
        }
    }

    fn receive_eval_expr_result(
        &mut self,
        _metadata: Option<CallMetadata>,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::GenYield(yielded) => {
                self.mode = Mode::HandleYield(yielded);
            }
            PyCallOutcome::GenError(exception) => {
                self.mode = self.mode_after_generror(GenErrorSite::EvalExpr, exception, false);
            }
            PyCallOutcome::GenReturn(value) | PyCallOutcome::Value(value) => {
                self.mode = Mode::Deliver(value);
            }
        }
    }

    fn receive_call_func_result(
        &mut self,
        _metadata: Option<CallMetadata>,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::Value(value) => {
                self.mode = Mode::Deliver(value);
            }
            PyCallOutcome::GenError(exception) => {
                self.mode =
                    self.mode_after_generror(GenErrorSite::CallFuncReturn, exception, false);
            }
            _ => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_expand_result(
        &mut self,
        metadata: Option<CallMetadata>,
        handler_return: bool,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::Value(value) => {
                if handler_return {
                    self.receive_expand_handler_value(metadata, value);
                } else {
                    self.receive_expand_program_value(metadata, value);
                }
            }
            PyCallOutcome::GenError(exception) => {
                self.receive_expand_gen_error(handler_return, exception);
            }
            _ => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_expand_handler_value(&mut self, metadata: Option<CallMetadata>, value: Value) {
        match value {
            Value::Python(handler_gen) => {
                match Self::extract_doeff_generator(handler_gen, metadata, "ExpandReturn(handler)")
                {
                    Ok((stream, metadata)) => {
                        let handler_return_cb = self.register_callback(Box::new(|value, vm| {
                            let _ = vm.handle_handler_return(value);
                            std::mem::replace(&mut vm.mode, Mode::Deliver(Value::Unit))
                        }));
                        let Some(seg) = self.current_segment_mut() else {
                            self.mode = Mode::Throw(PyException::runtime_error(
                                "current_segment_mut() returned None in receive_python_result \
                                 ExpandReturn(handler)",
                            ));
                            return;
                        };
                        seg.push_frame(Frame::RustReturn {
                            cb: handler_return_cb,
                        });
                        seg.push_frame(Frame::Program { stream, metadata });
                        self.mode = Mode::Deliver(Value::Unit);
                    }
                    Err(exception) => {
                        self.mode = Mode::Throw(exception);
                    }
                }
            }
            other => {
                let _ = self.handle_handler_return(other);
            }
        }
    }

    fn receive_expand_program_value(&mut self, metadata: Option<CallMetadata>, value: Value) {
        match value {
            Value::Python(generator) => {
                match Self::extract_doeff_generator(generator, metadata, "ExpandReturn") {
                    Ok((stream, metadata)) => {
                        if let Some(ref m) = metadata {
                            self.maybe_emit_frame_entered(m);
                        }
                        let Some(seg) = self.current_segment_mut() else {
                            self.mode = Mode::Throw(PyException::runtime_error(
                                "current_segment_mut() returned None in receive_python_result \
                             ExpandReturn(program)",
                            ));
                            return;
                        };
                        seg.push_frame(Frame::Program { stream, metadata });
                        self.mode = Mode::Deliver(Value::Unit);
                    }
                    Err(exception) => {
                        self.mode = Mode::Throw(exception);
                    }
                }
            }
            other => {
                self.mode = Mode::Throw(PyException::type_error(format!(
                    "ExpandReturn: expected DoeffGenerator, got {other:?}"
                )));
            }
        }
    }

    fn receive_expand_gen_error(&mut self, handler_return: bool, exception: PyException) {
        if handler_return {
            if let Some(dispatch_id) = self
                .dispatch_stack
                .last()
                .filter(|ctx| !ctx.completed)
                .map(|ctx| ctx.dispatch_id)
            {
                if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                    Self::set_exception_cause(&exception, &original);
                }
                self.maybe_emit_handler_threw_for_dispatch(dispatch_id, &exception);
                self.mark_dispatch_threw(dispatch_id);
            }
            self.mode =
                self.mode_after_generror(GenErrorSite::ExpandReturnHandler, exception, false);
            return;
        }

        self.mode = self.mode_after_generror(GenErrorSite::ExpandReturnProgram, exception, false);
    }

    fn receive_step_user_generator_result(
        &mut self,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::GenYield(yielded) => {
                if self.current_segment.is_none() {
                    self.mode = Mode::Throw(PyException::runtime_error(
                        "current_segment_mut() returned None in receive_python_result \
                         StepUserGenerator::GenYield",
                    ));
                    return;
                }
                let _ = self.handle_stream_yield(yielded, stream, metadata);
            }
            PyCallOutcome::GenReturn(value) => {
                if let Some(ref m) = metadata {
                    self.maybe_emit_frame_exited(m);
                }
                self.mode = Mode::Deliver(value);
            }
            PyCallOutcome::GenError(exception) => {
                let mut site = GenErrorSite::StepUserGeneratorDirect;
                if let Some(dispatch_id) = self.current_active_handler_dispatch_id() {
                    if self.dispatch_uses_user_continuation_stream(dispatch_id, &stream) {
                        self.mark_dispatch_completed(dispatch_id);
                        site = GenErrorSite::StepUserGeneratorConverted;
                    } else {
                        if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                            Self::set_exception_cause(&exception, &original);
                        }
                        self.maybe_emit_handler_threw_for_dispatch(dispatch_id, &exception);
                        self.mark_dispatch_threw(dispatch_id);
                    }
                }
                self.mode = self.mode_after_generror(site, exception, false);
            }
            PyCallOutcome::Value(_) => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_rust_program_result(
        &mut self,
        _marker: Marker,
        _continuation: Continuation,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::Value(result) => {
                self.mode = Mode::Deliver(result);
            }
            PyCallOutcome::GenError(exception) => {
                self.mode = self.mode_after_generror(
                    GenErrorSite::RustProgramContinuation,
                    exception,
                    false,
                );
            }
            _ => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_async_escape_result(&mut self, outcome: PyCallOutcome) {
        match outcome {
            PyCallOutcome::Value(result) => {
                self.mode = Mode::Deliver(result);
            }
            PyCallOutcome::GenError(exception) => {
                self.mode = self.mode_after_generror(GenErrorSite::AsyncEscape, exception, false);
            }
            _ => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_unexpected_outcome(&mut self) {
        self.mode = Mode::Throw(PyException::runtime_error(
            "unexpected pending/outcome combination in receive_python_result",
        ));
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

    fn record_continuation_activation(
        &mut self,
        kind: ContinuationActivationKind,
        k: &Continuation,
        value: &Value,
    ) {
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            {
                let value_repr = Self::value_repr(value);
                self.capture_log.push(CaptureEvent::HandlerCompleted {
                    dispatch_id,
                    handler_name: handler_name.clone(),
                    handler_index,
                    action: kind.handler_action(value_repr.clone()),
                });
                self.maybe_emit_resume_event(
                    dispatch_id,
                    handler_name,
                    value_repr,
                    k,
                    kind.is_transferred(),
                );
            }
        }
    }

    fn enter_continuation_segment(&mut self, k: &Continuation, caller: Option<SegmentId>) {
        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller,
            scope_chain: (*k.scope_chain).clone(),
            kind: crate::segment::SegmentKind::Normal,
        };
        let exec_seg_id = self.alloc_segment(exec_seg);
        self.current_segment = Some(exec_seg_id);
    }

    fn activate_continuation(
        &mut self,
        kind: ContinuationActivationKind,
        k: Continuation,
        value: Value,
    ) -> StepEvent {
        if !k.started {
            return self.throw_runtime_error(kind.unstarted_error_message());
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        self.mark_one_shot_consumed(k.cont_id);
        self.lazy_pop_completed();
        let error_dispatch = self.error_dispatch_for_continuation(&k);
        self.record_continuation_activation(kind, &k, &value);

        if let Some((dispatch_id, original_exception, terminal)) = error_dispatch {
            if terminal {
                self.mark_dispatch_completed(dispatch_id);
                let enriched_exception =
                    match Self::enrich_original_exception_with_context(original_exception, value) {
                        Ok(exception) => exception,
                        Err(effect_err) => effect_err,
                    };
                self.enter_continuation_segment(&k, kind.caller_segment(self.current_segment));
                self.mode = Mode::Throw(enriched_exception);
                return StepEvent::Continue;
            }
            self.check_dispatch_completion_after_activation(kind, &k, true);
        } else {
            self.check_dispatch_completion_after_activation(kind, &k, false);
        }

        self.enter_continuation_segment(&k, kind.caller_segment(self.current_segment));
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    fn handle_resume(&mut self, k: Continuation, value: Value) -> StepEvent {
        self.activate_continuation(ContinuationActivationKind::Resume, k, value)
    }

    fn handle_transfer(&mut self, k: Continuation, value: Value) -> StepEvent {
        self.activate_continuation(ContinuationActivationKind::Transfer, k, value)
    }

    fn activate_throw_continuation(
        &mut self,
        k: Continuation,
        exception: PyException,
        terminal_dispatch_completion: bool,
    ) -> StepEvent {
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
        let mut thrown_by_context_conversion_handler = self
            .current_active_handler_dispatch_id()
            .is_some_and(|dispatch_id| {
                self.dispatch_supports_error_context_conversion(dispatch_id)
            });
        if let Some(dispatch_id) = k.dispatch_id {
            thrown_by_context_conversion_handler =
                self.dispatch_supports_error_context_conversion(dispatch_id);
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
        if terminal_dispatch_completion {
            self.check_dispatch_completion(&k);
        } else {
            self.check_dispatch_completion_for_non_terminal_throw(&k);
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
        self.mode = if terminal_dispatch_completion && thrown_by_context_conversion_handler {
            self.mode_after_generror(
                GenErrorSite::RustProgramContinuation,
                exception,
                thrown_by_context_conversion_handler,
            )
        } else {
            Mode::Throw(exception)
        };
        StepEvent::Continue
    }

    fn handle_transfer_throw(&mut self, k: Continuation, exception: PyException) -> StepEvent {
        self.activate_throw_continuation(k, exception, true)
    }

    fn handle_transfer_throw_non_terminal(
        &mut self,
        k: Continuation,
        exception: PyException,
    ) -> StepEvent {
        self.activate_throw_continuation(k, exception, false)
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
                return StepEvent::Error(VMError::internal("no current segment for WithHandler"));
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

        let py_identity = explicit_py_identity.or_else(|| handler.py_identity());
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
        self.pending_python = Some(PendingPython::EvalExpr { metadata: None });
        StepEvent::NeedsPython(PythonCall::EvalExpr {
            expr: PyShared::new(program),
        })
    }

    fn clear_segment_frames(&mut self, segment_id: Option<SegmentId>) {
        if let Some(seg_id) = segment_id {
            if let Some(seg) = self.segments.get_mut(seg_id) {
                seg.frames.clear();
            }
        }
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

    fn handle_map(
        &mut self,
        source: PyShared,
        mapper: PyShared,
        mapper_meta: CallMetadata,
    ) -> StepEvent {
        let handlers = self.current_visible_handlers();
        let map_cb = self.register_callback(Box::new(move |value, _vm| {
            Mode::HandleYield(DoCtrl::Apply {
                f: CallArg::Value(Value::Python(mapper.into_inner())),
                args: vec![CallArg::Value(value)],
                kwargs: vec![],
                metadata: mapper_meta.clone(),
            })
        }));

        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("Map outside current segment"));
        };
        seg.push_frame(Frame::RustReturn { cb: map_cb });
        self.mode = Mode::HandleYield(DoCtrl::Eval {
            expr: source,
            handlers,
            metadata: None,
        });
        StepEvent::Continue
    }

    fn handle_flat_map(
        &mut self,
        source: PyShared,
        binder: PyShared,
        binder_meta: CallMetadata,
    ) -> StepEvent {
        let handlers = self.current_visible_handlers();
        let bind_result_cb =
            self.register_callback(Box::new(move |bound_value, _vm| Mode::Deliver(bound_value)));

        let bind_source_cb = self.register_callback(Box::new(move |value, vm| {
            let Some(seg) = vm.current_segment_mut() else {
                return Mode::Throw(PyException::runtime_error(
                    "flat_map binder callback outside current segment",
                ));
            };
            seg.push_frame(Frame::RustReturn { cb: bind_result_cb });
            Mode::HandleYield(DoCtrl::Expand {
                factory: CallArg::Value(Value::Python(binder.into_inner())),
                args: vec![CallArg::Value(value)],
                kwargs: vec![],
                metadata: binder_meta.clone(),
            })
        }));

        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("FlatMap outside current segment"));
        };
        seg.push_frame(Frame::RustReturn { cb: bind_source_cb });
        self.mode = Mode::HandleYield(DoCtrl::Eval {
            expr: source,
            handlers,
            metadata: None,
        });
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

    fn collect_traceback(continuation: &Continuation) -> Vec<TraceHop> {
        let mut hops = Vec::new();
        let mut current: Option<&Continuation> = Some(continuation);

        while let Some(cont) = current {
            let mut frames = Vec::new();
            for frame in cont.frames_snapshot.iter() {
                if let Frame::Program {
                    stream,
                    metadata: Some(metadata),
                } = frame
                {
                    let (source_file, source_line) = match Self::stream_debug_location(stream) {
                        Some(location) => (location.source_file, location.source_line),
                        None => (metadata.source_file.clone(), metadata.source_line),
                    };
                    frames.push(TraceFrame {
                        func_name: metadata.function_name.clone(),
                        source_file,
                        source_line,
                    });
                }
            }
            hops.push(TraceHop { frames });
            current = cont.parent.as_deref();
        }

        hops
    }

    fn handle_get_traceback(&mut self, continuation: Continuation) -> StepEvent {
        let Some(_top) = self.dispatch_stack.last() else {
            return StepEvent::Error(VMError::internal(
                "GetTraceback called outside of dispatch context",
            ));
        };
        let hops = Self::collect_traceback(&continuation);
        self.mode = Mode::Deliver(Value::Traceback(hops));
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
                return StepEvent::Error(VMError::internal("unstarted continuation has no program"));
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
        self.pending_python = Some(PendingPython::EvalExpr {
            metadata: start_metadata,
        });
        StepEvent::NeedsPython(PythonCall::EvalExpr { expr: program })
    }
}

impl Default for VM {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
#[path = "vm_tests.rs"]
mod vm_tests;
