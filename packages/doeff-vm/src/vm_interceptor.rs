//! VM interceptor chain and WithIntercept handling.

use super::*;

impl VM {
    pub(super) fn current_interceptor_chain(&self) -> Vec<Marker> {
        self.current_scope_chain()
            .into_iter()
            .filter(|marker| self.interceptors.contains_key(marker))
            .collect()
    }

    pub(super) fn interceptor_visible_to_active_handler(&self, interceptor_marker: Marker) -> bool {
        let Some(dispatch_id) = self.current_active_handler_dispatch_id() else {
            return true;
        };
        let Some(dispatch_ctx) = self
            .dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
        else {
            debug_assert!(false, "active dispatch_id not found on stack");
            return false;
        };
        let Some(handler_marker) = dispatch_ctx
            .handler_chain
            .get(dispatch_ctx.handler_idx)
            .copied()
        else {
            debug_assert!(false, "handler_idx out of bounds");
            return false;
        };
        let Some(entry) = self.handlers.get(&handler_marker) else {
            debug_assert!(false, "handler marker not in registry");
            return false;
        };
        let Some(prompt_seg) = self.segments.get(entry.prompt_seg_id) else {
            debug_assert!(false, "prompt segment missing");
            return false;
        };
        prompt_seg.scope_chain.contains(&interceptor_marker)
    }

    pub(super) fn is_interceptor_skipped(&self, marker: Marker) -> bool {
        self.interceptor_skip_stack.contains(&marker)
    }

    pub(super) fn pop_interceptor_skip(&mut self, marker: Marker) {
        if let Some(pos) = self
            .interceptor_skip_stack
            .iter()
            .rposition(|active| *active == marker)
        {
            self.interceptor_skip_stack.remove(pos);
        }
    }

    pub(super) fn should_apply_interceptor(
        &self,
        entry: &InterceptorEntry,
        yielded_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        let is_match = Python::attach(|py| {
            yielded_obj
                .bind(py)
                .is_instance(entry.types.bind(py))
                .map_err(PyException::from)
        })?;
        Ok(match entry.mode {
            InterceptMode::Include => is_match,
            InterceptMode::Exclude => !is_match,
        })
    }

    pub(super) fn classify_interceptor_result_object(
        &self,
        result_obj: Py<PyAny>,
        original_obj: &Py<PyAny>,
        original_yielded: DoCtrl,
    ) -> Result<DoCtrl, PyException> {
        Python::attach(|py| {
            if result_obj.bind(py).as_ptr() == original_obj.bind(py).as_ptr() {
                return Ok(original_yielded);
            }
            classify_yielded_for_vm(self, py, result_obj.bind(py))
        })
    }

    pub(super) fn continue_interceptor_chain_mode(
        &mut self,
        yielded: DoCtrl,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
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

            let Some(entry) = self.interceptors.get(&marker).cloned() else {
                continue;
            };

            let yielded_obj = match doctrl_to_pyexpr_for_vm(&current) {
                Ok(Some(obj)) => obj,
                Ok(None) => continue,
                Err(exc) => return Mode::Throw(exc),
            };

            let should_apply = match self.should_apply_interceptor(&entry, &yielded_obj) {
                Ok(flag) => flag,
                Err(exc) => return Mode::Throw(exc),
            };
            if !should_apply {
                continue;
            }

            return self.start_interceptor_invocation_mode(
                marker,
                entry,
                current,
                yielded_obj,
                stream,
                metadata,
                chain,
                idx,
            );
        }

