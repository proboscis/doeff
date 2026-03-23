//! Trace and active-event state for VM decomposition.

use std::collections::HashSet;

use crate::arena::FiberArena;
use crate::capture::{
    ActiveChainEntry, DelegationEntry, DispatchAction, EffectResult, FrameId, HandlerKind,
    HandlerStatus, TraceEntry, TraceFrame, TraceHop,
};
use crate::effect::{make_execution_context_object, PyExecutionContext};
use crate::frame::{CallMetadata, DispatchDisplay, Frame, ProgramFrameSnapshot};
use crate::ids::SegmentId;
use crate::ir_stream::{IRStreamRef, StreamLocation};
use crate::step::PyException;
use crate::value::Value;
use pyo3::prelude::*;
use pyo3::types::PyModule;

const EXECUTION_CONTEXT_ATTR: &str = "doeff_execution_context";
const MISSING_UNKNOWN: &str = "[MISSING] <unknown>";
const MISSING_SUB_PROGRAM: &str = "[MISSING] <sub_program>";
const MISSING_EXCEPTION: &str = "[MISSING] <exception>";
const MISSING_EXCEPTION_TYPE: &str = "[MISSING] Exception";

#[derive(Debug, Clone)]
pub(crate) struct LiveDispatchSnapshot {
    pub(crate) origin_fiber_id: SegmentId,
    pub(crate) effect_repr: String,
    pub(crate) dispatch_display: DispatchDisplay,
    pub(crate) frames: Vec<ProgramFrameSnapshot>,
}

#[derive(Debug, Clone)]
struct ProgramFrameState {
    frame_id: FrameId,
    function_name: String,
    source_file: String,
    source_line: u32,
    args_repr: Option<String>,
    sub_program_repr: String,
    handler_kind: Option<HandlerKind>,
}

#[derive(Debug, Clone)]
struct PreservedDispatchSnapshot {
    origin_fiber_id: SegmentId,
    effect_repr: String,
    dispatch_display: DispatchDisplay,
    frames: Vec<ProgramFrameSnapshot>,
}

#[derive(Debug, Clone, Default)]
pub(crate) struct TraceState {
    error_frames: Vec<ProgramFrameState>,
    completed_dispatches: Vec<PreservedDispatchSnapshot>,
}

impl TraceState {
    pub(crate) fn frame_stack_len(&self) -> usize {
        self.error_frames.len()
    }

    pub(crate) fn dispatch_display_count(&self) -> usize {
        self.completed_dispatches.len()
    }

    pub(crate) fn has_dispatch(&self, origin_fiber_id: SegmentId) -> bool {
        self.completed_dispatches
            .iter()
            .any(|dispatch| dispatch.origin_fiber_id == origin_fiber_id)
    }

    pub(crate) fn frame_stack_capacity(&self) -> usize {
        self.error_frames.capacity()
    }

    pub(crate) fn dispatch_display_capacity(&self) -> usize {
        self.completed_dispatches.capacity()
    }

    pub(crate) fn dispatch_has_terminal_result(&self, origin_fiber_id: SegmentId) -> bool {
        self.completed_dispatches
            .iter()
            .find(|dispatch| dispatch.origin_fiber_id == origin_fiber_id)
            .is_some_and(|dispatch| {
                !matches!(dispatch.dispatch_display.result, EffectResult::Active)
            })
    }

    pub(crate) fn clear(&mut self) {
        self.error_frames.clear();
        self.completed_dispatches.clear();
    }

    pub(crate) fn shrink_to_fit(&mut self) {
        self.error_frames.shrink_to_fit();
        self.completed_dispatches.shrink_to_fit();
    }

    pub(crate) fn remember_completed_dispatch(&mut self, preserved: Option<LiveDispatchSnapshot>) {
        let Some(preserved) = preserved else {
            return;
        };
        self.completed_dispatches.push(PreservedDispatchSnapshot {
            origin_fiber_id: preserved.origin_fiber_id,
            effect_repr: preserved.effect_repr,
            dispatch_display: preserved.dispatch_display,
            frames: preserved.frames,
        });
    }

    pub(crate) fn record_frame_exited_due_to_error(
        &mut self,
        stream: Option<&IRStreamRef>,
        metadata: &CallMetadata,
        handler_kind: Option<HandlerKind>,
        exception: &PyException,
    ) {
        self.upsert_error_frame(stream, metadata, handler_kind, exception);
    }

