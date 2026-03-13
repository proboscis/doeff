use super::*;

impl VM {
    /// Set mode to Throw with a RuntimeError and return Continue.
    pub(super) fn throw_runtime_error(&mut self, message: &str) -> StepEvent {
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal(
                "throw_runtime_error called without current segment",
            ));
        };
        seg.mode = Mode::Throw(PyException::runtime_error(message.to_string()));
        StepEvent::Continue
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
        seg.mode = Mode::HandleYield(expr);
        StepEvent::Continue
    }

    pub(super) fn evaluate(&mut self, ir_node: DoCtrl) -> StepEvent {
        self.current_seg_mut().mode = Mode::HandleYield(ir_node);
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
            )),
        }
    }

    fn extract_doeff_generator(
        value: Py<PyAny>,
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

            let stream = Arc::new(std::sync::Mutex::new(Box::new(PythonGeneratorStream::new(
                PyShared::new(wrapped.generator.clone_ref(py)),
                PyShared::new(wrapped.get_frame.clone_ref(py)),
            )) as Box<dyn IRStream>));
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
            let Some(seg) = self.current_segment_ref() else {
                return StepEvent::Error(VMError::internal("no current segment"));
            };
            match &seg.mode {
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
                let caller = segment.caller;
                let mode =
                    std::mem::replace(&mut self.current_seg_mut().mode, Mode::Deliver(Value::Unit));
                match mode {
                    Mode::Deliver(value) => {
                        // Don't free here — step_return reads the segment's caller.
                        self.current_seg_mut().mode = Mode::Return(value);
                        return StepEvent::Continue;
                    }
                    Mode::Throw(exc) => {
                        if let Some(caller_id) = caller {
                            self.segments.reparent_children(seg_id, Some(caller_id));
                            self.current_segment = Some(caller_id);
                            self.current_seg_mut().mode = Mode::Throw(exc);
                            self.segments.free(seg_id);
                            return StepEvent::Continue;
                        } else {
                            self.finalize_active_dispatches_as_threw(&exc);
                            let trace = self.assemble_traceback_entries(&exc);
                            let active_chain = self.assemble_active_chain(Some(&exc));
                            self.segments.reparent_children(seg_id, None);
                            self.segments.free(seg_id);
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

            // Take mode by move — each branch sets segment.mode before returning.
            let mode = std::mem::replace(&mut segment.mode, Mode::Deliver(Value::Unit));
            (frame, mode)
        };

        match frame {
            Frame::Program {
                stream,
                metadata,
                handler_kind,
            } => {
                let incoming_throw = match &mode {
                    Mode::Throw(exc) => Some(exc.clone()),
                    Mode::Deliver(_) | Mode::HandleYield(_) | Mode::Return(_) => None,
                };
                let step = {
                    let Some(seg) = self.segments.get_mut(seg_id) else {
                        return StepEvent::Error(VMError::invalid_segment("segment not found"));
                    };
                    let scope = &mut seg.scope_store;
                    let mut guard = stream.lock().expect("IRStream lock poisoned");
                    match mode {
                        Mode::Deliver(value) => guard.resume(value, &mut self.rust_store, scope),
                        Mode::Throw(exc) => guard.throw(exc, &mut self.rust_store, scope),
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
            Frame::InterceptorApply(cont) => self.step_interceptor_apply_frame(*cont, mode),
            Frame::InterceptorEval(cont) => self.step_interceptor_eval_frame(*cont, mode),
            Frame::HandlerDispatch {
                dispatch_id,
                continuation,
                ..
            } => self.step_handler_dispatch_frame(dispatch_id, continuation, mode),
            Frame::DispatchOrigin {
                dispatch_id,
                k_origin,
                ..
            } => self.step_dispatch_origin_frame(dispatch_id, k_origin, mode),
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

    fn same_exception(lhs: &PyException, rhs: &PyException) -> bool {
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
                },
                PyException::RuntimeError {
                    message: rhs_message,
                },
            )
            | (
                PyException::TypeError {
                    message: lhs_message,
                },
                PyException::TypeError {
                    message: rhs_message,
                },
            ) => lhs_message == rhs_message,
            (
                PyException::Materialized { .. },
                PyException::RuntimeError { .. } | PyException::TypeError { .. },
            )
            | (
                PyException::RuntimeError { .. } | PyException::TypeError { .. },
                PyException::Materialized { .. },
            )
            | (PyException::RuntimeError { .. }, PyException::TypeError { .. })
            | (PyException::TypeError { .. }, PyException::RuntimeError { .. })
            | (PyException::TypeError { .. }, PyException::TypeError { .. }) => false,
        }
    }

    fn chain_exception_context(
        original_exception: &PyException,
        cleanup_exception: &PyException,
    ) {
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
            let seg = self.current_seg_mut();
            seg.interceptor_eval_depth = seg.interceptor_eval_depth.saturating_sub(1);
        }
        if let Some(metadata) = continuation.interceptor_metadata.as_ref() {
            self.emit_frame_exited(metadata);
        }
        match mode {
            Mode::Deliver(value) => {
                self.current_seg_mut().mode =
                    self.handle_interceptor_apply_result(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.pop_interceptor_skip(continuation.marker);
                self.current_seg_mut().mode = Mode::Throw(exc);
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
        let seg = self.current_seg_mut();
        seg.interceptor_eval_depth = seg.interceptor_eval_depth.saturating_sub(1);
        match mode {
            Mode::Deliver(value) => {
                self.current_seg_mut().mode =
                    self.handle_interceptor_eval_result(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.pop_interceptor_skip(continuation.marker);
                self.current_seg_mut().mode = Mode::Throw(exc);
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

    fn step_handler_dispatch_frame(
        &mut self,
        dispatch_id: DispatchId,
        continuation: Continuation,
        mode: Mode,
    ) -> StepEvent {
        match mode {
            Mode::Deliver(mut value) => {
                let marker = self.current_seg().marker;
                let is_python_handler = self
                    .marker_handler_trace_info(marker)
                    .is_some_and(|(_, kind, _, _)| kind == HandlerKind::Python);
                if is_python_handler
                    && self
                        .continuation_registry
                        .contains_key(&continuation.cont_id)
                    && !self.is_one_shot_consumed(continuation.cont_id)
                {
                    self.mark_one_shot_consumed(continuation.cont_id);
                    return self.throw_runtime_error(&format!(
                        "handler returned without consuming continuation {}; use Resume(k, v), Transfer(k, v), Discontinue(k, exn), or Pass()",
                        continuation.cont_id.raw(),
                    ));
                }

                if let Err(err) = self
                    .maybe_attach_active_chain_to_execution_context(Some(dispatch_id), &mut value)
                {
                    return StepEvent::Error(err);
                }

                if let Some((handler_index, handler_name)) =
                    self.current_handler_identity_for_dispatch(dispatch_id)
                {
                    let value_repr = Self::value_repr(&value);
                    self.trace_state.emit_handler_completed(
                        dispatch_id,
                        handler_name.clone(),
                        handler_index,
                        HandlerAction::Returned {
                            value_repr: value_repr.clone(),
                        },
                    );
                    self.emit_resume_event(
                        dispatch_id,
                        handler_name,
                        value_repr,
                        &continuation,
                        false,
                    );
                }

                self.current_seg_mut().mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                if self.is_one_shot_consumed(continuation.cont_id) {
                    self.current_seg_mut().mode = Mode::Throw(exc);
                } else {
                    self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::TransferThrow {
                        continuation,
                        exception: exc,
                    });
                }
                StepEvent::Continue
            }
            Mode::HandleYield(yielded) => {
                unreachable!("handler dispatch frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                unreachable!("handler dispatch frame received Return mode: {value:?}")
            }
        }
    }

    fn step_dispatch_origin_frame(
        &mut self,
        dispatch_id: DispatchId,
        k_origin: Continuation,
        mode: Mode,
    ) -> StepEvent {
        match mode {
            Mode::Deliver(value) => {
                if let Some(original) = k_origin.pending_error_context {
                    self.current_seg_mut().mode =
                        match Self::enrich_original_exception_with_context(original, value) {
                            Ok(exception) => Mode::Throw(exception),
                            Err(effect_err) => Mode::Throw(effect_err),
                        };
                    return StepEvent::Continue;
                }
                self.current_seg_mut().mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            Mode::HandleYield(yielded) => {
                unreachable!("dispatch origin frame received HandleYield mode: {yielded:?}")
            }
            Mode::Return(value) => {
                return self.throw_runtime_error(&format!(
                    "handler returned without consuming continuation before dispatch {} completed: {:?}",
                    dispatch_id.raw(),
                    value,
                ))
            }
        }
    }

    fn step_eval_return_frame(
        &mut self,
        continuation: EvalReturnContinuation,
        mode: Mode,
    ) -> StepEvent {
        if let EvalReturnContinuation::EvalInScopeReturn { continuation } = continuation {
            self.current_seg_mut().mode = match mode {
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
        match mode {
            Mode::Deliver(value) => {
                self.current_seg_mut().mode =
                    self.mode_from_eval_return_continuation(continuation, value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
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
                    return Mode::Throw(PyException::runtime_error(
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
                    return Mode::Throw(PyException::runtime_error(
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
                    return Mode::Throw(PyException::runtime_error(
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
                    return Mode::Throw(PyException::runtime_error(
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
            EvalReturnContinuation::EvalInScopeReturn { .. } => {
                unreachable!("EvalInScopeReturn continuation is handled before value dispatch")
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
                self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::Apply {
                    f: Box::new(DoCtrl::Pure {
                        value: Value::Python(mapper.into_inner()),
                    }),
                    args: vec![DoCtrl::Pure { value }],
                    kwargs: vec![],
                    metadata: mapper_meta,
                });
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
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
                self.current_seg_mut().mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
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
                seg.mode = Mode::HandleYield(DoCtrl::Expand {
                    factory: Box::new(DoCtrl::Pure {
                        value: Value::Python(binder.into_inner()),
                    }),
                    args: vec![DoCtrl::Pure { value }],
                    kwargs: vec![],
                    metadata: binder_meta,
                });
                StepEvent::Continue
            }
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
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

    fn step_intercept_body_return_frame(
        &mut self,
        _marker: Marker,
        mode: Mode,
    ) -> StepEvent {
        match mode {
            Mode::Deliver(value) => self.handle_handler_return(value),
            Mode::Throw(exc) => {
                self.current_seg_mut().mode = Mode::Throw(exc);
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
                self.handle_stream_yield(yielded, stream, metadata, handler_kind)
            }
            IRStreamStep::Return(value) => {
                if let Some(ref m) = metadata {
                    self.emit_frame_exited(m);
                }
                self.handle_handler_return(value)
            }
            IRStreamStep::Throw(exc) => {
                if let Some(continuation) =
                    self.handler_stream_throw_continuation(&stream, handler_kind)
                {
                    self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::TransferThrow {
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
                self.current_seg_mut().mode = Mode::Throw(exc);
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
                    self.current_seg_mut().pending_python =
                        Some(PendingPython::StepUserGenerator {
                            stream,
                            metadata,
                            handler_kind,
                            incoming_throw,
                        });
                    return StepEvent::NeedsPython(call);
                }

                let Some(seg) = self.current_segment_mut() else {
                    return StepEvent::Error(VMError::internal(
                        "current_segment_mut() returned None in apply_stream_step \
                         (NeedsPython rust continuation)",
                    ));
                };
                seg.push_frame(Frame::Program {
                    stream,
                    metadata,
                    handler_kind,
                });
                let Some(dispatch_id) = self.current_dispatch_id() else {
                    return StepEvent::Error(VMError::internal(
                        "RustProgramContinuation outside dispatch",
                    ));
                };
                let Some((_, k, marker)) = self.active_handler_dispatch_for(dispatch_id) else {
                    return StepEvent::Error(VMError::internal(
                        "RustProgramContinuation: active handler dispatch not found",
                    ));
                };
                self.current_seg_mut().pending_python =
                    Some(PendingPython::RustProgramContinuation { marker, k });
                StepEvent::NeedsPython(call)
            }
        }
    }

    fn handle_stream_yield(
        &mut self,
        yielded: DoCtrl,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
    ) -> StepEvent {
        let chain = Arc::new(self.current_interceptor_chain());
        self.current_seg_mut().mode =
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
            match self.current_segment_mut() {
                Some(seg) => seg.push_frame(Frame::Program {
                    stream,
                    metadata,
                    handler_kind,
                }),
                None => {
                    return Mode::Throw(PyException::runtime_error(
                        "current_segment_mut() returned None in apply_stream_step \
                         (Yield non-terminal)",
                    ))
                }
            }
        }
        Mode::HandleYield(yielded)
    }

    fn current_interceptor_chain(&self) -> Vec<Marker> {
        let dispatch_origin_callers = self
            .dispatch_origins()
            .into_iter()
            .map(|origin| origin.k_origin.segment_id)
            .collect::<Vec<_>>();
        self.interceptor_state.current_chain(
            self.current_segment,
            &self.segments,
            &dispatch_origin_callers,
        )
    }

    fn interceptor_visible_to_active_handler(&self, interceptor_marker: Marker) -> bool {
        self.interceptor_state
            .visible_to_active_handler(interceptor_marker)
    }

    fn is_interceptor_skipped(&self, marker: Marker) -> bool {
        InterceptorState::is_skipped(self.current_seg(), marker)
    }

    fn pop_interceptor_skip(&mut self, marker: Marker) {
        let seg = self.current_seg_mut();
        if InterceptorState::is_skipped(seg, marker) {
            InterceptorState::pop_skip(seg, marker);
        }
    }

    fn push_interceptor_skip(&mut self, marker: Marker) {
        InterceptorState::push_skip(self.current_seg_mut(), marker);
    }

    fn classify_interceptor_result_object(
        &self,
        result_obj: Py<PyAny>,
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
        entry: &InterceptorEntry,
        yielded_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        let Some(types) = entry.types.as_ref() else {
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

    pub(super) fn should_invoke_handler(
        &self,
        entry: &HandlerChainEntry,
        effect_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        let Some(types) = entry.types.as_ref() else {
            return Ok(true);
        };
        if types.is_empty() {
            return Ok(false);
        }

        Ok(Python::attach(|py| -> PyResult<bool> {
            let effect = effect_obj.bind(py);
            let type_tuple = PyTuple::new(py, types.iter().map(|ty| ty.clone_ref(py)))?;
            effect.is_instance(&type_tuple)
        })?)
    }

    fn continue_interceptor_chain_mode(
        &mut self,
        yielded: DoCtrl,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        chain: Arc<Vec<Marker>>,
        start_idx: usize,
    ) -> Mode {
        let current = yielded;
        let mut idx = start_idx;

        while idx < chain.len() {
            let marker = chain[idx];
            idx += 1;
            if self.is_interceptor_skipped(marker) {
                continue;
            }
            if !self.interceptor_visible_to_active_handler(marker) {
                continue;
            }

            let Some(entry) = self.interceptor_state.get_entry(marker) else {
                continue;
            };

            let yielded_obj = match doctrl_to_pyexpr_for_vm(&current) {
                Ok(Some(obj)) => obj,
                Ok(None) => continue,
                Err(exc) => return Mode::Throw(exc),
            };

            match self.should_invoke_interceptor(&entry, &yielded_obj) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(exc) => return Mode::Throw(exc),
            }

            return self.start_interceptor_invocation_mode(
                marker,
                entry,
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
        )
    }

    fn start_interceptor_invocation_mode(
        &mut self,
        marker: Marker,
        entry: InterceptorEntry,
        yielded: DoCtrl,
        yielded_obj: Py<PyAny>,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        chain: Arc<Vec<Marker>>,
        next_idx: usize,
    ) -> Mode {
        let interceptor_kleisli = entry.interceptor.clone();
        let guard_eval_depth = entry.types.is_some();
        let interceptor_meta = entry.metadata.clone();
        let yielded_obj_for_continuation = Python::attach(|py| yielded_obj.clone_ref(py));
        let apply_metadata = interceptor_meta
            .clone()
            .unwrap_or_else(Self::fallback_interceptor_metadata);
        self.push_interceptor_skip(marker);

        if self.current_segment.is_none() {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        }
        if let Some(meta) = interceptor_meta.as_ref() {
            self.emit_frame_entered(meta, None);
        }
        let continuation = InterceptorContinuation {
            marker,
            original_yielded: yielded,
            original_obj: PyShared::new(yielded_obj_for_continuation),
            emitter_stream: stream,
            emitter_metadata: metadata,
            emitter_handler_kind: handler_kind,
            chain,
            next_idx,
            interceptor_metadata: interceptor_meta,
            guard_eval_depth,
        };
        let Some(seg) = self.current_segment_mut() else {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        };
        if guard_eval_depth {
            seg.interceptor_eval_depth = seg.interceptor_eval_depth.saturating_add(1);
        }
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
            return Mode::Throw(PyException::type_error(
                "WithIntercept interceptor must return DoExpr",
            ));
        };

        let (is_direct_expr, is_doexpr) = InterceptorState::classify_result_shape(&result_obj);

        if is_direct_expr {
            let transformed = match self.classify_interceptor_result_object(
                result_obj,
                &original_obj,
                original_yielded,
            ) {
                Ok(expr) => expr,
                Err(exc) => {
                    self.pop_interceptor_skip(marker);
                    return Mode::Throw(exc);
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
            let Some(seg) = self.current_segment_mut() else {
                self.pop_interceptor_skip(marker);
                return Mode::Throw(PyException::runtime_error(
                    "current_segment_mut() returned None while evaluating interceptor result",
                ));
            };
            seg.interceptor_eval_depth = seg.interceptor_eval_depth.saturating_add(1);
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
                expr: PyShared::new(result_obj),
                metadata: None,
            });
        }

        self.pop_interceptor_skip(marker);
        Mode::Throw(PyException::type_error(
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
                    return Mode::Throw(exc);
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
        let yielded =
            match std::mem::replace(&mut self.current_seg_mut().mode, Mode::Deliver(Value::Unit)) {
                Mode::HandleYield(y) => y,
                other => {
                    self.current_seg_mut().mode = other;
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
            } => self.handle_yield_create_continuation(expr, handlers, handler_identities),
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
                metadata,
            } => self.handle_yield_eval_in_scope(expr, scope, metadata),
            DoCtrl::GetCallStack => self.handle_yield_get_call_stack(),
        }
    }

    fn handle_yield_pure(&mut self, value: Value) -> StepEvent {
        self.current_seg_mut().mode = Mode::Deliver(value);
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

    fn handle_yield_resume(
        &mut self,
        continuation: Continuation,
        value: Value,
    ) -> StepEvent {
        self.handle_resume(continuation, value)
    }

    fn handle_yield_transfer(
        &mut self,
        continuation: Continuation,
        value: Value,
    ) -> StepEvent {
        self.handle_transfer(continuation, value)
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
    ) -> StepEvent {
        self.handle_create_continuation(expr, handlers, handler_identities)
    }

    fn handle_yield_resume_continuation(
        &mut self,
        continuation: Continuation,
        value: Value,
    ) -> StepEvent {
        self.handle_resume_continuation(continuation, value)
    }

    fn handle_yield_python_async_syntax_escape(
        &mut self,
        action: Py<PyAny>,
    ) -> StepEvent {
        self.current_seg_mut().pending_python = Some(PendingPython::AsyncEscape);
        StepEvent::NeedsPython(PythonCall::CallAsync {
            func: PyShared::new(action),
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
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Apply f must be a pure callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        let func = match f_value {
            Value::Python(func) => PyShared::new(func),
            Value::Kleisli(kleisli) => {
                let args_values = Self::collect_value_args(args);
                let kwargs_values = Self::collect_value_kwargs(kwargs);
                if !kwargs_values.is_empty() {
                    self.current_seg_mut().mode = Mode::Throw(PyException::type_error(
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
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Apply f must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        self.current_seg_mut().pending_python = Some(PendingPython::CallFuncReturn);
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
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Expand factory must be a pure callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        let (func, handler_return) = match factory_value {
            Value::Python(factory) => (PyShared::new(factory), false),
            Value::Kleisli(kleisli) => {
                let args_values = Self::collect_value_args(args);
                let kwargs_values = Self::collect_value_kwargs(kwargs);
                if !kwargs_values.is_empty() {
                    self.current_seg_mut().mode = Mode::Throw(PyException::type_error(
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
                self.current_seg_mut().mode = Mode::Throw(PyException::type_error(format!(
                    "DoCtrl::Expand factory must be Python callable value, got {:?}",
                    other
                )));
                return StepEvent::Continue;
            }
        };

        self.current_seg_mut().pending_python = Some(PendingPython::ExpandReturn {
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
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal(
                "handle_yield_ir_stream called without current segment",
            ));
        };
        seg.push_frame(Frame::Program {
            stream,
            metadata,
            handler_kind,
        });
        seg.mode = Mode::Deliver(Value::Unit);
        StepEvent::Continue
    }

    fn handle_yield_eval(
        &mut self,
        expr: PyShared,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let handlers = self.current_visible_handlers();
        let cont = Continuation::create_unstarted_with_metadata(expr, handlers, metadata);
        self.handle_resume_continuation(cont, Value::None)
    }

    fn handle_yield_eval_in_scope(
        &mut self,
        expr: PyShared,
        scope: Continuation,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let Some(current_seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal(
                "EvalInScope called without current segment",
            ));
        };
        let Some(current_seg) = self.segments.get(current_seg_id) else {
            return StepEvent::Error(VMError::internal("EvalInScope current segment not found"));
        };
        let mut return_to =
            Continuation::capture(current_seg, current_seg_id, current_seg.dispatch_id);
        // Correctness constraint: active interceptor markers are still referenced by
        // live frames in the current dispatch. We must preserve them as-is for the
        // replay chain; remapping them would require remapping those live frames,
        // which are outside the EvalInScope replay scope.
        let mut active_interceptor_markers: HashSet<Marker> =
            current_seg.interceptor_skip_stack.iter().copied().collect();
        active_interceptor_markers.insert(current_seg.marker);

        let Some(replay_chain_start_seg_id) = self.eval_in_scope_chain_start_segment(&scope) else {
            return StepEvent::Error(VMError::internal(
                "EvalInScope received scope from unknown segment",
            ));
        };

        let chain_entries = self.chain_entries_in_caller_chain(replay_chain_start_seg_id);

        // Evaluate in an isolated base segment so active handlers are exactly
        // the scope-site handlers (no duplication with current caller chain),
        // while inheriting dynamic scope/interceptor state from the call site.
        let mut base_seg = Segment::new(Marker::fresh(), None);
        self.copy_interceptor_guard_state(Some(current_seg_id), &mut base_seg);
        self.copy_scope_store_from(Some(current_seg_id), &mut base_seg);
        let base_seg_id = self.alloc_segment(base_seg);
        let mut replay_seg_ids = vec![base_seg_id];

        let mut outside_seg_id = Some(base_seg_id);
        let mut interceptor_marker_remap: HashMap<Marker, Marker> = HashMap::new();
        for entry in chain_entries.into_iter().rev() {
            match entry {
                CallerChainEntry::Handler(entry) => {
                    let handler = entry.handler.clone();
                    let handler_marker = Marker::fresh();
                    let mut prompt_seg = Segment::new_prompt_with_types(
                        handler_marker,
                        outside_seg_id,
                        handler_marker,
                        handler.clone(),
                        entry.types.clone(),
                    );
                    self.copy_interceptor_guard_state(outside_seg_id, &mut prompt_seg);
                    self.copy_scope_store_from(outside_seg_id, &mut prompt_seg);
                    let prompt_seg_id = self.alloc_segment(prompt_seg);
                    replay_seg_ids.push(prompt_seg_id);
                    self.track_run_handler(&handler);

                    let mut body_seg = Segment::new(handler_marker, Some(prompt_seg_id));
                    self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
                    self.copy_scope_store_from(outside_seg_id, &mut body_seg);
                    let body_seg_id = self.alloc_segment(body_seg);
                    replay_seg_ids.push(body_seg_id);
                    outside_seg_id = Some(body_seg_id);
                }
                CallerChainEntry::Interceptor(entry) => {
                    let interceptor_marker = if active_interceptor_markers.contains(&entry.marker) {
                        entry.marker
                    } else {
                        let fresh_marker = Marker::fresh();
                        interceptor_marker_remap.insert(entry.marker, fresh_marker);
                        self.interceptor_state.insert(
                            fresh_marker,
                            entry.interceptor.clone(),
                            entry.types.clone(),
                            entry.mode,
                            entry.metadata.clone(),
                        );
                        fresh_marker
                    };

                    let mut body_seg = Segment::new(interceptor_marker, outside_seg_id);
                    body_seg.kind = SegmentKind::InterceptorBoundary {
                        interceptor: entry.interceptor,
                        types: entry.types,
                        mode: entry.mode,
                        metadata: entry.metadata,
                    };
                    self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
                    self.copy_scope_store_from(outside_seg_id, &mut body_seg);
                    let body_seg_id = self.alloc_segment(body_seg);
                    replay_seg_ids.push(body_seg_id);
                    outside_seg_id = Some(body_seg_id);
                }
            }
        }

        if !interceptor_marker_remap.is_empty() {
            Self::remap_interceptor_markers_in_continuation(
                &mut return_to,
                &interceptor_marker_remap,
            );
            self.remap_interceptor_markers_in_runtime_state(&interceptor_marker_remap);
        }
        let Some(base_seg) = self.segments.get_mut(base_seg_id) else {
            return StepEvent::Error(VMError::invalid_segment(
                "EvalInScope replay base segment not found",
            ));
        };
        base_seg.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::EvalInScopeReturn {
                continuation: return_to,
            },
        )));
        for old_marker in interceptor_marker_remap.keys() {
            self.interceptor_state.remove(*old_marker);
        }

        self.current_segment = outside_seg_id;
        self.current_seg_mut().pending_python = Some(PendingPython::EvalExpr { metadata });
        StepEvent::NeedsPython(PythonCall::EvalExpr { expr })
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
                        Frame::Program { metadata: None, .. } => {
                            // no metadata
                        }
                        Frame::InterceptorApply(continuation)
                        | Frame::InterceptorEval(continuation) => {
                            if let Some(metadata) = continuation.emitter_metadata.as_ref() {
                                stack.push(metadata.clone());
                            }
                        }
                        Frame::HandlerDispatch { .. } | Frame::DispatchOrigin { .. } => {
                            // no metadata
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
                            EvalReturnContinuation::EvalInScopeReturn { .. } => {
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
                seg_id = seg.caller;
            } else {
                break;
            }
        }
        self.current_seg_mut().mode = Mode::Deliver(Value::CallStack(stack));
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
        let value =
            match std::mem::replace(&mut self.current_seg_mut().mode, Mode::Deliver(Value::Unit)) {
                Mode::Return(v) => v,
                other => {
                    self.current_seg_mut().mode = other;
                    return StepEvent::Error(VMError::internal("invalid mode for return"));
                }
            };

        let seg_id = match self.current_segment {
            Some(id) => id,
            None => return StepEvent::Done(value),
        };

        let caller = self.segments.get(seg_id).and_then(|s| s.caller);

        match caller {
            Some(caller_id) => {
                if self.segments.get(caller_id).is_none() {
                    return StepEvent::Error(VMError::invalid_segment(
                        "caller segment not found in step_return",
                    ));
                }
                self.segments.reparent_children(seg_id, Some(caller_id));
                self.current_segment = Some(caller_id);
                self.segments.free(seg_id);
                self.current_seg_mut().mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            None => {
                self.segments.reparent_children(seg_id, None);
                self.segments.free(seg_id);
                StepEvent::Done(value)
            }
        }
    }

    pub fn receive_python_result(&mut self, outcome: PyCallOutcome) {
        let pending = match self
            .current_segment_mut()
            .and_then(|seg| seg.pending_python.take())
        {
            Some(p) => p,
            None => {
                if let Some(seg) = self.current_segment_mut() {
                    seg.mode = Mode::Throw(PyException::runtime_error(
                        "receive_python_result called with no pending_python",
                    ));
                }
                return;
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
    }

    fn receive_eval_expr_result(
        &mut self,
        _metadata: Option<CallMetadata>,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::GenYield(yielded) => {
                self.current_seg_mut().mode = Mode::HandleYield(yielded);
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode =
                    self.mode_after_generror(GenErrorSite::EvalExpr, exception, false);
            }
            PyCallOutcome::GenReturn(value) | PyCallOutcome::Value(value) => {
                self.current_seg_mut().mode = Mode::Deliver(value);
            }
        }
    }

    fn receive_call_func_result(&mut self, outcome: PyCallOutcome) {
        match outcome {
            PyCallOutcome::Value(value) => {
                self.current_seg_mut().mode = Mode::Deliver(value);
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode =
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
        Some(PyException::runtime_error(format!(
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
                self.receive_expand_gen_error(handler_return, exception);
            }
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_expand_handler_value(
        &mut self,
        metadata: Option<CallMetadata>,
        value: Value,
    ) {
        match value {
            Value::Python(handler_gen) => {
                match Self::extract_doeff_generator(handler_gen, metadata, "ExpandReturn(handler)")
                {
                    Ok((stream, metadata)) => {
                        let Some(_dispatch_id) = self
                            .current_dispatch_id()
                            .or_else(|| self.current_active_handler_dispatch_id())
                        else {
                            self.current_seg_mut().mode = Mode::Throw(PyException::runtime_error(
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
                                self.current_seg_mut().mode =
                                    Mode::Throw(PyException::runtime_error(err.to_string()));
                            }
                            StepEvent::NeedsPython(_) | StepEvent::Done(_) => {
                                self.current_seg_mut().mode =
                                    Mode::Throw(PyException::runtime_error(
                                        "unexpected StepEvent from handle_yield_ir_stream",
                                    ));
                            }
                        }
                    }
                    Err(exception) => {
                        self.current_seg_mut().mode = Mode::Throw(exception);
                    }
                }
            }
            other => {
                let _ = self.handle_handler_return(other);
            }
        }
    }

    fn receive_expand_program_value(
        &mut self,
        metadata: Option<CallMetadata>,
        value: Value,
    ) {
        match self.classify_expand_result_as_doctrl(metadata, value, "ExpandReturn") {
            Ok(doctrl) => {
                self.current_seg_mut().mode = Mode::HandleYield(doctrl);
            }
            Err(exception) => {
                self.current_seg_mut().mode = Mode::Throw(exception);
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
                    Self::extract_doeff_generator(result_obj.clone_ref(py), metadata, context)?;
                return Ok(DoCtrl::IRStream { stream, metadata });
            }
            if bound.is_instance_of::<PyDoExprBase>() || bound.is_instance_of::<PyEffectBase>() {
                return classify_yielded_for_vm(self, py, bound);
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

    fn receive_expand_gen_error(
        &mut self,
        handler_return: bool,
        exception: PyException,
    ) {
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
            self.current_seg_mut().mode =
                self.mode_after_generror(GenErrorSite::ExpandReturnHandler, exception, false);
            return;
        }

        self.current_seg_mut().mode =
            self.mode_after_generror(GenErrorSite::ExpandReturnProgram, exception, false);
    }

    fn receive_step_user_generator_result(
        &mut self,
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        incoming_throw: Option<PyException>,
        outcome: PyCallOutcome,
    ) {
        match outcome {
            PyCallOutcome::GenYield(yielded) => {
                if self.current_segment.is_none() {
                    return;
                }
                let _ = self.handle_stream_yield(yielded, stream, metadata, handler_kind);
            }
            PyCallOutcome::GenReturn(value) => {
                if let Some(ref m) = metadata {
                    self.emit_frame_exited(m);
                }
                if handler_kind == Some(HandlerKind::Python) {
                    if let Some(exception) = Self::returned_control_primitive_exception(&value) {
                        self.current_seg_mut().mode = Mode::Throw(exception);
                        return;
                    }
                }
                self.current_seg_mut().mode = Mode::Deliver(value);
            }
            PyCallOutcome::GenError(exception) => {
                if let Some(continuation) =
                    self.handler_stream_throw_continuation(&stream, handler_kind)
                {
                    self.current_seg_mut().mode = Mode::HandleYield(DoCtrl::TransferThrow {
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
                    }
                }
                self.current_seg_mut().mode = self.mode_after_generror(site, exception, false);
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
                self.current_seg_mut().mode = Mode::Deliver(result);
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode = self.mode_after_generror(
                    GenErrorSite::RustProgramContinuation,
                    exception,
                    false,
                );
            }
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_async_escape_result(&mut self, outcome: PyCallOutcome) {
        match outcome {
            PyCallOutcome::Value(result) => {
                self.current_seg_mut().mode = Mode::Deliver(result);
            }
            PyCallOutcome::GenError(exception) => {
                self.current_seg_mut().mode =
                    self.mode_after_generror(GenErrorSite::AsyncEscape, exception, false);
            }
            PyCallOutcome::GenYield(_) | PyCallOutcome::GenReturn(_) => {
                self.receive_unexpected_outcome();
            }
        }
    }

    fn receive_unexpected_outcome(&mut self) {
        self.current_seg_mut().mode = Mode::Throw(PyException::runtime_error(
            "unexpected pending/outcome combination in receive_python_result",
        ));
    }
}
