//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::RwLock;

use pyo3::exceptions::{PyBaseException, PyException as PyStdException};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule, PyTuple};

use crate::arena::FiberArena;
use crate::bridge::{classify_yielded_for_vm, doctrl_tag, doctrl_to_pyexpr_for_vm};
use crate::capture::{
    ActiveChainEntry, EffectResult, FrameId, HandlerAction, HandlerDispatchEntry, HandlerKind,
    HandlerSnapshotEntry, HandlerStatus, TraceEntry,
};
use crate::continuation::{Continuation, OwnedControlContinuation, PendingContinuation};
use crate::debug_state::DebugState;
use crate::do_ctrl::{DoCtrl, DoExprTag, InterceptMode, PyDoExprBase};
use crate::doeff_generator::DoeffGenerator;
use crate::driver::{Mode, PyException, StepEvent};
use crate::effect::{
    dispatch_ref_as_python, dispatch_to_pyobject, make_get_execution_context_effect,
    DispatchEffect, PyEffectBase, PyExecutionContext, PyGetExecutionContext,
};
use crate::error::VMError;
use crate::frame::{
    CallMetadata, DispatchDisplay, DispatchEffectSite, EvalReturnContinuation, Frame,
    InterceptorChainLink, InterceptorContinuation, ProgramDispatch, ProgramFrameSnapshot,
};
use crate::ids::{ContId, Marker, SegmentId};
use crate::ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream};
use crate::kleisli::{notify_run_handlers_completed, IdentityKleisli, KleisliRef};
use crate::memory_stats::live_object_counts;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::segment::{Segment, SegmentKind};
use crate::trace_state::{LiveDispatchSnapshot, TraceState};
use crate::value::Value;

pub use crate::var_store::VarStore;

static NEXT_RUN_TOKEN: AtomicU64 = AtomicU64::new(1);

const MISSING_UNKNOWN: &str = "[MISSING] <unknown>";

#[path = "vm/handler.rs"]
mod handler_impl;

#[path = "vm/dispatch.rs"]
mod dispatch_impl;

#[path = "vm/step.rs"]
mod step_impl;

#[path = "vm/vm_trace.rs"]
mod vm_trace_impl;

#[path = "vm/var_store.rs"]
mod var_store_impl;

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
    VmRaisedUser,
    VmRaisedInternal,
}

