//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use pyo3::exceptions::{PyBaseException, PyException as PyStdException};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyModule, PyTuple};

use crate::arena::SegmentArena;
use crate::capture::{
    ActiveChainEntry, EffectCreationSite, HandlerAction, HandlerKind, HandlerSnapshotEntry,
    TraceEntry,
};
use crate::continuation::Continuation;
use crate::debug_state::DebugState;
use crate::dispatch_state::DispatchState;
use crate::do_ctrl::{DoCtrl, InterceptMode};
use crate::doeff_generator::DoeffGenerator;
use crate::driver::{Mode, PyException, StepEvent};
use crate::effect::{
    dispatch_ref_as_python, dispatch_to_pyobject, make_get_execution_context_effect,
    DispatchEffect, PyExecutionContext, PyGetExecutionContext,
};
#[cfg(test)]
use crate::effect::{Effect, PySpawn};
use crate::error::VMError;
use crate::frame::{CallMetadata, EvalReturnContinuation, Frame, InterceptorContinuation};
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::interceptor_state::InterceptorState;
use crate::ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream};
use crate::kleisli::{IdentityKleisli, KleisliRef};
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::pyvm::{
    classify_yielded_for_vm, doctrl_tag, doctrl_to_pyexpr_for_vm, DoExprTag, PyDoExprBase,
    PyEffectBase,
};
use crate::segment::{Segment, SegmentKind};
use crate::trace_state::TraceState;
use crate::value::Value;

pub use crate::dispatch::DispatchContext;
pub use crate::rust_store::RustStore;

static NEXT_RUN_TOKEN: AtomicU64 = AtomicU64::new(1);

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
    pub(crate) interceptor: KleisliRef,
    pub(crate) types: Option<Vec<PyShared>>,
    pub(crate) mode: InterceptMode,
    pub(crate) metadata: Option<CallMetadata>,
}

#[derive(Clone)]
struct InstalledHandler {
    marker: Marker,
    handler: KleisliRef,
}

#[derive(Clone)]
struct HandlerChainEntry {
    marker: Marker,
    prompt_seg_id: SegmentId,
    handler: KleisliRef,
    types: Option<Vec<PyShared>>,
}

#[derive(Clone)]
struct InterceptorChainEntry {
    marker: Marker,
    interceptor: KleisliRef,
    types: Option<Vec<PyShared>>,
    mode: InterceptMode,
    metadata: Option<CallMetadata>,
}

#[derive(Clone)]
enum CallerChainEntry {
    Handler(HandlerChainEntry),
    Interceptor(InterceptorChainEntry),
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
    run_handlers: Vec<KleisliRef>,
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
        self.continuation_registry.clear();
        self.consumed_cont_ids.clear();
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

    fn track_run_handler(&mut self, handler: &KleisliRef) {
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
    ) -> Option<(SegmentId, KleisliRef, Option<Vec<PyShared>>)> {
        self.segments
            .iter()
            .find_map(|(seg_id, seg)| match &seg.kind {
                SegmentKind::PromptBoundary {
                    handled_marker,
                    handler,
                    types,
                    ..
                } if *handled_marker == marker => Some((seg_id, handler.clone(), types.clone())),
                SegmentKind::PromptBoundary { .. }
                | SegmentKind::Normal
                | SegmentKind::InterceptorBoundary { .. }
                | SegmentKind::MaskBoundary { .. } => None,
            })
    }

