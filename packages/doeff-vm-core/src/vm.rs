//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use pyo3::exceptions::{PyBaseException, PyException as PyStdException};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule, PyTuple};

use crate::arena::FiberArena;
use crate::bridge::{classify_yielded_for_vm, doctrl_tag, doctrl_to_pyexpr_for_vm};
use crate::capture::{
    ActiveChainEntry, HandlerAction, HandlerKind, HandlerSnapshotEntry, TraceEntry,
};
use crate::continuation::Continuation;
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
    CallMetadata, EvalReturnContinuation, Frame, InterceptorChainLink, InterceptorContinuation,
    ProgramDispatch,
};
use crate::ids::{ContId, DispatchId, Marker, ScopeId, SegmentId};
use crate::ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream};
use crate::kleisli::{notify_run_handlers_completed, IdentityKleisli, KleisliRef};
use crate::memory_stats::live_object_counts;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::segment::{Segment, SegmentKind};
use crate::trace_state::{LiveDispatchSnapshot, TraceState};
use crate::value::Value;
use crate::var_store::VarStore;

pub use crate::rust_store::RustStore;

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

#[derive(Debug, Default, Clone)]
struct FiberRuntimeState {
    pending_error_context: Option<PyException>,
    throw_parent: Option<Continuation>,
    interceptor_eval_depth: usize,
    interceptor_skip_stack: Vec<Marker>,
    pending_program_dispatch: Option<ProgramDispatch>,
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
    types: Option<Vec<PyShared>>,
}

#[derive(Clone)]
struct WithHandlerPlan {
    handler_marker: Marker,
    outside_seg_id: SegmentId,
    handler: KleisliRef,
}

