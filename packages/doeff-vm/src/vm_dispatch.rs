//! VM dispatch, scope-chain resolution, and forward/delegate/pass handling.

use super::*;

impl VM {
    pub fn current_scope_chain(&self) -> Vec<Marker> {
        self.current_segment
            .and_then(|id| self.segments.get(id))
            .map(|seg| seg.scope_chain.clone())
            .unwrap_or_default()
    }

    pub fn lazy_pop_completed(&mut self) {
        while let Some(top) = self.dispatch_stack.last() {
            if top.completed {
                self.dispatch_stack.pop();
            } else {
                break;
            }
        }
    }

    /// Top-only busy boundary: handlers at indices 0..=handler_idx in the topmost
    /// non-completed dispatch are excluded from the visible set.
    pub fn visible_handlers(&self, scope_chain: &[Marker]) -> Vec<Marker> {
        let Some(top) = self.dispatch_stack.last() else {
            return scope_chain.to_vec();
        };

        if top.completed || self.consumed_cont_ids.contains(&top.k_user.cont_id) {
            return scope_chain.to_vec();
        }

        let busy: HashSet<Marker> = top.handler_chain[..=top.handler_idx]
            .iter()
            .copied()
            .collect();

        scope_chain
            .iter()
            .copied()
            .filter(|marker| !busy.contains(marker))
            .collect()
    }

    pub fn find_matching_handler(
        &self,
        handler_chain: &[Marker],
        effect: &DispatchEffect,
    ) -> Result<(usize, Marker, HandlerEntry), VMError> {
        for (idx, &marker) in handler_chain.iter().enumerate() {
            let Some(entry) = self.handlers.get(&marker) else {
                return Err(VMError::internal(format!(
                    "find_matching_handler: missing handler marker {} at index {}",
                    marker.raw(),
                    idx
                )));
            };
            if entry.handler.can_handle(effect)? {
                return Ok((idx, marker, entry.clone()));
            }
        }
        Err(VMError::no_matching_handler(effect.clone()))
    }