    fn handlers_in_caller_chain(&self, start_seg_id: SegmentId) -> Vec<HandlerChainEntry> {
        let mut chain = Vec::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                types,
                ..
            } = &seg.kind
            {
                chain.push(HandlerChainEntry {
                    marker: *handled_marker,
                    prompt_seg_id: seg_id,
                    handler: handler.clone(),
                    types: types.clone(),
                });
            }
            cursor = seg.caller;
        }
        chain
    }

    fn chain_entries_in_caller_chain(&self, start_seg_id: SegmentId) -> Vec<CallerChainEntry> {
        let mut chain = Vec::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            match &seg.kind {
                SegmentKind::PromptBoundary {
                    handled_marker,
                    handler,
                    types,
                    ..
                } => chain.push(CallerChainEntry::Handler(HandlerChainEntry {
                    marker: *handled_marker,
                    prompt_seg_id: seg_id,
                    handler: handler.clone(),
                    types: types.clone(),
                })),
                SegmentKind::InterceptorBoundary {
                    interceptor,
                    types,
                    mode,
                    metadata,
                } => chain.push(CallerChainEntry::Interceptor(InterceptorChainEntry {
                    marker: seg.marker,
                    interceptor: interceptor.clone(),
                    types: types.clone(),
                    mode: *mode,
                    metadata: metadata.clone(),
                })),
                SegmentKind::Normal | SegmentKind::MaskBoundary { .. } => {
                    assert!(
                        self.interceptor_state.get_entry(seg.marker).is_none(),
                        "normal segment marker {} unexpectedly has interceptor state entry",
                        seg.marker.raw()
                    );
                }
            }
            cursor = seg.caller;
        }
        chain
    }

    fn find_prompt_boundary_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
        marker: Marker,
    ) -> Option<SegmentId> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let SegmentKind::PromptBoundary { handled_marker, .. } = &seg.kind {
                if *handled_marker == marker {
                    return Some(seg_id);
                }
            }
            cursor = seg.caller;
        }
        None
    }

    fn same_effect_python_type(a: &DispatchEffect, b: &DispatchEffect) -> bool {
        let Some(a_obj) = dispatch_ref_as_python(a) else {
            return false;
        };
        let Some(b_obj) = dispatch_ref_as_python(b) else {
            return false;
        };
        Python::attach(|py| {
            let a_ty = a_obj.bind(py).get_type();
            let b_ty = b_obj.bind(py).get_type();
            a_ty.as_ptr() == b_ty.as_ptr()
        })
    }

    fn current_handler_chain(&self) -> Vec<HandlerChainEntry> {
        let Some(seg_id) = self.current_segment else {
            return Vec::new();
        };
        self.handlers_in_caller_chain(seg_id)
    }

    fn continuation_chain_contains_eval_in_scope_return(continuation: &Continuation) -> bool {
        let mut cursor = Some(continuation);
        while let Some(current) = cursor {
            if current.frames_snapshot.iter().any(|frame| {
                matches!(
                    frame,
                    Frame::EvalReturn(eval_return)
                        if matches!(
                            eval_return.as_ref(),
                            EvalReturnContinuation::EvalInScopeReturn { .. }
                        )
                )
            }) {
                return true;
            }
            cursor = current.parent.as_deref();
        }
        false
    }

    fn is_inside_eval_in_scope_subtopology(&self) -> bool {
        let mut seg_id = self.current_segment;
        while let Some(id) = seg_id {
            let Some(seg) = self.segments.get(id) else {
                break;
            };
            if seg.frames.iter().any(|frame| {
                matches!(
                    frame,
                    Frame::EvalReturn(continuation)
                        if matches!(
                            continuation.as_ref(),
                            EvalReturnContinuation::EvalInScopeReturn { .. }
                        )
                )
            }) {
                return true;
            }
            seg_id = seg.caller;
        }
        let Some(dispatch_id) = self.current_segment_dispatch_id_any() else {
            return false;
        };
        let Some(ctx) = self.dispatch_state.find_by_dispatch_id(dispatch_id) else {
            return false;
        };
        Self::continuation_chain_contains_eval_in_scope_return(&ctx.k_current)
            || Self::continuation_chain_contains_eval_in_scope_return(&ctx.k_origin)
    }

    fn materialize_vm_error_exception(module_attr: &str, message: &str) -> Option<PyException> {
        Python::attach(|py| {
            for module_name in ["doeff_vm", "doeff_vm.doeff_vm"] {
                let Ok(module) = PyModule::import(py, module_name) else {
                    continue;
                };
                let Ok(exc_type) = module.getattr(module_attr) else {
                    continue;
                };
                let Ok(exc_value) = exc_type.call1((message,)) else {
                    continue;
                };
                return Some(PyException::new(
                    exc_type.clone().unbind(),
                    exc_value.unbind(),
                    None,
                ));
            }
            None
        })
    }

    fn recoverable_eval_in_scope_dispatch_exception(&self, error: &VMError) -> Option<PyException> {
        if !self.is_inside_eval_in_scope_subtopology() {
            return None;
        }

        let message = error.to_string();
        match error {
            VMError::UnhandledEffect { .. } => {
                Self::materialize_vm_error_exception("UnhandledEffectError", &message)
                    .or_else(|| Some(PyException::type_error(message)))
            }
            VMError::NoMatchingHandler { .. }
            | VMError::DelegateNoOuterHandler { .. }
            | VMError::HandlerNotFound { .. } => {
                Self::materialize_vm_error_exception("NoMatchingHandlerError", &message)
                    .or_else(|| Some(PyException::type_error(message)))
            }
            VMError::OneShotViolation { .. }
            | VMError::InvalidSegment { .. }
            | VMError::PythonError { .. }
            | VMError::InternalError { .. }
            | VMError::TypeError { .. }
            | VMError::UncaughtException { .. } => None,
        }
    }

    fn dispatch_fatal_error_event(&mut self, error: VMError) -> StepEvent {
        if let Some(exception) = self.recoverable_eval_in_scope_dispatch_exception(&error) {
            self.current_seg_mut().mode = Mode::Throw(exception);
            return StepEvent::Continue;
        }
        StepEvent::Error(error)
    }

    fn eval_in_scope_chain_start_segment(&self, scope: &Continuation) -> Option<SegmentId> {
        let mut start_seg_id = scope.segment_id;
        if self.segments.get(start_seg_id).is_none() {
            return None;
        }

        // When EvalInScope is reached through Delegate chains, the continuation
        // passed to handlers may wrap the original effect-site continuation in
        // `parent`. Replay should use the origin scope so wrapper interceptors
        // around the effect site remain visible.
        let mut cursor = scope.parent.as_deref();
        while let Some(parent) = cursor {
            assert!(
                parent.dispatch_id.is_some(),
                "EvalInScope parent chain must be Delegate-created dispatch continuations"
            );
            if self.segments.get(parent.segment_id).is_none() {
                break;
            }
            start_seg_id = parent.segment_id;
            cursor = parent.parent.as_deref();
        }
        Some(start_seg_id)
    }

    fn structural_kind_for_marker(&self, marker: Marker) -> SegmentKind {
        let Some(entry) = self.interceptor_state.get_entry(marker) else {
            return SegmentKind::Normal;
        };
        SegmentKind::InterceptorBoundary {
            interceptor: entry.interceptor,
            types: entry.types,
            mode: entry.mode,
            metadata: entry.metadata,
        }
    }

    pub fn instantiate_installed_handlers(&mut self) -> Option<SegmentId> {
        let installed = self.installed_handlers.clone();
        let mut outside_seg_id: Option<SegmentId> = None;
        for entry in installed.into_iter().rev() {
            let mut prompt_seg = Segment::new_prompt(
                entry.marker,
                outside_seg_id,
                entry.marker,
                entry.handler.clone(),
            );
            self.copy_interceptor_guard_state(outside_seg_id, &mut prompt_seg);
            self.copy_scope_store_from(outside_seg_id, &mut prompt_seg);
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            self.track_run_handler(&entry.handler);

            let mut body_seg = Segment::new(entry.marker, Some(prompt_seg_id));
            self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
            self.copy_scope_store_from(outside_seg_id, &mut body_seg);
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

    #[inline]
    fn copy_scope_store_from(&self, source_seg_id: Option<SegmentId>, child_seg: &mut Segment) {
        let Some(source_seg_id) = source_seg_id else {
            return;
        };
        let Some(source_seg) = self.segments.get(source_seg_id) else {
            return;
        };
        child_seg.scope_store = source_seg.scope_store.clone();
    }

    fn remap_interceptor_skip_markers(seg: &mut Segment, marker_remap: &HashMap<Marker, Marker>) {
        if marker_remap.is_empty() {
            return;
        }
        for marker in &mut seg.interceptor_skip_stack {
            if let Some(remapped) = marker_remap.get(marker) {
                *marker = *remapped;
            }
        }
    }

    fn remap_marker(marker: &mut Marker, marker_remap: &HashMap<Marker, Marker>) {
        if let Some(remapped) = marker_remap.get(marker) {
            *marker = *remapped;
        }
    }

    fn remap_interceptor_markers_in_doctrl(
        ctrl: &mut DoCtrl,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        match ctrl {
            DoCtrl::Pure { .. } => {}
            DoCtrl::Map { .. } => {}
            DoCtrl::FlatMap { .. } => {}
            DoCtrl::Perform { .. } => {}
            DoCtrl::Resume { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::Transfer { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::TransferThrow { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::ResumeThrow { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::WithHandler { body, .. } => {
                Self::remap_interceptor_markers_in_doctrl(body, marker_remap);
            }
            DoCtrl::WithIntercept { body, .. } => {
                Self::remap_interceptor_markers_in_doctrl(body, marker_remap);
            }
            DoCtrl::Discontinue { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::Delegate { .. } => {}
            DoCtrl::Pass { .. } => {}
            DoCtrl::GetContinuation => {}
            DoCtrl::GetHandlers => {}
            DoCtrl::GetTraceback { continuation } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::CreateContinuation { .. } => {}
            DoCtrl::ResumeContinuation { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::PythonAsyncSyntaxEscape { .. } => {}
            DoCtrl::EvalInScope { scope, .. } => {
                Self::remap_interceptor_markers_in_continuation(scope, marker_remap);
            }
            DoCtrl::Apply {
                f, args, kwargs, ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(f, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(factory, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            DoCtrl::IRStream { .. } => {}
            DoCtrl::Eval { .. } => {}
            DoCtrl::GetCallStack => {}
        }
    }

    fn remap_interceptor_markers_in_interceptor_continuation(
        continuation: &mut InterceptorContinuation,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        Self::remap_marker(&mut continuation.marker, marker_remap);
        let remapped_chain: Vec<Marker> = continuation
            .chain
            .iter()
            .map(|marker| marker_remap.get(marker).copied().unwrap_or(*marker))
            .collect();
        continuation.chain = Arc::new(remapped_chain);
        Self::remap_interceptor_markers_in_doctrl(&mut continuation.original_yielded, marker_remap);
    }

    fn remap_interceptor_markers_in_eval_return_continuation(
        continuation: &mut EvalReturnContinuation,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        match continuation {
            EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            EvalReturnContinuation::ApplyResolveFunction { args, kwargs, .. }
            | EvalReturnContinuation::ExpandResolveFactory { args, kwargs, .. } => {
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            EvalReturnContinuation::ApplyResolveArg {
                f, args, kwargs, ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(f, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            EvalReturnContinuation::ApplyResolveKwarg {
                f, args, kwargs, ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(f, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            EvalReturnContinuation::ExpandResolveArg {
                factory,
                args,
                kwargs,
                ..
            }
            | EvalReturnContinuation::ExpandResolveKwarg {
                factory,
                args,
                kwargs,
                ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(factory, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
        }
    }

    fn remap_interceptor_markers_in_frame(
        frame: &mut Frame,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        match frame {
            Frame::Program { .. } => {}
            Frame::InterceptorApply(interceptor_continuation) => {
                Self::remap_interceptor_markers_in_interceptor_continuation(
                    interceptor_continuation.as_mut(),
                    marker_remap,
                );
            }
            Frame::InterceptorEval(interceptor_continuation) => {
                Self::remap_interceptor_markers_in_interceptor_continuation(
                    interceptor_continuation.as_mut(),
                    marker_remap,
                );
            }
            Frame::HandlerDispatch { .. } => {}
            Frame::EvalReturn(eval_continuation) => {
                Self::remap_interceptor_markers_in_eval_return_continuation(
                    eval_continuation.as_mut(),
                    marker_remap,
                );
            }
            Frame::MapReturn { .. } => {}
            Frame::FlatMapBindResult => {}
            Frame::FlatMapBindSource { .. } => {}
            Frame::InterceptBodyReturn { marker } => {
                Self::remap_marker(marker, marker_remap);
            }
        }
    }

    fn remap_interceptor_markers_in_continuation(
        continuation: &mut Continuation,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        if marker_remap.is_empty() {
            return;
        }

        Self::remap_marker(&mut continuation.marker, marker_remap);
        for marker in &mut continuation.interceptor_skip_stack {
            Self::remap_marker(marker, marker_remap);
        }

        match continuation.mode.as_mut() {
            Mode::HandleYield(yielded) => {
                Self::remap_interceptor_markers_in_doctrl(yielded, marker_remap);
            }
            Mode::Deliver(_) => {}
            Mode::Throw(_) => {}
            Mode::Return(_) => {}
        }

        if let Some(pending) = continuation.pending_python.as_mut() {
            match pending.as_mut() {
                PendingPython::RustProgramContinuation { marker, k } => {
                    Self::remap_marker(marker, marker_remap);
                    Self::remap_interceptor_markers_in_continuation(k, marker_remap);
                }
                PendingPython::EvalExpr { .. } => {}
                PendingPython::CallFuncReturn => {}
                PendingPython::StepUserGenerator { .. } => {}
                PendingPython::ExpandReturn { .. } => {}
                PendingPython::AsyncEscape => {}
            }
        }

        let mut frames = (*continuation.frames_snapshot).clone();
        for frame in &mut frames {
            Self::remap_interceptor_markers_in_frame(frame, marker_remap);
        }
        continuation.frames_snapshot = Arc::new(frames);

        if let Some(parent) = continuation.parent.as_ref() {
            let mut parent_remapped = (**parent).clone();
            Self::remap_interceptor_markers_in_continuation(&mut parent_remapped, marker_remap);
            continuation.parent = Some(Arc::new(parent_remapped));
        }
    }

    fn remap_interceptor_markers_in_segment(
        seg: &mut Segment,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        if marker_remap.is_empty() {
            return;
        }
        Self::remap_marker(&mut seg.marker, marker_remap);
        Self::remap_interceptor_skip_markers(seg, marker_remap);

        match &mut seg.mode {
            Mode::HandleYield(yielded) => {
                Self::remap_interceptor_markers_in_doctrl(yielded, marker_remap);
            }
            Mode::Deliver(_) => {}
            Mode::Throw(_) => {}
            Mode::Return(_) => {}
        }

        if let Some(pending) = &mut seg.pending_python {
            match pending {
                PendingPython::RustProgramContinuation { marker, k } => {
                    Self::remap_marker(marker, marker_remap);
                    Self::remap_interceptor_markers_in_continuation(k, marker_remap);
                }
                PendingPython::EvalExpr { .. } => {}
                PendingPython::CallFuncReturn => {}
                PendingPython::StepUserGenerator { .. } => {}
                PendingPython::ExpandReturn { .. } => {}
                PendingPython::AsyncEscape => {}
            }
        }

        for frame in &mut seg.frames {
            Self::remap_interceptor_markers_in_frame(frame, marker_remap);
        }

        if let SegmentKind::PromptBoundary { handled_marker, .. } = &mut seg.kind {
            Self::remap_marker(handled_marker, marker_remap);
        }
    }

    fn remap_interceptor_markers_in_runtime_state(
        &mut self,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        if marker_remap.is_empty() {
            return;
        }

        let seg_ids: Vec<SegmentId> = self.segments.iter().map(|(seg_id, _)| seg_id).collect();
        for seg_id in seg_ids {
            let Some(seg) = self.segments.get_mut(seg_id) else {
                continue;
            };
            Self::remap_interceptor_markers_in_segment(seg, marker_remap);
        }

        let dispatch_depth = self.dispatch_state.depth();
        for idx in 0..dispatch_depth {
            let Some(ctx) = self.dispatch_state.get_mut(idx) else {
                continue;
            };
            Self::remap_interceptor_markers_in_continuation(&mut ctx.k_origin, marker_remap);
            Self::remap_interceptor_markers_in_continuation(&mut ctx.k_current, marker_remap);
        }

        for continuation in self.continuation_registry.values_mut() {
            Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
        }
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
        expr: DoCtrl,
        continuation: EvalReturnContinuation,
    ) -> StepEvent {
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("Call evaluation outside current segment"));
        };
        seg.push_frame(Frame::EvalReturn(Box::new(continuation)));
        seg.mode = Mode::HandleYield(expr);
        StepEvent::Continue
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
    ) -> Result<(IRStreamRef, Option<CallMetadata>), PyException> {
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
            )) as Box<dyn IRStream>));
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

    fn value_variant_name(value: &Value) -> &'static str {
        match value {
            Value::Python(_) => "Python",
            Value::Unit => "Unit",
            Value::Int(_) => "Int",
            Value::String(_) => "String",
            Value::Bool(_) => "Bool",
            Value::None => "None",
            Value::Continuation(_) => "Continuation",
            Value::Handlers(_) => "Handlers",
            Value::Kleisli(_) => "Kleisli",
            Value::Task(_) => "Task",
            Value::Promise(_) => "Promise",
            Value::ExternalPromise(_) => "ExternalPromise",
            Value::CallStack(_) => "CallStack",
            Value::Trace(_) => "Trace",
            Value::Traceback(_) => "Traceback",
            Value::ActiveChain(_) => "ActiveChain",
            Value::List(_) => "List",
        }
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

    fn handler_trace_info(
        handler: &KleisliRef,
    ) -> (String, HandlerKind, Option<String>, Option<u32>) {
        let info = handler.debug_info();
        let kind = if handler.is_rust_builtin() {
            HandlerKind::RustBuiltin
        } else {
            HandlerKind::Python
        };
        (info.name, kind, info.file, info.line)
    }

    fn invoke_kleisli_handler_expr(
        kleisli: KleisliRef,
        effect: DispatchEffect,
        continuation: Continuation,
    ) -> Result<DoCtrl, VMError> {
        let effect_obj = Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|v| v.unbind()))
            .map_err(|err| {
                VMError::python_error(format!(
                    "failed to convert dispatch effect to Python object: {err}"
                ))
            })?;
        let debug = kleisli.debug_info();
        let metadata = CallMetadata::new(
            debug.name,
            debug.file.unwrap_or_else(|| "<unknown>".to_string()),
            debug.line.unwrap_or(0),
            None,
            None,
        );

        Ok(DoCtrl::Expand {
            factory: Box::new(DoCtrl::Pure {
                value: Value::Kleisli(kleisli),
            }),
            args: vec![
                DoCtrl::Pure {
                    value: Value::Python(effect_obj),
                },
                DoCtrl::Pure {
                    value: Value::Continuation(continuation),
                },
            ],
            kwargs: vec![],
            metadata,
        })
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
            .map(|(_seg_id, handler, _types)| Self::handler_trace_info(&handler))
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
        let Some(ctx) = self.dispatch_state.find_by_dispatch_id(dispatch_id) else {
            return false;
        };
        seg_id == ctx.active_handler_seg_id
    }

    fn current_active_handler_dispatch_id(&self) -> Option<DispatchId> {
        self.interceptor_state.current_active_handler_dispatch_id(
            self.dispatch_state.contexts(),
            self.current_segment,
            &self.segments,
        )
    }

    fn current_program_frame_handler_kind(&self) -> Option<HandlerKind> {
        // Invariant: when a handler Program frame yields DoCtrl::IRStream via `yield helper()`,
        // that handler Program frame is on top of the stack. Reading the top Program frame's
        // provenance correctly propagates handler context to nested sub-program frames.
        self.current_seg()
            .frames
            .last()
            .and_then(|frame| match frame {
                Frame::Program { handler_kind, .. } => *handler_kind,
                Frame::InterceptorApply(_)
                | Frame::InterceptorEval(_)
                | Frame::HandlerDispatch { .. }
                | Frame::EvalReturn(_)
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. }
                | Frame::InterceptBodyReturn { .. } => None,
            })
    }

    fn dispatch_uses_user_continuation_stream(
        &self,
        dispatch_id: DispatchId,
        stream: &IRStreamRef,
    ) -> bool {
        self.dispatch_state
            .find_by_dispatch_id(dispatch_id)
            .is_some_and(|ctx| {
                ctx.k_current
                    .frames_snapshot
                    .iter()
                    .any(|frame| match frame {
                        Frame::Program {
                            stream: snapshot_stream,
                            ..
                        } => Arc::ptr_eq(snapshot_stream, stream),
                        Frame::InterceptorApply(_)
                        | Frame::InterceptorEval(_)
                        | Frame::HandlerDispatch { .. }
                        | Frame::EvalReturn(_)
                        | Frame::MapReturn { .. }
                        | Frame::FlatMapBindResult
                        | Frame::FlatMapBindSource { .. }
                        | Frame::InterceptBodyReturn { .. } => false,
                    })
            })
    }

    fn handler_stream_throw_continuation(
        &self,
        _stream: &IRStreamRef,
        handler_kind: Option<HandlerKind>,
    ) -> Option<Continuation> {
        if handler_kind != Some(HandlerKind::Python) {
            return None;
        }

        let dispatch_id = self
            .current_segment_dispatch_id_any()
            .or_else(|| self.current_active_handler_dispatch_id())?;
        if self.dispatch_state.dispatch_is_execution_context_effect(dispatch_id) {
            return None;
        }
        let ctx = self.dispatch_state.find_by_dispatch_id(dispatch_id)?;
        // If the dispatch is already completed (handler already resumed the
        // continuation), don't return it — the exception should propagate
        // normally through the segment stack instead of attempting a
        // TransferThrow into an already-consumed continuation.
        if ctx.completed {
            return None;
        }
        if self.is_one_shot_consumed(ctx.k_current.cont_id) {
            return None;
        }
        Some(ctx.k_current.clone())
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

    fn emit_frame_entered(&mut self, metadata: &CallMetadata, handler_kind: Option<HandlerKind>) {
        self.trace_state.emit_frame_entered(
            metadata,
            Self::program_call_repr(metadata),
            handler_kind,
        );
    }

    fn emit_frame_exited(&mut self, metadata: &CallMetadata) {
        self.trace_state.emit_frame_exited(metadata);
    }

    fn emit_handler_threw_for_dispatch(&mut self, dispatch_id: DispatchId, exc: &PyException) {
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
        self.trace_state.emit_handler_threw_for_dispatch(
            dispatch_id,
            handler_name,
            handler_index,
            Self::exception_repr(exc),
        );
    }

    fn emit_resume_event(
        &mut self,
        dispatch_id: DispatchId,
        handler_name: String,
        value_repr: Option<String>,
        continuation: &Continuation,
        transferred: bool,
    ) {
        self.trace_state.emit_resume_event(
            dispatch_id,
            handler_name,
            value_repr,
            continuation,
            transferred,
            TraceState::continuation_resume_location,
        );
    }

    pub fn assemble_traceback_entries(&self, exception: &PyException) -> Vec<TraceEntry> {
        self.trace_state.assemble_traceback_entries(
            exception,
            &self.segments,
            self.current_segment,
            self.dispatch_state.contexts(),
        )
    }

    fn enrich_original_exception_with_context(
        original: PyException,
        context_value: Value,
    ) -> Result<PyException, PyException> {
        TraceState::enrich_original_exception_with_context(original, context_value)
    }

    pub fn assemble_active_chain(&self, exception: Option<&PyException>) -> Vec<ActiveChainEntry> {
        self.trace_state.assemble_active_chain(
            exception,
            &self.segments,
            self.current_segment,
            self.dispatch_state.contexts(),
        )
    }

    fn should_attach_active_chain_for_dispatch(&self, dispatch_id: DispatchId) -> bool {
        self.dispatch_state
            .find_by_dispatch_id(dispatch_id)
            .is_some_and(|ctx| ctx.is_execution_context_effect && ctx.original_exception.is_none())
    }

    fn maybe_attach_active_chain_to_execution_context(
        &mut self,
        dispatch_id: Option<DispatchId>,
        value: &mut Value,
    ) -> Result<(), VMError> {
        let Some(dispatch_id) = dispatch_id else {
            return Ok(());
        };
        if !self.should_attach_active_chain_for_dispatch(dispatch_id) {
            return Ok(());
        }
        let context_obj = match value {
            Value::Python(obj) => obj,
            other => {
                return Err(VMError::python_error(format!(
                    "GetExecutionContext handler must return ExecutionContext, got {}",
                    Self::value_variant_name(other)
                )))
            }
        };

        let mut active_chain = self.assemble_active_chain(None);
        Python::attach(|py| {
            let context_bound = context_obj.bind(py);
            if !context_bound.is_instance_of::<PyExecutionContext>() {
                let got_type = context_bound
                    .get_type()
                    .name()
                    .map(|name| name.to_string())
                    .unwrap_or_else(|_| MISSING_UNKNOWN.to_string());
                return Err(VMError::python_error(format!(
                    "GetExecutionContext handler must return ExecutionContext, got {got_type}"
                )));
            }

            let entries_obj = context_bound.getattr("entries").map_err(|err| {
                VMError::python_error(format!(
                    "GetExecutionContext handler returned invalid ExecutionContext.entries: {err}"
                ))
            })?;
            let iter = entries_obj.try_iter().map_err(|err| {
                VMError::python_error(format!(
                    "GetExecutionContext handler returned non-iterable ExecutionContext.entries: {err}"
                ))
            })?;
            for entry_result in iter {
                let entry = entry_result.map_err(|err| {
                    VMError::python_error(format!(
                        "failed to iterate ExecutionContext.entries while attaching active_chain: {err}"
                    ))
                })?;
                active_chain.push(ActiveChainEntry::ContextEntry {
                    data: entry.unbind(),
                });
            }

            let active_chain_obj =
                Value::ActiveChain(active_chain)
                    .to_pyobject(py)
                    .map_err(|err| {
                        VMError::python_error(format!(
                            "failed to convert active_chain snapshot to Python object: {err}"
                        ))
                    })?;
            let active_chain_list = active_chain_obj.cast::<PyList>().map_err(|err| {
                VMError::python_error(format!(
                    "active_chain snapshot serialization did not produce list: {err}"
                ))
            })?;
            let active_chain_tuple = PyTuple::new(py, active_chain_list.iter()).map_err(|err| {
                VMError::python_error(format!(
                    "failed to convert active_chain snapshot to tuple: {err}"
                ))
            })?;

            let mut context_ref = context_bound
                .extract::<PyRefMut<'_, PyExecutionContext>>()
                .map_err(|err| {
                    VMError::python_error(format!(
                        "failed to access ExecutionContext for active_chain assignment: {err}"
                    ))
                })?;
            context_ref.set_active_chain(Some(active_chain_tuple.into_any().unbind()));
            Ok(())
        })
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
            other => unreachable!("invalid mode discriminator in VM::step: {other}"),
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
                            let trace = self.assemble_traceback_entries(&exc);
                            let active_chain = self.assemble_active_chain(Some(&exc));
                            self.segments.reparent_children(seg_id, None);
                            self.segments.free(seg_id);
                            return StepEvent::Error(VMError::uncaught_exception(
                                exc,
                                trace,
                                active_chain,
                            ));
                        }
                    }
                    Mode::HandleYield(yielded) => {
                        unreachable!(
                            "segment without frames cannot be in HandleYield mode: {yielded:?}"
                        )
                    }
                    Mode::Return(value) => {
                        unreachable!("segment without frames cannot be in Return mode: {value:?}")
                    }
                }
            }
        }

        let (frame, mode) = {
            let segment = match self.segments.get_mut(seg_id) {
                Some(s) => s,
                None => return StepEvent::Error(VMError::invalid_segment("segment not found")),
            };
            let Some(frame) = segment.pop_frame() else {
                return StepEvent::Error(VMError::internal(
                    "segment frame stack unexpectedly empty in step_deliver_or_throw",
                ));
            };

            // Take mode by move — each branch sets segment.mode before returning.
            let mode = std::mem::replace(&mut segment.mode, Mode::Deliver(Value::Unit));
            (frame, mode)
        };

        match frame {
            Frame::Program {
                stream,
                metadata,
                handler_kind,
            } => {
                let step = {
                    let Some(seg) = self.segments.get_mut(seg_id) else {
                        return StepEvent::Error(VMError::invalid_segment("segment not found"));
                    };
                    let scope = &mut seg.scope_store;
                    let mut guard = stream.lock().expect("IRStream lock poisoned");
                    match mode {
                        Mode::Deliver(value) => guard.resume(value, &mut self.rust_store, scope),
                        Mode::Throw(exc) => guard.throw(exc, &mut self.rust_store, scope),
                        Mode::HandleYield(yielded) => {
                            unreachable!("Program frame resumed with HandleYield mode: {yielded:?}")
                        }
                        Mode::Return(value) => {
                            unreachable!("Program frame resumed with Return mode: {value:?}")
                        }
                    }
                };
                self.apply_stream_step(step, stream, metadata, handler_kind)
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
            (
                PyException::Materialized { .. },
                PyException::RuntimeError { .. } | PyException::TypeError { .. },
            )
            | (
                PyException::RuntimeError { .. } | PyException::TypeError { .. },
                PyException::Materialized { .. },
            )
            | (PyException::RuntimeError { .. }, PyException::RuntimeError { .. })
            | (PyException::RuntimeError { .. }, PyException::TypeError { .. })
            | (PyException::TypeError { .. }, PyException::RuntimeError { .. })
            | (PyException::TypeError { .. }, PyException::TypeError { .. }) => false,
        }
    }

    fn chain_exception_context(original_exception: &PyException, cleanup_exception: &PyException) {
        if Self::same_materialized_exception(original_exception, cleanup_exception) {
            return;
        }
        let PyException::Materialized { exc_value, .. } = original_exception else {
            return;
        };
        Python::attach(|py| {
            let _ = exc_value
                .bind(py)
                .setattr("__context__", cleanup_exception.value_clone_ref(py));
        });
    }

    fn step_interceptor_apply_frame(
        &mut self,
        continuation: InterceptorContinuation,
        mode: Mode,
    ) -> StepEvent {
        if continuation.guard_eval_depth {
            let seg = self.current_seg_mut();
            seg.interceptor_eval_depth = seg.interceptor_eval_depth.saturating_sub(1);
        }
        if let Some(metadata) = continuation.interceptor_metadata.as_ref() {
            self.emit_frame_exited(metadata);
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
            Mode::HandleYield(yielded) => {
                unreachable!("interceptor apply frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("interceptor apply frame received Return mode: {value:?}")
            }
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
            Mode::HandleYield(yielded) => {
                unreachable!("interceptor eval frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("interceptor eval frame received Return mode: {value:?}")
            }
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
            Mode::HandleYield(yielded) => {
                unreachable!("handler dispatch frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("handler dispatch frame received Return mode: {value:?}")
            }
        }
    }

    fn step_eval_return_frame(
        &mut self,
        continuation: EvalReturnContinuation,
        mode: Mode,
    ) -> StepEvent {
        if let EvalReturnContinuation::EvalInScopeReturn { continuation } = continuation {
            self.current_seg_mut().mode = match mode {
                Mode::Deliver(value) => Mode::HandleYield(DoCtrl::Transfer {
                    continuation,
                    value,
                }),
                Mode::Throw(exception) => Mode::HandleYield(DoCtrl::ResumeThrow {
                    continuation,
                    exception,
                }),
                Mode::HandleYield(yielded) => {
                    unreachable!(
                        "EvalInScope return continuation received HandleYield mode: {yielded:?}"
                    )
                }
                Mode::Return(value) => {
                    unreachable!("EvalInScope return continuation received Return mode: {value:?}")
                }
            };
            return StepEvent::Continue;
        }
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
            Mode::HandleYield(yielded) => {
                unreachable!("eval return frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("eval return frame received Return mode: {value:?}")
            }
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
            } => Mode::HandleYield(DoCtrl::Apply {
                f: Box::new(DoCtrl::Pure { value }),
                args,
                kwargs,
                metadata,
            }),
            EvalReturnContinuation::ApplyResolveArg {
                f,
                mut args,
                kwargs,
                arg_idx,
                metadata,
            } => {
                let Some(slot) = args.get_mut(arg_idx) else {
                    return Mode::Throw(PyException::runtime_error(
                        "apply continuation arg index out of bounds",
                    ));
                };
                *slot = DoCtrl::Pure { value };
                Mode::HandleYield(DoCtrl::Apply {
                    f: Box::new(f),
                    args,
                    kwargs,
                    metadata,
                })
            }
            EvalReturnContinuation::ApplyResolveKwarg {
                f,
                args,
                mut kwargs,
                kwarg_idx,
                metadata,
            } => {
                let Some((_, slot)) = kwargs.get_mut(kwarg_idx) else {
                    return Mode::Throw(PyException::runtime_error(
                        "apply continuation kwarg index out of bounds",
                    ));
                };
                *slot = DoCtrl::Pure { value };
                Mode::HandleYield(DoCtrl::Apply {
                    f: Box::new(f),
                    args,
                    kwargs,
                    metadata,
                })
            }
            EvalReturnContinuation::ExpandResolveFactory {
                args,
                kwargs,
                metadata,
            } => Mode::HandleYield(DoCtrl::Expand {
                factory: Box::new(DoCtrl::Pure { value }),
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
                *slot = DoCtrl::Pure { value };
                Mode::HandleYield(DoCtrl::Expand {
                    factory: Box::new(factory),
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
                *slot = DoCtrl::Pure { value };
                Mode::HandleYield(DoCtrl::Expand {
                    factory: Box::new(factory),
                    args,
                    kwargs,
                    metadata,
                })
            }
            EvalReturnContinuation::EvalInScopeReturn { .. } => {
                unreachable!("EvalInScopeReturn continuation is handled before value dispatch")
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
                    f: Box::new(DoCtrl::Pure {
                        value: Value::Python(mapper.into_inner()),
                    }),
                    args: vec![DoCtrl::Pure { value }],
                    kwargs: vec![],
                    metadata: mapper_meta,
                });
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            Mode::HandleYield(yielded) => {
                unreachable!("map return frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("map return frame received Return mode: {value:?}")
            }
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
            Mode::HandleYield(yielded) => {
                unreachable!("flat_map bind result frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("flat_map bind result frame received Return mode: {value:?}")
            }
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
                    factory: Box::new(DoCtrl::Pure {
                        value: Value::Python(binder.into_inner()),
                    }),
                    args: vec![DoCtrl::Pure { value }],
                    kwargs: vec![],
                    metadata: binder_meta,
                });
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            Mode::HandleYield(yielded) => {
                unreachable!("flat_map bind source frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("flat_map bind source frame received Return mode: {value:?}")
            }
        }
    }

    fn step_intercept_body_return_frame(&mut self, _marker: Marker, mode: Mode) -> StepEvent {
        match mode {
            Mode::Deliver(value) => self.handle_handler_return(value),
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            Mode::HandleYield(yielded) => {
                unreachable!("intercept body return frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("intercept body return frame received Return mode: {value:?}")
            }
        }
    }

    fn apply_stream_step(
        &mut self,
        step: IRStreamStep,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
    ) -> StepEvent {
        match step {
            IRStreamStep::Yield(yielded) => {
                self.handle_stream_yield(yielded, stream, metadata, handler_kind)
            }
            IRStreamStep::Return(value) => {
                if let Some(ref m) = metadata {
                    self.emit_frame_exited(m);
                }
                self.handle_handler_return(value)
            }
            IRStreamStep::Throw(exc) => {
                if let Some(continuation) =
                    self.handler_stream_throw_continuation(&stream, handler_kind)
                {
                    self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::TransferThrow {
                        continuation,
                        exception: exc,
                    });
                    return StepEvent::Continue;
                }

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
                    self.emit_handler_threw_for_dispatch(dispatch_id, &exc);
                    self.mark_dispatch_threw(dispatch_id);
                }
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            IRStreamStep::NeedsPython(call) => {
                if matches!(
                    &call,
                    PythonCall::GenNext | PythonCall::GenSend { .. } | PythonCall::GenThrow { .. }
                ) {
                    self.current_seg_mut().pending_python =
                        Some(PendingPython::StepUserGenerator {
                            stream,
                            metadata,
                            handler_kind,
                        });
                    return StepEvent::NeedsPython(call);
                }

                let Some(seg) = self.current_segment_mut() else {
                    return StepEvent::Error(VMError::internal(
                        "current_segment_mut() returned None in apply_stream_step \
                         (NeedsPython rust continuation)",
                    ));
                };
                seg.push_frame(Frame::Program {
                    stream,
                    metadata,
                    handler_kind,
                });
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
                let k = ctx.k_current.clone();
                self.current_seg_mut().pending_python =
                    Some(PendingPython::RustProgramContinuation { marker, k });
                StepEvent::NeedsPython(call)
            }
        }
    }

    fn handle_stream_yield(
        &mut self,
        yielded: DoCtrl,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
    ) -> StepEvent {
        let chain = Arc::new(self.current_interceptor_chain());
        self.current_seg_mut().mode =
            self.continue_interceptor_chain_mode(yielded, stream, metadata, handler_kind, chain, 0);
        StepEvent::Continue
    }

    fn finalize_stream_yield_mode(
        &mut self,
        yielded: DoCtrl,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
    ) -> Mode {
        let is_terminal = matches!(
            &yielded,
            DoCtrl::Transfer { .. }
                | DoCtrl::TransferThrow { .. }
                | DoCtrl::Discontinue { .. }
                | DoCtrl::Pass { .. }
        );
        if !is_terminal {
            match self.current_segment_mut() {
                Some(seg) => seg.push_frame(Frame::Program {
                    stream,
                    metadata,
                    handler_kind,
                }),
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
        self.interceptor_state.current_chain(
            self.current_segment,
            &self.segments,
            self.dispatch_state.contexts(),
        )
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

    fn classify_interceptor_eval_result(
        &self,
        value: Value,
        original_obj: &PyShared,
        original_yielded: DoCtrl,
    ) -> Result<DoCtrl, PyException> {
        let Value::Python(result_obj) = value else {
            return Err(PyException::type_error(
                "WithIntercept effectful interceptor must resolve to DoExpr",
            ));
        };
        self.classify_interceptor_result_object(result_obj, original_obj, original_yielded)
    }

    fn should_invoke_interceptor(
        &self,
        entry: &InterceptorEntry,
        yielded_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        let Some(types) = entry.types.as_ref() else {
            return Ok(true);
        };
        if types.is_empty() {
            return Ok(entry.mode.should_invoke(false));
        }

        let matches_filter = Python::attach(|py| -> PyResult<bool> {
            let yielded = yielded_obj.bind(py);
            for ty in types {
                let ty_bound = ty.bind(py);
                if yielded.is_instance(&ty_bound)? {
                    return Ok(true);
                }
            }
            Ok(false)
        })?;
        Ok(entry.mode.should_invoke(matches_filter))
    }

    fn should_invoke_handler(
        &self,
        entry: &HandlerChainEntry,
        effect_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        let Some(types) = entry.types.as_ref() else {
            return Ok(true);
        };
        if types.is_empty() {
            return Ok(false);
        }

        Ok(Python::attach(|py| -> PyResult<bool> {
            let effect = effect_obj.bind(py);
            let type_tuple = PyTuple::new(py, types.iter().map(|ty| ty.clone_ref(py)))?;
            effect.is_instance(&type_tuple)
        })?)
    }

    fn continue_interceptor_chain_mode(
        &mut self,
        yielded: DoCtrl,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
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

            match self.should_invoke_interceptor(&entry, &yielded_obj) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(exc) => return Mode::Throw(exc),
            }

            return self.start_interceptor_invocation_mode(
                marker,
                entry,
                current,
                yielded_obj,
                stream,
                metadata,
                handler_kind,
                chain,
                idx,
            );
        }

        self.finalize_stream_yield_mode(current, stream, metadata, handler_kind)
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
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        chain: Arc<Vec<Marker>>,
        next_idx: usize,
    ) -> Mode {
        let interceptor_kleisli = entry.interceptor.clone();
        let guard_eval_depth = entry.types.is_some();
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
            self.emit_frame_entered(meta, None);
        }
        let continuation = InterceptorContinuation {
            marker,
            original_yielded: yielded,
            original_obj: PyShared::new(yielded_obj_for_continuation),
            emitter_stream: stream,
            emitter_metadata: metadata,
            emitter_handler_kind: handler_kind,
            chain,
            next_idx,
            interceptor_metadata: interceptor_meta,
            guard_eval_depth,
        };
        let Some(seg) = self.current_segment_mut() else {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        };
        if guard_eval_depth {
            seg.interceptor_eval_depth = seg.interceptor_eval_depth.saturating_add(1);
        }
        seg.push_frame(Frame::InterceptorApply(Box::new(continuation)));

        Mode::HandleYield(DoCtrl::Apply {
            f: Box::new(DoCtrl::Pure {
                value: Value::Kleisli(interceptor_kleisli),
            }),
            args: vec![DoCtrl::Pure {
                value: Value::Python(yielded_obj),
            }],
            kwargs: vec![],
            metadata: apply_metadata,
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
            emitter_handler_kind,
            chain,
            next_idx,
            guard_eval_depth,
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
                emitter_handler_kind,
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
                emitter_handler_kind,
                chain,
                next_idx,
                interceptor_metadata: None,
                guard_eval_depth,
            })));

            return Mode::HandleYield(DoCtrl::Eval {
                expr: PyShared::new(result_obj),
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
            emitter_handler_kind,
            chain,
            next_idx,
            ..
        } = continuation;
        let transformed =
            match self.classify_interceptor_eval_result(value, &original_obj, original_yielded) {
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
            emitter_handler_kind,
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
                body,
                types,
            } => self.handle_with_handler(handler, *body, types),
            DoCtrl::WithIntercept {
                interceptor,
                body,
                types,
                mode,
                metadata,
            } => self.handle_yield_with_intercept(interceptor, *body, types, mode, metadata),
            DoCtrl::Discontinue {
                continuation,
                exception,
            } => self.handle_yield_discontinue(continuation, exception),
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
            } => self.handle_yield_apply(*f, args, kwargs, metadata),
            // PendingPython::ExpandReturn is set in handle_yield_expand.
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                metadata,
            } => self.handle_yield_expand(*factory, args, kwargs, metadata),
            DoCtrl::IRStream { stream, metadata } => self.handle_yield_ir_stream(
                stream,
                metadata,
                self.current_program_frame_handler_kind(),
            ),
            DoCtrl::Eval { expr, metadata } => self.handle_yield_eval(expr, metadata),
            DoCtrl::EvalInScope {
                expr,
                scope,
                metadata,
            } => self.handle_yield_eval_in_scope(expr, scope, metadata),
            DoCtrl::GetCallStack => self.handle_yield_get_call_stack(),
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
            Err(error) => self.dispatch_fatal_error_event(error),
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

    fn handle_yield_discontinue(
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

    fn handle_yield_with_intercept(
        &mut self,
        interceptor: KleisliRef,
        body: DoCtrl,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        self.handle_with_intercept(interceptor, body, types, mode, metadata)
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
        handlers: Vec<KleisliRef>,
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
        f: DoCtrl,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        metadata: CallMetadata,
    ) -> StepEvent {
        if !matches!(&f, DoCtrl::Pure { .. }) {
            return self.eval_then_reenter_call(
                f,
                EvalReturnContinuation::ApplyResolveFunction {
                    args,
                    kwargs,
                    metadata,
                },
            );
        }

        if let Some((arg_idx, expr)) = Self::first_non_pure_arg(&args) {
            return self.eval_then_reenter_call(
                expr,
                EvalReturnContinuation::ApplyResolveArg {
                    f,
                    args,
                    kwargs,
                    arg_idx,
                    metadata,
                },
            );
        }

        if let Some((kwargs_idx, expr)) = Self::first_non_pure_kwarg(&kwargs) {
            return self.eval_then_reenter_call(
                expr,
                EvalReturnContinuation::ApplyResolveKwarg {
                    f,
                    args,
                    kwargs,
                    kwarg_idx: kwargs_idx,
                    metadata,
                },
            );
        }

        let f_value = match f {
            DoCtrl::Pure { value } => value,
            other => {
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Apply f must be a pure callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        let func = match f_value {
            Value::Python(func) => PyShared::new(func),
            Value::Kleisli(kleisli) => {
                let args_values = Self::collect_value_args(args);
                let kwargs_values = Self::collect_value_kwargs(kwargs);
                if !kwargs_values.is_empty() {
                    self.current_seg_mut().mode = Mode::Throw(PyException::type_error(
                        "Kleisli apply does not support keyword arguments".to_string(),
                    ));
                    return StepEvent::Continue;
                }

                let run_token = self.current_run_token();
                let result =
                    Python::attach(|py| kleisli.apply_with_run_token(py, args_values, run_token));
                return match result {
                    Ok(doctrl) => self.evaluate(doctrl),
                    Err(vm_err) => StepEvent::Error(vm_err),
                };
            }
            other => {
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Apply f must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        self.current_seg_mut().pending_python = Some(PendingPython::CallFuncReturn);
        StepEvent::NeedsPython(PythonCall::CallFunc {
            func,
            args: Self::collect_value_args(args),
            kwargs: Self::collect_value_kwargs(kwargs),
        })
    }

    fn handle_yield_expand(
        &mut self,
        factory: DoCtrl,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        metadata: CallMetadata,
    ) -> StepEvent {
        if !matches!(&factory, DoCtrl::Pure { .. }) {
            return self.eval_then_reenter_call(
                factory,
                EvalReturnContinuation::ExpandResolveFactory {
                    args,
                    kwargs,
                    metadata,
                },
            );
        }

        if let Some((arg_idx, expr)) = Self::first_non_pure_arg(&args) {
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

        if let Some((kwargs_idx, expr)) = Self::first_non_pure_kwarg(&kwargs) {
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

        let factory_value = match factory {
            DoCtrl::Pure { value } => value,
            other => {
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Expand factory must be a pure callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        let (func, handler_return) = match factory_value {
            Value::Python(factory) => (PyShared::new(factory), false),
            Value::Kleisli(kleisli) => {
                let args_values = Self::collect_value_args(args);
                let kwargs_values = Self::collect_value_kwargs(kwargs);
                if !kwargs_values.is_empty() {
                    self.current_seg_mut().mode = Mode::Throw(PyException::type_error(
                        "Kleisli expand does not support keyword arguments".to_string(),
                    ));
                    return StepEvent::Continue;
                }

                let run_token = self.current_run_token();
                let result =
                    Python::attach(|py| kleisli.apply_with_run_token(py, args_values, run_token));
                return match result {
                    Ok(doctrl) => {
                        if let DoCtrl::IRStream { stream, metadata } = doctrl {
                            let maybe_dispatch_id = self
                                .current_dispatch_id()
                                .or_else(|| self.current_active_handler_dispatch_id());
                            if let Some(dispatch_id) = maybe_dispatch_id {
                                self.current_seg_mut()
                                    .push_frame(Frame::HandlerDispatch { dispatch_id });
                                let handler_kind = if kleisli.is_rust_builtin() {
                                    HandlerKind::RustBuiltin
                                } else {
                                    HandlerKind::Python
                                };
                                return self.handle_yield_ir_stream(
                                    stream,
                                    metadata,
                                    Some(handler_kind),
                                );
                            }
                            return self.handle_yield_ir_stream(stream, metadata, None);
                        }
                        self.evaluate(doctrl)
                    }
                    Err(vm_err) => StepEvent::Error(vm_err),
                };
            }
            other => {
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Expand factory must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
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

    fn handle_yield_ir_stream(
        &mut self,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
    ) -> StepEvent {
        if let Some(ref m) = metadata {
            self.emit_frame_entered(m, handler_kind);
        }
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal(
                "handle_yield_ir_stream called without current segment",
            ));
        };
        seg.push_frame(Frame::Program {
            stream,
            metadata,
            handler_kind,
        });
        seg.mode = Mode::Deliver(Value::Unit);
        StepEvent::Continue
    }

    fn handle_yield_eval(&mut self, expr: PyShared, metadata: Option<CallMetadata>) -> StepEvent {
        let handlers = self.current_visible_handlers();
        let cont = Continuation::create_unstarted_with_metadata(expr, handlers, metadata);
        self.handle_resume_continuation(cont, Value::None)
    }

    fn handle_yield_eval_in_scope(
        &mut self,
        expr: PyShared,
        scope: Continuation,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let Some(current_seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal(
                "EvalInScope called without current segment",
            ));
        };
        let Some(current_seg) = self.segments.get(current_seg_id) else {
            return StepEvent::Error(VMError::internal("EvalInScope current segment not found"));
        };
        let mut return_to =
            Continuation::capture(current_seg, current_seg_id, current_seg.dispatch_id);
        // Correctness constraint: active interceptor markers are still referenced by
        // live frames in the current dispatch. We must preserve them as-is for the
        // replay chain; remapping them would require remapping those live frames,
        // which are outside the EvalInScope replay scope.
        let mut active_interceptor_markers: HashSet<Marker> =
            current_seg.interceptor_skip_stack.iter().copied().collect();
        active_interceptor_markers.insert(current_seg.marker);

        let Some(replay_chain_start_seg_id) = self.eval_in_scope_chain_start_segment(&scope) else {
            return StepEvent::Error(VMError::internal(
                "EvalInScope received scope from unknown segment",
            ));
        };

        let chain_entries = self.chain_entries_in_caller_chain(replay_chain_start_seg_id);

        // Evaluate in an isolated base segment so active handlers are exactly
        // the scope-site handlers (no duplication with current caller chain),
        // while inheriting dynamic scope/interceptor state from the call site.
        let mut base_seg = Segment::new(Marker::fresh(), None);
        self.copy_interceptor_guard_state(Some(current_seg_id), &mut base_seg);
        self.copy_scope_store_from(Some(current_seg_id), &mut base_seg);
        let base_seg_id = self.alloc_segment(base_seg);
        let mut replay_seg_ids = vec![base_seg_id];

        let mut outside_seg_id = Some(base_seg_id);
        let mut interceptor_marker_remap: HashMap<Marker, Marker> = HashMap::new();
        for entry in chain_entries.into_iter().rev() {
            match entry {
                CallerChainEntry::Handler(entry) => {
                    let handler = entry.handler.clone();
                    let handler_marker = Marker::fresh();
                    let mut prompt_seg = Segment::new_prompt_with_types(
                        handler_marker,
                        outside_seg_id,
                        handler_marker,
                        handler.clone(),
                        entry.types.clone(),
                    );
                    self.copy_interceptor_guard_state(outside_seg_id, &mut prompt_seg);
                    self.copy_scope_store_from(outside_seg_id, &mut prompt_seg);
                    let prompt_seg_id = self.alloc_segment(prompt_seg);
                    replay_seg_ids.push(prompt_seg_id);
                    self.track_run_handler(&handler);

                    let mut body_seg = Segment::new(handler_marker, Some(prompt_seg_id));
                    self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
                    self.copy_scope_store_from(outside_seg_id, &mut body_seg);
                    let body_seg_id = self.alloc_segment(body_seg);
                    replay_seg_ids.push(body_seg_id);
                    outside_seg_id = Some(body_seg_id);
                }
                CallerChainEntry::Interceptor(entry) => {
                    let interceptor_marker = if active_interceptor_markers.contains(&entry.marker) {
                        entry.marker
                    } else {
                        let fresh_marker = Marker::fresh();
                        interceptor_marker_remap.insert(entry.marker, fresh_marker);
                        self.interceptor_state.insert(
                            fresh_marker,
                            entry.interceptor.clone(),
                            entry.types.clone(),
                            entry.mode,
                            entry.metadata.clone(),
                        );
                        fresh_marker
                    };

                    let mut body_seg = Segment::new(interceptor_marker, outside_seg_id);
                    body_seg.kind = SegmentKind::InterceptorBoundary {
                        interceptor: entry.interceptor,
                        types: entry.types,
                        mode: entry.mode,
                        metadata: entry.metadata,
                    };
                    self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
                    self.copy_scope_store_from(outside_seg_id, &mut body_seg);
                    let body_seg_id = self.alloc_segment(body_seg);
                    replay_seg_ids.push(body_seg_id);
                    outside_seg_id = Some(body_seg_id);
                }
            }
        }

        if !interceptor_marker_remap.is_empty() {
            Self::remap_interceptor_markers_in_continuation(
                &mut return_to,
                &interceptor_marker_remap,
            );
            self.remap_interceptor_markers_in_runtime_state(&interceptor_marker_remap);
        }
        let Some(base_seg) = self.segments.get_mut(base_seg_id) else {
            return StepEvent::Error(VMError::invalid_segment(
                "EvalInScope replay base segment not found",
            ));
        };
        base_seg.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::EvalInScopeReturn {
                continuation: return_to,
            },
        )));
        for old_marker in interceptor_marker_remap.keys() {
            self.interceptor_state.remove(*old_marker);
        }

        self.current_segment = outside_seg_id;
        self.current_seg_mut().pending_python = Some(PendingPython::EvalExpr { metadata });
        StepEvent::NeedsPython(PythonCall::EvalExpr { expr })
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
                        Frame::Program { metadata: None, .. } => {
                            // no metadata
                        }
                        Frame::InterceptorApply(continuation)
                        | Frame::InterceptorEval(continuation) => {
                            if let Some(metadata) = continuation.emitter_metadata.as_ref() {
                                stack.push(metadata.clone());
                            }
                        }
                        Frame::HandlerDispatch { .. } => {
                            // no metadata
                        }
                        Frame::EvalReturn(continuation) => match continuation.as_ref() {
                            EvalReturnContinuation::ApplyResolveFunction { metadata, .. }
                            | EvalReturnContinuation::ApplyResolveArg { metadata, .. }
                            | EvalReturnContinuation::ApplyResolveKwarg { metadata, .. }
                            | EvalReturnContinuation::ExpandResolveFactory { metadata, .. }
                            | EvalReturnContinuation::ExpandResolveArg { metadata, .. }
                            | EvalReturnContinuation::ExpandResolveKwarg { metadata, .. } => {
                                stack.push(metadata.clone());
                            }
                            EvalReturnContinuation::EvalInScopeReturn { .. } => {
                                // no metadata
                            }
                        },
                        Frame::MapReturn { mapper_meta, .. } => stack.push(mapper_meta.clone()),
                        Frame::FlatMapBindResult => {
                            // no metadata
                        }
                        Frame::FlatMapBindSource { binder_meta, .. } => {
                            stack.push(binder_meta.clone());
                        }
                        Frame::InterceptBodyReturn { .. } => {
                            // no metadata
                        }
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

    fn first_non_pure_arg(args: &[DoCtrl]) -> Option<(usize, DoCtrl)> {
        let arg_idx = args
            .iter()
            .position(|arg| !matches!(arg, DoCtrl::Pure { .. }))?;
        Some((arg_idx, args[arg_idx].clone()))
    }

    fn first_non_pure_kwarg(kwargs: &[(String, DoCtrl)]) -> Option<(usize, DoCtrl)> {
        let kwargs_idx = kwargs
            .iter()
            .position(|(_, value)| !matches!(value, DoCtrl::Pure { .. }))?;
        Some((kwargs_idx, kwargs[kwargs_idx].1.clone()))
    }

    fn collect_value_args(args: Vec<DoCtrl>) -> Vec<Value> {
        let mut values = Vec::with_capacity(args.len());
        for arg in args {
            match arg {
                DoCtrl::Pure { value } => values.push(value),
                non_pure @ DoCtrl::Map { .. }
                | non_pure @ DoCtrl::FlatMap { .. }
                | non_pure @ DoCtrl::Perform { .. }
                | non_pure @ DoCtrl::Resume { .. }
                | non_pure @ DoCtrl::Transfer { .. }
                | non_pure @ DoCtrl::TransferThrow { .. }
                | non_pure @ DoCtrl::ResumeThrow { .. }
                | non_pure @ DoCtrl::WithHandler { .. }
                | non_pure @ DoCtrl::WithIntercept { .. }
                | non_pure @ DoCtrl::Discontinue { .. }
                | non_pure @ DoCtrl::Delegate { .. }
                | non_pure @ DoCtrl::Pass { .. }
                | non_pure @ DoCtrl::GetContinuation
                | non_pure @ DoCtrl::GetHandlers
                | non_pure @ DoCtrl::GetTraceback { .. }
                | non_pure @ DoCtrl::CreateContinuation { .. }
                | non_pure @ DoCtrl::ResumeContinuation { .. }
                | non_pure @ DoCtrl::PythonAsyncSyntaxEscape { .. }
                | non_pure @ DoCtrl::Apply { .. }
                | non_pure @ DoCtrl::Expand { .. }
                | non_pure @ DoCtrl::IRStream { .. }
                | non_pure @ DoCtrl::Eval { .. }
                | non_pure @ DoCtrl::EvalInScope { .. }
                | non_pure @ DoCtrl::GetCallStack => {
                    unreachable!(
                        "collect_value_args requires DoCtrl::Pure values, got {non_pure:?}"
                    )
                }
            }
        }
        values
    }

    fn collect_value_kwargs(kwargs: Vec<(String, DoCtrl)>) -> Vec<(String, Value)> {
        let mut values = Vec::with_capacity(kwargs.len());
        for (key, value) in kwargs {
            match value {
                DoCtrl::Pure { value } => values.push((key, value)),
                non_pure @ DoCtrl::Map { .. }
                | non_pure @ DoCtrl::FlatMap { .. }
                | non_pure @ DoCtrl::Perform { .. }
                | non_pure @ DoCtrl::Resume { .. }
                | non_pure @ DoCtrl::Transfer { .. }
                | non_pure @ DoCtrl::TransferThrow { .. }
                | non_pure @ DoCtrl::ResumeThrow { .. }
                | non_pure @ DoCtrl::WithHandler { .. }
                | non_pure @ DoCtrl::WithIntercept { .. }
                | non_pure @ DoCtrl::Discontinue { .. }
                | non_pure @ DoCtrl::Delegate { .. }
                | non_pure @ DoCtrl::Pass { .. }
                | non_pure @ DoCtrl::GetContinuation
                | non_pure @ DoCtrl::GetHandlers
                | non_pure @ DoCtrl::GetTraceback { .. }
                | non_pure @ DoCtrl::CreateContinuation { .. }
                | non_pure @ DoCtrl::ResumeContinuation { .. }
                | non_pure @ DoCtrl::PythonAsyncSyntaxEscape { .. }
                | non_pure @ DoCtrl::Apply { .. }
                | non_pure @ DoCtrl::Expand { .. }
                | non_pure @ DoCtrl::IRStream { .. }
                | non_pure @ DoCtrl::Eval { .. }
                | non_pure @ DoCtrl::EvalInScope { .. }
                | non_pure @ DoCtrl::GetCallStack => {
                    unreachable!(
                        "collect_value_kwargs requires DoCtrl::Pure values, got {non_pure:?}"
                    )
                }
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
                if self.segments.get(caller_id).is_none() {
                    return StepEvent::Error(VMError::invalid_segment(
                        "caller segment not found in step_return",
                    ));
                }
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
            PendingPython::CallFuncReturn => self.receive_call_func_result(outcome),
            PendingPython::ExpandReturn {
                metadata,
                handler_return,
            } => self.receive_expand_result(metadata, handler_return, outcome),
            PendingPython::StepUserGenerator {
                stream,
                metadata,
                handler_kind,
            } => self.receive_step_user_generator_result(stream, metadata, handler_kind, outcome),
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

    fn receive_call_func_result(&mut self, outcome: PyCallOutcome) {
        match outcome {
            PyCallOutcome::Value(value) => {
                self.current_seg_mut().mode = Mode::Deliver(value);
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode =
                    self.mode_after_generror(GenErrorSite::CallFuncReturn, exception, false);
            }
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn returned_control_primitive_signature(value: &Value) -> Option<&'static str> {
        let Value::Python(result_obj) = value else {
            return None;
        };

        Python::attach(|py| match doctrl_tag(result_obj.bind(py)) {
            Some(DoExprTag::Pass) => Some("Pass()"),
            Some(DoExprTag::Resume) => Some("Resume(k, value)"),
            Some(DoExprTag::Delegate) => Some("Delegate()"),
            Some(DoExprTag::Transfer) => Some("Transfer(k, value)"),
            Some(DoExprTag::Discontinue) => Some("Discontinue(k, exn)"),
            Some(DoExprTag::ResumeContinuation) => Some("ResumeContinuation(k, value)"),
            _ => None,
        })
    }

    fn returned_control_primitive_exception(value: &Value) -> Option<PyException> {
        let signature = Self::returned_control_primitive_signature(value)?;
        Some(PyException::runtime_error(format!(
            "Handler returned {signature} but control primitives must be yielded, not returned.\n  Change: return {signature}  ->  yield {signature}"
        )))
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
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
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
                        match self.handle_yield_ir_stream(
                            stream,
                            metadata,
                            Some(HandlerKind::Python),
                        ) {
                            StepEvent::Continue => {}
                            StepEvent::Error(err) => {
                                self.current_seg_mut().mode =
                                    Mode::Throw(PyException::runtime_error(err.to_string()));
                            }
                            StepEvent::NeedsPython(_) | StepEvent::Done(_) => {
                                self.current_seg_mut().mode =
                                    Mode::Throw(PyException::runtime_error(
                                        "unexpected StepEvent from handle_yield_ir_stream",
                                    ));
                            }
                        }
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
        match self.classify_expand_result_as_doctrl(metadata, value, "ExpandReturn") {
            Ok(doctrl) => {
                self.current_seg_mut().mode = Mode::HandleYield(doctrl);
            }
            Err(exception) => {
                self.current_seg_mut().mode = Mode::Throw(exception);
            }
        }
    }

    fn classify_expand_result_as_doctrl(
        &self,
        metadata: Option<CallMetadata>,
        value: Value,
        context: &str,
    ) -> Result<DoCtrl, PyException> {
        let Value::Python(result_obj) = value else {
            return Err(PyException::type_error(format!(
                "{context}: expected DoeffGenerator, DoExpr, or EffectBase, got {value:?}"
            )));
        };

        Python::attach(|py| {
            let bound = result_obj.bind(py);
            if bound.is_instance_of::<DoeffGenerator>() {
                let (stream, metadata) =
                    Self::extract_doeff_generator(result_obj.clone_ref(py), metadata, context)?;
                return Ok(DoCtrl::IRStream { stream, metadata });
            }
            if bound.is_instance_of::<PyDoExprBase>() || bound.is_instance_of::<PyEffectBase>() {
                return classify_yielded_for_vm(self, py, bound);
            }
            let ty = bound
                .get_type()
                .name()
                .map(|name| name.to_string())
                .unwrap_or_else(|_| MISSING_UNKNOWN.to_string());
            Err(PyException::type_error(format!(
                "{context}: expected DoeffGenerator, DoExpr, or EffectBase, got {ty}"
            )))
        })
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
                self.emit_handler_threw_for_dispatch(dispatch_id, &exception);
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
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::GenYield(yielded) => {
                if self.current_segment.is_none() {
                    return;
                }
                let _ = self.handle_stream_yield(yielded, stream, metadata, handler_kind);
            }
            PyCallOutcome::GenReturn(value) => {
                if let Some(ref m) = metadata {
                    self.emit_frame_exited(m);
                }
                if handler_kind == Some(HandlerKind::Python) {
                    if let Some(exception) = Self::returned_control_primitive_exception(&value) {
                        self.current_seg_mut().mode = Mode::Throw(exception);
                        return;
                    }
                }
                self.current_seg_mut().mode = Mode::Deliver(value);
            }
            PyCallOutcome::GenError(exception) => {
                if let Some(continuation) =
                    self.handler_stream_throw_continuation(&stream, handler_kind)
                {
                    self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::TransferThrow {
                        continuation,
                        exception,
                    });
                    return;
                }

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
                        self.emit_handler_threw_for_dispatch(dispatch_id, &exception);
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
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
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
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
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

    pub fn current_dispatch_id(&self) -> Option<DispatchId> {
        self.current_segment_dispatch_id()
    }

    pub fn effect_for_dispatch(&self, dispatch_id: DispatchId) -> Option<DispatchEffect> {
        self.dispatch_state.effect_for_dispatch(dispatch_id)
    }

    pub fn lazy_pop_completed(&mut self) {
        self.dispatch_state.lazy_pop_completed();
    }

    pub fn find_matching_handler(
        &self,
        handler_chain: &[Marker],
        effect: &DispatchEffect,
    ) -> Result<(usize, Marker, KleisliRef), VMError> {
        let effect_obj =
            Python::attach(|py| dispatch_to_pyobject(py, effect).map(|obj| obj.unbind())).map_err(
                |err| {
                    VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    ))
                },
            )?;
        for (idx, marker) in handler_chain.iter().copied().enumerate() {
            let Some((prompt_seg_id, handler, types)) = self.find_prompt_boundary_by_marker(marker)
            else {
                return Err(VMError::internal(format!(
                    "find_matching_handler: missing handler marker {} at index {}",
                    marker.raw(),
                    idx
                )));
            };
            if handler.can_handle(effect)?
                && self
                    .should_invoke_handler(
                        &HandlerChainEntry {
                            marker,
                            prompt_seg_id,
                            handler: handler.clone(),
                            types,
                        },
                        &effect_obj,
                    )
                    .map_err(|err| {
                        VMError::python_error(format!(
                            "failed to evaluate WithHandler type filter: {err:?}"
                        ))
                    })?
            {
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
        // DEEP-HANDLER SELF-DISPATCH EXCLUSION (Koka/OCaml-style semantics):
        //
        // KleisliRef clause code executes *above* its own prompt boundary. During that interval,
        // dispatch must not re-select the currently active handler prompt, otherwise a handler
        // that performs an effect matching itself can recurse indefinitely.
        //
        // We scope exclusion to "active handler execution segment" only, so normal user-code
        // dispatch still sees the full caller-chain handlers.
        //
        // Python handlers remain permissive for cross-effect yields (different Python effect
        // type), because user handlers frequently delegate across effect families in the same
        // clause body; however same-effect Python re-dispatch is excluded to prevent loops.
        //
        // Scheduler/AST-stream paths rely on strict tail handoff (Transfer) and this exclusion
        // together to keep dispatch/switch behavior bounded under heavy task churn.
        let exclude_prompt = self.segments.get(seg_id).and_then(|seg| {
            let dispatch_id = seg.dispatch_id?;
            let ctx = self.dispatch_state.find_by_dispatch_id(dispatch_id)?;
            if ctx.completed {
                return None;
            }
            if seg_id != ctx.active_handler_seg_id {
                return None;
            }
            let active_marker = *ctx.handler_chain.get(ctx.handler_idx)?;
            let is_same_effect = Self::same_effect_python_type(&effect, &ctx.effect);
            if !is_same_effect {
                // Cross-effect yields from handler clauses should remain dispatchable.
                // Only same-effect re-dispatch is excluded to prevent self-recursion.
                return None;
            }
            self.find_prompt_boundary_in_caller_chain(seg_id, active_marker)
        });
        let mut handler_chain = self.handlers_in_caller_chain(seg_id);
        if let Some(excluded_prompt) = exclude_prompt {
            handler_chain.retain(|entry| entry.prompt_seg_id != excluded_prompt);
        }

        if handler_chain.is_empty() {
            if let Some(original) = original_exception.clone() {
                self.current_seg_mut().mode = Mode::Throw(original);
                return Ok(StepEvent::Continue);
            }
            return Err(VMError::unhandled_effect(effect));
        }

        let effect_obj =
            Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|obj| obj.unbind()))
                .map_err(|err| {
                    VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    ))
                })?;

        let mut selected: Option<(usize, HandlerChainEntry)> = None;
        let mut first_type_filtered_skip: Option<(usize, HandlerChainEntry)> = None;
        for (idx, entry) in handler_chain.iter().enumerate() {
            let can_handle = entry.handler.can_handle(&effect)?;
            if !can_handle {
                continue;
            }

            let should_invoke = self
                .should_invoke_handler(entry, &effect_obj)
                .map_err(|err| {
                    VMError::python_error(format!(
                        "failed to evaluate WithHandler type filter: {err:?}"
                    ))
                })?;
            if should_invoke {
                selected = Some((idx, entry.clone()));
                break;
            }

            if first_type_filtered_skip.is_none() {
                first_type_filtered_skip = Some((idx, entry.clone()));
            }
        }
        let mut bootstrap_with_pass = false;
        let (handler_idx, selected) = match selected {
            Some(found) => {
                if let Some(skipped) = &first_type_filtered_skip {
                    if skipped.0 < found.0 {
                        bootstrap_with_pass = true;
                        skipped.clone()
                    } else {
                        found
                    }
                } else {
                    found
                }
            }
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
            let (name, kind, file, line) = Self::handler_trace_info(&entry.handler);
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
        handler_seg.scope_store = current_seg.scope_store.clone();
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
            active_handler_seg_id: handler_seg_id,
            supports_error_context_conversion,
            k_origin: k_user.clone(),
            k_current: k_user.clone(),
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

        // Preserve handler scope when a type-filtered handler is skipped: this mirrors the
        // `Pass()` forwarding topology without invoking the skipped handler body.
        if bootstrap_with_pass {
            return Ok(self.handle_forward(ForwardKind::Pass, effect));
        }

        if handler.py_identity().is_some() {
            self.register_continuation(k_user.clone());
        }
        let ir_node = Self::invoke_kleisli_handler_expr(handler, effect, k_user)?;
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
        self.dispatch_state.dispatch_has_terminal_handler_action(
            dispatch_id,
            self.trace_state.active_chain_state(),
        )
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
        handler: KleisliRef,
        _py_identity: Option<PyShared>,
    ) {
        self.installed_handlers
            .retain(|entry| entry.marker != marker);
        self.installed_handlers
            .push(InstalledHandler { marker, handler });
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
        handler: KleisliRef,
        _py_identity: Option<PyShared>,
    ) -> bool {
        let Some(seg) = self.segments.get_mut(prompt_seg_id) else {
            let prompt_seg = Segment::new_prompt(marker, None, marker, handler.clone());
            self.alloc_segment(prompt_seg);
            self.track_run_handler(&handler);
            return true;
        };
        seg.kind = SegmentKind::PromptBoundary {
            handled_marker: marker,
            handler: handler.clone(),
            types: None,
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
                self.emit_resume_event(
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
            scope_store: k.scope_store.clone(),
            kind: self.structural_kind_for_marker(k.marker),
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
        mut value: Value,
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
        if let Err(err) =
            self.maybe_attach_active_chain_to_execution_context(k.dispatch_id, &mut value)
        {
            return StepEvent::Error(err);
        }

        if let Some((dispatch_id, original_exception, terminal)) = error_dispatch {
            if terminal {
                self.mark_dispatch_completed(dispatch_id);
                let enriched_exception =
                    match Self::enrich_original_exception_with_context(original_exception, value) {
                        Ok(exception) => exception,
                        Err(effect_err) => effect_err,
                    };
                let caller = match kind {
                    ContinuationActivationKind::Resume => kind
                        .caller_segment(self.current_segment)
                        .and_then(|seg_id| self.segments.get(seg_id))
                        .and_then(|seg| seg.caller),
                    ContinuationActivationKind::Transfer => {
                        self.segments.get(k.segment_id).and_then(|seg| seg.caller)
                    }
                };
                self.enter_continuation_segment_with_dispatch(&k, caller, None);
                self.current_seg_mut().mode = Mode::Throw(enriched_exception);
                return StepEvent::Continue;
            }
        }
        self.check_dispatch_completion_after_activation(kind, &k);

        let caller = match kind {
            ContinuationActivationKind::Resume => kind.caller_segment(self.current_segment),
            ContinuationActivationKind::Transfer => {
                self.segments.get(k.segment_id).and_then(|seg| seg.caller)
            }
        };
        self.enter_continuation_segment(&k, caller);
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
                "cannot throw into an unstarted continuation; use ResumeContinuation",
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
        let caller = self
            .segments
            .get(k.segment_id)
            .and_then(|seg| seg.caller)
            .or(self.current_segment);
        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller,
            scope_store: k.scope_store.clone(),
            kind: self.structural_kind_for_marker(k.marker),
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
        handler: KleisliRef,
        program: DoCtrl,
        types: Option<Vec<PyShared>>,
    ) -> StepEvent {
        let plan = match DispatchState::prepare_with_handler(handler, self.current_segment) {
            Ok(plan) => plan,
            Err(err) => return StepEvent::Error(err),
        };
        let prompt_handler = plan.handler.clone();

        let mut prompt_seg = Segment::new_prompt_with_types(
            plan.handler_marker,
            Some(plan.outside_seg_id),
            plan.handler_marker,
            prompt_handler.clone(),
            types,
        );
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), &mut prompt_seg);
        self.copy_scope_store_from(Some(plan.outside_seg_id), &mut prompt_seg);
        let prompt_seg_id = self.alloc_segment(prompt_seg);
        self.track_run_handler(&prompt_handler);

        let mut body_seg = Segment::new(plan.handler_marker, Some(prompt_seg_id));
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), &mut body_seg);
        self.copy_scope_store_from(Some(plan.outside_seg_id), &mut body_seg);
        let body_seg_id = self.alloc_segment(body_seg);

        self.current_segment = Some(body_seg_id);
        self.evaluate(program)
    }

    fn handle_with_intercept(
        &mut self,
        interceptor: KleisliRef,
        program: DoCtrl,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let body_seg = match self.interceptor_state.prepare_with_intercept(
            interceptor,
            types,
            mode,
            metadata,
            self.current_segment,
            &self.segments,
        ) {
            Ok(segment) => segment,
            Err(err) => return StepEvent::Error(err),
        };
        let mut body_seg = body_seg;
        self.copy_scope_store_from(self.current_segment, &mut body_seg);
        let body_seg_id = self.alloc_segment(body_seg);

        self.current_segment = Some(body_seg_id);
        self.evaluate(program)
    }

    fn clear_segment_frames(&mut self, segment_id: Option<SegmentId>) {
        if let Some(seg_id) = segment_id {
            if let Some(seg) = self.segments.get_mut(seg_id) {
                seg.frames.clear();
            }
        }
    }

    fn emit_forward_active_chain_event(
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
            match kind {
                ForwardKind::Delegate => self.trace_state.emit_delegated(
                    dispatch_id,
                    from_name,
                    from_idx,
                    to_name,
                    to_idx,
                    to_kind,
                    to_source_file,
                    to_source_line,
                ),
                ForwardKind::Pass => self.trace_state.emit_passed(
                    dispatch_id,
                    from_name,
                    from_idx,
                    to_name,
                    to_idx,
                    to_kind,
                    to_source_file,
                    to_source_line,
                ),
            }
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
                        Some(ctx.k_current.clone())
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
                ctx.k_current = k_new;
            }
            ForwardKind::Pass => {
                self.clear_segment_frames(inner_seg_id);
            }
        }

        let effect_obj =
            match Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|obj| obj.unbind())) {
                Ok(obj) => obj,
                Err(err) => {
                    return StepEvent::Error(VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    )))
                }
            };

        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            let Some(entry) = visible_chain.iter().find(|entry| entry.marker == marker) else {
                return StepEvent::Error(VMError::internal(format!(
                    "{}: missing handler marker {} at index {}",
                    kind.missing_handler_context(),
                    marker.raw(),
                    idx
                )));
            };
            let handler = entry.handler.clone();
            let can_handle = match handler.can_handle(&effect) {
                Ok(value) => value,
                Err(err) => return StepEvent::Error(err),
            };
            if can_handle {
                let should_invoke = match self.should_invoke_handler(entry, &effect_obj) {
                    Ok(value) => value,
                    Err(err) => {
                        return StepEvent::Error(VMError::python_error(format!(
                            "failed to evaluate WithHandler type filter: {err:?}"
                        )))
                    }
                };
                if !should_invoke {
                    continue;
                }
                let supports_error_context_conversion = handler.supports_error_context_conversion();
                self.emit_forward_active_chain_event(
                    kind,
                    dispatch_id,
                    &handler_chain,
                    from_idx,
                    idx,
                    marker,
                );

                let mut handler_seg = Segment::new(marker, inner_seg_id);
                self.copy_scope_store_from(inner_seg_id, &mut handler_seg);
                handler_seg.dispatch_id = Some(dispatch_id);
                self.copy_interceptor_guard_state(inner_seg_id, &mut handler_seg);
                let handler_seg_id = self.alloc_segment(handler_seg);

                let k_user = {
                    let Some(ctx) = self.dispatch_state.find_mut_by_dispatch_id(dispatch_id) else {
                        return StepEvent::Error(VMError::internal(
                            "forward target dispatch context not found",
                        ));
                    };
                    ctx.handler_idx = idx;
                    ctx.active_handler_seg_id = handler_seg_id;
                    ctx.supports_error_context_conversion = supports_error_context_conversion;
                    ctx.effect = effect.clone();
                    ctx.k_current.clone()
                };

                self.current_segment = Some(handler_seg_id);

                if handler.py_identity().is_some() {
                    self.register_continuation(k_user.clone());
                }
                let ir_node =
                    match Self::invoke_kleisli_handler_expr(handler, effect.clone(), k_user) {
                        Ok(node) => node,
                        Err(err) => return StepEvent::Error(err),
                    };
                return self.evaluate(ir_node);
            }
        }

        if let Some(original_exception) = self.original_exception_for_dispatch(dispatch_id) {
            self.mark_dispatch_completed(dispatch_id);
            self.current_seg_mut().mode = Mode::Throw(original_exception);
            return StepEvent::Continue;
        }

        self.dispatch_fatal_error_event(VMError::delegate_no_outer_handler(effect))
    }

    fn handle_delegate(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_forward(ForwardKind::Delegate, effect)
    }

    fn handle_pass(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_forward(ForwardKind::Pass, effect)
    }

    fn handle_handler_return(&mut self, mut value: Value) -> StepEvent {
        self.lazy_pop_completed();

        let active_python_handler = self.current_dispatch_id().and_then(|dispatch_id| {
            let ctx = self.dispatch_state.find_by_dispatch_id(dispatch_id)?;
            let marker = *ctx.handler_chain.get(ctx.handler_idx)?;
            let (_, kind, _, _) = self.marker_handler_trace_info(marker)?;
            Some((
                dispatch_id,
                kind == HandlerKind::Python,
                ctx.k_current.cont_id,
            ))
        });
        if let Some((_dispatch_id, true, cont_id)) = active_python_handler {
            if self.continuation_registry.contains_key(&cont_id)
                && !self.is_one_shot_consumed(cont_id)
            {
                self.mark_one_shot_consumed(cont_id);
                return self.throw_runtime_error(&format!(
                    "handler returned without consuming continuation {}; use Resume(k, v), Transfer(k, v), Discontinue(k, exn), or Pass()",
                    cont_id.raw(),
                ));
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
        if let Err(err) =
            self.maybe_attach_active_chain_to_execution_context(Some(dispatch_id), &mut value)
        {
            return StepEvent::Error(err);
        }

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
        self.emit_resume_event(
            top_snapshot.dispatch_id,
            handler_name,
            value_repr,
            &top_snapshot.k_current,
            false,
        );

        let original_exception = {
            let Some(ctx) = self.dispatch_state.find_mut_by_dispatch_id(dispatch_id) else {
                self.current_seg_mut().mode = Mode::Deliver(value);
                return StepEvent::Continue;
            };

            if caller_id == ctx.prompt_seg_id {
                ctx.completed = true;
                self.consumed_cont_ids.insert(ctx.k_current.cont_id);
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

    fn current_visible_handlers(&self) -> Vec<KleisliRef> {
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
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("Map outside current segment"));
        };
        seg.push_frame(Frame::MapReturn {
            mapper,
            mapper_meta,
        });
        self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Eval {
            expr: source,
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
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("FlatMap outside current segment"));
        };
        seg.push_frame(Frame::FlatMapBindSource {
            binder,
            binder_meta,
        });
        self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Eval {
            expr: source,
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
        let k = ctx.k_current.clone();
        self.register_continuation(k.clone());
        self.current_seg_mut().mode = Mode::Deliver(Value::Continuation(k));
        StepEvent::Continue
    }

    fn handle_get_handlers(&mut self) -> StepEvent {
        let Some(dispatch_id) = self.current_dispatch_id() else {
            return StepEvent::Error(VMError::internal("GetHandlers outside dispatch"));
        };
        let Some(ctx) = self.dispatch_state.find_by_dispatch_id(dispatch_id) else {
            return StepEvent::Error(VMError::internal(
                "GetHandlers called with missing dispatch context",
            ));
        };
        // Preserve full caller-visible handler stack (top-most first).
        //
        // This is part of the public contract used by tests and user-space
        // handlers. Continuation installation handles deduplication when these
        // handlers are reapplied from within active dispatch contexts.
        let handlers = self
            .handlers_in_caller_chain(ctx.k_origin.segment_id)
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
        handlers: Vec<KleisliRef>,
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
            let base_handler = k.handlers[idx].clone();
            let handler = if let Some(Some(identity)) = k.handler_identities.get(idx) {
                Arc::new(IdentityKleisli::new(base_handler, identity.clone())) as KleisliRef
            } else {
                base_handler
            };
            let handler_marker = Marker::fresh();
            let mut prompt_seg = Segment::new_prompt(
                handler_marker,
                outside_seg_id,
                handler_marker,
                handler.clone(),
            );
            self.copy_interceptor_guard_state(outside_seg_id, &mut prompt_seg);
            self.copy_scope_store_from(outside_seg_id, &mut prompt_seg);
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            self.track_run_handler(&handler);
            let mut body_seg = Segment::new(handler_marker, Some(prompt_seg_id));
            self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
            self.copy_scope_store_from(outside_seg_id, &mut body_seg);
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
