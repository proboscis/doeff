use super::*;
use crate::capture::EffectCreationSite;

impl VM {
    pub(super) fn value_repr(value: &Value) -> Option<String> {
        DebugState::value_repr(value)
    }

    fn program_call_repr(metadata: &CallMetadata) -> Option<String> {
        DebugState::program_call_repr(metadata)
    }

    pub(super) fn exception_repr(exception: &PyException) -> Option<String> {
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

    pub(super) fn effect_repr(effect: &DispatchEffect) -> String {
        DebugState::effect_repr(effect)
    }

    pub(super) fn is_execution_context_effect(effect: &DispatchEffect) -> bool {
        let Some(obj) = dispatch_ref_as_python(effect) else {
            return false;
        };
        Python::attach(|py| {
            obj.bind(py)
                .extract::<PyRef<'_, PyGetExecutionContext>>()
                .is_ok()
        })
    }

    pub(super) fn dispatch_supports_error_context_conversion(
        &self,
        dispatch_id: DispatchId,
    ) -> bool {
        let Some(marker) = self.active_handler_marker_for_dispatch(dispatch_id) else {
            return false;
        };
        self.find_prompt_boundary_by_marker(marker)
            .is_some_and(|(_, handler, _, _)| handler.supports_error_context_conversion())
    }

    fn is_execution_context_effect_for_dispatch(&self, dispatch_id: DispatchId) -> bool {
        self.effect_for_dispatch(dispatch_id)
            .is_some_and(|effect| Self::is_execution_context_effect(&effect))
    }

    pub(super) fn effect_creation_site_from_continuation(
        k: &Continuation,
    ) -> Option<EffectCreationSite> {
        let (_, function_name, source_file, source_line) =
            TraceState::effect_site_from_continuation(k)?;
        Some(EffectCreationSite {
            function_name,
            source_file,
            source_line,
        })
    }

    pub(super) fn handler_trace_info(
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

    pub(super) fn invoke_kleisli_handler_expr(
        kleisli: KleisliRef,
        effect: DispatchEffect,
        continuation: Continuation,
        handler_trace_info: &HandlerSnapshotEntry,
    ) -> Result<DoCtrl, VMError> {
        let effect_obj = Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|v| v.unbind()))
            .map_err(|err| {
                VMError::python_error(format!(
                    "failed to convert dispatch effect to Python object: {err}"
                ))
            })?;
        let metadata = CallMetadata::new(
            handler_trace_info.handler_name.to_string(),
            handler_trace_info
                .source_file
                .as_ref()
                .map(ToString::to_string)
                .unwrap_or_else(|| "<unknown>".to_string()),
            handler_trace_info.source_line.unwrap_or(0),
            None,
            None,
            false,
        );

        Ok(DoCtrl::Expand {
            factory: Box::new(DoCtrl::Pure {
                value: Value::Kleisli(kleisli),
            }),
            args: vec![
                DoCtrl::Pure {
                    value: Value::Python(PyShared::new(effect_obj)),
                },
                DoCtrl::Pure {
                    value: Value::Continuation(continuation),
                },
            ],
            kwargs: vec![],
            metadata,
        })
    }

    pub(super) fn marker_handler_trace_info(
        &self,
        marker: Marker,
    ) -> Option<(String, HandlerKind, Option<String>, Option<u32>)> {
        if let Some(seg_id) = self.current_segment {
            if let Some(info) = self.handler_trace_info_for_marker_in_caller_chain(seg_id, marker) {
                return Some(info);
            }
        }
        self.find_prompt_boundary_by_marker(marker)
            .map(|(_seg_id, _handler, _types, trace_info)| {
                (
                    trace_info.handler_name.to_string(),
                    trace_info.handler_kind,
                    trace_info.source_file.as_ref().map(ToString::to_string),
                    trace_info.source_line,
                )
            })
    }