impl GenErrorSite {
    fn allows_error_conversion(self) -> bool {
        matches!(
            self,
            GenErrorSite::EvalExpr
                | GenErrorSite::CallFuncReturn
                | GenErrorSite::ExpandReturnHandler
                | GenErrorSite::ExpandReturnProgram
                | GenErrorSite::StepUserGeneratorConverted
                | GenErrorSite::StepUserGeneratorDirect
                | GenErrorSite::RustProgramContinuation
                | GenErrorSite::AsyncEscape
                | GenErrorSite::VmRaisedUser
                | GenErrorSite::VmRaisedInternal
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

#[derive(Debug, Clone, Copy)]
struct CachedHandlerResolution {
    prompt_seg_id: SegmentId,
    segment_epoch: u64,
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
struct HandlerChainEntry {
    marker: Marker,
    prompt_seg_id: SegmentId,
    handler: KleisliRef,
    types: Option<Arc<Vec<PyShared>>>,
}

#[derive(Clone)]
struct WithHandlerPlan {
    handler_marker: Marker,
    outside_seg_id: SegmentId,
    handler: KleisliRef,
}

struct DispatchOriginView {
    origin_cont_id: ContId,
    parent_origin_cont_id: Option<ContId>,
    effect: DispatchEffect,
    k_origin: Continuation,
    original_exception: Option<PyException>,
}

impl Clone for DispatchOriginView {
    fn clone(&self) -> Self {
        Self {
            origin_cont_id: self.origin_cont_id,
            parent_origin_cont_id: self.parent_origin_cont_id,
            effect: self.effect.clone(),
            k_origin: self.k_origin.clone_handle(),
            original_exception: self.original_exception.clone(),
        }
    }
}

#[derive(Clone)]
enum CallerChainEntry {
    Handler(HandlerChainEntry),
    Interceptor(InterceptorChainLink),
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
    pub segments: FiberArena,
    pub var_store: VarStore,
    pub py_store: Option<PyStore>,
    pub mode: Mode,
    pub pending_python: Option<PendingPython>,
    pub current_segment: Option<SegmentId>,
    pub completed_segment: Option<SegmentId>,
    pub(crate) debug: DebugState,
    pub(crate) trace_state: TraceState,
    handler_type_match_cache: HashMap<(usize, usize), bool>,
    segment_handler_resolution_cache: HashMap<(SegmentId, usize), CachedHandlerResolution>,
    segment_topology_epochs: HashMap<SegmentId, u64>,
    next_segment_topology_epoch: u64,
    shared_builtin_prompt_cache: RwLock<HashMap<SegmentId, SegmentId>>,
    pub active_run_token: Option<u64>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: FiberArena::new(),
            var_store: VarStore::new(),
            py_store: None,
            mode: Mode::Deliver(Value::Unit),
            pending_python: None,
            current_segment: None,
            completed_segment: None,
            debug: DebugState::new(DebugConfig::default()),
            trace_state: TraceState::default(),
            handler_type_match_cache: HashMap::new(),
            segment_handler_resolution_cache: HashMap::new(),
            segment_topology_epochs: HashMap::new(),
            next_segment_topology_epoch: 1,
            shared_builtin_prompt_cache: RwLock::new(HashMap::new()),
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
        self.segments.clear();
        self.var_store.clear_run_local();
        self.mode = Mode::Deliver(Value::Unit);
        self.pending_python = None;
        self.current_segment = None;
        self.completed_segment = None;
        self.trace_state.clear();
        self.handler_type_match_cache.clear();
        self.segment_handler_resolution_cache.clear();
        self.segment_topology_epochs.clear();
        self.next_segment_topology_epoch = 1;
        self.shared_builtin_prompt_cache
            .get_mut()
            .expect("shared builtin prompt cache poisoned")
            .clear();
        token
    }

    pub fn current_run_token(&self) -> Option<u64> {
        self.active_run_token
    }

    pub fn end_active_run_session(&mut self) {
        let Some(run_token) = self.active_run_token.take() else {
            return;
        };

        notify_run_handlers_completed(run_token);
        self.segments.clear();
        self.segments.shrink_to_fit();
        self.var_store.clear_run_local();
        self.var_store.shrink_run_local_to_fit();
        self.mode = Mode::Deliver(Value::Unit);
        self.pending_python = None;
        self.current_segment = None;
        self.completed_segment = None;
        self.trace_state.clear();
        self.trace_state.shrink_to_fit();
        self.debug.shrink_to_fit();
        self.handler_type_match_cache.clear();
        self.handler_type_match_cache.shrink_to_fit();
        self.segment_handler_resolution_cache.clear();
        self.segment_handler_resolution_cache.shrink_to_fit();
        self.segment_topology_epochs.clear();
        self.segment_topology_epochs.shrink_to_fit();
        self.next_segment_topology_epoch = 1;
        self.shared_builtin_prompt_cache
            .get_mut()
            .expect("shared builtin prompt cache poisoned")
            .clear();
        self.shared_builtin_prompt_cache
            .get_mut()
            .expect("shared builtin prompt cache poisoned")
            .shrink_to_fit();
    }

    pub fn enable_trace(&mut self, enabled: bool) {
        self.debug.enable_trace(enabled);
    }

    pub fn trace_events(&self) -> &[TraceEvent] {
        self.debug.trace_events()
    }

    pub fn continuation_count(&self) -> usize {
        if self.active_run_token.is_none() {
            return 0;
        }
        live_object_counts().live_continuations
    }

    pub fn dispatch_count(&self) -> usize {
        let mut dispatch_ids = HashSet::new();
        for (_, segment) in self.segments.iter() {
            for frame in &segment.frames {
                if let Frame::Program {
                    dispatch: Some(dispatch),
                    ..
                } = frame
                {
                    dispatch_ids.insert(dispatch.origin_cont_id);
                }
            }
        }
        for (_, segment) in self.segments.iter() {
            if let Some(dispatch) = &segment.pending_program_dispatch {
                dispatch_ids.insert(dispatch.origin_cont_id);
            }
        }
        dispatch_ids.len()
    }

    pub fn segment_dispatch_binding_count(&self) -> usize {
        self.segments
            .iter()
            .filter(|(seg_id, _)| {
                self.segment_program_dispatch(*seg_id).is_some()
                    || self
                        .segments
                        .get(*seg_id)
                        .and_then(|segment| segment.pending_program_dispatch.as_ref())
                        .is_some()
            })
            .count()
    }

    pub fn dispatch_capacity(&self) -> usize {
        self.dispatch_count()
    }

    pub fn segment_dispatch_binding_capacity(&self) -> usize {
        self.segment_dispatch_binding_count()
    }

    pub fn trace_frame_stack_count(&self) -> usize {
        self.trace_state.frame_stack_len()
    }

    pub fn trace_dispatch_display_count(&self) -> usize {
        self.trace_state.dispatch_display_count()
    }

    pub fn trace_frame_stack_capacity(&self) -> usize {
        self.trace_state.frame_stack_capacity()
    }

    pub fn trace_dispatch_display_capacity(&self) -> usize {
        self.trace_state.dispatch_display_capacity()
    }

    pub fn scope_state_count(&self) -> usize {
        self.var_store.handler_state_count()
    }

    pub fn scope_writer_log_count(&self) -> usize {
        self.var_store.writer_log_count()
    }

    pub fn scope_epoch_count(&self) -> usize {
        0
    }

    pub fn scope_state_capacity(&self) -> usize {
        self.var_store.handler_state_capacity()
    }

    pub fn scope_writer_log_capacity(&self) -> usize {
        self.var_store.writer_log_capacity()
    }

    pub fn scope_epoch_capacity(&self) -> usize {
        0
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
        let seg_id = self.segments.alloc(segment);
        self.bump_segment_topology_epoch(seg_id);
        seg_id
    }

    pub fn free_segment(&mut self, id: SegmentId) {
        if self.segments.get(id).is_none() {
            return;
        }
        self.segments.free(id);
        self.segment_topology_epochs.remove(&id);
    }

    pub fn current_segment_mut(&mut self) -> Option<&mut Segment> {
        self.current_segment
            .and_then(|id| self.segments.get_mut(id))
    }

    pub fn current_segment_ref(&self) -> Option<&Segment> {
        self.current_segment.and_then(|id| self.segments.get(id))
    }

    pub(crate) fn continuation_segment_ref(&self, continuation: &Continuation) -> Option<&Segment> {
        continuation
            .segment_id()
            .and_then(|seg_id| self.segments.get(seg_id))
    }

    pub(crate) fn continuation_segment_mut(
        &mut self,
        continuation: &Continuation,
    ) -> Option<&mut Segment> {
        continuation
            .segment_id()
            .and_then(|seg_id| self.segments.get_mut(seg_id))
    }

    pub(crate) fn continuation_frames(&self, continuation: &Continuation) -> Option<&[Frame]> {
        self.continuation_segment_ref(continuation)
            .map(|segment| segment.frames.as_slice())
    }

    pub(crate) fn continuation_frame_stack(
        &self,
        continuation: &Continuation,
    ) -> Vec<ProgramFrameSnapshot> {
        continuation
            .fibers()
            .iter()
            .filter_map(|fiber_id| self.segments.get(*fiber_id))
            .flat_map(|segment| {
                segment.frames.iter().filter_map(|frame| match frame {
                    Frame::Program {
                        stream,
                        metadata,
                        handler_kind,
                        dispatch,
                    } => Some(ProgramFrameSnapshot {
                        stream: stream.clone(),
                        metadata: metadata.clone(),
                        handler_kind: *handler_kind,
                        dispatch: dispatch.clone(),
                    }),
                    Frame::LexicalScope { .. } => None,
                    Frame::EvalReturn(_)
                    | Frame::MapReturn { .. }
                    | Frame::FlatMapBindResult
                    | Frame::FlatMapBindSource { .. } => None,
                })
            })
            .collect()
    }

    pub(crate) fn continuation_pending_error_context(
        &self,
        continuation: &Continuation,
    ) -> Option<&PyException> {
        continuation
            .segment_id()
            .and_then(|seg_id| self.segments.get(seg_id))
            .and_then(|segment| segment.pending_error_context.as_ref())
    }

    pub(crate) fn segment_program_dispatch(&self, seg_id: SegmentId) -> Option<&ProgramDispatch> {
        let segment = self.segments.get(seg_id)?;
        segment.frames.iter().rev().find_map(|frame| match frame {
            Frame::Program {
                dispatch: Some(dispatch),
                ..
            } => Some(dispatch),
            Frame::LexicalScope { .. } => None,
            Frame::Program { .. }
            | Frame::EvalReturn(_)
            | Frame::MapReturn { .. }
            | Frame::FlatMapBindResult
            | Frame::FlatMapBindSource { .. } => None,
        })
    }

    pub(crate) fn segment_program_dispatch_mut(
        &mut self,
        seg_id: SegmentId,
    ) -> Option<&mut ProgramDispatch> {
        let segment = self.segments.get_mut(seg_id)?;
        segment
            .frames
            .iter_mut()
            .rev()
            .find_map(|frame| match frame {
                Frame::Program {
                    dispatch: Some(dispatch),
                    ..
                } => Some(dispatch),
                Frame::LexicalScope { .. } => None,
                Frame::Program { .. }
                | Frame::EvalReturn(_)
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. } => None,
            })
    }

    pub(crate) fn set_pending_program_dispatch(
        &mut self,
        seg_id: SegmentId,
        dispatch: ProgramDispatch,
    ) {
        if let Some(segment) = self.segments.get_mut(seg_id) {
            segment.pending_program_dispatch = Some(dispatch);
        }
    }

    pub(crate) fn clear_pending_program_dispatch(&mut self, seg_id: SegmentId) {
        if let Some(segment) = self.segments.get_mut(seg_id) {
            segment.pending_program_dispatch = None;
        }
    }

    fn take_pending_program_dispatch(&mut self, seg_id: SegmentId) -> Option<ProgramDispatch> {
        self.segments
            .get_mut(seg_id)
            .and_then(|segment| segment.pending_program_dispatch.take())
    }

    pub(crate) fn push_program_frame(
        &mut self,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
    ) -> Result<(), VMError> {
        let Some(seg_id) = self.current_segment else {
            return Err(VMError::internal(
                "push_program_frame called without current segment",
            ));
        };
        let dispatch = self
            .take_pending_program_dispatch(seg_id)
            .or_else(|| handler_kind.and_then(|_| self.segment_program_dispatch(seg_id).cloned()));
        let Some(seg) = self.current_segment_mut() else {
            return Err(VMError::internal(
                "push_program_frame current segment missing after dispatch lookup",
            ));
        };
        seg.push_frame(Frame::Program {
            stream,
            metadata,
            handler_kind,
            dispatch,
        });
        Ok(())
    }

    pub fn parent_segment(&self, seg_id: SegmentId) -> Option<SegmentId> {
        self.segments.get(seg_id).and_then(|segment| segment.parent)
    }

    pub(crate) fn clear_pending_error_context(&mut self, seg_id: SegmentId) {
        if let Some(segment) = self.segments.get_mut(seg_id) {
            segment.pending_error_context = None;
        }
    }

    pub(crate) fn set_pending_error_context(&mut self, seg_id: SegmentId, exception: PyException) {
        if let Some(segment) = self.segments.get_mut(seg_id) {
            segment.pending_error_context = Some(exception);
        }
    }

    pub(crate) fn pending_error_context(&self, seg_id: SegmentId) -> Option<&PyException> {
        self.segments
            .get(seg_id)
            .and_then(|segment| segment.pending_error_context.as_ref())
    }

    pub(crate) fn interceptor_eval_depth(&self, seg_id: SegmentId) -> usize {
        self.segments
            .get(seg_id)
            .map(|segment| segment.interceptor_eval_depth)
            .unwrap_or(0)
    }

    pub(crate) fn increment_interceptor_eval_depth(&mut self, seg_id: SegmentId) {
        if let Some(segment) = self.segments.get_mut(seg_id) {
            segment.interceptor_eval_depth = segment.interceptor_eval_depth.saturating_add(1);
        }
    }

    pub(crate) fn decrement_interceptor_eval_depth(&mut self, seg_id: SegmentId) {
        if let Some(segment) = self.segments.get_mut(seg_id) {
            segment.interceptor_eval_depth = segment.interceptor_eval_depth.saturating_sub(1);
        }
    }

    pub(crate) fn is_interceptor_skipped_on(&self, seg_id: SegmentId, marker: Marker) -> bool {
        self.segments
            .get(seg_id)
            .is_some_and(|segment| segment.interceptor_skip_stack.contains(&marker))
    }

    pub(crate) fn push_interceptor_skip_on(&mut self, seg_id: SegmentId, marker: Marker) {
        if let Some(segment) = self.segments.get_mut(seg_id) {
            segment.interceptor_skip_stack.push(marker);
        }
    }

    pub(crate) fn pop_interceptor_skip_on(&mut self, seg_id: SegmentId, marker: Marker) {
        let Some(segment) = self.segments.get_mut(seg_id) else {
            return;
        };
        if let Some(pos) = segment
            .interceptor_skip_stack
            .iter()
            .rposition(|active| *active == marker)
        {
            segment.interceptor_skip_stack.remove(pos);
        }
    }

    pub(crate) fn interceptor_skip_stack_is_empty(&self, seg_id: SegmentId) -> bool {
        self.segments
            .get(seg_id)
            .map_or(true, |segment| segment.interceptor_skip_stack.is_empty())
    }

    pub(crate) fn inherit_interceptor_guard_state(
        &mut self,
        source_seg_id: Option<SegmentId>,
        child_seg_id: SegmentId,
    ) {
        let Some(source_seg_id) = source_seg_id else {
            return;
        };
        let Some((source_depth, source_stack)) = self.segments.get(source_seg_id).map(|segment| {
            (
                segment.interceptor_eval_depth,
                segment.interceptor_skip_stack.clone(),
            )
        }) else {
            return;
        };
        let Some(child_segment) = self.segments.get_mut(child_seg_id) else {
            return;
        };
        child_segment.interceptor_eval_depth = source_depth;
        child_segment.interceptor_skip_stack = source_stack;
    }

    pub(crate) fn continuation_parent(&self, continuation: &Continuation) -> Option<SegmentId> {
        continuation
            .outermost_fiber_id()
            .and_then(|fiber_id| self.segments.get(fiber_id))
            .and_then(|segment| segment.parent)
    }

    fn collect_continuation_parent(
        continuation: &Continuation,
        parents: &mut std::collections::HashSet<SegmentId>,
    ) {
        if let Some(fiber_id) = continuation.outermost_fiber_id() {
            parents.insert(fiber_id);
        }
    }

    fn collect_eval_return_captured_caller(
        eval_return: &mut EvalReturnContinuation,
        parents: &mut std::collections::HashSet<SegmentId>,
    ) {
        match eval_return {
            EvalReturnContinuation::ResumeToContinuation { continuation }
            | EvalReturnContinuation::ReturnToContinuation { continuation }
            | EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                Self::collect_continuation_parent(continuation, parents)
            }
            EvalReturnContinuation::ApplyResolveFunction { .. }
            | EvalReturnContinuation::ApplyResolveArg { .. }
            | EvalReturnContinuation::ApplyResolveKwarg { .. }
            | EvalReturnContinuation::ExpandResolveFactory { .. }
            | EvalReturnContinuation::ExpandResolveArg { .. }
            | EvalReturnContinuation::ExpandResolveKwarg { .. }
            | EvalReturnContinuation::InterceptApplyResult { .. }
            | EvalReturnContinuation::InterceptEvalResult { .. }
            | EvalReturnContinuation::TailResumeReturn => {}
        }
    }

    fn reparent_owned_continuation_callers(
        &mut self,
        old_parent: SegmentId,
        new_parent: Option<SegmentId>,
    ) -> usize {
        let mut parent_rewrites = std::collections::HashSet::new();

        for (_, segment) in self.segments.iter_mut() {
            for frame in &mut segment.frames {
                if let Frame::Program {
                    dispatch: Some(dispatch),
                    ..
                } = frame
                {
                    if let Some(fiber_id) = dispatch.origin_fiber_ids.last() {
                        parent_rewrites.insert(*fiber_id);
                    }
                    if let Some(fiber_id) = dispatch.handler_fiber_ids.last() {
                        parent_rewrites.insert(*fiber_id);
                    }
                }
                if let Frame::EvalReturn(eval_return) = frame {
                    Self::collect_eval_return_captured_caller(eval_return, &mut parent_rewrites);
                }
            }
        }

        if let Some(PendingPython::RustProgramContinuation { k, .. }) = self.pending_python.as_mut()
        {
            Self::collect_continuation_parent(k, &mut parent_rewrites);
        }

        let mut rewired = 0usize;
        for fiber_id in parent_rewrites {
            let Some(segment) = self.segments.get_mut(fiber_id) else {
                continue;
            };
            if segment.parent == Some(old_parent) {
                segment.parent = new_parent;
                rewired += 1;
            }
        }

        rewired
    }

    pub(crate) fn normalize_live_parent_hint(
        &self,
        parent: Option<SegmentId>,
    ) -> Option<SegmentId> {
        parent.filter(|seg_id| self.segments.get(*seg_id).is_some())
    }

    fn next_segment_topology_epoch(&mut self) -> u64 {
        let epoch = self.next_segment_topology_epoch;
        self.next_segment_topology_epoch += 1;
        epoch
    }

    fn bump_segment_topology_epoch(&mut self, seg_id: SegmentId) {
        let epoch = self.next_segment_topology_epoch();
        self.segment_topology_epochs.insert(seg_id, epoch);
    }

    fn segment_topology_epoch(&self, seg_id: SegmentId) -> u64 {
        self.segment_topology_epochs
            .get(&seg_id)
            .copied()
            .unwrap_or(0)
    }

    fn touch_segment_topology_subtree(&mut self, root_seg_id: SegmentId) {
        self.touch_segment_topology_subtrees(std::iter::once(root_seg_id));
    }

    fn touch_segment_topology_subtrees<I>(&mut self, roots: I)
    where
        I: IntoIterator<Item = SegmentId>,
    {
        let roots = roots.into_iter().collect::<HashSet<_>>();
        if roots.is_empty() {
            return;
        }

        let epoch = self.next_segment_topology_epoch();
        for (seg_id, _) in self.segments.iter() {
            let mut cursor = Some(seg_id);
            while let Some(current_id) = cursor {
                if roots.contains(&current_id) {
                    self.segment_topology_epochs.insert(seg_id, epoch);
                    break;
                }
                cursor = self
                    .segments
                    .get(current_id)
                    .and_then(|segment| segment.parent);
            }
        }
    }

    pub fn reparent_children(
        &mut self,
        old_parent: SegmentId,
        new_parent: Option<SegmentId>,
    ) -> usize {
        let affected_roots = self
            .segments
            .iter()
            .filter_map(|(seg_id, segment)| (segment.parent == Some(old_parent)).then_some(seg_id))
            .collect::<Vec<_>>();
        let mut rewired = self.segments.reparent_children(old_parent, new_parent);
        if rewired > 0 {
            self.touch_segment_topology_subtrees(affected_roots);
        }
        rewired += self.reparent_owned_continuation_callers(old_parent, new_parent);
        rewired
    }

    fn collect_outputs_from_chain(
        &self,
        start_seg_id: SegmentId,
    ) -> (HashMap<String, Value>, Vec<Value>) {
        let mut chain = Vec::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            chain.push(seg_id);
            cursor = seg.parent;
        }
        chain.reverse();

        let mut state = HashMap::new();
        let mut logs = Vec::new();
        let mut seen_state_segments = HashSet::new();
        let mut seen_log_segments = HashSet::new();
        for seg_id in chain {
            if let Some((state_seg_id, shared_state)) = self.state_output_entries(seg_id) {
                if seen_state_segments.insert(state_seg_id) && !shared_state.is_empty() {
                    state.extend(shared_state);
                }
            }

            if let Some((log_seg_id, shared_logs)) = self.log_output_entries(seg_id) {
                if seen_log_segments.insert(log_seg_id) && !shared_logs.is_empty() {
                    logs.extend(shared_logs);
                }
            }
        }

        (state, logs)
    }

