use super::*;

impl VM {
    fn handler_dispatch_is_live(&self, continuation: &Continuation) -> bool {
        !self.is_one_shot_consumed(continuation.cont_id)
    }

    fn dispatch_origin_view_from_context(
        dispatch_id: DispatchId,
        dispatch: &crate::dispatch_observer::DispatchContext,
    ) -> DispatchOriginView {
        DispatchOriginView {
            dispatch_id,
            effect: dispatch.effect.clone(),
            k_origin: dispatch.k_origin.clone(),
            original_exception: dispatch.original_exception.clone(),
        }
    }

    fn dispatch_context_for_segment(
        &self,
        seg_id: SegmentId,
    ) -> Option<(DispatchId, &crate::dispatch_observer::DispatchContext)> {
        let dispatch_id = self.dispatch_origin_id_in_segment(seg_id)?;
        let dispatch = self.dispatch_observer.dispatch(dispatch_id).unwrap_or_else(|| {
            panic!(
                "dispatch observer invariant violated: segment {} references dispatch {} but \
                 observer has no context",
                seg_id.index(),
                dispatch_id.raw()
            )
        });
        Some((dispatch_id, dispatch))
    }

    pub(super) fn finish_dispatch_tracking(&mut self, dispatch_id: DispatchId) {
        self.dispatch_observer.finish_dispatch(dispatch_id);
        self.trace_state.finish_dispatch(dispatch_id);
    }

    fn dispatch_origin_view(&self, dispatch_id: DispatchId) -> Option<DispatchOriginView> {
        let dispatch = self.dispatch_observer.dispatch(dispatch_id)?;
        Some(Self::dispatch_origin_view_from_context(dispatch_id, dispatch))
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
                    origins.push(Self::dispatch_origin_view_from_context(dispatch_id, dispatch));
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
        self.dispatch_observer.segment_dispatch_id(seg_id)
    }

