//! Interceptor-domain state and helper logic for VM composition.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

use crate::arena::SegmentArena;
use crate::dispatch::DispatchContext;
use crate::do_ctrl::InterceptMode;
use crate::doeff_generator::DoeffGenerator;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::pyvm::{PyDoCtrlBase, PyDoExprBase, PyEffectBase};
use crate::segment::Segment;
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
        dispatch_contexts: &[DispatchContext],
    ) -> Vec<Marker> {
        let mut chain = Vec::new();
        let mut seen = HashSet::new();
        Self::walk_segment_chain(
            &self.interceptors,
            current_segment,
            segments,
            &mut chain,
            &mut seen,
        );
        let mut dispatch_id = current_segment
            .and_then(|sid| segments.get(sid))
            .and_then(|seg| seg.dispatch_id);
        while let Some(did) = dispatch_id {
            let ctx = dispatch_contexts
                .iter()
                .rev()
                .find(|ctx| ctx.dispatch_id == did && !ctx.completed);
            let Some(ctx) = ctx else {
                break;
            };
            let origin_seg_id = ctx.k_origin.segment_id;
            Self::walk_segment_chain(
                &self.interceptors,
                Some(origin_seg_id),
                segments,
                &mut chain,
                &mut seen,
            );
            dispatch_id = segments.get(origin_seg_id).and_then(|seg| seg.dispatch_id);
        }
        chain
    }

    fn walk_segment_chain(
        interceptors: &HashMap<Marker, InterceptorEntry>,
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
            if interceptors.contains_key(&seg.marker) && seen.insert(seg.marker) {
                chain.push(seg.marker);
            }
            cursor = seg.caller;
        }
    }

    pub(crate) fn visible_to_active_handler(
        &self,
        _interceptor_marker: Marker,
        _dispatch_stack: &[DispatchContext],
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
            let doctrl_tag = bound
                .extract::<PyRef<'_, PyDoCtrlBase>>()
                .ok()
                .and_then(|base| crate::pyvm::DoExprTag::try_from(base.tag).ok());
            let is_effect_base = bound.is_instance_of::<PyEffectBase>();
            let is_doexpr =
                bound.is_instance_of::<PyDoExprBase>() || bound.is_instance_of::<DoeffGenerator>();
            let is_direct_expr = is_effect_base
                || doctrl_tag.is_some_and(|tag| {
                    tag != crate::pyvm::DoExprTag::Expand && tag != crate::pyvm::DoExprTag::Apply
                });
            (is_direct_expr, is_doexpr)
        })
    }

    pub(crate) fn current_active_handler_dispatch_id(
        &self,
        dispatch_stack: &[DispatchContext],
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
    ) -> Option<DispatchId> {
        let top = dispatch_stack.last()?;
        if top.completed {
            return None;
        }
        let marker = *top.handler_chain.get(top.handler_idx)?;
        let seg_id = current_segment?;
        let seg = segments.get(seg_id)?;
        if seg.marker == marker {
            Some(top.dispatch_id)
        } else {
            None
        }
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

        self.insert(interceptor_marker, interceptor, types, mode, metadata);

        let mut body_seg = Segment::new(interceptor_marker, Some(outside_seg_id));
        // Inherit guard state — see `copy_interceptor_guard_state` doc for why
        // these fields must be copied rather than derived from frames.
        body_seg.interceptor_eval_depth = outside_seg.interceptor_eval_depth;
        body_seg.interceptor_skip_stack = outside_seg.interceptor_skip_stack.clone();
        Ok(body_seg)
    }
}