    pub fn read_handler_state_at(
        &self,
        prompt_seg_id: SegmentId,
        key: &str,
        missing_is_none: bool,
    ) -> Option<Value> {
        let prompt_seg_id = self.shared_builtin_handler_prompt(prompt_seg_id);
        let state_handler_prompt = self.segments.get(prompt_seg_id).is_some_and(|seg| {
            seg.kind
                .prompt_boundary()
                .is_some_and(|boundary| boundary.handler.handler_name() == "StateHandler")
        });
        if state_handler_prompt {
            return self
                .var_store
                .get(key)
                .cloned()
                .or_else(|| missing_is_none.then_some(Value::None));
        }
        self.var_store
            .handler_state(prompt_seg_id)
            .and_then(|state| state.get(key))
            .cloned()
            .or_else(|| self.var_store.get(key).cloned())
            .or_else(|| missing_is_none.then_some(Value::None))
    }

    pub fn write_handler_state_at(
        &mut self,
        prompt_seg_id: SegmentId,
        key: String,
        value: Value,
    ) -> bool {
        let prompt_seg_id = self.shared_builtin_handler_prompt(prompt_seg_id);
        let Some(sync_global_state) = self.segments.get(prompt_seg_id).map(|seg| {
            seg.kind
                .prompt_boundary()
                .is_some_and(|boundary| boundary.handler.handler_name() == "StateHandler")
        }) else {
            return false;
        };

        let Some(state) = self.var_store.handler_state_mut(prompt_seg_id) else {
            return false;
        };
        state.insert(key.clone(), value.clone());
        if sync_global_state {
            self.var_store.put(key, value);
        }
        true
    }

