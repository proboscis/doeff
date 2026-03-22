use std::collections::HashMap;

use super::*;
use crate::ids::VarId;

impl VM {
    fn has_live_handler_program_frame(&self) -> bool {
        self.current_seg().frames.iter().any(|frame| {
            matches!(
                frame,
                Frame::Program {
                    handler_kind: Some(_),
                    ..
                }
            )
        })
    }

    fn should_throw_into_live_handler_frame(&self, handler_kind: Option<HandlerKind>) -> bool {
        handler_kind.is_some() && self.has_live_handler_program_frame()
    }

    fn should_enrich_uncaught_exception_with_active_chain(
        active_chain: &[ActiveChainEntry],
    ) -> bool {
        active_chain.iter().any(|entry| match entry {
            ActiveChainEntry::ProgramYield { handler_kind, .. } => handler_kind.is_some(),
            ActiveChainEntry::EffectYield { .. } | ActiveChainEntry::ContextEntry { .. } => true,
            ActiveChainEntry::ExceptionSite { .. } => false,
        })
    }

    fn missing_state_key_exception(key: &str) -> PyException {
        PyException::from(pyo3::exceptions::PyKeyError::new_err(key.to_string()))
    }

    fn enrich_uncaught_exception_with_active_chain(
        &self,
        exception: PyException,
        active_chain: Vec<ActiveChainEntry>,
    ) -> PyException {
        let empty_context = Python::attach(|py| crate::effect::make_execution_context_object(py));
        let context_value = match empty_context {
            Ok(context) => Value::Python(PyShared::new(context)),
            Err(err) => {
                crate::vm_warn_log!(
                    "failed to create ExecutionContext while finalizing uncaught exception: {err}"
                );
                return exception;
            }
        };

        match TraceState::enrich_original_exception_with_context(
            exception,
            context_value,
            active_chain,
        ) {
            Ok(exception) => exception,
            Err(effect_err) => effect_err,
        }
    }

    fn should_treat_python_handler_gen_return_as_handler_completion(&self) -> bool {
        self.current_seg().frames.iter().all(|frame| {
            !matches!(
                frame,
                Frame::Program {
                    handler_kind: Some(_),
                    ..
                }
            )
        })
    }

    /// Set mode to Throw with a RuntimeError and return Continue.
    pub(super) fn throw_runtime_error(&mut self, message: &str) -> StepEvent {
        if self.current_segment.is_none() {
            return StepEvent::Error(VMError::internal(
                "throw_runtime_error called without current segment",
            ));
        }
        self.set_contextual_throw(PyException::runtime_error(message.to_string()));
        StepEvent::Continue
    }

    pub(super) fn throw_handler_protocol_error(&mut self, message: impl Into<String>) -> StepEvent {
        if self.current_segment.is_none() {
            return StepEvent::Error(VMError::internal(
                "throw_handler_protocol_error called without current segment",
            ));
        }
        self.set_contextual_throw(PyException::handler_protocol_error(message));
        StepEvent::Continue
    }

    pub(super) fn contextual_throw_mode(&mut self, exception: PyException) -> Mode {
        self.mode_after_generror(GenErrorSite::VmRaisedUser, exception, false)
    }

    pub(super) fn contextual_internal_throw_mode(&mut self, exception: PyException) -> Mode {
        self.mode_after_generror(GenErrorSite::VmRaisedInternal, exception, false)
    }

    pub(super) fn set_contextual_throw(&mut self, exception: PyException) {
        let mode = self.contextual_throw_mode(exception);
        self.mode = mode;
    }

    pub(super) fn set_contextual_internal_throw(&mut self, exception: PyException) {
        let mode = self.contextual_internal_throw_mode(exception);
        self.mode = mode;
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
        self.mode = Mode::HandleYield(expr);
        StepEvent::Continue
    }