        self.finalize_stream_yield_mode(current, stream, metadata)
    }

    pub(super) fn start_interceptor_invocation_mode(
        &mut self,
        marker: Marker,
        entry: InterceptorEntry,
        yielded: DoCtrl,
        yielded_obj: Py<PyAny>,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
        chain: Arc<Vec<Marker>>,
        next_idx: usize,
    ) -> Mode {
        let interceptor_callable = entry.interceptor.into_inner();
        let interceptor_meta = entry.metadata.clone();
        let yielded_obj_for_callback = Python::attach(|py| yielded_obj.clone_ref(py));
        let interceptor_arg = match self.interceptor_call_arg(&interceptor_callable, &yielded_obj) {
            Ok(arg) => arg,
            Err(exc) => return Mode::Throw(exc),
        };

        let cb = self.register_callback(Box::new(move |value, vm| {
            vm.handle_interceptor_apply_result(
                marker,
                value,
                yielded,
                yielded_obj_for_callback,
                stream,
                metadata,
                chain,
                next_idx,
            )
        }));

        self.interceptor_callbacks.insert(cb, marker);
        self.interceptor_skip_stack.push(marker);

        if self.current_segment.is_none() {
            self.pop_interceptor_skip(marker);
            self.interceptor_callbacks.remove(&cb);
            self.callbacks.remove(&cb);
            return Mode::Throw(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        }
        self.maybe_emit_frame_entered(&interceptor_meta);
        self.interceptor_call_metadata
            .insert(cb, interceptor_meta.clone());
        let Some(seg) = self.current_segment_mut() else {
            self.interceptor_call_metadata.remove(&cb);
            self.pop_interceptor_skip(marker);
            self.interceptor_callbacks.remove(&cb);
            self.callbacks.remove(&cb);
            return Mode::Throw(PyException::runtime_error(
                "current_segment_mut() returned None while invoking interceptor",
            ));
        };
        seg.push_frame(Frame::RustReturn { cb });

        Mode::HandleYield(DoCtrl::Apply {
            f: CallArg::Value(Value::Python(interceptor_callable)),
            args: vec![interceptor_arg],
            kwargs: vec![],
            metadata: interceptor_meta,
        })
    }

    pub(super) fn interceptor_call_arg(
        &self,
        interceptor_callable: &Py<PyAny>,
        yielded_obj: &Py<PyAny>,
    ) -> Result<CallArg, PyException> {
        Python::attach(|py| {
            let callable = interceptor_callable.bind(py);
            let is_do_callable = callable.is_instance_of::<DoeffGeneratorFn>()
                || callable
                    .getattr("_doeff_generator_factory")
                    .is_ok_and(|factory| factory.is_instance_of::<DoeffGeneratorFn>());

            // @do callables are expanded as DoCtrl::Expand and evaluate expression arguments.
            // Wrap the yielded object in Pure(...) so interceptor functions receive the original
            // DoExpr value (not the evaluated effect result).
            if is_do_callable {
                let quoted = Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Pure as u8,
                        })
                        .add_subclass(PyPure {
                            value: yielded_obj.clone_ref(py),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind();
                return Ok(CallArg::Expr(PyShared::new(quoted)));
            }

            Ok(CallArg::Value(Value::Python(yielded_obj.clone_ref(py))))
        })
    }

    pub(super) fn handle_interceptor_apply_result(
        &mut self,
        marker: Marker,
        value: Value,
        original_yielded: DoCtrl,
        original_obj: Py<PyAny>,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
        chain: Arc<Vec<Marker>>,
        next_idx: usize,
    ) -> Mode {
        let Value::Python(result_obj) = value else {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::type_error(
                "WithIntercept interceptor must return DoExpr",
            ));
        };

        let (doctrl_tag, is_effect_base, is_doexpr) = Python::attach(|py| {
            let bound = result_obj.bind(py);
            let doctrl_tag = bound
                .extract::<PyRef<'_, PyDoCtrlBase>>()
                .ok()
                .and_then(|base| DoExprTag::try_from(base.tag).ok());
            (
                doctrl_tag,
                bound.is_instance_of::<PyEffectBase>(),
                bound.is_instance_of::<PyDoExprBase>(),
            )
        });
        let is_direct_expr =
            is_effect_base || doctrl_tag.is_some_and(|tag| tag != DoExprTag::Expand);

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
                stream,
                metadata,
                chain,
                next_idx,
            );
        }

        if is_doexpr {
            let cb = self.register_callback(Box::new(move |resolved, vm| {
                vm.handle_interceptor_eval_result(
                    marker,
                    resolved,
                    original_yielded,
                    original_obj,
                    stream,
                    metadata,
                    chain,
                    next_idx,
                )
            }));
            self.interceptor_callbacks.insert(cb, marker);
            self.interceptor_eval_callbacks.insert(cb);
            self.interceptor_eval_depth = self.interceptor_eval_depth.saturating_add(1);

            let Some(seg) = self.current_segment_mut() else {
                self.pop_interceptor_skip(marker);
                self.interceptor_callbacks.remove(&cb);
                if self.interceptor_eval_callbacks.remove(&cb) {
                    self.interceptor_eval_depth = self.interceptor_eval_depth.saturating_sub(1);
                }
                self.callbacks.remove(&cb);
                return Mode::Throw(PyException::runtime_error(
                    "current_segment_mut() returned None while evaluating interceptor result",
                ));
            };
            seg.push_frame(Frame::RustReturn { cb });

            let handlers = self.current_visible_handlers();
            return Mode::HandleYield(DoCtrl::Eval {
                expr: PyShared::new(result_obj),
                handlers,
                metadata: None,
            });
        }

        self.pop_interceptor_skip(marker);
        Mode::Throw(PyException::type_error(
            "WithIntercept interceptor must return DoExpr",
        ))
    }

    pub(super) fn handle_interceptor_eval_result(
        &mut self,
        marker: Marker,
        value: Value,
        original_yielded: DoCtrl,
        original_obj: Py<PyAny>,
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
        chain: Arc<Vec<Marker>>,
        next_idx: usize,
    ) -> Mode {
        let Value::Python(result_obj) = value else {
            self.pop_interceptor_skip(marker);
            return Mode::Throw(PyException::type_error(
                "WithIntercept effectful interceptor must resolve to DoExpr",
            ));
        };

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
        self.continue_interceptor_chain_mode(transformed, stream, metadata, chain, next_idx)
    }

    pub(super) fn handle_yield_with_intercept(
        &mut self,
        interceptor: PyShared,
        expr: Py<PyAny>,
        types: PyShared,
        mode: InterceptMode,
        metadata: CallMetadata,
    ) -> StepEvent {
        self.handle_with_intercept(interceptor, expr, types, mode, metadata)
    }

    pub(super) fn handle_with_intercept(
        &mut self,
        interceptor: PyShared,
        program: Py<PyAny>,
        types: PyShared,
        mode: InterceptMode,
        metadata: CallMetadata,
    ) -> StepEvent {
        let interceptor_marker = Marker::fresh();
        let outside_seg_id = match self.current_segment {
            Some(id) => id,
            None => {
                return StepEvent::Error(VMError::internal("no current segment for WithIntercept"));
            }
        };
        let outside_scope = self
            .segments
            .get(outside_seg_id)
            .map(|s| s.scope_chain.clone())
            .unwrap_or_default();

        self.interceptors.insert(
            interceptor_marker,
            InterceptorEntry {
                interceptor,
                types,
                mode,
                metadata,
            },
        );

        let mut body_scope = vec![interceptor_marker];
        body_scope.extend(outside_scope);

        let body_seg = Segment::new(interceptor_marker, Some(outside_seg_id), body_scope);
        let body_seg_id = self.alloc_segment(body_seg);

        self.current_segment = Some(body_seg_id);
        self.pending_python = Some(PendingPython::EvalExpr { metadata: None });
        StepEvent::NeedsPython(PythonCall::EvalExpr {
            expr: PyShared::new(program),
        })
    }
}
