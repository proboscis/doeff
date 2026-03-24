use std::collections::HashMap;

use super::*;
use crate::ids::VarId;

impl VM {
    pub(crate) fn visible_lexical_segments(&self, start_seg_id: SegmentId) -> Vec<SegmentId> {
        fn push_continuation_segments(
            vm: &VM,
            continuation: &Continuation,
            ordered: &mut Vec<SegmentId>,
            seen: &mut std::collections::HashSet<SegmentId>,
            seen_continuations: &mut std::collections::HashSet<crate::ids::FiberId>,
        ) {
            if !seen_continuations.insert(continuation.identity().unwrap_or(crate::ids::FiberId::from_index(usize::MAX))) {
                return;
            }
            for seg_id in continuation.fibers() {
                push_segment_chain(vm, Some(*seg_id), ordered, seen, seen_continuations);
            }
            push_segment_chain(
                vm,
                vm.continuation_parent(continuation),
                ordered,
                seen,
                seen_continuations,
            );
        }

        fn push_fiber_ids_segments(
            vm: &VM,
            fiber_ids: &[crate::ids::FiberId],
            ordered: &mut Vec<SegmentId>,
            seen: &mut std::collections::HashSet<SegmentId>,
            seen_continuations: &mut std::collections::HashSet<crate::ids::FiberId>,
        ) {
            for seg_id in fiber_ids {
                push_segment_chain(vm, Some(*seg_id), ordered, seen, seen_continuations);
            }
            // parent from outermost fiber
            if let Some(parent) = fiber_ids
                .last()
                .and_then(|fiber_id| vm.segments.get(*fiber_id))
                .and_then(|segment| segment.parent)
            {
                push_segment_chain(vm, Some(parent), ordered, seen, seen_continuations);
            }
        }

        fn push_segment_chain(
            vm: &VM,
            start: Option<SegmentId>,
            ordered: &mut Vec<SegmentId>,
            seen: &mut std::collections::HashSet<SegmentId>,
            seen_continuations: &mut std::collections::HashSet<crate::ids::FiberId>,
        ) {
            let mut cursor = start;
            while let Some(seg_id) = cursor {
                let Some(segment) = vm.segments.get(seg_id) else {
                    break;
                };
                cursor = segment.parent;
                if !seen.insert(seg_id) {
                    continue;
                }
                ordered.push(seg_id);
                if let Some(dispatch) = segment.pending_program_dispatch.as_ref() {
                    push_fiber_ids_segments(
                        vm,
                        &dispatch.origin_fiber_ids,
                        ordered,
                        seen,
                        seen_continuations,
                    );
                    push_fiber_ids_segments(
                        vm,
                        &dispatch.handler_fiber_ids,
                        ordered,
                        seen,
                        seen_continuations,
                    );
                }
                for frame in segment.frames.iter().rev() {
                    match frame {
                        Frame::Program {
                            dispatch: Some(dispatch),
                            ..
                        } => {
                            push_fiber_ids_segments(
                                vm,
                                &dispatch.origin_fiber_ids,
                                ordered,
                                seen,
                                seen_continuations,
                            );
                            push_fiber_ids_segments(
                                vm,
                                &dispatch.handler_fiber_ids,
                                ordered,
                                seen,
                                seen_continuations,
                            );
                        }
                        Frame::EvalReturn(eval_return) => match eval_return.as_ref() {
                            EvalReturnContinuation::ResumeToContinuation { fiber_ids }
                            | EvalReturnContinuation::ReturnToContinuation { fiber_ids }
                            | EvalReturnContinuation::EvalInScopeReturn { fiber_ids } => {
                                push_fiber_ids_segments(
                                    vm,
                                    fiber_ids,
                                    ordered,
                                    seen,
                                    seen_continuations,
                                );
                            }
                            EvalReturnContinuation::ApplyResolveFunction { .. }
                            | EvalReturnContinuation::ApplyResolveArg { .. }
                            | EvalReturnContinuation::ApplyResolveKwarg { .. }
                            | EvalReturnContinuation::ExpandResolveFactory { .. }
                            | EvalReturnContinuation::ExpandResolveArg { .. }
                            | EvalReturnContinuation::ExpandResolveKwarg { .. }
                            | EvalReturnContinuation::InterceptApplyResult { .. }
                            | EvalReturnContinuation::InterceptEvalResult { .. }
                            | EvalReturnContinuation::TailResumeReturn => {}
                        },
                        Frame::LexicalScope { .. }
                        | Frame::Program { .. }
                        | Frame::MapReturn { .. }
                        | Frame::FlatMapBindResult
                        | Frame::FlatMapBindSource { .. } => {}
                    }
                }
            }
        }

        let mut ordered = Vec::new();
        let mut seen = std::collections::HashSet::new();
        let mut seen_continuations = std::collections::HashSet::new();
        push_segment_chain(
            self,
            Some(start_seg_id),
            &mut ordered,
            &mut seen,
            &mut seen_continuations,
        );

        ordered
    }

