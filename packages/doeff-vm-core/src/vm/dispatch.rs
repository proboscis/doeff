use super::*;

impl VM {
    fn handler_dispatch_is_live(&self, continuation: &Continuation) -> bool {
        !self.is_one_shot_consumed(continuation.cont_id)
    }

    fn dispatch_origin_view_from_context(
        dispatch_id: DispatchId,
        dispatch: &crate::dispatch_state::DispatchContext,
    ) -> DispatchOriginView {
        DispatchOriginView {
            dispatch_id,
            effect: dispatch.effect.clone(),
            k_origin: dispatch.k_origin.clone_handle(),
            original_exception: dispatch.original_exception.clone(),
        }
    }

    fn dispatch_context_for_segment(
        &self,
        seg_id: SegmentId,
    ) -> Option<(DispatchId, &crate::dispatch_state::DispatchContext)> {
        let dispatch_id = self.dispatch_origin_id_in_segment(seg_id)?;
        let dispatch = self
            .dispatch_state
            .dispatch(dispatch_id)
            .unwrap_or_else(|| {
                panic!(
                    "dispatch state invariant violated: segment {} references dispatch {} but \
                 state has no context",
                    seg_id.index(),
                    dispatch_id.raw()
                )
            });
        Some((dispatch_id, dispatch))
    }

    pub(super) fn finish_dispatch_tracking(&mut self, dispatch_id: DispatchId) {
        self.dispatch_state.finish_dispatch(dispatch_id);
        self.trace_state.finish_dispatch(dispatch_id);
    }

    fn dispatch_origin_view(&self, dispatch_id: DispatchId) -> Option<DispatchOriginView> {
        let dispatch = self.dispatch_state.dispatch(dispatch_id)?;
        Some(Self::dispatch_origin_view_from_context(
            dispatch_id,
            dispatch,
        ))
    }

    fn dispatch_origins_from_segment(
        &self,
        start_segment: Option<SegmentId>,
    ) -> Vec<DispatchOriginView> {
        let mut seen = HashSet::new();
        let mut origins = Vec::new();
        let mut cursor = start_segment;
        while let Some(seg_id) = cursor {
            if let Some((dispatch_id, dispatch)) = self.dispatch_context_for_segment(seg_id) {
                if seen.insert(dispatch_id) {
                    origins.push(Self::dispatch_origin_view_from_context(
                        dispatch_id,
                        dispatch,
                    ));
                }
            }
            cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
        }

        origins.sort_by_key(|origin| origin.dispatch_id.raw());
        origins
    }

    fn dispatch_origin_in_segment_by<T>(
        &self,
        seg_id: SegmentId,
        mut map: impl FnMut(
            DispatchId,
            &DispatchEffect,
            &Continuation,
            Option<&PyException>,
        ) -> Option<T>,
    ) -> Option<T> {
        let (dispatch_id, dispatch_origin) = self.dispatch_context_for_segment(seg_id)?;
        map(
            dispatch_id,
            &dispatch_origin.effect,
            &dispatch_origin.k_origin,
            dispatch_origin.original_exception.as_ref(),
        )
    }

    fn dispatch_origin_for_dispatch_id_anywhere(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<DispatchOriginView> {
        self.dispatch_origin_view(dispatch_id)
    }

    fn dispatch_origin_id_in_segment(&self, seg_id: SegmentId) -> Option<DispatchId> {
        self.dispatch_state.segment_dispatch_id(seg_id)
    }

    fn dispatch_origin_caller_in_segment(
        &self,
        seg_id: SegmentId,
    ) -> Option<(DispatchId, SegmentId)> {
        self.dispatch_origin_in_segment_by(seg_id, |dispatch_id, _, k_origin, _| {
            self.continuation_handler_chain_start(k_origin)
                .map(|segment_id| (dispatch_id, segment_id))
        })
    }

    pub(super) fn dispatch_origin_callers(&self) -> Vec<SegmentId> {
        let mut seen = HashSet::new();
        let mut callers = Vec::new();
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            if let Some((dispatch_id, caller_seg_id)) =
                self.dispatch_origin_caller_in_segment(seg_id)
            {
                if seen.insert(dispatch_id) {
                    callers.push((dispatch_id, caller_seg_id));
                }
            }
            cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
        }

        callers.sort_by_key(|(dispatch_id, _)| dispatch_id.raw());
        callers
            .into_iter()
            .map(|(_, caller_seg_id)| caller_seg_id)
            .collect()
    }