    pub fn append_handler_log_at(&mut self, prompt_seg_id: SegmentId, message: Value) -> bool {
        let prompt_seg_id = self.shared_builtin_handler_prompt(prompt_seg_id);
        self.var_store.append_writer_log(prompt_seg_id, message)
    }

    fn shared_builtin_handler_prompt(&self, prompt_seg_id: SegmentId) -> SegmentId {
        if let Some(canonical_seg_id) = self
            .shared_builtin_prompt_cache
            .read()
            .expect("shared builtin prompt cache poisoned")
            .get(&prompt_seg_id)
            .copied()
        {
            return canonical_seg_id;
        }

        let Some(seg) = self.segments.get(prompt_seg_id) else {
            self.shared_builtin_prompt_cache
                .write()
                .expect("shared builtin prompt cache poisoned")
                .insert(prompt_seg_id, prompt_seg_id);
            return prompt_seg_id;
        };
        let Some(boundary) = seg.kind.prompt_boundary() else {
            self.shared_builtin_prompt_cache
                .write()
                .expect("shared builtin prompt cache poisoned")
                .insert(prompt_seg_id, prompt_seg_id);
            return prompt_seg_id;
        };
        let handler_name = boundary.handler.handler_name();
        if !matches!(handler_name.as_str(), "StateHandler" | "WriterHandler") {
            self.shared_builtin_prompt_cache
                .write()
                .expect("shared builtin prompt cache poisoned")
                .insert(prompt_seg_id, prompt_seg_id);
            return prompt_seg_id;
        }
        // Spawn/CreateContinuation may install wrapper prompts that sit between
        // the resumed task body and the original outer State/Writer handler.
        if self.parent_segment(prompt_seg_id) == seg.parent {
            self.shared_builtin_prompt_cache
                .write()
                .expect("shared builtin prompt cache poisoned")
                .insert(prompt_seg_id, prompt_seg_id);
            return prompt_seg_id;
        }

        let mut cursor = self.parent_segment(prompt_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let Some(boundary) = seg.kind.prompt_boundary() {
                if boundary.handler.handler_name() == handler_name {
                    self.shared_builtin_prompt_cache
                        .write()
                        .expect("shared builtin prompt cache poisoned")
                        .insert(prompt_seg_id, seg_id);
                    return seg_id;
                }
            }
            cursor = self.parent_segment(seg_id);
        }

        self.shared_builtin_prompt_cache
            .write()
            .expect("shared builtin prompt cache poisoned")
            .insert(prompt_seg_id, prompt_seg_id);
        prompt_seg_id
    }

