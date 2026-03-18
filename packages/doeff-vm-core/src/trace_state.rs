//! Trace and active-event state for VM decomposition.

use crate::arena::SegmentArena;
use crate::capture::{
    ActiveChainEntry, DelegationEntry, DispatchAction, EffectResult, FrameId, HandlerAction,
    HandlerDispatchEntry, HandlerKind, HandlerSnapshotEntry, HandlerStatus, TraceEntry, TraceFrame,
    TraceHop,
};
use crate::continuation::Continuation;
use crate::effect::{make_execution_context_object, PyExecutionContext};
use crate::frame::{CallMetadata, Frame};
use crate::ids::{DispatchId, SegmentId};
use crate::ir_stream::{IRStreamRef, StreamLocation};
use crate::step::PyException;
use crate::value::Value;
use pyo3::prelude::*;

const EXECUTION_CONTEXT_ATTR: &str = "doeff_execution_context";
const MISSING_UNKNOWN: &str = "[MISSING] <unknown>";
const MISSING_SUB_PROGRAM: &str = "[MISSING] <sub_program>";
const MISSING_TARGET: &str = "[MISSING] <target>";
const MISSING_EXCEPTION: &str = "[MISSING] <exception>";
const MISSING_EXCEPTION_TYPE: &str = "[MISSING] Exception";
const MISSING_NONE_REPR: &str = "[MISSING] None";

#[derive(Debug, Clone)]
pub(crate) struct LiveDispatchSnapshot {
    pub(crate) dispatch_id: DispatchId,
    pub(crate) continuation: Continuation,
}

#[derive(Debug, Clone)]
struct ActiveChainFrameState {
    frame_id: FrameId,
    function_name: String,
    source_file: String,
    source_line: u32,
    args_repr: Option<String>,
    sub_program_repr: String,
    handler_kind: Option<HandlerKind>,
    dispatch_display: Option<DispatchDisplayState>,
}

#[derive(Debug, Clone)]
struct DispatchDisplayState {
    dispatch_id: DispatchId,
    function_name: Option<String>,
    source_file: Option<String>,
    source_line: Option<u32>,
    effect_repr: String,
    is_execution_context_effect: bool,
    handler_stack: Vec<HandlerDispatchEntry>,
    transfer_target_repr: Option<String>,
    result: EffectResult,
}

#[derive(Debug, Clone)]
pub(crate) struct TraceState {
    frame_stack: Vec<ActiveChainFrameState>,
}

impl Default for TraceState {
    fn default() -> Self {
        Self {
            frame_stack: Vec::new(),
        }
    }
}

impl TraceState {
    pub(crate) fn dispatch_has_terminal_result(&self, dispatch_id: DispatchId) -> bool {
        self.dispatch_display(dispatch_id).is_some_and(|dispatch| {
            matches!(
                dispatch.result,
                EffectResult::Resumed { .. }
                    | EffectResult::Transferred { .. }
                    | EffectResult::Threw { .. }
            )
        })
    }

    pub(crate) fn clear(&mut self) {
        *self = Self::default();
    }

    fn dispatch_display(&self, dispatch_id: DispatchId) -> Option<&DispatchDisplayState> {
        self.frame_stack.iter().find_map(|frame| {
            frame
                .dispatch_display
                .as_ref()
                .filter(|display| display.dispatch_id == dispatch_id)
        })
    }

    fn dispatch_display_mut(
        frame_stack: &mut [ActiveChainFrameState],
        dispatch_id: DispatchId,
    ) -> Option<&mut DispatchDisplayState> {
        frame_stack.iter_mut().find_map(|frame| {
            frame
                .dispatch_display
                .as_mut()
                .filter(|display| display.dispatch_id == dispatch_id)
        })
    }

    pub(crate) fn record_frame_entered(
        &mut self,
        metadata: &CallMetadata,
        handler_kind: Option<HandlerKind>,
    ) {
        self.frame_stack.push(ActiveChainFrameState {
            frame_id: metadata.frame_id as FrameId,
            function_name: metadata.function_name.clone(),
            source_file: metadata.source_file.clone(),
            source_line: metadata.source_line,
            args_repr: metadata.args_repr.clone(),
            sub_program_repr: Self::program_call_repr(metadata)
                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
            handler_kind,
            dispatch_display: None,
        });
    }

