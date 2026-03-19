//! Core VM struct and step execution.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use pyo3::exceptions::{PyBaseException, PyException as PyStdException};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule, PyTuple};

use crate::arena::SegmentArena;
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
};
use crate::ids::{ContId, DispatchId, Marker, ScopeId, SegmentId, VarId};
use crate::ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream};
use crate::kleisli::{IdentityKleisli, KleisliRef};
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::python_call::{PendingPython, PyCallOutcome, PythonCall};
use crate::segment::{Segment, SegmentKind};
use crate::trace_state::{LiveDispatchSnapshot, TraceState};
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
    pub segments: SegmentArena,
    pub consumed_cont_ids: HashSet<ContId>,
    installed_handlers: Vec<InstalledHandler>,
    run_handlers: Vec<KleisliRef>,
    pub rust_store: RustStore,
    pub env_store: HashMap<HashedPyKey, Value>,
    pub py_store: Option<PyStore>,
    pub current_segment: Option<SegmentId>,
    pub completed_segment: Option<SegmentId>,
    pub(crate) debug: DebugState,
    pub(crate) trace_state: TraceState,
    pub continuation_registry: HashMap<ContId, Continuation>,
    pub scope_variables: HashMap<ScopeId, HashMap<VarId, Value>>,
    scope_state_store: HashMap<ScopeId, HashMap<String, Value>>,
    scope_writer_logs: HashMap<ScopeId, Vec<Value>>,
    scope_persistent_epochs: HashMap<ScopeId, u64>,
    completed_state_entries_snapshot: Option<HashMap<String, Value>>,
    completed_log_entries_snapshot: Option<Vec<Value>>,
    pub active_run_token: Option<u64>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: SegmentArena::new(),
            consumed_cont_ids: HashSet::new(),
            installed_handlers: Vec::new(),
            run_handlers: Vec::new(),
            rust_store: RustStore::new(),
            env_store: HashMap::new(),
            py_store: None,
            current_segment: None,
            completed_segment: None,
            debug: DebugState::new(DebugConfig::default()),
            trace_state: TraceState::default(),
            continuation_registry: HashMap::new(),
            scope_variables: HashMap::new(),
            scope_state_store: HashMap::new(),
            scope_writer_logs: HashMap::new(),
            scope_persistent_epochs: HashMap::new(),
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
        self.current_segment = None;
        self.completed_segment = None;
        self.trace_state.clear();
        self.run_handlers.clear();
        self.scope_variables.clear();
        self.scope_state_store.clear();
        self.scope_writer_logs.clear();
        self.scope_persistent_epochs.clear();
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
        self.scope_variables.clear();
        self.scope_state_store.clear();
        self.scope_writer_logs.clear();
        self.scope_persistent_epochs.clear();
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
        self.register_segment_persistent_state(&segment);
        self.segments.alloc(segment)
    }

    pub fn current_segment_mut(&mut self) -> Option<&mut Segment> {
        self.current_segment
            .and_then(|id| self.segments.get_mut(id))
    }

    pub fn current_segment_ref(&self) -> Option<&Segment> {
        self.current_segment.and_then(|id| self.segments.get(id))
    }

    fn register_segment_persistent_state(&mut self, segment: &Segment) {
        self.scope_state_store
            .entry(segment.scope_id)
            .or_insert_with(|| segment.state_store.clone());
        self.scope_writer_logs
            .entry(segment.scope_id)
            .or_insert_with(|| segment.writer_log.clone());
        self.scope_persistent_epochs
            .entry(segment.scope_id)
            .or_insert(segment.persistent_epoch);
    }

    fn bump_scope_persistent_epoch(&mut self, scope_id: ScopeId) -> u64 {
        let epoch = self.scope_persistent_epochs.entry(scope_id).or_insert(0);
        *epoch += 1;
        *epoch
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
            cursor = seg.caller;
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

    fn collect_outputs_from_persistent_scopes(&self) -> (HashMap<String, Value>, Vec<Value>) {
        let mut scopes: Vec<(ScopeId, u64)> = self
            .scope_persistent_epochs
            .iter()
            .map(|(scope_id, epoch)| (*scope_id, *epoch))
            .collect();
        scopes.sort_by_key(|(scope_id, epoch)| (*epoch, scope_id.raw()));

        let mut state = HashMap::new();
        let mut logs = Vec::new();
        for (scope_id, _) in scopes {
            if let Some(scope_state) = self.scope_state_store.get(&scope_id) {
                if !scope_state.is_empty() {
                    state.extend(scope_state.clone());
                }
            }
            if let Some(scope_logs) = self.scope_writer_logs.get(&scope_id) {
                if !scope_logs.is_empty() {
                    logs.extend(scope_logs.clone());
                }
            }
        }

        (state, logs)
    }

    pub(crate) fn store_completed_outputs_from(&mut self, start_seg_id: SegmentId) {
        let (mut state, mut logs) = self.collect_outputs_from_chain(start_seg_id);
        let (persistent_state, persistent_logs) = self.collect_outputs_from_persistent_scopes();
        if state.is_empty() && !persistent_state.is_empty() {
            state = persistent_state;
        }
        if logs.is_empty() && !persistent_logs.is_empty() {
            logs = persistent_logs;
        }
        self.completed_state_entries_snapshot = Some(state);
        self.completed_log_entries_snapshot = Some(logs);
    }

    pub fn alloc_scoped_var_in_segment(&mut self, seg_id: SegmentId, initial: Value) -> VarId {
        let scope_id = self
            .segments
            .get(seg_id)
            .expect("alloc_scoped_var_in_segment requires a live segment")
            .scope_id;
        let var = VarId::fresh(scope_id);
        self.scope_variables
            .entry(scope_id)
            .or_default()
            .insert(var, initial.clone());
        self.segments
            .get_mut(seg_id)
            .expect("alloc_scoped_var_in_segment requires a mutable segment")
            .variables
            .insert(var, initial);
        var
    }

    pub fn read_scoped_var_from(&self, start_seg_id: SegmentId, var: VarId) -> Option<Value> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let Some(value) = self
                .scope_variables
                .get(&seg.scope_id)
                .and_then(|vars| vars.get(&var))
            {
                return Some(value.clone());
            }
            cursor = seg.scope_parent;
        }
        None
    }

    pub fn write_scoped_var_in_current_segment(
        &mut self,
        seg_id: SegmentId,
        var: VarId,
        value: Value,
    ) -> bool {
        let Some(seg) = self.segments.get_mut(seg_id) else {
            return false;
        };
        self.scope_variables
            .entry(seg.scope_id)
            .or_default()
            .insert(var, value.clone());
        seg.variables.insert(var, value);
        true
    }

    pub fn write_scoped_var_nonlocal(
        &mut self,
        start_seg_id: SegmentId,
        var: VarId,
        value: Value,
    ) -> bool {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                return false;
            };
            if seg.scope_id == var.owner_scope() {
                self.scope_variables
                    .entry(seg.scope_id)
                    .or_default()
                    .insert(var, value.clone());
                if let Some(target) = self.segments.get_mut(seg_id) {
                    target.variables.insert(var, value);
                    return true;
                }
                return false;
            }
            cursor = seg.scope_parent;
        }
        false
    }

    pub fn read_scope_binding_from(
        &self,
        start_seg_id: SegmentId,
        key: &HashedPyKey,
    ) -> Option<Value> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let Some(value) = seg.named_bindings.get(key) {
                return Some(value.clone());
            }
            cursor = seg.scope_parent;
        }
        self.env_store.get(key).cloned()
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

        let epoch = self.bump_scope_persistent_epoch(scope_id);
        self.scope_state_store
            .entry(scope_id)
            .or_default()
            .insert(key.clone(), value.clone());
        let Some(seg) = self.segments.get_mut(prompt_seg_id) else {
            return false;
        };
        seg.persistent_epoch = epoch;
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
        let epoch = self.bump_scope_persistent_epoch(scope_id);
        self.scope_writer_logs
            .entry(scope_id)
            .or_default()
            .push(message.clone());
        let Some(seg) = self.segments.get_mut(prompt_seg_id) else {
            return false;
        };
        seg.persistent_epoch = epoch;
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
            cursor = seg.caller;
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
            cursor = seg.caller;
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
                        EvalReturnContinuation::EvalInScopeReturn { .. } => {}
                        _ => {}
                    },
                    Frame::HandlerDispatch { .. }
                    | Frame::DispatchOrigin { .. }
                    | Frame::MapReturn { .. }
                    | Frame::FlatMapBindResult
                    | Frame::FlatMapBindSource { .. }
                    | Frame::InterceptBodyReturn { .. } => {}
                }
            }
            seg_id = seg.caller;
        }
        None
    }

    fn pending_auto_unwrap_programlike_metadata(&self) -> Option<&CallMetadata> {
        let pending = self.current_segment_ref()?.pending_python.as_ref()?;
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