    fn canonical_output_segment_id(&self, seg_id: SegmentId) -> SegmentId {
        let Some(seg) = self.segments.get(seg_id) else {
            return seg_id;
        };
        let Some(boundary) = seg.kind.prompt_boundary() else {
            return seg_id;
        };
        if matches!(
            boundary.handler.handler_name().as_str(),
            "StateHandler" | "WriterHandler"
        ) {
            return self.shared_builtin_handler_prompt(seg_id);
        }
        seg_id
    }

    fn state_output_entries(
        &self,
        seg_id: SegmentId,
    ) -> Option<(SegmentId, HashMap<String, Value>)> {
        let canonical_seg_id = self.canonical_output_segment_id(seg_id);
        let seg = self.segments.get(canonical_seg_id)?;
        match seg.kind.prompt_boundary() {
            Some(boundary) if boundary.handler.handler_name() == "StateHandler" => {
                let shared_state = self.var_store.global_state().clone();
                Some((canonical_seg_id, shared_state))
            }
            Some(_) | None => None,
        }
    }

    fn log_output_entries(&self, seg_id: SegmentId) -> Option<(SegmentId, Vec<Value>)> {
        let canonical_seg_id = self.canonical_output_segment_id(seg_id);
        let seg = self.segments.get(canonical_seg_id)?;
        match seg.kind.prompt_boundary() {
            Some(boundary) if boundary.handler.handler_name() == "WriterHandler" => {
                let shared_logs = self
                    .var_store
                    .writer_log(canonical_seg_id)
                    .cloned()
                    .unwrap_or_default();
                Some((canonical_seg_id, shared_logs))
            }
            Some(_) | None => None,
        }
    }

