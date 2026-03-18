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
use crate::do_ctrl::{ContinuationHandlerBase, DoCtrl, DoExprTag, InterceptMode, PyDoExprBase};
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
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream};
use crate::kleisli::{IdentityKleisli, KleisliRef};
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
    trace_info: Arc<HandlerSnapshotEntry>,
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
            consumed_cont_ids: HashSet::new(),
            installed_handlers: Vec::new(),
            run_handlers: Vec::new(),
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