    pub(crate) fn push_lexical_scope_frame(
        &mut self,
        seg_id: SegmentId,
        bindings: HashMap<HashedPyKey, Value>,
    ) -> bool {
        let Some(segment) = self.segments.get_mut(seg_id) else {
            return false;
        };
        segment.frames.insert(
            0,
            Frame::LexicalScope {
                bindings,
                var_overrides: HashMap::new(),
            },
        );
        true
    }

    pub(crate) fn segment_scope_bindings(
        &self,
        seg_id: SegmentId,
    ) -> Option<&HashMap<HashedPyKey, Value>> {
        self.segments
            .get(seg_id)?
            .frames
            .iter()
            .rev()
            .find_map(|frame| {
                if let Frame::LexicalScope { bindings, .. } = frame {
                    Some(bindings)
                } else {
                    None
                }
            })
    }

    pub(crate) fn segment_var_overrides(
        &self,
        seg_id: SegmentId,
    ) -> Option<&HashMap<VarId, Value>> {
        self.segments
            .get(seg_id)?
            .frames
            .iter()
            .rev()
            .find_map(|frame| {
                if let Frame::LexicalScope { var_overrides, .. } = frame {
                    Some(var_overrides)
                } else {
                    None
                }
            })
    }

    pub(crate) fn segment_var_overrides_mut(
        &mut self,
        seg_id: SegmentId,
    ) -> Option<&mut HashMap<VarId, Value>> {
        self.segments
            .get_mut(seg_id)?
            .frames
            .iter_mut()
            .rev()
            .find_map(|frame| {
                if let Frame::LexicalScope { var_overrides, .. } = frame {
                    Some(var_overrides)
                } else {
                    None
                }
            })
    }

    pub fn alloc_scoped_var_in_segment(&mut self, seg_id: SegmentId, initial: Value) -> VarId {
        let var = VarId::fresh(seg_id);
        self.var_store.cells.insert(var, initial);
        var
    }

    pub fn read_scoped_var_from(&self, start_seg_id: SegmentId, var: VarId) -> Option<Value> {
        for seg_id in self.visible_lexical_segments(start_seg_id) {
            if let Some(value) = self
                .segment_var_overrides(seg_id)
                .and_then(|overrides| overrides.get(&var))
            {
                return Some(value.clone());
            }
            if seg_id == var.owner_segment() {
                return self.var_store.cells.get(&var).cloned();
            }
        }
        self.var_store.cells.get(&var).cloned()
    }

    pub fn write_scoped_var_in_current_segment(
        &mut self,
        seg_id: SegmentId,
        var: VarId,
        value: Value,
    ) -> bool {
        if self.segments.get(seg_id).is_none() {
            return false;
        }
        if seg_id == var.owner_segment() {
            self.var_store.cells.insert(var, value);
            return true;
        }
        if self.segment_var_overrides(seg_id).is_none() {
            let _ = self.push_lexical_scope_frame(seg_id, HashMap::new());
        }
        self.segment_var_overrides_mut(seg_id)
            .map(|overrides| overrides.insert(var, value))
            .is_some()
    }

    pub fn write_scoped_var_nonlocal(
        &mut self,
        start_seg_id: SegmentId,
        var: VarId,
        value: Value,
    ) -> bool {
        let visible_segments = self.visible_lexical_segments(start_seg_id);
        for seg_id in visible_segments {
            if self.segments.get(seg_id).is_none() {
                return false;
            }
            if seg_id == var.owner_segment() {
                self.var_store.cells.insert(var, value);
                return true;
            }
            if self
                .segment_var_overrides(seg_id)
                .is_some_and(|overrides| overrides.contains_key(&var))
            {
                return self
                    .segment_var_overrides_mut(seg_id)
                    .map(|overrides| overrides.insert(var, value))
                    .is_some();
            }
        }
        false
    }

    pub fn read_scope_binding_from(
        &self,
        start_seg_id: SegmentId,
        key: &HashedPyKey,
    ) -> Option<Value> {
        for seg_id in self.visible_lexical_segments(start_seg_id) {
            if let Some(value) = self
                .segment_scope_bindings(seg_id)
                .and_then(|bindings| bindings.get(key))
            {
                return Some(value.clone());
            }
        }
        self.var_store.root_scope_bindings().get(key).cloned()
    }
}
