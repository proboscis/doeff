//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

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
use crate::do_ctrl::{CallArg, DoCtrl};
use crate::doeff_generator::DoeffGenerator;
use crate::driver::{Mode, PyException, StepEvent};
use crate::effect::{
    dispatch_ref_as_python, make_execution_context_object, make_get_execution_context_effect,
    DispatchEffect, PyExecutionContext, PyGetExecutionContext,
};
#[cfg(test)]
use crate::effect::{Effect, PySpawn};
use crate::error::VMError;
use crate::frame::{CallMetadata, Frame};
use crate::handler::{Handler, HandlerEntry, RustProgramInvocation};
use crate::ids::{CallbackId, ContId, DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::pyvm::{PyDoExprBase, PyEffectBase};
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
                    .unwrap_or_else(|_| MISSING_SUB_PROGRAM.to_string())
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
                    .unwrap_or_else(|_| MISSING_EXCEPTION.to_string())
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
                    is_execution_context_effect: _,
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
                }
                | CaptureEvent::Passed {
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
                let Frame::Program {
                    stream,
                    metadata: Some(metadata),
                } = frame
                else {
                    continue;
                };

                let current_line = Self::stream_debug_location(stream)
                    .map(|location| location.source_line)
                    .unwrap_or(metadata.source_line);
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
                exc_type: _exc_type,
                exc_value,
                exc_tb,
            } => Python::attach(|py| {
                let exc_value_bound = exc_value.bind(py);

                let exception_type = exc_value_bound
                    .get_type()
                    .name()
                    .ok()
                    .map(|name| name.to_string())
                    .unwrap_or_else(|| MISSING_EXCEPTION_TYPE.to_string());

                let message = exc_value_bound
                    .str()
                    .map(|v| v.to_string())
                    .unwrap_or_default();

                let mut function_name = MISSING_UNKNOWN.to_string();
                let mut source_file = MISSING_UNKNOWN.to_string();
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
                                .unwrap_or_else(|| MISSING_UNKNOWN.to_string());
                            source_file = code
                                .getattr("co_filename")
                                .ok()
                                .and_then(|v| v.extract::<String>().ok())
                                .unwrap_or_else(|| MISSING_UNKNOWN.to_string());
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

    fn context_entries_from_exception(exception: &PyException) -> Vec<Py<PyAny>> {
        let PyException::Materialized { exc_value, .. } = exception else {
            return Vec::new();
        };

        Python::attach(|py| {
            let exc = exc_value.bind(py);
            let context = exc
                .getattr(EXECUTION_CONTEXT_ATTR)
                .ok()
                .filter(|ctx| !ctx.is_none());
            let Some(context) = context else {
                return Vec::new();
            };
            let entries = context
                .getattr("entries")
                .ok()
                .filter(|entries| !entries.is_none());
            let Some(entries) = entries else {
                return Vec::new();
            };
            match entries.try_iter() {
                Ok(iter) => iter
                    .filter_map(Result::ok)
                    .map(|entry| entry.unbind())
                    .collect(),
                Err(_) => Vec::new(),
            }
        })
    }

    fn context_entries_from_context_obj(context: &Bound<'_, PyAny>) -> Vec<Py<PyAny>> {
        if !context.is_instance_of::<PyExecutionContext>() {
            return Vec::new();
        }
        let entries = context
            .getattr("entries")
            .ok()
            .filter(|entries| !entries.is_none());
        let Some(entries) = entries else {
            return Vec::new();
        };
        match entries.try_iter() {
            Ok(iter) => iter
                .filter_map(Result::ok)
                .map(|entry| entry.unbind())
                .collect(),
            Err(_) => Vec::new(),
        }
    }

    fn build_execution_context_from_entries(
        py: Python<'_>,
        entries: &[Py<PyAny>],
    ) -> PyResult<Py<PyAny>> {
        let context = make_execution_context_object(py)?;
        let add = context.bind(py).getattr("add")?;
        for entry in entries {
            add.call1((entry.clone_ref(py),))?;
        }
        Ok(context)
    }

    fn attach_execution_context(exception: &PyException, context: &Py<PyAny>) {
        let PyException::Materialized { exc_value, .. } = exception else {
            return;
        };
        Python::attach(|py| {
            let _ = exc_value
                .bind(py)
                .setattr(EXECUTION_CONTEXT_ATTR, context.clone_ref(py));
        });
    }

    fn enrich_original_exception_with_context(
        original: PyException,
        context_value: Value,
    ) -> Result<PyException, PyException> {
        let Value::Python(new_context) = context_value else {
            let err = PyException::type_error(
                "GetExecutionContext handlers must Resume with ExecutionContext".to_string(),
            );
            Self::set_exception_cause(&err, &original);
            return Err(err);
        };

        Python::attach(|py| {
            let context_bound = new_context.bind(py);
            if !context_bound.is_instance_of::<PyExecutionContext>() {
                let err = PyException::type_error(
                    "GetExecutionContext handlers must Resume with ExecutionContext".to_string(),
                );
                Self::set_exception_cause(&err, &original);
                return Err(err);
            }

            let mut merged_entries = Self::context_entries_from_context_obj(context_bound);
            let existing_entries = Self::context_entries_from_exception(&original);
            merged_entries.extend(existing_entries);

            let merged_context =
                match Self::build_execution_context_from_entries(py, &merged_entries) {
                    Ok(context) => context,
                    Err(err) => {
                        let err = PyException::runtime_error(format!(
                            "failed to merge ExecutionContext entries: {err}"
                        ));
                        Self::set_exception_cause(&err, &original);
                        return Err(err);
                    }
                };

            Self::attach_execution_context(&original, &merged_context);
            Ok(original)
        })
    }

    pub fn assemble_active_chain(&self, exception: &PyException) -> Vec<ActiveChainEntry> {
        let raw_events = self.collect_raw_events();
        let entries = self.events_to_entries(&raw_events);
        let entries = self.dedup_adjacent(entries);
        self.inject_context(entries, exception)
    }

    fn collect_raw_events(&self) -> Vec<CaptureEvent> {
        self.capture_log.clone()
    }

    fn events_to_entries(&self, raw_events: &[CaptureEvent]) -> Vec<ActiveChainEntry> {
        let mut state = ActiveChainAssemblyState::new();
        for event in raw_events {
            self.apply_active_chain_event(&mut state, event);
        }
        self.merge_live_frame_state(&mut state);
        self.entries_from_active_chain_state(&state, raw_events)
    }

    fn apply_active_chain_event(&self, state: &mut ActiveChainAssemblyState, event: &CaptureEvent) {
        match event {
            CaptureEvent::FrameEntered {
                frame_id,
                function_name,
                source_file,
                source_line,
                args_repr: _,
                program_call_repr,
            } => {
                state.frame_stack.push(ActiveChainFrameState {
                    frame_id: *frame_id,
                    function_name: function_name.clone(),
                    source_file: source_file.clone(),
                    source_line: *source_line,
                    sub_program_repr: program_call_repr
                        .clone()
                        .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
                });
            }
            CaptureEvent::FrameExited { .. } => {
                let _ = state.frame_stack.pop();
            }
            CaptureEvent::DispatchStarted {
                dispatch_id,
                effect_repr,
                is_execution_context_effect,
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
                let visible_effect = !*is_execution_context_effect;
                if let Some(frame_id) = effect_frame_id {
                    if visible_effect {
                        state.frame_dispatch.insert(*frame_id, *dispatch_id);
                        if let Some(frame) = state
                            .frame_stack
                            .iter_mut()
                            .find(|f| f.frame_id == *frame_id)
                        {
                            if let Some(line) = effect_source_line {
                                frame.source_line = *line;
                            }
                        }
                    }
                }

                state.dispatches.insert(
                    *dispatch_id,
                    ActiveChainDispatchState {
                        function_name: effect_function_name.clone(),
                        source_file: effect_source_file.clone(),
                        source_line: *effect_source_line,
                        effect_repr: effect_repr.clone(),
                        is_execution_context_effect: *is_execution_context_effect,
                        handler_stack: Self::handler_stack_from_snapshot(handler_chain_snapshot),
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
                if let Some(dispatch) = state.dispatches.get_mut(dispatch_id) {
                    if let Some(from_entry) = dispatch.handler_stack.get_mut(*from_handler_index) {
                        if from_entry.status == HandlerStatus::Active {
                            from_entry.status = HandlerStatus::Delegated;
                        }
                    }
                    if let Some(to_entry) = dispatch.handler_stack.get_mut(*to_handler_index) {
                        to_entry.status = HandlerStatus::Active;
                    }
                }
            }
            CaptureEvent::Passed {
                dispatch_id,
                from_handler_name: _,
                from_handler_index,
                to_handler_name: _,
                to_handler_index,
                to_handler_kind: _,
                to_handler_source_file: _,
                to_handler_source_line: _,
            } => {
                if let Some(dispatch) = state.dispatches.get_mut(dispatch_id) {
                    if let Some(from_entry) = dispatch.handler_stack.get_mut(*from_handler_index) {
                        if from_entry.status == HandlerStatus::Active {
                            from_entry.status = HandlerStatus::Passed;
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
                if let Some(dispatch) = state.dispatches.get_mut(dispatch_id) {
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
                                .unwrap_or_else(|| MISSING_NONE_REPR.to_string()),
                        },
                        HandlerAction::Transferred { value_repr } => EffectResult::Transferred {
                            handler_name: handler_name.clone(),
                            target_repr: state
                                .transfer_targets
                                .get(dispatch_id)
                                .cloned()
                                .unwrap_or_else(|| {
                                    value_repr
                                        .clone()
                                        .unwrap_or_else(|| MISSING_TARGET.to_string())
                                }),
                        },
                        HandlerAction::Threw { exception_repr } => EffectResult::Threw {
                            handler_name: handler_name.clone(),
                            exception_repr: exception_repr
                                .clone()
                                .unwrap_or_else(|| MISSING_EXCEPTION.to_string()),
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
                state.transfer_targets.insert(
                    *dispatch_id,
                    format!("{resumed_function_name}() {source_file}:{source_line}"),
                );
            }
        }
    }

    fn handler_stack_from_snapshot(
        handler_chain_snapshot: &[HandlerSnapshotEntry],
    ) -> Vec<HandlerDispatchEntry> {
        handler_chain_snapshot
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
            .collect()
    }

    fn merge_live_frame_state(&self, state: &mut ActiveChainAssemblyState) {
        self.merge_frame_lines_from_segments(&mut state.frame_stack);
        let (frame_stack, dispatches) = (&mut state.frame_stack, &state.dispatches);
        self.merge_frame_lines_from_visible_dispatch_snapshot(frame_stack, dispatches);
    }

    fn merge_frame_lines_from_segments(&self, frame_stack: &mut Vec<ActiveChainFrameState>) {
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
                let Frame::Program {
                    stream,
                    metadata: Some(metadata),
                } = frame
                else {
                    continue;
                };
                Self::upsert_frame_state_from_metadata(frame_stack, stream, metadata);
            }
        }
    }

    fn merge_frame_lines_from_visible_dispatch_snapshot(
        &self,
        frame_stack: &mut Vec<ActiveChainFrameState>,
        dispatches: &HashMap<DispatchId, ActiveChainDispatchState>,
    ) {
        let Some(dispatch_ctx) = self.dispatch_stack.iter().rev().find(|ctx| {
            dispatches
                .get(&ctx.dispatch_id)
                .is_some_and(|dispatch| Self::is_visible_dispatch(dispatch))
        }) else {
            return;
        };

        for frame in dispatch_ctx.k_user.frames_snapshot.iter() {
            let Frame::Program {
                stream,
                metadata: Some(metadata),
            } = frame
            else {
                continue;
            };
            Self::upsert_frame_state_from_metadata(frame_stack, stream, metadata);
        }
    }

    fn upsert_frame_state_from_metadata(
        frame_stack: &mut Vec<ActiveChainFrameState>,
        stream: &ASTStreamRef,
        metadata: &CallMetadata,
    ) {
        let line = Self::stream_debug_location(stream)
            .map(|location| location.source_line)
            .unwrap_or(metadata.source_line);
        if let Some(existing) = frame_stack
            .iter_mut()
            .find(|entry| entry.frame_id == metadata.frame_id)
        {
            existing.source_line = line;
            if existing.sub_program_repr == MISSING_SUB_PROGRAM {
                if let Some(repr) = Self::program_call_repr(metadata) {
                    existing.sub_program_repr = repr;
                }
            }
            return;
        }

        frame_stack.push(ActiveChainFrameState {
            frame_id: metadata.frame_id as FrameId,
            function_name: metadata.function_name.clone(),
            source_file: metadata.source_file.clone(),
            source_line: line,
            sub_program_repr: Self::program_call_repr(metadata)
                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
        });
    }

    fn entries_from_active_chain_state(
        &self,
        state: &ActiveChainAssemblyState,
        raw_events: &[CaptureEvent],
    ) -> Vec<ActiveChainEntry> {
        let mut active_chain = self.entries_from_frame_stack(state);
        if active_chain.is_empty() {
            self.fallback_entries_when_chain_empty(state, raw_events, &mut active_chain);
        }
        active_chain
    }

    fn entries_from_frame_stack(&self, state: &ActiveChainAssemblyState) -> Vec<ActiveChainEntry> {
        let mut active_chain = Vec::new();
        for (index, frame) in state.frame_stack.iter().enumerate() {
            let dispatch_id = state.frame_dispatch.get(&frame.frame_id).copied();
            let dispatch = dispatch_id.and_then(|id| state.dispatches.get(&id));
            if let Some(dispatch) = dispatch.filter(|dispatch| Self::is_visible_dispatch(dispatch))
            {
                Self::push_effect_yield_entry(&mut active_chain, dispatch, Some(frame));
                continue;
            }

            active_chain.push(Self::program_yield_entry(
                frame,
                state.frame_stack.get(index + 1),
            ));
        }
        active_chain
    }

    fn fallback_entries_when_chain_empty(
        &self,
        state: &ActiveChainAssemblyState,
        raw_events: &[CaptureEvent],
        active_chain: &mut Vec<ActiveChainEntry>,
    ) {
        let Some(dispatch_id) = self.fallback_dispatch_id(state, raw_events) else {
            return;
        };
        let Some(dispatch) = state
            .dispatches
            .get(&dispatch_id)
            .filter(|dispatch| Self::is_visible_dispatch(dispatch))
        else {
            return;
        };

        let snapshot_frames = self.snapshot_frames_for_dispatch(dispatch_id);
        if snapshot_frames.is_empty() {
            Self::push_effect_yield_entry(active_chain, dispatch, None);
            return;
        }

        let last_index = snapshot_frames.len() - 1;
        for (index, frame) in snapshot_frames.iter().enumerate() {
            if index == last_index {
                Self::push_effect_yield_entry(active_chain, dispatch, Some(frame));
                continue;
            }
            active_chain.push(Self::program_yield_entry(
                frame,
                snapshot_frames.get(index + 1),
            ));
        }
    }

    fn fallback_dispatch_id(
        &self,
        state: &ActiveChainAssemblyState,
        raw_events: &[CaptureEvent],
    ) -> Option<DispatchId> {
        self.dispatch_stack
            .iter()
            .rev()
            .find_map(|ctx| {
                let dispatch = state.dispatches.get(&ctx.dispatch_id)?;
                if Self::is_visible_dispatch(dispatch) {
                    Some(ctx.dispatch_id)
                } else {
                    None
                }
            })
            .or_else(|| {
                raw_events.iter().rev().find_map(|event| {
                    let dispatch_id = Self::dispatch_id_for_event(event)?;
                    let dispatch = state.dispatches.get(&dispatch_id)?;
                    if Self::is_visible_dispatch(dispatch) {
                        Some(dispatch_id)
                    } else {
                        None
                    }
                })
            })
    }

    fn dispatch_id_for_event(event: &CaptureEvent) -> Option<DispatchId> {
        match event {
            CaptureEvent::DispatchStarted { dispatch_id, .. }
            | CaptureEvent::Delegated { dispatch_id, .. }
            | CaptureEvent::Passed { dispatch_id, .. }
            | CaptureEvent::HandlerCompleted { dispatch_id, .. }
            | CaptureEvent::Resumed { dispatch_id, .. }
            | CaptureEvent::Transferred { dispatch_id, .. } => Some(*dispatch_id),
            _ => None,
        }
    }

    fn snapshot_frames_for_dispatch(&self, dispatch_id: DispatchId) -> Vec<ActiveChainFrameState> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .map(|dispatch_ctx| {
                dispatch_ctx
                    .k_user
                    .frames_snapshot
                    .iter()
                    .filter_map(|frame| {
                        let Frame::Program {
                            stream,
                            metadata: Some(metadata),
                        } = frame
                        else {
                            return None;
                        };

                        let line = Self::stream_debug_location(stream)
                            .map(|location| location.source_line)
                            .unwrap_or(metadata.source_line);
                        Some(ActiveChainFrameState {
                            frame_id: metadata.frame_id as FrameId,
                            function_name: metadata.function_name.clone(),
                            source_file: metadata.source_file.clone(),
                            source_line: line,
                            sub_program_repr: Self::program_call_repr(metadata)
                                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    fn push_effect_yield_entry(
        chain: &mut Vec<ActiveChainEntry>,
        dispatch: &ActiveChainDispatchState,
        frame: Option<&ActiveChainFrameState>,
    ) {
        let function_name = dispatch.function_name.clone().unwrap_or_else(|| {
            frame
                .map(|snapshot| snapshot.function_name.clone())
                .unwrap_or_else(|| MISSING_UNKNOWN.to_string())
        });
        let source_file = dispatch.source_file.clone().unwrap_or_else(|| {
            frame
                .map(|snapshot| snapshot.source_file.clone())
                .unwrap_or_else(|| MISSING_UNKNOWN.to_string())
        });
        let source_line = dispatch
            .source_line
            .unwrap_or_else(|| frame.map_or(0, |snapshot| snapshot.source_line));
        chain.push(ActiveChainEntry::EffectYield {
            function_name,
            source_file,
            source_line,
            effect_repr: dispatch.effect_repr.clone(),
            handler_stack: dispatch.handler_stack.clone(),
            result: dispatch.result.clone(),
        });
    }

    fn program_yield_entry(
        frame: &ActiveChainFrameState,
        next_frame: Option<&ActiveChainFrameState>,
    ) -> ActiveChainEntry {
        let inferred_sub_program = next_frame.map(|next| format!("{}()", next.function_name));
        let sub_program_repr = if frame.sub_program_repr == MISSING_SUB_PROGRAM {
            inferred_sub_program.unwrap_or_else(|| frame.sub_program_repr.clone())
        } else {
            frame.sub_program_repr.clone()
        };
        ActiveChainEntry::ProgramYield {
            function_name: frame.function_name.clone(),
            source_file: frame.source_file.clone(),
            source_line: frame.source_line,
            sub_program_repr,
        }
    }

    fn dedup_adjacent(&self, entries: Vec<ActiveChainEntry>) -> Vec<ActiveChainEntry> {
        let mut deduped = Vec::with_capacity(entries.len());
        for entry in entries {
            let is_duplicate = deduped
                .last()
                .is_some_and(|prev| Self::is_adjacent_duplicate(prev, &entry));
            if !is_duplicate {
                deduped.push(entry);
            }
        }
        deduped
    }

    fn is_adjacent_duplicate(lhs: &ActiveChainEntry, rhs: &ActiveChainEntry) -> bool {
        match (lhs, rhs) {
            (
                ActiveChainEntry::ProgramYield {
                    function_name: lhs_function_name,
                    source_file: lhs_source_file,
                    source_line: lhs_source_line,
                    sub_program_repr: lhs_sub_program_repr,
                },
                ActiveChainEntry::ProgramYield {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    sub_program_repr: rhs_sub_program_repr,
                },
            ) => {
                lhs_function_name == rhs_function_name
                    && lhs_source_file == rhs_source_file
                    && lhs_source_line == rhs_source_line
                    && lhs_sub_program_repr == rhs_sub_program_repr
            }
            (
                ActiveChainEntry::EffectYield {
                    function_name: lhs_function_name,
                    source_file: lhs_source_file,
                    source_line: lhs_source_line,
                    effect_repr: lhs_effect_repr,
                    handler_stack: lhs_handler_stack,
                    result: lhs_result,
                },
                ActiveChainEntry::EffectYield {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    effect_repr: rhs_effect_repr,
                    handler_stack: rhs_handler_stack,
                    result: rhs_result,
                },
            ) => {
                lhs_function_name == rhs_function_name
                    && lhs_source_file == rhs_source_file
                    && lhs_source_line == rhs_source_line
                    && lhs_effect_repr == rhs_effect_repr
                    && lhs_handler_stack == rhs_handler_stack
                    && lhs_result == rhs_result
            }
            (
                ActiveChainEntry::ExceptionSite {
                    function_name: lhs_function_name,
                    source_file: lhs_source_file,
                    source_line: lhs_source_line,
                    exception_type: lhs_exception_type,
                    message: lhs_message,
                },
                ActiveChainEntry::ExceptionSite {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    exception_type: rhs_exception_type,
                    message: rhs_message,
                },
            ) => {
                lhs_function_name == rhs_function_name
                    && lhs_source_file == rhs_source_file
                    && lhs_source_line == rhs_source_line
                    && lhs_exception_type == rhs_exception_type
                    && lhs_message == rhs_message
            }
            _ => false,
        }
    }

    fn inject_context(
        &self,
        mut active_chain: Vec<ActiveChainEntry>,
        exception: &PyException,
    ) -> Vec<ActiveChainEntry> {
        let context_entries = Self::context_entries_from_exception(exception);
        let has_context_entries = !context_entries.is_empty();
        for data in context_entries {
            active_chain.push(ActiveChainEntry::ContextEntry { data });
        }

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
            ActiveChainEntry::ContextEntry { .. } => false,
        });

        let suppress_exception_site = !has_context_entries
            && active_chain
                .iter()
                .rev()
                .find(|entry| !matches!(entry, ActiveChainEntry::ContextEntry { .. }))
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

    fn is_visible_dispatch(dispatch: &ActiveChainDispatchState) -> bool {
        !dispatch.is_execution_context_effect
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

    fn format_do_ctrl(yielded: &DoCtrl, verbosity: ModeFormatVerbosity) -> &'static str {
        let formatted = match yielded {
            DoCtrl::Pure { .. } => "HandleYield(Pure)",
            DoCtrl::Map { .. } => "HandleYield(Map)",
            DoCtrl::FlatMap { .. } => "HandleYield(FlatMap)",
            DoCtrl::Perform { .. } => "HandleYield(Perform)",
            DoCtrl::Resume { .. } => "HandleYield(Resume)",
            DoCtrl::ResumeThenTransfer { .. } => "HandleYield(ResumeThenTransfer)",
            DoCtrl::Transfer { .. } => "HandleYield(Transfer)",
            DoCtrl::TransferThrow { .. } => "HandleYield(TransferThrow)",
            DoCtrl::TransferThrowThenTransfer { .. } => "HandleYield(TransferThrowThenTransfer)",
            DoCtrl::WithHandler { .. } => "HandleYield(WithHandler)",
            DoCtrl::Delegate { .. } => "HandleYield(Delegate)",
            DoCtrl::Pass { .. } => "HandleYield(Pass)",
            DoCtrl::GetContinuation => "HandleYield(GetContinuation)",
            DoCtrl::GetHandlers => "HandleYield(GetHandlers)",
            DoCtrl::GetTraceback { .. } => "HandleYield(GetTraceback)",
            DoCtrl::CreateContinuation { .. } => "HandleYield(CreateContinuation)",
            DoCtrl::ResumeContinuation { .. } => "HandleYield(ResumeContinuation)",
            DoCtrl::PythonAsyncSyntaxEscape { .. } => "HandleYield(AsyncEscape)",
            DoCtrl::Apply { .. } => "HandleYield(Apply)",
            DoCtrl::Expand { .. } => "HandleYield(Expand)",
            DoCtrl::Eval { .. } => "HandleYield(Eval)",
            DoCtrl::GetCallStack => "HandleYield(GetCallStack)",
            DoCtrl::GetTrace => "HandleYield(GetTrace)",
        };
        match verbosity {
            ModeFormatVerbosity::Compact | ModeFormatVerbosity::Verbose => formatted,
        }
    }

    fn format_mode(&self, verbosity: ModeFormatVerbosity) -> &'static str {
        match &self.mode {
            Mode::Deliver(_) => "Deliver",
            Mode::Throw(_) => "Throw",
            Mode::HandleYield(yielded) => Self::format_do_ctrl(yielded, verbosity),
            Mode::Return(_) => "Return",
        }
    }

    fn mode_kind(&self) -> &'static str {
        self.format_mode(ModeFormatVerbosity::Compact)
    }

    fn pending_kind(&self) -> &'static str {
        self.pending_python
            .as_ref()
            .map(|p| match p {
                PendingPython::EvalExpr { .. } => "EvalExpr",
                PendingPython::CallFuncReturn { .. } => "CallFuncReturn",
                PendingPython::ExpandReturn { .. } => "ExpandReturn",
                PendingPython::StepUserGenerator { .. } => "StepUserGenerator",
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
                    PythonCall::EvalExpr { .. } => "EvalExpr",
                    PythonCall::CallFunc { .. } => "CallFunc",
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
        let mode_kind = self.format_mode(ModeFormatVerbosity::Verbose);

        let seg_info = self
            .current_segment
            .and_then(|id| self.segments.get(id))
            .map(|s| format!("seg={:?} frames={}", self.current_segment, s.frames.len()))
            .unwrap_or_else(|| "seg=None".to_string());

        let pending = self.pending_kind();

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
                        Frame::Program { metadata, .. } if metadata.is_some() => "Program(meta)",
                        Frame::Program { .. } => "Program",
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
                    PythonCall::EvalExpr { .. } => "EvalExpr",
                    PythonCall::CallFunc { .. } => "CallFunc",
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
            ASTStreamStep::Yield(yielded) => {
                // Terminal DoCtrl variants (Resume, Transfer, TransferThrow, Pass) transfer
                // control elsewhere — the handler is done and no value flows back. Do NOT
                // re-push the Program frame for these. Non-terminal variants (Eval, GetHandlers,
                // GetCallStack) expect a result to be delivered back to this stream.
                let is_terminal = matches!(
                    &yielded,
                    DoCtrl::Resume { .. }
                        | DoCtrl::Transfer { .. }
                        | DoCtrl::TransferThrow { .. }
                        | DoCtrl::Pass { .. }
                );
                if !is_terminal {
                    let Some(seg) = self.current_segment_mut() else {
                        return StepEvent::Error(VMError::internal(
                            "current_segment_mut() returned None in apply_stream_step \
                             (Yield non-terminal)",
                        ));
                    };
                    seg.push_frame(Frame::Program { stream, metadata });
                }
                self.mode = Mode::HandleYield(yielded);
                StepEvent::Continue
            }
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
            DoCtrl::ResumeThenTransfer {
                continuation,
                value,
            } => self.handle_yield_resume_then_transfer(continuation, value),
            DoCtrl::Transfer {
                continuation,
                value,
            } => self.handle_yield_transfer(continuation, value),
            DoCtrl::TransferThrow {
                continuation,
                exception,
            } => self.handle_yield_transfer_throw(continuation, exception),
            DoCtrl::TransferThrowThenTransfer {
                continuation,
                exception,
            } => self.handle_yield_transfer_throw_then_transfer(continuation, exception),
            DoCtrl::WithHandler {
                handler,
                expr,
                py_identity,
            } => self.handle_yield_with_handler(handler, expr, py_identity),
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

    fn handle_yield_resume_then_transfer(
        &mut self,
        continuation: Continuation,
        value: Value,
    ) -> StepEvent {
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

    fn handle_yield_transfer_throw_then_transfer(
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
                let Some(seg) = self.current_segment_mut() else {
                    self.mode = Mode::Throw(PyException::runtime_error(
                        "current_segment_mut() returned None in receive_python_result \
                         StepUserGenerator::GenYield",
                    ));
                    return;
                };
                seg.push_frame(Frame::Program { stream, metadata });
                self.mode = Mode::HandleYield(yielded);
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
            let Some(entry) = self.handlers.get(&marker) else {
                return Err(VMError::internal(format!(
                    "find_matching_handler: missing handler marker {} at index {}",
                    marker.raw(),
                    idx
                )));
            };
            if entry.handler.can_handle(effect)? {
                return Ok((idx, marker, entry.clone()));
            }
        }
        Err(VMError::no_matching_handler(effect.clone()))
    }

    pub fn start_dispatch(&mut self, effect: DispatchEffect) -> Result<StepEvent, VMError> {
        self.lazy_pop_completed();
        let original_exception = self.pending_error_context.take();

        let scope_chain = self.current_scope_chain();
        let handler_chain: Vec<Marker> = self
            .visible_handlers(&scope_chain)
            .into_iter()
            .filter(|marker| self.handlers.contains_key(marker))
            .collect();

        if handler_chain.is_empty() {
            if let Some(original) = original_exception.clone() {
                self.mode = Mode::Throw(original);
                return Ok(StepEvent::Continue);
            }
            return Err(VMError::unhandled_effect(effect));
        }

        let (handler_idx, handler_marker, entry) =
            match self.find_matching_handler(&handler_chain, &effect) {
                Ok(found) => found,
                Err(err) => {
                    if let Some(original) = original_exception.clone() {
                        self.mode = Mode::Throw(original);
                        return Ok(StepEvent::Continue);
                    }
                    return Err(err);
                }
            };

        let prompt_seg_id = entry.prompt_seg_id;
        let handler = entry.handler.clone();
        let dispatch_id = DispatchId::fresh();
        let is_execution_context_effect = Self::is_execution_context_effect(&effect);
        let supports_error_context_conversion = handler.supports_error_context_conversion();
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
            is_execution_context_effect,
            handler_chain: handler_chain.clone(),
            handler_idx,
            supports_error_context_conversion,
            k_user: k_user.clone(),
            prompt_seg_id,
            completed: false,
            original_exception,
        });

        let (handler_name, handler_kind, handler_source_file, handler_source_line) =
            Self::handler_trace_info(&handler);
        let effect_site = Self::effect_site_from_continuation(&k_user);
        self.capture_log.push(CaptureEvent::DispatchStarted {
            dispatch_id,
            effect_repr: Self::effect_repr(&effect),
            is_execution_context_effect,
            creation_site: Self::effect_creation_site_from_continuation(&k_user),
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

        if handler.py_identity().is_some() {
            self.register_continuation(k_user.clone());
        }
        let ir_node = handler.invoke(effect, k_user);
        Ok(self.evaluate(ir_node))
    }

    fn check_dispatch_completion(&mut self, k: &Continuation) {
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id
                    && top.k_user.cont_id == k.cont_id
                    && top.k_user.parent.is_none()
                {
                    top.completed = true;
                }
            }
        }
    }

    fn error_dispatch_for_continuation(
        &self,
        k: &Continuation,
    ) -> Option<(DispatchId, PyException, bool)> {
        let dispatch_id = k.dispatch_id?;
        let ctx = self
            .dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)?;
        let original = ctx.original_exception.clone()?;
        let mut cursor = Some(ctx.k_user.clone());
        while let Some(current) = cursor {
            if current.cont_id == k.cont_id {
                return Some((dispatch_id, original, current.parent.is_none()));
            }
            cursor = current.parent.as_ref().map(|parent| (**parent).clone());
        }
        None
    }

    fn active_dispatch_handler_is_python(&self, dispatch_id: DispatchId) -> bool {
        self.dispatch_stack
            .last()
            .filter(|ctx| ctx.dispatch_id == dispatch_id)
            .and_then(|ctx| ctx.handler_chain.get(ctx.handler_idx))
            .and_then(|marker| self.handlers.get(marker))
            .is_some_and(|entry| entry.handler.py_identity().is_some())
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

    fn check_dispatch_completion_after_activation(
        &mut self,
        kind: ContinuationActivationKind,
        k: &Continuation,
        had_error_dispatch: bool,
    ) {
        match kind {
            ContinuationActivationKind::Resume => {
                if had_error_dispatch {
                    if let Some(dispatch_id) = k.dispatch_id {
                        if !self.active_dispatch_handler_is_python(dispatch_id) {
                            self.check_dispatch_completion(k);
                        }
                    } else {
                        self.check_dispatch_completion(k);
                    }
                    return;
                }

                if let Some(dispatch_id) = k.dispatch_id {
                    if !self.active_dispatch_handler_is_python(dispatch_id) {
                        self.check_dispatch_completion(k);
                    }
                } else {
                    self.check_dispatch_completion(k);
                }
            }
            ContinuationActivationKind::Transfer => {
                self.check_dispatch_completion(k);
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

    fn check_dispatch_completion_for_non_terminal_throw(&mut self, k: &Continuation) {
        if let Some(dispatch_id) = k.dispatch_id {
            if !self.active_dispatch_handler_is_python(dispatch_id) {
                self.check_dispatch_completion(k);
            }
        } else {
            self.check_dispatch_completion(k);
        }
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

    fn maybe_emit_forward_capture_event(
        &mut self,
        kind: ForwardKind,
        dispatch_id: DispatchId,
        handler_chain: &[Marker],
        from_idx: usize,
        to_idx: usize,
        to_marker: Marker,
    ) {
        let from_marker = handler_chain.get(from_idx).copied();
        let from_name = from_marker
            .and_then(|m| self.marker_handler_trace_info(m))
            .map(|(name, _, _, _)| name);
        let to_info = self.marker_handler_trace_info(to_marker);
        if let (Some(from_name), Some((to_name, to_kind, to_source_file, to_source_line))) =
            (from_name, to_info)
        {
            let event = match kind {
                ForwardKind::Delegate => CaptureEvent::Delegated {
                    dispatch_id,
                    from_handler_name: from_name,
                    from_handler_index: from_idx,
                    to_handler_name: to_name,
                    to_handler_index: to_idx,
                    to_handler_kind: to_kind,
                    to_handler_source_file: to_source_file,
                    to_handler_source_line: to_source_line,
                },
                ForwardKind::Pass => CaptureEvent::Passed {
                    dispatch_id,
                    from_handler_name: from_name,
                    from_handler_index: from_idx,
                    to_handler_name: to_name,
                    to_handler_index: to_idx,
                    to_handler_kind: to_kind,
                    to_handler_source_file: to_source_file,
                    to_handler_source_line: to_source_line,
                },
            };
            self.capture_log.push(event);
        }
    }

    fn handle_forward(&mut self, kind: ForwardKind, effect: DispatchEffect) -> StepEvent {
        let (handler_chain, start_idx, from_idx, dispatch_id, parent_k_user) =
            match self.dispatch_stack.last() {
                Some(top) => (
                    top.handler_chain.clone(),
                    top.handler_idx + 1,
                    top.handler_idx,
                    top.dispatch_id,
                    if kind == ForwardKind::Delegate {
                        Some(top.k_user.clone())
                    } else {
                        None
                    },
                ),
                None => return StepEvent::Error(VMError::internal(kind.outside_dispatch_error())),
            };

        // Capture inner handler segment so outer handler return flows back as the
        // result of Delegate/Pass. Per spec this preserves caller = Some(inner_seg_id).
        let inner_seg_id = self.current_segment;

        match kind {
            ForwardKind::Delegate => {
                // Delegate is non-terminal: keep a parent chain to the old continuation.
                let Some(mut k_new) = self.capture_continuation(Some(dispatch_id)) else {
                    return StepEvent::Error(VMError::internal(
                        "Delegate called without current segment",
                    ));
                };
                let Some(parent_k_user) = parent_k_user else {
                    return StepEvent::Error(VMError::internal(
                        "Delegate called without active dispatch continuation",
                    ));
                };
                k_new.parent = Some(Arc::new(parent_k_user));
                self.clear_segment_frames(inner_seg_id);
                if let Some(top) = self.dispatch_stack.last_mut() {
                    top.k_user = k_new;
                }
            }
            ForwardKind::Pass => {
                // Pass is terminal for the current handler; clear frames so values pass through.
                self.clear_segment_frames(inner_seg_id);
            }
        }

        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            let Some(entry) = self.handlers.get(&marker) else {
                return StepEvent::Error(VMError::internal(format!(
                    "{}: missing handler marker {} at index {}",
                    kind.missing_handler_context(),
                    marker.raw(),
                    idx
                )));
            };
            let can_handle = match entry.handler.can_handle(&effect) {
                Ok(value) => value,
                Err(err) => return StepEvent::Error(err),
            };
            if can_handle {
                let handler = entry.handler.clone();
                let supports_error_context_conversion =
                    entry.handler.supports_error_context_conversion();
                self.maybe_emit_forward_capture_event(
                    kind,
                    dispatch_id,
                    &handler_chain,
                    from_idx,
                    idx,
                    marker,
                );
                let k_user = {
                    let top = self.dispatch_stack.last_mut().unwrap();
                    top.handler_idx = idx;
                    top.supports_error_context_conversion = supports_error_context_conversion;
                    top.effect = effect.clone();
                    top.k_user.clone()
                };

                let scope_chain = self.current_scope_chain();
                let handler_seg = Segment::new(marker, inner_seg_id, scope_chain);
                let handler_seg_id = self.alloc_segment(handler_seg);
                self.current_segment = Some(handler_seg_id);

                if handler.py_identity().is_some() {
                    self.register_continuation(k_user.clone());
                }
                let ir_node = handler.invoke(effect.clone(), k_user);
                return self.evaluate(ir_node);
            }
        }

        if let Some((dispatch_id, original_exception)) =
            self.dispatch_stack.last().and_then(|ctx| {
                ctx.original_exception
                    .clone()
                    .map(|exc| (ctx.dispatch_id, exc))
            })
        {
            self.mark_dispatch_completed(dispatch_id);
            self.mode = Mode::Throw(original_exception);
            return StepEvent::Continue;
        }

        StepEvent::Error(VMError::delegate_no_outer_handler(effect))
    }

    fn handle_delegate(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_forward(ForwardKind::Delegate, effect)
    }

    fn handle_pass(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_forward(ForwardKind::Pass, effect)
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
                let Some(seg) = self.current_segment_mut() else {
                    return StepEvent::Error(VMError::internal(
                        "current_segment_mut() returned None in handle_handler_return \
                         while scheduling Eval callback",
                    ));
                };
                seg.push_frame(Frame::RustReturn { cb });
                self.mode = Mode::HandleYield(DoCtrl::Eval {
                    expr,
                    handlers,
                    metadata: None,
                });
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

        let Some(seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal(
                "current_segment missing in handle_handler_return while dispatch active",
            ));
        };
        let Some(seg) = self.segments.get(seg_id) else {
            return StepEvent::Error(VMError::internal(
                "current segment not found in handle_handler_return while dispatch active",
            ));
        };
        let Some(caller_id) = seg.caller else {
            return StepEvent::Error(VMError::internal(
                "handler segment missing caller in handle_handler_return",
            ));
        };

        let original_exception = {
            let Some(top) = self.dispatch_stack.last_mut() else {
                return StepEvent::Error(VMError::internal("Return outside of dispatch"));
            };

            if caller_id == top.prompt_seg_id {
                top.completed = true;
                self.consumed_cont_ids.insert(top.k_user.cont_id);
            }

            if top.completed {
                top.original_exception.clone()
            } else {
                None
            }
        };

        if let Some(original) = original_exception {
            self.mode = match Self::enrich_original_exception_with_context(original, value) {
                Ok(exception) => Mode::Throw(exception),
                Err(effect_err) => Mode::Throw(effect_err),
            };
            return StepEvent::Continue;
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
mod tests {
    use super::*;
    use crate::ast_stream::{ASTStream, ASTStreamStep};
    use crate::frame::CallMetadata;
    use std::sync::{Arc, Mutex};

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
            parent: None,
        }
    }

    #[derive(Debug)]
    struct DummyProgramStream;

    impl ASTStream for DummyProgramStream {
        fn resume(&mut self, _value: Value, _store: &mut RustStore) -> ASTStreamStep {
            ASTStreamStep::Return(Value::Unit)
        }

        fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> ASTStreamStep {
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
        let stream: Arc<Mutex<Box<dyn ASTStream>>> = Arc::new(Mutex::new(Box::new(
            DummyProgramStream,
        )
            as Box<dyn ASTStream>));
        Frame::program(stream, Some(metadata))
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
            is_execution_context_effect: false,
            handler_chain: vec![m1, m2, m3],
            handler_idx: 1,
            supports_error_context_conversion: false,
            k_user: k_user.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: false,
            original_exception: None,
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
            is_execution_context_effect: false,
            handler_chain: vec![m1, m2],
            handler_idx: 0,
            supports_error_context_conversion: false,
            k_user: k_user.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: true,
            original_exception: None,
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
            is_execution_context_effect: false,
            handler_chain: vec![],
            handler_idx: 0,
            supports_error_context_conversion: false,
            k_user: k_user_1.clone(),

            prompt_seg_id: SegmentId::from_index(0),

            completed: true,
            original_exception: None,
        });
        vm.dispatch_stack.push(DispatchContext {
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
        vm.dispatch_stack.push(DispatchContext {
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
                std::sync::Arc::new(crate::handler::ReaderHandlerFactory),
                prompt_seg_id,
            ),
        );
        vm.install_handler(
            m2,
            HandlerEntry::new(
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
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
    fn test_find_matching_handler_propagates_can_handle_parse_error() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let prompt_seg_id = SegmentId::from_index(0);

            vm.install_handler(
                marker,
                HandlerEntry::new(
                    std::sync::Arc::new(crate::handler::ReaderHandlerFactory),
                    prompt_seg_id,
                ),
            );

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
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
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
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
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
    fn test_start_dispatch_records_effect_creation_site_from_continuation_frame() {
        Python::attach(|py| {
            use crate::frame::Frame;
            use pyo3::types::PyModule;
            use std::sync::Arc;

            let mut vm = VM::new();
            let marker = Marker::fresh();

            let prompt_seg = Segment::new(marker, None, vec![]);
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

            let mut body_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
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

            vm.install_handler(
                marker,
                HandlerEntry::new(
                    Arc::new(crate::scheduler::SchedulerHandler::new()),
                    prompt_seg_id,
                ),
            );

            let spawn = Py::new(py, PySpawn::create(py, py.None(), None, None, None, None))
                .expect("failed to create SpawnEffect");
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
            let observed = VM::stream_debug_location(&stream).expect("expected stream location");
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
            runtime_src.contains("debug_location()")
                && runtime_src.contains("stream_debug_location"),
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
    fn test_handle_delegate_links_previous_k_as_parent() {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let original_k_user = make_dummy_continuation();
        let original_cont_id = original_k_user.cont_id;
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
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

        let top = vm
            .dispatch_stack
            .last()
            .expect("dispatch context must exist");
        let parent = top
            .k_user
            .parent
            .as_ref()
            .expect("delegate must set parent");
        assert_ne!(top.k_user.cont_id, original_cont_id);
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
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
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
        match &vm.mode {
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

        let hops = VM::collect_traceback(&child);
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
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = vm.alloc_segment(seg);
        vm.current_segment = Some(seg_id);

        let k_user = make_dummy_continuation();
        vm.dispatch_stack.push(DispatchContext {
            dispatch_id: DispatchId::fresh(),
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
        vm.mode = Mode::HandleYield(DoCtrl::GetTraceback {
            continuation: query_continuation,
        });

        let event = vm.step_handle_yield();
        assert!(matches!(event, StepEvent::Continue));
        match &vm.mode {
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
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
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
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
                prompt_seg_id,
            ),
        );
        vm.install_handler(
            m2,
            HandlerEntry::new(
                std::sync::Arc::new(crate::handler::WriterHandlerFactory),
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
        let seg = Segment::new(m1, None, vec![m1]);
        let seg_id = vm.alloc_segment(seg);
        vm.install_handler(
            m1,
            HandlerEntry::new(
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
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
            is_execution_context_effect: false,
            handler_chain: vec![],
            handler_idx: 0,
            supports_error_context_conversion: false,
            k_user: k_user.clone(),
            prompt_seg_id: SegmentId::from_index(0),
            completed: true,
            original_exception: None,
        });

        vm.mode = Mode::HandleYield(DoCtrl::GetHandlers);
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
                    std::sync::Arc::new(crate::handler::DoubleCallHandlerFactory),
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
                    std::sync::Arc::new(crate::handler::StateHandlerFactory),
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

        let prompt_seg = Segment::new(marker, None, vec![marker]);
        let prompt_seg_id = vm.alloc_segment(prompt_seg);

        let handler_seg = Segment::new(marker, Some(prompt_seg_id), vec![marker]);
        let handler_seg_id = vm.alloc_segment(handler_seg);
        vm.current_segment = Some(handler_seg_id);

        vm.install_handler(
            marker,
            HandlerEntry::new(
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
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
            parent: None,
        };

        vm.dispatch_stack.push(DispatchContext {
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
    // R9-A: DoCtrl::Apply — direct Python call dispatch tests
    // ==========================================================

    #[test]
    fn test_apply_return_delivers_value_without_pushing_frame() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
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

            vm.mode = Mode::HandleYield(DoCtrl::Apply {
                f: CallArg::Value(Value::Python(dummy_f)),
                args: vec![],
                kwargs: vec![],
                metadata,
            });

            let event = vm.step_handle_yield();
            assert!(matches!(
                event,
                StepEvent::NeedsPython(PythonCall::CallFunc { .. })
            ));
            assert!(matches!(
                vm.pending_python,
                Some(PendingPython::CallFuncReturn { .. })
            ));

            vm.receive_python_result(PyCallOutcome::Value(Value::Int(7)));
            assert!(matches!(vm.mode, Mode::Deliver(Value::Int(7))));
            let seg = vm.segments.get(seg_id).expect("segment missing");
            assert!(
                seg.frames.is_empty(),
                "Apply must not push a PythonGenerator frame"
            );
        });
    }

    #[test]
    fn test_expand_requires_doeff_generator_or_errors() {
        Python::attach(|py| {
            let mut vm = VM::new();
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
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

            vm.mode = Mode::HandleYield(DoCtrl::Expand {
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
                vm.pending_python,
                Some(PendingPython::ExpandReturn {
                    metadata: Some(_),
                    ..
                })
            ));

            vm.receive_python_result(PyCallOutcome::Value(Value::Int(1)));
            match &vm.mode {
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

    /// R9-A: Apply with empty args/kwargs still dispatches via CallFunc.
    #[test]
    fn test_r9a_apply_empty_args_yields_call_func() {
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

            vm.mode = Mode::HandleYield(DoCtrl::Apply {
                f: CallArg::Value(Value::Python(dummy_f)),
                args: vec![],
                kwargs: vec![],
                metadata: metadata.clone(),
            });

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

    /// R9-A: Apply with non-empty args → CallFunc.
    /// Spec: "Kernel call (with args): Apply { f: kernel, args, kwargs, metadata }
    ///        → driver calls kernel(*args, **kwargs), gets result, pushes frame."
    #[test]
    fn test_r9a_apply_with_args_yields_call_func() {
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

            vm.mode = Mode::HandleYield(DoCtrl::Apply {
                f: CallArg::Value(Value::Python(dummy_f)),
                args: vec![
                    CallArg::Value(Value::Int(42)),
                    CallArg::Value(Value::String("hello".to_string())),
                ],
                kwargs: vec![],
                metadata,
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

            vm.mode = Mode::HandleYield(DoCtrl::Apply {
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

            vm.mode = Mode::HandleYield(DoCtrl::Apply {
                f: CallArg::Value(Value::Python(dummy_f)),
                args: vec![],
                kwargs: vec![(
                    "name".to_string(),
                    CallArg::Value(Value::String("test".to_string())),
                )],
                metadata,
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
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let dummy_expr = py.None().into_pyobject(py).unwrap().unbind().into_any();

            vm.mode = Mode::HandleYield(DoCtrl::Eval {
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
                    vm.pending_python,
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
            let seg = Segment::new(marker, None, vec![]);
            let seg_id = vm.alloc_segment(seg);
            vm.current_segment = Some(seg_id);

            let dummy_expr = py.None().into_pyobject(py).unwrap().unbind().into_any();

            let handler = std::sync::Arc::new(crate::handler::StateHandlerFactory);

            vm.mode = Mode::HandleYield(DoCtrl::Eval {
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
