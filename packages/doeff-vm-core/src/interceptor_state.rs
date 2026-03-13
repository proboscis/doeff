//! Interceptor-domain state and helper logic for VM composition.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

use crate::arena::SegmentArena;
use crate::dispatch_state::DispatchState;
use crate::do_ctrl::InterceptMode;
use crate::doeff_generator::DoeffGenerator;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::pyvm::{PyDoCtrlBase, PyDoExprBase, PyEffectBase};
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
        dispatch_state: &DispatchState,
    ) -> Vec<Marker> {
        let mut chain = Vec::new();
        let mut seen = HashSet::new();
        Self::walk_segment_chain(current_segment, segments, &mut chain, &mut seen);
        let mut dispatch_id = current_segment
            .and_then(|sid| segments.get(sid))
            .and_then(|seg| seg.dispatch_id);
        while let Some(did) = dispatch_id {
            let Some(dispatch) = dispatch_state.find_by_dispatch_id(did) else {
                break;
            };
            if dispatch.completed {
                break;
            }
            let origin_seg_id = dispatch.k_origin.segment_id;
            Self::walk_segment_chain(Some(origin_seg_id), segments, &mut chain, &mut seen);
            dispatch_id = segments
                .get(origin_seg_id)
                .and_then(|seg| seg.dispatch_id)
                .filter(|next| *next != did);
        }
        chain
    }

    fn walk_segment_chain(
        start: Option<SegmentId>,
        segments: &SegmentArena,
        chain: &mut Vec<Marker>,
        seen: &mut HashSet<Marker>,
    ) {
        let mut cursor = start;
        while let Some(seg_id) = cursor {
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

    pub(crate) fn visible_to_active_handler(
        &self,
        _interceptor_marker: Marker,
        _dispatch_state: &DispatchState,
        _current_segment: Option<SegmentId>,
        _segments: &SegmentArena,
    ) -> bool {
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

    pub(crate) fn classify_result_shape(result_obj: &Py<PyAny>) -> (bool, bool) {
        Python::attach(|py| {
            let bound = result_obj.bind(py);
            let is_effect_base = bound.is_instance_of::<PyEffectBase>();
            let is_py_doexpr = bound.is_instance_of::<PyDoExprBase>();
            let is_doexpr =
                is_py_doexpr || bound.is_instance_of::<DoeffGenerator>();
            // Interceptor return values that are already DoExpr objects should be
            // re-classified directly, not eagerly evaluated. The extra Eval step is
            // only for generator-like results that still need to resolve to a DoExpr.
            let is_direct_expr = is_effect_base || is_py_doexpr;
            (is_direct_expr, is_doexpr)
        })
    }

    pub(crate) fn current_active_handler_dispatch_id(
        &self,
        dispatch_state: &DispatchState,
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
    ) -> Option<DispatchId> {
        let seg_id = current_segment?;
        let seg = segments.get(seg_id)?;
        let dispatch_id = seg.dispatch_id?;
        let dispatch = dispatch_state.find_by_dispatch_id(dispatch_id)?;
        if dispatch.completed {
            return None;
        }
        let activation = dispatch.active_activation()?;
        (activation.active_handler_seg_id == seg_id).then_some(dispatch_id)
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