    pub fn final_state_entries(&self) -> HashMap<String, Value> {
        let mut chain = Vec::new();
        let mut cursor = self.completed_segment.or(self.current_segment);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            chain.push(seg_id);
            cursor = seg.parent;
        }
        chain.reverse();

        let mut state = HashMap::new();
        let mut seen_segments = HashSet::new();
        for seg_id in chain {
            if let Some((canonical_seg_id, shared_state)) = self.state_output_entries(seg_id) {
                if seen_segments.insert(canonical_seg_id) && !shared_state.is_empty() {
                    state.extend(shared_state);
                }
            }
        }

        if state.is_empty() {
            return self.var_store.global_state().clone();
        }
        state
    }

    pub fn final_log_entries(&self) -> Vec<Value> {
        let mut chain = Vec::new();
        let mut cursor = self.completed_segment.or(self.current_segment);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            chain.push(seg_id);
            cursor = seg.parent;
        }
        chain.reverse();

        let mut logs = Vec::new();
        let mut seen_segments = HashSet::new();
        for seg_id in chain {
            if let Some((canonical_seg_id, shared_logs)) = self.log_output_entries(seg_id) {
                if seen_segments.insert(canonical_seg_id) && !shared_logs.is_empty() {
                    logs.extend(shared_logs);
                }
            }
        }
        logs
    }

    fn nearest_auto_unwrap_programlike_metadata(&self) -> Option<CallMetadata> {
        let mut seg_id = self.current_segment;
        while let Some(id) = seg_id {
            let Some(seg) = self.segments.get(id) else {
                break;
            };
            for frame in seg.frames.iter().rev() {
                match frame {
                    Frame::Program {
                        metadata: Some(metadata),
                        ..
                    } if metadata.auto_unwrap_programlike => return Some(metadata.clone()),
                    Frame::LexicalScope { .. } => {}
                    Frame::Program { .. } => {}
                    Frame::EvalReturn(continuation) => {
                        if let Some(metadata) = continuation
                            .metadata()
                            .filter(|metadata| metadata.auto_unwrap_programlike)
                        {
                            return Some(metadata.clone());
                        }
                    }
                    Frame::MapReturn { .. }
                    | Frame::FlatMapBindResult
                    | Frame::FlatMapBindSource { .. } => {}
                }
            }
            seg_id = seg.parent;
        }
        None
    }

    fn pending_auto_unwrap_programlike_metadata(&self) -> Option<&CallMetadata> {
        let pending = self.pending_python.as_ref()?;
        match pending {
            PendingPython::EvalExpr { metadata }
            | PendingPython::ExpandReturn { metadata, .. }
            | PendingPython::StepUserGenerator { metadata, .. } => metadata
                .as_ref()
                .filter(|metadata| metadata.auto_unwrap_programlike),
            PendingPython::CallFuncReturn
            | PendingPython::RustProgramContinuation { .. }
            | PendingPython::AsyncEscape => None,
        }
    }

    pub fn has_nearby_auto_unwrap_programlike(&self) -> bool {
        self.pending_auto_unwrap_programlike_metadata().is_some()
            || self.nearest_auto_unwrap_programlike_metadata().is_some()
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
}

impl Default for VM {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
#[path = "vm_tests.rs"]
mod vm_tests;
