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
    ActiveChainEntry, CaptureEvent, EffectCreationSite, HandlerAction, HandlerKind,
    HandlerSnapshotEntry, TraceEntry,
};
use crate::continuation::Continuation;
use crate::debug_state::DebugState;
use crate::dispatch_state::DispatchState;
use crate::do_ctrl::{CallArg, DoCtrl};
use crate::doeff_generator::DoeffGenerator;
use crate::driver::{Mode, PyException, StepEvent};
use crate::effect::{
    dispatch_ref_as_python, make_get_execution_context_effect, DispatchEffect,
    PyGetExecutionContext,
};
#[cfg(test)]
use crate::effect::{Effect, PySpawn};
use crate::error::VMError;
use crate::frame::{CallMetadata, EvalReturnContinuation, Frame, InterceptorContinuation};
use crate::handler::{Handler, RustProgramInvocation};
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::interceptor_state::InterceptorState;
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::pyvm::{classify_yielded_for_vm, doctrl_to_pyexpr_for_vm, PyDoExprBase, PyEffectBase};
use crate::segment::{Segment, SegmentKind};
use crate::trace_state::TraceState;
use crate::value::Value;

pub use crate::dispatch::DispatchContext;
pub use crate::rust_store::RustStore;

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

const MISSING_UNKNOWN: &str = "[MISSING] <unknown>";

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
pub(crate) enum ModeFormatVerbosity {
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
pub struct InterceptorEntry {
    pub(crate) interceptor: PyShared,
    pub(crate) metadata: Option<CallMetadata>,
}

#[derive(Clone)]
struct InstalledHandler {
    marker: Marker,
    handler: Handler,
    py_identity: Option<PyShared>,
}

#[derive(Clone)]
struct HandlerChainEntry {
    marker: Marker,
    prompt_seg_id: SegmentId,
    handler: Handler,
    py_identity: Option<PyShared>,
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
    pub(crate) dispatch_state: DispatchState,
    pub consumed_cont_ids: HashSet<ContId>,
    installed_handlers: Vec<InstalledHandler>,
    run_handlers: Vec<Handler>,
    pub(crate) interceptor_state: InterceptorState,
    pub rust_store: RustStore,
    pub py_store: Option<PyStore>,
    pub current_segment: Option<SegmentId>,
    pub(crate) debug: DebugState,
    pub(crate) trace_state: TraceState,
    pub continuation_registry: HashMap<ContId, Continuation>,
    pub active_run_token: Option<u64>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: SegmentArena::new(),
            dispatch_state: DispatchState::default(),
            consumed_cont_ids: HashSet::new(),
            installed_handlers: Vec::new(),
            run_handlers: Vec::new(),
            interceptor_state: InterceptorState::default(),
            rust_store: RustStore::new(),
            py_store: None,
            current_segment: None,
            debug: DebugState::new(DebugConfig::default()),
            trace_state: TraceState::default(),
            continuation_registry: HashMap::new(),
            active_run_token: None,
        }
    }

    pub fn with_debug(debug: DebugConfig) -> Self {
        let mut vm = Self::new();
        vm.debug.set_config(debug);
        vm
    }

    pub fn set_debug(&mut self, config: DebugConfig) {
        self.debug.set_config(config);
    }

    pub fn begin_run_session(&mut self) -> u64 {
        let token = NEXT_RUN_TOKEN.fetch_add(1, Ordering::Relaxed);
        self.active_run_token = Some(token);
        self.trace_state.clear();
        self.interceptor_state.clear_for_run();
        self.run_handlers.clear();
        token
    }

    pub fn current_run_token(&self) -> Option<u64> {
        self.active_run_token
    }

    pub fn end_active_run_session(&mut self) {
        let Some(run_token) = self.active_run_token.take() else {
            return;
        };

        for handler in &self.run_handlers {
            handler.on_run_end(run_token);
        }
        self.run_handlers.clear();
    }

    pub fn enable_trace(&mut self, enabled: bool) {
        self.debug.enable_trace(enabled);
    }

    pub fn trace_events(&self) -> &[TraceEvent] {
        self.debug.trace_events()
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

    #[inline]
    fn current_seg(&self) -> &Segment {
        let seg_id = self
            .current_segment
            .expect("current_segment missing in current_seg()");
        self.segments
            .get(seg_id)
            .expect("current segment not found in arena")
    }

    #[inline]
    fn current_seg_mut(&mut self) -> &mut Segment {
        let seg_id = self
            .current_segment
            .expect("current_segment missing in current_seg_mut()");
        self.segments
            .get_mut(seg_id)
            .expect("current segment not found in arena")
    }

    fn track_run_handler(&mut self, handler: &Handler) {
        if !self
            .run_handlers
            .iter()
            .any(|existing| Arc::ptr_eq(existing, handler))
        {
            self.run_handlers.push(handler.clone());
        }
    }

    fn find_prompt_boundary_by_marker(
        &self,
        marker: Marker,
    ) -> Option<(SegmentId, Handler, Option<PyShared>)> {
        self.segments
            .iter()
            .find_map(|(seg_id, seg)| match &seg.kind {
                SegmentKind::PromptBoundary {
                    handled_marker,
                    handler,
                    py_identity,
                    ..
                } if *handled_marker == marker => {
                    Some((seg_id, handler.clone(), py_identity.clone()))
                }
                _ => None,
            })
    }

    fn handlers_in_caller_chain(&self, start_seg_id: SegmentId) -> Vec<HandlerChainEntry> {
        let mut chain = Vec::new();
        let mut cursor = Some(start_seg_id);
        if let Some(start_seg) = self.segments.get(start_seg_id) {
            if let Some(anchor) = start_seg.handler_lookup_anchor {
                let anchor_is_live = match start_seg.handler_lookup_anchor_marker {
                    Some(expected_marker) => self
                        .segments
                        .get(anchor)
                        .is_some_and(|seg| seg.marker == expected_marker),
                    None => self.segments.get(anchor).is_some(),
                };
                if anchor_is_live {
                    // Handler segments execute outside the effect site prompt chain.
                    // Anchor lookup at the effect site to preserve nested visibility.
                    cursor = Some(anchor);
                }
            }
        }
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                py_identity,
                ..
            } = &seg.kind
            {
                chain.push(HandlerChainEntry {
                    marker: *handled_marker,
                    prompt_seg_id: seg_id,
                    handler: handler.clone(),
                    py_identity: py_identity.clone(),
                });
            }
            cursor = seg.caller;
        }
        chain
    }

    fn current_handler_chain(&self) -> Vec<HandlerChainEntry> {
        let Some(seg_id) = self.current_segment else {
            return Vec::new();
        };
        self.handlers_in_caller_chain(seg_id)
    }

    fn active_busy_markers(&self) -> HashSet<Marker> {
        let mut busy = HashSet::new();
        for ctx in self.dispatch_state.contexts() {
            if ctx.completed {
                continue;
            }
            if let Some(marker) = ctx.handler_chain.get(ctx.handler_idx) {
                busy.insert(*marker);
            }
        }
        busy
    }

    fn prompt_boundary_handler_lookup(&self) -> HashMap<Marker, (Handler, Option<PyShared>)> {
        let mut handlers = HashMap::new();
        for (_seg_id, seg) in self.segments.iter() {
            if let SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                py_identity,
                ..
            } = &seg.kind
            {
                handlers.insert(*handled_marker, (handler.clone(), py_identity.clone()));
            }
        }
        handlers
    }

    pub(crate) fn instantiate_installed_handlers(&mut self) -> Option<SegmentId> {
        let installed = self.installed_handlers.clone();
        let mut outside_seg_id: Option<SegmentId> = None;
        for entry in installed.into_iter().rev() {
            let mut prompt_seg = Segment::new_prompt(
                entry.marker,
                outside_seg_id,
                entry.marker,
                entry.handler.clone(),
                None,
                entry.py_identity.clone(),
            );
            self.copy_interceptor_guard_state(outside_seg_id, &mut prompt_seg);
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            self.track_run_handler(&entry.handler);

            let mut body_seg = Segment::new(entry.marker, Some(prompt_seg_id));
            self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
            let body_seg_id = self.alloc_segment(body_seg);
            outside_seg_id = Some(body_seg_id);
        }
        outside_seg_id
    }

    /// Copy interceptor guard state from a source segment to a child segment.
    ///
    /// **Why inheritance is required (not derivable from frames):**
    ///
    /// `interceptor_eval_depth` and `interceptor_skip_stack` are *dynamic guard
    /// context* that spans segment topology changes. They cannot be derived from
    /// the child segment's local frame stack because:
    ///
    /// 1. **Child segments start with empty frames.** A new handler segment
    ///    (created during dispatch at prompt boundaries) or a new interceptor body
    ///    segment (created by `prepare_with_intercept`) has no frames, yet it runs
    ///    within the parent's interceptor invocation context and must inherit the
    ///    guard state to prevent re-entrancy and double-evaluation.
    ///
    /// 2. **Delegate/pass clears frames.** `clear_segment_frames` wipes the inner
    ///    segment's frame stack during forwarding, but guard state must survive so
    ///    the next handler segment inherits the correct interceptor context.
    ///
    /// 3. **Typed continuation frames are local.** Interceptor guard state must
    ///    survive continuation capture/resume and segment topology rewrites even
    ///    when relevant continuation frames are no longer present locally.
    #[inline]
    fn copy_interceptor_guard_state(
        &self,
        source_seg_id: Option<SegmentId>,
        child_seg: &mut Segment,
    ) {
        let Some(source_seg_id) = source_seg_id else {
            return;
        };
        let Some(source_seg) = self.segments.get(source_seg_id) else {
            return;
        };
        child_seg.interceptor_eval_depth = source_seg.interceptor_eval_depth;
        child_seg.interceptor_skip_stack = source_seg.interceptor_skip_stack.clone();
    }

    /// Set mode to Throw with a RuntimeError and return Continue.
    fn throw_runtime_error(&mut self, message: &str) -> StepEvent {
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal(
                "throw_runtime_error called without current segment",
            ));
        };
        seg.mode = Mode::Throw(PyException::runtime_error(message.to_string()));
        StepEvent::Continue
    }

    fn eval_then_reenter_call(
        &mut self,
        expr: PyShared,
        continuation: EvalReturnContinuation,
    ) -> StepEvent {
        let handlers = self.current_visible_handlers();
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("Call evaluation outside current segment"));
        };
        seg.push_frame(Frame::EvalReturn(Box::new(continuation)));
        seg.mode = Mode::HandleYield(DoCtrl::Eval {
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
        self.current_seg_mut().mode = Mode::HandleYield(ir_node);
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

    fn value_repr(value: &Value) -> Option<String> {
        DebugState::value_repr(value)
    }

    fn program_call_repr(metadata: &CallMetadata) -> Option<String> {
        DebugState::program_call_repr(metadata)
    }

    fn exception_repr(exception: &PyException) -> Option<String> {
        DebugState::exception_repr(exception)
    }

    fn effect_repr(effect: &DispatchEffect) -> String {
        DebugState::effect_repr(effect)
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

    fn is_local_effect(effect: &DispatchEffect) -> bool {
        let Some(obj) = dispatch_ref_as_python(effect) else {
            return false;
        };
        Python::attach(|py| {
            obj.bind(py)
                .extract::<PyRef<'_, crate::effect::PyLocal>>()
                .is_ok()
        })
    }

    fn dispatch_supports_error_context_conversion(&self, dispatch_id: DispatchId) -> bool {
        self.dispatch_state
            .dispatch_supports_error_context_conversion(dispatch_id)
    }

    fn effect_creation_site_from_continuation(k: &Continuation) -> Option<EffectCreationSite> {
        let (_, function_name, source_file, source_line) =
            TraceState::effect_site_from_continuation(k)?;
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
        if let Some(seg_id) = self.current_segment {
            if let Some(entry) = self
                .handlers_in_caller_chain(seg_id)
                .into_iter()
                .find(|entry| entry.marker == marker)
            {
                return Some(Self::handler_trace_info(&entry.handler));
            }
        }
        self.find_prompt_boundary_by_marker(marker)
            .map(|(_seg_id, handler, _py_identity)| Self::handler_trace_info(&handler))
    }

    fn current_handler_identity_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<(usize, String)> {
        let ctx = self
            .dispatch_state
            .find_by_dispatch_id(dispatch_id)?
            .clone();
        let marker = *ctx.handler_chain.get(ctx.handler_idx)?;
        let (name, _, _, _) = self.marker_handler_trace_info(marker)?;
        Some((ctx.handler_idx, name))
    }

    fn current_segment_is_active_handler_for_dispatch(&self, dispatch_id: DispatchId) -> bool {
        let Some(seg_id) = self.current_segment else {
            return false;
        };
        let Some(seg) = self.segments.get(seg_id) else {
            return false;
        };
        let Some(ctx) = self.dispatch_state.find_by_dispatch_id(dispatch_id) else {
            return false;
        };
        let Some(active_marker) = ctx.handler_chain.get(ctx.handler_idx) else {
            return false;
        };
        seg.marker == *active_marker
    }

    fn current_active_handler_dispatch_id(&self) -> Option<DispatchId> {
        self.interceptor_state.current_active_handler_dispatch_id(
            self.dispatch_state.contexts(),
            self.current_segment,
            &self.segments,
        )
    }

    fn dispatch_uses_user_continuation_stream(
        &self,
        dispatch_id: DispatchId,
        stream: &ASTStreamRef,
    ) -> bool {
        self.dispatch_state
            .find_by_dispatch_id(dispatch_id)
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
        self.dispatch_state
            .active_error_dispatch_original_exception()
    }

    fn original_exception_for_dispatch(&self, dispatch_id: DispatchId) -> Option<PyException> {
        self.dispatch_state
            .original_exception_for_dispatch(dispatch_id)
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
        let current_dispatch_id = self.current_dispatch_id().or(active_dispatch_id);
        let allow_handler_context_conversion = conversion_hint
            || active_dispatch_id.is_some_and(|dispatch_id| {
                self.dispatch_supports_error_context_conversion(dispatch_id)
                    && matches!(
                        site,
                        GenErrorSite::RustProgramContinuation
                            | GenErrorSite::StepUserGeneratorDirect
                    )
            });
        let in_get_execution_context_dispatch = current_dispatch_id.is_some_and(|dispatch_id| {
            self.dispatch_state
                .dispatch_is_execution_context_effect(dispatch_id)
        });

        if !site.allows_error_conversion() && !allow_handler_context_conversion {
            if let Some(original) = self.active_error_dispatch_original_exception() {
                TraceState::set_exception_cause(&exception, &original);
            }
            return Mode::Throw(exception);
        }

        if Self::is_base_exception_not_exception(&exception) {
            return Mode::Throw(exception);
        }

        if in_get_execution_context_dispatch {
            if let Some(dispatch_id) = current_dispatch_id {
                if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                    TraceState::set_exception_cause(&exception, &original);
                }
            }
            return Mode::Throw(exception);
        }

        if let Some(original) = self.active_error_dispatch_original_exception() {
            if !allow_handler_context_conversion {
                TraceState::set_exception_cause(&exception, &original);
                return Mode::Throw(exception);
            }
        }

        match make_get_execution_context_effect() {
            Ok(effect) => {
                self.current_seg_mut().pending_error_context = Some(exception.clone());
                Mode::HandleYield(DoCtrl::Perform { effect })
            }
            Err(_) => Mode::Throw(exception),
        }
    }

    fn stream_debug_location(stream: &ASTStreamRef) -> Option<crate::ast_stream::StreamLocation> {
        let guard = stream.lock().expect("ASTStream lock poisoned");
        guard.debug_location()
    }

    fn maybe_emit_frame_entered(&mut self, metadata: &CallMetadata) {
        self.trace_state
            .maybe_emit_frame_entered(metadata, Self::program_call_repr(metadata));
    }

    fn maybe_emit_frame_exited(&mut self, metadata: &CallMetadata) {
        self.trace_state.maybe_emit_frame_exited(metadata);
    }

    fn maybe_emit_handler_threw_for_dispatch(
        &mut self,
        dispatch_id: DispatchId,
        exc: &PyException,
    ) {
        let handler_identity = self
            .current_handler_identity_for_dispatch(dispatch_id)
            .or_else(|| {
                let seg = self
                    .current_segment
                    .and_then(|seg_id| self.segments.get(seg_id))?;
                if seg.dispatch_id != Some(dispatch_id) {
                    return None;
                }
                let (handler_name, _, _, _) = self.marker_handler_trace_info(seg.marker)?;
                Some((0, handler_name))
            });
        let Some((handler_index, handler_name)) = handler_identity else {
            return;
        };
        self.trace_state.maybe_emit_handler_threw_for_dispatch(
            dispatch_id,
            handler_name,
            handler_index,
            Self::exception_repr(exc),
        );
    }

    fn maybe_emit_resume_event(
        &mut self,
        dispatch_id: DispatchId,
        handler_name: String,
        value_repr: Option<String>,
        continuation: &Continuation,
        transferred: bool,
    ) {
        self.trace_state.maybe_emit_resume_event(
            dispatch_id,
            handler_name,
            value_repr,
            continuation,
            transferred,
            TraceState::continuation_resume_location,
        );
    }

    pub fn assemble_trace(&self) -> Vec<TraceEntry> {
        let handlers = self.prompt_boundary_handler_lookup();
        self.trace_state.assemble_trace(
            &self.segments,
            self.current_segment,
            self.dispatch_state.contexts(),
            &handlers,
            Self::effect_repr,
        )
    }

    fn enrich_original_exception_with_context(
        original: PyException,
        context_value: Value,
    ) -> Result<PyException, PyException> {
        TraceState::enrich_original_exception_with_context(original, context_value)
    }

    pub fn assemble_active_chain(&self, exception: &PyException) -> Vec<ActiveChainEntry> {
        self.trace_state.assemble_active_chain(
            exception,
            &self.segments,
            self.current_segment,
            self.dispatch_state.contexts(),
        )
    }

    pub fn step(&mut self) -> StepEvent {
        let mode_kind = {
            let Some(seg) = self.current_segment_ref() else {
                return StepEvent::Error(VMError::internal("no current segment"));
            };
            match &seg.mode {
                Mode::Deliver(_) | Mode::Throw(_) => 0_u8,
                Mode::HandleYield(_) => 1_u8,
                Mode::Return(_) => 2_u8,
            }
        };

        self.debug.advance_step();

        if self.debug.trace_enabled {
            self.record_trace_entry();
        }

        if self.debug.is_enabled() {
            self.debug_step_entry();
        }

        let result = match mode_kind {
            0 => self.step_deliver_or_throw(),
            1 => self.step_handle_yield(),
            2 => self.step_return(),
            _ => unreachable!(),
        };

        if self.debug.is_enabled() {
            self.debug_step_exit(&result);
        }

        if self.debug.trace_enabled {
            self.record_trace_exit(&result);
        }

        result
    }

    fn record_trace_entry(&mut self) {
        let dispatch_depth = self.dispatch_state.depth();
        let (debug, segments, current_segment) =
            (&mut self.debug, &self.segments, self.current_segment);
        let Some(seg_id) = current_segment else {
            return;
        };
        let Some(seg) = segments.get(seg_id) else {
            return;
        };
        debug.record_trace_entry(&seg.mode, &seg.pending_python, dispatch_depth);
    }

    fn record_trace_exit(&mut self, result: &StepEvent) {
        let dispatch_depth = self.dispatch_state.depth();
        let (debug, segments, current_segment) =
            (&mut self.debug, &self.segments, self.current_segment);
        let Some(seg_id) = current_segment else {
            return;
        };
        let Some(seg) = segments.get(seg_id) else {
            return;
        };
        debug.record_trace_exit(&seg.mode, &seg.pending_python, dispatch_depth, result);
    }

    fn debug_step_entry(&self) {
        self.debug.debug_step_entry(
            &self.current_seg().mode,
            self.current_segment,
            &self.segments,
            self.dispatch_state.depth(),
            &self.current_seg().pending_python,
        );
    }

    fn debug_step_exit(&self, result: &StepEvent) {
        self.debug.debug_step_exit(result);
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
                let mode =
                    std::mem::replace(&mut self.current_seg_mut().mode, Mode::Deliver(Value::Unit));
                match mode {
                    Mode::Deliver(value) => {
                        // Don't free here — step_return reads the segment's caller.
                        self.current_seg_mut().mode = Mode::Return(value);
                        return StepEvent::Continue;
                    }
                    Mode::Throw(exc) => {
                        if let Some(caller_id) = caller {
                            self.segments.reparent_children(seg_id, Some(caller_id));
                            self.current_segment = Some(caller_id);
                            self.current_seg_mut().mode = Mode::Throw(exc);
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
        let mode = std::mem::replace(&mut self.current_seg_mut().mode, Mode::Deliver(Value::Unit));

        match frame {
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
            Frame::InterceptorApply(cont) => self.step_interceptor_apply_frame(*cont, mode),
            Frame::InterceptorEval(cont) => self.step_interceptor_eval_frame(*cont, mode),
            Frame::HandlerDispatch { dispatch_id } => {
                self.step_handler_dispatch_frame(dispatch_id, mode)
            }
            Frame::EvalReturn(continuation) => self.step_eval_return_frame(*continuation, mode),
            Frame::MapReturn {
                mapper,
                mapper_meta,
            } => self.step_map_return_frame(mapper, mapper_meta, mode),
            Frame::FlatMapBindResult => self.step_flat_map_bind_result_frame(mode),
            Frame::FlatMapBindSource {
                binder,
                binder_meta,
            } => self.step_flat_map_bind_source_frame(binder, binder_meta, mode),
            Frame::InterceptBodyReturn { marker } => {
                self.step_intercept_body_return_frame(marker, mode)
            }
        }
    }

    fn step_interceptor_apply_frame(
        &mut self,
        continuation: InterceptorContinuation,
        mode: Mode,
    ) -> StepEvent {
        if let Some(metadata) = continuation.interceptor_metadata.as_ref() {
            self.maybe_emit_frame_exited(metadata);
        }
        match mode {
            Mode::Deliver(value) => {
                self.current_seg_mut().mode =
                    self.handle_interceptor_apply_result(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.pop_interceptor_skip(continuation.marker);
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            _ => unreachable!(),
        }
    }

    fn step_interceptor_eval_frame(
        &mut self,
        continuation: InterceptorContinuation,
        mode: Mode,
    ) -> StepEvent {
        let seg = self.current_seg_mut();
        seg.interceptor_eval_depth = seg.interceptor_eval_depth.saturating_sub(1);
        match mode {
            Mode::Deliver(value) => {
                self.current_seg_mut().mode =
                    self.handle_interceptor_eval_result(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.pop_interceptor_skip(continuation.marker);
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            _ => unreachable!(),
        }
    }

    fn step_handler_dispatch_frame(&mut self, dispatch_id: DispatchId, mode: Mode) -> StepEvent {
        let _ = dispatch_id;
        match mode {
            Mode::Deliver(value) => self.handle_handler_return(value),
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            _ => unreachable!(),
        }
    }

    fn step_eval_return_frame(
        &mut self,
        continuation: EvalReturnContinuation,
        mode: Mode,
    ) -> StepEvent {
        match mode {
            Mode::Deliver(value) => {
                self.current_seg_mut().mode =
                    self.mode_from_eval_return_continuation(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            _ => unreachable!(),
        }
    }

    fn mode_from_eval_return_continuation(
        &mut self,
        continuation: EvalReturnContinuation,
        value: Value,
    ) -> Mode {
        match continuation {
            EvalReturnContinuation::ApplyResolveFunction {
                args,
                kwargs,
                metadata,
                evaluate_result,
            } => Mode::HandleYield(DoCtrl::Apply {
                f: CallArg::Value(value),
                args,
                kwargs,
                metadata,
                evaluate_result,
            }),
            EvalReturnContinuation::ApplyResolveArg {
                f,
                mut args,
                kwargs,
                arg_idx,
                metadata,
                evaluate_result,
            } => {
                let Some(slot) = args.get_mut(arg_idx) else {
                    return Mode::Throw(PyException::runtime_error(
                        "apply continuation arg index out of bounds",
                    ));
                };
                *slot = CallArg::Value(value);
                Mode::HandleYield(DoCtrl::Apply {
                    f,
                    args,
                    kwargs,
                    metadata,
                    evaluate_result,
                })
            }
            EvalReturnContinuation::ApplyResolveKwarg {
                f,
                args,
                mut kwargs,
                kwarg_idx,
                metadata,
                evaluate_result,
            } => {
                let Some((_, slot)) = kwargs.get_mut(kwarg_idx) else {
                    return Mode::Throw(PyException::runtime_error(
                        "apply continuation kwarg index out of bounds",
                    ));
                };
                *slot = CallArg::Value(value);
                Mode::HandleYield(DoCtrl::Apply {
                    f,
                    args,
                    kwargs,
                    metadata,
                    evaluate_result,
                })
            }
            EvalReturnContinuation::ExpandResolveFactory {
                args,
                kwargs,
                metadata,
            } => Mode::HandleYield(DoCtrl::Expand {
                factory: CallArg::Value(value),
                args,
                kwargs,
                metadata,
            }),
            EvalReturnContinuation::ExpandResolveArg {
                factory,
                mut args,
                kwargs,
                arg_idx,
                metadata,
            } => {
                let Some(slot) = args.get_mut(arg_idx) else {
                    return Mode::Throw(PyException::runtime_error(
                        "expand continuation arg index out of bounds",
                    ));
                };
                *slot = CallArg::Value(value);
                Mode::HandleYield(DoCtrl::Expand {
                    factory,
                    args,
                    kwargs,
                    metadata,
                })
            }
            EvalReturnContinuation::ExpandResolveKwarg {
                factory,
                args,
                mut kwargs,
                kwarg_idx,
                metadata,
            } => {
                let Some((_, slot)) = kwargs.get_mut(kwarg_idx) else {
                    return Mode::Throw(PyException::runtime_error(
                        "expand continuation kwarg index out of bounds",
                    ));
                };
                *slot = CallArg::Value(value);
                Mode::HandleYield(DoCtrl::Expand {
                    factory,
                    args,
                    kwargs,
                    metadata,
                })
            }
        }
    }

    fn step_map_return_frame(
        &mut self,
        mapper: PyShared,
        mapper_meta: CallMetadata,
        mode: Mode,
    ) -> StepEvent {
        match mode {
            Mode::Deliver(value) => {
                self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
                    f: CallArg::Value(Value::Python(mapper.into_inner())),
                    args: vec![CallArg::Value(value)],
                    kwargs: vec![],
                    metadata: mapper_meta,
                    evaluate_result: false,
                });
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            _ => unreachable!(),
        }
    }

    fn step_flat_map_bind_result_frame(&mut self, mode: Mode) -> StepEvent {
        match mode {
            Mode::Deliver(value) => {
                self.current_seg_mut().mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            _ => unreachable!(),
        }
    }

    fn step_flat_map_bind_source_frame(
        &mut self,
        binder: PyShared,
        binder_meta: CallMetadata,
        mode: Mode,
    ) -> StepEvent {
        match mode {
            Mode::Deliver(value) => {
                let Some(seg) = self.current_segment_mut() else {
                    return StepEvent::Error(VMError::internal(
                        "flat_map binder callback outside current segment",
                    ));
                };
                seg.push_frame(Frame::FlatMapBindResult);
                seg.mode = Mode::HandleYield(DoCtrl::Expand {
                    factory: CallArg::Value(Value::Python(binder.into_inner())),
                    args: vec![CallArg::Value(value)],
                    kwargs: vec![],
                    metadata: binder_meta,
                });
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            _ => unreachable!(),
        }
    }

    fn step_intercept_body_return_frame(&mut self, _marker: Marker, mode: Mode) -> StepEvent {
        match mode {
            Mode::Deliver(value) => self.handle_handler_return(value),
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            _ => unreachable!(),
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
                    TraceState::set_exception_cause(&exc, &original);
                }
                let dispatch_id = self.current_active_handler_dispatch_id().or_else(|| {
                    let dispatch_id = self.current_segment_dispatch_id_any()?;
                    if self.current_segment_is_active_handler_for_dispatch(dispatch_id) {
                        Some(dispatch_id)
                    } else {
                        None
                    }
                });
                if let Some(dispatch_id) = dispatch_id {
                    self.maybe_emit_handler_threw_for_dispatch(dispatch_id, &exc);
                    self.mark_dispatch_threw(dispatch_id);
                }
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            ASTStreamStep::NeedsPython(call) => {
                if matches!(
                    &call,
                    PythonCall::GenNext | PythonCall::GenSend { .. } | PythonCall::GenThrow { .. }
                ) {
                    self.current_seg_mut().pending_python =
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
                let Some(dispatch_id) = self.current_dispatch_id() else {
                    return StepEvent::Error(VMError::internal(
                        "RustProgramContinuation outside dispatch",
                    ));
                };
                let Some(ctx) = self.dispatch_state.find_by_dispatch_id(dispatch_id) else {
                    return StepEvent::Error(VMError::internal(
                        "RustProgramContinuation: dispatch context not found",
                    ));
                };
                let marker = ctx
                    .handler_chain
                    .get(ctx.handler_idx)
                    .copied()
                    .unwrap_or_else(Marker::fresh);
                let k = ctx.k_user.clone();
                self.current_seg_mut().pending_python =
                    Some(PendingPython::RustProgramContinuation { marker, k });
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
        self.current_seg_mut().mode =
            self.continue_interceptor_chain_mode(yielded, stream, metadata, chain, 0);
        StepEvent::Continue
    }

    fn finalize_stream_yield_mode(
        &mut self,
        yielded: DoCtrl,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
    ) -> Mode {
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
                    ))
                }
            }
        }
        Mode::HandleYield(yielded)
    }

    fn current_interceptor_chain(&self) -> Vec<Marker> {
        self.interceptor_state
            .current_chain(self.current_segment, &self.segments)
    }

    fn interceptor_visible_to_active_handler(&self, interceptor_marker: Marker) -> bool {
        self.interceptor_state.visible_to_active_handler(
            interceptor_marker,
            self.dispatch_state.contexts(),
            self.current_segment,
            &self.segments,
        )
    }

    fn is_interceptor_skipped(&self, marker: Marker) -> bool {
        InterceptorState::is_skipped(self.current_seg(), marker)
    }

    fn pop_interceptor_skip(&mut self, marker: Marker) {
        let seg = self.current_seg_mut();
        if InterceptorState::is_skipped(seg, marker) {
            InterceptorState::pop_skip(seg, marker);
        }
    }

    fn push_interceptor_skip(&mut self, marker: Marker) {
        InterceptorState::push_skip(self.current_seg_mut(), marker);
    }

    fn is_interceptor_eval_idle(&self) -> bool {
        self.current_seg().interceptor_eval_depth == 0
    }

    fn classify_interceptor_result_object(
        &self,
        result_obj: Py<PyAny>,
        original_obj: &PyShared,
        original_yielded: DoCtrl,
    ) -> Result<DoCtrl, PyException> {
        Python::attach(|py| {
            if result_obj.bind(py).as_ptr() == original_obj.bind(py).as_ptr() {
                return Ok(original_yielded);
            }
            classify_yielded_for_vm(self, py, result_obj.bind(py))
        })
    }

    fn continue_interceptor_chain_mode(
        &mut self,
        yielded: DoCtrl,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
        chain: Arc<Vec<Marker>>,
        start_idx: usize,
    ) -> Mode {
        let current = yielded;
        let mut idx = start_idx;

        while idx < chain.len() {
            let marker = chain[idx];
            idx += 1;
            if self.is_interceptor_skipped(marker) {
                continue;
            }
            if !self.interceptor_visible_to_active_handler(marker) {
                continue;
            }

            let Some(entry) = self.interceptor_state.get_entry(marker) else {
                continue;
            };

            let yielded_obj = match doctrl_to_pyexpr_for_vm(&current) {
                Ok(Some(obj)) => obj,
                Ok(None) => continue,
                Err(exc) => return Mode::Throw(exc),
            };

            return self.start_interceptor_invocation_mode(
                marker,
                entry,
                current,
                yielded_obj,
                stream,
                metadata,
                chain,
                idx,
            );
        }

        self.finalize_stream_yield_mode(current, stream, metadata)
    }

    fn fallback_interceptor_metadata() -> CallMetadata {
        CallMetadata::new(
            "WithIntercept.interceptor".to_string(),
            "<unknown>".to_string(),
            0,
            None,
            None,
        )
    }

    fn start_interceptor_invocation_mode(
        &mut self,
        marker: Marker,
        entry: InterceptorEntry,
        yielded: DoCtrl,
        yielded_obj: Py<PyAny>,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
        chain: Arc<Vec<Marker>>,
        next_idx: usize,
    ) -> Mode {
        let interceptor_callable = entry.interceptor.into_inner();
        let interceptor_meta = entry.metadata.clone();
        let yielded_obj_for_continuation = Python::attach(|py| yielded_obj.clone_ref(py));
        let apply_metadata = interceptor_meta
            .clone()
            .unwrap_or_else(Self::fallback_interceptor_metadata);
        self.push_interceptor_skip(marker);

        if self.current_segment.is_none() {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        }
        if let Some(meta) = interceptor_meta.as_ref() {
            self.maybe_emit_frame_entered(meta);
        }
        let continuation = InterceptorContinuation {
            marker,
            original_yielded: yielded,
            original_obj: PyShared::new(yielded_obj_for_continuation),
            emitter_stream: stream,
            emitter_metadata: metadata,
            chain,
            next_idx,
            interceptor_metadata: interceptor_meta,
        };
        let Some(seg) = self.current_segment_mut() else {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        };
        seg.push_frame(Frame::InterceptorApply(Box::new(continuation)));

        Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(interceptor_callable)),
            args: vec![CallArg::Value(Value::Python(yielded_obj))],
            kwargs: vec![],
            metadata: apply_metadata,
            evaluate_result: false,
        })
    }

    fn handle_interceptor_apply_result(
        &mut self,
        continuation: InterceptorContinuation,
        value: Value,
    ) -> Mode {
        let InterceptorContinuation {
            marker,
            original_yielded,
            original_obj,
            emitter_stream,
            emitter_metadata,
            chain,
            next_idx,
            ..
        } = continuation;
        let Value::Python(result_obj) = value else {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::type_error(
                "WithIntercept interceptor must return DoExpr",
            ));
        };

        let (is_direct_expr, is_doexpr) = InterceptorState::classify_result_shape(&result_obj);

        if is_direct_expr {
            let transformed = match self.classify_interceptor_result_object(
                result_obj,
                &original_obj,
                original_yielded,
            ) {
                Ok(expr) => expr,
                Err(exc) => {
                    self.pop_interceptor_skip(marker);
                    return Mode::Throw(exc);
                }
            };
            self.pop_interceptor_skip(marker);
            return self.continue_interceptor_chain_mode(
                transformed,
                emitter_stream,
                emitter_metadata,
                chain,
                next_idx,
            );
        }

        if is_doexpr {
            let Some(seg) = self.current_segment_mut() else {
                self.pop_interceptor_skip(marker);
                return Mode::Throw(PyException::runtime_error(
                    "current_segment_mut() returned None while evaluating interceptor result",
                ));
            };
            seg.interceptor_eval_depth = seg.interceptor_eval_depth.saturating_add(1);
            seg.push_frame(Frame::InterceptorEval(Box::new(InterceptorContinuation {
                marker,
                original_yielded,
                original_obj,
                emitter_stream,
                emitter_metadata,
                chain,
                next_idx,
                interceptor_metadata: None,
            })));

            let handlers = self.current_visible_handlers();
            return Mode::HandleYield(DoCtrl::Eval {
                expr: PyShared::new(result_obj),
                handlers,
                metadata: None,
            });
        }

        self.pop_interceptor_skip(marker);
        Mode::Throw(PyException::type_error(
            "WithIntercept interceptor must return DoExpr",
        ))
    }

    fn handle_interceptor_eval_result(
        &mut self,
        continuation: InterceptorContinuation,
        value: Value,
    ) -> Mode {
        let InterceptorContinuation {
            marker,
            original_yielded,
            original_obj,
            emitter_stream,
            emitter_metadata,
            chain,
            next_idx,
            ..
        } = continuation;
        let Value::Python(result_obj) = value else {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::type_error(
                "WithIntercept effectful interceptor must resolve to DoExpr",
            ));
        };

        let transformed = match self.classify_interceptor_result_object(
            result_obj,
            &original_obj,
            original_yielded,
        ) {
            Ok(expr) => expr,
            Err(exc) => {
                self.pop_interceptor_skip(marker);
                return Mode::Throw(exc);
            }
        };
        self.pop_interceptor_skip(marker);
        self.continue_interceptor_chain_mode(
            transformed,
            emitter_stream,
            emitter_metadata,
            chain,
            next_idx,
        )
    }

    fn step_handle_yield(&mut self) -> StepEvent {
        let yielded =
            match std::mem::replace(&mut self.current_seg_mut().mode, Mode::Deliver(Value::Unit)) {
                Mode::HandleYield(y) => y,
                other => {
                    self.current_seg_mut().mode = other;
                    return StepEvent::Error(VMError::internal("invalid mode for handle_yield"));
                }
            };

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
                metadata,
            } => self.handle_yield_with_intercept(interceptor, expr, metadata),
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
                evaluate_result,
            } => self.handle_yield_apply(f, args, kwargs, metadata, evaluate_result),
            // PendingPython::ExpandReturn is set in handle_yield_expand.
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                metadata,
            } => self.handle_yield_expand(factory, args, kwargs, metadata),
            DoCtrl::ASTStream { stream, metadata } => {
                self.handle_yield_ast_stream(stream, metadata)
            }
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
        self.current_seg_mut().mode = Mode::Deliver(value);
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

    fn handle_yield_with_intercept(
        &mut self,
        interceptor: PyShared,
        expr: Py<PyAny>,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        self.handle_with_intercept(interceptor, expr, metadata)
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
        self.current_seg_mut().pending_python = Some(PendingPython::AsyncEscape);
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
        evaluate_result: bool,
    ) -> StepEvent {
        if let CallArg::Expr(expr) = &f {
            let expr = expr.clone();
            return self.eval_then_reenter_call(
                expr,
                EvalReturnContinuation::ApplyResolveFunction {
                    args,
                    kwargs,
                    metadata,
                    evaluate_result,
                },
            );
        }

        if let Some((arg_idx, expr)) = Self::first_expr_arg(&args) {
            return self.eval_then_reenter_call(
                expr,
                EvalReturnContinuation::ApplyResolveArg {
                    f,
                    args,
                    kwargs,
                    arg_idx,
                    metadata,
                    evaluate_result,
                },
            );
        }

        if let Some((kwargs_idx, expr)) = Self::first_expr_kwarg(&kwargs) {
            return self.eval_then_reenter_call(
                expr,
                EvalReturnContinuation::ApplyResolveKwarg {
                    f,
                    args,
                    kwargs,
                    kwarg_idx: kwargs_idx,
                    metadata,
                    evaluate_result,
                },
            );
        }

        let func = match f {
            CallArg::Value(Value::Python(func)) => PyShared::new(func),
            CallArg::Value(Value::PythonHandlerCallable(func)) => PyShared::new(func),
            CallArg::Value(Value::RustProgramInvocation(invocation)) => {
                return self.invoke_rust_program(invocation);
            }
            CallArg::Value(other) => {
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Apply f must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
            CallArg::Expr(_) => unreachable!(),
        };

        self.current_seg_mut().pending_python = Some(PendingPython::CallFuncReturn {
            metadata: Some(metadata),
            evaluate_result,
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
                EvalReturnContinuation::ExpandResolveFactory {
                    args,
                    kwargs,
                    metadata,
                },
            );
        }

        if let Some((arg_idx, expr)) = Self::first_expr_arg(&args) {
            return self.eval_then_reenter_call(
                expr,
                EvalReturnContinuation::ExpandResolveArg {
                    factory,
                    args,
                    kwargs,
                    arg_idx,
                    metadata,
                },
            );
        }

        if let Some((kwargs_idx, expr)) = Self::first_expr_kwarg(&kwargs) {
            return self.eval_then_reenter_call(
                expr,
                EvalReturnContinuation::ExpandResolveKwarg {
                    factory,
                    args,
                    kwargs,
                    kwarg_idx: kwargs_idx,
                    metadata,
                },
            );
        }

        let (func, handler_return) = match factory {
            CallArg::Value(Value::Python(factory)) => (PyShared::new(factory), false),
            CallArg::Value(Value::PythonHandlerCallable(factory)) => (PyShared::new(factory), true),
            CallArg::Value(Value::RustProgramInvocation(invocation)) => {
                return self.invoke_rust_program(invocation);
            }
            CallArg::Value(other) => {
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Expand factory must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
            CallArg::Expr(_) => unreachable!(),
        };

        self.current_seg_mut().pending_python = Some(PendingPython::ExpandReturn {
            metadata: Some(metadata),
            handler_return,
        });
        StepEvent::NeedsPython(PythonCall::CallFunc {
            func,
            args: Self::collect_value_args(args),
            kwargs: Self::collect_value_kwargs(kwargs),
        })
    }

    fn handle_yield_ast_stream(
        &mut self,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        if let Some(ref m) = metadata {
            self.maybe_emit_frame_entered(m);
        }
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal(
                "handle_yield_ast_stream called without current segment",
            ));
        };
        seg.push_frame(Frame::Program { stream, metadata });
        seg.mode = Mode::Deliver(Value::Unit);
        StepEvent::Continue
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
                    match frame {
                        Frame::Program {
                            metadata: Some(m), ..
                        } => stack.push(m.clone()),
                        Frame::InterceptorApply(continuation)
                        | Frame::InterceptorEval(continuation) => {
                            if let Some(metadata) = continuation.emitter_metadata.as_ref() {
                                stack.push(metadata.clone());
                            }
                        }
                        _ => {}
                    }
                }
                seg_id = seg.caller;
            } else {
                break;
            }
        }
        self.current_seg_mut().mode = Mode::Deliver(Value::CallStack(stack));
        StepEvent::Continue
    }

    fn handle_yield_get_trace(&mut self) -> StepEvent {
        self.current_seg_mut().mode = Mode::Deliver(Value::Trace(self.assemble_trace()));
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
        let value =
            match std::mem::replace(&mut self.current_seg_mut().mode, Mode::Deliver(Value::Unit)) {
                Mode::Return(v) => v,
                other => {
                    self.current_seg_mut().mode = other;
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
                self.current_seg_mut().mode = Mode::Deliver(value);
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
        let pending = match self
            .current_segment_mut()
            .and_then(|seg| seg.pending_python.take())
        {
            Some(p) => p,
            None => {
                if let Some(seg) = self.current_segment_mut() {
                    seg.mode = Mode::Throw(PyException::runtime_error(
                        "receive_python_result called with no pending_python",
                    ));
                }
                return;
            }
        };

        match pending {
            PendingPython::EvalExpr { metadata } => {
                self.receive_eval_expr_result(metadata, outcome)
            }
            PendingPython::CallFuncReturn {
                metadata,
                evaluate_result,
            } => self.receive_call_func_result(metadata, evaluate_result, outcome),
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
                self.current_seg_mut().mode = Mode::HandleYield(yielded);
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode =
                    self.mode_after_generror(GenErrorSite::EvalExpr, exception, false);
            }
            PyCallOutcome::GenReturn(value) | PyCallOutcome::Value(value) => {
                self.current_seg_mut().mode = Mode::Deliver(value);
            }
        }
    }

    fn receive_call_func_result(
        &mut self,
        _metadata: Option<CallMetadata>,
        evaluate_result: bool,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::Value(value) => {
                if evaluate_result {
                    match self.classify_apply_result_as_doctrl(&value) {
                        Ok(Some(doctrl)) => {
                            self.current_seg_mut().mode = Mode::HandleYield(doctrl);
                        }
                        Ok(None) => {
                            self.current_seg_mut().mode = Mode::Deliver(value);
                        }
                        Err(exception) => {
                            self.current_seg_mut().mode = Mode::Throw(exception);
                        }
                    }
                } else {
                    self.current_seg_mut().mode = Mode::Deliver(value);
                }
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode =
                    self.mode_after_generror(GenErrorSite::CallFuncReturn, exception, false);
            }
            _ => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn classify_apply_result_as_doctrl(
        &self,
        value: &Value,
    ) -> Result<Option<DoCtrl>, PyException> {
        let Value::Python(result_obj) = value else {
            return Ok(None);
        };

        Python::attach(|py| {
            let result_bound = result_obj.bind(py);
            let is_expression_like = result_bound.is_instance_of::<PyDoExprBase>()
                || result_bound.is_instance_of::<DoeffGenerator>()
                || result_bound.is_instance_of::<PyEffectBase>();
            if !is_expression_like {
                return Ok(None);
            }
            classify_yielded_for_vm(self, py, result_bound).map(Some)
        })
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
                        let Some(dispatch_id) = self
                            .current_dispatch_id()
                            .or_else(|| self.current_active_handler_dispatch_id())
                        else {
                            self.current_seg_mut().mode = Mode::Throw(PyException::runtime_error(
                                "handler dispatch continuation outside dispatch",
                            ));
                            return;
                        };
                        let Some(seg) = self.current_segment_mut() else {
                            return;
                        };
                        seg.push_frame(Frame::HandlerDispatch { dispatch_id });
                        self.current_seg_mut().mode =
                            Mode::HandleYield(DoCtrl::ASTStream { stream, metadata });
                    }
                    Err(exception) => {
                        self.current_seg_mut().mode = Mode::Throw(exception);
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
                        self.current_seg_mut().mode =
                            Mode::HandleYield(DoCtrl::ASTStream { stream, metadata });
                    }
                    Err(exception) => {
                        self.current_seg_mut().mode = Mode::Throw(exception);
                    }
                }
            }
            other => {
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "ExpandReturn: expected DoeffGenerator, got {other:?}"
                )));
            }
        }
    }

    fn receive_expand_gen_error(&mut self, handler_return: bool, exception: PyException) {
        if handler_return {
            let dispatch_id = self.current_active_handler_dispatch_id().or_else(|| {
                let dispatch_id = self.current_segment_dispatch_id_any()?;
                if self.current_segment_is_active_handler_for_dispatch(dispatch_id) {
                    Some(dispatch_id)
                } else {
                    None
                }
            });
            if let Some(dispatch_id) = dispatch_id {
                if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                    TraceState::set_exception_cause(&exception, &original);
                }
                self.maybe_emit_handler_threw_for_dispatch(dispatch_id, &exception);
                self.mark_dispatch_threw(dispatch_id);
            }
            self.current_seg_mut().mode =
                self.mode_after_generror(GenErrorSite::ExpandReturnHandler, exception, false);
            return;
        }

        self.current_seg_mut().mode =
            self.mode_after_generror(GenErrorSite::ExpandReturnProgram, exception, false);
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
                    return;
                }
                let _ = self.handle_stream_yield(yielded, stream, metadata);
            }
            PyCallOutcome::GenReturn(value) => {
                if let Some(ref m) = metadata {
                    self.maybe_emit_frame_exited(m);
                }
                self.current_seg_mut().mode = Mode::Deliver(value);
            }
            PyCallOutcome::GenError(exception) => {
                let mut site = GenErrorSite::StepUserGeneratorDirect;
                if let Some(dispatch_id) = self.current_segment_dispatch_id_any().and_then(|id| {
                    let completed = self
                        .dispatch_state
                        .find_by_dispatch_id(id)
                        .is_some_and(|ctx| ctx.completed);
                    if completed && self.dispatch_uses_user_continuation_stream(id, &stream) {
                        Some(id)
                    } else {
                        None
                    }
                }) {
                    site = GenErrorSite::StepUserGeneratorConverted;
                    if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                        TraceState::set_exception_cause(&exception, &original);
                    }
                    self.current_seg_mut().mode = self.mode_after_generror(site, exception, false);
                    return;
                }
                let active_dispatch_id = self.current_active_handler_dispatch_id();
                let fallback_active_handler_dispatch = || {
                    let dispatch_id = self.current_segment_dispatch_id_any()?;
                    if self.current_segment_is_active_handler_for_dispatch(dispatch_id) {
                        Some(dispatch_id)
                    } else {
                        None
                    }
                };
                if let Some(dispatch_id) = active_dispatch_id
                    .or_else(|| self.current_segment_dispatch_id())
                    .or_else(fallback_active_handler_dispatch)
                {
                    if self.dispatch_uses_user_continuation_stream(dispatch_id, &stream) {
                        site = GenErrorSite::StepUserGeneratorConverted;
                    } else if self.current_segment_is_active_handler_for_dispatch(dispatch_id) {
                        if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                            TraceState::set_exception_cause(&exception, &original);
                        }
                        self.maybe_emit_handler_threw_for_dispatch(dispatch_id, &exception);
                        self.mark_dispatch_threw(dispatch_id);
                    }
                }
                self.current_seg_mut().mode = self.mode_after_generror(site, exception, false);
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
                self.current_seg_mut().mode = Mode::Deliver(result);
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode = self.mode_after_generror(
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
                self.current_seg_mut().mode = Mode::Deliver(result);
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode =
                    self.mode_after_generror(GenErrorSite::AsyncEscape, exception, false);
            }
            _ => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_unexpected_outcome(&mut self) {
        self.current_seg_mut().mode = Mode::Throw(PyException::runtime_error(
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

    fn current_segment_dispatch_id(&self) -> Option<DispatchId> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let Some(dispatch_id) = seg.dispatch_id {
                if self
                    .dispatch_state
                    .find_by_dispatch_id(dispatch_id)
                    .is_some_and(|ctx| !ctx.completed)
                {
                    return Some(dispatch_id);
                }
            }
            cursor = seg.caller;
        }
        None
    }

    fn current_segment_dispatch_id_any(&self) -> Option<DispatchId> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let Some(dispatch_id) = seg.dispatch_id {
                if self
                    .dispatch_state
                    .find_by_dispatch_id(dispatch_id)
                    .is_some()
                {
                    return Some(dispatch_id);
                }
            }
            cursor = seg.caller;
        }
        None
    }

    pub(crate) fn current_dispatch_id(&self) -> Option<DispatchId> {
        self.current_segment_dispatch_id()
    }

    pub fn lazy_pop_completed(&mut self) {
        self.dispatch_state.lazy_pop_completed();
    }

    pub fn find_matching_handler(
        &self,
        handler_chain: &[Marker],
        effect: &DispatchEffect,
    ) -> Result<(usize, Marker, Handler), VMError> {
        for (idx, marker) in handler_chain.iter().copied().enumerate() {
            let Some((_prompt_seg_id, handler, _py_identity)) =
                self.find_prompt_boundary_by_marker(marker)
            else {
                return Err(VMError::internal(format!(
                    "find_matching_handler: missing handler marker {} at index {}",
                    marker.raw(),
                    idx
                )));
            };
            if handler.can_handle(effect)? {
                return Ok((idx, marker, handler));
            }
        }
        Err(VMError::no_matching_handler(effect.clone()))
    }

    pub fn start_dispatch(&mut self, effect: DispatchEffect) -> Result<StepEvent, VMError> {
        self.lazy_pop_completed();
        let original_exception = self
            .current_segment_mut()
            .and_then(|seg| seg.pending_error_context.take());

        let seg_id = self
            .current_segment
            .ok_or_else(|| VMError::internal("no current segment during dispatch"))?;
        let handler_chain = self.handlers_in_caller_chain(seg_id);
        let busy_markers = self.active_busy_markers();

        if handler_chain.is_empty() {
            if let Some(original) = original_exception.clone() {
                self.current_seg_mut().mode = Mode::Throw(original);
                return Ok(StepEvent::Continue);
            }
            return Err(VMError::unhandled_effect(effect));
        }

        let mut selected: Option<(usize, HandlerChainEntry)> = None;
        for (idx, entry) in handler_chain.iter().enumerate() {
            if busy_markers.contains(&entry.marker) {
                continue;
            }
            if entry.handler.can_handle(&effect)? {
                selected = Some((idx, entry.clone()));
                break;
            }
        }
        let (handler_idx, selected) = match selected {
            Some(found) => found,
            None => {
                if let Some(original) = original_exception.clone() {
                    self.current_seg_mut().mode = Mode::Throw(original);
                    return Ok(StepEvent::Continue);
                }
                return Err(VMError::no_matching_handler(effect));
            }
        };

        let handler_marker = selected.marker;
        let prompt_seg_id = selected.prompt_seg_id;
        let handler = selected.handler.clone();
        let dispatch_id = DispatchId::fresh();
        let is_execution_context_effect = Self::is_execution_context_effect(&effect);
        let supports_error_context_conversion = handler.supports_error_context_conversion();
        let mut handler_chain_snapshot: Vec<HandlerSnapshotEntry> = Vec::new();
        for entry in &handler_chain {
            let (name, kind, file, line) =
                Self::handler_trace_info(&entry.handler);
            handler_chain_snapshot.push(HandlerSnapshotEntry {
                handler_name: name,
                handler_kind: kind,
                source_file: file,
                source_line: line,
            });
        }

        let current_seg = self
            .segments
            .get(seg_id)
            .ok_or_else(|| VMError::invalid_segment("current segment not found"))?;
        let k_user = Continuation::capture(current_seg, seg_id, Some(dispatch_id));

        let mut handler_seg = Segment::new(handler_marker, Some(prompt_seg_id));
        handler_seg.handler_lookup_anchor = current_seg.handler_lookup_anchor.or(Some(seg_id));
        handler_seg.handler_lookup_anchor_marker = current_seg
            .handler_lookup_anchor_marker
            .or(Some(current_seg.marker));
        handler_seg.dispatch_id = Some(dispatch_id);
        self.copy_interceptor_guard_state(Some(seg_id), &mut handler_seg);
        let handler_seg_id = self.alloc_segment(handler_seg);
        self.current_segment = Some(handler_seg_id);

        self.dispatch_state.push_dispatch(DispatchContext {
            dispatch_id,
            effect: effect.clone(),
            is_execution_context_effect,
            handler_chain: handler_chain.iter().map(|entry| entry.marker).collect(),
            handler_idx,
            supports_error_context_conversion,
            k_user: k_user.clone(),
            prompt_seg_id,
            completed: false,
            original_exception,
        });

        let (handler_name, handler_kind, handler_source_file, handler_source_line) =
            Self::handler_trace_info(&handler);
        let effect_site = TraceState::effect_site_from_continuation(&k_user);
        self.trace_state.emit_dispatch_started(
            dispatch_id,
            Self::effect_repr(&effect),
            is_execution_context_effect,
            Self::effect_creation_site_from_continuation(&k_user),
            handler_name,
            handler_kind,
            handler_source_file,
            handler_source_line,
            handler_chain_snapshot,
            effect_site.as_ref().map(|(frame_id, _, _, _)| *frame_id),
            effect_site
                .as_ref()
                .map(|(_, function_name, _, _)| function_name.clone()),
            effect_site
                .as_ref()
                .map(|(_, _, source_file, _)| source_file.clone()),
            effect_site
                .as_ref()
                .map(|(_, _, _, source_line)| *source_line),
        );

        if selected.py_identity.is_some() || handler.py_identity().is_some() {
            self.register_continuation(k_user.clone());
        }
        let ir_node = handler.invoke(effect, k_user);
        Ok(self.evaluate(ir_node))
    }

    fn check_dispatch_completion(&mut self, k: &Continuation) {
        self.dispatch_state.check_dispatch_completion(k);
    }

    fn error_dispatch_for_continuation(
        &self,
        k: &Continuation,
    ) -> Option<(DispatchId, PyException, bool)> {
        self.dispatch_state.error_dispatch_for_continuation(k)
    }

    fn mark_dispatch_threw(&mut self, dispatch_id: DispatchId) {
        self.dispatch_state
            .mark_dispatch_threw(dispatch_id, &mut self.consumed_cont_ids);
    }

    fn mark_dispatch_completed(&mut self, dispatch_id: DispatchId) {
        self.dispatch_state
            .mark_dispatch_completed(dispatch_id, &mut self.consumed_cont_ids);
    }

    fn dispatch_has_terminal_handler_action(&self, dispatch_id: DispatchId) -> bool {
        self.dispatch_state
            .dispatch_has_terminal_handler_action(dispatch_id, self.trace_state.events())
    }

    fn finalize_active_dispatches_as_threw(&mut self, exception: &PyException) {
        let exception_repr = Self::exception_repr(exception);
        for idx in 0..self.dispatch_state.depth() {
            let Some(ctx) = self.dispatch_state.get(idx) else {
                continue;
            };
            let dispatch_id = ctx.dispatch_id;
            let completed = ctx.completed;
            if completed {
                continue;
            }
            if self.dispatch_has_terminal_handler_action(dispatch_id) {
                self.dispatch_state
                    .mark_completed_at(idx, &mut self.consumed_cont_ids);
                continue;
            }
            let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            else {
                self.dispatch_state
                    .mark_completed_at(idx, &mut self.consumed_cont_ids);
                continue;
            };
            self.trace_state.emit_handler_completed(
                dispatch_id,
                handler_name,
                handler_index,
                HandlerAction::Threw {
                    exception_repr: exception_repr.clone(),
                },
            );
            self.dispatch_state
                .mark_completed_at(idx, &mut self.consumed_cont_ids);
        }
    }

    pub fn install_handler(
        &mut self,
        marker: Marker,
        handler: Handler,
        py_identity: Option<PyShared>,
    ) {
        self.installed_handlers
            .retain(|entry| entry.marker != marker);
        self.installed_handlers.push(InstalledHandler {
            marker,
            handler,
            py_identity,
        });
    }

    pub fn remove_handler(&mut self, marker: Marker) -> bool {
        let before = self.installed_handlers.len();
        self.installed_handlers
            .retain(|entry| entry.marker != marker);
        before != self.installed_handlers.len()
    }

    pub fn installed_handler_markers(&self) -> Vec<Marker> {
        self.installed_handlers
            .iter()
            .map(|entry| entry.marker)
            .collect()
    }

    pub fn install_handler_on_segment(
        &mut self,
        marker: Marker,
        prompt_seg_id: SegmentId,
        handler: Handler,
        py_identity: Option<PyShared>,
    ) -> bool {
        let Some(seg) = self.segments.get_mut(prompt_seg_id) else {
            let prompt_seg = Segment::new_prompt(
                marker,
                None,
                marker,
                handler.clone(),
                None,
                py_identity.clone(),
            );
            self.alloc_segment(prompt_seg);
            self.track_run_handler(&handler);
            return true;
        };
        seg.kind = SegmentKind::PromptBoundary {
            handled_marker: marker,
            handler: handler.clone(),
            return_clause: None,
            py_identity,
        };
        self.track_run_handler(&handler);
        true
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
                self.trace_state.emit_handler_completed(
                    dispatch_id,
                    handler_name.clone(),
                    handler_index,
                    kind.handler_action(value_repr.clone()),
                );
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
    ) {
        match kind {
            ContinuationActivationKind::Resume => {
                self.check_dispatch_completion(k);
            }
            ContinuationActivationKind::Transfer => {
                self.check_dispatch_completion(k);
            }
        }
    }

    fn continuation_segment_dispatch_id(&self, k: &Continuation) -> Option<DispatchId> {
        if let Some(dispatch_id) = k.dispatch_id {
            if self
                .dispatch_state
                .find_by_dispatch_id(dispatch_id)
                .is_some_and(|ctx| !ctx.completed)
            {
                return Some(dispatch_id);
            }
        }

        self.segments
            .get(k.segment_id)
            .and_then(|source_seg| source_seg.dispatch_id)
            .filter(|dispatch_id| {
                self.dispatch_state
                    .find_by_dispatch_id(*dispatch_id)
                    .is_some_and(|ctx| !ctx.completed)
            })
    }

    fn enter_continuation_segment_with_dispatch(
        &mut self,
        k: &Continuation,
        caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) {
        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller,
            handler_lookup_anchor: k.handler_lookup_anchor.or(Some(k.segment_id)),
            handler_lookup_anchor_marker: k.handler_lookup_anchor_marker.or(Some(k.marker)),
            kind: SegmentKind::Normal,
            dispatch_id,
            mode: k.mode.as_ref().clone(),
            pending_python: k
                .pending_python
                .as_ref()
                .map(|pending| pending.as_ref().clone()),
            pending_error_context: k.pending_error_context.clone(),
            interceptor_eval_depth: k.interceptor_eval_depth,
            interceptor_skip_stack: k.interceptor_skip_stack.clone(),
        };
        let exec_seg_id = self.alloc_segment(exec_seg);
        self.current_segment = Some(exec_seg_id);
    }

    fn enter_continuation_segment(&mut self, k: &Continuation, caller: Option<SegmentId>) {
        let dispatch_id = self.continuation_segment_dispatch_id(k);
        self.enter_continuation_segment_with_dispatch(k, caller, dispatch_id);
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
                let caller = kind
                    .caller_segment(self.current_segment)
                    .and_then(|seg_id| self.segments.get(seg_id))
                    .and_then(|seg| seg.caller);
                self.enter_continuation_segment_with_dispatch(&k, caller, None);
                self.current_seg_mut().mode = Mode::Throw(enriched_exception);
                return StepEvent::Continue;
            }
        }
        self.check_dispatch_completion_after_activation(kind, &k);

        self.enter_continuation_segment(&k, kind.caller_segment(self.current_segment));
        self.current_seg_mut().mode = Mode::Deliver(value);
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
            if self.original_exception_for_dispatch(dispatch_id).is_some() {
                self.check_dispatch_completion(k);
            }
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
                self.trace_state.emit_handler_completed(
                    dispatch_id,
                    handler_name,
                    handler_index,
                    HandlerAction::Threw {
                        exception_repr: Self::exception_repr(&exception),
                    },
                );
            }
        }
        if terminal_dispatch_completion {
            self.check_dispatch_completion(&k);
        } else {
            self.check_dispatch_completion_for_non_terminal_throw(&k);
        }

        let dispatch_id: Option<DispatchId> = None;
        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller: self.current_segment,
            handler_lookup_anchor: k.handler_lookup_anchor.or(Some(k.segment_id)),
            handler_lookup_anchor_marker: k.handler_lookup_anchor_marker.or(Some(k.marker)),
            kind: SegmentKind::Normal,
            dispatch_id,
            mode: k.mode.as_ref().clone(),
            pending_python: k
                .pending_python
                .as_ref()
                .map(|pending| pending.as_ref().clone()),
            pending_error_context: k.pending_error_context.clone(),
            interceptor_eval_depth: k.interceptor_eval_depth,
            interceptor_skip_stack: k.interceptor_skip_stack.clone(),
        };
        let exec_seg_id = self.alloc_segment(exec_seg);

        self.current_segment = Some(exec_seg_id);
        self.current_seg_mut().mode =
            if terminal_dispatch_completion && thrown_by_context_conversion_handler {
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
        let plan = match DispatchState::prepare_with_handler(
            handler,
            explicit_py_identity,
            self.current_segment,
        ) {
            Ok(plan) => plan,
            Err(err) => return StepEvent::Error(err),
        };

        let mut prompt_seg = Segment::new_prompt(
            plan.handler_marker,
            Some(plan.outside_seg_id),
            plan.handler_marker,
            plan.handler.clone(),
            None,
            plan.py_identity.clone(),
        );
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), &mut prompt_seg);
        let prompt_seg_id = self.alloc_segment(prompt_seg);
        self.track_run_handler(&plan.handler);

        let mut body_seg = Segment::new(plan.handler_marker, Some(prompt_seg_id));
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), &mut body_seg);
        let body_seg_id = self.alloc_segment(body_seg);

        self.current_segment = Some(body_seg_id);
        self.current_seg_mut().pending_python = Some(PendingPython::EvalExpr { metadata: None });
        StepEvent::NeedsPython(PythonCall::EvalExpr {
            expr: PyShared::new(program),
        })
    }

    fn handle_with_intercept(
        &mut self,
        interceptor: PyShared,
        program: Py<PyAny>,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let body_seg = match self.interceptor_state.prepare_with_intercept(
            interceptor,
            metadata,
            self.current_segment,
            &self.segments,
        ) {
            Ok(segment) => segment,
            Err(err) => return StepEvent::Error(err),
        };
        let body_seg_id = self.alloc_segment(body_seg);

        self.current_segment = Some(body_seg_id);
        self.current_seg_mut().pending_python = Some(PendingPython::EvalExpr { metadata: None });
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
            self.trace_state.emit_capture(event);
        }
    }

    fn handle_forward(&mut self, kind: ForwardKind, effect: DispatchEffect) -> StepEvent {
        let Some(dispatch_id) = self.current_dispatch_id() else {
            return StepEvent::Error(VMError::internal(kind.outside_dispatch_error()));
        };
        let (handler_chain, start_idx, from_idx, parent_k_user) =
            match self.dispatch_state.find_by_dispatch_id(dispatch_id) {
                Some(ctx) => (
                    ctx.handler_chain.clone(),
                    ctx.handler_idx + 1,
                    ctx.handler_idx,
                    if kind == ForwardKind::Delegate {
                        Some(ctx.k_user.clone())
                    } else {
                        None
                    },
                ),
                None => {
                    return StepEvent::Error(VMError::internal(format!(
                        "{}: dispatch {} not found",
                        kind.missing_handler_context(),
                        dispatch_id.raw()
                    )))
                }
            };

        let inner_seg_id = self.current_segment;
        let visible_chain = inner_seg_id
            .map(|seg_id| self.handlers_in_caller_chain(seg_id))
            .unwrap_or_default();

        match kind {
            ForwardKind::Delegate => {
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
                let Some(ctx) = self.dispatch_state.find_mut_by_dispatch_id(dispatch_id) else {
                    return StepEvent::Error(VMError::internal(
                        "Delegate called with missing dispatch context",
                    ));
                };
                ctx.k_user = k_new;
            }
            ForwardKind::Pass => {
                self.clear_segment_frames(inner_seg_id);
            }
        }

        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            let Some(entry) = visible_chain.iter().find(|entry| entry.marker == marker)
            else {
                return StepEvent::Error(VMError::internal(format!(
                    "{}: missing handler marker {} at index {}",
                    kind.missing_handler_context(),
                    marker.raw(),
                    idx
                )));
            };
            let handler = entry.handler.clone();
            let py_identity = entry.py_identity.clone();
            let can_handle = match handler.can_handle(&effect) {
                Ok(value) => value,
                Err(err) => return StepEvent::Error(err),
            };
            if can_handle {
                let supports_error_context_conversion = handler.supports_error_context_conversion();
                self.maybe_emit_forward_capture_event(
                    kind,
                    dispatch_id,
                    &handler_chain,
                    from_idx,
                    idx,
                    marker,
                );
                let k_user = {
                    let Some(ctx) = self.dispatch_state.find_mut_by_dispatch_id(dispatch_id) else {
                        return StepEvent::Error(VMError::internal(
                            "forward target dispatch context not found",
                        ));
                    };
                    ctx.handler_idx = idx;
                    ctx.supports_error_context_conversion = supports_error_context_conversion;
                    ctx.effect = effect.clone();
                    ctx.k_user.clone()
                };

                let (inner_anchor, inner_anchor_marker) = inner_seg_id
                    .and_then(|seg_id| {
                        self.segments.get(seg_id).map(|seg| {
                            (
                                seg.handler_lookup_anchor.or(Some(seg_id)),
                                seg.handler_lookup_anchor_marker.or(Some(seg.marker)),
                            )
                        })
                    })
                    .unwrap_or((None, None));
                let mut handler_seg = Segment::new(marker, inner_seg_id);
                handler_seg.handler_lookup_anchor = inner_anchor;
                handler_seg.handler_lookup_anchor_marker = inner_anchor_marker;
                handler_seg.dispatch_id = Some(dispatch_id);
                self.copy_interceptor_guard_state(inner_seg_id, &mut handler_seg);
                let handler_seg_id = self.alloc_segment(handler_seg);
                self.current_segment = Some(handler_seg_id);

                if py_identity.is_some() || handler.py_identity().is_some() {
                    self.register_continuation(k_user.clone());
                }
                let ir_node = handler.invoke(effect.clone(), k_user);
                return self.evaluate(ir_node);
            }
        }

        if let Some(original_exception) = self.original_exception_for_dispatch(dispatch_id) {
            self.mark_dispatch_completed(dispatch_id);
            self.current_seg_mut().mode = Mode::Throw(original_exception);
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

    fn handle_handler_return(&mut self, value: Value) -> StepEvent {
        self.lazy_pop_completed();

        if let Value::Python(obj) = &value {
            let should_eval = Python::attach(|py| {
                let bound = obj.bind(py);
                bound.is_instance_of::<PyDoExprBase>()
                    || bound.is_instance_of::<DoeffGenerator>()
                    || bound.is_instance_of::<PyEffectBase>()
            });

            if should_eval && self.is_interceptor_eval_idle() {
                let handlers = self.current_visible_handlers();
                let expr = Python::attach(|py| PyShared::new(obj.clone_ref(py)));
                let marker = self.current_seg().marker;
                let Some(seg) = self.current_segment_mut() else {
                    return StepEvent::Error(VMError::internal(
                        "current_segment_mut() returned None in handle_handler_return \
                         while scheduling Eval callback",
                    ));
                };
                seg.push_frame(Frame::InterceptBodyReturn { marker });
                self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Eval {
                    expr,
                    handlers,
                    metadata: None,
                });
                return StepEvent::Continue;
            }
        }

        let Some(dispatch_id) = self.current_dispatch_id() else {
            self.current_seg_mut().mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };
        let Some(top_snapshot) = self
            .dispatch_state
            .find_by_dispatch_id(dispatch_id)
            .cloned()
        else {
            self.current_seg_mut().mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };

        let Some((handler_index, handler_name)) =
            self.current_handler_identity_for_dispatch(top_snapshot.dispatch_id)
        else {
            self.current_seg_mut().mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };
        let value_repr = Self::value_repr(&value);
        self.trace_state.emit_handler_completed(
            top_snapshot.dispatch_id,
            handler_name.clone(),
            handler_index,
            HandlerAction::Returned {
                value_repr: value_repr.clone(),
            },
        );
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
            let Some(ctx) = self.dispatch_state.find_mut_by_dispatch_id(dispatch_id) else {
                self.current_seg_mut().mode = Mode::Deliver(value);
                return StepEvent::Continue;
            };

            if caller_id == ctx.prompt_seg_id {
                ctx.completed = true;
                self.consumed_cont_ids.insert(ctx.k_user.cont_id);
            }

            if ctx.completed {
                ctx.original_exception.clone()
            } else {
                None
            }
        };

        if let Some(original) = original_exception {
            self.current_seg_mut().mode =
                match Self::enrich_original_exception_with_context(original, value) {
                    Ok(exception) => Mode::Throw(exception),
                    Err(effect_err) => Mode::Throw(effect_err),
                };
            return StepEvent::Continue;
        }

        self.current_seg_mut().mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    fn current_visible_handlers(&self) -> Vec<Handler> {
        self.current_handler_chain()
            .into_iter()
            .map(|entry| entry.handler)
            .collect()
    }

    fn handle_map(
        &mut self,
        source: PyShared,
        mapper: PyShared,
        mapper_meta: CallMetadata,
    ) -> StepEvent {
        let handlers = self.current_visible_handlers();

        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("Map outside current segment"));
        };
        seg.push_frame(Frame::MapReturn {
            mapper,
            mapper_meta,
        });
        self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Eval {
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

        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("FlatMap outside current segment"));
        };
        seg.push_frame(Frame::FlatMapBindSource {
            binder,
            binder_meta,
        });
        self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Eval {
            expr: source,
            handlers,
            metadata: None,
        });
        StepEvent::Continue
    }

    fn handle_get_continuation(&mut self) -> StepEvent {
        let Some(dispatch_id) = self.current_dispatch_id() else {
            return StepEvent::Error(VMError::internal("GetContinuation outside dispatch"));
        };
        let Some(ctx) = self.dispatch_state.find_by_dispatch_id(dispatch_id) else {
            return StepEvent::Error(VMError::internal("GetContinuation: dispatch not found"));
        };
        let k = ctx.k_user.clone();
        self.register_continuation(k.clone());
        self.current_seg_mut().mode = Mode::Deliver(Value::Continuation(k));
        StepEvent::Continue
    }

    fn handle_get_handlers(&mut self) -> StepEvent {
        if self.current_dispatch_id().is_none() {
            return StepEvent::Error(VMError::internal("GetHandlers outside dispatch"));
        }
        let handlers = self
            .current_handler_chain()
            .into_iter()
            .map(|entry| entry.handler)
            .collect::<Vec<_>>();
        self.current_seg_mut().mode = Mode::Deliver(Value::Handlers(handlers));
        StepEvent::Continue
    }

    fn handle_get_traceback(&mut self, continuation: Continuation) -> StepEvent {
        if self.current_dispatch_id().is_none() {
            return StepEvent::Error(VMError::internal(
                "GetTraceback called outside of dispatch context",
            ));
        }
        let hops = TraceState::collect_traceback(&continuation);
        self.current_seg_mut().mode = Mode::Deliver(Value::Traceback(hops));
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
        self.current_seg_mut().mode = Mode::Deliver(Value::Continuation(k));
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

        let k_handler_count = k.handlers.len();
        for idx in (0..k_handler_count).rev() {
            let handler = &k.handlers[idx];
            let py_identity = k.handler_identities.get(idx).cloned().unwrap_or(None);
            let handler_marker = Marker::fresh();
            let mut prompt_seg = Segment::new_prompt(
                handler_marker,
                outside_seg_id,
                handler_marker,
                handler.clone(),
                None,
                py_identity.clone(),
            );
            self.copy_interceptor_guard_state(outside_seg_id, &mut prompt_seg);
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            self.track_run_handler(handler);
            let mut body_seg = Segment::new(handler_marker, Some(prompt_seg_id));
            self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
            let body_seg_id = self.alloc_segment(body_seg);

            outside_seg_id = Some(body_seg_id);
        }

        self.current_segment = outside_seg_id;
        self.current_seg_mut().pending_python = Some(PendingPython::EvalExpr {
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