    pub(crate) fn clear_error_frames(&mut self) {
        self.error_frames.clear();
    }

    pub(crate) fn cleanup_orphaned_threw_dispatch_displays(&mut self) {
        self.completed_dispatches.clear();
    }

    fn upsert_error_frame(
        &mut self,
        stream: Option<&IRStreamRef>,
        metadata: &CallMetadata,
        handler_kind: Option<HandlerKind>,
        exception: &PyException,
    ) {
        let mut source_line = stream
            .and_then(Self::stream_debug_location)
            .map(|location| location.source_line)
            .unwrap_or(metadata.source_line);
        if let Some((function_name, source_file, resolved_line)) =
            Self::resolved_exception_location(exception)
        {
            if function_name == metadata.function_name && source_file == metadata.source_file {
                source_line = resolved_line;
            }
        }
        let frame = ProgramFrameState {
            frame_id: metadata.frame_id as FrameId,
            function_name: metadata.function_name.clone(),
            source_file: metadata.source_file.clone(),
            source_line,
            args_repr: metadata.args_repr.clone(),
            sub_program_repr: Self::program_call_repr(metadata)
                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
            handler_kind,
        };
        if let Some(existing) = self
            .error_frames
            .iter_mut()
            .find(|existing| existing.frame_id == frame.frame_id)
        {
            *existing = frame;
            return;
        }
        self.error_frames.insert(0, frame);
    }

    pub(crate) fn stream_debug_location(stream: &IRStreamRef) -> Option<StreamLocation> {
        let guard = stream.lock().expect("IRStream lock poisoned");
        guard.debug_location()
    }