    pub(super) fn current_handler_identity_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<(usize, String)> {
        let marker = self
            .active_handler_marker_for_dispatch(dispatch_id)
            .or_else(|| {
                self.current_segment_ref()
                    .filter(|seg| seg.dispatch_id == Some(dispatch_id))
                    .map(|seg| seg.marker)
            })?;
        let (name, _, _, _) = self.marker_handler_trace_info(marker)?;
        let origin_seg_id = self.dispatch_origin_user_segment_id(dispatch_id)?;
        let handler_idx = self.handler_index_in_caller_chain(origin_seg_id, marker)?;
        Some((handler_idx, name))
    }

    pub(super) fn current_segment_is_active_handler_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> bool {
        self.current_handler_dispatch()
            .is_some_and(|(seg_id, current_dispatch_id, _, _, _)| {
                Some(seg_id) == self.current_segment && current_dispatch_id == dispatch_id
            })
    }

    pub(super) fn current_active_handler_dispatch_id(&self) -> Option<DispatchId> {
        self.nearest_handler_dispatch()
            .map(|(_, dispatch_id, _, _, _)| dispatch_id)
    }

    pub(super) fn current_program_frame_handler_kind(&self) -> Option<HandlerKind> {
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
                | Frame::DispatchOrigin { .. }
                | Frame::EvalReturn(_)
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. }
                | Frame::InterceptBodyReturn { .. } => None,
            })
    }

    pub(super) fn dispatch_uses_user_continuation_stream(
        &self,
        dispatch_id: DispatchId,
        stream: &IRStreamRef,
    ) -> bool {
        let continuation = self
            .active_handler_dispatch_for(dispatch_id)
            .map(|(_, continuation, _)| continuation)
            .or_else(|| {
                self.dispatch_origin_for_dispatch_id(dispatch_id)
                    .map(|origin| origin.k_origin)
            });
        continuation.is_some_and(|continuation| {
            continuation.frames().is_some_and(|frames| {
                frames.iter().any(|frame| match frame {
                    Frame::Program {
                        stream: snapshot_stream,
                        ..
                    } => Arc::ptr_eq(&snapshot_stream, stream),
                    Frame::InterceptorApply(_)
                    | Frame::InterceptorEval(_)
                    | Frame::HandlerDispatch { .. }
                    | Frame::DispatchOrigin { .. }
                    | Frame::EvalReturn(_)
                    | Frame::MapReturn { .. }
                    | Frame::FlatMapBindResult
                    | Frame::FlatMapBindSource { .. }
                    | Frame::InterceptBodyReturn { .. } => false,
                })
            })
        })
    }

    pub(super) fn handler_stream_throw_continuation(
        &self,
        stream: &IRStreamRef,
        handler_kind: Option<HandlerKind>,
    ) -> Option<Continuation> {
        let handler_kind = handler_kind?;

        let dispatch_id = self
            .current_segment_dispatch_id_any()
            .or_else(|| self.current_active_handler_dispatch_id())?;
        if self.is_execution_context_effect_for_dispatch(dispatch_id) {
            return None;
        }
        let continuation = if handler_kind == HandlerKind::RustBuiltin
            && self.dispatch_uses_user_continuation_stream(dispatch_id, stream)
        {
            self.dispatch_origin_for_dispatch_id(dispatch_id)
                .map(|origin| origin.k_origin)
        } else {
            self.active_handler_dispatch_for(dispatch_id)
                .map(|(_, continuation, _)| continuation)
        }?;
        (!self.is_one_shot_consumed(continuation.cont_id)).then_some(continuation)
    }

    pub(super) fn active_error_dispatch_original_exception(&self) -> Option<PyException> {
        self.current_dispatch_origin()
            .and_then(|origin| origin.original_exception)
    }

    pub(super) fn original_exception_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<PyException> {
        self.dispatch_origin_for_dispatch_id(dispatch_id)
            .and_then(|origin| origin.original_exception)
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

    pub(super) fn mode_after_generror(
        &mut self,
        site: GenErrorSite,
        exception: PyException,
        conversion_hint: bool,
    ) -> Mode {
        let active_dispatch_id = self.current_active_handler_dispatch_id();
        let current_dispatch_id = self.current_dispatch_id().or(active_dispatch_id);
        let active_handler_supports_conversion = active_dispatch_id.is_some_and(|dispatch_id| {
            self.dispatch_supports_error_context_conversion(dispatch_id)
        });
        let allow_repeat_enrichment = active_handler_supports_conversion
            && matches!(
                site,
                GenErrorSite::StepUserGeneratorConverted | GenErrorSite::RustProgramContinuation
            );
        let allow_handler_context_conversion = conversion_hint
            || active_handler_supports_conversion
                && matches!(
                    site,
                    GenErrorSite::RustProgramContinuation | GenErrorSite::StepUserGeneratorDirect
                );
        let in_get_execution_context_dispatch = current_dispatch_id
            .is_some_and(|dispatch_id| self.is_execution_context_effect_for_dispatch(dispatch_id));

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

        if exception.is_materialized_synthetic_vm_error() && !active_handler_supports_conversion {
            return Mode::Throw(exception);
        }

        if TraceState::has_execution_context(&exception) && !allow_repeat_enrichment {
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

    pub(super) fn emit_frame_entered(
        &mut self,
        metadata: &CallMetadata,
        handler_kind: Option<HandlerKind>,
    ) {
        self.trace_state
            .record_frame_entered(metadata, handler_kind);
    }

    pub(super) fn emit_frame_exited(&mut self, _metadata: &CallMetadata) {
        self.trace_state.record_frame_exited();
    }

    pub(super) fn emit_handler_threw_for_dispatch(
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
        self.trace_state.record_handler_completed(
            dispatch_id,
            &handler_name,
            handler_index,
            &HandlerAction::Threw {
                exception_repr: Self::exception_repr(exc),
            },
        );
    }

    pub(super) fn emit_resume_event(
        &mut self,
        dispatch_id: DispatchId,
        continuation: &Continuation,
        transferred: bool,
    ) {
        if let Some((resumed_function_name, source_file, source_line)) =
            TraceState::continuation_resume_location(continuation)
        {
            if transferred {
                self.trace_state.record_transfer_target(
                    dispatch_id,
                    &resumed_function_name,
                    &source_file,
                    source_line,
                );
            }
        }
    }

    pub fn assemble_traceback_entries(&mut self, exception: &PyException) -> Vec<TraceEntry> {
        self.trace_state.assemble_traceback_entries(
            exception,
            &self.segments,
            self.current_segment,
            &self.live_dispatch_snapshots(),
        )
    }

    pub fn assemble_active_chain(
        &mut self,
        exception: Option<&PyException>,
    ) -> Vec<ActiveChainEntry> {
        self.trace_state.assemble_active_chain(
            exception,
            &self.segments,
            self.current_segment,
            &self.live_dispatch_snapshots(),
        )
    }

    fn should_attach_active_chain_for_dispatch(&self, dispatch_id: DispatchId) -> bool {
        let Some(origin) = self.dispatch_origin_for_dispatch_id(dispatch_id) else {
            return false;
        };
        Self::is_execution_context_effect(&origin.effect) && origin.original_exception.is_none()
    }

    pub(super) fn maybe_attach_active_chain_to_execution_context(
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

            let active_chain_tuple =
                Value::active_chain_to_pytuple(py, &active_chain).map_err(|err| {
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

    pub(super) fn record_trace_entry(&mut self) {
        let dispatch_depth = self.dispatch_depth();
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

    pub(super) fn record_trace_exit(&mut self, result: &StepEvent) {
        let dispatch_depth = self.dispatch_depth();
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

    pub(super) fn debug_step_entry(&self) {
        self.debug.debug_step_entry(
            &self.current_seg().mode,
            self.current_segment,
            &self.segments,
            self.dispatch_depth(),
            &self.current_seg().pending_python,
        );
    }

    pub(super) fn debug_step_exit(&self, result: &StepEvent) {
        self.debug.debug_step_exit(result);
    }
}