    pub(crate) fn record_frame_exited(&mut self) {
        let _ = self.frame_stack.pop();
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn record_dispatch_started(
        &mut self,
        dispatch_id: DispatchId,
        effect_repr: String,
        is_execution_context_effect: bool,
        handler_chain_snapshot: &[HandlerSnapshotEntry],
        effect_frame_id: Option<FrameId>,
        effect_function_name: Option<String>,
        effect_source_file: Option<String>,
        effect_source_line: Option<u32>,
    ) {
        let dispatch_display = DispatchDisplayState {
            dispatch_id,
            function_name: effect_function_name,
            source_file: effect_source_file,
            source_line: effect_source_line,
            effect_repr,
            is_execution_context_effect,
            handler_stack: Self::handler_stack_from_snapshot(handler_chain_snapshot),
            transfer_target_repr: None,
            result: EffectResult::Active,
        };
        let Some(frame_id) = effect_frame_id else {
            // Internal effects such as execution-context enrichment can have no user-visible
            // owning frame, so there is nowhere to attach frame-local display state.
            return;
        };

        if let Some(frame) = self
            .frame_stack
            .iter_mut()
            .find(|frame| frame.frame_id == frame_id)
        {
            if let Some(line) = effect_source_line {
                frame.source_line = line;
            }
            frame.dispatch_display = Some(dispatch_display);
        }
    }

    pub(crate) fn record_delegated(
        &mut self,
        dispatch_id: DispatchId,
        from_handler_index: usize,
        to_handler_index: usize,
    ) {
        self.update_dispatch_display(dispatch_id, |dispatch| {
            if let Some(from_entry) = dispatch.handler_stack.get_mut(from_handler_index) {
                if from_entry.status == HandlerStatus::Active {
                    from_entry.status = HandlerStatus::Delegated;
                }
            }
            if let Some(to_entry) = dispatch.handler_stack.get_mut(to_handler_index) {
                to_entry.status = HandlerStatus::Active;
            }
        });
    }

    pub(crate) fn record_passed(
        &mut self,
        dispatch_id: DispatchId,
        from_handler_index: usize,
        to_handler_index: usize,
    ) {
        self.update_dispatch_display(dispatch_id, |dispatch| {
            if let Some(from_entry) = dispatch.handler_stack.get_mut(from_handler_index) {
                if from_entry.status == HandlerStatus::Active {
                    from_entry.status = HandlerStatus::Passed;
                }
            }
            if let Some(to_entry) = dispatch.handler_stack.get_mut(to_handler_index) {
                to_entry.status = HandlerStatus::Active;
            }
        });
    }

    pub(crate) fn record_handler_completed(
        &mut self,
        dispatch_id: DispatchId,
        handler_name: &str,
        handler_index: usize,
        action: &HandlerAction,
    ) {
        self.update_dispatch_display(dispatch_id, |dispatch| {
            let status = match action {
                HandlerAction::Resumed { .. } => HandlerStatus::Resumed,
                HandlerAction::Transferred { .. } => HandlerStatus::Transferred,
                HandlerAction::Returned { .. } => HandlerStatus::Returned,
                HandlerAction::Threw { .. } => HandlerStatus::Threw,
            };
            if let Some(target) = dispatch.handler_stack.get_mut(handler_index) {
                target.status = status;
            }

            dispatch.result = match action {
                HandlerAction::Resumed { value_repr } | HandlerAction::Returned { value_repr } => {
                    EffectResult::Resumed {
                        value_repr: value_repr
                            .clone()
                            .unwrap_or_else(|| MISSING_NONE_REPR.to_string()),
                    }
                }
                HandlerAction::Transferred { value_repr } => EffectResult::Transferred {
                    handler_name: handler_name.to_string(),
                    target_repr: dispatch
                        .transfer_target_repr
                        .clone()
                        .or_else(|| value_repr.clone())
                        .unwrap_or_else(|| MISSING_TARGET.to_string()),
                },
                HandlerAction::Threw { exception_repr } => EffectResult::Threw {
                    handler_name: handler_name.to_string(),
                    exception_repr: exception_repr
                        .clone()
                        .unwrap_or_else(|| MISSING_EXCEPTION.to_string()),
                },
            };
        });
    }

    pub(crate) fn record_transfer_target(
        &mut self,
        dispatch_id: DispatchId,
        resumed_function_name: &str,
        source_file: &str,
        source_line: u32,
    ) {
        let target_repr = format!("{resumed_function_name}() {source_file}:{source_line}");
        self.update_dispatch_display(dispatch_id, |dispatch| {
            dispatch.transfer_target_repr = Some(target_repr.clone());
            if let EffectResult::Transferred {
                target_repr: current_target,
                ..
            } = &mut dispatch.result
            {
                *current_target = target_repr.clone();
            }
        });
    }

    fn update_dispatch_display<F>(&mut self, dispatch_id: DispatchId, update: F)
    where
        F: FnOnce(&mut DispatchDisplayState),
    {
        if let Some(dispatch) = Self::dispatch_display_mut(&mut self.frame_stack, dispatch_id) {
            update(dispatch);
        }
    }

    pub(crate) fn stream_debug_location(stream: &IRStreamRef) -> Option<StreamLocation> {
        let guard = stream.lock().expect("IRStream lock poisoned");
        guard.debug_location()
    }

    fn resume_location_from_frames(frames: &[Frame]) -> Option<(String, String, u32)> {
        for frame in frames.iter().rev() {
            if let Frame::Program {
                stream,
                metadata: Some(metadata),
                ..
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

    pub(crate) fn continuation_resume_location(k: &Continuation) -> Option<(String, String, u32)> {
        Self::resume_location_from_frames(k.frames().unwrap_or(&[]))
    }

    fn is_internal_source_file(source_file: &str) -> bool {
        let normalized = source_file.replace('\\', "/").to_lowercase();
        normalized == "_effect_wrap" || normalized.contains("/doeff/")
    }

    pub(crate) fn effect_site_from_continuation(
        k: &Continuation,
    ) -> Option<(FrameId, String, String, u32)> {
        let mut fallback: Option<(FrameId, String, String, u32)> = None;

        for frame in k.frames().unwrap_or(&[]).iter().rev() {
            if let Frame::Program {
                stream,
                metadata: Some(metadata),
                ..
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

    fn program_call_repr(metadata: &CallMetadata) -> Option<String> {
        metadata.program_call.as_ref().map(|program_call| {
            Python::attach(|py| {
                program_call
                    .bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| MISSING_SUB_PROGRAM.to_string())
            })
        })
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

    pub(crate) fn set_exception_cause(effect_err: &PyException, cause: &PyException) {
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

    pub(crate) fn has_execution_context(exception: &PyException) -> bool {
        let PyException::Materialized { exc_value, .. } = exception else {
            return false;
        };

        Python::attach(|py| {
            exc_value
                .bind(py)
                .getattr(EXECUTION_CONTEXT_ATTR)
                .ok()
                .is_some_and(|ctx| !ctx.is_none())
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
        active_chain: Option<&[ActiveChainEntry]>,
    ) -> PyResult<Py<PyAny>> {
        let context = make_execution_context_object(py)?;
        let mut context_ref = context
            .bind(py)
            .extract::<PyRefMut<'_, PyExecutionContext>>()?;
        for entry in entries {
            context_ref.add(py, entry.clone_ref(py))?;
        }
        if let Some(active_chain) = active_chain {
            let active_chain_tuple = Value::active_chain_to_pytuple(py, active_chain)?;
            context_ref.set_active_chain(Some(active_chain_tuple.into_any().unbind()));
        }
        Ok(context)
    }

    fn materialize_exception(exception: &PyException) -> PyException {
        match exception {
            PyException::Materialized { .. } => exception.clone(),
            PyException::RuntimeError { .. } | PyException::TypeError { .. } => {
                Python::attach(|py| {
                    PyException::from(exception.to_pyerr(py)).with_metadata(exception.metadata())
                })
            }
        }
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

    pub(crate) fn ensure_execution_context(exception: PyException) -> PyException {
        let exception = Self::materialize_exception(&exception);
        if Self::has_execution_context(&exception) {
            return exception;
        }

        Python::attach(|py| {
            match make_execution_context_object(py) {
                Ok(context) => Self::attach_execution_context(&exception, &context),
                Err(err) => crate::vm_warn_log!(
                    "failed to create ExecutionContext while enriching exception context: {err}"
                ),
            }
            exception
        })
    }

    pub(crate) fn enrich_original_exception_with_context(
        original: PyException,
        context_value: Value,
        active_chain: Vec<ActiveChainEntry>,
    ) -> Result<PyException, PyException> {
        let original = Self::materialize_exception(&original);
        let Value::Python(new_context) = context_value else {
            return Ok(original);
        };

        Python::attach(|py| {
            let context_bound = new_context.bind(py);
            if !context_bound.is_instance_of::<PyExecutionContext>() {
                return Ok(original);
            }

            let mut merged_entries = Self::context_entries_from_context_obj(context_bound);
            let existing_entries = Self::context_entries_from_exception(&original);
            merged_entries.extend(existing_entries);

            let merged_context = match Self::build_execution_context_from_entries(
                py,
                &merged_entries,
                Some(&active_chain),
            ) {
                Ok(context) => context,
                Err(_) => return Ok(original),
            };

            Self::attach_execution_context(&original, &merged_context);
            Ok(original)
        })
    }

    fn exception_site(exception: &PyException) -> ActiveChainEntry {
        match exception {
            PyException::Materialized {
                exc_type: _exc_type,
                exc_value,
                exc_tb,
                ..
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

                if let Ok(do_module) = PyModule::import(py, "doeff.do") {
                    if let Ok(resolve_location) = do_module.getattr("resolve_exception_location") {
                        if let Ok(Some((resolved_function, resolved_file, resolved_line))) =
                            resolve_location
                                .call1((exc_value_bound.clone(),))
                                .and_then(|value| value.extract::<Option<(String, String, u32)>>())
                        {
                            function_name = resolved_function;
                            source_file = resolved_file;
                            source_line = resolved_line;
                        }
                    }
                }

                if source_line == 0 {
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
                }

                ActiveChainEntry::ExceptionSite {
                    function_name,
                    source_file,
                    source_line,
                    exception_type,
                    message,
                }
            }),
            PyException::RuntimeError { message, .. } => ActiveChainEntry::ExceptionSite {
                function_name: "<runtime>".to_string(),
                source_file: "<runtime>".to_string(),
                source_line: 0,
                exception_type: "RuntimeError".to_string(),
                message: message.clone(),
            },
            PyException::TypeError { message, .. } => ActiveChainEntry::ExceptionSite {
                function_name: "<runtime>".to_string(),
                source_file: "<runtime>".to_string(),
                source_line: 0,
                exception_type: "TypeError".to_string(),
                message: message.clone(),
            },
        }
    }

    pub(crate) fn assemble_active_chain(
        &self,
        exception: Option<&PyException>,
        segments: &SegmentArena,
        current_segment: Option<SegmentId>,
        dispatch_stack: &[LiveDispatchSnapshot],
    ) -> Vec<ActiveChainEntry> {
        let mut frame_stack = self.frame_stack.clone();
        self.merge_live_frame_state(&mut frame_stack, segments, current_segment, dispatch_stack);

        if let Some(exception) = exception {
            Self::finalize_unresolved_dispatches_as_threw(&mut frame_stack, exception);
        }

        let entries = self.entries_from_active_chain_parts(&frame_stack, dispatch_stack);
        let entries = Self::dedup_adjacent(entries);
        Self::inject_context(entries, exception)
    }

    pub(crate) fn assemble_traceback_entries(
        &self,
        exception: &PyException,
        segments: &SegmentArena,
        current_segment: Option<SegmentId>,
        dispatch_stack: &[LiveDispatchSnapshot],
    ) -> Vec<TraceEntry> {
        let mut frame_stack = self.frame_stack.clone();
        self.merge_live_frame_state(&mut frame_stack, segments, current_segment, dispatch_stack);
        Self::finalize_unresolved_dispatches_as_threw(&mut frame_stack, exception);

        let mut entries = Vec::new();
        for frame in &frame_stack {
            entries.push(TraceEntry::Frame {
                frame_id: frame.frame_id,
                function_name: frame.function_name.clone(),
                source_file: frame.source_file.clone(),
                source_line: frame.source_line,
                args_repr: frame.args_repr.clone(),
            });
        }

        for frame in &frame_stack {
            let Some(dispatch) = frame.dispatch_display.as_ref() else {
                continue;
            };
            if !Self::is_visible_dispatch(dispatch) {
                continue;
            }
            let (handler_name, handler_kind, handler_source_file, handler_source_line) =
                Self::active_handler_trace_info(dispatch);
            let delegation_chain = dispatch
                .handler_stack
                .iter()
                .map(|entry| DelegationEntry {
                    handler_name: entry.handler_name.to_string(),
                    handler_kind: entry.handler_kind,
                    handler_source_file: entry.source_file.as_ref().map(ToString::to_string),
                    handler_source_line: entry.source_line,
                })
                .collect();
            let (action, value_repr, exception_repr) =
                Self::dispatch_trace_action_fields(&dispatch.result);
            entries.push(TraceEntry::Dispatch {
                dispatch_id: dispatch.dispatch_id,
                effect_repr: dispatch.effect_repr.clone(),
                handler_name,
                handler_kind,
                handler_source_file,
                handler_source_line,
                delegation_chain,
                action,
                value_repr,
                exception_repr,
            });
        }

        entries
    }

    pub(crate) fn collect_traceback(continuation: &Continuation) -> Vec<TraceHop> {
        let mut hops = Vec::new();
        let mut current: Option<&Continuation> = Some(continuation);

        while let Some(cont) = current {
            let mut frames = Vec::new();
            for frame in cont.frames().unwrap_or(&[]) {
                if let Frame::Program {
                    stream,
                    metadata: Some(metadata),
                    ..
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
            current = cont.parent();
        }

        hops
    }

    fn exception_repr(exception: &PyException) -> String {
        match exception {
            PyException::Materialized {
                exc_type: _,
                exc_value,
                exc_tb: _,
                ..
            } => Python::attach(|py| {
                exc_value
                    .bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| MISSING_EXCEPTION.to_string())
            }),
            PyException::RuntimeError { message, .. } => format!("RuntimeError({message:?})"),
            PyException::TypeError { message, .. } => format!("TypeError({message:?})"),
        }
    }

    fn finalize_unresolved_dispatches_as_threw(
        frame_stack: &mut [ActiveChainFrameState],
        exception: &PyException,
    ) {
        let exception_repr = Self::exception_repr(exception);
        for frame in frame_stack.iter_mut() {
            let Some(dispatch) = frame.dispatch_display.as_mut() else {
                continue;
            };
            if !matches!(dispatch.result, EffectResult::Active) {
                continue;
            }
            let handler_name = if let Some(active_entry) = dispatch
                .handler_stack
                .iter_mut()
                .find(|entry| entry.status == HandlerStatus::Active)
            {
                active_entry.status = HandlerStatus::Threw;
                active_entry.handler_name.to_string()
            } else if let Some(last_entry) = dispatch.handler_stack.last_mut() {
                if last_entry.status == HandlerStatus::Pending {
                    last_entry.status = HandlerStatus::Threw;
                }
                last_entry.handler_name.to_string()
            } else {
                MISSING_UNKNOWN.to_string()
            };
            dispatch.result = EffectResult::Threw {
                handler_name,
                exception_repr: exception_repr.clone(),
            };
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
                handler_kind: snapshot.handler_kind,
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

    fn merge_live_frame_state(
        &self,
        frame_stack: &mut Vec<ActiveChainFrameState>,
        segments: &SegmentArena,
        current_segment: Option<SegmentId>,
        dispatch_stack: &[LiveDispatchSnapshot],
    ) {
        self.merge_frame_lines_from_visible_dispatch_snapshot(frame_stack, dispatch_stack);
        self.merge_frame_lines_from_segments(frame_stack, segments, current_segment);
    }

    fn merge_frame_lines_from_segments(
        &self,
        frame_stack: &mut Vec<ActiveChainFrameState>,
        segments: &SegmentArena,
        current_segment: Option<SegmentId>,
    ) {
        let mut seg_chain = Vec::new();
        let mut seg_id = current_segment;
        while let Some(id) = seg_id {
            seg_chain.push(id);
            seg_id = segments.get(id).and_then(|seg| seg.caller);
        }
        seg_chain.reverse();

        for id in seg_chain {
            let Some(seg) = segments.get(id) else {
                continue;
            };
            for frame in &seg.frames {
                let Frame::Program {
                    stream,
                    metadata: Some(metadata),
                    handler_kind,
                    ..
                } = frame
                else {
                    continue;
                };
                Self::upsert_frame_state_from_metadata(frame_stack, stream, metadata, handler_kind);
            }
        }
    }

    fn merge_frame_lines_from_visible_dispatch_snapshot(
        &self,
        frame_stack: &mut Vec<ActiveChainFrameState>,
        dispatch_stack: &[LiveDispatchSnapshot],
    ) {
        let Some(dispatch_ctx) = dispatch_stack.iter().rev().find(|ctx| {
            frame_stack
                .iter()
                .find_map(|frame| {
                    frame.dispatch_display.as_ref().filter(|dispatch| {
                        dispatch.dispatch_id == ctx.dispatch_id
                            && Self::is_visible_dispatch(dispatch)
                    })
                })
                .is_some_and(|dispatch| Self::is_visible_dispatch(dispatch))
        }) else {
            return;
        };

        for frame in dispatch_ctx
            .continuation
            .frames()
            .expect("dispatch context continuation must be captured")
        {
            let Frame::Program {
                stream,
                metadata: Some(metadata),
                handler_kind,
                ..
            } = frame
            else {
                continue;
            };
            Self::upsert_frame_state_from_metadata(frame_stack, stream, metadata, handler_kind);
        }
    }

    fn upsert_frame_state_from_metadata(
        frame_stack: &mut Vec<ActiveChainFrameState>,
        stream: &IRStreamRef,
        metadata: &CallMetadata,
        handler_kind: &Option<HandlerKind>,
    ) {
        let line = Self::stream_debug_location(stream)
            .map(|location| location.source_line)
            .unwrap_or(metadata.source_line);
        if let Some(existing) = frame_stack
            .iter_mut()
            .find(|entry| entry.frame_id == metadata.frame_id)
        {
            existing.source_line = line;
            if existing.args_repr.is_none() {
                existing.args_repr = metadata.args_repr.clone();
            }
            if existing.sub_program_repr == MISSING_SUB_PROGRAM {
                if let Some(repr) = Self::program_call_repr(metadata) {
                    existing.sub_program_repr = repr;
                }
            }
            debug_assert!(
                existing.handler_kind.is_none() || existing.handler_kind == *handler_kind,
                "frame provenance mismatch for frame_id={}: existing={:?}, new={:?}",
                metadata.frame_id,
                existing.handler_kind,
                handler_kind
            );
            if existing.handler_kind.is_none() {
                existing.handler_kind = *handler_kind;
            }
            return;
        }

        frame_stack.push(ActiveChainFrameState {
            frame_id: metadata.frame_id as FrameId,
            function_name: metadata.function_name.clone(),
            source_file: metadata.source_file.clone(),
            source_line: line,
            args_repr: metadata.args_repr.clone(),
            sub_program_repr: Self::program_call_repr(metadata)
                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
            handler_kind: *handler_kind,
            dispatch_display: None,
        });
    }

    fn entries_from_active_chain_parts(
        &self,
        frame_stack: &[ActiveChainFrameState],
        dispatch_stack: &[LiveDispatchSnapshot],
    ) -> Vec<ActiveChainEntry> {
        let mut active_chain = self.entries_from_frame_stack(frame_stack);
        if active_chain.is_empty() {
            self.fallback_entries_when_chain_empty(frame_stack, dispatch_stack, &mut active_chain);
        }
        active_chain
    }

    fn entries_from_frame_stack(
        &self,
        frame_stack: &[ActiveChainFrameState],
    ) -> Vec<ActiveChainEntry> {
        let mut active_chain = Vec::new();
        for (index, frame) in frame_stack.iter().enumerate() {
            if let Some(dispatch) = frame
                .dispatch_display
                .as_ref()
                .filter(|dispatch| Self::is_visible_dispatch(dispatch))
            {
                if matches!(
                    dispatch.result,
                    EffectResult::Active
                        | EffectResult::Transferred { .. }
                        | EffectResult::Threw { .. }
                ) {
                    Self::push_effect_yield_entry(&mut active_chain, dispatch, Some(frame));
                    continue;
                }
            }

            if Self::should_skip_program_frame(frame_stack, index) {
                continue;
            }
            active_chain.push(Self::program_yield_entry(
                frame,
                Self::next_visible_program_frame(frame_stack, index + 1),
            ));
        }
        active_chain
    }

    fn fallback_entries_when_chain_empty(
        &self,
        frame_stack: &[ActiveChainFrameState],
        dispatch_stack: &[LiveDispatchSnapshot],
        active_chain: &mut Vec<ActiveChainEntry>,
    ) {
        let Some(dispatch) = self.fallback_dispatch_display(frame_stack, dispatch_stack) else {
            return;
        };

        let snapshot_frames =
            self.snapshot_frames_for_dispatch(dispatch.dispatch_id, dispatch_stack);
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
                Self::next_visible_program_frame(&snapshot_frames, index + 1),
            ));
        }
    }

    fn fallback_dispatch_display<'a>(
        &self,
        frame_stack: &'a [ActiveChainFrameState],
        dispatch_stack: &[LiveDispatchSnapshot],
    ) -> Option<&'a DispatchDisplayState> {
        dispatch_stack.iter().rev().find_map(|ctx| {
            frame_stack.iter().rev().find_map(|frame| {
                frame.dispatch_display.as_ref().filter(|dispatch| {
                    dispatch.dispatch_id == ctx.dispatch_id && Self::is_visible_dispatch(dispatch)
                })
            })
        })
    }

    fn snapshot_frames_for_dispatch(
        &self,
        dispatch_id: DispatchId,
        dispatch_stack: &[LiveDispatchSnapshot],
    ) -> Vec<ActiveChainFrameState> {
        dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .map(|dispatch_ctx| {
                dispatch_ctx
                    .continuation
                    .frames()
                    .expect("dispatch context continuation must be captured")
                    .iter()
                    .filter_map(|frame| {
                        let Frame::Program {
                            stream,
                            metadata: Some(metadata),
                            handler_kind,
                            ..
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
                            args_repr: metadata.args_repr.clone(),
                            sub_program_repr: Self::program_call_repr(metadata)
                                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
                            handler_kind: *handler_kind,
                            dispatch_display: None,
                        })
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    fn push_effect_yield_entry(
        chain: &mut Vec<ActiveChainEntry>,
        dispatch: &DispatchDisplayState,
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
            args_repr: frame.args_repr.clone(),
            sub_program_repr,
            handler_kind: frame.handler_kind,
        }
    }

    fn next_visible_program_frame(
        frame_stack: &[ActiveChainFrameState],
        start_index: usize,
    ) -> Option<&ActiveChainFrameState> {
        frame_stack
            .iter()
            .skip(start_index)
            .find(|frame| frame.handler_kind.is_none())
    }

    fn should_skip_program_frame(frame_stack: &[ActiveChainFrameState], index: usize) -> bool {
        let Some(frame) = frame_stack.get(index) else {
            return false;
        };
        if frame.handler_kind.is_some() {
            return false;
        }
        let Some(next_frame) = frame_stack.get(index + 1) else {
            return false;
        };
        if next_frame.handler_kind.is_none() {
            return false;
        }
        Self::next_visible_program_frame(frame_stack, index + 1).is_some()
    }

    fn active_handler_trace_info(
        dispatch: &DispatchDisplayState,
    ) -> (String, HandlerKind, Option<String>, Option<u32>) {
        let handler = dispatch
            .handler_stack
            .iter()
            .rev()
            .find(|entry| {
                matches!(
                    entry.status,
                    HandlerStatus::Active
                        | HandlerStatus::Resumed
                        | HandlerStatus::Transferred
                        | HandlerStatus::Returned
                        | HandlerStatus::Threw
                )
            })
            .or_else(|| {
                dispatch
                    .handler_stack
                    .iter()
                    .rev()
                    .find(|entry| entry.status != HandlerStatus::Pending)
            })
            .or_else(|| dispatch.handler_stack.last());
        let Some(handler) = handler else {
            return (
                MISSING_UNKNOWN.to_string(),
                HandlerKind::RustBuiltin,
                None,
                None,
            );
        };
        (
            handler.handler_name.to_string(),
            handler.handler_kind,
            handler.source_file.as_ref().map(ToString::to_string),
            handler.source_line,
        )
    }

    fn dispatch_trace_action_fields(
        result: &EffectResult,
    ) -> (DispatchAction, Option<String>, Option<String>) {
        match result {
            EffectResult::Active => (DispatchAction::Active, None, None),
            EffectResult::Resumed { value_repr } => {
                (DispatchAction::Resumed, Some(value_repr.clone()), None)
            }
            EffectResult::Transferred { .. } => (DispatchAction::Transferred, None, None),
            EffectResult::Threw { exception_repr, .. } => {
                (DispatchAction::Threw, None, Some(exception_repr.clone()))
            }
        }
    }

    fn dedup_adjacent(entries: Vec<ActiveChainEntry>) -> Vec<ActiveChainEntry> {
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
        match lhs {
            ActiveChainEntry::ProgramYield {
                function_name: lhs_function_name,
                source_file: lhs_source_file,
                source_line: lhs_source_line,
                args_repr: lhs_args_repr,
                sub_program_repr: lhs_sub_program_repr,
                handler_kind: lhs_handler_kind,
            } => match rhs {
                ActiveChainEntry::ProgramYield {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    args_repr: rhs_args_repr,
                    sub_program_repr: rhs_sub_program_repr,
                    handler_kind: rhs_handler_kind,
                } => {
                    lhs_function_name == rhs_function_name
                        && lhs_source_file == rhs_source_file
                        && lhs_source_line == rhs_source_line
                        && lhs_args_repr == rhs_args_repr
                        && lhs_sub_program_repr == rhs_sub_program_repr
                        && lhs_handler_kind == rhs_handler_kind
                }
                ActiveChainEntry::EffectYield { .. }
                | ActiveChainEntry::ContextEntry { .. }
                | ActiveChainEntry::ExceptionSite { .. } => false,
            },
            ActiveChainEntry::EffectYield {
                function_name: lhs_function_name,
                source_file: lhs_source_file,
                source_line: lhs_source_line,
                effect_repr: lhs_effect_repr,
                handler_stack: lhs_handler_stack,
                result: lhs_result,
            } => match rhs {
                ActiveChainEntry::EffectYield {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    effect_repr: rhs_effect_repr,
                    handler_stack: rhs_handler_stack,
                    result: rhs_result,
                } => {
                    lhs_function_name == rhs_function_name
                        && lhs_source_file == rhs_source_file
                        && lhs_source_line == rhs_source_line
                        && lhs_effect_repr == rhs_effect_repr
                        && lhs_handler_stack == rhs_handler_stack
                        && lhs_result == rhs_result
                }
                ActiveChainEntry::ProgramYield { .. }
                | ActiveChainEntry::ContextEntry { .. }
                | ActiveChainEntry::ExceptionSite { .. } => false,
            },
            ActiveChainEntry::ContextEntry { .. } => false,
            ActiveChainEntry::ExceptionSite {
                function_name: lhs_function_name,
                source_file: lhs_source_file,
                source_line: lhs_source_line,
                exception_type: lhs_exception_type,
                message: lhs_message,
            } => match rhs {
                ActiveChainEntry::ExceptionSite {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    exception_type: rhs_exception_type,
                    message: rhs_message,
                } => {
                    lhs_function_name == rhs_function_name
                        && lhs_source_file == rhs_source_file
                        && lhs_source_line == rhs_source_line
                        && lhs_exception_type == rhs_exception_type
                        && lhs_message == rhs_message
                }
                ActiveChainEntry::ProgramYield { .. }
                | ActiveChainEntry::EffectYield { .. }
                | ActiveChainEntry::ContextEntry { .. } => false,
            },
        }
    }

    fn inject_context(
        mut active_chain: Vec<ActiveChainEntry>,
        exception: Option<&PyException>,
    ) -> Vec<ActiveChainEntry> {
        let context_entries = exception.map_or_else(Vec::new, Self::context_entries_from_exception);
        let has_context_entries = !context_entries.is_empty();
        for data in context_entries {
            active_chain.push(ActiveChainEntry::ContextEntry { data });
        }

        let Some(exception) = exception else {
            return active_chain;
        };

        let exception_site = Self::exception_site(exception);
        let ActiveChainEntry::ExceptionSite { function_name, .. } = &exception_site else {
            unreachable!("exception_site() must return ActiveChainEntry::ExceptionSite")
        };
        let exception_function_name = function_name.as_str();
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

    fn is_visible_dispatch(dispatch: &DispatchDisplayState) -> bool {
        !dispatch.is_execution_context_effect
    }
}
