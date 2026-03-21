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
use crate::dispatch_observer::DispatchObserver;
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
};
use crate::ids::{ContId, DispatchId, Marker, ScopeId, SegmentId};
use crate::ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream};
use crate::kleisli::{IdentityKleisli, KleisliRef};
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::segment::{Segment, SegmentKind};
use crate::trace_state::{LiveDispatchSnapshot, TraceState};
use crate::var_store::VarStore;
use crate::value::Value;

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
    pub consumed_cont_ids: HashSet<ContId>,
    installed_handlers: Vec<InstalledHandler>,
    run_handlers: Vec<KleisliRef>,
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
    pub(crate) dispatch_observer: DispatchObserver,
    pub continuation_registry: HashMap<ContId, Continuation>,
    scope_parents: HashMap<SegmentId, Option<SegmentId>>,
    scope_state_store: HashMap<ScopeId, HashMap<String, Value>>,
    scope_writer_logs: HashMap<ScopeId, Vec<Value>>,
    completed_state_entries_snapshot: Option<HashMap<String, Value>>,
    completed_log_entries_snapshot: Option<Vec<Value>>,
    pub active_run_token: Option<u64>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: FiberArena::new(),
            consumed_cont_ids: HashSet::new(),
            installed_handlers: Vec::new(),
            run_handlers: Vec::new(),
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
            dispatch_observer: DispatchObserver::default(),
            continuation_registry: HashMap::new(),
            scope_parents: HashMap::new(),
            scope_state_store: HashMap::new(),
            scope_writer_logs: HashMap::new(),
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
        self.dispatch_observer.clear();
        self.run_handlers.clear();
        self.scope_parents.clear();
        self.scope_state_store.clear();
        self.scope_writer_logs.clear();
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

        for handler in &self.run_handlers {
            handler.on_run_end(run_token);
        }
        self.run_handlers.clear();
        self.continuation_registry.clear();
        self.consumed_cont_ids.clear();
        self.dispatch_observer.clear();
        self.var_store.clear();
        self.mode = Mode::Deliver(Value::Unit);
        self.pending_python = None;
        self.scope_parents.clear();
        self.scope_state_store.clear();
        self.scope_writer_logs.clear();
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
        self.register_segment_scope_state(&segment);
        let seg_id = self.segments.alloc(segment);
        self.var_store.init_segment(seg_id);
        let parent = self.segments.get(seg_id).and_then(|segment| segment.parent);
        self.scope_parents.insert(seg_id, parent);
        seg_id
    }

    pub fn free_segment(&mut self, id: SegmentId) {
        let Some(scope_id) = self.segments.get(id).map(|segment| segment.scope_id) else {
            return;
        };
        self.dispatch_observer.unbind_segment(id);
        self.segments.free(id);
        self.var_store.remove_segment(id);
        self.scope_parents.remove(&id);
        self.maybe_cleanup_scope_state(scope_id);
    }

    pub fn current_segment_mut(&mut self) -> Option<&mut Segment> {
        self.current_segment
            .and_then(|id| self.segments.get_mut(id))
    }

    pub fn current_segment_ref(&self) -> Option<&Segment> {
        self.current_segment.and_then(|id| self.segments.get(id))
    }

    pub(crate) fn continuation_segment_ref(
        &self,
        continuation: &Continuation,
    ) -> Option<&Segment> {
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
                    } => Some(Frame::Program {
                        stream: stream.clone(),
                        metadata: metadata.clone(),
                        handler_kind: *handler_kind,
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
        self.continuation_segment_ref(continuation)
            .and_then(|segment| segment.pending_error_context.as_ref())
    }

    pub fn scope_parent(&self, seg_id: SegmentId) -> Option<SegmentId> {
        self.scope_parents.get(&seg_id).copied().flatten()
    }

    pub fn set_scope_parent(&mut self, seg_id: SegmentId, scope_parent: Option<SegmentId>) {
        self.scope_parents.insert(seg_id, scope_parent);
    }

    pub fn reparent_children(
        &mut self,
        old_parent: SegmentId,
        new_parent: Option<SegmentId>,
        new_scope_parent: Option<SegmentId>,
    ) -> usize {
        let mut rewired = self.segments.reparent_children(old_parent, new_parent);
        for scope_parent in self.scope_parents.values_mut() {
            if *scope_parent == Some(old_parent) {
                *scope_parent = new_scope_parent;
                rewired += 1;
            }
        }
        rewired
    }

    fn register_segment_scope_state(&mut self, segment: &Segment) {
        self.scope_state_store
            .entry(segment.scope_id)
            .or_insert_with(|| segment.state_store.clone());
        self.scope_writer_logs
            .entry(segment.scope_id)
            .or_insert_with(|| segment.writer_log.clone());
    }

    fn maybe_cleanup_scope_state(&mut self, scope_id: ScopeId) {
        if self.scope_is_still_referenced(scope_id) {
            return;
        }
        self.scope_state_store.remove(&scope_id);
        self.scope_writer_logs.remove(&scope_id);
    }

    fn scope_is_still_referenced(&self, scope_id: ScopeId) -> bool {
        // TODO(vm-shared-handlers): This scans live segments and registered
        // continuations on every segment free. Replace it with refcount-style
        // scope tracking if cleanup cost becomes visible in long-running VMs.
        let mut visited = HashSet::new();
        self.segments
            .iter()
            .any(|(_, segment)| self.segment_references_scope(segment, scope_id, &mut visited))
            || self.continuation_registry.values().any(|continuation| {
                self.continuation_references_scope(continuation, scope_id, &mut visited)
            })
            || self.dispatch_observer.iter().any(|(_, dispatch)| {
                self.continuation_references_scope(&dispatch.k_origin, scope_id, &mut visited)
                    || self.continuation_references_scope(
                        &dispatch.active_handler.continuation,
                        scope_id,
                        &mut visited,
                    )
            })
    }

    fn segment_references_scope(
        &self,
        segment: &Segment,
        scope_id: ScopeId,
        visited: &mut HashSet<ContId>,
    ) -> bool {
        if segment.scope_id == scope_id {
            return true;
        }
        if segment.throw_parent.as_ref().is_some_and(|continuation| {
            self.continuation_references_scope(continuation, scope_id, visited)
        }) {
            return true;
        }
        for frame in &segment.frames {
            let referenced = match frame {
                Frame::EvalReturn(eval_return) => {
                    self.eval_return_references_scope(eval_return.as_ref(), scope_id, visited)
                }
                Frame::Program { .. }
                | Frame::InterceptorApply(_)
                | Frame::InterceptorEval(_)
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. }
                | Frame::InterceptBodyReturn { .. } => false,
            };
            if referenced {
                return true;
            }
        }
        false
    }

    fn continuation_references_scope(
        &self,
        continuation: &Continuation,
        scope_id: ScopeId,
        visited: &mut HashSet<ContId>,
    ) -> bool {
        if !visited.insert(continuation.cont_id) {
            return false;
        }
        if continuation.fibers().iter().any(|fiber_id| {
            self.segments
                .get(*fiber_id)
                .is_some_and(|segment| self.segment_references_scope(segment, scope_id, visited))
        }) {
            return true;
        }
        false
    }

    fn eval_return_references_scope(
        &self,
        eval_return: &EvalReturnContinuation,
        scope_id: ScopeId,
        visited: &mut HashSet<ContId>,
    ) -> bool {
        match eval_return {
            EvalReturnContinuation::ResumeToContinuation { continuation }
            | EvalReturnContinuation::EvalInScopeReturn { continuation }
            | EvalReturnContinuation::ReturnToContinuation { continuation } => {
                self.continuation_references_scope(continuation, scope_id, visited)
            }
            _ => false,
        }
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
        for seg_id in chain {
            let Some(seg) = self.segments.get(seg_id) else {
                continue;
            };
            if !seg.state_store.is_empty() {
                state.extend(seg.state_store.clone());
            }
            if !seg.writer_log.is_empty() {
                logs.extend(seg.writer_log.clone());
            }
        }

        (state, logs)
    }

    pub(crate) fn store_completed_outputs_from(&mut self, start_seg_id: SegmentId) {
        let (state, logs) = self.collect_outputs_from_chain(start_seg_id);
        self.completed_state_entries_snapshot = Some(state);
        self.completed_log_entries_snapshot = Some(logs);
    }

    pub fn read_handler_state_at(
        &self,
        prompt_seg_id: SegmentId,
        key: &str,
        missing_is_none: bool,
    ) -> Option<Value> {
        self.segments.get(prompt_seg_id).and_then(|seg| {
            seg.state_store
                .get(key)
                .cloned()
                .or_else(|| missing_is_none.then_some(Value::None))
        })
    }

    pub fn write_handler_state_at(
        &mut self,
        prompt_seg_id: SegmentId,
        key: String,
        value: Value,
    ) -> bool {
        let Some((scope_id, sync_rust_store)) = self.segments.get(prompt_seg_id).map(|seg| {
            (
                seg.scope_id,
                matches!(
                    &seg.kind,
                    SegmentKind::PromptBoundary { handler, .. } if handler.handler_name() == "StateHandler"
                ),
            )
        }) else {
            return false;
        };

        self.scope_state_store
            .entry(scope_id)
            .or_default()
            .insert(key.clone(), value.clone());
        let Some(seg) = self.segments.get_mut(prompt_seg_id) else {
            return false;
        };
        seg.state_store.insert(key.clone(), value.clone());
        if sync_rust_store {
            self.rust_store.entries.insert(key, value);
        }
        true
    }

    pub fn append_handler_log_at(&mut self, prompt_seg_id: SegmentId, message: Value) -> bool {
        let Some(scope_id) = self.segments.get(prompt_seg_id).map(|seg| seg.scope_id) else {
            return false;
        };
        self.scope_writer_logs
            .entry(scope_id)
            .or_default()
            .push(message.clone());
        let Some(seg) = self.segments.get_mut(prompt_seg_id) else {
            return false;
        };
        seg.writer_log.push(message);
        true
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
        for seg_id in chain {
            let Some(seg) = self.segments.get(seg_id) else {
                continue;
            };
            if !seg.state_store.is_empty() {
                state.extend(seg.state_store.clone());
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
        for seg_id in chain {
            let Some(seg) = self.segments.get(seg_id) else {
                continue;
            };
            if !seg.writer_log.is_empty() {
                logs.extend(seg.writer_log.clone());
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