    pub(super) fn dispatch_origin_user_segment_id(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<SegmentId> {
        self.dispatch_state
            .dispatch(dispatch_id)
            .and_then(|dispatch| dispatch.k_origin.segment_id())
    }

    pub(super) fn dispatch_origins(&self) -> Vec<DispatchOriginView> {
        self.dispatch_origins_from_segment(self.current_segment)
    }

    pub(super) fn dispatch_depth(&self) -> usize {
        let Some(mut dispatch_id) = self.current_dispatch_id() else {
            return 0;
        };
        let mut seen = HashSet::new();
        let mut depth = 0usize;
        while seen.insert(dispatch_id) {
            depth += 1;
            let next_dispatch_id = self
                .dispatch_state
                .dispatch(dispatch_id)
                .and_then(|dispatch| dispatch.k_origin.resume_dispatch_id());
            let Some(next_dispatch_id) = next_dispatch_id else {
                break;
            };
            dispatch_id = next_dispatch_id;
        }
        depth
    }

    pub(super) fn live_dispatch_snapshots(&self) -> Vec<LiveDispatchSnapshot> {
        self.live_dispatch_snapshots_from_segment(self.current_segment)
    }

    pub(super) fn live_dispatch_snapshots_from_segment(
        &self,
        start_segment: Option<SegmentId>,
    ) -> Vec<LiveDispatchSnapshot> {
        self.dispatch_origins_from_segment(start_segment)
            .into_iter()
            .map(|origin| {
                let continuation = self
                    .active_handler_dispatch_for(origin.dispatch_id)
                    .map(|(_, continuation, _)| continuation)
                    .unwrap_or(origin.k_origin);
                LiveDispatchSnapshot {
                    dispatch_id: origin.dispatch_id,
                    frames: self.continuation_frame_stack(&continuation),
                }
            })
            .collect()
    }

    pub(super) fn current_dispatch_origin(&self) -> Option<DispatchOriginView> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            if let Some((dispatch_id, dispatch)) = self.dispatch_context_for_segment(seg_id) {
                return Some(Self::dispatch_origin_view_from_context(
                    dispatch_id,
                    dispatch,
                ));
            }
            cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
        }
        None
    }

    pub(super) fn dispatch_origin_for_dispatch_id(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<DispatchOriginView> {
        self.dispatch_origin_for_dispatch_id_anywhere(dispatch_id)
    }

    fn dispatch_origin_for_continuation(
        &self,
        continuation: &Continuation,
    ) -> Option<DispatchOriginView> {
        let dispatch_id = continuation.dispatch_id()?;
        self.dispatch_origin_for_dispatch_id(dispatch_id)
    }

    fn continuation_is_in_origin_chain(
        &self,
        continuation: &Continuation,
        target: &Continuation,
        visited: &mut HashSet<ContId>,
    ) -> bool {
        if !visited.insert(continuation.cont_id) {
            return false;
        }
        if continuation.same_owned_fibers(target) {
            return true;
        }
        continuation.fibers().iter().any(|fiber_id| {
            self.segments.get(*fiber_id).is_some_and(|segment| {
                segment.frames.iter().any(|frame| match frame {
                    Frame::EvalReturn(eval_return) => match eval_return.as_ref() {
                        EvalReturnContinuation::ResumeToContinuation { continuation }
                        | EvalReturnContinuation::ReturnToContinuation { continuation }
                        | EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                            self.continuation_is_in_origin_chain(continuation, target, visited)
                        }
                        EvalReturnContinuation::ApplyResolveFunction { .. }
                        | EvalReturnContinuation::ApplyResolveArg { .. }
                        | EvalReturnContinuation::ApplyResolveKwarg { .. }
                        | EvalReturnContinuation::ExpandResolveFactory { .. }
                        | EvalReturnContinuation::ExpandResolveArg { .. }
                        | EvalReturnContinuation::ExpandResolveKwarg { .. }
                        | EvalReturnContinuation::TailResumeReturn => false,
                    },
                    _ => false,
                })
            })
        })
    }

    pub(super) fn exact_dispatch_origin_for_continuation(
        &self,
        continuation: &Continuation,
    ) -> Option<DispatchOriginView> {
        let origin = self.dispatch_origin_for_continuation(continuation)?;
        let mut visited = HashSet::new();
        self.continuation_is_in_origin_chain(&origin.k_origin, continuation, &mut visited)
            .then_some(origin)
    }

    pub(super) fn active_handler_marker_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<Marker> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            if let Some((found_dispatch_id, dispatch)) = self.dispatch_context_for_segment(seg_id) {
                if found_dispatch_id == dispatch_id
                    && self.segment_matches_active_handler_context(seg_id, dispatch)
                {
                    return Some(dispatch.active_handler.marker);
                }
            }
            cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
        }
        None
    }

    fn segment_matches_active_handler_context(
        &self,
        seg_id: SegmentId,
        dispatch: &crate::dispatch_state::DispatchContext,
    ) -> bool {
        dispatch.active_handler.segment_id == seg_id
            && self.handler_dispatch_is_live(&dispatch.active_handler.continuation)
            && self
                .segments
                .get(seg_id)
                .is_some_and(|seg| seg.marker() == dispatch.active_handler.marker)
    }

    pub(super) fn current_handler_dispatch(
        &self,
    ) -> Option<(SegmentId, DispatchId, Continuation, Marker, SegmentId)> {
        let seg_id = self.current_segment?;
        let (dispatch_id, dispatch) = self.dispatch_context_for_segment(seg_id)?;
        self.segment_matches_active_handler_context(seg_id, dispatch)
            .then(|| {
                (
                    seg_id,
                    dispatch_id,
                    dispatch.active_handler.continuation.clone_handle(),
                    dispatch.active_handler.marker,
                    dispatch.active_handler.prompt_seg_id,
                )
            })
    }

    pub(super) fn nearest_handler_dispatch(
        &self,
    ) -> Option<(SegmentId, DispatchId, Continuation, Marker, SegmentId)> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            let Some((dispatch_id, dispatch)) = self.dispatch_context_for_segment(seg_id) else {
                cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
                continue;
            };
            if self.segment_matches_active_handler_context(seg_id, dispatch) {
                let found = (
                    seg_id,
                    dispatch_id,
                    dispatch.active_handler.continuation.clone_handle(),
                    dispatch.active_handler.marker,
                    dispatch.active_handler.prompt_seg_id,
                );
                return Some(found);
            }
            cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
        }
        None
    }

    pub(super) fn active_handler_dispatch_for(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<(SegmentId, Continuation, Marker)> {
        let dispatch = self.dispatch_state.dispatch(dispatch_id)?;
        self.handler_dispatch_is_live(&dispatch.active_handler.continuation)
            .then(|| {
                (
                    dispatch.active_handler.segment_id,
                    dispatch.active_handler.continuation.clone_handle(),
                    dispatch.active_handler.marker,
                )
            })
    }

    pub(super) fn handler_dispatch_for_any(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<(SegmentId, Continuation, Marker)> {
        let dispatch = self.dispatch_state.dispatch(dispatch_id)?;
        Some((
            dispatch.active_handler.segment_id,
            dispatch.active_handler.continuation.clone_handle(),
            dispatch.active_handler.marker,
        ))
    }

    fn clear_forwarded_handler_segment(&mut self, seg_id: SegmentId) {
        self.dispatch_state.unbind_segment(seg_id);
        let Some(seg) = self.segments.get_mut(seg_id) else {
            return;
        };
        seg.frames.clear();
        let _ = seg;
        self.clear_pending_error_context(seg_id);
        self.clear_throw_parent(seg_id);
    }

    fn continuation_chain_contains_eval_in_scope_return(
        &self,
        continuation: &Continuation,
    ) -> bool {
        continuation.fibers().iter().any(|fiber_id| {
            self.segments.get(*fiber_id).is_some_and(|segment| {
                segment.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::EvalReturn(eval_return)
                            if matches!(
                                eval_return.as_ref(),
                                EvalReturnContinuation::EvalInScopeReturn { .. }
                            )
                    )
                })
            })
        })
    }

    fn continuation_chain_contains_return_to_continuation(
        &self,
        continuation: &Continuation,
    ) -> bool {
        continuation.fibers().iter().any(|fiber_id| {
            self.segments.get(*fiber_id).is_some_and(|segment| {
                segment.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::EvalReturn(eval_return)
                            if matches!(
                                eval_return.as_ref(),
                                EvalReturnContinuation::ReturnToContinuation { .. }
                            )
                    )
                })
            })
        })
    }

    fn is_inside_eval_in_scope_subtopology(&self) -> bool {
        let contains_eval_in_scope_return = |start_seg_id: Option<SegmentId>| {
            let mut seg_id = start_seg_id;
            while let Some(id) = seg_id {
                let Some(seg) = self.segments.get(id) else {
                    break;
                };
                if seg.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::EvalReturn(continuation)
                            if matches!(
                                continuation.as_ref(),
                                EvalReturnContinuation::EvalInScopeReturn { .. }
                        )
                    )
                }) {
                    return true;
                }
                seg_id = seg.parent;
            }
            false
        };

        let mut seg_id = self.current_segment;
        while let Some(id) = seg_id {
            let Some(seg) = self.segments.get(id) else {
                break;
            };
            if seg.frames.iter().any(|frame| {
                matches!(
                    frame,
                    Frame::EvalReturn(continuation)
                        if matches!(
                            continuation.as_ref(),
                            EvalReturnContinuation::EvalInScopeReturn { .. }
                        )
                )
            }) {
                return true;
            }
            seg_id = seg.parent;
        }
        let Some(dispatch_id) = self.current_segment_dispatch_id_any() else {
            return false;
        };
        if contains_eval_in_scope_return(self.dispatch_origin_user_segment_id(dispatch_id)) {
            return true;
        }
        let Some(origin) = self.dispatch_origin_for_dispatch_id(dispatch_id) else {
            return false;
        };
        self.active_handler_dispatch_for(dispatch_id)
            .is_some_and(|(_, continuation, _)| {
                self.continuation_chain_contains_eval_in_scope_return(&continuation)
            })
            || self.continuation_chain_contains_eval_in_scope_return(&origin.k_origin)
    }

    fn materialize_vm_error_exception(module_attr: &str, message: &str) -> Option<PyException> {
        Python::attach(|py| {
            for module_name in ["doeff_vm", "doeff_vm.doeff_vm"] {
                let Ok(module) = PyModule::import(py, module_name) else {
                    continue;
                };
                let Ok(exc_type) = module.getattr(module_attr) else {
                    continue;
                };
                let Ok(exc_value) = exc_type.call1((message,)) else {
                    continue;
                };
                return Some(PyException::new_with_metadata(
                    exc_type.clone().unbind(),
                    exc_value.unbind(),
                    None,
                    crate::driver::PyExceptionMetadata::synthetic_vm_error(),
                ));
            }
            None
        })
    }

    fn recoverable_eval_in_scope_dispatch_exception(&self, error: &VMError) -> Option<PyException> {
        if !self.is_inside_eval_in_scope_subtopology() {
            return None;
        }

        let message = error.to_string();
        match error {
            VMError::UnhandledEffect { .. } => {
                Self::materialize_vm_error_exception("UnhandledEffectError", &message)
                    .or_else(|| Some(PyException::type_error(message)))
            }
            VMError::NoMatchingHandler { .. }
            | VMError::DelegateNoOuterHandler { .. }
            | VMError::HandlerNotFound { .. } => {
                Self::materialize_vm_error_exception("NoMatchingHandlerError", &message)
                    .or_else(|| Some(PyException::type_error(message)))
            }
            VMError::OneShotViolation { .. }
            | VMError::InvalidSegment { .. }
            | VMError::PythonError { .. }
            | VMError::InternalError { .. }
            | VMError::TypeError { .. }
            | VMError::UncaughtException { .. } => None,
        }
    }

    pub(super) fn dispatch_fatal_error_event(&mut self, error: VMError) -> StepEvent {
        if let Some(exception) = self.recoverable_eval_in_scope_dispatch_exception(&error) {
            self.set_contextual_internal_throw(exception);
            return StepEvent::Continue;
        }
        StepEvent::Error(error)
    }

    pub(super) fn eval_in_scope_chain_start_segment(
        &self,
        scope: &Continuation,
    ) -> Option<SegmentId> {
        // Lexical scope must anchor to the immediate captured scope
        // continuation. Dynamic handler/interceptor visibility is handled
        // separately via `child.parent`.
        self.continuation_chain_segment_id(scope)
    }

    fn root_delegate_parent_segment_id(&self, continuation: &Continuation) -> Option<SegmentId> {
        continuation
            .outermost_fiber_id()
            .filter(|seg_id| self.segments.get(*seg_id).is_some())
            .or_else(|| self.normalize_live_parent_hint(continuation.captured_caller()))
    }

    fn root_live_delegate_parent_segment_id(
        &self,
        continuation: &Continuation,
    ) -> Option<SegmentId> {
        self.normalize_live_parent_hint(continuation.captured_caller())
            .or_else(|| {
                continuation
                    .outermost_fiber_id()
                    .filter(|seg_id| self.segments.get(*seg_id).is_some())
            })
    }

    fn delegate_return_target_segment_id(&self, seg_id: SegmentId) -> Option<SegmentId> {
        let mut cursor = Some(seg_id);
        while let Some(current_seg_id) = cursor {
            let seg = self.segments.get(current_seg_id)?;
            if matches!(seg.kind, SegmentKind::InterceptorBoundary { .. }) && seg.frames.is_empty()
            {
                cursor = seg.parent;
                continue;
            }
            return Some(current_seg_id);
        }
        None
    }

    fn continuation_chain_segment_id(&self, continuation: &Continuation) -> Option<SegmentId> {
        continuation
            .segment_id()
            .filter(|seg_id| self.segments.get(*seg_id).is_some())
            .or_else(|| self.normalize_live_parent_hint(continuation.captured_caller()))
    }

    pub(super) fn continuation_handler_chain_start(
        &self,
        continuation: &Continuation,
    ) -> Option<SegmentId> {
        self.normalize_live_parent_hint(continuation.captured_caller())
            .or_else(|| self.continuation_chain_segment_id(continuation))
    }

    fn return_to_continuation(&self) -> Option<Continuation> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let Some(continuation) = seg.frames.iter().rev().find_map(|frame| match frame {
                Frame::EvalReturn(eval_return) => match eval_return.as_ref() {
                    EvalReturnContinuation::ReturnToContinuation { continuation } => {
                        Some(continuation.clone())
                    }
                    EvalReturnContinuation::ResumeToContinuation { .. }
                    | EvalReturnContinuation::EvalInScopeReturn { .. }
                    | EvalReturnContinuation::ApplyResolveFunction { .. }
                    | EvalReturnContinuation::ApplyResolveArg { .. }
                    | EvalReturnContinuation::ApplyResolveKwarg { .. }
                    | EvalReturnContinuation::ExpandResolveFactory { .. }
                    | EvalReturnContinuation::ExpandResolveArg { .. }
                    | EvalReturnContinuation::ExpandResolveKwarg { .. }
                    | EvalReturnContinuation::TailResumeReturn => None,
                },
                Frame::Program { .. }
                | Frame::InterceptorApply(_)
                | Frame::InterceptorEval(_)
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. }
                | Frame::InterceptBodyReturn { .. } => None,
            }) {
                return Some(continuation);
            }
            cursor = seg.parent;
        }
        None
    }

    fn is_internal_doeff_handler_source_file(source_file: &str) -> bool {
        let normalized = source_file.replace('\\', "/").to_lowercase();
        if !normalized.contains("/doeff/") {
            return false;
        }
        if normalized.contains("/tests/") || normalized.contains("/examples/") {
            return false;
        }

        [
            "/doeff/_",
            "/doeff/cache.py",
            "/doeff/do.py",
            "/doeff/effects/",
            "/doeff/handlers/",
            "/doeff/interpreter",
            "/doeff/kleisli",
            "/doeff/program",
            "/doeff/rust_vm.py",
            "/doeff/traceback.py",
            "/doeff/types",
            "/doeff/utils",
        ]
        .iter()
        .any(|pattern| normalized.contains(pattern))
    }

    fn is_user_defined_python_handler_marker(&self, marker: Marker) -> bool {
        self.marker_handler_trace_info(marker)
            .is_some_and(|(_, kind, file, _)| {
                kind == HandlerKind::Python
                    && file
                        .as_deref()
                        .is_some_and(|path| !Self::is_internal_doeff_handler_source_file(path))
            })
    }

    fn delegate_return_continuation(
        &mut self,
        continuation: &Continuation,
    ) -> Option<Continuation> {
        let seg_id = self
            .root_live_delegate_parent_segment_id(continuation)
            .and_then(|seg_id| self.delegate_return_target_segment_id(seg_id))?;
        if let Some(dispatch_id) = self.dispatch_state.segment_dispatch_id(seg_id) {
            if let Some((active_seg_id, continuation, _)) =
                self.handler_dispatch_for_any(dispatch_id)
            {
                if active_seg_id == seg_id && self.handler_dispatch_is_live(&continuation) {
                    return Some(continuation);
                }
            }
        }
        self.segments.get(seg_id)?;
        Some(
            self.capture_live_continuation(
                seg_id,
                self.dispatch_state.segment_dispatch_id(seg_id),
            ),
        )
    }

    pub fn instantiate_installed_handlers(&mut self) -> Option<SegmentId> {
        let installed = self.handlers.installed.clone();
        let mut outside_seg_id: Option<SegmentId> = None;
        for entry in installed.into_iter().rev() {
            let prompt_seg = Segment::new_prompt(
                entry.marker,
                outside_seg_id,
                entry.marker,
                entry.handler.clone(),
            );
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            self.copy_interceptor_guard_state(outside_seg_id, prompt_seg_id);
            self.initialize_builtin_prompt_segment(&entry.handler, prompt_seg_id);
            self.track_run_handler(&entry.handler);

            let body_seg = Segment::new(entry.marker, Some(prompt_seg_id));
            let body_seg_id = self.alloc_segment(body_seg);
            self.copy_interceptor_guard_state(outside_seg_id, body_seg_id);
            outside_seg_id = Some(body_seg_id);
        }
        outside_seg_id
    }

    fn initialize_builtin_prompt_segment(&mut self, handler: &KleisliRef, prompt_seg_id: SegmentId) {
        if handler.handler_name() == "StateHandler" {
            self.var_store
                .replace_handler_state(prompt_seg_id, self.rust_store.entries.clone());
        }
    }

    /// Copy interceptor guard state from a source segment to a child segment.
    ///
    /// **Why inheritance is required (not derivable from frames):**
    ///
    /// `interceptor_eval_depth` and `interceptor_skip_stack` are *dynamic guard
    /// context* that spans segment topology changes. They cannot be derived from
    /// the child segment's local frame stack because:
    ///
    /// 1. **Child segments start with empty frames.** A new handler segment
    ///    (created during dispatch at prompt boundaries) or a new interceptor body
    ///    segment (created by `prepare_with_intercept`) has no frames, yet it runs
    ///    within the parent's interceptor invocation context and must inherit the
    ///    guard state to prevent re-entrancy and double-evaluation.
    ///
    /// 2. **Delegate/pass rewrites the active handler segment.** Forwarding keeps only
    ///    the live `DispatchOrigin` on the current handler segment, but guard state must
    ///    survive so the next handler segment inherits the correct interceptor context.
    ///
    /// 3. **Typed continuation frames are local.** Interceptor guard state must
    ///    survive continuation capture/resume and segment topology rewrites even
    ///    when relevant continuation frames are no longer present locally.
    #[inline]
    pub(super) fn copy_interceptor_guard_state(
        &mut self,
        source_seg_id: Option<SegmentId>,
        child_seg_id: SegmentId,
    ) {
        self.inherit_interceptor_guard_state(source_seg_id, child_seg_id);
    }

    pub fn is_one_shot_consumed(&self, cont_id: ContId) -> bool {
        self.consumed_continuations.contains(&cont_id)
            || self
                .continuations
                .entries
                .get(&cont_id)
                .is_some_and(Continuation::consumed)
    }

    pub fn mark_one_shot_consumed(&mut self, cont_id: ContId) {
        self.consumed_continuations.insert(cont_id);
        if let Some(continuation) = self.continuations.entries.get_mut(&cont_id) {
            continuation.mark_consumed();
        }
        self.continuations.entries.remove(&cont_id);
    }

    pub fn register_continuation(&mut self, k: Continuation) {
        self.continuations.entries.insert(k.cont_id, k);
    }

    pub fn lookup_continuation(&self, cont_id: ContId) -> Option<&Continuation> {
        self.continuations.entries.get(&cont_id)
    }

    pub fn take_continuation(&mut self, cont_id: ContId) -> Option<Continuation> {
        self.continuations.entries.remove(&cont_id)
    }

    fn materialize_owned_continuation(
        &mut self,
        k: Continuation,
        op_name: &str,
    ) -> Result<Continuation, VMError> {
        if !k.is_started() || k.owns_fibers() {
            return Ok(k);
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return Err(VMError::one_shot_violation(k.cont_id));
        }
        if let Some(continuation) = self.take_continuation(k.cont_id) {
            if continuation.owns_fibers() {
                return Ok(continuation);
            }
            if continuation
                .fibers()
                .iter()
                .all(|fiber_id| self.segments.get(*fiber_id).is_some())
            {
                return Ok(continuation.into_owned());
            }
        }
        if k.fibers()
            .iter()
            .all(|fiber_id| self.segments.get(*fiber_id).is_some())
        {
            return Ok(k.into_owned());
        }
        Err(VMError::internal(format!(
            "{op_name} continuation {} is not owned by the registry",
            k.cont_id.raw()
        )))
    }

    fn annotate_live_continuation(&self, continuation: &mut Continuation, seg_id: SegmentId) {
        continuation.set_resume_dispatch_id(self.current_segment_dispatch_id_any());
        continuation.set_dispatch_handler_hint(self.handlers_in_caller_chain(seg_id).first().map(
            |entry| crate::continuation::DispatchHandlerHint {
                marker: entry.marker,
                prompt_seg_id: entry.prompt_seg_id,
            },
        ));
    }

    pub(crate) fn capture_live_continuation(
        &mut self,
        seg_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Continuation {
        let captured_caller = self.segments.get(seg_id).and_then(|segment| segment.parent);
        let mut continuation = Continuation::from_fiber(seg_id, captured_caller, dispatch_id);
        self.annotate_live_continuation(&mut continuation, seg_id);
        if let Some(segment) = self.segments.get_mut(seg_id) {
            segment.parent = None;
        }
        continuation
    }

    pub fn capture_continuation(
        &mut self,
        dispatch_id: Option<DispatchId>,
    ) -> Option<Continuation> {
        let seg_id = self.current_segment?;
        Some(self.capture_live_continuation(seg_id, dispatch_id))
    }

    pub(super) fn current_segment_dispatch_id(&self) -> Option<DispatchId> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let Some(dispatch_id) = self.dispatch_state.segment_dispatch_id(seg_id) {
                return Some(dispatch_id);
            }
            cursor = seg.parent;
        }
        None
    }

    pub(super) fn current_segment_dispatch_id_any(&self) -> Option<DispatchId> {
        self.current_segment_dispatch_id()
    }

    pub fn current_dispatch_id(&self) -> Option<DispatchId> {
        self.current_active_handler_dispatch_id()
            .or_else(|| self.current_segment_dispatch_id())
    }

    pub fn effect_for_dispatch(&self, dispatch_id: DispatchId) -> Option<DispatchEffect> {
        self.dispatch_origin_for_dispatch_id(dispatch_id)
            .map(|origin| origin.effect)
    }

    pub fn find_matching_handler(
        &self,
        handler_chain: &[Marker],
        effect: &DispatchEffect,
    ) -> Result<(usize, Marker, KleisliRef), VMError> {
        let effect_obj =
            Python::attach(|py| dispatch_to_pyobject(py, effect).map(|obj| obj.unbind())).map_err(
                |err| {
                    VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    ))
                },
            )?;
        for (idx, marker) in handler_chain.iter().copied().enumerate() {
            let Some((prompt_seg_id, handler, types)) = self.find_prompt_boundary_by_marker(marker)
            else {
                return Err(VMError::internal(format!(
                    "find_matching_handler: missing handler marker {} at index {}",
                    marker.raw(),
                    idx
                )));
            };
            if handler.can_handle(effect)?
                && self
                    .should_invoke_handler(
                        &HandlerChainEntry {
                            marker,
                            prompt_seg_id,
                            handler: handler.clone(),
                            types,
                        },
                        &effect_obj,
                    )
                    .map_err(|err| {
                        VMError::python_error(format!(
                            "failed to evaluate WithHandler type filter: {err:?}"
                        ))
                    })?
            {
                return Ok((idx, marker, handler));
            }
        }
        Err(VMError::no_matching_handler(effect.clone()))
    }

    pub fn start_dispatch(&mut self, effect: DispatchEffect) -> Result<StepEvent, VMError> {
        let seg_id = self
            .current_segment
            .ok_or_else(|| VMError::internal("no current segment during dispatch"))?;
        // DEEP-HANDLER SELF-DISPATCH EXCLUSION (Koka/OCaml-style semantics):
        //
        // KleisliRef clause code executes *above* its own prompt boundary. During that interval,
        // dispatch must not re-select the currently active handler prompt, otherwise a handler
        // that performs an effect matching itself can recurse indefinitely.
        //
        // We scope exclusion to "active handler execution segment" only, so normal user-code
        // dispatch still sees the full caller-chain handlers.
        //
        // Python handlers remain permissive for cross-effect yields (different Python effect
        // type), because user handlers frequently delegate across effect families in the same
        // clause body; however same-effect Python re-dispatch is excluded to prevent loops.
        //
        // Scheduler/AST-stream paths rely on strict tail handoff (Transfer) and this exclusion
        // together to keep dispatch/switch behavior bounded under heavy task churn.
        let original_exception = self
            .current_segment
            .and_then(|seg_id| self.pending_error_context(seg_id).cloned());
        let restricted_error_context_dispatch = Self::is_execution_context_effect(&effect)
            && original_exception
                .as_ref()
                .is_some_and(PyException::requires_safe_error_context_dispatch);
        let restricted_excluded_prompts: HashSet<SegmentId> = if restricted_error_context_dispatch {
            self.dispatch_state
                .segment_dispatch_id(seg_id)
                .and_then(|dispatch_id| {
                    self.dispatch_origin_for_dispatch_id(dispatch_id)
                        .map(|origin| {
                            self.handlers_in_caller_chain(
                                self.continuation_handler_chain_start(&origin.k_origin)
                                    .expect("dispatch origin continuations must be captured"),
                            )
                            .into_iter()
                            .map(|entry| entry.prompt_seg_id)
                            .collect()
                        })
                })
                .unwrap_or_default()
        } else {
            HashSet::new()
        };
        let exclude_prompt = self.current_handler_dispatch().and_then(
            |(active_seg_id, dispatch_id, _, active_marker, active_prompt_seg_id)| {
                if active_seg_id != seg_id {
                    return None;
                }
                let origin = self.dispatch_origin_for_dispatch_id(dispatch_id)?;
                let is_same_effect = Self::same_effect_python_type(&effect, &origin.effect);
                if !is_same_effect {
                    // Cross-effect yields from handler clauses should remain dispatchable.
                    // Only same-effect re-dispatch is excluded to prevent self-recursion.
                    return None;
                }
                let _ = active_marker;
                Some(active_prompt_seg_id)
            },
        );
        self.segments
            .get(seg_id)
            .ok_or_else(|| VMError::invalid_segment("current segment not found"))?;
        let dispatch_id = DispatchId::fresh();
        let effect_obj =
            Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|obj| obj.unbind()))
                .map_err(|err| {
                    VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    ))
                })?;

        let mut current_entries: Vec<HandlerChainEntry> = Vec::new();
        let mut cursor = Some(seg_id);
        while let Some(cursor_id) = cursor {
            let Some(seg) = self.segments.get(cursor_id) else {
                break;
            };
            let next = seg.parent;
            if let SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                types,
                ..
            } = &seg.kind
            {
                let is_shared_spawn_writer = handler.handler_name() == "WriterHandler"
                    && self.shared_builtin_handler_prompt(cursor_id) != cursor_id;
                let restricted_excluded = restricted_excluded_prompts.contains(&cursor_id);
                if Some(cursor_id) != exclude_prompt
                    && !restricted_excluded
                    && !is_shared_spawn_writer
                {
                    current_entries.push(HandlerChainEntry {
                        marker: *handled_marker,
                        prompt_seg_id: cursor_id,
                        handler: handler.clone(),
                        types: types.clone(),
                    });
                }
            }
            cursor = next;
        }

        let mut selected: Option<(usize, Marker, SegmentId, KleisliRef)> = None;
        let mut first_type_filtered_skip: Option<(usize, Marker, SegmentId, KleisliRef)> = None;
        let mut handler_chain_snapshot: Vec<HandlerSnapshotEntry> = Vec::new();
        let mut handler_count = 0usize;
        for entry in &current_entries {
            let handler = &entry.handler;
            let (name, kind, file, line) = Self::handler_trace_info(handler);
            handler_chain_snapshot.push(HandlerSnapshotEntry {
                handler_name: name,
                handler_kind: kind,
                source_file: file,
                source_line: line,
            });

            if handler.can_handle(&effect)? {
                let should_invoke = self
                    .should_invoke_handler_types(entry.types.as_ref(), &effect_obj)
                    .map_err(|err| {
                        VMError::python_error(format!(
                            "failed to evaluate WithHandler type filter: {err:?}"
                        ))
                    })?;
                if should_invoke {
                    if selected.is_none() {
                        selected = Some((
                            handler_count,
                            entry.marker,
                            entry.prompt_seg_id,
                            handler.clone(),
                        ));
                    }
                } else if first_type_filtered_skip.is_none() {
                    first_type_filtered_skip = Some((
                        handler_count,
                        entry.marker,
                        entry.prompt_seg_id,
                        handler.clone(),
                    ));
                }
            }

            handler_count += 1;
        }

        let full_current_entries = self.current_handler_chain();
        let outer_entries = if self.current_handler_dispatch().is_none() {
            self.return_to_continuation()
                .and_then(|continuation| {
                    self.live_handler_chain_start_for_return_to(&continuation)
                        .or_else(|| self.continuation_handler_chain_start(&continuation))
                })
                .map(|outer_start| self.handlers_in_caller_chain(outer_start))
                .unwrap_or_default()
        } else {
            Vec::new()
        };
        let outer_prefix_len = if outer_entries.is_empty() {
            0
        } else {
            Self::outer_handler_prefix_len(&full_current_entries, &outer_entries)
        };
        let prefer_outer_fallback = outer_prefix_len > 0
            && selected
                .as_ref()
                .is_some_and(|(_, _, _, selected_handler)| {
                    outer_entries[outer_prefix_len..]
                        .iter()
                        .any(|entry| Arc::ptr_eq(&entry.handler, selected_handler))
                });
        if prefer_outer_fallback {
            selected = None;
            first_type_filtered_skip = None;
            handler_count = 0;
        }
        let fallback_return_to = (selected.is_none())
            .then(|| self.return_to_continuation())
            .flatten();

        if selected.is_none() {
            let mut cursor = fallback_return_to.as_ref().and_then(|continuation| {
                self.live_handler_chain_start_for_return_to(continuation)
                    .or_else(|| self.continuation_handler_chain_start(continuation))
            });
            while let Some(cursor_id) = cursor {
                let Some(seg) = self.segments.get(cursor_id) else {
                    break;
                };
                let next = seg.parent;
                if let SegmentKind::PromptBoundary {
                    handled_marker,
                    handler,
                    types,
                    ..
                } = &seg.kind
                {
                    let restricted_excluded = restricted_excluded_prompts.contains(&cursor_id);
                    if Some(cursor_id) != exclude_prompt && !restricted_excluded {
                        let (name, kind, file, line) = Self::handler_trace_info(handler);
                        handler_chain_snapshot.push(HandlerSnapshotEntry {
                            handler_name: name,
                            handler_kind: kind,
                            source_file: file,
                            source_line: line,
                        });

                        if handler.can_handle(&effect)? {
                            let should_invoke = self
                                .should_invoke_handler_types(types.as_ref(), &effect_obj)
                                .map_err(|err| {
                                    VMError::python_error(format!(
                                        "failed to evaluate WithHandler type filter: {err:?}"
                                    ))
                                })?;
                            if should_invoke {
                                if selected.is_none() {
                                    selected = Some((
                                        handler_count,
                                        *handled_marker,
                                        cursor_id,
                                        handler.clone(),
                                    ));
                                }
                            } else if first_type_filtered_skip.is_none() {
                                first_type_filtered_skip = Some((
                                    handler_count,
                                    *handled_marker,
                                    cursor_id,
                                    handler.clone(),
                                ));
                            }
                        }

                        handler_count += 1;
                    }
                }
                cursor = next;
            }
        }

        if handler_count == 0 {
            if let Some(original) = original_exception.clone() {
                let exception = if restricted_error_context_dispatch {
                    TraceState::ensure_execution_context(original)
                } else {
                    original
                };
                self.mode = Mode::Throw(exception);
                return Ok(StepEvent::Continue);
            }
            return Err(VMError::unhandled_effect(effect));
        }

        let mut bootstrap_with_pass = false;
        let selected = match selected {
            Some(found) => {
                if let Some(skipped) = &first_type_filtered_skip {
                    if skipped.0 < found.0 {
                        bootstrap_with_pass = true;
                        skipped.clone()
                    } else {
                        found
                    }
                } else {
                    found
                }
            }
            None => {
                if let Some(original) = original_exception.clone() {
                    self.mode = Mode::Throw(original);
                    return Ok(StepEvent::Continue);
                }
                return Err(VMError::no_matching_handler(effect));
            }
        };

        let handler_marker = selected.1;
        let prompt_seg_id = selected.2;
        let handler = selected.3;
        let is_execution_context_effect = Self::is_execution_context_effect(&effect);
        if self.segments.get(prompt_seg_id).is_none() {
            return Err(VMError::invalid_segment("dispatch prompt not found"));
        }

        let resume_dispatch_id = self
            .current_segment
            .and_then(|current_seg_id| self.dispatch_state.segment_dispatch_id(current_seg_id));
        let current_hint = self.first_handler_hint_in_caller_chain(seg_id);
        let mut k_user = if Self::is_execution_context_effect(&effect)
            && original_exception.is_some()
        {
            let reusable_origin = self.current_dispatch_origin().filter(|origin| {
                let Some(current_original) = original_exception.as_ref() else {
                    return false;
                };
                let Some(origin_original) = origin.original_exception.as_ref() else {
                    return false;
                };
                let same_original_exception = match (origin_original, current_original) {
                    (
                        PyException::Materialized {
                            exc_value: origin_value,
                            ..
                        },
                        PyException::Materialized {
                            exc_value: current_value,
                            ..
                        },
                    ) => Python::attach(|py| {
                        origin_value.bind(py).as_ptr() == current_value.bind(py).as_ptr()
                    }),
                    _ => false,
                };
                if !same_original_exception {
                    return false;
                }
                let Some(origin_seg_id) = self.continuation_handler_chain_start(&origin.k_origin)
                else {
                    return false;
                };
                let Some(current_hint) = current_hint else {
                    return true;
                };
                self.find_prompt_boundary_in_caller_chain(origin_seg_id, current_hint.marker)
                    .is_some()
            });
            reusable_origin
                .map(|origin| origin.k_origin.clone_for_dispatch(Some(dispatch_id)))
                .unwrap_or_else(|| self.capture_live_continuation(seg_id, Some(dispatch_id)))
        } else {
            self.capture_live_continuation(seg_id, Some(dispatch_id))
        };
        k_user.set_resume_dispatch_id(resume_dispatch_id);
        k_user.set_dispatch_handler_hint(current_hint);
        if let Some(return_to) = fallback_return_to {
            k_user.append_owned_fibers(return_to.clone_for_dispatch(Some(dispatch_id)));
        }
        if let Some(seg_id) = self.current_segment {
            self.clear_pending_error_context(seg_id);
        }

        let handler_seg = Segment::new(handler_marker, Some(prompt_seg_id));
        let handler_seg_id = self.alloc_segment(handler_seg);
        self.copy_interceptor_guard_state(Some(seg_id), handler_seg_id);
        self.set_scope_parent(handler_seg_id, Some(seg_id));
        let handler_k = k_user.clone_handle();
        let origin_k = handler_k.clone_handle();
        let active_k = handler_k.clone_handle();
        self.register_continuation(k_user);
        self.dispatch_state.start_dispatch(
            dispatch_id,
            effect.clone(),
            origin_k,
            original_exception.clone(),
            crate::dispatch_state::ActiveHandlerContext {
                segment_id: handler_seg_id,
                continuation: active_k,
                marker: handler_marker,
                prompt_seg_id,
            },
        );
        self.current_segment = Some(handler_seg_id);

        let effect_frames = self.continuation_frame_stack(&handler_k);
        let effect_site = TraceState::effect_site_from_frames(&effect_frames);
        self.trace_state.record_dispatch_started(
            dispatch_id,
            Self::effect_repr(&effect),
            is_execution_context_effect,
            &handler_chain_snapshot,
            effect_site.as_ref().map(|(frame_id, _, _, _)| *frame_id),
            effect_site
                .as_ref()
                .map(|(_, function_name, _, _)| function_name.clone()),
            effect_site
                .as_ref()
                .map(|(_, _, source_file, _)| source_file.clone()),
            effect_site
                .as_ref()
                .map(|(_, _, _, source_line)| *source_line),
        );

        // Preserve handler scope when a type-filtered handler is skipped: this mirrors the
        // `Pass()` forwarding topology without invoking the skipped handler body.
        if bootstrap_with_pass {
            return Ok(self.handle_forward(ForwardKind::Pass, effect));
        }

        let ir_node = Self::invoke_kleisli_handler_expr(handler, effect, handler_k)?;
        Ok(self.evaluate(ir_node))
    }

    fn error_dispatch_for_continuation(
        &self,
        k: &Continuation,
    ) -> Option<(DispatchId, PyException, bool)> {
        let origin = self
            .exact_dispatch_origin_for_continuation(k)
            .or_else(|| self.dispatch_origin_for_continuation(k))?;
        let original = origin.original_exception?;
        Some((
            origin.dispatch_id,
            original,
            k.cont_id == origin.k_origin.cont_id,
        ))
    }

    fn dispatch_has_terminal_handler_action(&self, dispatch_id: DispatchId) -> bool {
        self.trace_state.dispatch_has_terminal_result(dispatch_id)
    }

    pub(super) fn finalize_active_dispatches_as_threw(&mut self, exception: &PyException) {
        let exception_repr = Self::exception_repr(exception);
        for origin in self.dispatch_origins() {
            let dispatch_id = origin.dispatch_id;
            if self.dispatch_has_terminal_handler_action(dispatch_id) {
                continue;
            }
            let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            else {
                continue;
            };
            self.trace_state.record_handler_completed(
                dispatch_id,
                &handler_name,
                handler_index,
                &HandlerAction::Threw {
                    exception_repr: exception_repr.clone(),
                },
            );
        }
    }

    pub fn install_handler(
        &mut self,
        marker: Marker,
        handler: KleisliRef,
        _py_identity: Option<PyShared>,
    ) {
        self.handlers
            .installed
            .retain(|entry| entry.marker != marker);
        self.handlers
            .installed
            .push(InstalledHandler { marker, handler });
    }

    pub fn remove_handler(&mut self, marker: Marker) -> bool {
        let before = self.handlers.installed.len();
        self.handlers
            .installed
            .retain(|entry| entry.marker != marker);
        before != self.handlers.installed.len()
    }

    pub fn installed_handler_markers(&self) -> Vec<Marker> {
        self.handlers
            .installed
            .iter()
            .map(|entry| entry.marker)
            .collect()
    }

    pub fn install_handler_on_segment(
        &mut self,
        marker: Marker,
        prompt_seg_id: SegmentId,
        handler: KleisliRef,
        _py_identity: Option<PyShared>,
    ) -> bool {
        let Some(seg) = self.segments.get_mut(prompt_seg_id) else {
            let prompt_seg = Segment::new_prompt(marker, None, marker, handler.clone());
            self.alloc_segment(prompt_seg);
            self.track_run_handler(&handler);
            return true;
        };
        let seg_marker = seg.marker();
        seg.kind = SegmentKind::PromptBoundary {
            marker: seg_marker,
            handled_marker: marker,
            handler: handler.clone(),
            types: None,
        };
        self.track_run_handler(&handler);
        true
    }

    fn record_continuation_activation(
        &mut self,
        kind: ContinuationActivationKind,
        k: &Continuation,
        value: &Value,
    ) {
        if kind.is_transferred() && self.exact_dispatch_origin_for_continuation(k).is_none() {
            return;
        }
        if let Some(dispatch_id) = k.dispatch_id() {
            if let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            {
                let value_repr = Self::value_repr(value);
                self.trace_state.record_handler_completed(
                    dispatch_id,
                    &handler_name,
                    handler_index,
                    &kind.handler_action(value_repr.clone()),
                );
                self.emit_resume_event(dispatch_id, k, kind.is_transferred());
            }
        }
    }

    fn continuation_segment_dispatch_id(&mut self, k: &Continuation) -> Option<DispatchId> {
        k.dispatch_id()
            .filter(|dispatch_id| self.dispatch_origin_for_dispatch_id(*dispatch_id).is_some())
    }

    fn alloc_resume_return_anchor(
        &mut self,
        caller: Option<SegmentId>,
        continuation: Continuation,
        dispatch_id: Option<DispatchId>,
    ) -> SegmentId {
        let mut anchor = Segment::new(Marker::fresh(), caller);
        anchor.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::ResumeToContinuation { continuation },
        )));
        let anchor_seg_id = self.alloc_segment(anchor);
        self.copy_interceptor_guard_state(self.current_segment, anchor_seg_id);
        self.set_scope_parent(anchor_seg_id, caller);
        if let Some(dispatch_id) = dispatch_id {
            self.dispatch_state
                .bind_segment(anchor_seg_id, dispatch_id);
        }
        anchor_seg_id
    }

    fn alloc_tail_resume_anchor(
        &mut self,
        caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) -> SegmentId {
        let mut anchor = Segment::new(Marker::fresh(), caller);
        anchor.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::TailResumeReturn,
        )));
        let anchor_seg_id = self.alloc_segment(anchor);
        self.copy_interceptor_guard_state(self.current_segment, anchor_seg_id);
        self.set_scope_parent(anchor_seg_id, caller);
        if let Some(dispatch_id) = dispatch_id {
            self.dispatch_state
                .bind_segment(anchor_seg_id, dispatch_id);
        }
        anchor_seg_id
    }

    fn segment_is_tail_resume_return(&self, seg_id: SegmentId) -> bool {
        let Some(seg) = self.segments.get(seg_id) else {
            return false;
        };
        let Some(stream) = seg.frames.iter().rev().find_map(|frame| match frame {
            Frame::Program { stream, .. } => Some(stream.clone()),
            Frame::InterceptorApply(_)
            | Frame::InterceptorEval(_)
            | Frame::EvalReturn(_)
            | Frame::MapReturn { .. }
            | Frame::FlatMapBindResult
            | Frame::FlatMapBindSource { .. }
            | Frame::InterceptBodyReturn { .. } => None,
        }) else {
            return false;
        };
        stream
            .lock()
            .ok()
            .is_some_and(|stream| stream.is_tail_resume_return())
    }

    fn abandoned_branch_root_for_transfer(
        &self,
        continuation: &Continuation,
        preserved_ancestor: Option<SegmentId>,
    ) -> Option<SegmentId> {
        let current_seg_id = self.current_segment?;
        let mut cursor = current_seg_id;
        let mut child_below_preserved = None;

        loop {
            if Some(cursor) == preserved_ancestor || continuation.fibers().contains(&cursor) {
                return child_below_preserved;
            }
            let Some(segment) = self.segments.get(cursor) else {
                return Some(current_seg_id);
            };
            if matches!(segment.kind, SegmentKind::InterceptorBoundary { .. })
                || self.interceptor_eval_depth(cursor) > 0
                || !self.interceptor_skip_stack_is_empty(cursor)
                || segment.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::InterceptorApply(_)
                            | Frame::InterceptorEval(_)
                            | Frame::InterceptBodyReturn { .. }
                    )
                })
            {
                return child_below_preserved;
            }
            let parent = segment.parent;
            child_below_preserved = Some(cursor);
            match parent {
                Some(parent_id) => cursor = parent_id,
                None => return Some(current_seg_id),
            }
        }
    }

    fn free_segment_subtree(&mut self, root_seg_id: SegmentId) {
        let mut stack = vec![root_seg_id];
        let mut order = Vec::new();
        let mut seen = HashSet::new();

        while let Some(seg_id) = stack.pop() {
            if !seen.insert(seg_id) {
                continue;
            }
            order.push(seg_id);
            for (child_id, segment) in self.segments.iter() {
                if segment.parent == Some(seg_id) {
                    stack.push(child_id);
                }
            }
        }

        for seg_id in order.into_iter().rev() {
            let caller = self.segments.get(seg_id).and_then(|segment| segment.parent);
            let scope_parent = self.scope_parent(seg_id);
            self.reparent_children(seg_id, caller, scope_parent);
            self.free_segment(seg_id);
        }
    }

    fn abandon_current_live_branch_for_transfer(
        &mut self,
        continuation: &Continuation,
        preserved_ancestor: Option<SegmentId>,
    ) {
        let Some(root_seg_id) =
            self.abandoned_branch_root_for_transfer(continuation, preserved_ancestor)
        else {
            return;
        };
        self.free_segment_subtree(root_seg_id);
        self.current_segment = self.normalize_live_parent_hint(preserved_ancestor);
    }

    fn live_branch_requires_transfer_abandon(
        &self,
        continuation: &Continuation,
        preserved_ancestor: Option<SegmentId>,
    ) -> bool {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            if Some(seg_id) == preserved_ancestor || continuation.fibers().contains(&seg_id) {
                return false;
            }
            let Some(seg) = self.segments.get(seg_id) else {
                return false;
            };
            if matches!(seg.kind, SegmentKind::InterceptorBoundary { .. })
                || self.interceptor_eval_depth(seg_id) > 0
                || !self.interceptor_skip_stack_is_empty(seg_id)
                || seg.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::InterceptorApply(_)
                            | Frame::InterceptorEval(_)
                            | Frame::InterceptBodyReturn { .. }
                    )
                })
            {
                return true;
            }
            cursor = seg.parent;
        }
        false
    }

    fn chain_has_interceptor_context(&self, start: Option<SegmentId>) -> bool {
        let mut cursor = start;
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                return false;
            };
            if matches!(seg.kind, SegmentKind::InterceptorBoundary { .. })
                || self.interceptor_eval_depth(seg_id) > 0
                || !self.interceptor_skip_stack_is_empty(seg_id)
                || seg.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::InterceptorApply(_)
                            | Frame::InterceptorEval(_)
                            | Frame::InterceptBodyReturn { .. }
                    )
                })
            {
                return true;
            }
            cursor = seg.parent;
        }
        false
    }

    fn enter_or_reenter_continuation_segment_with_dispatch(
        &mut self,
        k: &Continuation,
        caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) {
        let caller = self.normalize_live_parent_hint(caller);
        let Some(seg_id) = k.segment_id() else {
            return;
        };
        let existing_caller = self.segments.get(seg_id).and_then(|seg| seg.parent);
        let caller = if self.chain_has_interceptor_context(existing_caller)
            && !self.chain_has_interceptor_context(caller)
        {
            existing_caller
        } else {
            caller
        };
        let exact_origin_before_bind = dispatch_id.and_then(|dispatch_id| {
            (k.dispatch_id() == Some(dispatch_id))
                .then(|| self.exact_dispatch_origin_for_continuation(k).is_some())
        });
        let fiber_ids = k.fibers().to_vec();
        for (index, fiber_id) in fiber_ids.iter().enumerate() {
            let Some(seg) = self.segments.get_mut(*fiber_id) else {
                continue;
            };
            seg.parent = fiber_ids.get(index + 1).copied().or(caller);
        }
        for fiber_id in &fiber_ids {
            self.clear_throw_parent(*fiber_id);
            // The original exception lives on the active DispatchOrigin.k_origin.
            // Reinstalling it onto resumed continuation segments makes unrelated
            // nested Perform() calls look like fresh GetExecutionContext dispatches.
            self.clear_pending_error_context(*fiber_id);
        }

        match dispatch_id {
            Some(dispatch_id) => {
                self.dispatch_state.bind_segment(seg_id, dispatch_id);
                if let Some(hint) = k.dispatch_handler_hint() {
                    let restoring_outer_dispatch = k.dispatch_id() != Some(dispatch_id);
                    let resuming_user_defined_python_handler =
                        self.is_user_defined_python_handler_marker(hint.marker);
                    if restoring_outer_dispatch
                        || !exact_origin_before_bind.unwrap_or(false)
                        || resuming_user_defined_python_handler
                    {
                        let (marker, prompt_seg_id) = if restoring_outer_dispatch {
                            self.dispatch_state
                                .dispatch(dispatch_id)
                                .map(|dispatch| {
                                    (
                                        dispatch.active_handler.marker,
                                        dispatch.active_handler.prompt_seg_id,
                                    )
                                })
                                .unwrap_or((hint.marker, hint.prompt_seg_id))
                        } else {
                            (hint.marker, hint.prompt_seg_id)
                        };
                        let continuation = if restoring_outer_dispatch {
                            self.dispatch_state
                                .dispatch(dispatch_id)
                                .map(|dispatch| dispatch.active_handler.continuation.clone_handle())
                                .unwrap_or_else(|| k.clone_handle())
                        } else {
                            k.clone_for_dispatch(Some(dispatch_id))
                        };
                        self.dispatch_state.update_forwarded_dispatch(
                            dispatch_id,
                            None,
                            None,
                            crate::dispatch_state::ActiveHandlerContext {
                                segment_id: seg_id,
                                continuation,
                                marker,
                                prompt_seg_id,
                            },
                        );
                    }
                }
            }
            None => self.dispatch_state.unbind_segment(seg_id),
        }
        self.current_segment = Some(seg_id);
    }

    fn activate_continuation(
        &mut self,
        kind: ContinuationActivationKind,
        mut k: Continuation,
        mut value: Value,
        caller: Option<SegmentId>,
    ) -> StepEvent {
        if !k.is_started() {
            return self.throw_runtime_error(kind.unstarted_error_message());
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        k.mark_consumed();
        self.mark_one_shot_consumed(k.cont_id);
        let error_dispatch = self.error_dispatch_for_continuation(&k);
        self.record_continuation_activation(kind, &k, &value);
        if self.exact_dispatch_origin_for_continuation(&k).is_some() {
            if let Err(err) =
                self.maybe_attach_active_chain_to_execution_context(k.dispatch_id(), &mut value)
            {
                return StepEvent::Error(err);
            }
        }

        if let Some((dispatch_id, original_exception, terminal)) = error_dispatch {
            if terminal {
                let active_chain = self
                    .assemble_active_chain(Some(&original_exception))
                    .into_iter()
                    .filter(|entry| !matches!(entry, ActiveChainEntry::ContextEntry { .. }))
                    .collect();
                let enriched_exception = match TraceState::enrich_original_exception_with_context(
                    original_exception,
                    value,
                    active_chain,
                ) {
                    Ok(exception) => exception,
                    Err(effect_err) => effect_err,
                };
                self.finish_dispatch_tracking(dispatch_id);
                // Terminal error-context dispatches must detach from the active handler
                // segment so normal completion does not re-pop the same DispatchOrigin.
                let caller = k.captured_caller();
                self.enter_or_reenter_continuation_segment_with_dispatch(&k, caller, None);
                self.mode = Mode::Throw(enriched_exception);
                return StepEvent::Continue;
            }
        }

        let exact_origin = self.exact_dispatch_origin_for_continuation(&k);
        let dispatch_id = match kind {
            ContinuationActivationKind::Transfer | ContinuationActivationKind::Resume => {
                if exact_origin.is_some() {
                    k.resume_dispatch_id()
                } else {
                    self.continuation_segment_dispatch_id(&k)
                }
            }
        };
        if kind.is_transferred() {
            let preserved_ancestor = if self.chain_has_interceptor_context(caller) {
                caller
            } else {
                self.current_handler_dispatch()
                    .map(|(_, _, _, _, prompt_seg_id)| prompt_seg_id)
                    .or(caller)
            };
            if self.live_branch_requires_transfer_abandon(&k, preserved_ancestor) {
                self.abandon_current_live_branch_for_transfer(&k, preserved_ancestor);
            }
        }
        self.enter_or_reenter_continuation_segment_with_dispatch(&k, caller, dispatch_id);
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    pub(super) fn handle_dispatch_resume(&mut self, k: Continuation, value: Value) -> StepEvent {
        let k = match self.materialize_owned_continuation(k, "Resume") {
            Ok(continuation) => continuation,
            Err(err) => return StepEvent::Error(err),
        };
        let current_dispatch_id = self.current_dispatch_id();
        let exact_origin_target = self.exact_dispatch_origin_for_continuation(&k).is_some();
        let caller = k
            .dispatch_id()
            .filter(|dispatch_id| current_dispatch_id == Some(*dispatch_id))
            .and_then(|dispatch_id| {
                self.current_handler_dispatch()
                    .filter(|(_, current_dispatch_id, ..)| *current_dispatch_id == dispatch_id)
                    .and_then(
                        |(handler_seg_id, _, _continuation, marker, _prompt_seg_id)| {
                            if self.is_user_defined_python_handler_marker(marker) {
                                if self.segment_is_tail_resume_return(handler_seg_id) {
                                    let anchor_seg_id = self.alloc_tail_resume_anchor(
                                        k.captured_caller(),
                                        Some(dispatch_id),
                                    );
                                    return Some(anchor_seg_id);
                                }
                                let handler_return = self
                                    .capture_continuation(Some(dispatch_id))
                                    .expect("dispatch resume requires a live handler segment");
                                let anchor_seg_id = self.alloc_resume_return_anchor(
                                    k.captured_caller(),
                                    handler_return,
                                    Some(dispatch_id),
                                );
                                return Some(anchor_seg_id);
                            }
                            if exact_origin_target {
                                let handler_return = self
                                    .capture_continuation(Some(dispatch_id))
                                    .expect("dispatch resume requires a live handler segment");
                                let anchor_seg_id = self.alloc_resume_return_anchor(
                                    k.captured_caller(),
                                    handler_return,
                                    Some(dispatch_id),
                                );
                                return Some(anchor_seg_id);
                            }
                            None
                        },
                    )
            })
            .or_else(|| k.captured_caller());
        self.activate_continuation(ContinuationActivationKind::Resume, k, value, caller)
    }

    pub(super) fn handle_dispatch_transfer(&mut self, k: Continuation, value: Value) -> StepEvent {
        let k = match self.materialize_owned_continuation(k, "Transfer") {
            Ok(continuation) => continuation,
            Err(err) => return StepEvent::Error(err),
        };
        let caller = k.captured_caller();
        self.activate_continuation(ContinuationActivationKind::Transfer, k, value, caller)
    }

    fn activate_throw_continuation(
        &mut self,
        k: Continuation,
        exception: PyException,
        terminal_dispatch_completion: bool,
    ) -> StepEvent {
        let mut k = match self.materialize_owned_continuation(k, "Throw") {
            Ok(continuation) => continuation,
            Err(err) => return StepEvent::Error(err),
        };
        if !k.is_started() {
            return self.throw_runtime_error(
                "cannot throw into an unstarted continuation; use ResumeContinuation",
            );
        }
        if self.is_one_shot_consumed(k.cont_id) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        let handler_identity = k
            .dispatch_id()
            .and_then(|dispatch_id| self.current_handler_identity_for_dispatch(dispatch_id));
        k.mark_consumed();
        self.mark_one_shot_consumed(k.cont_id);
        let mut thrown_by_context_conversion_handler = self
            .current_active_handler_dispatch_id()
            .is_some_and(|dispatch_id| {
                self.dispatch_supports_error_context_conversion(dispatch_id)
            });
        let mut throws_into_dispatch_origin = false;
        if let Some(dispatch_id) = k.dispatch_id() {
            throws_into_dispatch_origin = self
                .dispatch_origin_for_dispatch_id(dispatch_id)
                .is_some_and(|origin| origin.k_origin.cont_id == k.cont_id);
            thrown_by_context_conversion_handler =
                self.dispatch_supports_error_context_conversion(dispatch_id);
            if !self.dispatch_has_terminal_handler_action(dispatch_id) {
                if let Some((handler_index, handler_name)) = handler_identity.as_ref() {
                    self.trace_state.record_handler_completed(
                        dispatch_id,
                        handler_name,
                        *handler_index,
                        &HandlerAction::Threw {
                            exception_repr: Self::exception_repr(&exception),
                        },
                    );
                }
            }
        }
        let current_dispatch_id = self.current_dispatch_id();
        let caller = if terminal_dispatch_completion {
            k.captured_caller()
        } else {
            k.dispatch_id()
                .filter(|dispatch_id| current_dispatch_id == Some(*dispatch_id))
                .and_then(|dispatch_id| {
                    self.current_handler_dispatch()
                        .filter(|(_, current_dispatch_id, ..)| *current_dispatch_id == dispatch_id)
                        .map(|(handler_seg_id, ..)| handler_seg_id)
                })
                .or_else(|| k.captured_caller())
        };
        let dispatch_id = if self.exact_dispatch_origin_for_continuation(&k).is_some() {
            k.resume_dispatch_id()
                .or_else(|| self.continuation_segment_dispatch_id(&k))
        } else {
            self.continuation_segment_dispatch_id(&k)
        };
        let throws_during_execution_context_dispatch = dispatch_id.is_some_and(|dispatch_id| {
            self.effect_for_dispatch(dispatch_id)
                .is_some_and(|effect| Self::is_execution_context_effect(&effect))
        });
        let original_exception =
            dispatch_id.and_then(|dispatch_id| self.original_exception_for_dispatch(dispatch_id));
        let enter_dispatch_id = if terminal_dispatch_completion && throws_into_dispatch_origin {
            if let Some(dispatch_id) = dispatch_id {
                self.finish_dispatch_tracking(dispatch_id);
            }
            None
        } else {
            dispatch_id
        };
        self.enter_or_reenter_continuation_segment_with_dispatch(&k, caller, enter_dispatch_id);
        self.mode = if terminal_dispatch_completion {
            if throws_into_dispatch_origin {
                Mode::Throw(exception)
            } else if throws_during_execution_context_dispatch {
                if let Some(original) = original_exception {
                    TraceState::set_exception_cause(&exception, &original);
                }
                Mode::Throw(exception)
            } else {
                self.mode_after_generror(
                    GenErrorSite::RustProgramContinuation,
                    exception,
                    thrown_by_context_conversion_handler,
                )
            }
        } else {
            Mode::Throw(exception)
        };
        StepEvent::Continue
    }

    pub(super) fn handle_transfer_throw(
        &mut self,
        k: Continuation,
        exception: PyException,
    ) -> StepEvent {
        self.activate_throw_continuation(k, exception, true)
    }

    pub(super) fn handle_transfer_throw_non_terminal(
        &mut self,
        k: Continuation,
        exception: PyException,
    ) -> StepEvent {
        self.activate_throw_continuation(k, exception, false)
    }

    pub(super) fn handle_with_handler(
        &mut self,
        handler: KleisliRef,
        program: DoCtrl,
        types: Option<Vec<PyShared>>,
    ) -> StepEvent {
        let plan = match Self::prepare_with_handler(handler, self.current_segment) {
            Ok(plan) => plan,
            Err(err) => return StepEvent::Error(err),
        };
        let prompt_handler = plan.handler.clone();

        let prompt_seg = Segment::new_prompt_with_types(
            plan.handler_marker,
            Some(plan.outside_seg_id),
            plan.handler_marker,
            prompt_handler.clone(),
            types,
        );
        let prompt_seg_id = self.alloc_segment(prompt_seg);
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), prompt_seg_id);
        self.initialize_builtin_prompt_segment(&prompt_handler, prompt_seg_id);
        self.track_run_handler(&prompt_handler);

        let body_seg = Segment::new(plan.handler_marker, Some(prompt_seg_id));
        let body_seg_id = self.alloc_segment(body_seg);
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), body_seg_id);

        self.current_segment = Some(body_seg_id);
        self.evaluate(program)
    }

    pub(super) fn handle_with_intercept(
        &mut self,
        interceptor: KleisliRef,
        program: DoCtrl,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    ) -> StepEvent {
        let (interceptor_marker, outside_seg_id) = match self.current_segment {
            Some(seg_id) => (Marker::fresh(), seg_id),
            None => {
                return StepEvent::Error(VMError::internal("no current segment for WithIntercept"))
            }
        };

        let mut boundary_seg = Segment::new(interceptor_marker, Some(outside_seg_id));
        boundary_seg.kind = SegmentKind::InterceptorBoundary {
            marker: interceptor_marker,
            interceptor,
            types,
            mode,
            metadata,
        };
        let boundary_seg_id = self.alloc_segment(boundary_seg);
        self.copy_interceptor_guard_state(Some(outside_seg_id), boundary_seg_id);
        self.set_scope_parent(boundary_seg_id, Some(outside_seg_id));

        let body_seg = Segment::new(interceptor_marker, Some(boundary_seg_id));
        let body_seg_id = self.alloc_segment(body_seg);
        self.copy_interceptor_guard_state(Some(outside_seg_id), body_seg_id);
        self.set_scope_parent(body_seg_id, Some(outside_seg_id));

        self.current_segment = Some(body_seg_id);
        self.evaluate(program)
    }

    fn emit_forward_active_chain_event(
        &mut self,
        kind: ForwardKind,
        dispatch_id: DispatchId,
        from_idx: usize,
        to_idx: usize,
    ) {
        match kind {
            ForwardKind::Delegate => {
                self.trace_state
                    .record_delegated(dispatch_id, from_idx, to_idx);
            }
            ForwardKind::Pass => {
                self.trace_state
                    .record_passed(dispatch_id, from_idx, to_idx);
            }
        }
    }

    fn make_pass_continuation(
        &mut self,
        dispatch_id: DispatchId,
        prompt_seg_id: SegmentId,
        handler_marker: Marker,
        parent_k_user: &Continuation,
    ) -> Result<Continuation, VMError> {
        let Some(prompt_seg) = self.segments.get(prompt_seg_id) else {
            return Err(VMError::invalid_segment(
                "Pass forwarding prompt segment not found",
            ));
        };
        let (handler, types, prompt_caller) = match &prompt_seg.kind {
            SegmentKind::PromptBoundary { handler, types, .. } => {
                (handler.clone(), types.clone(), prompt_seg.parent)
            }
            SegmentKind::Normal { .. }
            | SegmentKind::InterceptorBoundary { .. }
            | SegmentKind::MaskBoundary { .. } => {
                return Err(VMError::internal(
                    "Pass forwarding requires current prompt boundary segment",
                ))
            }
        };
        let mut wrapper_caller = prompt_caller;
        wrapper_caller = parent_k_user
            .captured_caller()
            .and_then(|seg_id| self.normalize_live_parent_hint(Some(seg_id)))
            .or_else(|| {
                parent_k_user
                    .outermost_fiber_id()
                    .filter(|seg_id| self.segments.get(*seg_id).is_some())
            })
            .or(wrapper_caller);

        let mut pass_seg = Segment::new_prompt_with_types(
            Marker::fresh(),
            wrapper_caller,
            handler_marker,
            handler,
            types,
        );
        let captured_caller = wrapper_caller.or_else(|| {
            self.root_delegate_parent_segment_id(parent_k_user)
                .or_else(|| self.continuation_chain_segment_id(parent_k_user))
        });
        let eval_return = if self.continuation_chain_contains_return_to_continuation(parent_k_user)
        {
            EvalReturnContinuation::ReturnToContinuation {
                continuation: parent_k_user.clone(),
            }
        } else {
            EvalReturnContinuation::ResumeToContinuation {
                continuation: parent_k_user.clone(),
            }
        };
        pass_seg.push_frame(Frame::EvalReturn(Box::new(eval_return)));
        let pass_cont_id = ContId::fresh();
        pass_seg.parent = None;
        let pass_seg_id = self.alloc_segment(pass_seg);
        self.copy_interceptor_guard_state(Some(prompt_seg_id), pass_seg_id);
        self.set_scope_parent(
            pass_seg_id,
            self.eval_in_scope_chain_start_segment(parent_k_user),
        );
        let mut pass_cont = Continuation::with_id(
            pass_cont_id,
            pass_seg_id,
            captured_caller,
            Some(dispatch_id),
        );
        pass_cont.set_resume_dispatch_id(parent_k_user.resume_dispatch_id());
        pass_cont.set_dispatch_handler_hint(Some(crate::continuation::DispatchHandlerHint {
            marker: handler_marker,
            prompt_seg_id,
        }));
        Ok(pass_cont)
    }

    fn handle_forward(&mut self, kind: ForwardKind, effect: DispatchEffect) -> StepEvent {
        let Some(dispatch_id) = self.current_dispatch_id() else {
            return StepEvent::Error(VMError::internal(kind.outside_dispatch_error()));
        };
        let Some(origin) = self.dispatch_origin_for_dispatch_id(dispatch_id) else {
            return StepEvent::Error(VMError::internal(format!(
                "{}: dispatch {} not found",
                kind.missing_handler_context(),
                dispatch_id.raw()
            )));
        };
        let Some((inner_seg_id, _, parent_k_user, current_marker, current_prompt_seg_id)) =
            self.nearest_handler_dispatch()
        else {
            return StepEvent::Error(VMError::internal(format!(
                "{}: active handler dispatch {} not found",
                kind.missing_handler_context(),
                dispatch_id.raw()
            )));
        };
        let handler_chain_start = match self.caller_visible_handler_chain_start() {
            Ok(seg_id) => seg_id,
            Err(err) => return StepEvent::Error(err),
        };
        let mut handler_chain = self.handlers_in_caller_chain(handler_chain_start);
        let from_idx = if let Some(idx) = handler_chain
            .iter()
            .position(|entry| entry.marker == current_marker)
        {
            idx
        } else {
            let Some(current_entry) = self.segments.get(current_prompt_seg_id).and_then(|seg| {
                let SegmentKind::PromptBoundary {
                    handled_marker,
                    handler,
                    types,
                    ..
                } = &seg.kind
                else {
                    return None;
                };
                Some(HandlerChainEntry {
                    marker: *handled_marker,
                    prompt_seg_id: current_prompt_seg_id,
                    handler: handler.clone(),
                    types: types.clone(),
                })
            }) else {
                return StepEvent::Error(VMError::internal(format!(
                    "{}: current handler marker {} not found in caller chain",
                    kind.missing_handler_context(),
                    current_marker.raw()
                )));
            };
            handler_chain.insert(0, current_entry);
            0
        };
        let outer_caller = self
            .segments
            .get(current_prompt_seg_id)
            .and_then(|seg| seg.parent);
        let visible_chain = handler_chain
            .iter()
            .skip(from_idx + 1)
            .cloned()
            .collect::<Vec<_>>();
        let next_k = match kind {
            ForwardKind::Delegate => {
                let Some(mut k_new) = self.capture_continuation(Some(dispatch_id)) else {
                    return StepEvent::Error(VMError::internal(
                        "Delegate called without current segment",
                    ));
                };
                let parent_owned = if parent_k_user.owns_fibers() {
                    parent_k_user.clone()
                } else {
                    match self.take_continuation(parent_k_user.cont_id) {
                        Some(continuation) => {
                            self.register_continuation(continuation.clone_handle());
                            continuation.into_owned()
                        }
                        None if self.is_one_shot_consumed(parent_k_user.cont_id) => {
                            return StepEvent::Error(VMError::one_shot_violation(
                                parent_k_user.cont_id,
                            ))
                        }
                        None => {
                            return StepEvent::Error(VMError::internal(format!(
                                "Delegate parent continuation {} missing from registry",
                                parent_k_user.cont_id.raw()
                            )))
                        }
                    }
                };
                k_new.append_owned_fibers(parent_owned);
                k_new
            }
            ForwardKind::Pass => match self.make_pass_continuation(
                dispatch_id,
                current_prompt_seg_id,
                current_marker,
                &parent_k_user,
            ) {
                Ok(k_new) => k_new,
                Err(err) => return StepEvent::Error(err),
            },
        };
        match kind {
            ForwardKind::Delegate | ForwardKind::Pass => {}
        }

        let effect_obj =
            match Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|obj| obj.unbind())) {
                Ok(obj) => obj,
                Err(err) => {
                    return StepEvent::Error(VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    )))
                }
            };

        for entry in &visible_chain {
            let handler = entry.handler.clone();
            let can_handle = match handler.can_handle(&effect) {
                Ok(value) => value,
                Err(err) => return StepEvent::Error(err),
            };
            if can_handle {
                let should_invoke = match self.should_invoke_handler(&entry, &effect_obj) {
                    Ok(value) => value,
                    Err(err) => {
                        return StepEvent::Error(VMError::python_error(format!(
                            "failed to evaluate WithHandler type filter: {err:?}"
                        )))
                    }
                };
                if !should_invoke {
                    continue;
                }
                let Some(idx) = handler_chain
                    .iter()
                    .position(|chain_entry| chain_entry.marker == entry.marker)
                else {
                    return StepEvent::Error(VMError::internal(format!(
                        "{}: target handler marker {} not found in original caller chain",
                        kind.missing_handler_context(),
                        entry.marker.raw()
                    )));
                };
                self.emit_forward_active_chain_event(kind, dispatch_id, from_idx, idx);
                if matches!(kind, ForwardKind::Pass) {
                    self.clear_forwarded_handler_segment(inner_seg_id);
                }

                let handler_seg = Segment::new(entry.marker, outer_caller);
                let handler_seg_id = self.alloc_segment(handler_seg);
                self.copy_interceptor_guard_state(outer_caller, handler_seg_id);
                self.set_scope_parent(handler_seg_id, Some(inner_seg_id));
                let handler_k = next_k.clone_handle();
                let observer_k = handler_k.clone_handle();
                self.register_continuation(next_k);
                self.dispatch_state.update_forwarded_dispatch(
                    dispatch_id,
                    self.continuation_pending_error_context(&handler_k).cloned(),
                    None,
                    crate::dispatch_state::ActiveHandlerContext {
                        segment_id: handler_seg_id,
                        continuation: observer_k,
                        marker: entry.marker,
                        prompt_seg_id: entry.prompt_seg_id,
                    },
                );
                self.current_segment = Some(handler_seg_id);
                let ir_node =
                    match Self::invoke_kleisli_handler_expr(handler, effect.clone(), handler_k) {
                        Ok(node) => node,
                        Err(err) => return StepEvent::Error(err),
                    };
                return self.evaluate(ir_node);
            }
        }

        if let Some(original_exception) = origin.original_exception {
            self.mode = Mode::Throw(original_exception);
            return StepEvent::Continue;
        }
        self.dispatch_fatal_error_event(VMError::delegate_no_outer_handler(effect))
    }

    pub(super) fn handle_delegate(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_forward(ForwardKind::Delegate, effect)
    }

    pub(super) fn handle_pass(&mut self, effect: DispatchEffect) -> StepEvent {
        self.handle_forward(ForwardKind::Pass, effect)
    }

    pub(super) fn handle_handler_return(&mut self, mut value: Value) -> StepEvent {
        let Some(dispatch_id) = self.current_dispatch_id() else {
            self.mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };
        let original_exception = self.original_exception_for_dispatch(dispatch_id);
        let handler_dispatch = self.handler_dispatch_for_any(dispatch_id);
        let continuation = handler_dispatch
            .as_ref()
            .map(|(_, continuation, _)| continuation.clone());
        let is_python_handler = handler_dispatch
            .as_ref()
            .and_then(|(_, _, marker)| self.marker_handler_trace_info(*marker))
            .is_some_and(|(_, kind, _, _)| kind == HandlerKind::Python);
        let continuation_is_live = continuation.as_ref().is_some_and(|continuation| {
            self.continuations
                .entries
                .contains_key(&continuation.cont_id)
                && !self.is_one_shot_consumed(continuation.cont_id)
        });
        let is_user_defined_python_handler = handler_dispatch
            .as_ref()
            .is_some_and(|(_, _, marker)| self.is_user_defined_python_handler_marker(*marker));
        if is_python_handler && continuation_is_live {
            let continuation = continuation.clone().expect("checked above");
            self.mark_one_shot_consumed(continuation.cont_id);
            return self.throw_handler_protocol_error(format!(
                "handler returned without consuming continuation {}; use Resume(k, v), Transfer(k, v), Discontinue(k, exn), or Pass()",
                continuation.cont_id.raw(),
            ));
        }
        if original_exception.is_none() && !is_python_handler && continuation_is_live {
            let continuation = continuation.clone().expect("checked above");
            let value_repr = Self::value_repr(&value);
            if let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(dispatch_id)
            {
                self.trace_state.record_handler_completed(
                    dispatch_id,
                    &handler_name,
                    handler_index,
                    &HandlerAction::Returned {
                        value_repr: value_repr.clone(),
                    },
                );
                self.emit_resume_event(dispatch_id, &continuation, false);
            }
            return self.handle_dispatch_resume(continuation, value);
        }
        if original_exception.is_none() && is_user_defined_python_handler && !continuation_is_live {
            // ResultSafe/Try can consume the handler continuation before the Python handler
            // returns here. After removing the old caller-mutation hack, the safe return path is
            // to transfer via the original dispatch topology rather than the exhausted handler k.
            let target = self
                .dispatch_origin_for_dispatch_id(dispatch_id)
                .and_then(|origin| self.delegate_return_continuation(&origin.k_origin))
                .or_else(|| {
                    continuation
                        .as_ref()
                        .filter(|continuation| continuation.tail_owned_fibers().is_none())
                        .and_then(|continuation| self.delegate_return_continuation(continuation))
                });
            if let Some(target) = target {
                let value_repr = Self::value_repr(&value);
                if let Some((handler_index, handler_name)) =
                    self.current_handler_identity_for_dispatch(dispatch_id)
                {
                    self.trace_state.record_handler_completed(
                        dispatch_id,
                        &handler_name,
                        handler_index,
                        &HandlerAction::Returned {
                            value_repr: value_repr.clone(),
                        },
                    );
                    self.emit_resume_event(dispatch_id, &target, true);
                }
                return self.handle_dispatch_transfer(target, value);
            }
        }
        if continuation_is_live {
            if let Err(err) =
                self.maybe_attach_active_chain_to_execution_context(Some(dispatch_id), &mut value)
            {
                return StepEvent::Error(err);
            }
        }
        if let (Some((handler_index, handler_name)), Some(continuation)) = (
            self.current_handler_identity_for_dispatch(dispatch_id),
            continuation.as_ref(),
        ) {
            let value_repr = Self::value_repr(&value);
            self.trace_state.record_handler_completed(
                dispatch_id,
                &handler_name,
                handler_index,
                &HandlerAction::Returned {
                    value_repr: value_repr.clone(),
                },
            );
            self.emit_resume_event(dispatch_id, continuation, false);
        }
        if let Some(original) = original_exception {
            let active_chain = self
                .assemble_active_chain(Some(&original))
                .into_iter()
                .filter(|entry| !matches!(entry, ActiveChainEntry::ContextEntry { .. }))
                .collect();
            self.finish_dispatch_tracking(dispatch_id);
            self.mode = match TraceState::enrich_original_exception_with_context(
                original,
                value,
                active_chain,
            ) {
                Ok(exception) => Mode::Throw(exception),
                Err(effect_err) => Mode::Throw(effect_err),
            };
            return StepEvent::Continue;
        }
        self.finish_dispatch_tracking(dispatch_id);
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    pub(super) fn handle_tail_resume_return(&mut self, value: Value) -> StepEvent {
        let Some(dispatch_id) = self.current_dispatch_id() else {
            self.mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };

        if let Some((handler_index, handler_name)) =
            self.current_handler_identity_for_dispatch(dispatch_id)
        {
            let value_repr = Self::value_repr(&value);
            self.trace_state.record_handler_completed(
                dispatch_id,
                &handler_name,
                handler_index,
                &HandlerAction::Returned { value_repr },
            );
        }

        if let Some(original) = self.original_exception_for_dispatch(dispatch_id) {
            let active_chain = self
                .assemble_active_chain(Some(&original))
                .into_iter()
                .filter(|entry| !matches!(entry, ActiveChainEntry::ContextEntry { .. }))
                .collect();
            self.finish_dispatch_tracking(dispatch_id);
            self.mode = match TraceState::enrich_original_exception_with_context(
                original,
                value,
                active_chain,
            ) {
                Ok(exception) => Mode::Throw(exception),
                Err(effect_err) => Mode::Throw(effect_err),
            };
            return StepEvent::Continue;
        }

        self.finish_dispatch_tracking(dispatch_id);
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    pub(super) fn current_visible_handlers(&self) -> Vec<KleisliRef> {
        self.current_handler_chain()
            .into_iter()
            .map(|entry| entry.handler)
            .collect()
    }

    fn same_handler_entry(a: &HandlerChainEntry, b: &HandlerChainEntry) -> bool {
        Arc::ptr_eq(&a.handler, &b.handler)
    }

    fn outer_handler_prefix_len(
        base_entries: &[HandlerChainEntry],
        outer_entries: &[HandlerChainEntry],
    ) -> usize {
        if base_entries.is_empty() {
            return outer_entries.len();
        }

        for prefix_len in 0..outer_entries.len() {
            let overlap = &outer_entries[prefix_len..];
            if overlap.is_empty() || overlap.len() > base_entries.len() {
                continue;
            }

            let base_suffix = &base_entries[base_entries.len() - overlap.len()..];
            if overlap
                .iter()
                .zip(base_suffix.iter())
                .all(|(outer, base)| Self::same_handler_entry(outer, base))
            {
                return prefix_len;
            }
        }

        0
    }

    fn current_handler_chain_with_live_prefix(&self) -> Vec<HandlerChainEntry> {
        let base_entries = self.current_handler_chain();
        let Some(return_to) = self.return_to_continuation() else {
            return base_entries;
        };
        let Some(outer_start) = self
            .live_handler_chain_start_for_return_to(&return_to)
            .or_else(|| self.continuation_handler_chain_start(&return_to))
        else {
            return base_entries;
        };
        let outer_entries = self.handlers_in_caller_chain(outer_start);
        let prefix_len = Self::outer_handler_prefix_len(&base_entries, &outer_entries);
        if prefix_len == 0 {
            return base_entries;
        }

        let mut merged = outer_entries[..prefix_len].to_vec();
        merged.extend(base_entries);
        merged
    }

    fn live_handler_chain_start_for_return_to(
        &self,
        continuation: &Continuation,
    ) -> Option<SegmentId> {
        continuation
            .resume_dispatch_id()
            .and_then(|dispatch_id| self.dispatch_origin_for_dispatch_id_anywhere(dispatch_id))
            .and_then(|origin| self.continuation_handler_chain_start(&origin.k_origin))
    }

    fn caller_visible_handler_chain_start(&self) -> Result<SegmentId, VMError> {
        if let Some((_, _, continuation, _, _)) = self.current_handler_dispatch() {
            return self
                .continuation_handler_chain_start(&continuation)
                .or_else(|| {
                    self.current_dispatch_origin()
                        .and_then(|origin| self.continuation_handler_chain_start(&origin.k_origin))
                })
                .ok_or_else(|| {
                    VMError::internal("dispatch origin continuations must be captured")
                });
        }

        self.current_segment
            .ok_or_else(|| VMError::internal("handler chain requested without current segment"))
    }

    pub(super) fn handle_map(
        &mut self,
        source: PyShared,
        mapper: PyShared,
        mapper_meta: CallMetadata,
    ) -> StepEvent {
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("Map outside current segment"));
        };
        seg.push_frame(Frame::MapReturn {
            mapper,
            mapper_meta,
        });
        self.mode = Mode::HandleYield(DoCtrl::Eval {
            expr: source,
            metadata: None,
        });
        StepEvent::Continue
    }

    pub(super) fn handle_flat_map(
        &mut self,
        source: PyShared,
        binder: PyShared,
        binder_meta: CallMetadata,
    ) -> StepEvent {
        let Some(seg) = self.current_segment_mut() else {
            return StepEvent::Error(VMError::internal("FlatMap outside current segment"));
        };
        seg.push_frame(Frame::FlatMapBindSource {
            binder,
            binder_meta,
        });
        self.mode = Mode::HandleYield(DoCtrl::Eval {
            expr: source,
            metadata: None,
        });
        StepEvent::Continue
    }

    pub(super) fn handle_get_continuation(&mut self) -> StepEvent {
        let Some(dispatch_id) = self.current_active_handler_dispatch_id() else {
            return StepEvent::Error(VMError::internal("GetContinuation outside dispatch"));
        };
        let Some((_, k, _)) = self.active_handler_dispatch_for(dispatch_id) else {
            return StepEvent::Error(VMError::internal(
                "GetContinuation: active handler continuation not found",
            ));
        };
        if k.owns_fibers() {
            self.register_continuation(k.clone());
        }
        self.mode = Mode::Deliver(Value::Continuation(k));
        StepEvent::Continue
    }

    pub(super) fn handle_get_handlers(&mut self) -> StepEvent {
        // Preserve full caller-visible handler stack (top-most first).
        //
        // Outside dispatch, GetHandlers should still report the currently
        // visible chain from the running segment. During dispatch we keep the
        // existing Delegate-aware behavior so handler code sees the same
        // caller-visible stack as the effect site.
        let entries = if self.current_handler_dispatch().is_some() {
            let chain_start = match self.caller_visible_handler_chain_start() {
                Ok(seg_id) => seg_id,
                Err(err) => return StepEvent::Error(err),
            };
            self.handlers_in_caller_chain(chain_start)
        } else {
            self.current_handler_chain_with_live_prefix()
        };
        let handlers = entries
            .into_iter()
            .map(|entry| entry.handler)
            .collect::<Vec<_>>();
        self.mode = Mode::Deliver(Value::Handlers(handlers));
        StepEvent::Continue
    }

    pub(super) fn handle_get_traceback(&mut self, continuation: Continuation) -> StepEvent {
        if self.current_dispatch_id().is_none() {
            return StepEvent::Error(VMError::internal(
                "GetTraceback called outside of dispatch context",
            ));
        }
        let hops = self.collect_traceback(&continuation);
        self.mode = Mode::Deliver(Value::Traceback(hops));
        StepEvent::Continue
    }

    pub(super) fn handle_create_continuation(
        &mut self,
        program: PyShared,
        handlers: Vec<KleisliRef>,
        handler_identities: Vec<Option<PyShared>>,
        metadata: Option<CallMetadata>,
        outside_scope: Option<SegmentId>,
    ) -> StepEvent {
        let k = Continuation::create_unstarted_with_identities_and_metadata(
            program,
            handlers,
            handler_identities,
            metadata,
            outside_scope.or(self.current_segment),
        );
        self.register_continuation(k.clone());
        self.mode = Mode::Deliver(Value::Continuation(k));
        StepEvent::Continue
    }

    pub(super) fn handle_resume_continuation(
        &mut self,
        mut k: Continuation,
        value: Value,
    ) -> StepEvent {
        if k.is_started() {
            k = match self.materialize_owned_continuation(k, "ResumeContinuation") {
                Ok(continuation) => continuation,
                Err(err) => return StepEvent::Error(err),
            };
            let caller = k.captured_caller();
            return self.activate_continuation(
                ContinuationActivationKind::Resume,
                k,
                value,
                caller,
            );
        }

        if self.is_one_shot_consumed(k.cont_id) {
            return StepEvent::Error(VMError::one_shot_violation(k.cont_id));
        }
        k.mark_consumed();
        self.mark_one_shot_consumed(k.cont_id);

        let Some((program, handlers, handler_identities, start_metadata, outside_scope)) =
            k.into_unstarted_parts()
        else {
            return StepEvent::Error(VMError::internal(
                "unstarted continuation has no program payload",
            ));
        };

        let Some(current_seg_id) = self.current_segment else {
            return StepEvent::Error(VMError::internal(
                "unstarted continuation resumed without current segment",
            ));
        };
        let current_dispatch_id = self.current_segment_dispatch_id();

        let mut caller_outside = Some(current_seg_id);
        let scope_outside = outside_scope.or(Some(current_seg_id));
        if outside_scope.is_some() {
            let Some(_current_seg) = self.segments.get(current_seg_id) else {
                return StepEvent::Error(VMError::internal(
                    "unstarted continuation current segment not found",
                ));
            };
            let mut return_anchor = Segment::new(Marker::fresh(), Some(current_seg_id));
            return_anchor.push_frame(Frame::EvalReturn(Box::new(
                EvalReturnContinuation::ReturnToContinuation {
                    continuation: self
                        .capture_live_continuation(current_seg_id, current_dispatch_id),
                },
            )));
            let anchor_seg_id = self.alloc_segment(return_anchor);
            self.copy_interceptor_guard_state(Some(current_seg_id), anchor_seg_id);
            self.set_scope_parent(anchor_seg_id, scope_outside);
            caller_outside = Some(anchor_seg_id);
        }

        let k_handler_count = handlers.len();
        for idx in (0..k_handler_count).rev() {
            let base_handler = handlers[idx].clone();
            let handler = if let Some(Some(identity)) = handler_identities.get(idx) {
                Arc::new(IdentityKleisli::new(base_handler, identity.clone())) as KleisliRef
            } else {
                base_handler
            };
            let handler_marker = Marker::fresh();
            let prompt_seg = Segment::new_prompt(
                handler_marker,
                caller_outside,
                handler_marker,
                handler.clone(),
            );
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            self.copy_interceptor_guard_state(caller_outside, prompt_seg_id);
            self.set_scope_parent(prompt_seg_id, scope_outside);
            self.track_run_handler(&handler);
            let body_seg = Segment::new(handler_marker, Some(prompt_seg_id));
            let body_seg_id = self.alloc_segment(body_seg);
            self.copy_interceptor_guard_state(caller_outside, body_seg_id);
            self.set_scope_parent(body_seg_id, scope_outside);

            caller_outside = Some(body_seg_id);
        }

        let body_seg = Segment::new(Marker::fresh(), caller_outside);
        let body_seg_id = self.alloc_segment(body_seg);
        self.copy_interceptor_guard_state(caller_outside, body_seg_id);
        self.set_scope_parent(body_seg_id, scope_outside);
        self.current_segment = Some(body_seg_id);
        self.pending_python = Some(PendingPython::EvalExpr {
            metadata: start_metadata,
        });
        StepEvent::NeedsPython(PythonCall::EvalExpr { expr: program })
    }
}