    pub fn start_dispatch(&mut self, effect: DispatchEffect) -> Result<StepEvent, VMError> {
        self.lazy_pop_completed();
        let original_exception = self.pending_error_context.take();

        let scope_chain = self.current_scope_chain();
        let handler_chain: Vec<Marker> = self
            .visible_handlers(&scope_chain)
            .into_iter()
            .filter(|marker| self.handlers.contains_key(marker))
            .collect();

        if handler_chain.is_empty() {
            if let Some(original) = original_exception.clone() {
                self.mode = Mode::Throw(original);
                return Ok(StepEvent::Continue);
            }
            return Err(VMError::unhandled_effect(effect));
        }

        let (handler_idx, handler_marker, entry) =
            match self.find_matching_handler(&handler_chain, &effect) {
                Ok(found) => found,
                Err(err) => {
                    if let Some(original) = original_exception.clone() {
                        self.mode = Mode::Throw(original);
                        return Ok(StepEvent::Continue);
                    }
                    return Err(err);
                }
            };

        let prompt_seg_id = entry.prompt_seg_id;
        let handler = entry.handler.clone();
        let dispatch_id = DispatchId::fresh();
        let is_execution_context_effect = Self::is_execution_context_effect(&effect);
        let supports_error_context_conversion = handler.supports_error_context_conversion();
        let mut handler_chain_snapshot: Vec<HandlerSnapshotEntry> = Vec::new();
        for marker in handler_chain.iter().copied() {
            let Some(entry) = self.handlers.get(&marker) else {
                continue;
            };
            let (name, kind, file, line) = Self::handler_trace_info(&entry.handler);
            handler_chain_snapshot.push(HandlerSnapshotEntry {
                handler_name: name,
                handler_kind: kind,
                source_file: file,
                source_line: line,
            });
        }

        let seg_id = self
            .current_segment
            .ok_or_else(|| VMError::internal("no current segment during dispatch"))?;
        let current_seg = self
            .segments
            .get(seg_id)
            .ok_or_else(|| VMError::invalid_segment("current segment not found"))?;
        let k_user = Continuation::capture(current_seg, seg_id, Some(dispatch_id));

        let scope_chain = self.current_scope_chain();
        let handler_seg = Segment::new(handler_marker, Some(prompt_seg_id), scope_chain);
        let handler_seg_id = self.alloc_segment(handler_seg);
        self.current_segment = Some(handler_seg_id);

        self.dispatch_stack.push(DispatchContext {
            dispatch_id,
            effect: effect.clone(),
            is_execution_context_effect,
            handler_chain: handler_chain.clone(),
            handler_idx,
            supports_error_context_conversion,
            k_user: k_user.clone(),
            prompt_seg_id,
            completed: false,
            original_exception,
        });

        let (handler_name, handler_kind, handler_source_file, handler_source_line) =
            Self::handler_trace_info(&handler);
        let effect_site = Self::effect_site_from_continuation(&k_user);
        self.capture_log.push(CaptureEvent::DispatchStarted {
            dispatch_id,
            effect_repr: Self::effect_repr(&effect),
            is_execution_context_effect,
            creation_site: Self::effect_creation_site_from_continuation(&k_user),
            handler_name,
            handler_kind,
            handler_source_file,
            handler_source_line,
            handler_chain_snapshot,
            effect_frame_id: effect_site.as_ref().map(|(frame_id, _, _, _)| *frame_id),
            effect_function_name: effect_site
                .as_ref()
                .map(|(_, function_name, _, _)| function_name.clone()),
            effect_source_file: effect_site
                .as_ref()
                .map(|(_, _, source_file, _)| source_file.clone()),
            effect_source_line: effect_site
                .as_ref()
                .map(|(_, _, _, source_line)| *source_line),
        });

        if handler.py_identity().is_some() {
            self.register_continuation(k_user.clone());
        }
        let ir_node = handler.invoke(effect, k_user);
        Ok(self.evaluate(ir_node))
    }