    pub(super) fn evaluate(&mut self, ir_node: DoCtrl) -> StepEvent {
        self.mode = Mode::HandleYield(ir_node);
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
                false,
            )),
        }
    }

    fn extract_doeff_generator(
        value: PyShared,
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

            let stream = IRStreamRef::new(Box::new(PythonGeneratorStream::new(
                PyShared::new(wrapped.generator.clone_ref(py)),
                PyShared::new(wrapped.get_frame.clone_ref(py)),
            )) as Box<dyn IRStream>);
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

    pub fn step(&mut self) -> StepEvent {
        let mode_kind = {
            if self.current_segment_ref().is_none() {
                return StepEvent::Error(VMError::internal("no current segment"));
            }
            match &self.mode {
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
                if matches!(segment.kind, SegmentKind::InterceptorBoundary { .. }) {
                    let caller = segment.parent;
                    let mode = std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit));
                    self.current_segment = caller;
                    self.mode = mode;
                    return StepEvent::Continue;
                }
                let caller = segment.parent;
                let mode = std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit));
                match mode {
                    Mode::Deliver(value) => {
                        // Don't free here — step_return reads the segment's caller.
                        self.mode = Mode::Return(value);
                        return StepEvent::Continue;
                    }
                    Mode::Throw(exc) => {
                        if let Some(caller_id) = caller {
                            self.reparent_children(seg_id, Some(caller_id));
                            self.current_segment = Some(caller_id);
                            self.mode = Mode::Throw(exc);
                            self.free_segment(seg_id);
                            return StepEvent::Continue;
                        } else {
                            self.finalize_active_dispatches_as_threw(&exc);
                            let trace = self.assemble_traceback_entries(&exc);
                            let active_chain = self
                                .assemble_active_chain(Some(&exc))
                                .into_iter()
                                .filter(|entry| {
                                    !matches!(entry, ActiveChainEntry::ContextEntry { .. })
                                })
                                .collect::<Vec<ActiveChainEntry>>();
                            let exc = if Self::should_enrich_uncaught_exception_with_active_chain(
                                &active_chain,
                            ) {
                                self.enrich_uncaught_exception_with_active_chain(
                                    exc,
                                    active_chain.clone(),
                                )
                            } else {
                                exc
                            };
                            self.completed_segment = Some(seg_id);
                            self.current_segment = None;
                            self.trace_state.cleanup_orphaned_threw_dispatch_displays();
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

            // Take mode by move — each branch sets VM.mode before returning.
            let mode = std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit));
            (frame, mode)
        };

        match frame {
            Frame::Program {
                stream,
                metadata,
                handler_kind,
                dispatch,
            } => {
                let incoming_throw = match &mode {
                    Mode::Throw(exc) => Some(exc.clone()),
                    Mode::Deliver(_) | Mode::HandleYield(_) | Mode::Return(_) => None,
                };
                if let Some(program_dispatch) = dispatch {
                    self.set_pending_program_dispatch(seg_id, program_dispatch);
                }
                let step = {
                    let Some(_segment) = self.segments.get_mut(seg_id) else {
                        return StepEvent::Error(VMError::invalid_segment("segment not found"));
                    };
                    let mut scope = self.visible_scope_store(seg_id);
                    let mut guard = stream.lock().expect("IRStream lock poisoned");
                    match mode {
                        Mode::Deliver(value) => {
                            guard.resume(value, &mut self.var_store, &mut scope)
                        }
                        Mode::Throw(exc) => guard.throw(exc, &mut self.var_store, &mut scope),
                        Mode::HandleYield(yielded) => {
                            unreachable!("Program frame resumed with HandleYield mode: {yielded:?}")
                        }
                        Mode::Return(value) => {
                            unreachable!("Program frame resumed with Return mode: {value:?}")
                        }
                    }
                };
                self.apply_stream_step(step, stream, metadata, handler_kind, incoming_throw)
            }
            Frame::LexicalScope { .. } => {
                self.mode = mode;
                StepEvent::Continue
            }
            Frame::InterceptorApply(cont) => self.step_interceptor_apply_frame(*cont, mode),
            Frame::InterceptorEval(cont) => self.step_interceptor_eval_frame(*cont, mode),
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

    pub(super) fn same_exception(lhs: &PyException, rhs: &PyException) -> bool {
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
            ) => Python::attach(|py| {
                let lhs_bound = lhs_value.bind(py);
                let rhs_bound = rhs_value.bind(py);
                if lhs_bound.as_ptr() == rhs_bound.as_ptr() {
                    return true;
                }

                let same_type = lhs_bound.get_type().as_ptr() == rhs_bound.get_type().as_ptr();
                if !same_type {
                    return false;
                }

                let lhs_message = lhs_bound
                    .str()
                    .map(|value| value.to_string())
                    .unwrap_or_default();
                let rhs_message = rhs_bound
                    .str()
                    .map(|value| value.to_string())
                    .unwrap_or_default();
                lhs_message == rhs_message
            }),
            (
                PyException::RuntimeError {
                    message: lhs_message,
                    metadata: lhs_metadata,
                },
                PyException::RuntimeError {
                    message: rhs_message,
                    metadata: rhs_metadata,
                },
            )
            | (
                PyException::TypeError {
                    message: lhs_message,
                    metadata: lhs_metadata,
                },
                PyException::TypeError {
                    message: rhs_message,
                    metadata: rhs_metadata,
                },
            ) => lhs_message == rhs_message && lhs_metadata == rhs_metadata,
            (
                PyException::Materialized { .. },
                PyException::RuntimeError { .. } | PyException::TypeError { .. },
            )
            | (
                PyException::RuntimeError { .. } | PyException::TypeError { .. },
                PyException::Materialized { .. },
            )
            | (PyException::RuntimeError { .. }, PyException::TypeError { .. })
            | (PyException::TypeError { .. }, PyException::RuntimeError { .. }) => false,
        }
    }

    fn chain_exception_context(original_exception: &PyException, cleanup_exception: &PyException) {
        if Self::same_exception(original_exception, cleanup_exception) {
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
            if let Some(seg_id) = self.current_segment {
                self.decrement_interceptor_eval_depth(seg_id);
            }
        }
        if let Some(metadata) = continuation.interceptor_metadata.as_ref() {
            self.emit_frame_exited(metadata);
        }
        match mode {
            Mode::Deliver(value) => {
                self.mode = self.handle_interceptor_apply_result(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.pop_interceptor_skip(continuation.marker);
                self.mode = Mode::Throw(exc);
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
        if let Some(seg_id) = self.current_segment {
            self.decrement_interceptor_eval_depth(seg_id);
        }
        match mode {
            Mode::Deliver(value) => {
                self.mode = self.handle_interceptor_eval_result(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.pop_interceptor_skip(continuation.marker);
                self.mode = Mode::Throw(exc);
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

    fn step_eval_return_frame(
        &mut self,
        continuation: EvalReturnContinuation,
        mode: Mode,
    ) -> StepEvent {
        if matches!(continuation, EvalReturnContinuation::TailResumeReturn) {
            return match mode {
                Mode::Deliver(value) => self.handle_tail_resume_return(value),
                Mode::Throw(exception) => {
                    if let Some(dispatch_id) = self.current_dispatch_id() {
                        self.finish_dispatch_tracking(dispatch_id);
                    }
                    self.mode = Mode::Throw(exception);
                    StepEvent::Continue
                }
                Mode::HandleYield(yielded) => {
                    unreachable!("tail-resume return frame received HandleYield mode: {yielded:?}")
                }
                Mode::Return(value) => {
                    unreachable!("tail-resume return frame received Return mode: {value:?}")
                }
            };
        }

        if let EvalReturnContinuation::ResumeToContinuation { continuation } = continuation {
            self.mode = match mode {
                Mode::Deliver(value) => Mode::HandleYield(DoCtrl::Resume {
                    continuation,
                    value,
                }),
                Mode::Throw(exception) => Mode::HandleYield(DoCtrl::ResumeThrow {
                    continuation,
                    exception,
                }),
                Mode::HandleYield(yielded) => {
                    unreachable!(
                        "resume-to-continuation frame received HandleYield mode: {yielded:?}"
                    )
                }
                Mode::Return(value) => {
                    unreachable!("resume-to-continuation frame received Return mode: {value:?}")
                }
            };
            return StepEvent::Continue;
        }

        if let EvalReturnContinuation::ReturnToContinuation { continuation } = continuation {
            self.mode = match mode {
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
        if let EvalReturnContinuation::EvalInScopeReturn { continuation } = continuation {
            self.mode = match mode {
                Mode::Deliver(value) => Mode::HandleYield(DoCtrl::Transfer {
                    continuation,
                    value,
                }),
                Mode::Throw(exception) => Mode::HandleYield(DoCtrl::TransferThrow {
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
                self.mode = self.mode_from_eval_return_continuation(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.mode = Mode::Throw(exc);
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
                    return self.contextual_internal_throw_mode(PyException::runtime_error(
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
                    return self.contextual_internal_throw_mode(PyException::runtime_error(
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
                    return self.contextual_internal_throw_mode(PyException::runtime_error(
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
                    return self.contextual_internal_throw_mode(PyException::runtime_error(
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
            EvalReturnContinuation::ResumeToContinuation { .. }
            | EvalReturnContinuation::ReturnToContinuation { .. }
            | EvalReturnContinuation::EvalInScopeReturn { .. }
            | EvalReturnContinuation::TailResumeReturn => {
                unreachable!("return-to-continuation frames are handled before value dispatch")
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
                self.mode = Mode::HandleYield(DoCtrl::Apply {
                    f: Box::new(DoCtrl::Pure {
                        value: Value::Python(mapper),
                    }),
                    args: vec![DoCtrl::Pure { value }],
                    kwargs: vec![],
                    metadata: mapper_meta,
                });
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.mode = Mode::Throw(exc);
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
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.mode = Mode::Throw(exc);
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
                self.mode = Mode::HandleYield(DoCtrl::Expand {
                    factory: Box::new(DoCtrl::Pure {
                        value: Value::Python(binder),
                    }),
                    args: vec![DoCtrl::Pure { value }],
                    kwargs: vec![],
                    metadata: binder_meta,
                });
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.mode = Mode::Throw(exc);
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
            Mode::Deliver(value) => {
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.mode = Mode::Throw(exc);
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
        incoming_throw: Option<PyException>,
    ) -> StepEvent {
        match step {
            IRStreamStep::Yield(yielded) => {
                if incoming_throw.is_some() {
                    self.trace_state.clear_preserved_error_frames();
                }
                self.propagate_auto_unwrap_program_context_to_yielded(metadata.as_ref(), &yielded);
                self.handle_stream_yield(yielded, stream, metadata, handler_kind)
            }
            IRStreamStep::Return(value) => {
                if incoming_throw.is_some() {
                    self.trace_state.clear_preserved_error_frames();
                }
                if let Some(ref m) = metadata {
                    self.emit_frame_exited(m);
                }
                if handler_kind.is_some() {
                    self.handle_handler_return(value)
                } else {
                    self.mode = Mode::Deliver(value);
                    StepEvent::Continue
                }
            }
            IRStreamStep::Throw(exc) => {
                if let Some(ref m) = metadata {
                    self.emit_frame_exited_due_to_error(Some(&stream), m, handler_kind, &exc);
                }
                if self.should_throw_into_live_handler_frame(handler_kind) {
                    self.mode = Mode::Throw(exc);
                    return StepEvent::Continue;
                }
                if let Some(continuation) =
                    self.handler_stream_throw_continuation(&stream, handler_kind)
                {
                    self.mode = Mode::HandleYield(DoCtrl::TransferThrow {
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
                let propagated_throw = incoming_throw
                    .as_ref()
                    .is_some_and(|original| Self::same_exception(original, &exc));
                if let Some(dispatch_id) = dispatch_id.filter(|_| !propagated_throw) {
                    self.emit_handler_threw_for_dispatch(dispatch_id, &exc);
                }
                self.set_contextual_throw(exc);
                StepEvent::Continue
            }
            IRStreamStep::NeedsPython(call) => {
                if matches!(
                    &call,
                    PythonCall::GenNext | PythonCall::GenSend { .. } | PythonCall::GenThrow { .. }
                ) {
                    let incoming_throw = match &call {
                        PythonCall::GenThrow { exc } => Some(exc.clone()),
                        PythonCall::GenNext
                        | PythonCall::GenSend { .. }
                        | PythonCall::CallFunc { .. }
                        | PythonCall::EvalExpr { .. }
                        | PythonCall::CallAsync { .. } => None,
                    };
                    self.pending_python = Some(PendingPython::StepUserGenerator {
                        stream,
                        metadata,
                        handler_kind,
                        incoming_throw,
                    });
                    return StepEvent::NeedsPython(call);
                }

                if let Err(err) = self.push_program_frame(stream, metadata, handler_kind) {
                    return StepEvent::Error(err);
                }
                let (marker, k) = if let Some(dispatch_id) = self.current_dispatch_id() {
                    let Some((_, k, marker)) = self
                        .active_handler_dispatch_for(dispatch_id)
                        .or_else(|| self.handler_dispatch_for_any(dispatch_id))
                    else {
                        return StepEvent::Error(VMError::internal(
                            "RustProgramContinuation: active handler dispatch not found",
                        ));
                    };
                    (marker, k)
                } else {
                    let Some(seg_id) = self.current_segment else {
                        return StepEvent::Error(VMError::internal(
                            "RustProgramContinuation without current segment",
                        ));
                    };
                    if self.segments.get(seg_id).is_none() {
                        return StepEvent::Error(VMError::invalid_segment(
                            "RustProgramContinuation segment not found",
                        ));
                    }
                    (
                        self.handler_marker_in_caller_chain(seg_id)
                            .unwrap_or_else(Marker::fresh),
                        self.capture_live_continuation(seg_id, self.current_segment_dispatch_id()),
                    )
                };
                self.pending_python = Some(PendingPython::RustProgramContinuation { marker, k });
                StepEvent::NeedsPython(call)
            }
        }
    }

    fn propagate_auto_unwrap_program_context_to_yielded(
        &self,
        metadata: Option<&CallMetadata>,
        yielded: &DoCtrl,
    ) {
        let Some(metadata) = metadata else {
            return;
        };
        if !metadata.auto_unwrap_programlike {
            return;
        }
        let DoCtrl::Perform { effect } = yielded else {
            return;
        };
        let Some(effect_obj) = dispatch_ref_as_python(effect) else {
            return;
        };
        Python::attach(|py| {
            let _ = Self::tag_program_auto_unwrap_metadata(effect_obj.bind(py));
        });
    }

    fn tag_program_auto_unwrap_metadata(obj: &Bound<'_, PyAny>) -> PyResult<()> {
        for attr in ["program", "sub_program"] {
            let Ok(program) = obj.getattr(attr) else {
                continue;
            };
            Self::tag_programlike_meta_recursive(program.as_any())?;
        }
        Ok(())
    }

    fn tag_programlike_meta_recursive(obj: &Bound<'_, PyAny>) -> PyResult<()> {
        if let Ok(meta) = obj.getattr("meta") {
            if let Ok(meta_dict) = meta.cast::<PyDict>() {
                meta_dict.set_item("auto_unwrap_programlike", true)?;
                return Ok(());
            }
        }
        for attr in ["program", "sub_program", "expr", "inner"] {
            let Ok(inner) = obj.getattr(attr) else {
                continue;
            };
            Self::tag_programlike_meta_recursive(inner.as_any())?;
        }
        Ok(())
    }

    fn handle_stream_yield(
        &mut self,
        yielded: DoCtrl,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
    ) -> StepEvent {
        let chain = Arc::new(self.current_interceptor_chain());
        self.mode =
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
            if let Err(err) = self.push_program_frame(stream, metadata, handler_kind) {
                return Mode::Throw(PyException::runtime_error(err.to_string()));
            }
        } else if let Some(ref m) = metadata {
            self.emit_frame_exited(m);
        }
        Mode::HandleYield(yielded)
    }

    fn current_interceptor_chain(&self) -> Vec<InterceptorChainLink> {
        fn extend_chain(
            vm: &VM,
            start: Option<SegmentId>,
            chain: &mut Vec<InterceptorChainLink>,
            seen: &mut HashSet<Marker>,
            visited_segments: &mut HashSet<SegmentId>,
        ) {
            let mut cursor = start;
            let mut path_segments = HashSet::new();
            while let Some(seg_id) = cursor {
                if !path_segments.insert(seg_id) {
                    debug_assert!(false, "segment graph cycle detected at {:?}", seg_id);
                    break;
                }
                if !visited_segments.insert(seg_id) {
                    break;
                }
                let Some(seg) = vm.segments.get(seg_id) else {
                    break;
                };
                if let Some(link) = InterceptorChainLink::from_boundary(&seg.kind) {
                    if seen.insert(link.marker) {
                        chain.push(link);
                    }
                }
                cursor = seg.parent;
            }
        }

        let mut chain = Vec::new();
        let mut seen = HashSet::new();
        let mut visited_segments = HashSet::new();
        extend_chain(
            self,
            self.current_segment,
            &mut chain,
            &mut seen,
            &mut visited_segments,
        );
        for origin_seg_id in self.dispatch_origin_callers() {
            extend_chain(
                self,
                Some(origin_seg_id),
                &mut chain,
                &mut seen,
                &mut visited_segments,
            );
        }
        chain
    }

    fn is_interceptor_skipped(&self, marker: Marker) -> bool {
        self.current_segment
            .is_some_and(|seg_id| self.is_interceptor_skipped_on(seg_id, marker))
    }

    fn pop_interceptor_skip(&mut self, marker: Marker) {
        if let Some(seg_id) = self.current_segment {
            self.pop_interceptor_skip_on(seg_id, marker);
        }
    }

    fn push_interceptor_skip(&mut self, marker: Marker) {
        if let Some(seg_id) = self.current_segment {
            self.push_interceptor_skip_on(seg_id, marker);
        }
    }

    fn classify_interceptor_result_shape(result_obj: &PyShared) -> (bool, bool) {
        Python::attach(|py| {
            let bound = result_obj.bind(py);
            let is_effect_base = bound.is_instance_of::<PyEffectBase>();
            let is_py_doexpr = bound.is_instance_of::<PyDoExprBase>();
            let is_doexpr = is_py_doexpr || bound.is_instance_of::<DoeffGenerator>();
            let is_direct_expr = is_effect_base || is_py_doexpr;
            (is_direct_expr, is_doexpr)
        })
    }

    fn classify_interceptor_result_object(
        &self,
        result_obj: PyShared,
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
        entry: &InterceptorChainLink,
        yielded_obj: &PyShared,
    ) -> Result<bool, PyException> {
        let Some(types) = entry.types.as_deref() else {
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

    fn continue_interceptor_chain_mode(
        &mut self,
        yielded: DoCtrl,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        chain: Arc<Vec<InterceptorChainLink>>,
        start_idx: usize,
    ) -> Mode {
        let current = yielded;
        let mut idx = start_idx;

        while idx < chain.len() {
            let link = &chain[idx];
            let marker = link.marker;
            idx += 1;
            if self.is_interceptor_skipped(marker) {
                continue;
            }

            let yielded_obj = match doctrl_to_pyexpr_for_vm(&current) {
                Ok(Some(obj)) => PyShared::new(obj),
                Ok(None) => continue,
                Err(exc) => return self.contextual_throw_mode(exc),
            };

            match self.should_invoke_interceptor(link, &yielded_obj) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(exc) => return self.contextual_throw_mode(exc),
            }

            return self.start_interceptor_invocation_mode(
                marker,
                link.clone(),
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
            false,
        )
    }

    fn start_interceptor_invocation_mode(
        &mut self,
        marker: Marker,
        entry: InterceptorChainLink,
        yielded: DoCtrl,
        yielded_obj: PyShared,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        chain: Arc<Vec<InterceptorChainLink>>,
        next_idx: usize,
    ) -> Mode {
        let interceptor_kleisli = entry.interceptor.clone();
        let guard_eval_depth = entry.types.is_some();
        let interceptor_meta = entry.metadata.clone();
        let apply_metadata = interceptor_meta
            .clone()
            .unwrap_or_else(Self::fallback_interceptor_metadata);
        self.push_interceptor_skip(marker);

        if self.current_segment.is_none() {
            self.pop_interceptor_skip(marker);
            return self.contextual_internal_throw_mode(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        }
        if let Some(meta) = interceptor_meta.as_ref() {
            self.emit_frame_entered(meta, None);
        }
        let continuation = InterceptorContinuation {
            marker,
            original_yielded: yielded,
            original_obj: yielded_obj.clone(),
            emitter_stream: stream,
            emitter_metadata: metadata,
            emitter_handler_kind: handler_kind,
            chain,
            next_idx,
            interceptor_metadata: interceptor_meta,
            guard_eval_depth,
        };
        let Some(seg_id) = self.current_segment else {
            self.pop_interceptor_skip(marker);
            return self.contextual_internal_throw_mode(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        };
        if guard_eval_depth {
            self.increment_interceptor_eval_depth(seg_id);
        }
        let Some(seg) = self.segments.get_mut(seg_id) else {
            self.pop_interceptor_skip(marker);
            return self.contextual_internal_throw_mode(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        };
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
            return self.contextual_throw_mode(PyException::type_error(
                "WithIntercept interceptor must return DoExpr",
            ));
        };

        let (is_direct_expr, is_doexpr) = Self::classify_interceptor_result_shape(&result_obj);

        if is_direct_expr {
            let transformed = match self.classify_interceptor_result_object(
                result_obj,
                &original_obj,
                original_yielded,
            ) {
                Ok(expr) => expr,
                Err(exc) => {
                    self.pop_interceptor_skip(marker);
                    return self.contextual_throw_mode(exc);
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
            let Some(seg_id) = self.current_segment else {
                self.pop_interceptor_skip(marker);
                return self.contextual_internal_throw_mode(PyException::runtime_error(
                    "current_segment_mut() returned None while evaluating interceptor result",
                ));
            };
            self.increment_interceptor_eval_depth(seg_id);
            let Some(seg) = self.segments.get_mut(seg_id) else {
                self.pop_interceptor_skip(marker);
                return self.contextual_internal_throw_mode(PyException::runtime_error(
                    "current_segment_mut() returned None while evaluating interceptor result",
                ));
            };
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
                expr: result_obj,
                metadata: None,
            });
        }

        self.pop_interceptor_skip(marker);
        self.contextual_throw_mode(PyException::type_error(
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
                    return self.contextual_throw_mode(exc);
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
        let yielded = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
            Mode::HandleYield(y) => y,
            other => {
                self.mode = other;
                return StepEvent::Error(VMError::internal("invalid mode for handle_yield"));
            }
        };
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
                outside_scope,
            } => self.handle_yield_create_continuation(
                expr,
                handlers,
                handler_identities,
                outside_scope,
            ),
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
                bindings,
                metadata,
            } => self.handle_yield_eval_in_scope(expr, scope, bindings, metadata),
            DoCtrl::AllocVar { initial } => self.handle_yield_alloc_var(initial),
            DoCtrl::ReadVar { var } => self.handle_yield_read_var(var),
            DoCtrl::WriteVar { var, value } => self.handle_yield_write_var(var, value),
            DoCtrl::WriteVarNonlocal { var, value } => {
                self.handle_yield_write_var_nonlocal(var, value)
            }
            DoCtrl::ReadHandlerState {
                key,
                missing_is_none,
            } => self.handle_yield_read_handler_state(key, missing_is_none),
            DoCtrl::WriteHandlerState { key, value } => {
                self.handle_yield_write_handler_state(key, value)
            }
            DoCtrl::AppendHandlerLog { message } => self.handle_yield_append_handler_log(message),
            DoCtrl::GetCallStack => self.handle_yield_get_call_stack(),
        }
    }

    fn handle_yield_pure(&mut self, value: Value) -> StepEvent {
        self.mode = Mode::Deliver(value);
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
        self.handle_dispatch_resume(continuation, value)
    }

    fn handle_yield_transfer(&mut self, continuation: Continuation, value: Value) -> StepEvent {
        self.handle_dispatch_transfer(continuation, value)
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
        outside_scope: Option<SegmentId>,
    ) -> StepEvent {
        let metadata = if outside_scope.is_some() {
            None
        } else {
            self.nearest_auto_unwrap_programlike_metadata()
        };
        self.handle_create_continuation(expr, handlers, handler_identities, metadata, outside_scope)
    }

    fn handle_yield_resume_continuation(
        &mut self,
        continuation: OwnedControlContinuation,
        value: Value,
    ) -> StepEvent {
        self.handle_resume_continuation(continuation, value)
    }

    fn handle_yield_python_async_syntax_escape(&mut self, action: PyShared) -> StepEvent {
        self.pending_python = Some(PendingPython::AsyncEscape);
        StepEvent::NeedsPython(PythonCall::CallAsync {
            func: action,
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
                self.set_contextual_throw(PyException::type_error(format!(
                    "DoCtrl::Apply f must be a pure callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        let func = match f_value {
            Value::Python(func) => func,
            Value::Kleisli(kleisli) => {
                let args_values = Self::collect_value_args(args);
                let kwargs_values = Self::collect_value_kwargs(kwargs);
                if !kwargs_values.is_empty() {
                    self.set_contextual_throw(PyException::type_error(
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
                self.set_contextual_throw(PyException::type_error(format!(
                    "DoCtrl::Apply f must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        self.pending_python = Some(PendingPython::CallFuncReturn);
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
                self.set_contextual_throw(PyException::type_error(format!(
                    "DoCtrl::Expand factory must be a pure callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        let (func, handler_return) = match factory_value {
            Value::Python(factory) => (factory, false),
            Value::Kleisli(kleisli) => {
                let args_values = Self::collect_value_args(args);
                let kwargs_values = Self::collect_value_kwargs(kwargs);
                if !kwargs_values.is_empty() {
                    self.set_contextual_throw(PyException::type_error(
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
                            let has_active_dispatch = self
                                .current_dispatch_id()
                                .or_else(|| self.current_active_handler_dispatch_id())
                                .is_some();
                            if has_active_dispatch {
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
                self.set_contextual_throw(PyException::type_error(format!(
                    "DoCtrl::Expand factory must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        self.pending_python = Some(PendingPython::ExpandReturn {
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
        if let Err(err) = self.push_program_frame(stream, metadata, handler_kind) {
            return StepEvent::Error(err);
        }
        self.mode = Mode::Deliver(Value::Unit);
        StepEvent::Continue
    }

    fn handle_yield_eval(&mut self, expr: PyShared, metadata: Option<CallMetadata>) -> StepEvent {
        if let Some((stream, metadata, handler_kind)) = self
            .current_seg()
            .frames
            .iter()
            .rev()
            .find_map(|frame| match frame {
                Frame::Program {
                    stream,
                    metadata: Some(metadata),
                    handler_kind,
                    ..
                } => Some((stream.clone(), metadata.clone(), *handler_kind)),
                Frame::LexicalScope { .. } => None,
                Frame::Program { .. }
                | Frame::InterceptorApply(_)
                | Frame::InterceptorEval(_)
                | Frame::EvalReturn(_)
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. }
                | Frame::InterceptBodyReturn { .. } => None,
            })
        {
            self.emit_frame_location(&stream, &metadata, handler_kind);
        }
        let cont = OwnedControlContinuation::Pending(PendingContinuation::create_with_metadata(
            expr,
            Vec::new(),
            Vec::new(),
            metadata,
            self.current_segment,
        ));
        self.handle_resume_continuation(cont, Value::None)
    }

    pub(super) fn handle_yield_eval_in_scope(
        &mut self,
        expr: PyShared,
        scope: Continuation,
        bindings: HashMap<HashedPyKey, Value>,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let Some(current_seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal(
                "EvalInScope called without current segment",
            ));
        };
        let Some(_current_seg) = self.segments.get(current_seg_id) else {
            return StepEvent::Error(VMError::internal("EvalInScope current segment not found"));
        };
        let current_dispatch_id = self.current_segment_dispatch_id();
        let captured_caller = self.parent_segment(current_seg_id);
        let mut return_to =
            Continuation::from_fiber(current_seg_id, captured_caller, current_dispatch_id);
        self.annotate_live_continuation(&mut return_to, current_seg_id);
        let Some(scope_parent_seg_id) = self.eval_in_scope_chain_start_segment(&scope) else {
            return StepEvent::Error(VMError::internal(
                "EvalInScope received scope from unknown segment",
            ));
        };
        let mut child_seg = Segment::new(Marker::fresh(), Some(scope_parent_seg_id));
        child_seg.push_frame(Frame::LexicalScope {
            bindings,
            var_overrides: HashMap::new(),
        });
        child_seg.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::EvalInScopeReturn {
                continuation: return_to,
            },
        )));
        let child_seg_id = self.alloc_segment(child_seg);
        self.inherit_interceptor_guard_state(Some(current_seg_id), child_seg_id);

        self.current_segment = Some(child_seg_id);
        self.pending_python = Some(PendingPython::EvalExpr { metadata });
        StepEvent::NeedsPython(PythonCall::EvalExpr { expr })
    }

    fn handle_yield_alloc_var(&mut self, initial: Value) -> StepEvent {
        let Some(seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal("AllocVar called without current segment"));
        };
        let var = self.alloc_scoped_var_in_segment(seg_id, initial);
        self.mode = Mode::Deliver(Value::Var(var));
        StepEvent::Continue
    }

    fn handle_yield_read_var(&mut self, var: VarId) -> StepEvent {
        let Some(seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal("ReadVar called without current segment"));
        };
        let Some(value) = self.read_scoped_var_from(seg_id, var) else {
            return StepEvent::Error(VMError::internal(format!(
                "ReadVar could not find variable {} in lexical scope",
                var.raw()
            )));
        };
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    fn handle_yield_write_var(&mut self, var: VarId, value: Value) -> StepEvent {
        let Some(seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal("WriteVar called without current segment"));
        };
        if !self.write_scoped_var_in_current_segment(seg_id, var, value) {
            return StepEvent::Error(VMError::internal("WriteVar target segment not found"));
        }
        self.mode = Mode::Deliver(Value::Unit);
        StepEvent::Continue
    }

    fn handle_yield_write_var_nonlocal(&mut self, var: VarId, value: Value) -> StepEvent {
        let Some(seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal(
                "WriteVarNonlocal called without current segment",
            ));
        };
        if !self.write_scoped_var_nonlocal(seg_id, var, value) {
            return StepEvent::Error(VMError::internal(format!(
                "WriteVarNonlocal could not find owner scope {} for variable {}",
                var.owner_segment().index(),
                var.raw()
            )));
        }
        self.mode = Mode::Deliver(Value::Unit);
        StepEvent::Continue
    }

    fn handle_yield_read_handler_state(&mut self, key: String, missing_is_none: bool) -> StepEvent {
        let Some(value) = self
            .var_store
            .get(&key)
            .cloned()
            .or_else(|| missing_is_none.then_some(Value::None))
        else {
            self.set_contextual_throw(Self::missing_state_key_exception(&key));
            return StepEvent::Continue;
        };
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    fn handle_yield_write_handler_state(&mut self, key: String, value: Value) -> StepEvent {
        let key_for_shadow = key.clone();
        let value_for_shadow = value.clone();
        self.var_store.put(key, value);
        if let Some((_, _, _, _, prompt_seg_id)) = self.current_live_handler_dispatch() {
            let _ = self.write_handler_state_at(prompt_seg_id, key_for_shadow, value_for_shadow);
        }
        self.mode = Mode::Deliver(Value::Unit);
        StepEvent::Continue
    }

    fn handle_yield_append_handler_log(&mut self, message: Value) -> StepEvent {
        let Some((_, _, _, _, prompt_seg_id)) = self.current_live_handler_dispatch() else {
            return StepEvent::Error(VMError::internal(
                "AppendHandlerLog called outside handler dispatch",
            ));
        };
        if !self.append_handler_log_at(prompt_seg_id, message) {
            return StepEvent::Error(VMError::internal("handler log prompt segment not found"));
        }
        self.mode = Mode::Deliver(Value::Unit);
        StepEvent::Continue
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
                        Frame::LexicalScope { .. } => {
                            // lexical scope frames carry bindings, not call metadata
                        }
                        Frame::Program { metadata: None, .. } => {
                            // no metadata
                        }
                        Frame::InterceptorApply(continuation)
                        | Frame::InterceptorEval(continuation) => {
                            if let Some(metadata) = continuation.emitter_metadata.as_ref() {
                                stack.push(metadata.clone());
                            }
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
                            EvalReturnContinuation::ResumeToContinuation { .. } => {
                                // no metadata
                            }
                            EvalReturnContinuation::ReturnToContinuation { .. } => {
                                // no metadata
                            }
                            EvalReturnContinuation::EvalInScopeReturn { .. } => {
                                // no metadata
                            }
                            EvalReturnContinuation::TailResumeReturn => {
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
                seg_id = seg.parent;
            } else {
                break;
            }
        }
        self.mode = Mode::Deliver(Value::CallStack(stack));
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
                | non_pure @ DoCtrl::AllocVar { .. }
                | non_pure @ DoCtrl::ReadVar { .. }
                | non_pure @ DoCtrl::WriteVar { .. }
                | non_pure @ DoCtrl::WriteVarNonlocal { .. }
                | non_pure @ DoCtrl::ReadHandlerState { .. }
                | non_pure @ DoCtrl::WriteHandlerState { .. }
                | non_pure @ DoCtrl::AppendHandlerLog { .. }
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
                | non_pure @ DoCtrl::AllocVar { .. }
                | non_pure @ DoCtrl::ReadVar { .. }
                | non_pure @ DoCtrl::WriteVar { .. }
                | non_pure @ DoCtrl::WriteVarNonlocal { .. }
                | non_pure @ DoCtrl::ReadHandlerState { .. }
                | non_pure @ DoCtrl::WriteHandlerState { .. }
                | non_pure @ DoCtrl::AppendHandlerLog { .. }
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
        let value = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
            Mode::Return(v) => v,
            other => {
                self.mode = other;
                return StepEvent::Error(VMError::internal("invalid mode for return"));
            }
        };

        let seg_id = match self.current_segment {
            Some(id) => id,
            None => return StepEvent::Done(value),
        };

        let caller = self.segments.get(seg_id).and_then(|s| s.parent);
        match caller {
            Some(caller_id) => {
                if self.segments.get(caller_id).is_none() {
                    return StepEvent::Error(VMError::invalid_segment(
                        "caller segment not found in step_return",
                    ));
                }
                self.reparent_children(seg_id, Some(caller_id));
                self.current_segment = Some(caller_id);
                self.free_segment(seg_id);
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            None => {
                self.completed_segment = Some(seg_id);
                self.current_segment = None;
                StepEvent::Done(value)
            }
        }
    }

    pub fn receive_python_result(&mut self, outcome: PyCallOutcome) -> Result<(), VMError> {
        let Some(seg_id) = self.current_segment else {
            return Err(VMError::internal(
                "receive_python_result called without current segment",
            ));
        };

        let pending = match self.pending_python.take() {
            Some(p) => p,
            None => {
                let mode = self.contextual_internal_throw_mode(PyException::runtime_error(
                    "receive_python_result called with no pending_python",
                ));
                if self.segments.get(seg_id).is_none() {
                    self.current_segment = None;
                    return Err(VMError::internal(
                        "receive_python_result called without current segment",
                    ));
                }
                // We record the internal throw on the live segment here; the driver observes it
                // on the next step() rather than via receive_python_result's return value.
                self.mode = mode;
                return Ok(());
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
                incoming_throw,
            } => self.receive_step_user_generator_result(
                stream,
                metadata,
                handler_kind,
                incoming_throw,
                outcome,
            ),
            PendingPython::RustProgramContinuation { marker, k } => {
                self.receive_rust_program_result(marker, k, outcome)
            }
            PendingPython::AsyncEscape => self.receive_async_escape_result(outcome),
        }

        Ok(())
    }

    fn receive_eval_expr_result(&mut self, metadata: Option<CallMetadata>, outcome: PyCallOutcome) {
        match outcome {
            PyCallOutcome::GenYield(yielded) => {
                self.mode = Mode::HandleYield(yielded);
            }
            PyCallOutcome::GenError(exception) => {
                if let Some(ref m) = metadata {
                    self.emit_frame_exited_due_to_error(None, m, None, &exception);
                }
                self.mode = self.mode_after_generror(GenErrorSite::EvalExpr, exception, false);
            }
            PyCallOutcome::GenReturn(value) | PyCallOutcome::Value(value) => {
                if metadata.is_some() && matches!(value, Value::Python(_)) {
                    match self.classify_expand_result_as_doctrl(metadata, value, "EvalExpr") {
                        Ok(doctrl) => {
                            self.mode = Mode::HandleYield(doctrl);
                        }
                        Err(exception) => {
                            self.mode = Mode::Throw(exception);
                        }
                    }
                } else {
                    self.mode = Mode::Deliver(value);
                }
            }
        }
    }

    fn receive_call_func_result(&mut self, outcome: PyCallOutcome) {
        match outcome {
            PyCallOutcome::Value(value) => {
                self.mode = Mode::Deliver(value);
            }
            PyCallOutcome::GenError(exception) => {
                if self.has_live_handler_program_frame() {
                    self.mode = Mode::Throw(exception);
                    return;
                }
                self.mode =
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
        Some(PyException::handler_protocol_error(format!(
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
                if let Some(ref m) = metadata {
                    self.emit_frame_exited(m);
                }
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
                        let Some(_dispatch_id) = self
                            .current_dispatch_id()
                            .or_else(|| self.current_active_handler_dispatch_id())
                        else {
                            self.set_contextual_internal_throw(PyException::runtime_error(
                                "handler dispatch continuation outside dispatch",
                            ));
                            return;
                        };
                        match self.handle_yield_ir_stream(
                            stream,
                            metadata,
                            Some(HandlerKind::Python),
                        ) {
                            StepEvent::Continue => {}
                            StepEvent::Error(err) => {
                                self.set_contextual_internal_throw(PyException::runtime_error(
                                    err.to_string(),
                                ));
                            }
                            StepEvent::NeedsPython(_) | StepEvent::Done(_) => {
                                self.set_contextual_internal_throw(PyException::runtime_error(
                                    "unexpected StepEvent from handle_yield_ir_stream",
                                ));
                            }
                        }
                    }
                    Err(exception) => {
                        self.set_contextual_throw(exception);
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
                self.mode = Mode::HandleYield(doctrl);
            }
            Err(exception) => {
                self.set_contextual_throw(exception);
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
                    Self::extract_doeff_generator(result_obj.clone(), metadata, context)?;
                return Ok(DoCtrl::IRStream { stream, metadata });
            }
            if bound.is_instance_of::<PyDoExprBase>() || bound.is_instance_of::<PyEffectBase>() {
                let yielded = classify_yielded_for_vm(self, py, bound)?;
                return Ok(self.propagate_auto_unwrap_programlike(metadata, yielded));
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

    fn propagate_auto_unwrap_programlike(
        &self,
        inherited: Option<CallMetadata>,
        yielded: DoCtrl,
    ) -> DoCtrl {
        let Some(inherited) = inherited else {
            return yielded;
        };
        if !inherited.auto_unwrap_programlike {
            return yielded;
        }

        match yielded {
            DoCtrl::Apply {
                f,
                args,
                kwargs,
                mut metadata,
            } => {
                metadata.auto_unwrap_programlike = true;
                DoCtrl::Apply {
                    f,
                    args,
                    kwargs,
                    metadata,
                }
            }
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                mut metadata,
            } => {
                metadata.auto_unwrap_programlike = true;
                DoCtrl::Expand {
                    factory,
                    args,
                    kwargs,
                    metadata,
                }
            }
            DoCtrl::IRStream { stream, metadata } => {
                let metadata = Some(match metadata {
                    Some(mut metadata) => {
                        metadata.auto_unwrap_programlike = true;
                        metadata
                    }
                    None => inherited,
                });
                DoCtrl::IRStream { stream, metadata }
            }
            DoCtrl::WithHandler {
                handler,
                body,
                types,
            } => DoCtrl::WithHandler {
                handler,
                body: Box::new(self.propagate_auto_unwrap_programlike(Some(inherited), *body)),
                types,
            },
            DoCtrl::WithIntercept {
                interceptor,
                body,
                types,
                mode,
                metadata,
            } => DoCtrl::WithIntercept {
                interceptor,
                body: Box::new(
                    self.propagate_auto_unwrap_programlike(Some(inherited.clone()), *body),
                ),
                types,
                mode,
                metadata: metadata.map(|mut metadata| {
                    metadata.auto_unwrap_programlike = true;
                    metadata
                }),
            },
            other => other,
        }
    }

    fn receive_expand_gen_error(&mut self, handler_return: bool, exception: PyException) {
        if self.has_live_handler_program_frame() {
            self.mode = Mode::Throw(exception);
            return;
        }
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
            }
            self.mode =
                self.mode_after_generror(GenErrorSite::ExpandReturnHandler, exception, false);
            return;
        }

        self.mode = self.mode_after_generror(GenErrorSite::ExpandReturnProgram, exception, false);
    }

    fn receive_step_user_generator_result(
        &mut self,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        incoming_throw: Option<PyException>,
        outcome: PyCallOutcome,
    ) {
        let user_continuation_dispatch_id = self.user_continuation_dispatch_for_stream(&stream);
        let effective_handler_kind = user_continuation_dispatch_id
            .map(|_| None)
            .unwrap_or(handler_kind);
        match outcome {
            PyCallOutcome::GenYield(yielded) => {
                if incoming_throw.is_some() {
                    self.trace_state.clear_preserved_error_frames();
                }
                if self.current_segment.is_none() {
                    return;
                }
                self.propagate_auto_unwrap_program_context_to_yielded(metadata.as_ref(), &yielded);
                let _ = self.handle_stream_yield(yielded, stream, metadata, effective_handler_kind);
            }
            PyCallOutcome::GenReturn(value) => {
                if incoming_throw.is_some() {
                    self.trace_state.clear_preserved_error_frames();
                }
                if let Some(ref m) = metadata {
                    self.emit_frame_exited(m);
                }
                if effective_handler_kind == Some(HandlerKind::Python)
                    && self.should_treat_python_handler_gen_return_as_handler_completion()
                {
                    if let Some(exception) = Self::returned_control_primitive_exception(&value) {
                        self.set_contextual_throw(exception);
                        return;
                    }
                    let _ = self.handle_handler_return(value);
                } else {
                    self.mode = Mode::Deliver(value);
                }
            }
            PyCallOutcome::GenError(exception) => {
                if let Some(ref m) = metadata {
                    self.emit_frame_exited_due_to_error(
                        Some(&stream),
                        m,
                        effective_handler_kind,
                        &exception,
                    );
                }
                if self.should_throw_into_live_handler_frame(effective_handler_kind) {
                    let propagated_throw = incoming_throw
                        .as_ref()
                        .is_some_and(|original| Self::same_exception(original, &exception));
                    if !propagated_throw {
                        if let Some(dispatch_id) = self
                            .current_active_handler_dispatch_id()
                            .or_else(|| self.current_segment_dispatch_id_any())
                        {
                            if let Some(original) =
                                self.original_exception_for_dispatch(dispatch_id)
                            {
                                TraceState::set_exception_cause(&exception, &original);
                            }
                            self.emit_handler_threw_for_dispatch(dispatch_id, &exception);
                        }
                    }
                    self.mode = Mode::Throw(exception);
                    return;
                }
                if let Some(dispatch_id) = self
                    .current_active_handler_dispatch_id()
                    .or_else(|| self.current_segment_dispatch_id_any())
                {
                    let propagated_throw = incoming_throw
                        .as_ref()
                        .is_some_and(|original| Self::same_exception(original, &exception));
                    if effective_handler_kind.is_some()
                        && !self.dispatch_uses_user_continuation_stream(dispatch_id, &stream)
                        && !propagated_throw
                    {
                        if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                            TraceState::set_exception_cause(&exception, &original);
                        }
                        self.emit_handler_threw_for_dispatch(dispatch_id, &exception);
                    }
                }
                if let Some(continuation) =
                    self.handler_stream_throw_continuation(&stream, handler_kind)
                {
                    self.mode = Mode::HandleYield(DoCtrl::TransferThrow {
                        continuation,
                        exception,
                    });
                    return;
                }

                let mut site = GenErrorSite::StepUserGeneratorDirect;
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
                    let propagated_throw = incoming_throw
                        .as_ref()
                        .is_some_and(|original| Self::same_exception(original, &exception));
                    if self.dispatch_uses_user_continuation_stream(dispatch_id, &stream)
                        || propagated_throw
                    {
                        site = GenErrorSite::StepUserGeneratorConverted;
                    } else if self.current_segment_is_active_handler_for_dispatch(dispatch_id) {
                        if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                            TraceState::set_exception_cause(&exception, &original);
                        }
                        self.emit_handler_threw_for_dispatch(dispatch_id, &exception);
                    } else if effective_handler_kind.is_some()
                        && self
                            .current_segment_dispatch_id_any()
                            .is_some_and(|current_dispatch_id| current_dispatch_id == dispatch_id)
                    {
                        if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
                            TraceState::set_exception_cause(&exception, &original);
                        }
                        self.emit_handler_threw_for_dispatch(dispatch_id, &exception);
                    }
                }
                if effective_handler_kind.is_none() {
                    self.mode = Mode::Throw(exception);
                    return;
                }
                self.mode = self.mode_after_generror(site, exception, false);
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
                self.mode = Mode::Deliver(result);
            }
            PyCallOutcome::GenError(exception) => {
                self.mode = Mode::Throw(exception);
            }
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_async_escape_result(&mut self, outcome: PyCallOutcome) {
        match outcome {
            PyCallOutcome::Value(result) => {
                self.mode = Mode::Deliver(result);
            }
            PyCallOutcome::GenError(exception) => {
                self.mode = self.mode_after_generror(GenErrorSite::AsyncEscape, exception, false);
            }
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_unexpected_outcome(&mut self) {
        self.set_contextual_internal_throw(PyException::runtime_error(
            "unexpected pending/outcome combination in receive_python_result",
        ));
    }
}