#[derive(Clone)]
struct DispatchOriginView {
    dispatch_id: DispatchId,
    effect: DispatchEffect,
    k_origin: Continuation,
    original_exception: Option<PyException>,
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
    pub rust_store: RustStore,
    pub var_store: VarStore,
    pub env_store: HashMap<HashedPyKey, Value>,
    pub py_store: Option<PyStore>,
    pub mode: Mode,
    pub pending_python: Option<PendingPython>,
    pub current_segment: Option<SegmentId>,
    pub completed_segment: Option<SegmentId>,
    pub(crate) debug: DebugState,
    pub(crate) trace_state: TraceState,
    fiber_runtime: HashMap<SegmentId, FiberRuntimeState>,
    scope_ids: HashMap<SegmentId, ScopeId>,
    scope_parents: HashMap<SegmentId, Option<SegmentId>>,
    segment_parent_redirects: HashMap<SegmentId, Option<SegmentId>>,
    completed_state_entries_snapshot: Option<HashMap<String, Value>>,
    completed_log_entries_snapshot: Option<Vec<Value>>,
    pub active_run_token: Option<u64>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: FiberArena::new(),
            rust_store: RustStore::new(),
            var_store: VarStore::default(),
            env_store: HashMap::new(),
            py_store: None,
            mode: Mode::Deliver(Value::Unit),
            pending_python: None,
            current_segment: None,
            completed_segment: None,
            debug: DebugState::new(DebugConfig::default()),
            trace_state: TraceState::default(),
            fiber_runtime: HashMap::new(),
            scope_ids: HashMap::new(),
            scope_parents: HashMap::new(),
            segment_parent_redirects: HashMap::new(),
            completed_state_entries_snapshot: None,
            completed_log_entries_snapshot: None,
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
        self.var_store.clear();
        self.mode = Mode::Deliver(Value::Unit);
        self.pending_python = None;
        self.current_segment = None;
        self.completed_segment = None;
        self.trace_state.clear();
        self.fiber_runtime.clear();
        self.scope_ids.clear();
        self.scope_parents.clear();
        self.segment_parent_redirects.clear();
        self.completed_state_entries_snapshot = None;
        self.completed_log_entries_snapshot = None;
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
        self.var_store.clear();
        self.var_store.shrink_to_fit();
        self.mode = Mode::Deliver(Value::Unit);
        self.pending_python = None;
        self.current_segment = None;
        self.completed_segment = None;
        self.trace_state.clear();
        self.trace_state.shrink_to_fit();
        self.debug.shrink_to_fit();
        self.fiber_runtime.clear();
        self.fiber_runtime.shrink_to_fit();
        self.scope_ids.clear();
        self.scope_ids.shrink_to_fit();
        self.scope_parents.clear();
        self.segment_parent_redirects.clear();
        self.scope_parents.shrink_to_fit();
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
                    dispatch_ids.insert(dispatch.dispatch_id);
                }
            }
        }
        for state in self.fiber_runtime.values() {
            if let Some(dispatch) = &state.pending_program_dispatch {
                dispatch_ids.insert(dispatch.dispatch_id);
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
                        .fiber_runtime(*seg_id)
                        .and_then(|state| state.pending_program_dispatch.as_ref())
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
        self.segment_parent_redirects.remove(&seg_id);
        self.var_store.init_segment(seg_id);
        self.fiber_runtime
            .insert(seg_id, FiberRuntimeState::default());
        self.scope_ids.insert(seg_id, ScopeId::fresh());
        let parent = self.segments.get(seg_id).and_then(|segment| segment.parent);
        self.scope_parents.insert(seg_id, parent);
        seg_id
    }

    pub fn free_segment(&mut self, id: SegmentId) {
        let Some(parent) = self.segments.get(id).map(|segment| segment.parent) else {
            return;
        };
        self.segment_parent_redirects.insert(id, parent);
        self.segments.free(id);
        self.var_store.remove_segment(id);
        self.fiber_runtime.remove(&id);
        self.scope_ids.remove(&id);
        self.scope_parents.remove(&id);
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

    pub(crate) fn continuation_frame_stack(&self, continuation: &Continuation) -> Vec<Frame> {
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
                    } => Some(Frame::Program {
                        stream: stream.clone(),
                        metadata: metadata.clone(),
                        handler_kind: *handler_kind,
                        dispatch: dispatch.clone(),
                    }),
                    Frame::InterceptorApply(_)
                    | Frame::InterceptorEval(_)
                    | Frame::EvalReturn(_)
                    | Frame::MapReturn { .. }
                    | Frame::FlatMapBindResult
                    | Frame::FlatMapBindSource { .. }
                    | Frame::InterceptBodyReturn { .. } => None,
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
            .and_then(|seg_id| self.fiber_runtime.get(&seg_id))
            .and_then(|state| state.pending_error_context.as_ref())
    }

    pub(crate) fn segment_program_dispatch(&self, seg_id: SegmentId) -> Option<&ProgramDispatch> {
        let segment = self.segments.get(seg_id)?;
        segment.frames.iter().rev().find_map(|frame| match frame {
            Frame::Program {
                dispatch: Some(dispatch),
                ..
            } => Some(dispatch),
            Frame::Program { .. }
            | Frame::InterceptorApply(_)
            | Frame::InterceptorEval(_)
            | Frame::EvalReturn(_)
            | Frame::MapReturn { .. }
            | Frame::FlatMapBindResult
            | Frame::FlatMapBindSource { .. }
            | Frame::InterceptBodyReturn { .. } => None,
        })
    }

    pub(crate) fn segment_program_dispatch_mut(
        &mut self,
        seg_id: SegmentId,
    ) -> Option<&mut ProgramDispatch> {
        let segment = self.segments.get_mut(seg_id)?;
        segment.frames.iter_mut().rev().find_map(|frame| match frame {
            Frame::Program {
                dispatch: Some(dispatch),
                ..
            } => Some(dispatch),
            Frame::Program { .. }
            | Frame::InterceptorApply(_)
            | Frame::InterceptorEval(_)
            | Frame::EvalReturn(_)
            | Frame::MapReturn { .. }
            | Frame::FlatMapBindResult
            | Frame::FlatMapBindSource { .. }
            | Frame::InterceptBodyReturn { .. } => None,
        })
    }

    pub(crate) fn set_pending_program_dispatch(
        &mut self,
        seg_id: SegmentId,
        dispatch: ProgramDispatch,
    ) {
        if let Some(runtime) = self.fiber_runtime_mut(seg_id) {
            runtime.pending_program_dispatch = Some(dispatch);
        }
    }

    fn take_pending_program_dispatch(&mut self, seg_id: SegmentId) -> Option<ProgramDispatch> {
        self.fiber_runtime_mut(seg_id)
            .and_then(|runtime| runtime.pending_program_dispatch.take())
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

    pub fn scope_parent(&self, seg_id: SegmentId) -> Option<SegmentId> {
        self.scope_parents.get(&seg_id).copied().flatten()
    }

    fn fiber_runtime(&self, seg_id: SegmentId) -> Option<&FiberRuntimeState> {
        self.fiber_runtime.get(&seg_id)
    }

    fn fiber_runtime_mut(&mut self, seg_id: SegmentId) -> Option<&mut FiberRuntimeState> {
        self.fiber_runtime.get_mut(&seg_id)
    }

    pub(crate) fn scope_id_for_segment(&self, seg_id: SegmentId) -> Option<ScopeId> {
        self.scope_ids.get(&seg_id).copied()
    }

    pub(crate) fn clear_pending_error_context(&mut self, seg_id: SegmentId) {
        if let Some(state) = self.fiber_runtime_mut(seg_id) {
            state.pending_error_context = None;
        }
    }

    pub(crate) fn set_pending_error_context(&mut self, seg_id: SegmentId, exception: PyException) {
        if let Some(state) = self.fiber_runtime_mut(seg_id) {
            state.pending_error_context = Some(exception);
        }
    }

    pub(crate) fn pending_error_context(&self, seg_id: SegmentId) -> Option<&PyException> {
        self.fiber_runtime(seg_id)
            .and_then(|state| state.pending_error_context.as_ref())
    }

    pub(crate) fn throw_parent(&self, seg_id: SegmentId) -> Option<&Continuation> {
        self.fiber_runtime(seg_id)
            .and_then(|state| state.throw_parent.as_ref())
    }

    pub(crate) fn clear_throw_parent(&mut self, seg_id: SegmentId) {
        if let Some(state) = self.fiber_runtime_mut(seg_id) {
            state.throw_parent = None;
        }
    }

    pub(crate) fn interceptor_eval_depth(&self, seg_id: SegmentId) -> usize {
        self.fiber_runtime(seg_id)
            .map(|state| state.interceptor_eval_depth)
            .unwrap_or(0)
    }

    pub(crate) fn increment_interceptor_eval_depth(&mut self, seg_id: SegmentId) {
        if let Some(state) = self.fiber_runtime_mut(seg_id) {
            state.interceptor_eval_depth = state.interceptor_eval_depth.saturating_add(1);
        }
    }

    pub(crate) fn decrement_interceptor_eval_depth(&mut self, seg_id: SegmentId) {
        if let Some(state) = self.fiber_runtime_mut(seg_id) {
            state.interceptor_eval_depth = state.interceptor_eval_depth.saturating_sub(1);
        }
    }

    pub(crate) fn is_interceptor_skipped_on(&self, seg_id: SegmentId, marker: Marker) -> bool {
        self.fiber_runtime(seg_id)
            .is_some_and(|state| state.interceptor_skip_stack.contains(&marker))
    }

    pub(crate) fn push_interceptor_skip_on(&mut self, seg_id: SegmentId, marker: Marker) {
        if let Some(state) = self.fiber_runtime_mut(seg_id) {
            state.interceptor_skip_stack.push(marker);
        }
    }

    pub(crate) fn pop_interceptor_skip_on(&mut self, seg_id: SegmentId, marker: Marker) {
        let Some(state) = self.fiber_runtime_mut(seg_id) else {
            return;
        };
        if let Some(pos) = state
            .interceptor_skip_stack
            .iter()
            .rposition(|active| *active == marker)
        {
            state.interceptor_skip_stack.remove(pos);
        }
    }

    pub(crate) fn interceptor_skip_stack_is_empty(&self, seg_id: SegmentId) -> bool {
        self.fiber_runtime(seg_id)
            .map_or(true, |state| state.interceptor_skip_stack.is_empty())
    }

    pub(crate) fn inherit_interceptor_guard_state(
        &mut self,
        source_seg_id: Option<SegmentId>,
        child_seg_id: SegmentId,
    ) {
        let Some(source_seg_id) = source_seg_id else {
            return;
        };
        let Some(source_state) = self.fiber_runtime(source_seg_id).cloned() else {
            return;
        };
        let Some(child_state) = self.fiber_runtime_mut(child_seg_id) else {
            return;
        };
        child_state.interceptor_eval_depth = source_state.interceptor_eval_depth;
        child_state.interceptor_skip_stack = source_state.interceptor_skip_stack;
    }

    pub fn set_scope_parent(&mut self, seg_id: SegmentId, scope_parent: Option<SegmentId>) {
        self.scope_parents.insert(seg_id, scope_parent);
    }

    fn reparent_continuation_captured_caller(
        continuation: &mut Continuation,
        old_parent: SegmentId,
        new_parent: Option<SegmentId>,
    ) -> usize {
        if continuation.captured_caller() != Some(old_parent) {
            return 0;
        }
        continuation.set_captured_caller(new_parent);
        1
    }

    fn reparent_eval_return_captured_caller(
        eval_return: &mut EvalReturnContinuation,
        old_parent: SegmentId,
        new_parent: Option<SegmentId>,
    ) -> usize {
        match eval_return {
            EvalReturnContinuation::ResumeToContinuation { continuation }
            | EvalReturnContinuation::ReturnToContinuation { continuation }
            | EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                Self::reparent_continuation_captured_caller(continuation, old_parent, new_parent)
            }
            EvalReturnContinuation::ApplyResolveFunction { .. }
            | EvalReturnContinuation::ApplyResolveArg { .. }
            | EvalReturnContinuation::ApplyResolveKwarg { .. }
            | EvalReturnContinuation::ExpandResolveFactory { .. }
            | EvalReturnContinuation::ExpandResolveArg { .. }
            | EvalReturnContinuation::ExpandResolveKwarg { .. }
            | EvalReturnContinuation::TailResumeReturn => 0,
        }
    }

    fn reparent_owned_continuation_callers(
        &mut self,
        old_parent: SegmentId,
        new_parent: Option<SegmentId>,
    ) -> usize {
        let mut rewired = 0usize;

        for (_, segment) in self.segments.iter_mut() {
            for frame in &mut segment.frames {
                if let Frame::Program {
                    dispatch: Some(dispatch),
                    ..
                } = frame
                {
                    rewired += Self::reparent_continuation_captured_caller(
                        &mut dispatch.origin,
                        old_parent,
                        new_parent,
                    );
                    rewired += Self::reparent_continuation_captured_caller(
                        &mut dispatch.handler_continuation,
                        old_parent,
                        new_parent,
                    );
                }
                if let Frame::EvalReturn(eval_return) = frame {
                    rewired += Self::reparent_eval_return_captured_caller(
                        eval_return,
                        old_parent,
                        new_parent,
                    );
                }
            }
        }

        if let Some(PendingPython::RustProgramContinuation { k, .. }) = self.pending_python.as_mut()
        {
            rewired += Self::reparent_continuation_captured_caller(k, old_parent, new_parent);
        }

        rewired
    }

    pub(crate) fn normalize_live_parent_hint(
        &self,
        parent: Option<SegmentId>,
    ) -> Option<SegmentId> {
        let mut cursor = parent;
        let mut seen = HashSet::new();
        while let Some(seg_id) = cursor {
            if !seen.insert(seg_id) {
                return None;
            }
            if let Some(next) = self
                .segment_parent_redirects
                .get(&seg_id)
                .copied()
                .flatten()
            {
                cursor = Some(next);
                continue;
            }
            if self.segments.get(seg_id).is_some() {
                return Some(seg_id);
            }
            cursor = None;
        }
        None
    }

    pub fn reparent_children(
        &mut self,
        old_parent: SegmentId,
        new_parent: Option<SegmentId>,
        new_scope_parent: Option<SegmentId>,
    ) -> usize {
        let mut rewired = self.segments.reparent_children(old_parent, new_parent);
        if rewired > 0 {
            self.segment_parent_redirects.insert(old_parent, new_parent);
        }
        for scope_parent in self.scope_parents.values_mut() {
            if *scope_parent == Some(old_parent) {
                *scope_parent = new_scope_parent;
                rewired += 1;
            }
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

    pub(crate) fn store_completed_outputs_from(&mut self, start_seg_id: SegmentId) {
        let (state, logs) = self.collect_outputs_from_chain(start_seg_id);
        if !state.is_empty() || self.completed_state_entries_snapshot.is_none() {
            self.completed_state_entries_snapshot = Some(state);
        }
        if !logs.is_empty() || self.completed_log_entries_snapshot.is_none() {
            self.completed_log_entries_snapshot = Some(logs);
        }
    }

    pub fn read_handler_state_at(
        &self,
        prompt_seg_id: SegmentId,
        key: &str,
        missing_is_none: bool,
    ) -> Option<Value> {
        let prompt_seg_id = self.shared_builtin_handler_prompt(prompt_seg_id);
        self.var_store
            .handler_state(prompt_seg_id)
            .and_then(|state| state.get(key))
            .cloned()
            .or_else(|| missing_is_none.then_some(Value::None))
    }

    pub fn write_handler_state_at(
        &mut self,
        prompt_seg_id: SegmentId,
        key: String,
        value: Value,
    ) -> bool {
        let prompt_seg_id = self.shared_builtin_handler_prompt(prompt_seg_id);
        let Some(sync_rust_store) = self.segments.get(prompt_seg_id).map(|seg| {
            matches!(
                &seg.kind,
                SegmentKind::PromptBoundary { handler, .. } if handler.handler_name() == "StateHandler"
            )
        }) else {
            return false;
        };

        let Some(state) = self.var_store.handler_state_mut(prompt_seg_id) else {
            return false;
        };
        state.insert(key.clone(), value.clone());
        if sync_rust_store {
            self.rust_store.entries.insert(key, value);
        }
        true
    }

    pub fn append_handler_log_at(&mut self, prompt_seg_id: SegmentId, message: Value) -> bool {
        let prompt_seg_id = self.shared_builtin_handler_prompt(prompt_seg_id);
        self.var_store.append_writer_log(prompt_seg_id, message)
    }

    fn shared_builtin_handler_prompt(&self, prompt_seg_id: SegmentId) -> SegmentId {
        let Some(seg) = self.segments.get(prompt_seg_id) else {
            return prompt_seg_id;
        };
        let SegmentKind::PromptBoundary { handler, .. } = &seg.kind else {
            return prompt_seg_id;
        };
        let handler_name = handler.handler_name();
        if !matches!(handler_name.as_str(), "StateHandler" | "WriterHandler") {
            return prompt_seg_id;
        }
        // Spawn/CreateContinuation installs synthetic handler wrappers whose
        // lexical scope (`scope_parent`) points at the captured parent chain
        // instead of the wrapper's structural parent. Built-in State/Writer
        // must share the outer live handler instance across spawned tasks, so
        // redirect reads/writes/log appends to the first matching prompt in
        // that captured scope chain.
        if self.scope_parent(prompt_seg_id) == seg.parent {
            return prompt_seg_id;
        }

        let mut cursor = self.scope_parent(prompt_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let SegmentKind::PromptBoundary { handler, .. } = &seg.kind {
                if handler.handler_name() == handler_name {
                    return seg_id;
                }
            }
            cursor = self.scope_parent(seg_id);
        }

        prompt_seg_id
    }

    fn canonical_output_segment_id(&self, seg_id: SegmentId) -> SegmentId {
        let Some(seg) = self.segments.get(seg_id) else {
            return seg_id;
        };
        let SegmentKind::PromptBoundary { handler, .. } = &seg.kind else {
            return seg_id;
        };
        if matches!(
            handler.handler_name().as_str(),
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
        match &seg.kind {
            SegmentKind::PromptBoundary { handler, .. }
                if handler.handler_name() == "StateHandler" =>
            {
                let shared_state = self
                    .var_store
                    .handler_state(canonical_seg_id)
                    .cloned()
                    .unwrap_or_default();
                Some((canonical_seg_id, shared_state))
            }
            SegmentKind::PromptBoundary { .. }
            | SegmentKind::Normal { .. }
            | SegmentKind::InterceptorBoundary { .. }
            | SegmentKind::MaskBoundary { .. } => None,
        }
    }

    fn log_output_entries(&self, seg_id: SegmentId) -> Option<(SegmentId, Vec<Value>)> {
        let canonical_seg_id = self.canonical_output_segment_id(seg_id);
        let seg = self.segments.get(canonical_seg_id)?;
        match &seg.kind {
            SegmentKind::PromptBoundary { handler, .. }
                if handler.handler_name() == "WriterHandler" =>
            {
                let shared_logs = self
                    .var_store
                    .writer_log(canonical_seg_id)
                    .cloned()
                    .unwrap_or_default();
                Some((canonical_seg_id, shared_logs))
            }
            SegmentKind::PromptBoundary { .. }
            | SegmentKind::Normal { .. }
            | SegmentKind::InterceptorBoundary { .. }
            | SegmentKind::MaskBoundary { .. } => None,
        }
    }

    pub fn final_state_entries(&self) -> HashMap<String, Value> {
        if self.current_segment.is_none() {
            if let Some(state) = &self.completed_state_entries_snapshot {
                if state.is_empty() {
                    return self.rust_store.entries.clone();
                }
                return state.clone();
            }
        }

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
            return self.rust_store.entries.clone();
        }
        state
    }

    pub fn final_log_entries(&self) -> Vec<Value> {
        if self.current_segment.is_none() {
            if let Some(logs) = &self.completed_log_entries_snapshot {
                return logs.clone();
            }
        }

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
                    Frame::Program { .. } => {}
                    Frame::InterceptorApply(continuation)
                    | Frame::InterceptorEval(continuation) => {
                        if let Some(metadata) = continuation
                            .emitter_metadata
                            .as_ref()
                            .filter(|metadata| metadata.auto_unwrap_programlike)
                        {
                            return Some(metadata.clone());
                        }
                        if let Some(metadata) = continuation
                            .interceptor_metadata
                            .as_ref()
                            .filter(|metadata| metadata.auto_unwrap_programlike)
                        {
                            return Some(metadata.clone());
                        }
                    }
                    Frame::EvalReturn(continuation) => match continuation.as_ref() {
                        EvalReturnContinuation::ApplyResolveFunction { metadata, .. }
                        | EvalReturnContinuation::ApplyResolveArg { metadata, .. }
                        | EvalReturnContinuation::ApplyResolveKwarg { metadata, .. }
                        | EvalReturnContinuation::ExpandResolveFactory { metadata, .. }
                        | EvalReturnContinuation::ExpandResolveArg { metadata, .. }
                        | EvalReturnContinuation::ExpandResolveKwarg { metadata, .. }
                            if metadata.auto_unwrap_programlike =>
                        {
                            return Some(metadata.clone());
                        }
                        EvalReturnContinuation::ResumeToContinuation { .. } => {}
                        EvalReturnContinuation::ReturnToContinuation { .. } => {}
                        EvalReturnContinuation::EvalInScopeReturn { .. } => {}
                        _ => {}
                    },
                    Frame::MapReturn { .. }
                    | Frame::FlatMapBindResult
                    | Frame::FlatMapBindSource { .. }
                    | Frame::InterceptBodyReturn { .. } => {}
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