    pub(super) fn check_dispatch_completion(&mut self, k: &Continuation) {
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id
                    && top.k_user.cont_id == k.cont_id
                    && top.k_user.parent.is_none()
                {
                    top.completed = true;
                }
            }
        }
    }

    pub(super) fn error_dispatch_for_continuation(
        &self,
        k: &Continuation,
    ) -> Option<(DispatchId, PyException, bool)> {
        let dispatch_id = k.dispatch_id?;
        let ctx = self
            .dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)?;
        let original = ctx.original_exception.clone()?;
        let mut cursor = Some(ctx.k_user.clone());
        while let Some(current) = cursor {
            if current.cont_id == k.cont_id {
                return Some((dispatch_id, original, current.parent.is_none()));
            }
            cursor = current.parent.as_ref().map(|parent| (**parent).clone());
        }
        None
    }

    pub(super) fn active_dispatch_handler_is_python(&self, dispatch_id: DispatchId) -> bool {
        self.dispatch_stack
            .last()
            .filter(|ctx| ctx.dispatch_id == dispatch_id)
            .and_then(|ctx| ctx.handler_chain.get(ctx.handler_idx))
            .and_then(|marker| self.handlers.get(marker))
            .is_some_and(|entry| entry.handler.py_identity().is_some())
    }

    pub(super) fn mark_dispatch_threw(&mut self, dispatch_id: DispatchId) {
        self.mark_dispatch_completed(dispatch_id);
    }

    pub(super) fn mark_dispatch_completed(&mut self, dispatch_id: DispatchId) {
        if let Some(ctx) = self
            .dispatch_stack
            .iter_mut()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
        {
            ctx.completed = true;
            self.consumed_cont_ids.insert(ctx.k_user.cont_id);
        }
    }

    pub(super) fn dispatch_has_terminal_handler_action(&self, dispatch_id: DispatchId) -> bool {
        self.capture_log.iter().rev().any(|event| match event {
            CaptureEvent::HandlerCompleted {
                dispatch_id: event_dispatch_id,
                action:
                    HandlerAction::Resumed { .. }
                    | HandlerAction::Transferred { .. }
                    | HandlerAction::Returned { .. }
                    | HandlerAction::Threw { .. },
                ..
            } => *event_dispatch_id == dispatch_id,
            _ => false,
        })
    }

    pub(super) fn finalize_active_dispatches_as_threw(&mut self, exception: &PyException) {
        let exception_repr = Self::exception_repr(exception);
        for idx in 0..self.dispatch_stack.len() {
            let (dispatch_id, cont_id, completed) = {
                let ctx = &self.dispatch_stack[idx];
                (ctx.dispatch_id, ctx.k_user.cont_id, ctx.completed)
            };
            if completed {
                continue;
            }
            if self.dispatch_has_terminal_handler_action(dispatch_id) {
                if let Some(ctx) = self.dispatch_stack.get_mut(idx) {
                    ctx.completed = true;
                }
                self.consumed_cont_ids.insert(cont_id);
                continue;
            }
            let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            else {
                if let Some(ctx) = self.dispatch_stack.get_mut(idx) {
                    ctx.completed = true;
                }
                self.consumed_cont_ids.insert(cont_id);
                continue;
            };
            self.capture_log.push(CaptureEvent::HandlerCompleted {
                dispatch_id,
                handler_name,
                handler_index,
                action: HandlerAction::Threw {
                    exception_repr: exception_repr.clone(),
                },
            });
            if let Some(ctx) = self.dispatch_stack.get_mut(idx) {
                ctx.completed = true;
            }
            self.consumed_cont_ids.insert(cont_id);
        }
    }

    pub(super) fn check_dispatch_completion_after_activation(
        &mut self,
        kind: ContinuationActivationKind,
        k: &Continuation,
        had_error_dispatch: bool,
    ) {
        match kind {
            ContinuationActivationKind::Resume => {
                if had_error_dispatch {
                    if let Some(dispatch_id) = k.dispatch_id {
                        if !self.active_dispatch_handler_is_python(dispatch_id) {
                            self.check_dispatch_completion(k);
                        }
                    } else {
                        self.check_dispatch_completion(k);
                    }
                    return;
                }

                if let Some(dispatch_id) = k.dispatch_id {
                    if !self.active_dispatch_handler_is_python(dispatch_id) {
                        self.check_dispatch_completion(k);
                    }
                } else {
                    self.check_dispatch_completion(k);
                }
            }
            ContinuationActivationKind::Transfer => {
                self.check_dispatch_completion(k);
            }
        }
    }

    pub(super) fn check_dispatch_completion_for_non_terminal_throw(&mut self, k: &Continuation) {
        if let Some(dispatch_id) = k.dispatch_id {
            if !self.active_dispatch_handler_is_python(dispatch_id) {
                self.check_dispatch_completion(k);
            }
        } else {
            self.check_dispatch_completion(k);
        }
    }

    pub(super) fn maybe_emit_forward_capture_event(
        &mut self,
        kind: ForwardKind,
        dispatch_id: DispatchId,
        handler_chain: &[Marker],
        from_idx: usize,
        to_idx: usize,
        to_marker: Marker,
    ) {
        let from_marker = handler_chain.get(from_idx).copied();
        let from_name = from_marker
            .and_then(|m| self.marker_handler_trace_info(m))
            .map(|(name, _, _, _)| name);
        let to_info = self.marker_handler_trace_info(to_marker);
        if let (Some(from_name), Some((to_name, to_kind, to_source_file, to_source_line))) =
            (from_name, to_info)
        {
            let event = match kind {
                ForwardKind::Delegate => CaptureEvent::Delegated {
                    dispatch_id,
                    from_handler_name: from_name,
                    from_handler_index: from_idx,
                    to_handler_name: to_name,
                    to_handler_index: to_idx,
                    to_handler_kind: to_kind,
                    to_handler_source_file: to_source_file,
                    to_handler_source_line: to_source_line,
                },
                ForwardKind::Pass => CaptureEvent::Passed {
                    dispatch_id,
                    from_handler_name: from_name,
                    from_handler_index: from_idx,
                    to_handler_name: to_name,
                    to_handler_index: to_idx,
                    to_handler_kind: to_kind,
                    to_handler_source_file: to_source_file,
                    to_handler_source_line: to_source_line,
                },
            };
            self.capture_log.push(event);
        }
    }

    pub(super) fn handle_forward(
        &mut self,
        kind: ForwardKind,
        effect: DispatchEffect,
    ) -> StepEvent {
        let (handler_chain, start_idx, from_idx, dispatch_id, parent_k_user) =
            match self.dispatch_stack.last() {
                Some(top) => (
                    top.handler_chain.clone(),
                    top.handler_idx + 1,
                    top.handler_idx,
                    top.dispatch_id,
                    if kind == ForwardKind::Delegate {
                        Some(top.k_user.clone())
                    } else {
                        None
                    },
                ),
                None => return StepEvent::Error(VMError::internal(kind.outside_dispatch_error())),
            };

        // Capture inner handler segment so outer handler return flows back as the
        // result of Delegate/Pass. Per spec this preserves caller = Some(inner_seg_id).
        let inner_seg_id = self.current_segment;

        match kind {
            ForwardKind::Delegate => {
                // Delegate is non-terminal: keep a parent chain to the old continuation.
                let Some(mut k_new) = self.capture_continuation(Some(dispatch_id)) else {
                    return StepEvent::Error(VMError::internal(
                        "Delegate called without current segment",
                    ));
                };
                let Some(parent_k_user) = parent_k_user else {
                    return StepEvent::Error(VMError::internal(
                        "Delegate called without active dispatch continuation",
                    ));
                };
                k_new.parent = Some(Arc::new(parent_k_user));
                self.clear_segment_frames(inner_seg_id);
                if let Some(top) = self.dispatch_stack.last_mut() {
                    top.k_user = k_new;
                }
            }
            ForwardKind::Pass => {
                // Pass is terminal for the current handler; clear frames so values pass through.
                self.clear_segment_frames(inner_seg_id);
            }
        }

        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            let Some(entry) = self.handlers.get(&marker) else {
                return StepEvent::Error(VMError::internal(format!(
                    "{}: missing handler marker {} at index {}",
                    kind.missing_handler_context(),
                    marker.raw(),
                    idx
                )));
            };
            let can_handle = match entry.handler.can_handle(&effect) {
                Ok(value) => value,
                Err(err) => return StepEvent::Error(err),
            };
            if can_handle {
                let handler = entry.handler.clone();
                let supports_error_context_conversion =
                    entry.handler.supports_error_context_conversion();
                self.maybe_emit_forward_capture_event(
                    kind,
                    dispatch_id,
                    &handler_chain,
                    from_idx,
                    idx,
                    marker,
                );
                let k_user = {
                    let top = self.dispatch_stack.last_mut().unwrap();
                    top.handler_idx = idx;
                    top.supports_error_context_conversion = supports_error_context_conversion;
                    top.effect = effect.clone();
                    top.k_user.clone()
                };

                let scope_chain = self.current_scope_chain();
                let handler_seg = Segment::new(marker, inner_seg_id, scope_chain);
                let handler_seg_id = self.alloc_segment(handler_seg);
                self.current_segment = Some(handler_seg_id);

                if handler.py_identity().is_some() {
                    self.register_continuation(k_user.clone());
                }
                let ir_node = handler.invoke(effect.clone(), k_user);
                return self.evaluate(ir_node);
            }
        }

        if let Some((dispatch_id, original_exception)) =
            self.dispatch_stack.last().and_then(|ctx| {
                ctx.original_exception
                    .clone()
                    .map(|exc| (ctx.dispatch_id, exc))
            })
        {
            self.mark_dispatch_completed(dispatch_id);
            self.mode = Mode::Throw(original_exception);
            return StepEvent::Continue;
        }

        StepEvent::Error(VMError::delegate_no_outer_handler(effect))
    }

    pub(super) fn handle_delegate(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_forward(ForwardKind::Delegate, effect)
    }

    pub(super) fn handle_pass(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_forward(ForwardKind::Pass, effect)
    }

    /// Handle handler return (explicit or implicit).
    ///
    /// Per SPEC-008: sets Mode::Deliver(value) and lets the natural caller chain
    /// walk deliver the value back. Does NOT explicitly jump to prompt_seg_id.
    /// If the handler's caller is the prompt boundary, marks dispatch completed.
    pub(super) fn handle_handler_return(&mut self, value: Value) -> StepEvent {
        // Transfer paths may complete dispatches before stream return reaches here.
        // Drop completed entries so handler-return bookkeeping does not bind to stale state.
        self.lazy_pop_completed();

        if let Value::Python(obj) = &value {
            let should_eval = Python::attach(|py| {
                let bound = obj.bind(py);
                bound.is_instance_of::<PyDoExprBase>() || bound.is_instance_of::<PyEffectBase>()
            });

            if should_eval && self.interceptor_eval_depth == 0 {
                let handlers = self.current_visible_handlers();
                let expr = Python::attach(|py| PyShared::new(obj.clone_ref(py)));
                let cb = self.register_callback(Box::new(|resolved, vm| {
                    let _ = vm.handle_handler_return(resolved);
                    std::mem::replace(&mut vm.mode, Mode::Deliver(Value::Unit))
                }));
                let Some(seg) = self.current_segment_mut() else {
                    return StepEvent::Error(VMError::internal(
                        "current_segment_mut() returned None in handle_handler_return \
                         while scheduling Eval callback",
                    ));
                };
                seg.push_frame(Frame::RustReturn { cb });
                self.mode = Mode::HandleYield(DoCtrl::Eval {
                    expr,
                    handlers,
                    metadata: None,
                });
                return StepEvent::Continue;
            }
        }

        let Some(top_snapshot) = self.dispatch_stack.last().cloned() else {
            self.mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };

        let Some((handler_index, handler_name)) =
            self.current_handler_identity_for_dispatch(top_snapshot.dispatch_id)
        else {
            self.mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };
        let value_repr = Self::value_repr(&value);
        self.capture_log.push(CaptureEvent::HandlerCompleted {
            dispatch_id: top_snapshot.dispatch_id,
            handler_name: handler_name.clone(),
            handler_index,
            action: HandlerAction::Returned {
                value_repr: value_repr.clone(),
            },
        });
        self.maybe_emit_resume_event(
            top_snapshot.dispatch_id,
            handler_name,
            value_repr,
            &top_snapshot.k_user,
            false,
        );

        let Some(seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal(
                "current_segment missing in handle_handler_return while dispatch active",
            ));
        };
        let Some(seg) = self.segments.get(seg_id) else {
            return StepEvent::Error(VMError::internal(
                "current segment not found in handle_handler_return while dispatch active",
            ));
        };
        let Some(caller_id) = seg.caller else {
            return StepEvent::Error(VMError::internal(
                "handler segment missing caller in handle_handler_return",
            ));
        };

        let original_exception = {
            let Some(top) = self.dispatch_stack.last_mut() else {
                return StepEvent::Error(VMError::internal("Return outside of dispatch"));
            };

            if caller_id == top.prompt_seg_id {
                top.completed = true;
                self.consumed_cont_ids.insert(top.k_user.cont_id);
            }

            if top.completed {
                top.original_exception.clone()
            } else {
                None
            }
        };

        if let Some(original) = original_exception {
            self.mode = match Self::enrich_original_exception_with_context(original, value) {
                Ok(exception) => Mode::Throw(exception),
                Err(effect_err) => Mode::Throw(effect_err),
            };
            return StepEvent::Continue;
        }

        // D10: Spec says Mode::Deliver, not Mode::Return + explicit segment jump.
        // Natural caller-chain walking handles segment transitions.
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }
}