    fn dispatch_origin_caller_in_segment(
        &self,
        seg_id: SegmentId,
    ) -> Option<(DispatchId, SegmentId)> {
        self.dispatch_origin_in_segment_by(seg_id, |dispatch_id, _, k_origin, _| {
            k_origin
                .segment_id()
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
        self.dispatch_observer
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
                .dispatch_observer
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
            .map(|origin| LiveDispatchSnapshot {
                dispatch_id: origin.dispatch_id,
                continuation: self
                    .active_handler_dispatch_for(origin.dispatch_id)
                    .map(|(_, continuation, _)| continuation)
                    .unwrap_or(origin.k_origin),
            })
            .collect()
    }

    pub(super) fn current_dispatch_origin(&self) -> Option<DispatchOriginView> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            if let Some((dispatch_id, dispatch)) = self.dispatch_context_for_segment(seg_id) {
                return Some(Self::dispatch_origin_view_from_context(dispatch_id, dispatch));
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
        continuation: &Continuation,
        target_cont_id: ContId,
        visited: &mut HashSet<ContId>,
    ) -> bool {
        if !visited.insert(continuation.cont_id) {
            return false;
        }
        if continuation.cont_id == target_cont_id {
            return true;
        }
        if continuation.parent().is_some_and(|parent| {
            Self::continuation_is_in_origin_chain(parent, target_cont_id, visited)
        }) {
            return true;
        }
        continuation.frames().is_some_and(|frames| {
            frames.iter().any(|frame| match frame {
                Frame::EvalReturn(eval_return) => match eval_return.as_ref() {
                    EvalReturnContinuation::ResumeToContinuation { continuation }
                    | EvalReturnContinuation::ReturnToContinuation { continuation }
                    | EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                        Self::continuation_is_in_origin_chain(
                            continuation,
                            target_cont_id,
                            visited,
                        )
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
    }

    pub(super) fn exact_dispatch_origin_for_continuation(
        &self,
        continuation: &Continuation,
    ) -> Option<DispatchOriginView> {
        let origin = self.dispatch_origin_for_continuation(continuation)?;
        let mut visited = HashSet::new();
        Self::continuation_is_in_origin_chain(&origin.k_origin, continuation.cont_id, &mut visited)
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
                    && dispatch.active_handler.segment_id == seg_id
                    && self.handler_dispatch_is_live(&dispatch.active_handler.continuation)
                {
                    return Some(dispatch.active_handler.marker);
                }
            }
            cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
        }
        None
    }

    pub(super) fn current_handler_dispatch(
        &self,
    ) -> Option<(SegmentId, DispatchId, Continuation, Marker, SegmentId)> {
        let seg_id = self.current_segment?;
        let (dispatch_id, dispatch) = self.dispatch_context_for_segment(seg_id)?;
        (dispatch.active_handler.segment_id == seg_id
            && self.handler_dispatch_is_live(&dispatch.active_handler.continuation))
        .then(|| {
            (
                seg_id,
                dispatch_id,
                dispatch.active_handler.continuation.clone(),
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
            if dispatch.active_handler.segment_id == seg_id
                && self.handler_dispatch_is_live(&dispatch.active_handler.continuation)
            {
                let found = (
                    seg_id,
                    dispatch_id,
                    dispatch.active_handler.continuation.clone(),
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
        let dispatch = self.dispatch_observer.dispatch(dispatch_id)?;
        self.handler_dispatch_is_live(&dispatch.active_handler.continuation).then(|| {
            (
                dispatch.active_handler.segment_id,
                dispatch.active_handler.continuation.clone(),
                dispatch.active_handler.marker,
            )
        })
    }

    pub(super) fn handler_dispatch_for_any(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<(SegmentId, Continuation, Marker)> {
        let dispatch = self.dispatch_observer.dispatch(dispatch_id)?;
        Some((
            dispatch.active_handler.segment_id,
            dispatch.active_handler.continuation.clone(),
            dispatch.active_handler.marker,
        ))
    }

    fn clear_forwarded_handler_segment(&mut self, seg_id: SegmentId) {
        self.dispatch_observer.unbind_segment(seg_id);
        let Some(seg) = self.segments.get_mut(seg_id) else {
            return;
        };
        seg.frames.clear();
        seg.pending_error_context = None;
        seg.throw_parent = None;
    }

    fn continuation_chain_contains_eval_in_scope_return(continuation: &Continuation) -> bool {
        let mut cursor = Some(continuation);
        while let Some(current) = cursor {
            if current.frames().is_some_and(|frames| {
                frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::EvalReturn(eval_return)
                            if matches!(
                                eval_return.as_ref(),
                                EvalReturnContinuation::EvalInScopeReturn { .. }
                            )
                    )
                })
            }) {
                return true;
            }
            cursor = current.parent();
        }
        false
    }

    fn continuation_chain_contains_return_to_continuation(continuation: &Continuation) -> bool {
        let mut cursor = Some(continuation);
        while let Some(current) = cursor {
            if current.frames().is_some_and(|frames| {
                frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::EvalReturn(eval_return)
                            if matches!(
                                eval_return.as_ref(),
                                EvalReturnContinuation::ReturnToContinuation { .. }
                            )
                    )
                })
            }) {
                return true;
            }
            cursor = current.parent();
        }
        false
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
                Self::continuation_chain_contains_eval_in_scope_return(&continuation)
            })
            || Self::continuation_chain_contains_eval_in_scope_return(&origin.k_origin)
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
        let mut start_seg_id = self.continuation_chain_segment_id(scope)?;

        // When EvalInScope is reached through Delegate chains, the continuation
        // passed to handlers may wrap the original effect-site continuation in
        // `parent`. Replay should use the origin scope so wrapper interceptors
        // around the effect site remain visible.
        let mut cursor = scope.parent();
        while let Some(parent) = cursor {
            assert!(
                parent.dispatch_id().is_some(),
                "EvalInScope parent chain must be Delegate-created dispatch continuations"
            );
            let Some(parent_seg_id) = self.continuation_chain_segment_id(parent) else {
                break;
            };
            start_seg_id = parent_seg_id;
            cursor = parent.parent();
        }
        Some(start_seg_id)
    }

    fn root_delegate_parent_segment_id(
        &self,
        continuation: &Continuation,
        assert_message: &str,
    ) -> Option<SegmentId> {
        let mut start_seg_id = self
            .continuation_chain_segment_id(continuation)
            .or_else(|| continuation.captured_caller())?;

        let mut cursor = continuation.parent();
        while let Some(parent) = cursor {
            debug_assert!(parent.dispatch_id().is_some(), "{}", assert_message);
            if let Some(parent_seg_id) = self
                .continuation_chain_segment_id(parent)
                .or_else(|| parent.captured_caller())
            {
                start_seg_id = parent_seg_id;
            }
            cursor = parent.parent();
        }
        Some(start_seg_id)
    }

    fn continuation_chain_segment_id(&self, continuation: &Continuation) -> Option<SegmentId> {
        continuation
            .segment_id()
            .filter(|seg_id| self.segments.get(*seg_id).is_some())
            .or_else(|| {
                continuation
                    .captured_caller()
                    .filter(|seg_id| self.segments.get(*seg_id).is_some())
            })
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
                    && file.as_deref().is_some_and(|path| {
                        !Self::is_internal_doeff_handler_source_file(path)
                    })
            })
    }

    fn delegate_return_continuation(&self, continuation: &Continuation) -> Option<Continuation> {
        let mut target_seg_id = continuation
            .captured_caller()
            .filter(|seg_id| self.segments.get(*seg_id).is_some());
        let mut cursor = continuation.parent();
        while let Some(parent) = cursor {
            target_seg_id = self
                .continuation_chain_segment_id(parent)
                .or_else(|| {
                    parent
                        .captured_caller()
                        .filter(|seg_id| self.segments.get(*seg_id).is_some())
                })
                .or(target_seg_id);
            cursor = parent.parent();
        }

        let seg_id = target_seg_id?;
        if let Some(dispatch_id) = self.dispatch_observer.segment_dispatch_id(seg_id) {
            if let Some((active_seg_id, continuation, _)) = self.handler_dispatch_for_any(dispatch_id)
            {
                if active_seg_id == seg_id && self.handler_dispatch_is_live(&continuation) {
                    return Some(continuation);
                }
            }
        }
        let segment = self.segments.get(seg_id)?;
        Some(self.capture_live_continuation(
            segment,
            seg_id,
            self.dispatch_observer.segment_dispatch_id(seg_id),
        ))
    }

    pub fn instantiate_installed_handlers(&mut self) -> Option<SegmentId> {
        let installed = self.installed_handlers.clone();
        let mut outside_seg_id: Option<SegmentId> = None;
        for entry in installed.into_iter().rev() {
            let mut prompt_seg = Segment::new_prompt(
                entry.marker,
                outside_seg_id,
                entry.marker,
                entry.handler.clone(),
            );
            self.initialize_builtin_prompt_segment(&entry.handler, &mut prompt_seg);
            self.copy_interceptor_guard_state(outside_seg_id, &mut prompt_seg);
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            self.track_run_handler(&entry.handler);

            let mut body_seg = Segment::new(entry.marker, Some(prompt_seg_id));
            self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
            let body_seg_id = self.alloc_segment(body_seg);
            outside_seg_id = Some(body_seg_id);
        }
        outside_seg_id
    }

    fn initialize_builtin_prompt_segment(&self, handler: &KleisliRef, prompt_seg: &mut Segment) {
        if handler.handler_name() == "StateHandler" {
            prompt_seg.state_store = self.rust_store.entries.clone();
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
        &self,
        source_seg_id: Option<SegmentId>,
        child_seg: &mut Segment,
    ) {
        let Some(source_seg_id) = source_seg_id else {
            return;
        };
        let Some(source_seg) = self.segments.get(source_seg_id) else {
            return;
        };
        child_seg.interceptor_eval_depth = source_seg.interceptor_eval_depth;
        child_seg.interceptor_skip_stack = source_seg.interceptor_skip_stack.clone();
    }

    fn remap_interceptor_skip_markers(seg: &mut Segment, marker_remap: &HashMap<Marker, Marker>) {
        if marker_remap.is_empty() {
            return;
        }
        for marker in &mut seg.interceptor_skip_stack {
            if let Some(remapped) = marker_remap.get(marker) {
                *marker = *remapped;
            }
        }
    }

    fn remap_marker(marker: &mut Marker, marker_remap: &HashMap<Marker, Marker>) {
        if let Some(remapped) = marker_remap.get(marker) {
            *marker = *remapped;
        }
    }

    fn remap_interceptor_markers_in_doctrl(
        ctrl: &mut DoCtrl,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        match ctrl {
            DoCtrl::Pure { .. } => {}
            DoCtrl::Map { .. } => {}
            DoCtrl::FlatMap { .. } => {}
            DoCtrl::Perform { .. } => {}
            DoCtrl::Resume { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::Transfer { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::TransferThrow { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::ResumeThrow { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::WithHandler { body, .. } => {
                Self::remap_interceptor_markers_in_doctrl(body, marker_remap);
            }
            DoCtrl::WithIntercept { body, .. } => {
                Self::remap_interceptor_markers_in_doctrl(body, marker_remap);
            }
            DoCtrl::Discontinue { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::Delegate { .. } => {}
            DoCtrl::Pass { .. } => {}
            DoCtrl::GetContinuation => {}
            DoCtrl::GetHandlers => {}
            DoCtrl::GetTraceback { continuation } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::CreateContinuation { .. } => {}
            DoCtrl::ResumeContinuation { continuation, .. } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            DoCtrl::PythonAsyncSyntaxEscape { .. } => {}
            DoCtrl::EvalInScope { scope, .. } => {
                Self::remap_interceptor_markers_in_continuation(scope, marker_remap);
            }
            DoCtrl::AllocVar { .. }
            | DoCtrl::ReadVar { .. }
            | DoCtrl::WriteVar { .. }
            | DoCtrl::WriteVarNonlocal { .. }
            | DoCtrl::ReadHandlerState { .. }
            | DoCtrl::WriteHandlerState { .. }
            | DoCtrl::AppendHandlerLog { .. } => {}
            DoCtrl::Apply {
                f, args, kwargs, ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(f, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(factory, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            DoCtrl::IRStream { .. } => {}
            DoCtrl::Eval { .. } => {}
            DoCtrl::GetCallStack => {}
        }
    }

    fn remap_interceptor_markers_in_interceptor_continuation(
        continuation: &mut InterceptorContinuation,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        Self::remap_marker(&mut continuation.marker, marker_remap);
        let remapped_chain: Vec<InterceptorChainLink> = continuation
            .chain
            .iter()
            .cloned()
            .map(|mut link| {
                Self::remap_marker(&mut link.marker, marker_remap);
                link
            })
            .collect();
        continuation.chain = Arc::new(remapped_chain);
        Self::remap_interceptor_markers_in_doctrl(&mut continuation.original_yielded, marker_remap);
    }

    fn remap_interceptor_markers_in_eval_return_continuation(
        continuation: &mut EvalReturnContinuation,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        match continuation {
            EvalReturnContinuation::ResumeToContinuation { continuation }
            | EvalReturnContinuation::ReturnToContinuation { continuation }
            | EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
            }
            EvalReturnContinuation::TailResumeReturn => {}
            EvalReturnContinuation::ApplyResolveFunction { args, kwargs, .. }
            | EvalReturnContinuation::ExpandResolveFactory { args, kwargs, .. } => {
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            EvalReturnContinuation::ApplyResolveArg {
                f, args, kwargs, ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(f, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            EvalReturnContinuation::ApplyResolveKwarg {
                f, args, kwargs, ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(f, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
            EvalReturnContinuation::ExpandResolveArg {
                factory,
                args,
                kwargs,
                ..
            }
            | EvalReturnContinuation::ExpandResolveKwarg {
                factory,
                args,
                kwargs,
                ..
            } => {
                Self::remap_interceptor_markers_in_doctrl(factory, marker_remap);
                for arg in args {
                    Self::remap_interceptor_markers_in_doctrl(arg, marker_remap);
                }
                for (_, kwarg) in kwargs {
                    Self::remap_interceptor_markers_in_doctrl(kwarg, marker_remap);
                }
            }
        }
    }

    fn remap_interceptor_markers_in_frame(
        frame: &mut Frame,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        match frame {
            Frame::Program { .. } => {}
            Frame::InterceptorApply(interceptor_continuation) => {
                Self::remap_interceptor_markers_in_interceptor_continuation(
                    interceptor_continuation.as_mut(),
                    marker_remap,
                );
            }
            Frame::InterceptorEval(interceptor_continuation) => {
                Self::remap_interceptor_markers_in_interceptor_continuation(
                    interceptor_continuation.as_mut(),
                    marker_remap,
                );
            }
            Frame::EvalReturn(eval_continuation) => {
                Self::remap_interceptor_markers_in_eval_return_continuation(
                    eval_continuation.as_mut(),
                    marker_remap,
                );
            }
            Frame::MapReturn { .. } => {}
            Frame::FlatMapBindResult => {}
            Frame::FlatMapBindSource { .. } => {}
            Frame::InterceptBodyReturn { marker } => {
                Self::remap_marker(marker, marker_remap);
            }
        }
    }

    pub(super) fn remap_interceptor_markers_in_continuation(
        continuation: &mut Continuation,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        if marker_remap.is_empty() {
            return;
        }
        if let Some(segment) = continuation.segment_mut() {
            Self::remap_interceptor_markers_in_segment(segment, marker_remap);
        }

        if let Some(parent) = continuation.parent() {
            let mut parent_remapped = parent.clone();
            Self::remap_interceptor_markers_in_continuation(&mut parent_remapped, marker_remap);
            continuation.set_parent(Some(Arc::new(parent_remapped)));
        }
    }

    fn remap_interceptor_markers_in_segment(
        seg: &mut Segment,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        if marker_remap.is_empty() {
            return;
        }
        Self::remap_marker(&mut seg.marker, marker_remap);
        Self::remap_interceptor_skip_markers(seg, marker_remap);
        Self::remap_interceptor_markers_in_segment_kind(&mut seg.kind, marker_remap);
        for frame in &mut seg.frames {
            Self::remap_interceptor_markers_in_frame(frame, marker_remap);
        }
    }

    fn remap_interceptor_markers_in_segment_kind(
        kind: &mut SegmentKind,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        if marker_remap.is_empty() {
            return;
        }
        if let SegmentKind::PromptBoundary { handled_marker, .. } = kind {
            Self::remap_marker(handled_marker, marker_remap);
        }
    }

    pub(super) fn remap_interceptor_markers_in_runtime_state(
        &mut self,
        marker_remap: &HashMap<Marker, Marker>,
    ) {
        if marker_remap.is_empty() {
            return;
        }

        let seg_ids: Vec<SegmentId> = self.segments.iter().map(|(seg_id, _)| seg_id).collect();
        for seg_id in seg_ids {
            let Some(seg) = self.segments.get_mut(seg_id) else {
                continue;
            };
            Self::remap_interceptor_markers_in_segment(seg, marker_remap);
        }

        for continuation in self.continuation_registry.values_mut() {
            Self::remap_interceptor_markers_in_continuation(continuation, marker_remap);
        }
        let dispatch_ids: Vec<DispatchId> = self
            .dispatch_observer
            .iter()
            .map(|(dispatch_id, _)| dispatch_id)
            .collect();
        for dispatch_id in dispatch_ids {
            let Some(dispatch) = self.dispatch_observer.dispatch_mut(dispatch_id) else {
                continue;
            };
            Self::remap_marker(&mut dispatch.active_handler.marker, marker_remap);
            Self::remap_interceptor_markers_in_continuation(&mut dispatch.k_origin, marker_remap);
            Self::remap_interceptor_markers_in_continuation(
                &mut dispatch.active_handler.continuation,
                marker_remap,
            );
        }
    }

    pub fn is_one_shot_consumed(&self, cont_id: ContId) -> bool {
        self.consumed_cont_ids.contains(&cont_id)
    }

    pub fn mark_one_shot_consumed(&mut self, cont_id: ContId) {
        self.consumed_cont_ids.insert(cont_id);
        self.continuation_registry.remove(&cont_id);
    }

    pub fn register_continuation(&mut self, k: Continuation) {
        self.continuation_registry.insert(k.cont_id, k);
    }

    pub fn lookup_continuation(&self, cont_id: ContId) -> Option<&Continuation> {
        self.continuation_registry.get(&cont_id)
    }

    fn annotate_live_continuation(&self, continuation: &mut Continuation, seg_id: SegmentId) {
        continuation.set_resume_dispatch_id(self.current_segment_dispatch_id_any());
        continuation.set_dispatch_handler_hint(
            self.handlers_in_caller_chain(seg_id)
                .first()
                .map(|entry| crate::continuation::DispatchHandlerHint {
                    marker: entry.marker,
                    prompt_seg_id: entry.prompt_seg_id,
                }),
        );
    }

    pub(crate) fn capture_live_continuation(
        &self,
        segment: &Segment,
        seg_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Continuation {
        let mut continuation = Continuation::capture(segment, seg_id, dispatch_id);
        self.annotate_live_continuation(&mut continuation, seg_id);
        continuation.set_scope_snapshot(
            self.scope_parent(seg_id),
            self.scope_bindings(seg_id).cloned().unwrap_or_default(),
            self.segment_var_overrides(seg_id)
                .cloned()
                .unwrap_or_default(),
        );
        continuation
    }

    pub fn capture_continuation(&self, dispatch_id: Option<DispatchId>) -> Option<Continuation> {
        let seg_id = self.current_segment?;
        let segment = self.segments.get(seg_id)?;
        Some(self.capture_live_continuation(segment, seg_id, dispatch_id))
    }

    pub(super) fn current_segment_dispatch_id(&self) -> Option<DispatchId> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let Some(dispatch_id) = self.dispatch_observer.segment_dispatch_id(seg_id) {
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
            .current_segment_ref()
            .and_then(|seg| seg.pending_error_context.clone());
        let restricted_error_context_dispatch = Self::is_execution_context_effect(&effect)
            && original_exception
                .as_ref()
                .is_some_and(PyException::requires_safe_error_context_dispatch);
        let restricted_excluded_prompts: HashSet<SegmentId> = if restricted_error_context_dispatch {
            self.dispatch_observer
                .segment_dispatch_id(seg_id)
                .and_then(|dispatch_id| {
                    self.dispatch_origin_for_dispatch_id(dispatch_id).map(|origin| {
                        self.handlers_in_caller_chain(
                            origin
                                .k_origin
                                .segment_id()
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
        let current_seg = self
            .segments
            .get(seg_id)
            .ok_or_else(|| VMError::invalid_segment("current segment not found"))?;
        let dispatch_id = DispatchId::fresh();
        let resume_dispatch_id = self
            .current_segment
            .and_then(|current_seg_id| self.dispatch_observer.segment_dispatch_id(current_seg_id));
        let mut k_user = if Self::is_execution_context_effect(&effect) && original_exception.is_some() {
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
                let Some(origin_seg_id) = origin.k_origin.segment_id() else {
                    return false;
                };
                let Some(current_hint) = self.first_handler_hint_in_caller_chain(seg_id) else {
                    return true;
                };
                self.find_prompt_boundary_in_caller_chain(origin_seg_id, current_hint.marker)
                    .is_some()
            });
            reusable_origin
                .map(|origin| origin.k_origin.clone_for_dispatch(Some(dispatch_id)))
                .unwrap_or_else(|| self.capture_live_continuation(current_seg, seg_id, Some(dispatch_id)))
        } else {
            self.capture_live_continuation(current_seg, seg_id, Some(dispatch_id))
        };
        k_user.set_resume_dispatch_id(resume_dispatch_id);
        k_user.set_dispatch_handler_hint(self.first_handler_hint_in_caller_chain(seg_id));
        if let Some(seg) = self.current_segment_mut() {
            seg.pending_error_context = None;
        }
        let effect_obj =
            Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|obj| obj.unbind()))
                .map_err(|err| {
                    VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    ))
                })?;

        let mut selected: Option<(usize, Marker, SegmentId, KleisliRef)> = None;
        let mut first_type_filtered_skip: Option<(usize, Marker, SegmentId, KleisliRef)> = None;
        let mut handler_chain_snapshot: Vec<HandlerSnapshotEntry> = Vec::new();
        let mut handler_count = 0usize;
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
                let restricted_excluded = restricted_excluded_prompts.contains(&cursor_id);
                let restricted_handler_blocked = restricted_error_context_dispatch
                    && !(handler.is_rust_builtin() || handler.supports_error_context_conversion());
                if Some(cursor_id) != exclude_prompt
                    && !restricted_excluded
                    && !restricted_handler_blocked
                {
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
                            first_type_filtered_skip =
                                Some((handler_count, *handled_marker, cursor_id, handler.clone()));
                        }
                    }

                    handler_count += 1;
                }
            }
            cursor = next;
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

        let mut handler_seg = Segment::new(handler_marker, Some(prompt_seg_id));
        self.copy_interceptor_guard_state(Some(seg_id), &mut handler_seg);
        let handler_seg_id = self.alloc_segment(handler_seg);
        self.set_scope_parent(handler_seg_id, Some(seg_id));
        self.dispatch_observer.start_dispatch(
            dispatch_id,
            effect.clone(),
            k_user.clone(),
            original_exception.clone(),
            crate::dispatch_observer::ActiveHandlerContext {
                segment_id: handler_seg_id,
                continuation: k_user.clone(),
                marker: handler_marker,
                prompt_seg_id,
            },
        );
        self.current_segment = Some(handler_seg_id);

        let effect_site = TraceState::effect_site_from_continuation(&k_user);
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

        if handler.py_identity().is_some() {
            self.register_continuation(k_user.clone());
        }
        let ir_node = Self::invoke_kleisli_handler_expr(handler, effect, k_user)?;
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
        self.installed_handlers
            .retain(|entry| entry.marker != marker);
        self.installed_handlers
            .push(InstalledHandler { marker, handler });
    }

    pub fn remove_handler(&mut self, marker: Marker) -> bool {
        let before = self.installed_handlers.len();
        self.installed_handlers
            .retain(|entry| entry.marker != marker);
        before != self.installed_handlers.len()
    }

    pub fn installed_handler_markers(&self) -> Vec<Marker> {
        self.installed_handlers
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
        seg.kind = SegmentKind::PromptBoundary {
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

    fn continuation_exec_segment(
        k: &Continuation,
        caller: Option<SegmentId>,
        _dispatch_id: Option<DispatchId>,
    ) -> Segment {
        let mut exec_seg = k
            .segment()
            .expect("captured continuation must have a segment snapshot")
            .clone();
        exec_seg.parent = caller;
        if let Some(parent) = k.parent().cloned() {
            exec_seg.throw_parent = Some(parent);
        }
        // The original exception lives on the active DispatchOrigin.k_origin.
        // Reinstalling it onto resumed continuation segments makes unrelated
        // nested Perform() calls look like fresh GetExecutionContext dispatches.
        exec_seg.pending_error_context = None;
        exec_seg
    }

    fn enter_continuation_segment_with_dispatch(
        &mut self,
        k: &Continuation,
        caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) {
        let exec_seg = Self::continuation_exec_segment(k, caller, dispatch_id);
        let exec_seg_id = self.alloc_segment(exec_seg);
        self.set_scope_parent(exec_seg_id, k.scope_parent_snapshot());
        self.replace_scope_bindings(exec_seg_id, k.scope_bindings_snapshot().clone());
        self.replace_segment_var_overrides(exec_seg_id, k.var_overrides_snapshot().clone());
        if let Some(dispatch_id) = dispatch_id {
            self.dispatch_observer.bind_segment(exec_seg_id, dispatch_id);
            if let Some(hint) = k.dispatch_handler_hint() {
                let restoring_outer_dispatch = k.dispatch_id() != Some(dispatch_id);
                let resuming_user_defined_python_handler =
                    self.is_user_defined_python_handler_marker(hint.marker);
                if restoring_outer_dispatch
                    || self.exact_dispatch_origin_for_continuation(k).is_none()
                    || resuming_user_defined_python_handler
                {
                    let (marker, prompt_seg_id) = if restoring_outer_dispatch {
                        self.dispatch_observer
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
                        self.dispatch_observer
                            .dispatch(dispatch_id)
                            .map(|dispatch| dispatch.active_handler.continuation.clone())
                            .unwrap_or_else(|| k.clone())
                    } else {
                        k.clone_for_dispatch(Some(dispatch_id))
                    };
                    self.dispatch_observer.update_forwarded_dispatch(
                        dispatch_id,
                        None,
                        None,
                        crate::dispatch_observer::ActiveHandlerContext {
                            segment_id: exec_seg_id,
                            continuation,
                            marker,
                            prompt_seg_id,
                        },
                    );
                }
            }
        }
        self.current_segment = Some(exec_seg_id);
    }

    fn enter_continuation_segment(&mut self, k: &Continuation, caller: Option<SegmentId>) {
        let dispatch_id = self.continuation_segment_dispatch_id(k);
        self.enter_continuation_segment_with_dispatch(k, caller, dispatch_id);
    }

    fn alloc_resume_return_anchor(
        &mut self,
        caller: Option<SegmentId>,
        continuation: Continuation,
        dispatch_id: Option<DispatchId>,
    ) -> SegmentId {
        let mut anchor = Segment::new(Marker::fresh(), caller);
        self.copy_interceptor_guard_state(self.current_segment, &mut anchor);
        anchor.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::ResumeToContinuation { continuation },
        )));
        let anchor_seg_id = self.alloc_segment(anchor);
        self.set_scope_parent(anchor_seg_id, caller);
        if let Some(dispatch_id) = dispatch_id {
            self.dispatch_observer.bind_segment(anchor_seg_id, dispatch_id);
        }
        anchor_seg_id
    }

    fn alloc_tail_resume_anchor(
        &mut self,
        caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) -> SegmentId {
        let mut anchor = Segment::new(Marker::fresh(), caller);
        self.copy_interceptor_guard_state(self.current_segment, &mut anchor);
        anchor.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::TailResumeReturn,
        )));
        let anchor_seg_id = self.alloc_segment(anchor);
        self.set_scope_parent(anchor_seg_id, caller);
        if let Some(dispatch_id) = dispatch_id {
            self.dispatch_observer.bind_segment(anchor_seg_id, dispatch_id);
        }
        anchor_seg_id
    }

    fn park_segment_after_capture(&mut self, seg_id: SegmentId) {
        let Some(seg) = self.segments.get_mut(seg_id) else {
            return;
        };
        seg.frames.clear();
        seg.pending_error_context = None;
        seg.throw_parent = None;
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

    fn live_segment_id_for_in_place_reentry(
        &self,
        k: &Continuation,
        caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) -> Option<SegmentId> {
        if caller != k.captured_caller() {
            return None;
        }

        let seg_id = k.segment_id()?;
        let snapshot = k.segment()?;
        if matches!(&snapshot.kind, SegmentKind::PromptBoundary { .. })
            && snapshot.frames.len() == 1
            && snapshot
                .frames
                .last()
                .is_some_and(|frame| matches!(frame, Frame::EvalReturn(_)))
        {
            return None;
        }
        let live = self.segments.get(seg_id)?;
        if !self.current_segment_is_transition_anchor_for(seg_id) {
            return None;
        }
        (live.marker == snapshot.marker
            && self.dispatch_observer.segment_dispatch_id(seg_id) == dispatch_id)
        .then_some(seg_id)
    }

    fn current_segment_is_transition_anchor_for(&self, target_seg_id: SegmentId) -> bool {
        let Some(current_seg_id) = self.current_segment else {
            return false;
        };
        if current_seg_id == target_seg_id {
            return false;
        }

        self.segments.get(current_seg_id).is_some_and(|seg| {
            seg.parent == Some(target_seg_id)
                && seg.frames.is_empty()
                && self.pending_python.is_none()
                && matches!(self.mode, Mode::Deliver(Value::Unit))
        })
    }

    fn free_current_transition_anchor_for(&mut self, target_seg_id: SegmentId) {
        let Some(current_seg_id) = self.current_segment else {
            return;
        };
        let Some(caller) = self.segments.get(current_seg_id).map(|seg| seg.parent) else {
            return;
        };
        let scope_parent = self.scope_parent(current_seg_id);

        if !self.current_segment_is_transition_anchor_for(target_seg_id) {
            return;
        }

        self.reparent_children(current_seg_id, caller, scope_parent);
        self.free_segment(current_seg_id);
    }

    fn enter_or_reenter_continuation_segment_with_dispatch(
        &mut self,
        k: &Continuation,
        caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) {
        self.enter_continuation_segment_with_dispatch(k, caller, dispatch_id);
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
        self.mark_one_shot_consumed(k.cont_id);
        k.refresh_persistent_segment_state(
            &self.scope_state_store,
            &self.scope_writer_logs,
            &self.scope_persistent_epochs,
        );
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
                let caller = k
                    .captured_caller()
                    .or_else(|| k.segment().and_then(|segment| segment.parent));
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
        self.enter_or_reenter_continuation_segment_with_dispatch(&k, caller, dispatch_id);
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    pub(super) fn handle_dispatch_resume(&mut self, k: Continuation, value: Value) -> StepEvent {
        let current_dispatch_id = self.current_dispatch_id();
        let exact_origin_target = self.exact_dispatch_origin_for_continuation(&k).is_some();
        let caller = k
            .dispatch_id()
            .filter(|dispatch_id| current_dispatch_id == Some(*dispatch_id))
            .and_then(|dispatch_id| {
                self.current_handler_dispatch()
                    .filter(|(_, current_dispatch_id, ..)| *current_dispatch_id == dispatch_id)
                    .and_then(|(handler_seg_id, _, _continuation, marker, _prompt_seg_id)| {
                        if exact_origin_target && self.is_user_defined_python_handler_marker(marker)
                        {
                            if self.segment_is_tail_resume_return(handler_seg_id) {
                                let anchor_seg_id =
                                    self.alloc_tail_resume_anchor(k.captured_caller(), Some(dispatch_id));
                                self.park_segment_after_capture(handler_seg_id);
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
                            self.park_segment_after_capture(handler_seg_id);
                            return Some(anchor_seg_id);
                        }
                        self.is_user_defined_python_handler_marker(marker)
                            .then_some(handler_seg_id)
                    })
            })
            .or_else(|| k.captured_caller());
        self.activate_continuation(ContinuationActivationKind::Resume, k, value, caller)
    }

    pub(super) fn handle_dispatch_transfer(&mut self, k: Continuation, value: Value) -> StepEvent {
        let caller = k.captured_caller();
        self.activate_continuation(ContinuationActivationKind::Transfer, k, value, caller)
    }

    fn activate_throw_continuation(
        &mut self,
        k: Continuation,
        exception: PyException,
        terminal_dispatch_completion: bool,
    ) -> StepEvent {
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

        let mut prompt_seg = Segment::new_prompt_with_types(
            plan.handler_marker,
            Some(plan.outside_seg_id),
            plan.handler_marker,
            prompt_handler.clone(),
            types,
        );
        self.initialize_builtin_prompt_segment(&prompt_handler, &mut prompt_seg);
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), &mut prompt_seg);
        let prompt_seg_id = self.alloc_segment(prompt_seg);
        self.track_run_handler(&prompt_handler);

        let mut body_seg = Segment::new(plan.handler_marker, Some(prompt_seg_id));
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), &mut body_seg);
        let body_seg_id = self.alloc_segment(body_seg);

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
        let body_seg = match Self::prepare_with_intercept(
            interceptor,
            types,
            mode,
            metadata,
            self.current_segment,
            &self.segments,
        ) {
            Ok(segment) => segment,
            Err(err) => return StepEvent::Error(err),
        };
        let body_seg_id = self.alloc_segment(body_seg);

        self.current_segment = Some(body_seg_id);
        self.evaluate(program)
    }

    fn prepare_with_intercept(
        interceptor: KleisliRef,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
        current_segment: Option<SegmentId>,
        segments: &FiberArena,
    ) -> Result<Segment, VMError> {
        let interceptor_marker = Marker::fresh();
        let Some(outside_seg_id) = current_segment else {
            return Err(VMError::internal("no current segment for WithIntercept"));
        };
        let outside_seg = segments.get(outside_seg_id).ok_or_else(|| {
            VMError::invalid_segment("current segment not found for WithIntercept")
        })?;

        let mut body_seg = Segment::new(interceptor_marker, Some(outside_seg_id));
        body_seg.kind = SegmentKind::InterceptorBoundary {
            interceptor,
            types,
            mode,
            metadata,
        };
        body_seg.interceptor_eval_depth = outside_seg.interceptor_eval_depth;
        body_seg.interceptor_skip_stack = outside_seg.interceptor_skip_stack.clone();
        Ok(body_seg)
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
            SegmentKind::Normal
            | SegmentKind::InterceptorBoundary { .. }
            | SegmentKind::MaskBoundary { .. } => {
                return Err(VMError::internal(
                    "Pass forwarding requires current prompt boundary segment",
                ))
            }
        };
        let mut wrapper_caller = prompt_caller;
        let mut cursor = Some(parent_k_user);
        while let Some(current) = cursor {
            wrapper_caller = current
                .captured_caller()
                .filter(|seg_id| self.segments.get(*seg_id).is_some())
                .or_else(|| {
                    current
                        .segment_id()
                        .filter(|seg_id| self.segments.get(*seg_id).is_some())
                })
                .or(wrapper_caller);
            cursor = current.parent();
        }

        let mut pass_seg = Segment::new_prompt_with_types(
            Marker::fresh(),
            wrapper_caller,
            handler_marker,
            handler,
            types,
        );
        self.copy_interceptor_guard_state(Some(prompt_seg_id), &mut pass_seg);
        let pass_segment_id = self
            .root_delegate_parent_segment_id(
                parent_k_user,
                "Pass parent chain must be Delegate-created dispatch continuations",
            )
            .or_else(|| self.continuation_chain_segment_id(parent_k_user))
            .unwrap_or(prompt_seg_id);
        let eval_return = if Self::continuation_chain_contains_return_to_continuation(parent_k_user) {
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
        let mut pass_cont = Continuation::with_id(
            pass_cont_id,
            &pass_seg,
            pass_segment_id,
            Some(dispatch_id),
        );
        pass_cont.set_resume_dispatch_id(parent_k_user.resume_dispatch_id());
        pass_cont.set_dispatch_handler_hint(Some(
            crate::continuation::DispatchHandlerHint {
                marker: handler_marker,
                prompt_seg_id,
            },
        ));
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
        let handler_chain = self.handlers_in_caller_chain(
            origin
                .k_origin
                .segment_id()
                .expect("dispatch origin continuations must be captured"),
        );
        let Some(from_idx) = handler_chain
            .iter()
            .position(|entry| entry.marker == current_marker)
        else {
            return StepEvent::Error(VMError::internal(format!(
                "{}: current handler marker {} not found in caller chain",
                kind.missing_handler_context(),
                current_marker.raw()
            )));
        };
        let search_start = self
            .segments
            .get(current_prompt_seg_id)
            .and_then(|seg| seg.parent);
        let visible_chain = search_start
            .map(|seg_id| self.handlers_in_caller_chain(seg_id))
            .unwrap_or_default();
        let next_k = match kind {
            ForwardKind::Delegate => {
                let Some(mut k_new) = self.capture_continuation(Some(dispatch_id)) else {
                    return StepEvent::Error(VMError::internal(
                        "Delegate called without current segment",
                    ));
                };
                k_new.set_parent(Some(Arc::new(parent_k_user)));
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
                self.clear_forwarded_handler_segment(inner_seg_id);

                let mut handler_seg = Segment::new(entry.marker, Some(inner_seg_id));
                self.copy_interceptor_guard_state(Some(inner_seg_id), &mut handler_seg);
                let handler_seg_id = self.alloc_segment(handler_seg);
                self.set_scope_parent(handler_seg_id, Some(inner_seg_id));
                self.dispatch_observer.update_forwarded_dispatch(
                    dispatch_id,
                    next_k.pending_error_context().cloned(),
                    None,
                    crate::dispatch_observer::ActiveHandlerContext {
                        segment_id: handler_seg_id,
                        continuation: next_k.clone(),
                        marker: entry.marker,
                        prompt_seg_id: entry.prompt_seg_id,
                    },
                );
                self.current_segment = Some(handler_seg_id);
                if handler.py_identity().is_some() {
                    self.register_continuation(next_k.clone());
                }
                let ir_node = match Self::invoke_kleisli_handler_expr(
                    handler,
                    effect.clone(),
                    next_k.clone(),
                ) {
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
            self.continuation_registry
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
                        .filter(|continuation| continuation.parent().is_none())
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
            self.mode =
                match TraceState::enrich_original_exception_with_context(
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
            self.mode =
                match TraceState::enrich_original_exception_with_context(original, value, active_chain)
                {
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
        self.register_continuation(k.clone());
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
        let chain_start = if let Some((_, _, continuation, _, _)) = self.current_handler_dispatch() {
            self.root_delegate_parent_segment_id(
                &continuation,
                "GetHandlers parent chain must be Delegate-created continuations",
            )
            .or_else(|| continuation.segment_id())
            .or_else(|| {
                self.current_dispatch_origin()
                    .and_then(|origin| {
                        self.root_delegate_parent_segment_id(
                            &origin.k_origin,
                            "GetHandlers parent chain must be Delegate-created continuations",
                        )
                        .or_else(|| origin.k_origin.segment_id())
                    })
            })
            .expect("dispatch origin continuations must be captured")
        } else {
            let Some(seg_id) = self.current_segment else {
                return StepEvent::Error(VMError::internal(
                    "GetHandlers called without current segment",
                ));
            };
            seg_id
        };
        let handlers = self
            .handlers_in_caller_chain(chain_start)
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
        let hops = TraceState::collect_traceback(&continuation);
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
        k: Continuation,
        value: Value,
    ) -> StepEvent {
        if k.is_started() {
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
            let Some(current_seg) = self.segments.get(current_seg_id) else {
                return StepEvent::Error(VMError::internal(
                    "unstarted continuation current segment not found",
                ));
            };
            let mut return_anchor = Segment::new(Marker::fresh(), Some(current_seg_id));
            self.copy_interceptor_guard_state(Some(current_seg_id), &mut return_anchor);
            return_anchor.push_frame(Frame::EvalReturn(Box::new(
                EvalReturnContinuation::ReturnToContinuation {
                    continuation: self.capture_live_continuation(
                        current_seg,
                        current_seg_id,
                        current_dispatch_id,
                    ),
                },
            )));
            let anchor_seg_id = self.alloc_segment(return_anchor);
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
            let mut prompt_seg = Segment::new_prompt(
                handler_marker,
                caller_outside,
                handler_marker,
                handler.clone(),
            );
            self.copy_interceptor_guard_state(caller_outside, &mut prompt_seg);
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            self.set_scope_parent(prompt_seg_id, scope_outside);
            self.track_run_handler(&handler);
            let mut body_seg = Segment::new(handler_marker, Some(prompt_seg_id));
            self.copy_interceptor_guard_state(caller_outside, &mut body_seg);
            let body_seg_id = self.alloc_segment(body_seg);
            self.set_scope_parent(body_seg_id, scope_outside);

            caller_outside = Some(body_seg_id);
        }

        let mut body_seg = Segment::new(Marker::fresh(), caller_outside);
        self.copy_interceptor_guard_state(caller_outside, &mut body_seg);
        let body_seg_id = self.alloc_segment(body_seg);
        self.set_scope_parent(body_seg_id, scope_outside);
        self.current_segment = Some(body_seg_id);
        self.pending_python = Some(PendingPython::EvalExpr {
            metadata: start_metadata,
        });
        StepEvent::NeedsPython(PythonCall::EvalExpr { expr: program })
    }
}
