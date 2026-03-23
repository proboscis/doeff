use super::*;
use crate::capture::{EffectCreationSite, TraceHop};

impl VM {
    fn continuation_uses_stream(&self, continuation: &Continuation, stream: &IRStreamRef) -> bool {
        continuation.fibers().iter().any(|fiber_id| {
            self.segments.get(*fiber_id).is_some_and(|segment| {
                segment.frames.iter().any(|frame| match frame {
                    Frame::Program {
                        stream: snapshot_stream,
                        ..
                    } => IRStreamRef::ptr_eq(snapshot_stream, stream),
                    Frame::LexicalScope { .. } => false,
                    Frame::EvalReturn(_)
                    | Frame::MapReturn { .. }
                    | Frame::FlatMapBindResult
                    | Frame::FlatMapBindSource { .. } => false,
                })
            })
        })
    }

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
            Value::PendingContinuation(_) => "PendingContinuation",
            Value::Handlers(_) => "Handlers",
            Value::Kleisli(_) => "Kleisli",
            Value::Var(_) => "Var",
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
        origin_cont_id: ContId,
    ) -> bool {
        let marker = self
            .current_handler_dispatch()
            .filter(|(_, current_origin_cont_id, ..)| *current_origin_cont_id == origin_cont_id)
            .map(|(_, _, _, marker, _)| marker)
            .or_else(|| self.active_handler_marker_for_dispatch(origin_cont_id));
        let Some(marker) = marker else {
            return false;
        };
        self.find_prompt_boundary_by_marker(marker)
            .is_some_and(|(_, handler, _)| handler.supports_error_context_conversion())
    }

    fn is_execution_context_effect_for_dispatch(&self, origin_cont_id: ContId) -> bool {
        self.effect_for_dispatch(origin_cont_id)
            .is_some_and(|effect| Self::is_execution_context_effect(&effect))
    }

    pub(super) fn effect_creation_site_from_continuation(
        &self,
        k: &Continuation,
    ) -> Option<EffectCreationSite> {
        let frames = self.continuation_frame_stack(k);
        let (_, function_name, source_file, source_line) =
            TraceState::effect_site_from_frames(&frames)?;
        Some(EffectCreationSite {
            function_name,
            source_file,
            source_line,
        })
    }

    pub(super) fn collect_traceback(&self, continuation: &Continuation) -> Vec<TraceHop> {
        continuation
            .fibers()
            .iter()
            .map(|fiber_id| {
                self.segments
                    .get(*fiber_id)
                    .map(|segment| TraceState::traceback_hop_from_frames(&segment.frames))
                    .unwrap_or_else(|| TraceHop { frames: Vec::new() })
            })
            .collect()
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
            .map(|(_seg_id, handler, _types)| Self::handler_trace_info(&handler))
    }

    pub(super) fn current_handler_identity_for_dispatch(
        &self,
        origin_cont_id: ContId,
    ) -> Option<(usize, String)> {
        let active_dispatch = self
            .current_handler_dispatch()
            .filter(|(_, current_origin_cont_id, ..)| *current_origin_cont_id == origin_cont_id);
        let marker = active_dispatch
            .as_ref()
            .map(|(_, _, _, marker, _)| *marker)
            .or_else(|| self.active_handler_marker_for_dispatch(origin_cont_id))
            .or_else(|| {
                self.current_segment
                    .filter(|_| self.current_segment_dispatch_id() == Some(origin_cont_id))
                    .and_then(|seg_id| self.handler_marker_in_caller_chain(seg_id))
            })?;
        let (name, _, _, _) = self.marker_handler_trace_info(marker)?;
        let origin_seg_id = active_dispatch
            .as_ref()
            .and_then(|(_, _, continuation, _, _)| {
                self.continuation_handler_chain_start(continuation)
            })
            .or_else(|| {
                self.current_segment
                    .filter(|_| self.current_segment_dispatch_id() == Some(origin_cont_id))
                    .and_then(|seg_id| self.segment_program_dispatch(seg_id))
                    .and_then(|dispatch| {
                        let origin_k = dispatch.origin_as_continuation();
                        self.continuation_handler_chain_start(&origin_k)
                    })
            })
            .or_else(|| {
                self.dispatch_origin_for_origin_cont_id(origin_cont_id)
                    .and_then(|origin| self.continuation_handler_chain_start(&origin.k_origin))
            })
            .or_else(|| self.dispatch_origin_user_segment_id(origin_cont_id))?;
        let handler_idx = self.handler_index_in_caller_chain(origin_seg_id, marker)?;
        Some((handler_idx, name))
    }

    pub(super) fn current_segment_is_active_handler_for_dispatch(
        &self,
        origin_cont_id: ContId,
    ) -> bool {
        self.current_handler_dispatch()
            .is_some_and(|(seg_id, current_origin_cont_id, _, _, _)| {
                Some(seg_id) == self.current_segment && current_origin_cont_id == origin_cont_id
            })
    }

    pub(super) fn current_active_handler_dispatch_id(&self) -> Option<ContId> {
        self.current_live_handler_dispatch()
            .map(|(_, origin_cont_id, _, _, _)| origin_cont_id)
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
                Frame::LexicalScope { .. } => None,
                Frame::EvalReturn(_)
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. } => None,
            })
    }

    pub(super) fn dispatch_uses_user_continuation_stream(
        &self,
        origin_cont_id: ContId,
        stream: &IRStreamRef,
    ) -> bool {
        self.dispatch_origin_for_origin_cont_id(origin_cont_id)
            .map(|origin| origin.k_origin)
            .is_some_and(|continuation| self.continuation_uses_stream(&continuation, stream))
    }

    pub(super) fn user_continuation_dispatch_for_stream(
        &self,
        stream: &IRStreamRef,
    ) -> Option<ContId> {
        self.dispatch_origins().into_iter().find_map(|origin| {
            self.continuation_uses_stream(&origin.k_origin, stream)
                .then_some(origin.origin_cont_id)
        })
    }

    pub(super) fn handler_stream_throw_continuation(
        &self,
        stream: &IRStreamRef,
        handler_kind: Option<HandlerKind>,
    ) -> Option<Continuation> {
        handler_kind?;

        let origin_cont_id = self
            .current_active_handler_dispatch_id()
            .or_else(|| self.current_segment_dispatch_id_any())?;
        if self.is_execution_context_effect_for_dispatch(origin_cont_id) {
            return None;
        }
        let origin = self.dispatch_origin_for_origin_cont_id(origin_cont_id)?;
        let continuation = if self.dispatch_uses_user_continuation_stream(origin_cont_id, stream) {
            origin.k_origin
        } else if let Some((_, active_handler_continuation, _)) =
            self.active_handler_dispatch_for(origin_cont_id)
        {
            if self.continuation_uses_stream(&active_handler_continuation, stream) {
                active_handler_continuation
                    .tail_owned_fibers()
                    .unwrap_or(origin.k_origin)
            } else {
                active_handler_continuation
            }
        } else {
            origin.k_origin
        };
        (!self.continuation_is_consumed(&continuation)).then_some(continuation)
    }

    pub(super) fn active_error_dispatch_original_exception(&self) -> Option<PyException> {
        self.current_dispatch_origin()
            .and_then(|origin| origin.original_exception)
    }

    pub(super) fn original_exception_for_dispatch(
        &self,
        origin_cont_id: ContId,
    ) -> Option<PyException> {
        if let Some((seg_id, current_origin_cont_id, ..)) = self.current_handler_dispatch() {
            if current_origin_cont_id == origin_cont_id {
                if let Some(dispatch) = self
                    .segments
                    .get(seg_id)
                    .and_then(|segment| segment.pending_program_dispatch.as_ref())
                    .or_else(|| self.segment_program_dispatch(seg_id))
                {
                    return dispatch.original_exception.clone();
                }
            }
        }
        if let Some(seg_id) = self
            .current_segment
            .filter(|_| self.current_segment_dispatch_id() == Some(origin_cont_id))
        {
            if let Some(dispatch) = self
                .segments
                .get(seg_id)
                .and_then(|segment| segment.pending_program_dispatch.as_ref())
                .or_else(|| self.segment_program_dispatch(seg_id))
            {
                return dispatch.original_exception.clone();
            }
        }
        self.dispatch_origin_for_origin_cont_id(origin_cont_id)
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
        let current_origin_cont_id = self.current_origin_cont_id().or(active_dispatch_id);
        let active_handler_supports_conversion = active_dispatch_id.is_some_and(|origin_cont_id| {
            self.dispatch_supports_error_context_conversion(origin_cont_id)
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
        let in_get_execution_context_dispatch =
            current_origin_cont_id.is_some_and(|origin_cont_id| {
                self.is_execution_context_effect_for_dispatch(origin_cont_id)
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
            if let Some(origin_cont_id) = current_origin_cont_id {
                if let Some(original) = self.original_exception_for_dispatch(origin_cont_id) {
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
                if let Some(seg_id) = self.current_segment {
                    self.set_pending_error_context(seg_id, exception.clone());
                }
                Mode::HandleYield(DoCtrl::Perform { effect })
            }
            Err(_) => Mode::Throw(exception),
        }
    }

    pub(super) fn emit_frame_exited_due_to_error(
        &mut self,
        stream: Option<&IRStreamRef>,
        metadata: &CallMetadata,
        handler_kind: Option<HandlerKind>,
        exception: &PyException,
    ) {
        self.trace_state.record_frame_exited_due_to_error(
            stream,
            metadata,
            handler_kind,
            exception,
        );
    }

    pub(super) fn emit_handler_threw_for_dispatch(
        &mut self,
        origin_cont_id: ContId,
        exc: &PyException,
    ) {
        let is_live_handler_throw = self.current_active_handler_dispatch_id()
            == Some(origin_cont_id)
            || self.current_segment_is_active_handler_for_dispatch(origin_cont_id);
        if self.dispatch_has_terminal_handler_action(origin_cont_id) && !is_live_handler_throw {
            return;
        }
        let handler_identity = self
            .current_handler_identity_for_dispatch(origin_cont_id)
            .or_else(|| {
                let seg_id = self.current_segment?;
                if self.current_segment_dispatch_id() != Some(origin_cont_id) {
                    return None;
                }
                let marker = self.handler_marker_in_caller_chain(seg_id)?;
                let (handler_name, _, _, _) = self.marker_handler_trace_info(marker)?;
                Some((0, handler_name))
            });
        let Some((handler_index, handler_name)) = handler_identity else {
            return;
        };
        self.record_handler_completion(
            origin_cont_id,
            &handler_name,
            handler_index,
            &HandlerAction::Threw {
                exception_repr: Self::exception_repr(exc),
            },
        );
    }

    pub(super) fn emit_resume_event(
        &mut self,
        origin_cont_id: ContId,
        continuation: &Continuation,
        transferred: bool,
    ) {
        let frames = self.continuation_frame_stack(continuation);
        if let Some((resumed_function_name, source_file, source_line)) =
            TraceState::continuation_resume_location_from_frames(&frames)
        {
            if transferred {
                self.record_dispatch_transfer_target(
                    origin_cont_id,
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

    pub(crate) fn assemble_active_chain_for_dispatch(
        &mut self,
        origin_cont_id: ContId,
        exception: Option<&PyException>,
    ) -> Vec<ActiveChainEntry> {
        let current_segment = self
            .dispatch_origin_user_segment_id(origin_cont_id)
            .or(self.current_segment);
        let dispatch_stack = self.live_dispatch_snapshots_from_segment(current_segment);
        self.trace_state.assemble_scoped_active_chain(
            exception,
            &self.segments,
            current_segment,
            &dispatch_stack,
        )
    }

    fn should_attach_active_chain_for_dispatch(&self, origin_cont_id: ContId) -> bool {
        let Some(origin) = self.dispatch_origin_for_origin_cont_id(origin_cont_id) else {
            return false;
        };
        Self::is_execution_context_effect(&origin.effect) && origin.original_exception.is_none()
    }

    pub(super) fn maybe_attach_active_chain_to_execution_context(
        &mut self,
        origin_cont_id: Option<ContId>,
        value: &mut Value,
    ) -> Result<(), VMError> {
        let Some(origin_cont_id) = origin_cont_id else {
            return Ok(());
        };
        if !self.should_attach_active_chain_for_dispatch(origin_cont_id) {
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
        let context_entries = Python::attach(|py| -> Result<Vec<Py<PyAny>>, VMError> {
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
            let mut context_entries = Vec::new();
            for entry_result in iter {
                let entry = entry_result.map_err(|err| {
                    VMError::python_error(format!(
                        "failed to iterate ExecutionContext.entries while attaching active_chain: {err}"
                    ))
                })?;
                // ExecutionContext.entries is user-extensible. We only inspect
                // scheduler-owned {"kind": "spawn_boundary"} markers here and
                // intentionally ignore malformed/non-mapping payloads.
                let Ok(kind) = entry.get_item("kind") else {
                    context_entries.push(entry.unbind());
                    continue;
                };
                let Ok(kind) = kind.extract::<&str>() else {
                    context_entries.push(entry.unbind());
                    continue;
                };
                context_entries.push(entry.unbind());
            }
            Ok(context_entries)
        })?;

        let mut active_chain = self.assemble_active_chain_for_dispatch(origin_cont_id, None);
        for entry in context_entries {
            active_chain.push(ActiveChainEntry::ContextEntry { data: entry });
        }

        Python::attach(|py| {
            let context_bound = context_obj.bind(py);
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
        let debug = &mut self.debug;
        if self.current_segment.is_none() {
            return;
        }
        debug.record_trace_entry(&self.mode, &self.pending_python, dispatch_depth);
    }

    pub(super) fn record_trace_exit(&mut self, result: &StepEvent) {
        let dispatch_depth = self.dispatch_depth();
        let debug = &mut self.debug;
        if self.current_segment.is_none() {
            return;
        }
        debug.record_trace_exit(&self.mode, &self.pending_python, dispatch_depth, result);
    }

    pub(super) fn debug_step_entry(&self) {
        self.debug.debug_step_entry(
            &self.mode,
            self.current_segment,
            &self.segments,
            self.dispatch_depth(),
            &self.pending_python,
        );
    }

    pub(super) fn debug_step_exit(&self, result: &StepEvent) {
        self.debug.debug_step_exit(result);
    }
}
