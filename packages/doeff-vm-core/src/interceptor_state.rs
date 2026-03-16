//! Interceptor-domain state and helper logic for VM composition.

use std::collections::HashMap;

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
