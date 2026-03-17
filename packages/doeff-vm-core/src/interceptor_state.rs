//! Interceptor-domain state and helper logic for VM composition.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

use crate::arena::SegmentArena;
use crate::do_ctrl::InterceptMode;
use crate::do_ctrl::PyDoExprBase;
use crate::doeff_generator::DoeffGenerator;
use crate::effect::PyEffectBase;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::ids::{Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::segment::{Segment, SegmentKind};
use crate::vm::InterceptorEntry;

#[derive(Clone, Default)]
pub(crate) struct InterceptorState {
    interceptors: HashMap<Marker, InterceptorEntry>,
}

impl InterceptorState {
    pub(crate) fn clear_for_run(&mut self) {
        self.interceptors.clear();
    }

    pub(crate) fn current_chain(
        &self,
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
        dispatch_origin_segments: &[SegmentId],
    ) -> Vec<Marker> {
        let mut chain = Vec::new();
        let mut seen = HashSet::new();
        let mut visited_segments = HashSet::new();
        Self::walk_segment_chain(
            current_segment,
            segments,
            &mut chain,
            &mut seen,
            &mut visited_segments,
        );
        for origin_seg_id in dispatch_origin_segments {
            Self::walk_segment_chain(
                Some(*origin_seg_id),
                segments,
                &mut chain,
                &mut seen,
                &mut visited_segments,
            );
        }
        chain
    }

    fn walk_segment_chain(
        start: Option<SegmentId>,
        segments: &SegmentArena,
        chain: &mut Vec<Marker>,
        seen: &mut HashSet<Marker>,
        visited_segments: &mut HashSet<SegmentId>,
    ) {
        let mut cursor = start;
        while let Some(seg_id) = cursor {
            if !visited_segments.insert(seg_id) {
                break;
            }
            let Some(seg) = segments.get(seg_id) else {
                break;
            };
            if matches!(seg.kind, SegmentKind::InterceptorBoundary { .. })
                && seen.insert(seg.marker)
            {
                chain.push(seg.marker);
            }
            cursor = seg.caller;
        }
    }

    pub(crate) fn visible_to_active_handler(&self, _interceptor_marker: Marker) -> bool {
        // WithIntercept sees ALL effects regardless of handler nesting.
        // Re-entrancy is prevented by the skip stack (is_skipped).
        true
    }

    pub(crate) fn is_skipped(seg: &Segment, marker: Marker) -> bool {
        seg.interceptor_skip_stack.contains(&marker)
    }

    pub(crate) fn pop_skip(seg: &mut Segment, marker: Marker) {
        if let Some(pos) = seg
            .interceptor_skip_stack
            .iter()
            .rposition(|active| *active == marker)
        {
            seg.interceptor_skip_stack.remove(pos);
        }
    }

    pub(crate) fn push_skip(seg: &mut Segment, marker: Marker) {
        seg.interceptor_skip_stack.push(marker);
    }

    pub(crate) fn classify_result_shape(result_obj: &PyShared) -> (bool, bool) {
        Python::attach(|py| {
            let bound = result_obj.bind(py);
            let is_effect_base = bound.is_instance_of::<PyEffectBase>();
            let is_py_doexpr = bound.is_instance_of::<PyDoExprBase>();
            let is_doexpr = is_py_doexpr || bound.is_instance_of::<DoeffGenerator>();
            // Interceptor return values that are already DoExpr objects should be
            // re-classified directly, not eagerly evaluated. The extra Eval step is
            // only for generator-like results that still need to resolve to a DoExpr.
            let is_direct_expr = is_effect_base || is_py_doexpr;
            (is_direct_expr, is_doexpr)
        })
    }

    pub(crate) fn insert(
        &mut self,
        marker: Marker,
        interceptor: KleisliRef,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    ) {
        self.interceptors.insert(
            marker,
            InterceptorEntry {
                interceptor,
                types,
                mode,
                metadata,
            },
        );
    }

    pub(crate) fn get_entry(&self, marker: Marker) -> Option<InterceptorEntry> {
        self.interceptors.get(&marker).cloned()
    }

    pub(crate) fn remove(&mut self, marker: Marker) {
        self.interceptors.remove(&marker);
    }

    pub(crate) fn prepare_with_intercept(
        &mut self,
        interceptor: KleisliRef,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
    ) -> Result<Segment, VMError> {
        let interceptor_marker = Marker::fresh();
        let Some(outside_seg_id) = current_segment else {
            return Err(VMError::internal("no current segment for WithIntercept"));
        };
        let outside_seg = segments.get(outside_seg_id).ok_or_else(|| {
            VMError::invalid_segment("current segment not found for WithIntercept")
        })?;

        self.insert(
            interceptor_marker,
            interceptor.clone(),
            types.clone(),
            mode,
            metadata.clone(),
        );

        let mut body_seg = Segment::new(interceptor_marker, Some(outside_seg_id));
        body_seg.kind = SegmentKind::InterceptorBoundary {
            interceptor,
            types,
            mode,
            metadata,
        };
        // Inherit guard state — see `copy_interceptor_guard_state` doc for why
        // these fields must be copied rather than derived from frames.
        body_seg.interceptor_eval_depth = outside_seg.interceptor_eval_depth;
        body_seg.interceptor_skip_stack = outside_seg.interceptor_skip_stack.clone();
        Ok(body_seg)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::Python;
    use std::sync::Arc;

    use crate::do_ctrl::DoCtrl;
    use crate::kleisli::{Kleisli, KleisliDebugInfo};
    use crate::value::Value;

    #[derive(Debug)]
    struct DummyKleisli;

    impl Kleisli for DummyKleisli {
        fn apply(&self, _py: Python<'_>, _args: Vec<Value>) -> Result<DoCtrl, VMError> {
            unreachable!("test dummy should never be invoked")
        }

        fn debug_info(&self) -> KleisliDebugInfo {
            KleisliDebugInfo {
                name: "DummyKleisli".to_string(),
                file: None,
                line: None,
            }
        }
    }

    fn dummy_interceptor_segment(marker: Marker, caller: Option<SegmentId>) -> Segment {
        let mut seg = Segment::new(marker, caller);
        seg.kind = SegmentKind::InterceptorBoundary {
            interceptor: Arc::new(DummyKleisli),
            types: None,
            mode: InterceptMode::Include,
            metadata: None,
        };
        seg
    }

    #[test]
    fn current_chain_deduplicates_shared_segment_tails() {
        let mut arena = SegmentArena::new();

        let tail_marker = Marker::fresh();
        let tail = arena.alloc(dummy_interceptor_segment(tail_marker, None));

        let shared_marker = Marker::fresh();
        let shared = arena.alloc(dummy_interceptor_segment(shared_marker, Some(tail)));

        let current_marker = Marker::fresh();
        let current = arena.alloc(dummy_interceptor_segment(current_marker, Some(shared)));

        let origin_a_marker = Marker::fresh();
        let origin_a = arena.alloc(dummy_interceptor_segment(origin_a_marker, Some(shared)));

        let origin_b_marker = Marker::fresh();
        let origin_b = arena.alloc(dummy_interceptor_segment(origin_b_marker, Some(shared)));

        let state = InterceptorState::default();
        let chain = state.current_chain(Some(current), &arena, &[origin_a, origin_b]);

        assert_eq!(
            chain,
            vec![
                current_marker,
                shared_marker,
                tail_marker,
                origin_a_marker,
                origin_b_marker,
            ]
        );
    }
}
