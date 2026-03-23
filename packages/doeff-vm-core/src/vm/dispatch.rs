use super::*;

#[derive(Clone)]
struct DispatchFrameView {
    seg_id: SegmentId,
    dispatch: ProgramDispatch,
}

impl VM {
    fn full_handler_entries_for_segment(&self, seg_id: SegmentId) -> Vec<HandlerChainEntry> {
        self.handlers_in_caller_chain(seg_id)
    }

    fn handler_snapshot_from_entries(entries: &[HandlerChainEntry]) -> Vec<HandlerSnapshotEntry> {
        entries
            .iter()
            .map(|entry| {
                let (name, kind, file, line) = Self::handler_trace_info(&entry.handler);
                HandlerSnapshotEntry {
                    handler_name: name,
                    handler_kind: kind,
                    source_file: file,
                    source_line: line,
                }
            })
            .collect()
    }

    fn handler_trace_from_snapshot(entries: &[HandlerSnapshotEntry]) -> Vec<HandlerDispatchEntry> {
        entries
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

    fn effect_site_snapshot(
        effect_site: Option<(FrameId, String, String, u32)>,
    ) -> Option<DispatchEffectSite> {
        effect_site.map(
            |(frame_id, function_name, source_file, source_line)| DispatchEffectSite {
                frame_id,
                function_name,
                source_file,
                source_line,
            },
        )
    }

    fn dispatch_trace_from_snapshot(
        effect: &DispatchEffect,
        effect_site: Option<(FrameId, String, String, u32)>,
        handler_chain_snapshot: &[HandlerSnapshotEntry],
    ) -> DispatchDisplay {
        DispatchDisplay {
            effect_site: Self::effect_site_snapshot(effect_site),
            handler_stack: Self::handler_trace_from_snapshot(handler_chain_snapshot),
            transfer_target_repr: None,
            result: EffectResult::Active,
            resumed_once: false,
            is_execution_context_effect: Self::is_execution_context_effect(effect),
        }
    }

    fn with_dispatch_mut<R>(
        &mut self,
        origin_cont_id: ContId,
        update: impl FnOnce(&mut ProgramDispatch) -> R,
    ) -> Option<R> {
        for (_, segment) in self.segments.iter_mut() {
            if segment
                .pending_program_dispatch
                .as_ref()
                .is_some_and(|dispatch| dispatch.origin_cont_id == origin_cont_id)
            {
                return segment.pending_program_dispatch.as_mut().map(update);
            }
            for frame in segment.frames.iter_mut().rev() {
                let Frame::Program {
                    dispatch: Some(dispatch),
                    ..
                } = frame
                else {
                    continue;
                };
                if dispatch.origin_cont_id == origin_cont_id {
                    return Some(update(dispatch));
                }
            }
        }
        None
    }

    fn dispatch_trace(&self, origin_cont_id: ContId) -> Option<&DispatchDisplay> {
        for (_, segment) in self.segments.iter() {
            if let Some(dispatch) = segment
                .pending_program_dispatch
                .as_ref()
                .filter(|dispatch| dispatch.origin_cont_id == origin_cont_id)
            {
                return Some(&dispatch.trace);
            }
            for frame in segment.frames.iter().rev() {
                let Frame::Program {
                    dispatch: Some(dispatch),
                    ..
                } = frame
                else {
                    continue;
                };
                if dispatch.origin_cont_id == origin_cont_id {
                    return Some(&dispatch.trace);
                }
            }
        }
        None
    }

    fn record_dispatch_delegated(
        &mut self,
        origin_cont_id: ContId,
        from_handler_index: usize,
        to_handler_index: usize,
    ) {
        let _ = self.with_dispatch_mut(origin_cont_id, |dispatch| {
            if let Some(from_entry) = dispatch.trace.handler_stack.get_mut(from_handler_index) {
                if from_entry.status == HandlerStatus::Active {
                    from_entry.status = HandlerStatus::Delegated;
                }
            }
            if let Some(to_entry) = dispatch.trace.handler_stack.get_mut(to_handler_index) {
                to_entry.status = HandlerStatus::Active;
            }
        });
    }

    fn record_dispatch_passed(
        &mut self,
        origin_cont_id: ContId,
        from_handler_index: usize,
        to_handler_index: usize,
    ) {
        let _ = self.with_dispatch_mut(origin_cont_id, |dispatch| {
            if let Some(from_entry) = dispatch.trace.handler_stack.get_mut(from_handler_index) {
                if from_entry.status == HandlerStatus::Active {
                    from_entry.status = HandlerStatus::Passed;
                }
            }
            if let Some(to_entry) = dispatch.trace.handler_stack.get_mut(to_handler_index) {
                to_entry.status = HandlerStatus::Active;
            }
        });
    }

    pub(super) fn record_handler_completion(
        &mut self,
        origin_cont_id: ContId,
        handler_name: &str,
        handler_index: usize,
        action: &HandlerAction,
    ) {
        let _ = self.with_dispatch_mut(origin_cont_id, |dispatch| {
            let status = match action {
                HandlerAction::Resumed { .. } => HandlerStatus::Resumed,
                HandlerAction::Transferred { .. } => HandlerStatus::Transferred,
                HandlerAction::Returned { .. } => HandlerStatus::Returned,
                HandlerAction::Threw { .. } => HandlerStatus::Threw,
            };
            if let Some(target) = dispatch.trace.handler_stack.get_mut(handler_index) {
                target.status = status;
            }

            dispatch.trace.result = match action {
                HandlerAction::Resumed { value_repr } | HandlerAction::Returned { value_repr } => {
                    EffectResult::Resumed {
                        value_repr: value_repr
                            .clone()
                            .unwrap_or_else(|| "[MISSING] None".to_string()),
                    }
                }
                HandlerAction::Transferred { value_repr } => EffectResult::Transferred {
                    handler_name: handler_name.to_string(),
                    target_repr: dispatch
                        .trace
                        .transfer_target_repr
                        .clone()
                        .or_else(|| value_repr.clone())
                        .unwrap_or_else(|| "[MISSING] <target>".to_string()),
                },
                HandlerAction::Threw { exception_repr } => EffectResult::Threw {
                    handler_name: handler_name.to_string(),
                    exception_repr: exception_repr
                        .clone()
                        .unwrap_or_else(|| "[MISSING] <exception>".to_string()),
                },
            };
            if matches!(action, HandlerAction::Resumed { .. }) {
                dispatch.trace.resumed_once = true;
            }
        });
    }

    pub(super) fn record_dispatch_transfer_target(
        &mut self,
        origin_cont_id: ContId,
        resumed_function_name: &str,
        source_file: &str,
        source_line: u32,
    ) {
        let target_repr = format!("{resumed_function_name}() {source_file}:{source_line}");
        let _ = self.with_dispatch_mut(origin_cont_id, |dispatch| {
            dispatch.trace.transfer_target_repr = Some(target_repr.clone());
            if let EffectResult::Transferred {
                target_repr: current_target,
                ..
            } = &mut dispatch.trace.result
            {
                *current_target = target_repr.clone();
            }
        });
    }

    fn handler_dispatch_is_live(&self, continuation: &Continuation) -> bool {
        !self.continuation_is_consumed(continuation)
    }

    /// Mark the ProgramDispatch that owns this continuation's fibers as consumed.
    /// Called after mark_consumed() on owned continuations to keep dispatch state in sync.
    fn mark_dispatch_consumed_for_continuation(&mut self, k: &Continuation) {
        if let Some(origin_cont_id) = self.continuation_dispatch_id(k) {
            let _ = self.with_dispatch_mut(origin_cont_id, |dispatch| {
                dispatch.origin_consumed = true;
                dispatch.handler_consumed = true;
            });
        }
    }

    fn continuation_handle_matches(a: &Continuation, b: &Continuation) -> bool {
        a.cont_id == b.cont_id || a.same_owned_fibers(b)
    }

    fn prompt_forward_context(
        &self,
        prompt_seg_id: SegmentId,
    ) -> Option<(DispatchEffect, Continuation)> {
        let segment = self.segments.get(prompt_seg_id)?;
        let effect = segment.pending_effect.as_ref()?.clone();
        let continuation = segment.pending_continuation.as_ref()?.clone_handle();
        Some((effect, continuation))
    }

    fn cloned_continuation_without_error_context(
        &mut self,
        continuation: &Continuation,
    ) -> Continuation {
        let cloned = continuation.clone_handle();
        for fiber_id in cloned.fibers() {
            self.clear_pending_error_context(*fiber_id);
        }
        cloned
    }

    fn set_prompt_forward_context(
        &mut self,
        prompt_seg_id: SegmentId,
        effect: &DispatchEffect,
        continuation: &Continuation,
    ) {
        let segment = self.segments.get_mut(prompt_seg_id).unwrap_or_else(|| {
            panic!(
                "set_prompt_forward_context: prompt segment {} not found",
                prompt_seg_id.0
            )
        });
        segment.pending_effect = Some(effect.clone());
        segment.pending_continuation = Some(continuation.clone_handle());
    }

    fn clear_prompt_forward_context(&mut self, prompt_seg_id: SegmentId) {
        let Some(segment) = self.segments.get_mut(prompt_seg_id) else {
            return;
        };
        segment.pending_effect = None;
        segment.pending_continuation = None;
    }

    fn clear_prompt_forward_context_pair(&mut self, prompt_seg_id: SegmentId) {
        self.clear_prompt_forward_context(prompt_seg_id);
        let canonical_prompt_seg_id = self.canonical_output_segment_id(prompt_seg_id);
        if canonical_prompt_seg_id != prompt_seg_id {
            self.clear_prompt_forward_context(canonical_prompt_seg_id);
        }
    }

    fn clear_dispatch_forward_context(&mut self, dispatch: &ProgramDispatch) {
        self.clear_prompt_forward_context_pair(dispatch.prompt_segment_id);
        if let Some(prompt_seg_id) = self
            .segments
            .get(dispatch.handler_segment_id)
            .and_then(|segment| segment.parent)
        {
            self.clear_prompt_forward_context_pair(prompt_seg_id);
        }
    }

    fn continuation_is_suffix(container: &Continuation, candidate: &Continuation) -> bool {
        let container_fibers = container.fibers();
        let candidate_fibers = candidate.fibers();
        !candidate_fibers.is_empty()
            && candidate_fibers.len() <= container_fibers.len()
            && container_fibers[container_fibers.len() - candidate_fibers.len()..]
                == *candidate_fibers
    }

    fn dispatch_origin_view_from_program(dispatch: &ProgramDispatch) -> DispatchOriginView {
        DispatchOriginView {
            origin_cont_id: dispatch.origin_cont_id,
            parent_origin_cont_id: dispatch.parent_origin_cont_id,
            effect: dispatch.effect.clone(),
            k_origin: dispatch.origin_as_continuation(),
            original_exception: dispatch.original_exception.clone(),
        }
    }

    fn dispatch_lookup_candidates(&self) -> Vec<DispatchFrameView> {
        let mut views = self.dispatch_frames_from_segment(self.current_segment);
        let mut seen = views
            .iter()
            .map(|view| view.dispatch.origin_cont_id)
            .collect::<HashSet<_>>();
        for (seg_id, _) in self.segments.iter() {
            if let Some(view) = self
                .dispatch_view_in_segment(seg_id)
                .filter(|view| seen.insert(view.dispatch.origin_cont_id))
            {
                views.push(view);
            }
        }
        views
    }

    fn eval_return_continuation(eval_return: &EvalReturnContinuation) -> Option<&Continuation> {
        match eval_return {
            EvalReturnContinuation::ResumeToContinuation { continuation }
            | EvalReturnContinuation::ReturnToContinuation { continuation }
            | EvalReturnContinuation::EvalInScopeReturn { continuation } => Some(continuation),
            EvalReturnContinuation::ApplyResolveFunction { .. }
            | EvalReturnContinuation::ApplyResolveArg { .. }
            | EvalReturnContinuation::ApplyResolveKwarg { .. }
            | EvalReturnContinuation::ExpandResolveFactory { .. }
            | EvalReturnContinuation::ExpandResolveArg { .. }
            | EvalReturnContinuation::ExpandResolveKwarg { .. }
            | EvalReturnContinuation::InterceptApplyResult { .. }
            | EvalReturnContinuation::InterceptEvalResult { .. }
            | EvalReturnContinuation::TailResumeReturn => None,
        }
    }

    fn dispatch_view_in_segment(&self, seg_id: SegmentId) -> Option<DispatchFrameView> {
        self.segments
            .get(seg_id)
            .and_then(|segment| segment.pending_program_dispatch.clone())
            .or_else(|| self.segment_program_dispatch(seg_id).cloned())
            .map(|dispatch| DispatchFrameView { seg_id, dispatch })
    }

    fn dispatch_ref_in_segment(&self, seg_id: SegmentId) -> Option<&ProgramDispatch> {
        self.segments
            .get(seg_id)
            .and_then(|segment| segment.pending_program_dispatch.as_ref())
            .or_else(|| self.segment_program_dispatch(seg_id))
    }

    fn collect_dispatches_in_continuation(
        &self,
        continuation: &Continuation,
        views: &mut Vec<DispatchFrameView>,
        seen_dispatches: &mut HashSet<ContId>,
        seen_segments: &mut HashSet<SegmentId>,
        seen_continuations: &mut HashSet<ContId>,
    ) {
        if !seen_continuations.insert(continuation.cont_id) {
            return;
        }
        for fiber_id in continuation.fibers() {
            self.collect_dispatches_from_segment_inner(
                Some(*fiber_id),
                views,
                seen_dispatches,
                seen_segments,
                seen_continuations,
            );
        }
    }

    fn collect_dispatches_from_segment_inner(
        &self,
        start_segment: Option<SegmentId>,
        views: &mut Vec<DispatchFrameView>,
        seen_dispatches: &mut HashSet<ContId>,
        seen_segments: &mut HashSet<SegmentId>,
        seen_continuations: &mut HashSet<ContId>,
    ) {
        let mut cursor = start_segment;
        while let Some(seg_id) = cursor {
            let parent = self.segments.get(seg_id).and_then(|seg| seg.parent);
            if !seen_segments.insert(seg_id) {
                cursor = parent;
                continue;
            }
            if let Some(view) = self.dispatch_view_in_segment(seg_id) {
                if seen_dispatches.insert(view.dispatch.origin_cont_id) {
                    views.push(view);
                }
            }
            if let Some(segment) = self.segments.get(seg_id) {
                for frame in &segment.frames {
                    if let Frame::EvalReturn(eval_return) = frame {
                        if let Some(continuation) =
                            Self::eval_return_continuation(eval_return.as_ref())
                        {
                            self.collect_dispatches_in_continuation(
                                continuation,
                                views,
                                seen_dispatches,
                                seen_segments,
                                seen_continuations,
                            );
                        }
                    }
                }
            }
            cursor = parent;
        }
    }

    fn dispatch_frames_from_segment(
        &self,
        start_segment: Option<SegmentId>,
    ) -> Vec<DispatchFrameView> {
        let mut views = Vec::new();
        self.collect_dispatches_from_segment_inner(
            start_segment,
            &mut views,
            &mut HashSet::new(),
            &mut HashSet::new(),
            &mut HashSet::new(),
        );
        views
    }

    fn first_dispatch_in_continuation(
        &self,
        continuation: &Continuation,
        seen_segments: &mut HashSet<SegmentId>,
        seen_continuations: &mut HashSet<ContId>,
    ) -> Option<DispatchFrameView> {
        if !seen_continuations.insert(continuation.cont_id) {
            return None;
        }
        continuation.fibers().iter().find_map(|fiber_id| {
            self.first_dispatch_from_segment_inner(
                Some(*fiber_id),
                seen_segments,
                seen_continuations,
            )
        })
    }

    fn first_dispatch_from_segment_inner(
        &self,
        start_segment: Option<SegmentId>,
        seen_segments: &mut HashSet<SegmentId>,
        seen_continuations: &mut HashSet<ContId>,
    ) -> Option<DispatchFrameView> {
        let mut cursor = start_segment;
        while let Some(seg_id) = cursor {
            let parent = self.segments.get(seg_id).and_then(|seg| seg.parent);
            if !seen_segments.insert(seg_id) {
                cursor = parent;
                continue;
            }
            if let Some(view) = self.dispatch_view_in_segment(seg_id) {
                return Some(view);
            }
            if let Some(segment) = self.segments.get(seg_id) {
                for frame in &segment.frames {
                    if let Frame::EvalReturn(eval_return) = frame {
                        if let Some(continuation) =
                            Self::eval_return_continuation(eval_return.as_ref())
                        {
                            if let Some(view) = self.first_dispatch_in_continuation(
                                continuation,
                                seen_segments,
                                seen_continuations,
                            ) {
                                return Some(view);
                            }
                        }
                    }
                }
            }
            cursor = parent;
        }
        None
    }

    fn first_dispatch_from_segment(
        &self,
        start_segment: Option<SegmentId>,
    ) -> Option<DispatchFrameView> {
        self.first_dispatch_from_segment_inner(
            start_segment,
            &mut HashSet::new(),
            &mut HashSet::new(),
        )
    }

    fn dispatch_frame_in_topology_inner(
        &self,
        start_segment: Option<SegmentId>,
        origin_cont_id: ContId,
        seen_segments: &mut HashSet<SegmentId>,
        seen_continuations: &mut HashSet<ContId>,
    ) -> Option<DispatchFrameView> {
        let mut cursor = start_segment;
        while let Some(seg_id) = cursor {
            let parent = self.segments.get(seg_id).and_then(|seg| seg.parent);
            if !seen_segments.insert(seg_id) {
                cursor = parent;
                continue;
            }
            if let Some(view) = self
                .dispatch_view_in_segment(seg_id)
                .filter(|view| view.dispatch.origin_cont_id == origin_cont_id)
            {
                return Some(view);
            }
            if let Some(segment) = self.segments.get(seg_id) {
                for frame in &segment.frames {
                    if let Frame::EvalReturn(eval_return) = frame {
                        if let Some(continuation) =
                            Self::eval_return_continuation(eval_return.as_ref())
                        {
                            if !seen_continuations.insert(continuation.cont_id) {
                                continue;
                            }
                            for fiber_id in continuation.fibers() {
                                if let Some(view) = self.dispatch_frame_in_topology_inner(
                                    Some(*fiber_id),
                                    origin_cont_id,
                                    seen_segments,
                                    seen_continuations,
                                ) {
                                    return Some(view);
                                }
                            }
                        }
                    }
                }
            }
            cursor = parent;
        }
        None
    }

    fn dispatch_frame_in_topology(
        &self,
        start_segment: Option<SegmentId>,
        origin_cont_id: ContId,
    ) -> Option<DispatchFrameView> {
        self.dispatch_frame_in_topology_inner(
            start_segment,
            origin_cont_id,
            &mut HashSet::new(),
            &mut HashSet::new(),
        )
    }

    fn dispatch_frame_in_parent_chain(
        &self,
        start_segment: Option<SegmentId>,
        origin_cont_id: ContId,
    ) -> Option<DispatchFrameView> {
        let mut cursor = start_segment;
        while let Some(seg_id) = cursor {
            if let Some(view) = self
                .dispatch_view_in_segment(seg_id)
                .filter(|view| view.dispatch.origin_cont_id == origin_cont_id)
            {
                return Some(view);
            }
            cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
        }
        None
    }

    fn active_dispatch_frame(&self, origin_cont_id: ContId) -> Option<DispatchFrameView> {
        if let Some(view) = self.dispatch_frame_in_topology(self.current_segment, origin_cont_id) {
            if view.dispatch.handler_segment_id == view.seg_id
                && !view.dispatch.handler_consumed
            {
                return Some(view);
            }
        }

        self.segments.iter().find_map(|(seg_id, _)| {
            self.dispatch_view_in_segment(seg_id).filter(|view| {
                view.dispatch.origin_cont_id == origin_cont_id
                    && view.dispatch.handler_segment_id == view.seg_id
                    && !view.dispatch.handler_consumed
            })
        })
    }

    fn find_dispatch_frame(&self, origin_cont_id: ContId) -> Option<DispatchFrameView> {
        self.dispatch_frame_in_topology(self.current_segment, origin_cont_id)
            .or_else(|| {
                self.segments.iter().find_map(|(seg_id, _)| {
                    self.dispatch_view_in_segment(seg_id)
                        .filter(|view| view.dispatch.origin_cont_id == origin_cont_id)
                })
            })
    }

    fn handler_prompt_segment_id(&self, seg_id: SegmentId, marker: Marker) -> Option<SegmentId> {
        self.segments
            .get(seg_id)
            .and_then(|segment| segment.parent)
            .filter(|parent_id| {
                self.segments.get(*parent_id).is_some_and(|segment| {
                    segment
                        .kind
                        .prompt_boundary()
                        .is_some_and(|boundary| boundary.handled_marker == marker)
                })
            })
            .or_else(|| self.find_prompt_boundary_in_caller_chain(seg_id, marker))
    }

    pub(super) fn complete_dispatch_context(&mut self, origin_cont_id: ContId) {
        let dispatch = self
            .current_segment
            .and_then(|seg_id| self.dispatch_view_in_segment(seg_id))
            .filter(|view| view.dispatch.origin_cont_id == origin_cont_id)
            .map(|view| view.dispatch)
            .or_else(|| {
                self.find_dispatch_frame(origin_cont_id)
                    .map(|view| view.dispatch)
            });

        let preserved = dispatch.as_ref().and_then(|dispatch| {
            matches!(dispatch.trace.result, EffectResult::Threw { .. }).then(|| {
                LiveDispatchSnapshot {
                    origin_cont_id: dispatch.origin_cont_id,
                    effect_repr: Self::effect_repr(&dispatch.effect),
                    dispatch_display: dispatch.trace.clone(),
                    frames: self.continuation_frame_stack(&dispatch.origin_as_continuation()),
                }
            })
        });

        if let Some(dispatch) = dispatch {
            self.clear_dispatch_forward_context(&dispatch);
            let seg_id = dispatch.handler_segment_id;
            if let Some(segment) = self.segments.get_mut(seg_id) {
                for frame in &mut segment.frames {
                    if let Frame::Program { dispatch, .. } = frame {
                        if dispatch.as_ref().is_some_and(|program_dispatch| {
                            program_dispatch.origin_cont_id == origin_cont_id
                        }) {
                            *dispatch = None;
                        }
                    }
                }
            }
            if let Some(segment) = self.segments.get_mut(seg_id) {
                if segment
                    .pending_program_dispatch
                    .as_ref()
                    .is_some_and(|program_dispatch| {
                        program_dispatch.origin_cont_id == origin_cont_id
                    })
                {
                    segment.pending_program_dispatch = None;
                }
            }
        }
        self.trace_state.remember_completed_dispatch(preserved);
    }

    fn dispatch_origin_view(&self, origin_cont_id: ContId) -> Option<DispatchOriginView> {
        self.find_dispatch_frame(origin_cont_id)
            .map(|view| Self::dispatch_origin_view_from_program(&view.dispatch))
    }

    fn dispatch_origins_from_segment(
        &self,
        start_segment: Option<SegmentId>,
    ) -> Vec<DispatchOriginView> {
        let mut seen = HashSet::new();
        let mut origins = self
            .dispatch_frames_from_segment(start_segment)
            .into_iter()
            .filter_map(|view| {
                seen.insert(view.dispatch.origin_cont_id)
                    .then(|| Self::dispatch_origin_view_from_program(&view.dispatch))
            })
            .collect::<Vec<_>>();
        origins.sort_by_key(|origin| origin.origin_cont_id.raw());
        origins
    }

    fn dispatch_origin_in_segment_by<T>(
        &self,
        seg_id: SegmentId,
        mut map: impl FnMut(ContId, &DispatchEffect, &Continuation, Option<&PyException>) -> Option<T>,
    ) -> Option<T> {
        let dispatch_origin = self.dispatch_view_in_segment(seg_id)?;
        let origin_k = dispatch_origin.dispatch.origin_as_continuation();
        map(
            dispatch_origin.dispatch.origin_cont_id,
            &dispatch_origin.dispatch.effect,
            &origin_k,
            dispatch_origin.dispatch.original_exception.as_ref(),
        )
    }

    fn dispatch_origin_for_origin_cont_id_anywhere(
        &self,
        origin_cont_id: ContId,
    ) -> Option<DispatchOriginView> {
        self.dispatch_origin_view(origin_cont_id)
    }

    fn dispatch_origin_id_in_segment(&self, seg_id: SegmentId) -> Option<ContId> {
        self.dispatch_view_in_segment(seg_id)
            .map(|view| view.dispatch.origin_cont_id)
    }

    pub(super) fn dispatch_origin_callers(&self) -> Vec<SegmentId> {
        let mut callers = self
            .dispatch_origins_from_segment(self.current_segment)
            .into_iter()
            .filter_map(|origin| {
                self.continuation_handler_chain_start(&origin.k_origin)
                    .map(|caller_seg_id| (origin.origin_cont_id, caller_seg_id))
            })
            .collect::<Vec<_>>();
        callers.sort_by_key(|(origin_cont_id, _)| origin_cont_id.raw());
        callers
            .into_iter()
            .map(|(_, caller_seg_id)| caller_seg_id)
            .collect()
    }

    pub(super) fn dispatch_origin_user_segment_id(
        &self,
        origin_cont_id: ContId,
    ) -> Option<SegmentId> {
        self.find_dispatch_frame(origin_cont_id)
            .and_then(|view| view.dispatch.origin_fiber_ids.first().copied())
    }

    pub(super) fn dispatch_origins(&self) -> Vec<DispatchOriginView> {
        self.dispatch_origins_from_segment(self.current_segment)
    }

    pub(super) fn dispatch_depth(&self) -> usize {
        let Some(mut origin_cont_id) = self.current_origin_cont_id() else {
            return 0;
        };
        let mut seen = HashSet::new();
        let mut depth = 0usize;
        while seen.insert(origin_cont_id) {
            depth += 1;
            let next_origin_cont_id = self
                .dispatch_origin_for_origin_cont_id(origin_cont_id)
                .and_then(|origin| origin.parent_origin_cont_id);
            let Some(next_origin_cont_id) = next_origin_cont_id else {
                break;
            };
            origin_cont_id = next_origin_cont_id;
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
        self.dispatch_frames_from_segment(start_segment)
            .into_iter()
            .map(|view| {
                let continuation = self
                    .active_handler_dispatch_for(view.dispatch.origin_cont_id)
                    .map(|(_, continuation, _)| continuation)
                    .unwrap_or_else(|| view.dispatch.origin_as_continuation());
                LiveDispatchSnapshot {
                    origin_cont_id: view.dispatch.origin_cont_id,
                    effect_repr: Self::effect_repr(&view.dispatch.effect),
                    dispatch_display: view.dispatch.trace.clone(),
                    frames: self.continuation_frame_stack(&continuation),
                }
            })
            .collect()
    }

    pub(super) fn current_dispatch_origin(&self) -> Option<DispatchOriginView> {
        self.first_dispatch_from_segment(self.current_segment)
            .map(|view| Self::dispatch_origin_view_from_program(&view.dispatch))
    }

    pub(super) fn dispatch_origin_for_origin_cont_id(
        &self,
        origin_cont_id: ContId,
    ) -> Option<DispatchOriginView> {
        self.dispatch_origin_for_origin_cont_id_anywhere(origin_cont_id)
    }

    fn dispatch_view_for_continuation_exact(
        &self,
        continuation: &Continuation,
    ) -> Option<DispatchFrameView> {
        self.dispatch_lookup_candidates().into_iter().find(|view| {
            let origin_k = view.dispatch.origin_as_continuation();
            let handler_k = view.dispatch.handler_as_continuation();
            Self::continuation_handle_matches(&origin_k, continuation)
                || Self::continuation_handle_matches(&handler_k, continuation)
        })
    }

    fn dispatch_view_for_continuation_suffix(
        &self,
        continuation: &Continuation,
    ) -> Option<DispatchFrameView> {
        self.dispatch_lookup_candidates()
            .into_iter()
            .filter(|view| {
                let handler_k = view.dispatch.handler_as_continuation();
                Self::continuation_is_suffix(&handler_k, continuation)
            })
            .min_by_key(|view| view.dispatch.handler_fiber_ids.len())
    }

    fn continuation_origin_relation_depth(
        &self,
        continuation: &Continuation,
        target: &Continuation,
        visited: &mut HashSet<ContId>,
    ) -> Option<usize> {
        if !visited.insert(continuation.cont_id) {
            return None;
        }
        if Self::continuation_handle_matches(continuation, target)
            || Self::continuation_is_suffix(continuation, target)
        {
            return Some(0);
        }

        let mut best = None;
        for fiber_id in continuation.fibers() {
            let Some(segment) = self.segments.get(*fiber_id) else {
                continue;
            };
            for frame in &segment.frames {
                let Frame::EvalReturn(eval_return) = frame else {
                    continue;
                };
                let Some(child_continuation) = Self::eval_return_continuation(eval_return.as_ref())
                else {
                    continue;
                };
                let Some(child_depth) =
                    self.continuation_origin_relation_depth(child_continuation, target, visited)
                else {
                    continue;
                };
                let candidate_depth = child_depth + 1;
                best = Some(best.map_or(candidate_depth, |current: usize| {
                    current.min(candidate_depth)
                }));
            }
        }
        best
    }

    fn continuation_dispatch_view(&self, continuation: &Continuation) -> Option<DispatchFrameView> {
        self.dispatch_view_for_continuation_exact(continuation)
            .or_else(|| self.dispatch_view_for_continuation_suffix(continuation))
            .or_else(|| {
                self.dispatch_lookup_candidates()
                    .into_iter()
                    .filter_map(|view| {
                        let mut visited = HashSet::new();
                        let origin_k = view.dispatch.origin_as_continuation();
                        self.continuation_origin_relation_depth(
                            &origin_k,
                            continuation,
                            &mut visited,
                        )
                        .map(|depth| (depth, view))
                    })
                    .min_by_key(|(depth, _)| *depth)
                    .map(|(_, view)| view)
            })
    }

    pub(super) fn dispatch_origin_for_continuation(
        &self,
        continuation: &Continuation,
    ) -> Option<DispatchOriginView> {
        self.continuation_dispatch_view(continuation)
            .map(|view| Self::dispatch_origin_view_from_program(&view.dispatch))
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
        if Self::continuation_handle_matches(continuation, target) {
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
                        | EvalReturnContinuation::InterceptApplyResult { .. }
                        | EvalReturnContinuation::InterceptEvalResult { .. }
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

    fn continuation_dispatch_id(&self, continuation: &Continuation) -> Option<ContId> {
        self.continuation_dispatch_view(continuation)
            .map(|view| view.dispatch.origin_cont_id)
    }

    fn continuation_parent_dispatch_id(&self, continuation: &Continuation) -> Option<ContId> {
        self.continuation_dispatch_view(continuation)
            .and_then(|view| view.dispatch.parent_origin_cont_id)
    }

    pub(super) fn active_handler_marker_for_dispatch(
        &self,
        origin_cont_id: ContId,
    ) -> Option<Marker> {
        let view = self.find_dispatch_frame(origin_cont_id)?;
        self.segments
            .get(view.dispatch.prompt_segment_id)
            .and_then(|seg| seg.handled_marker())
    }

    pub(super) fn current_handler_dispatch(
        &self,
    ) -> Option<(SegmentId, ContId, Continuation, Marker, SegmentId)> {
        let seg_id = self.current_segment?;
        let dispatch = self.dispatch_ref_in_segment(seg_id)?;
        if dispatch.handler_segment_id != seg_id {
            return None;
        }
        let prompt_seg_id = dispatch.prompt_segment_id;
        let marker = self
            .segments
            .get(prompt_seg_id)
            .and_then(|seg| seg.handled_marker())?;
        (!dispatch.handler_consumed).then(|| {
            (
                seg_id,
                dispatch.origin_cont_id,
                dispatch.handler_as_continuation(),
                marker,
                prompt_seg_id,
            )
        })
    }

    pub(super) fn current_live_handler_dispatch(
        &self,
    ) -> Option<(SegmentId, ContId, Continuation, Marker, SegmentId)> {
        self.current_handler_dispatch().or_else(|| {
            let origin_cont_id = self.current_segment_dispatch_id()?;
            self.active_handler_dispatch_for(origin_cont_id).and_then(
                |(handler_seg_id, continuation, marker)| {
                    let prompt_seg_id = self.handler_prompt_segment_id(handler_seg_id, marker)?;
                    Some((
                        handler_seg_id,
                        origin_cont_id,
                        continuation,
                        marker,
                        prompt_seg_id,
                    ))
                },
            )
        })
    }

    pub(super) fn nearest_handler_dispatch(
        &self,
    ) -> Option<(SegmentId, ContId, Continuation, Marker, SegmentId)> {
        let mut cursor = self.current_segment;
        while let Some(seg_id) = cursor {
            let Some(dispatch) = self.dispatch_ref_in_segment(seg_id) else {
                cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
                continue;
            };
            if dispatch.handler_segment_id != seg_id {
                cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
                continue;
            }
            let prompt_seg_id = dispatch.prompt_segment_id;
            let marker = self
                .segments
                .get(prompt_seg_id)
                .and_then(|seg| seg.handled_marker())?;
            if !dispatch.handler_consumed {
                return Some((
                    seg_id,
                    dispatch.origin_cont_id,
                    dispatch.handler_as_continuation(),
                    marker,
                    prompt_seg_id,
                ));
            }
            cursor = self.segments.get(seg_id).and_then(|seg| seg.parent);
        }
        None
    }

    pub(super) fn active_handler_dispatch_for(
        &self,
        origin_cont_id: ContId,
    ) -> Option<(SegmentId, Continuation, Marker)> {
        let view = self.active_dispatch_frame(origin_cont_id)?;
        let marker = self
            .segments
            .get(view.dispatch.prompt_segment_id)
            .and_then(|seg| seg.handled_marker())?;
        (!view.dispatch.handler_consumed).then(|| {
            (
                view.seg_id,
                view.dispatch.handler_as_continuation(),
                marker,
            )
        })
    }

    pub(super) fn handler_dispatch_for_any(
        &self,
        origin_cont_id: ContId,
    ) -> Option<(SegmentId, Continuation, Marker)> {
        let view = self.find_dispatch_frame(origin_cont_id)?;
        let marker = self
            .segments
            .get(view.dispatch.prompt_segment_id)
            .and_then(|seg| seg.handled_marker())?;
        Some((
            view.seg_id,
            view.dispatch.handler_as_continuation(),
            marker,
        ))
    }

    fn clear_forwarded_handler_segment(&mut self, seg_id: SegmentId) {
        let Some(seg) = self.segments.get_mut(seg_id) else {
            return;
        };
        seg.frames.clear();
        let _ = seg;
        self.clear_pending_program_dispatch(seg_id);
        self.clear_pending_error_context(seg_id);
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
        let Some(origin_cont_id) = self.current_segment_dispatch_id_any() else {
            return false;
        };
        if contains_eval_in_scope_return(self.dispatch_origin_user_segment_id(origin_cont_id)) {
            return true;
        }
        let Some(origin) = self.dispatch_origin_for_origin_cont_id(origin_cont_id) else {
            return false;
        };
        self.active_handler_dispatch_for(origin_cont_id)
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

    fn continuation_parent_hint(&self, continuation: &Continuation) -> Option<SegmentId> {
        self.normalize_live_parent_hint(self.continuation_parent(continuation))
    }

    fn root_delegate_parent_segment_id(&self, continuation: &Continuation) -> Option<SegmentId> {
        continuation
            .outermost_fiber_id()
            .filter(|seg_id| self.segments.get(*seg_id).is_some())
            .or_else(|| self.continuation_parent_hint(continuation))
    }

    fn root_live_delegate_parent_segment_id(
        &self,
        continuation: &Continuation,
    ) -> Option<SegmentId> {
        self.continuation_parent_hint(continuation).or_else(|| {
            continuation
                .outermost_fiber_id()
                .filter(|seg_id| self.segments.get(*seg_id).is_some())
        })
    }

    fn delegate_return_target_segment_id(&self, seg_id: SegmentId) -> Option<SegmentId> {
        let mut cursor = Some(seg_id);
        while let Some(current_seg_id) = cursor {
            let seg = self.segments.get(current_seg_id)?;
            if seg.kind.is_intercept_boundary() && seg.frames.is_empty() {
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
            .or_else(|| self.continuation_parent_hint(continuation))
    }

    pub(super) fn continuation_handler_chain_start(
        &self,
        continuation: &Continuation,
    ) -> Option<SegmentId> {
        self.continuation_parent_hint(continuation)
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
                    EvalReturnContinuation::ReturnToContinuation { continuation }
                    | EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                        Some(continuation.clone_handle())
                    }
                    EvalReturnContinuation::ResumeToContinuation { .. }
                    | EvalReturnContinuation::ApplyResolveFunction { .. }
                    | EvalReturnContinuation::ApplyResolveArg { .. }
                    | EvalReturnContinuation::ApplyResolveKwarg { .. }
                    | EvalReturnContinuation::ExpandResolveFactory { .. }
                    | EvalReturnContinuation::ExpandResolveArg { .. }
                    | EvalReturnContinuation::ExpandResolveKwarg { .. }
                    | EvalReturnContinuation::InterceptApplyResult { .. }
                    | EvalReturnContinuation::InterceptEvalResult { .. }
                    | EvalReturnContinuation::TailResumeReturn => None,
                },
                Frame::LexicalScope { .. } => None,
                Frame::Program { .. }
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. } => None,
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

    fn current_user_defined_python_handler_segment(&self) -> Option<SegmentId> {
        let seg_id = self.current_segment?;
        let seg = self.segments.get(seg_id)?;
        seg.frames
            .iter()
            .any(|frame| {
                matches!(
                    frame,
                    Frame::Program {
                        handler_kind: Some(HandlerKind::Python),
                        ..
                    }
                )
            })
            .then_some(seg_id)
            .filter(|seg_id| {
                self.handler_marker_in_caller_chain(*seg_id)
                    .is_some_and(|marker| self.is_user_defined_python_handler_marker(marker))
            })
    }

    fn delegate_return_continuation(
        &mut self,
        continuation: &Continuation,
    ) -> Option<Continuation> {
        let seg_id = self
            .root_live_delegate_parent_segment_id(continuation)
            .and_then(|seg_id| self.delegate_return_target_segment_id(seg_id))?;
        let origin_cont_id = self
            .dispatch_view_in_segment(seg_id)
            .map(|view| view.dispatch.origin_cont_id);
        if let Some(origin_cont_id) = origin_cont_id {
            if let Some((active_seg_id, continuation, _)) =
                self.handler_dispatch_for_any(origin_cont_id)
            {
                if active_seg_id == seg_id && self.handler_dispatch_is_live(&continuation) {
                    return Some(continuation);
                }
            }
        }
        self.segments.get(seg_id)?;
        Some(self.capture_live_continuation(seg_id))
    }

    fn initialize_builtin_prompt_segment(
        &mut self,
        handler: &KleisliRef,
        prompt_seg_id: SegmentId,
    ) {
        if handler.handler_name() == "StateHandler" {
            self.var_store
                .replace_handler_state(prompt_seg_id, self.var_store.global_state().clone());
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

    pub(super) fn continuation_is_consumed(&self, continuation: &Continuation) -> bool {
        continuation.consumed()
    }

    fn materialize_owned_continuation(
        &mut self,
        mut k: Continuation,
        op_name: &str,
    ) -> Result<Continuation, VMError> {
        if !k.is_started() {
            return Ok(k);
        }
        if self.continuation_is_consumed(&k) {
            return Err(VMError::one_shot_violation(k.cont_id));
        }
        if k.fibers()
            .iter()
            .all(|fiber_id| self.segments.get(*fiber_id).is_some())
        {
            return Ok(k);
        }
        k.retain_owned_fibers(|fiber_id| self.segments.get(fiber_id).is_some());
        if k.is_started() {
            return Ok(k);
        }
        Err(VMError::internal(format!(
            "{op_name} continuation {} no longer owns live fibers",
            k.cont_id.raw()
        )))
    }

    pub(crate) fn capture_live_continuation(&mut self, seg_id: SegmentId) -> Continuation {
        let captured_caller = self.segments.get(seg_id).and_then(|segment| segment.parent);
        let continuation = Continuation::from_fiber(seg_id, captured_caller);
        self.touch_segment_topology_subtree(seg_id);
        continuation
    }

    pub fn capture_continuation(&mut self) -> Option<Continuation> {
        let seg_id = self.current_segment?;
        Some(self.capture_live_continuation(seg_id))
    }

    pub(super) fn current_segment_dispatch_id(&self) -> Option<ContId> {
        let seg_id = self.current_segment?;
        self.dispatch_ref_in_segment(seg_id)
            .map(|dispatch| dispatch.origin_cont_id)
            .or_else(|| {
                self.return_to_continuation().and_then(|continuation| {
                    self.continuation_parent_dispatch_id(&continuation)
                        .or_else(|| self.continuation_dispatch_id(&continuation))
                })
            })
            .or_else(|| {
                self.first_dispatch_from_segment(Some(seg_id))
                    .map(|view| view.dispatch.origin_cont_id)
            })
    }

    pub(super) fn current_segment_dispatch_id_any(&self) -> Option<ContId> {
        self.current_segment_dispatch_id()
    }

    pub fn current_origin_cont_id(&self) -> Option<ContId> {
        self.current_active_handler_dispatch_id()
            .or_else(|| self.current_segment_dispatch_id())
    }

    pub fn effect_for_dispatch(&self, origin_cont_id: ContId) -> Option<DispatchEffect> {
        self.dispatch_origin_for_origin_cont_id(origin_cont_id)
            .map(|origin| origin.effect)
    }

    pub fn find_matching_handler(
        &mut self,
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
            let origin_cont_id = self.current_segment_dispatch_id().ok_or_else(|| {
                VMError::internal(
                    "restricted GetExecutionContext dispatch requires a current dispatch id",
                )
            })?;
            let origin = self
                .dispatch_origin_for_origin_cont_id(origin_cont_id)
                .ok_or_else(|| {
                    VMError::internal(format!(
                        "restricted GetExecutionContext dispatch {} missing origin",
                        origin_cont_id.raw()
                    ))
                })?;
            let start_seg_id = self
                .continuation_handler_chain_start(&origin.k_origin)
                .ok_or_else(|| {
                    VMError::internal(format!(
                        "restricted GetExecutionContext dispatch {} missing handler chain start",
                        origin_cont_id.raw()
                    ))
                })?;
            self.handlers_in_caller_chain(start_seg_id)
                .into_iter()
                .map(|entry| entry.prompt_seg_id)
                .collect()
        } else {
            HashSet::new()
        };
        let exclude_prompt = self.current_handler_dispatch().and_then(
            |(active_seg_id, origin_cont_id, _, active_marker, active_prompt_seg_id)| {
                if active_seg_id != seg_id {
                    return None;
                }
                let origin = self.dispatch_origin_for_origin_cont_id(origin_cont_id)?;
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
        let origin_cont_id = ContId::fresh();
        let effect_obj =
            Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|obj| obj.unbind()))
                .map_err(|err| {
                    VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    ))
                })?;

        let cacheable_current_chain =
            exclude_prompt.is_none() && restricted_excluded_prompts.is_empty();
        let effect_type_id = Self::effect_type_cache_key(&effect_obj).map_err(|err| {
            VMError::python_error(format!("failed to derive effect type id: {err}"))
        })?;
        let mut full_current_entries: Option<Vec<HandlerChainEntry>> = None;
        let (_, current_entries) = self.collect_dispatch_handler_entries(
            seg_id,
            exclude_prompt,
            &restricted_excluded_prompts,
        );

        let mut selected = if cacheable_current_chain {
            self.cached_current_chain_handler_resolution(
                seg_id,
                effect_type_id,
                &effect,
                &effect_obj,
                &current_entries,
            )?
        } else {
            None
        };
        let mut handler_chain_snapshot = Self::handler_snapshot_from_entries(&current_entries);
        let mut handler_count = current_entries.len();
        let mut selected_from_current_chain = selected.is_some();
        if selected.is_none() {
            selected =
                self.first_matching_handler_in_entries(&current_entries, &effect, &effect_obj)?;
            selected_from_current_chain = selected.is_some();
        }

        let selected_is_writer = selected
            .as_ref()
            .is_some_and(|(_, _, _, handler)| handler.handler_name() == "WriterHandler");
        let outer_entries = if selected.is_some()
            && !selected_is_writer
            && self.current_handler_dispatch().is_none()
        {
            full_current_entries
                .get_or_insert_with(|| self.full_handler_entries_for_segment(seg_id));
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
            Self::outer_handler_prefix_len(
                full_current_entries
                    .get_or_insert_with(|| self.full_handler_entries_for_segment(seg_id)),
                &outer_entries,
            )
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
            selected_from_current_chain = false;
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
                let Some(boundary) = seg.kind.prompt_boundary() else {
                    cursor = next;
                    continue;
                };
                let handled_marker = boundary.handled_marker;
                let handler = boundary.handler.clone();
                let types = boundary.types.clone();
                let restricted_excluded = restricted_excluded_prompts.contains(&cursor_id);
                if Some(cursor_id) != exclude_prompt && !restricted_excluded {
                    let (name, kind, file, line) = Self::handler_trace_info(&handler);
                    handler_chain_snapshot.push(HandlerSnapshotEntry {
                        handler_name: name,
                        handler_kind: kind,
                        source_file: file,
                        source_line: line,
                    });

                    if handler.can_handle(&effect)? {
                        let should_invoke = self
                            .should_invoke_handler_types(
                                Self::handler_type_cache_key(&handler),
                                types.as_ref(),
                                &effect_obj,
                            )
                            .map_err(|err| {
                                VMError::python_error(format!(
                                    "failed to evaluate WithHandler type filter: {err:?}"
                                ))
                            })?;
                        if should_invoke {
                            if selected.is_none() {
                                selected = Some((
                                    handler_count,
                                    handled_marker,
                                    cursor_id,
                                    handler.clone(),
                                ));
                            }
                        }
                    }

                    handler_count += 1;
                }
                cursor = next;
            }
        }

        if cacheable_current_chain && selected_from_current_chain && fallback_return_to.is_none() {
            if let Some((_, _, prompt_seg_id, _)) = selected.as_ref() {
                self.cache_current_chain_handler_resolution(seg_id, effect_type_id, *prompt_seg_id);
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

        let selected = match selected {
            Some(found) => found,
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
        if self.segments.get(prompt_seg_id).is_none() {
            return Err(VMError::invalid_segment("dispatch prompt not found"));
        }

        let resume_dispatch_id = self.current_segment_dispatch_id();
        let current_handler_marker = self.handler_marker_in_caller_chain(seg_id);
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
                let Some(current_handler_marker) = current_handler_marker else {
                    return true;
                };
                self.find_prompt_boundary_in_caller_chain(origin_seg_id, current_handler_marker)
                    .is_some()
            });
            reusable_origin
                .map(|origin| origin.k_origin.clone_handle())
                .unwrap_or_else(|| self.capture_live_continuation(seg_id))
        } else {
            self.capture_live_continuation(seg_id)
        };
        if let Some(return_to) = fallback_return_to {
            k_user.append_owned_fibers(return_to.clone_handle());
        }
        if let Some(seg_id) = self.current_segment {
            self.clear_pending_error_context(seg_id);
        }

        let effect_frames = self.continuation_frame_stack(&k_user);
        let effect_site = TraceState::effect_site_from_frames(&effect_frames);
        let handler_seg = Segment::new(Some(prompt_seg_id));
        let handler_seg_id = self.alloc_segment(handler_seg);
        self.copy_interceptor_guard_state(Some(seg_id), handler_seg_id);
        let origin_fiber_ids = k_user.fibers().to_vec();
        let origin_consumed = k_user.consumed();
        let handler_cont_id = k_user.cont_id;
        let canonical_prompt_seg_id = self.canonical_output_segment_id(prompt_seg_id);
        self.set_prompt_forward_context(prompt_seg_id, &effect, &k_user);
        if canonical_prompt_seg_id != prompt_seg_id {
            self.set_prompt_forward_context(canonical_prompt_seg_id, &effect, &k_user);
        }
        self.set_pending_program_dispatch(
            handler_seg_id,
            ProgramDispatch {
                origin_cont_id,
                parent_origin_cont_id: resume_dispatch_id,
                handler_segment_id: handler_seg_id,
                prompt_segment_id: canonical_prompt_seg_id,
                effect: effect.clone(),
                trace: Self::dispatch_trace_from_snapshot(
                    &effect,
                    effect_site.clone(),
                    &handler_chain_snapshot,
                ),
                origin_fiber_ids: origin_fiber_ids.clone(),
                handler_fiber_ids: origin_fiber_ids,
                handler_cont_id,
                origin_consumed,
                handler_consumed: origin_consumed,
                original_exception: original_exception.clone(),
            },
        );
        self.current_segment = Some(handler_seg_id);

        // Preserve handler scope when a type-filtered handler is skipped: this mirrors the
        // `Pass()` forwarding topology without invoking the skipped handler body.
        let handler_k = k_user;
        let ir_node = Self::invoke_kleisli_handler_expr(handler, effect, handler_k)?;
        Ok(self.evaluate(ir_node))
    }

    fn error_dispatch_for_continuation(
        &self,
        k: &Continuation,
    ) -> Option<(ContId, PyException, bool)> {
        let origin = self
            .exact_dispatch_origin_for_continuation(k)
            .or_else(|| self.dispatch_origin_for_continuation(k))?;
        let original = origin.original_exception?;
        Some((
            origin.origin_cont_id,
            original,
            k.cont_id == origin.k_origin.cont_id,
        ))
    }

    pub(super) fn dispatch_has_terminal_handler_action(&self, origin_cont_id: ContId) -> bool {
        self.dispatch_trace(origin_cont_id)
            .is_some_and(|trace| !matches!(trace.result, EffectResult::Active))
    }

    pub(super) fn finalize_active_dispatches_as_threw(&mut self, exception: &PyException) {
        let exception_repr = Self::exception_repr(exception);
        for origin in self.dispatch_origins() {
            let origin_cont_id = origin.origin_cont_id;
            if self.dispatch_has_terminal_handler_action(origin_cont_id) {
                continue;
            }
            let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(origin_cont_id)
            else {
                continue;
            };
            self.record_handler_completion(
                origin_cont_id,
                &handler_name,
                handler_index,
                &HandlerAction::Threw {
                    exception_repr: exception_repr.clone(),
                },
            );
        }
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
            return true;
        };
        let seg_marker = seg.boundary_marker().unwrap_or(marker);
        seg.set_boundary(crate::segment::FiberBoundary::prompt(
            seg_marker,
            marker,
            handler.clone(),
            None,
        ));
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
        if let Some(origin_cont_id) = self.continuation_dispatch_id(k) {
            if let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(origin_cont_id)
            {
                let value_repr = Self::value_repr(value);
                self.record_handler_completion(
                    origin_cont_id,
                    &handler_name,
                    handler_index,
                    &kind.handler_action(value_repr.clone()),
                );
                self.emit_resume_event(origin_cont_id, k, kind.is_transferred());
            }
        }
    }

    fn continuation_segment_dispatch_id(&mut self, k: &Continuation) -> Option<ContId> {
        self.continuation_dispatch_id(k)
            .filter(|_| self.dispatch_origin_for_continuation(k).is_some())
    }

    fn alloc_resume_return_anchor(
        &mut self,
        caller: Option<SegmentId>,
        continuation: Continuation,
    ) -> SegmentId {
        let mut anchor = Segment::new(caller);
        anchor.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::ResumeToContinuation { continuation },
        )));
        let anchor_seg_id = self.alloc_segment(anchor);
        self.copy_interceptor_guard_state(self.current_segment, anchor_seg_id);
        anchor_seg_id
    }

    fn alloc_tail_resume_anchor(&mut self, caller: Option<SegmentId>) -> SegmentId {
        let mut anchor = Segment::new(caller);
        anchor.push_frame(Frame::EvalReturn(Box::new(
            EvalReturnContinuation::TailResumeReturn,
        )));
        let anchor_seg_id = self.alloc_segment(anchor);
        self.copy_interceptor_guard_state(self.current_segment, anchor_seg_id);
        anchor_seg_id
    }

    fn segment_is_tail_resume_return(&self, seg_id: SegmentId) -> bool {
        let Some(seg) = self.segments.get(seg_id) else {
            return false;
        };
        let Some(stream) = seg.frames.iter().rev().find_map(|frame| match frame {
            Frame::Program { stream, .. } => Some(stream.clone()),
            Frame::LexicalScope { .. } => None,
            Frame::EvalReturn(_)
            | Frame::MapReturn { .. }
            | Frame::FlatMapBindResult
            | Frame::FlatMapBindSource { .. } => None,
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
            if segment.kind.is_intercept_boundary()
                || self.interceptor_eval_depth(cursor) > 0
                || !self.interceptor_skip_stack_is_empty(cursor)
                || segment.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::EvalReturn(eval_return)
                            if matches!(
                                eval_return.as_ref(),
                                EvalReturnContinuation::InterceptApplyResult { .. }
                                    | EvalReturnContinuation::InterceptEvalResult { .. }
                            )
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
            self.reparent_children(seg_id, caller);
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
            if seg.kind.is_intercept_boundary()
                || self.interceptor_eval_depth(seg_id) > 0
                || !self.interceptor_skip_stack_is_empty(seg_id)
                || seg.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::EvalReturn(eval_return)
                            if matches!(
                                eval_return.as_ref(),
                                EvalReturnContinuation::InterceptApplyResult { .. }
                                    | EvalReturnContinuation::InterceptEvalResult { .. }
                            )
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
            if seg.kind.is_intercept_boundary()
                || self.interceptor_eval_depth(seg_id) > 0
                || !self.interceptor_skip_stack_is_empty(seg_id)
                || seg.frames.iter().any(|frame| {
                    matches!(
                        frame,
                        Frame::EvalReturn(eval_return)
                            if matches!(
                                eval_return.as_ref(),
                                EvalReturnContinuation::InterceptApplyResult { .. }
                                    | EvalReturnContinuation::InterceptEvalResult { .. }
                            )
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
        k: &mut Continuation,
        caller: Option<SegmentId>,
        origin_cont_id: Option<ContId>,
    ) {
        let Some(seg_id) = k.segment_id() else {
            return;
        };
        let caller = self
            .normalize_live_parent_hint(caller)
            .filter(|caller_id| *caller_id != seg_id)
            .or_else(|| {
                self.continuation_parent_hint(k)
                    .filter(|caller_id| *caller_id != seg_id)
            });
        let existing_caller = self.segments.get(seg_id).and_then(|seg| seg.parent);
        let caller = if self.chain_has_interceptor_context(existing_caller)
            && !self.chain_has_interceptor_context(caller)
        {
            existing_caller
        } else {
            caller
        };
        let continuation_dispatch_id = self.continuation_dispatch_id(k);
        let exact_origin_before_bind = origin_cont_id.and_then(|origin_cont_id| {
            (continuation_dispatch_id == Some(origin_cont_id))
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
            // The original exception lives on the active DispatchOrigin.k_origin.
            // Reinstalling it onto resumed continuation segments makes unrelated
            // nested Perform() calls look like fresh GetExecutionContext dispatches.
            self.clear_pending_error_context(*fiber_id);
        }
        self.touch_segment_topology_subtrees(fiber_ids.iter().copied());

        if let Some(origin_cont_id) = origin_cont_id {
            let restoring_outer_dispatch = continuation_dispatch_id != Some(origin_cont_id);
            let resuming_user_defined_python_handler = self
                .active_handler_marker_for_dispatch(origin_cont_id)
                .or_else(|| {
                    self.current_handler_dispatch()
                        .filter(|(_, current_origin_cont_id, ..)| {
                            *current_origin_cont_id == origin_cont_id
                        })
                        .map(|(_, _, _, marker, _)| marker)
                })
                .is_some_and(|marker| self.is_user_defined_python_handler_marker(marker));
            if restoring_outer_dispatch
                || !exact_origin_before_bind.unwrap_or(false)
                || resuming_user_defined_python_handler
            {
                let outer_dispatch = self
                    .find_dispatch_frame(origin_cont_id)
                    .map(|view| view.dispatch);
                let outer_handler_fiber_ids = outer_dispatch
                    .as_ref()
                    .map(|dispatch| dispatch.handler_fiber_ids.clone());
                let outer_handler_cont_id = outer_dispatch
                    .as_ref()
                    .map(|dispatch| dispatch.handler_cont_id);
                let outer_handler_consumed = outer_dispatch
                    .as_ref()
                    .map(|dispatch| dispatch.handler_consumed)
                    .unwrap_or(false);
                let outer_parent_dispatch_id = outer_dispatch
                    .as_ref()
                    .and_then(|dispatch| dispatch.parent_origin_cont_id);
                if let Some(program_dispatch) = self.segment_program_dispatch_mut(seg_id) {
                    if !restoring_outer_dispatch
                        || program_dispatch.origin_cont_id == origin_cont_id
                    {
                        program_dispatch.origin_cont_id = origin_cont_id;
                        program_dispatch.parent_origin_cont_id = outer_parent_dispatch_id;
                        program_dispatch.handler_segment_id = seg_id;
                        if restoring_outer_dispatch {
                            program_dispatch.handler_fiber_ids = outer_handler_fiber_ids.unwrap_or_else(|| k.fibers().to_vec());
                            program_dispatch.handler_cont_id = outer_handler_cont_id.unwrap_or(k.cont_id);
                            program_dispatch.handler_consumed = outer_handler_consumed;
                        } else {
                            program_dispatch.handler_fiber_ids = k.fibers().to_vec();
                            program_dispatch.handler_cont_id = k.cont_id;
                            program_dispatch.handler_consumed = k.consumed();
                        };
                    }
                }
            }
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
        if self.continuation_is_consumed(&k) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        k.mark_consumed();
        self.mark_dispatch_consumed_for_continuation(&k);
        let error_dispatch = self.error_dispatch_for_continuation(&k);
        self.record_continuation_activation(kind, &k, &value);
        if let Err(err) = self.maybe_attach_active_chain_to_execution_context(
            self.continuation_dispatch_id(&k),
            &mut value,
        ) {
            return StepEvent::Error(err);
        }

        if let Some((origin_cont_id, original_exception, terminal)) = error_dispatch {
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
                self.complete_dispatch_context(origin_cont_id);
                // Terminal error-context dispatches must detach from the active handler
                // segment so normal completion does not re-pop the same DispatchOrigin.
                let caller = self.continuation_parent_hint(&k);
                self.enter_or_reenter_continuation_segment_with_dispatch(&mut k, caller, None);
                self.mode = Mode::Throw(enriched_exception);
                return StepEvent::Continue;
            }
        }

        let exact_origin = self.exact_dispatch_origin_for_continuation(&k);
        let origin_cont_id = match kind {
            ContinuationActivationKind::Transfer | ContinuationActivationKind::Resume => {
                if exact_origin.is_some() {
                    self.continuation_parent_dispatch_id(&k)
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
        self.enter_or_reenter_continuation_segment_with_dispatch(&mut k, caller, origin_cont_id);
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    pub(super) fn handle_dispatch_resume(&mut self, k: Continuation, value: Value) -> StepEvent {
        let k = match self.materialize_owned_continuation(k, "Resume") {
            Ok(continuation) => continuation,
            Err(err) => return StepEvent::Error(err),
        };
        let current_origin_cont_id = self.current_origin_cont_id();
        let exact_origin_target = self.exact_dispatch_origin_for_continuation(&k).is_some();
        let continuation_dispatch_id = self.continuation_dispatch_id(&k);
        let caller = continuation_dispatch_id
            .filter(|origin_cont_id| current_origin_cont_id == Some(*origin_cont_id))
            .and_then(|origin_cont_id| {
                self.current_handler_dispatch()
                    .filter(|(_, current_origin_cont_id, ..)| {
                        *current_origin_cont_id == origin_cont_id
                    })
                    .and_then(
                        |(handler_seg_id, _, _continuation, marker, _prompt_seg_id)| {
                            if self.is_user_defined_python_handler_marker(marker) {
                                if self.segment_is_tail_resume_return(handler_seg_id) {
                                    let anchor_seg_id = self.alloc_tail_resume_anchor(
                                        self.continuation_parent_hint(&k),
                                    );
                                    return Some(anchor_seg_id);
                                }
                                let handler_return = self
                                    .capture_continuation()
                                    .expect("dispatch resume requires a live handler segment");
                                let anchor_seg_id = self.alloc_resume_return_anchor(
                                    self.continuation_parent_hint(&k),
                                    handler_return,
                                );
                                return Some(anchor_seg_id);
                            }
                            if exact_origin_target {
                                let handler_return = self
                                    .capture_continuation()
                                    .expect("dispatch resume requires a live handler segment");
                                let anchor_seg_id = self.alloc_resume_return_anchor(
                                    self.continuation_parent_hint(&k),
                                    handler_return,
                                );
                                return Some(anchor_seg_id);
                            }
                            None
                        },
                    )
            })
            .or_else(|| {
                if !exact_origin_target {
                    return None;
                }
                continuation_dispatch_id?;
                let handler_seg_id = self.current_user_defined_python_handler_segment()?;
                if self.segment_is_tail_resume_return(handler_seg_id) {
                    let anchor_seg_id =
                        self.alloc_tail_resume_anchor(self.continuation_parent_hint(&k));
                    return Some(anchor_seg_id);
                }
                let handler_return = self
                    .capture_continuation()
                    .expect("dispatch resume requires a live handler segment");
                let anchor_seg_id = self
                    .alloc_resume_return_anchor(self.continuation_parent_hint(&k), handler_return);
                Some(anchor_seg_id)
            })
            .or_else(|| self.continuation_parent_hint(&k));
        self.activate_continuation(ContinuationActivationKind::Resume, k, value, caller)
    }

    pub(super) fn handle_dispatch_transfer(&mut self, k: Continuation, value: Value) -> StepEvent {
        let k = match self.materialize_owned_continuation(k, "Transfer") {
            Ok(continuation) => continuation,
            Err(err) => return StepEvent::Error(err),
        };
        let caller = self.continuation_parent_hint(&k);
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
        if self.continuation_is_consumed(&k) {
            return self.throw_runtime_error(&format!(
                "one-shot violation: continuation {} already consumed",
                k.cont_id.raw()
            ));
        }
        let continuation_dispatch_id = self.continuation_dispatch_id(&k);
        let exact_view = self.dispatch_view_for_continuation_exact(&k);
        let exact_dispatch_id = exact_view.as_ref().map(|view| view.dispatch.origin_cont_id);
        let exact_dispatch_target = exact_dispatch_id == continuation_dispatch_id;
        let handler_identity = continuation_dispatch_id
            .and_then(|origin_cont_id| self.current_handler_identity_for_dispatch(origin_cont_id));
        k.mark_consumed();
        self.mark_dispatch_consumed_for_continuation(&k);
        let mut thrown_by_context_conversion_handler = self
            .current_active_handler_dispatch_id()
            .is_some_and(|origin_cont_id| {
                self.dispatch_supports_error_context_conversion(origin_cont_id)
            });
        let mut throws_into_dispatch_origin = false;
        if let Some(origin_cont_id) = continuation_dispatch_id {
            throws_into_dispatch_origin = exact_dispatch_target;
            thrown_by_context_conversion_handler =
                self.dispatch_supports_error_context_conversion(origin_cont_id);
            if !self.dispatch_has_terminal_handler_action(origin_cont_id) {
                if let Some((handler_index, handler_name)) = handler_identity.as_ref() {
                    self.record_handler_completion(
                        origin_cont_id,
                        handler_name,
                        *handler_index,
                        &HandlerAction::Threw {
                            exception_repr: Self::exception_repr(&exception),
                        },
                    );
                }
            }
        }
        let current_origin_cont_id = self.current_origin_cont_id();
        let caller = if terminal_dispatch_completion {
            self.continuation_parent_hint(&k)
        } else {
            continuation_dispatch_id
                .filter(|origin_cont_id| current_origin_cont_id == Some(*origin_cont_id))
                .and_then(|origin_cont_id| {
                    self.current_handler_dispatch()
                        .filter(|(_, current_origin_cont_id, ..)| {
                            *current_origin_cont_id == origin_cont_id
                        })
                        .map(|(handler_seg_id, ..)| handler_seg_id)
                })
                .or_else(|| self.continuation_parent_hint(&k))
        };
        let origin_cont_id = if exact_dispatch_target {
            self.continuation_parent_dispatch_id(&k)
                .or_else(|| self.continuation_segment_dispatch_id(&k))
        } else {
            self.continuation_segment_dispatch_id(&k)
        };
        let throws_during_execution_context_dispatch =
            origin_cont_id.is_some_and(|origin_cont_id| {
                self.effect_for_dispatch(origin_cont_id)
                    .is_some_and(|effect| Self::is_execution_context_effect(&effect))
            });
        let original_exception = origin_cont_id
            .and_then(|origin_cont_id| self.original_exception_for_dispatch(origin_cont_id));
        let enter_dispatch_id = if terminal_dispatch_completion && throws_into_dispatch_origin {
            if let Some(origin_cont_id) = origin_cont_id {
                self.complete_dispatch_context(origin_cont_id);
            }
            None
        } else {
            origin_cont_id
        };
        self.enter_or_reenter_continuation_segment_with_dispatch(&mut k, caller, enter_dispatch_id);
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
        let shared_types = types.map(Arc::new);

        let prompt_seg = Segment::new_prompt_with_types(
            plan.handler_marker,
            Some(plan.outside_seg_id),
            plan.handler_marker,
            prompt_handler.clone(),
            shared_types,
        );
        let prompt_seg_id = self.alloc_segment(prompt_seg);
        self.copy_interceptor_guard_state(Some(plan.outside_seg_id), prompt_seg_id);
        self.initialize_builtin_prompt_segment(&prompt_handler, prompt_seg_id);

        let body_seg = Segment::new(Some(prompt_seg_id));
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

        let mut boundary_seg = Segment::new(Some(outside_seg_id));
        boundary_seg.set_boundary(crate::segment::FiberBoundary::intercept(
            interceptor_marker,
            interceptor,
            types,
            mode,
            metadata,
        ));
        let boundary_seg_id = self.alloc_segment(boundary_seg);
        self.copy_interceptor_guard_state(Some(outside_seg_id), boundary_seg_id);

        let body_seg = Segment::new(Some(boundary_seg_id));
        let body_seg_id = self.alloc_segment(body_seg);
        self.copy_interceptor_guard_state(Some(outside_seg_id), body_seg_id);

        self.current_segment = Some(body_seg_id);
        self.evaluate(program)
    }

    fn emit_forward_active_chain_event(
        &mut self,
        kind: ForwardKind,
        origin_cont_id: ContId,
        from_idx: usize,
        to_idx: usize,
    ) {
        match kind {
            ForwardKind::Delegate => {
                self.record_dispatch_delegated(origin_cont_id, from_idx, to_idx)
            }
            ForwardKind::Pass => self.record_dispatch_passed(origin_cont_id, from_idx, to_idx),
        }
    }

    fn make_pass_continuation(
        &mut self,
        prompt_seg_id: SegmentId,
        handler_marker: Marker,
        parent_k_user: &Continuation,
    ) -> Result<Continuation, VMError> {
        let Some(prompt_seg) = self.segments.get(prompt_seg_id) else {
            return Err(VMError::invalid_segment(
                "Pass forwarding prompt segment not found",
            ));
        };
        let Some(boundary) = prompt_seg.kind.prompt_boundary() else {
            return Err(VMError::internal(
                "Pass forwarding requires current prompt boundary segment",
            ));
        };
        let handler = boundary.handler.clone();
        let types = boundary.types.clone();
        let prompt_caller = prompt_seg.parent;
        let mut wrapper_caller = prompt_caller;
        wrapper_caller = parent_k_user
            .outermost_fiber_id()
            .and_then(|fiber_id| self.segments.get(fiber_id))
            .and_then(|segment| segment.parent)
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
                continuation: parent_k_user.clone_handle(),
            }
        } else {
            EvalReturnContinuation::ResumeToContinuation {
                continuation: parent_k_user.clone_handle(),
            }
        };
        pass_seg.push_frame(Frame::EvalReturn(Box::new(eval_return)));
        let pass_cont_id = ContId::fresh();
        pass_seg.parent = wrapper_caller;
        let pass_seg_id = self.alloc_segment(pass_seg);
        self.copy_interceptor_guard_state(Some(prompt_seg_id), pass_seg_id);
        let pass_cont = Continuation::with_id(pass_cont_id, pass_seg_id, captured_caller);
        Ok(pass_cont)
    }

    fn handle_forward(&mut self, kind: ForwardKind) -> StepEvent {
        let handler_dispatch = self.nearest_handler_dispatch().or_else(|| {
            self.current_segment_dispatch_id()
                .and_then(|origin_cont_id| {
                    self.active_handler_dispatch_for(origin_cont_id).and_then(
                        |(seg_id, continuation, marker)| {
                            let prompt_seg_id = self.handler_prompt_segment_id(seg_id, marker)?;
                            Some((seg_id, origin_cont_id, continuation, marker, prompt_seg_id))
                        },
                    )
                })
        });
        let Some((
            inner_seg_id,
            origin_cont_id,
            _dispatch_k,
            current_marker,
            current_prompt_seg_id,
        )) = handler_dispatch
        else {
            return StepEvent::Error(VMError::internal(kind.outside_dispatch_error()));
        };
        let Some(origin) = self.dispatch_origin_for_origin_cont_id(origin_cont_id) else {
            return StepEvent::Error(VMError::internal(format!(
                "{}: dispatch {} not found",
                kind.missing_handler_context(),
                origin_cont_id.raw()
            )));
        };
        let (effect, parent_k_user) = match self.prompt_forward_context(current_prompt_seg_id) {
            Some((effect, continuation)) if effect == origin.effect => (effect, continuation),
            Some(_) | None => (
                origin.effect.clone(),
                self.cloned_continuation_without_error_context(&origin.k_origin),
            ),
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
                let boundary = seg.kind.prompt_boundary()?;
                Some(HandlerChainEntry {
                    marker: boundary.handled_marker,
                    prompt_seg_id: current_prompt_seg_id,
                    handler: boundary.handler.clone(),
                    types: boundary.types.clone(),
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
        let visible_additions = self.visible_ancestor_handler_additions(
            origin_cont_id,
            current_prompt_seg_id,
            &handler_chain[from_idx + 1..],
        );
        if !visible_additions.is_empty() {
            let mut expanded = handler_chain[..from_idx + 1].to_vec();
            expanded.extend(visible_additions);
            expanded.extend_from_slice(&handler_chain[from_idx + 1..]);
            handler_chain = expanded;
        }
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
                let Some(mut k_new) = self.capture_continuation() else {
                    return StepEvent::Error(VMError::internal(
                        "Delegate called without current segment",
                    ));
                };
                let parent_owned = match self
                    .materialize_owned_continuation(parent_k_user.clone_handle(), "Delegate")
                {
                    Ok(continuation) => continuation,
                    Err(err) => return StepEvent::Error(err),
                };
                k_new.append_owned_fibers(parent_owned);
                k_new
            }
            ForwardKind::Pass => match self.make_pass_continuation(
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
                self.emit_forward_active_chain_event(kind, origin_cont_id, from_idx, idx);
                if matches!(kind, ForwardKind::Pass) {
                    self.clear_forwarded_handler_segment(inner_seg_id);
                }

                let handler_seg = Segment::new(Some(entry.prompt_seg_id));
                let handler_seg_id = self.alloc_segment(handler_seg);
                self.copy_interceptor_guard_state(outer_caller, handler_seg_id);
                let observer_k = next_k.clone_handle();
                let forwarded_exception = self.continuation_pending_error_context(&next_k).cloned();
                let canonical_prompt_seg_id = self.canonical_output_segment_id(entry.prompt_seg_id);
                self.set_prompt_forward_context(entry.prompt_seg_id, &effect, &next_k);
                if canonical_prompt_seg_id != entry.prompt_seg_id {
                    self.set_prompt_forward_context(canonical_prompt_seg_id, &effect, &next_k);
                }
                self.set_pending_program_dispatch(
                    handler_seg_id,
                    ProgramDispatch {
                        origin_cont_id,
                        parent_origin_cont_id: origin.parent_origin_cont_id,
                        handler_segment_id: handler_seg_id,
                        prompt_segment_id: canonical_prompt_seg_id,
                        effect: effect.clone(),
                        trace: self
                            .dispatch_trace(origin_cont_id)
                            .cloned()
                            .unwrap_or_else(|| DispatchDisplay {
                                effect_site: None,
                                handler_stack: Vec::new(),
                                transfer_target_repr: None,
                                result: EffectResult::Active,
                                resumed_once: false,
                                is_execution_context_effect: Self::is_execution_context_effect(
                                    &effect,
                                ),
                            }),
                        origin_fiber_ids: origin.k_origin.fibers().to_vec(),
                        origin_consumed: origin.k_origin.consumed(),
                        handler_fiber_ids: observer_k.fibers().to_vec(),
                        handler_cont_id: observer_k.cont_id,
                        handler_consumed: observer_k.consumed(),
                        original_exception: forwarded_exception
                            .clone()
                            .or(origin.original_exception.clone()),
                    },
                );
                self.current_segment = Some(handler_seg_id);
                let handler_k = next_k;
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

    pub(super) fn handle_delegate(&mut self) -> StepEvent {
        self.handle_forward(ForwardKind::Delegate)
    }

    pub(super) fn handle_pass(&mut self) -> StepEvent {
        self.handle_forward(ForwardKind::Pass)
    }

    pub(super) fn handle_handler_return(&mut self, mut value: Value) -> StepEvent {
        let Some(origin_cont_id) = self.current_origin_cont_id() else {
            self.mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };
        let handler_dispatch = self.handler_dispatch_for_any(origin_cont_id);
        let continuation = handler_dispatch
            .as_ref()
            .map(|(_, continuation, _)| continuation.clone_handle());
        let is_python_handler = handler_dispatch
            .as_ref()
            .and_then(|(_, _, marker)| self.marker_handler_trace_info(*marker))
            .is_some_and(|(_, kind, _, _)| kind == HandlerKind::Python);
        let continuation_is_live = continuation
            .as_ref()
            .is_some_and(|continuation| !continuation.consumed());
        let is_user_defined_python_handler = handler_dispatch
            .as_ref()
            .is_some_and(|(_, _, marker)| self.is_user_defined_python_handler_marker(*marker));
        let dispatch_resumed_once = self
            .dispatch_trace(origin_cont_id)
            .is_some_and(|trace| trace.resumed_once);
        let handler_status_is_active = self
            .current_handler_identity_for_dispatch(origin_cont_id)
            .and_then(|(handler_index, _)| {
                self.dispatch_trace(origin_cont_id)
                    .and_then(|trace| trace.handler_stack.get(handler_index))
                    .map(|entry| entry.status == HandlerStatus::Active)
            })
            .unwrap_or(!dispatch_resumed_once);
        if is_python_handler
            && continuation_is_live
            && handler_status_is_active
            && !dispatch_resumed_once
        {
            let mut continuation = continuation.expect("checked above");
            continuation.mark_consumed();
            let _ = self.with_dispatch_mut(origin_cont_id, |d| {
                d.origin_consumed = true;
                d.handler_consumed = true;
            });
            let exception = PyException::handler_protocol_error(format!(
                "handler returned without consuming continuation {}; use Resume(k, v), Transfer(k, v), Discontinue(k, exn), or Pass()",
                continuation.cont_id.raw(),
            ));
            self.emit_handler_threw_for_dispatch(origin_cont_id, &exception);
            self.complete_dispatch_context(origin_cont_id);
            self.mode = Mode::Throw(exception);
            return StepEvent::Continue;
        }
        let original_exception = if !is_python_handler && continuation_is_live {
            None
        } else {
            self.original_exception_for_dispatch(origin_cont_id)
        };
        if original_exception.is_none() && !is_python_handler && continuation_is_live {
            let continuation = continuation.expect("checked above");
            let value_repr = Self::value_repr(&value);
            if let Some((handler_index, handler_name)) =
                self.current_handler_identity_for_dispatch(origin_cont_id)
            {
                self.record_handler_completion(
                    origin_cont_id,
                    &handler_name,
                    handler_index,
                    &HandlerAction::Returned {
                        value_repr: value_repr.clone(),
                    },
                );
                self.emit_resume_event(origin_cont_id, &continuation, false);
            }
            return self.handle_dispatch_resume(continuation, value);
        }
        if original_exception.is_none() && is_user_defined_python_handler && !continuation_is_live {
            // ResultSafe/Try can consume the handler continuation before the Python handler
            // returns here. After removing the old caller-mutation hack, the safe return path is
            // to transfer via the original dispatch topology rather than the exhausted handler k.
            let target = self
                .dispatch_origin_for_origin_cont_id(origin_cont_id)
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
                    self.current_handler_identity_for_dispatch(origin_cont_id)
                {
                    self.record_handler_completion(
                        origin_cont_id,
                        &handler_name,
                        handler_index,
                        &HandlerAction::Returned {
                            value_repr: value_repr.clone(),
                        },
                    );
                    self.emit_resume_event(origin_cont_id, &target, true);
                }
                return self.handle_dispatch_transfer(target, value);
            }
        }
        if continuation_is_live {
            if let Err(err) = self
                .maybe_attach_active_chain_to_execution_context(Some(origin_cont_id), &mut value)
            {
                return StepEvent::Error(err);
            }
        }
        if let (Some((handler_index, handler_name)), Some(continuation)) = (
            self.current_handler_identity_for_dispatch(origin_cont_id),
            continuation.as_ref(),
        ) {
            let value_repr = Self::value_repr(&value);
            self.record_handler_completion(
                origin_cont_id,
                &handler_name,
                handler_index,
                &HandlerAction::Returned {
                    value_repr: value_repr.clone(),
                },
            );
            self.emit_resume_event(origin_cont_id, continuation, false);
        }
        if let Some(original) = original_exception {
            let active_chain = self
                .assemble_active_chain(Some(&original))
                .into_iter()
                .filter(|entry| !matches!(entry, ActiveChainEntry::ContextEntry { .. }))
                .collect();
            self.complete_dispatch_context(origin_cont_id);
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
        self.complete_dispatch_context(origin_cont_id);
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }

    pub(super) fn handle_tail_resume_return(&mut self, value: Value) -> StepEvent {
        let Some(origin_cont_id) = self.current_origin_cont_id() else {
            self.mode = Mode::Deliver(value);
            return StepEvent::Continue;
        };

        if let Some((handler_index, handler_name)) =
            self.current_handler_identity_for_dispatch(origin_cont_id)
        {
            let value_repr = Self::value_repr(&value);
            self.record_handler_completion(
                origin_cont_id,
                &handler_name,
                handler_index,
                &HandlerAction::Returned { value_repr },
            );
        }

        if let Some(original) = self.original_exception_for_dispatch(origin_cont_id) {
            let active_chain = self
                .assemble_active_chain(Some(&original))
                .into_iter()
                .filter(|entry| !matches!(entry, ActiveChainEntry::ContextEntry { .. }))
                .collect();
            self.complete_dispatch_context(origin_cont_id);
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

        self.complete_dispatch_context(origin_cont_id);
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

    fn overlap_prefix_len_with_base(
        base_entries: &[HandlerChainEntry],
        outer_entries: &[HandlerChainEntry],
    ) -> Option<usize> {
        if base_entries.is_empty() {
            return None;
        }

        for prefix_len in 0..outer_entries.len() {
            let overlap = &outer_entries[prefix_len..];
            if overlap.is_empty() || overlap.len() > base_entries.len() {
                continue;
            }
            if overlap
                .iter()
                .zip(base_entries.iter())
                .all(|(outer, base)| Self::same_handler_entry(outer, base))
            {
                return Some(prefix_len);
            }
        }

        None
    }

    fn visible_ancestor_handler_additions(
        &self,
        origin_cont_id: ContId,
        current_prompt_seg_id: SegmentId,
        base_visible_entries: &[HandlerChainEntry],
    ) -> Vec<HandlerChainEntry> {
        let mut additions = Vec::new();
        let mut seen_prompts = base_visible_entries
            .iter()
            .map(|entry| entry.prompt_seg_id)
            .collect::<HashSet<_>>();
        seen_prompts.insert(current_prompt_seg_id);

        let mut cursor = self
            .dispatch_origin_for_origin_cont_id_anywhere(origin_cont_id)
            .and_then(|origin| origin.parent_origin_cont_id);
        let mut seen_dispatches = HashSet::new();
        while let Some(id) = cursor {
            if !seen_dispatches.insert(id) {
                break;
            }
            let Some(origin) = self.dispatch_origin_for_origin_cont_id_anywhere(id) else {
                break;
            };
            if let Some(start_seg_id) = self.continuation_handler_chain_start(&origin.k_origin) {
                let outer_entries = self.handlers_in_caller_chain(start_seg_id);
                let Some(prefix_len) =
                    Self::overlap_prefix_len_with_base(base_visible_entries, &outer_entries)
                else {
                    cursor = origin.parent_origin_cont_id;
                    continue;
                };
                if outer_entries[..prefix_len]
                    .iter()
                    .any(|entry| entry.prompt_seg_id == current_prompt_seg_id)
                {
                    cursor = origin.parent_origin_cont_id;
                    continue;
                }
                for entry in outer_entries[..prefix_len].iter() {
                    if seen_prompts.insert(entry.prompt_seg_id) {
                        additions.push(entry.clone());
                    }
                }
            }
            cursor = origin.parent_origin_cont_id;
        }

        additions
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
        self.continuation_handler_chain_start(continuation)
            .or_else(|| {
                self.continuation_parent_dispatch_id(continuation)
                    .and_then(|origin_cont_id| {
                        self.dispatch_origin_for_origin_cont_id_anywhere(origin_cont_id)
                    })
                    .and_then(|origin| self.continuation_handler_chain_start(&origin.k_origin))
            })
    }

    fn caller_visible_handler_chain_start(&self) -> Result<SegmentId, VMError> {
        if let Some((seg_id, _, continuation, _, _)) = self.current_live_handler_dispatch() {
            if Some(seg_id) == self.current_segment {
                return Ok(seg_id);
            }
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
        let Some(origin_cont_id) = self.current_active_handler_dispatch_id() else {
            return StepEvent::Error(VMError::internal("GetContinuation outside dispatch"));
        };
        let Some((_, k, _)) = self.active_handler_dispatch_for(origin_cont_id) else {
            return StepEvent::Error(VMError::internal(
                "GetContinuation: active handler continuation not found",
            ));
        };
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
        let entries = if self.current_live_handler_dispatch().is_some() {
            let chain_start = self
                .current_dispatch_origin()
                .and_then(|origin| self.continuation_handler_chain_start(&origin.k_origin))
                .or_else(|| {
                    self.current_live_handler_dispatch()
                        .and_then(|(_, _, continuation, _, _)| {
                            self.continuation_handler_chain_start(&continuation)
                        })
                })
                .or_else(|| self.current_segment);
            let Some(chain_start) = chain_start else {
                return StepEvent::Error(VMError::internal(
                    "handler chain requested without current segment",
                ));
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
        if self.current_origin_cont_id().is_none() {
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
        let k = PendingContinuation::create_with_metadata(
            program,
            handlers,
            handler_identities,
            metadata,
            outside_scope.or(self.current_segment),
        );
        self.mode = Mode::Deliver(Value::PendingContinuation(k));
        StepEvent::Continue
    }

    pub(super) fn handle_resume_continuation(
        &mut self,
        k: OwnedControlContinuation,
        value: Value,
    ) -> StepEvent {
        let OwnedControlContinuation::Started(mut k) = k else {
            let OwnedControlContinuation::Pending(k_pending) = k else {
                unreachable!("control continuation variant mismatch")
            };
            let (program, handlers, handler_identities, start_metadata, outside_scope) =
                k_pending.into_parts();

            let Some(current_seg_id) = self.current_segment else {
                return StepEvent::Error(VMError::internal(
                    "pending continuation resumed without current segment",
                ));
            };
            let mut caller_outside = Some(current_seg_id);
            let scope_outside = outside_scope.or(Some(current_seg_id));
            if outside_scope.is_some() {
                let Some(_current_seg) = self.segments.get(current_seg_id) else {
                    return StepEvent::Error(VMError::internal(
                        "pending continuation current segment not found",
                    ));
                };
                let mut return_anchor = Segment::new(scope_outside);
                return_anchor.push_frame(Frame::EvalReturn(Box::new(
                    EvalReturnContinuation::ReturnToContinuation {
                        continuation: self.capture_live_continuation(current_seg_id),
                    },
                )));
                let anchor_seg_id = self.alloc_segment(return_anchor);
                self.copy_interceptor_guard_state(Some(current_seg_id), anchor_seg_id);
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
                let body_seg = Segment::new(Some(prompt_seg_id));
                let body_seg_id = self.alloc_segment(body_seg);
                self.copy_interceptor_guard_state(caller_outside, body_seg_id);

                caller_outside = Some(body_seg_id);
            }

            let body_seg = Segment::new(caller_outside);
            let body_seg_id = self.alloc_segment(body_seg);
            self.copy_interceptor_guard_state(caller_outside, body_seg_id);
            self.current_segment = Some(body_seg_id);
            self.pending_python = Some(PendingPython::EvalExpr {
                metadata: start_metadata,
            });
            return StepEvent::NeedsPython(PythonCall::EvalExpr { expr: program });
        };

        if k.is_started() {
            k = match self.materialize_owned_continuation(k, "ResumeContinuation") {
                Ok(continuation) => continuation,
                Err(err) => return StepEvent::Error(err),
            };
            let caller = self.continuation_parent_hint(&k);
            return self.activate_continuation(
                ContinuationActivationKind::Resume,
                k,
                value,
                caller,
            );
        }

        let caller = self.continuation_parent_hint(&k);
        self.activate_continuation(ContinuationActivationKind::Resume, k, value, caller)
    }
}
