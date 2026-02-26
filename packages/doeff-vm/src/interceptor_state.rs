//! Interceptor-domain state and helper logic for VM composition.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

use crate::arena::SegmentArena;
use crate::dispatch::DispatchContext;
use crate::doeff_generator::DoeffGenerator;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::HandlerEntry;
use crate::ids::{CallbackId, DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::pyvm::{PyDoCtrlBase, PyDoExprBase, PyEffectBase};
use crate::segment::Segment;
use crate::vm::InterceptorEntry;

#[derive(Clone, Default)]
pub(crate) struct InterceptorState {
    interceptors: HashMap<Marker, InterceptorEntry>,
    interceptor_callbacks: HashMap<CallbackId, Marker>,
    interceptor_call_metadata: HashMap<CallbackId, CallMetadata>,
    interceptor_eval_callbacks: HashSet<CallbackId>,
}

impl InterceptorState {
    pub(crate) fn clear_for_run(&mut self) {
        self.interceptors.clear();
        self.interceptor_callbacks.clear();
        self.interceptor_call_metadata.clear();
        self.interceptor_eval_callbacks.clear();
    }

    pub(crate) fn current_chain(&self, scope_chain: &[Marker]) -> Vec<Marker> {
        scope_chain
            .iter()
            .copied()
            .filter(|marker| self.interceptors.contains_key(marker))
            .collect()
    }

    pub(crate) fn visible_to_active_handler(
        &self,
        interceptor_marker: Marker,
        dispatch_stack: &[DispatchContext],
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
        handlers: &HashMap<Marker, HandlerEntry>,
    ) -> bool {
        let Some(top) = dispatch_stack.last() else {
            return true;
        };
        if top.completed {
            return true;
        }

        let Some(seg_id) = current_segment else {
            return true;
        };
        let Some(seg) = segments.get(seg_id) else {
            return true;
        };
        let Some(handler_marker) = top.handler_chain.get(top.handler_idx).copied() else {
            debug_assert!(false, "handler_idx out of bounds");
            return false;
        };
        if seg.marker != handler_marker {
            return true;
        }

        let Some(entry) = handlers.get(&handler_marker) else {
            debug_assert!(false, "handler marker not in registry");
            return false;
        };
        let Some(prompt_seg) = segments.get(entry.prompt_seg_id) else {
            debug_assert!(false, "prompt segment missing");
            return false;
        };

        prompt_seg.scope_chain.contains(&interceptor_marker)
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
        interceptor: PyShared,
        metadata: Option<CallMetadata>,
    ) {
        self.interceptors.insert(
            marker,
            InterceptorEntry {
                interceptor,
                metadata,
            },
        );
    }

    pub(crate) fn get_entry(&self, marker: Marker) -> Option<InterceptorEntry> {
        self.interceptors.get(&marker).cloned()
    }

    pub(crate) fn register_callback(&mut self, cb: CallbackId, marker: Marker) {
        self.interceptor_callbacks.insert(cb, marker);
    }

    pub(crate) fn unregister_callback(&mut self, cb: CallbackId) -> Option<Marker> {
        self.interceptor_callbacks.remove(&cb)
    }

    pub(crate) fn set_call_metadata(&mut self, cb: CallbackId, metadata: CallMetadata) {
        self.interceptor_call_metadata.insert(cb, metadata);
    }

    pub(crate) fn take_call_metadata(&mut self, cb: CallbackId) -> Option<CallMetadata> {
        self.interceptor_call_metadata.remove(&cb)
    }

    pub(crate) fn register_eval_callback(&mut self, cb: CallbackId) {
        self.interceptor_eval_callbacks.insert(cb);
    }

    pub(crate) fn unregister_eval_callback(&mut self, cb: CallbackId) -> bool {
        self.interceptor_eval_callbacks.remove(&cb)
    }

    pub(crate) fn prepare_with_intercept(
        &mut self,
        interceptor: PyShared,
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
        let outside_scope = outside_seg.scope_chain.clone();

        self.insert(interceptor_marker, interceptor, metadata);

        let mut body_scope = vec![interceptor_marker];
        body_scope.extend(outside_scope);
        let mut body_seg = Segment::new(interceptor_marker, Some(outside_seg_id), body_scope);
        // Inherit guard state — see `copy_interceptor_guard_state` doc for why
        // these fields must be copied rather than derived from frames.
        body_seg.interceptor_eval_depth = outside_seg.interceptor_eval_depth;
        body_seg.interceptor_skip_stack = outside_seg.interceptor_skip_stack.clone();
        Ok(body_seg)
    }
}