    fn resume_location_from_frames(
        frames: &[ProgramFrameSnapshot],
    ) -> Option<(String, String, u32)> {
        for frame in frames.iter().rev() {
            let metadata = frame.metadata.as_ref()?;
            if let Some(location) = Self::stream_debug_location(&frame.stream) {
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
        None
    }

    pub(crate) fn continuation_resume_location_from_frames(
        frames: &[ProgramFrameSnapshot],
    ) -> Option<(String, String, u32)> {
        Self::resume_location_from_frames(frames)
    }

    fn is_internal_source_file(source_file: &str) -> bool {
        let normalized = source_file.replace('\\', "/").to_lowercase();
        normalized == "_effect_wrap" || normalized.contains("/doeff/")
    }

    pub(crate) fn effect_site_from_frames(
        frames: &[ProgramFrameSnapshot],
    ) -> Option<(FrameId, String, String, u32)> {
        let mut fallback: Option<(FrameId, String, String, u32)> = None;

        for frame in frames.iter().rev() {
            let Some(metadata) = frame.metadata.as_ref() else {
                continue;
            };
            let fallback_candidate = (
                metadata.frame_id as FrameId,
                metadata.function_name.clone(),
                metadata.source_file.clone(),
                metadata.source_line,
            );
            let candidate = match Self::stream_debug_location(&frame.stream) {
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

        fallback
    }

    fn program_call_repr(metadata: &CallMetadata) -> Option<String> {
        metadata.program_call.as_ref().map(|program_call| {
            Python::attach(|py| {
                program_call
                    .bind(py)
                    .repr()
                    .map(|value| value.to_string())
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
            _ => false,
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
        let active_chain = Self::ensure_exception_site(active_chain, &original);

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
            PyException::Materialized { exc_value, .. } => Python::attach(|py| {
                let exc_value_bound = exc_value.bind(py);
                let exception_type = exc_value_bound
                    .get_type()
                    .name()
                    .ok()
                    .map(|name| name.to_string())
                    .unwrap_or_else(|| MISSING_EXCEPTION_TYPE.to_string());
                let message = exc_value_bound
                    .str()
                    .map(|value| value.to_string())
                    .unwrap_or_default();
                let (function_name, source_file, source_line) =
                    Self::resolved_exception_location(exception).unwrap_or_else(|| {
                        (MISSING_UNKNOWN.to_string(), MISSING_UNKNOWN.to_string(), 0)
                    });

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

    fn resolved_exception_location(exception: &PyException) -> Option<(String, String, u32)> {
        let PyException::Materialized {
            exc_value, exc_tb, ..
        } = exception
        else {
            return None;
        };

        Python::attach(|py| {
            let exc_value_bound = exc_value.bind(py);

            if let Ok(do_module) = PyModule::import(py, "doeff.do") {
                if let Ok(resolve_location) = do_module.getattr("resolve_exception_location") {
                    if let Ok(Some(location)) = resolve_location
                        .call1((exc_value_bound.clone(),))
                        .and_then(|value| value.extract::<Option<(String, String, u32)>>())
                    {
                        return Some(location);
                    }
                }
            }

            let mut tb = exc_tb
                .as_ref()
                .map(|tb| tb.bind(py).clone().into_any())
                .or_else(|| exc_value_bound.getattr("__traceback__").ok());

            while let Some(tb_obj) = tb {
                let next = tb_obj.getattr("tb_next").ok();
                if next.as_ref().is_some_and(|item| !item.is_none()) {
                    tb = next;
                    continue;
                }

                let source_line = tb_obj
                    .getattr("tb_lineno")
                    .ok()
                    .and_then(|value| value.extract::<u32>().ok())
                    .unwrap_or(0);
                if let Ok(frame) = tb_obj.getattr("tb_frame") {
                    if let Ok(code) = frame.getattr("f_code") {
                        let function_name = code
                            .getattr("co_name")
                            .ok()
                            .and_then(|value| value.extract::<String>().ok())
                            .unwrap_or_else(|| MISSING_UNKNOWN.to_string());
                        let source_file = code
                            .getattr("co_filename")
                            .ok()
                            .and_then(|value| value.extract::<String>().ok())
                            .unwrap_or_else(|| MISSING_UNKNOWN.to_string());
                        return Some((function_name, source_file, source_line));
                    }
                }
                break;
            }
            None
        })
    }

    pub(crate) fn assemble_active_chain(
        &self,
        exception: Option<&PyException>,
        segments: &FiberArena,
        current_segment: Option<SegmentId>,
        dispatches: &[LiveDispatchSnapshot],
    ) -> Vec<ActiveChainEntry> {
        let frames = self.collect_frames(segments, current_segment);
        let dispatches = self.collect_dispatches(dispatches, exception);
        let entries = self.active_chain_entries(&frames, &dispatches, true);
        let entries = Self::dedup_adjacent(entries);
        Self::inject_context(entries, exception)
    }

    pub(crate) fn assemble_scoped_active_chain(
        &self,
        exception: Option<&PyException>,
        segments: &FiberArena,
        current_segment: Option<SegmentId>,
        dispatches: &[LiveDispatchSnapshot],
    ) -> Vec<ActiveChainEntry> {
        let frames = self.collect_frames(segments, current_segment);
        let dispatches = self.collect_dispatches(dispatches, exception);
        let entries = self.active_chain_entries(&frames, &dispatches, false);
        let entries = Self::dedup_adjacent(entries);
        Self::inject_context(entries, exception)
    }

    pub(crate) fn assemble_traceback_entries(
        &self,
        exception: &PyException,
        segments: &FiberArena,
        current_segment: Option<SegmentId>,
        dispatches: &[LiveDispatchSnapshot],
    ) -> Vec<TraceEntry> {
        let frames = self.collect_frames(segments, current_segment);
        let dispatches = self.collect_dispatches(dispatches, Some(exception));
        let mut entries = Vec::new();
        for frame in &frames {
            entries.push(TraceEntry::Frame {
                frame_id: frame.frame_id,
                function_name: frame.function_name.clone(),
                source_file: frame.source_file.clone(),
                source_line: frame.source_line,
                args_repr: frame.args_repr.clone(),
            });
        }
        for dispatch in &dispatches {
            if !(Self::dispatch_is_visible(&dispatch.dispatch_display)
                || matches!(
                    dispatch.dispatch_display.result,
                    EffectResult::Resumed { .. }
                ))
            {
                continue;
            }
            let (handler_name, handler_kind, handler_source_file, handler_source_line) =
                Self::active_handler_trace_info(&dispatch.dispatch_display);
            let delegation_chain = dispatch
                .dispatch_display
                .handler_stack
                .iter()
                .map(|entry| DelegationEntry {
                    handler_name: entry.handler_name.clone(),
                    handler_kind: entry.handler_kind,
                    handler_source_file: entry.source_file.clone(),
                    handler_source_line: entry.source_line,
                })
                .collect();
            let (action, value_repr, exception_repr) =
                Self::dispatch_trace_action_fields(&dispatch.dispatch_display.result);
            entries.push(TraceEntry::Dispatch {
                dispatch_id: dispatch.origin_fiber_id,
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

    pub(crate) fn traceback_hop_from_frames(frames: &[Frame]) -> TraceHop {
        let mut trace_frames = Vec::new();
        for frame in frames {
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
                trace_frames.push(TraceFrame {
                    func_name: metadata.function_name.clone(),
                    source_file,
                    source_line,
                });
            }
        }
        TraceHop {
            frames: trace_frames,
        }
    }

    fn exception_repr(exception: &PyException) -> String {
        match exception {
            PyException::Materialized { exc_value, .. } => Python::attach(|py| {
                exc_value
                    .bind(py)
                    .repr()
                    .map(|value| value.to_string())
                    .unwrap_or_else(|_| MISSING_EXCEPTION.to_string())
            }),
            PyException::RuntimeError { message, .. } => format!("RuntimeError({message:?})"),
            PyException::TypeError { message, .. } => format!("TypeError({message:?})"),
        }
    }

    fn collect_frames(
        &self,
        segments: &FiberArena,
        current_segment: Option<SegmentId>,
    ) -> Vec<ProgramFrameState> {
        let mut frames = self.error_frames.clone();
        self.append_segment_frames(&mut frames, segments, current_segment);
        frames
    }

    fn append_segment_frames(
        &self,
        frames: &mut Vec<ProgramFrameState>,
        segments: &FiberArena,
        current_segment: Option<SegmentId>,
    ) {
        let mut seg_chain = Vec::new();
        let mut seg_id = current_segment;
        while let Some(id) = seg_id {
            seg_chain.push(id);
            seg_id = segments.get(id).and_then(|seg| seg.parent);
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
                Self::upsert_frame_state_from_metadata(frames, stream, metadata, handler_kind);
            }
        }
    }

    fn upsert_frame_state_from_metadata(
        frames: &mut Vec<ProgramFrameState>,
        stream: &IRStreamRef,
        metadata: &CallMetadata,
        handler_kind: &Option<HandlerKind>,
    ) {
        let line = Self::stream_debug_location(stream)
            .map(|location| location.source_line)
            .unwrap_or(metadata.source_line);
        if let Some(existing) = frames
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
            if existing.handler_kind.is_none() {
                existing.handler_kind = *handler_kind;
            }
            return;
        }
        frames.push(ProgramFrameState {
            frame_id: metadata.frame_id as FrameId,
            function_name: metadata.function_name.clone(),
            source_file: metadata.source_file.clone(),
            source_line: line,
            args_repr: metadata.args_repr.clone(),
            sub_program_repr: Self::program_call_repr(metadata)
                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
            handler_kind: *handler_kind,
        });
    }

    fn collect_dispatches(
        &self,
        live_dispatches: &[LiveDispatchSnapshot],
        exception: Option<&PyException>,
    ) -> Vec<PreservedDispatchSnapshot> {
        let mut seen = HashSet::new();
        let mut dispatches = Vec::new();

        for dispatch in live_dispatches {
            if !seen.insert(dispatch.origin_fiber_id) {
                continue;
            }
            let mut trace = dispatch.dispatch_display.clone();
            if let Some(exception) = exception {
                Self::finalize_dispatch_as_threw(&mut trace, exception);
            }
            dispatches.push(PreservedDispatchSnapshot {
                origin_fiber_id: dispatch.origin_fiber_id,
                effect_repr: dispatch.effect_repr.clone(),
                dispatch_display: trace,
                frames: dispatch.frames.clone(),
            });
        }

        for dispatch in &self.completed_dispatches {
            if seen.insert(dispatch.origin_fiber_id) {
                dispatches.push(dispatch.clone());
            }
        }

        dispatches.sort_by_key(|dispatch| dispatch.origin_fiber_id.index());
        dispatches
    }

    fn finalize_dispatch_as_threw(dispatch_display: &mut DispatchDisplay, exception: &PyException) {
        if !matches!(dispatch_display.result, EffectResult::Active) {
            return;
        }
        let exception_repr = Self::exception_repr(exception);
        let handler_name = if let Some(active_entry) = dispatch_display
            .handler_stack
            .iter_mut()
            .find(|entry| entry.status == HandlerStatus::Active)
        {
            active_entry.status = HandlerStatus::Threw;
            active_entry.handler_name.clone()
        } else if let Some(last_entry) = dispatch_display.handler_stack.last_mut() {
            if last_entry.status == HandlerStatus::Pending {
                last_entry.status = HandlerStatus::Threw;
            }
            last_entry.handler_name.clone()
        } else {
            MISSING_UNKNOWN.to_string()
        };
        dispatch_display.result = EffectResult::Threw {
            handler_name,
            exception_repr,
        };
    }

    fn active_chain_entries(
        &self,
        frames: &[ProgramFrameState],
        dispatches: &[PreservedDispatchSnapshot],
        include_orphan_threw: bool,
    ) -> Vec<ActiveChainEntry> {
        let mut active_chain = Vec::new();
        let mut represented = HashSet::new();
        let mut pending_transferred_handler: Option<ActiveChainEntry> = None;

        for (index, frame) in frames.iter().enumerate() {
            if let Some(dispatch) = Self::dispatch_for_frame(dispatches, frame.frame_id, false) {
                represented.insert(dispatch.origin_fiber_id);
                Self::push_effect_yield_entry(&mut active_chain, dispatch, Some(frame));
                if !Self::has_visible_program_frame_for_handler(
                    frames,
                    index + 1,
                    &dispatch.dispatch_display,
                ) {
                    if matches!(
                        dispatch.dispatch_display.result,
                        EffectResult::Transferred { .. }
                    ) {
                        pending_transferred_handler =
                            Self::synthetic_handler_program_entry(&dispatch.dispatch_display);
                    } else if matches!(dispatch.dispatch_display.result, EffectResult::Threw { .. })
                    {
                        if let Some(entry) =
                            Self::synthetic_handler_program_entry(&dispatch.dispatch_display)
                        {
                            active_chain.push(entry);
                        }
                        if !Self::has_visible_rust_builtin_program_frame(frames, index + 1)
                            && !Self::active_chain_has_rust_builtin_program_frame(&active_chain)
                        {
                            if let Some(entry) = Self::synthetic_rust_builtin_handler_program_entry(
                                &dispatch.dispatch_display,
                            ) {
                                active_chain.push(entry);
                            }
                        }
                    }
                }
                continue;
            }

            if Self::should_skip_program_frame(frames, index) {
                if let Some(entry) =
                    Self::synthetic_hidden_gather_effect_entry(frames, index, dispatches)
                {
                    active_chain.push(entry);
                }
                continue;
            }

            active_chain.push(Self::program_yield_entry(
                frame,
                Self::next_visible_program_frame(frames, index + 1),
            ));
            if let Some(entry) = pending_transferred_handler.take() {
                active_chain.push(entry);
            }
        }

        if let Some(entry) = pending_transferred_handler {
            active_chain.push(entry);
        }

        for dispatch in dispatches
            .iter()
            .filter(|dispatch| !represented.contains(&dispatch.origin_fiber_id))
        {
            if !Self::dispatch_is_visible(&dispatch.dispatch_display) {
                continue;
            }
            let snapshot_frames = Self::snapshot_frames_for_dispatch(dispatch);
            if snapshot_frames.is_empty() {
                if matches!(dispatch.dispatch_display.result, EffectResult::Threw { .. })
                    && !include_orphan_threw
                {
                    continue;
                }
                Self::push_effect_yield_entry(&mut active_chain, dispatch, None);
            } else {
                let last_index = snapshot_frames.len() - 1;
                for (index, frame) in snapshot_frames.iter().enumerate() {
                    if index == last_index {
                        Self::push_effect_yield_entry(&mut active_chain, dispatch, Some(frame));
                        continue;
                    }
                    active_chain.push(Self::program_yield_entry(
                        frame,
                        Self::next_visible_program_frame(&snapshot_frames, index + 1),
                    ));
                }
            }
            if matches!(
                dispatch.dispatch_display.result,
                EffectResult::Transferred { .. }
            ) {
                if let Some(entry) =
                    Self::synthetic_handler_program_entry(&dispatch.dispatch_display)
                {
                    active_chain.push(entry);
                }
            } else if matches!(dispatch.dispatch_display.result, EffectResult::Threw { .. }) {
                if let Some(entry) =
                    Self::synthetic_handler_program_entry(&dispatch.dispatch_display)
                {
                    active_chain.push(entry);
                }
                if !Self::active_chain_has_rust_builtin_program_frame(&active_chain) {
                    if let Some(entry) = Self::synthetic_rust_builtin_handler_program_entry(
                        &dispatch.dispatch_display,
                    ) {
                        active_chain.push(entry);
                    }
                }
            }
        }

        active_chain
    }

    fn push_effect_yield_entry(
        chain: &mut Vec<ActiveChainEntry>,
        dispatch: &PreservedDispatchSnapshot,
        frame: Option<&ProgramFrameState>,
    ) {
        let function_name = dispatch
            .dispatch_display
            .effect_site
            .as_ref()
            .map(|site| site.function_name.clone())
            .unwrap_or_else(|| {
                frame
                    .map(|snapshot| snapshot.function_name.clone())
                    .unwrap_or_else(|| MISSING_UNKNOWN.to_string())
            });
        let source_file = dispatch
            .dispatch_display
            .effect_site
            .as_ref()
            .map(|site| site.source_file.clone())
            .unwrap_or_else(|| {
                frame
                    .map(|snapshot| snapshot.source_file.clone())
                    .unwrap_or_else(|| MISSING_UNKNOWN.to_string())
            });
        let source_line = dispatch
            .dispatch_display
            .effect_site
            .as_ref()
            .map(|site| site.source_line)
            .unwrap_or_else(|| frame.map_or(0, |snapshot| snapshot.source_line));
        chain.push(ActiveChainEntry::EffectYield {
            function_name,
            source_file,
            source_line,
            effect_repr: dispatch.effect_repr.clone(),
            handler_stack: dispatch.dispatch_display.handler_stack.clone(),
            result: dispatch.dispatch_display.result.clone(),
        });
    }

    fn program_yield_entry(
        frame: &ProgramFrameState,
        next_frame: Option<&ProgramFrameState>,
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

    fn has_visible_program_frame_for_handler(
        frames: &[ProgramFrameState],
        start_index: usize,
        dispatch_display: &DispatchDisplay,
    ) -> bool {
        let (handler_name, handler_kind, _, _) = Self::active_handler_trace_info(dispatch_display);
        frames.iter().skip(start_index).any(|frame| {
            frame.function_name == handler_name && frame.handler_kind == Some(handler_kind)
        })
    }

    fn has_visible_rust_builtin_program_frame(
        frames: &[ProgramFrameState],
        start_index: usize,
    ) -> bool {
        frames
            .iter()
            .skip(start_index)
            .any(|frame| frame.handler_kind == Some(HandlerKind::RustBuiltin))
    }

    fn active_chain_has_rust_builtin_program_frame(active_chain: &[ActiveChainEntry]) -> bool {
        active_chain.iter().any(|entry| {
            matches!(
                entry,
                ActiveChainEntry::ProgramYield {
                    handler_kind: Some(HandlerKind::RustBuiltin),
                    ..
                }
            )
        })
    }

    fn synthetic_handler_program_entry(
        dispatch_display: &DispatchDisplay,
    ) -> Option<ActiveChainEntry> {
        let (handler_name, handler_kind, source_file, source_line) =
            Self::active_handler_trace_info(dispatch_display);
        let source_file = source_file.unwrap_or_else(|| {
            if handler_kind == HandlerKind::RustBuiltin {
                "<rust>".to_string()
            } else {
                MISSING_UNKNOWN.to_string()
            }
        });
        Some(ActiveChainEntry::ProgramYield {
            function_name: handler_name,
            source_file,
            source_line: source_line.unwrap_or(0),
            args_repr: None,
            sub_program_repr: MISSING_SUB_PROGRAM.to_string(),
            handler_kind: Some(handler_kind),
        })
    }

    fn synthetic_rust_builtin_handler_program_entry(
        dispatch_display: &DispatchDisplay,
    ) -> Option<ActiveChainEntry> {
        let handler = dispatch_display
            .handler_stack
            .iter()
            .find(|entry| entry.handler_kind == HandlerKind::RustBuiltin)?;
        Some(ActiveChainEntry::ProgramYield {
            function_name: handler.handler_name.clone(),
            source_file: handler
                .source_file
                .clone()
                .unwrap_or_else(|| "<rust>".to_string()),
            source_line: handler.source_line.unwrap_or(0),
            args_repr: None,
            sub_program_repr: MISSING_SUB_PROGRAM.to_string(),
            handler_kind: Some(HandlerKind::RustBuiltin),
        })
    }

    fn next_visible_program_frame(
        frames: &[ProgramFrameState],
        start_index: usize,
    ) -> Option<&ProgramFrameState> {
        frames
            .iter()
            .skip(start_index)
            .find(|frame| frame.handler_kind.is_none())
    }

    fn should_skip_program_frame(frames: &[ProgramFrameState], index: usize) -> bool {
        let Some(frame) = frames.get(index) else {
            return false;
        };
        if frame.handler_kind.is_some() {
            return false;
        }
        let Some(next_frame) = frames.get(index + 1) else {
            return false;
        };
        if next_frame.handler_kind.is_none() {
            return false;
        }
        Self::next_visible_program_frame(frames, index + 1).is_some()
    }

    fn extract_hidden_gather_effect_repr(args_repr: Option<&str>) -> Option<String> {
        let args_repr = args_repr?;
        let prefix = "args=(";
        let separator = ", K(";
        if !args_repr.starts_with(prefix) || !args_repr.contains(separator) {
            return None;
        }
        let effect_repr = &args_repr[prefix.len()..args_repr.find(separator)?];
        effect_repr
            .starts_with("Gather(")
            .then(|| effect_repr.to_string())
    }

    fn dispatch_for_frame<'a>(
        dispatches: &'a [PreservedDispatchSnapshot],
        frame_id: FrameId,
        include_resumed: bool,
    ) -> Option<&'a PreservedDispatchSnapshot> {
        dispatches.iter().find(|dispatch| {
            dispatch
                .dispatch_display
                .effect_site
                .as_ref()
                .is_some_and(|site| site.frame_id == frame_id)
                && (Self::dispatch_is_visible(&dispatch.dispatch_display)
                    || (include_resumed
                        && matches!(
                            dispatch.dispatch_display.result,
                            EffectResult::Resumed { .. }
                        )))
        })
    }

    fn next_visible_dispatch_after<'a>(
        frames: &[ProgramFrameState],
        start_index: usize,
        dispatches: &'a [PreservedDispatchSnapshot],
    ) -> Option<&'a PreservedDispatchSnapshot> {
        for frame in frames.iter().skip(start_index) {
            if let Some(dispatch) = Self::dispatch_for_frame(dispatches, frame.frame_id, false) {
                return Some(dispatch);
            }
        }
        None
    }

    fn synthetic_hidden_gather_effect_entry(
        frames: &[ProgramFrameState],
        index: usize,
        dispatches: &[PreservedDispatchSnapshot],
    ) -> Option<ActiveChainEntry> {
        let frame = frames.get(index)?;
        if frame.handler_kind.is_some() {
            return None;
        }

        let mut gather_repr = None;
        for next_frame in frames.iter().skip(index + 1) {
            if next_frame.handler_kind.is_none() {
                break;
            }
            if let Some(effect_repr) =
                Self::extract_hidden_gather_effect_repr(next_frame.args_repr.as_deref())
            {
                gather_repr = Some(effect_repr);
            }
        }
        let effect_repr = gather_repr?;
        let (handler_stack, result) =
            Self::next_visible_dispatch_after(frames, index + 1, dispatches)
                .map(|dispatch| {
                    (
                        dispatch.dispatch_display.handler_stack.clone(),
                        dispatch.dispatch_display.result.clone(),
                    )
                })
                .unwrap_or_else(|| (Vec::new(), EffectResult::Active));

        Some(ActiveChainEntry::EffectYield {
            function_name: frame.function_name.clone(),
            source_file: frame.source_file.clone(),
            source_line: frame.source_line,
            effect_repr,
            handler_stack,
            result,
        })
    }

    fn snapshot_frames_for_dispatch(
        dispatch: &PreservedDispatchSnapshot,
    ) -> Vec<ProgramFrameState> {
        dispatch
            .frames
            .iter()
            .filter_map(|frame| {
                let metadata = frame.metadata.as_ref()?;
                let line = Self::stream_debug_location(&frame.stream)
                    .map(|location| location.source_line)
                    .unwrap_or(metadata.source_line);
                Some(ProgramFrameState {
                    frame_id: metadata.frame_id as FrameId,
                    function_name: metadata.function_name.clone(),
                    source_file: metadata.source_file.clone(),
                    source_line: line,
                    args_repr: metadata.args_repr.clone(),
                    sub_program_repr: Self::program_call_repr(metadata)
                        .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
                    handler_kind: frame.handler_kind,
                })
            })
            .collect()
    }

    fn active_handler_trace_info(
        dispatch_display: &DispatchDisplay,
    ) -> (String, HandlerKind, Option<String>, Option<u32>) {
        let handler = dispatch_display
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
                dispatch_display
                    .handler_stack
                    .iter()
                    .rev()
                    .find(|entry| entry.status != HandlerStatus::Pending)
            })
            .or_else(|| dispatch_display.handler_stack.last());
        let Some(handler) = handler else {
            return (
                MISSING_UNKNOWN.to_string(),
                HandlerKind::RustBuiltin,
                None,
                None,
            );
        };
        (
            handler.handler_name.clone(),
            handler.handler_kind,
            handler.source_file.clone(),
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
                    let identical = lhs_function_name == rhs_function_name
                        && lhs_source_file == rhs_source_file
                        && lhs_source_line == rhs_source_line
                        && lhs_args_repr == rhs_args_repr
                        && lhs_sub_program_repr == rhs_sub_program_repr
                        && lhs_handler_kind == rhs_handler_kind;
                    identical
                        || (lhs_function_name == rhs_function_name
                            && lhs_source_file == rhs_source_file
                            && lhs_source_line == rhs_source_line
                            && lhs_handler_kind == rhs_handler_kind
                            && lhs_handler_kind.is_some()
                            && Self::is_hidden_execution_context_handler_args(lhs_args_repr)
                            && Self::is_hidden_execution_context_handler_args(rhs_args_repr))
                }
                _ => false,
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
                _ => false,
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
                _ => false,
            },
        }
    }

    fn is_hidden_execution_context_handler_args(args_repr: &Option<String>) -> bool {
        let Some(args_repr) = args_repr.as_deref() else {
            return false;
        };
        let prefix = "args=(";
        let separator = ", K(";
        if !args_repr.starts_with(prefix) {
            return false;
        }
        let Some(separator_index) = args_repr.find(separator) else {
            return false;
        };
        &args_repr[prefix.len()..separator_index] == "GetExecutionContext()"
    }

    fn contains_exception_site(
        active_chain: &[ActiveChainEntry],
        exception_site: &ActiveChainEntry,
    ) -> bool {
        let ActiveChainEntry::ExceptionSite {
            function_name,
            source_file,
            source_line,
            exception_type,
            message,
        } = exception_site
        else {
            return false;
        };

        active_chain.iter().any(|entry| {
            matches!(
                entry,
                ActiveChainEntry::ExceptionSite {
                    function_name: entry_function_name,
                    source_file: entry_source_file,
                    source_line: entry_source_line,
                    exception_type: entry_exception_type,
                    message: entry_message,
                } if entry_function_name == function_name
                    && entry_source_file == source_file
                    && entry_source_line == source_line
                    && entry_exception_type == exception_type
                    && entry_message == message
            )
        })
    }

    fn ensure_exception_site(
        mut active_chain: Vec<ActiveChainEntry>,
        exception: &PyException,
    ) -> Vec<ActiveChainEntry> {
        let exception_site = Self::exception_site(exception);
        if !Self::contains_exception_site(&active_chain, &exception_site) {
            active_chain.push(exception_site);
        }
        active_chain
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

    fn dispatch_is_visible(dispatch_display: &DispatchDisplay) -> bool {
        !dispatch_display.is_execution_context_effect
            && (!dispatch_display.resumed_once
                || !matches!(dispatch_display.result, EffectResult::Resumed { .. }))
    }
}
