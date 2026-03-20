use std::collections::HashMap;

use crate::continuation::Continuation;
use crate::effect::DispatchEffect;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::step::PyException;

#[derive(Debug, Clone)]
pub(crate) struct ActiveHandlerContext {
    pub(crate) segment_id: SegmentId,
    pub(crate) continuation: Continuation,
    pub(crate) marker: Marker,
    pub(crate) prompt_seg_id: SegmentId,
}

#[derive(Debug, Clone)]
pub(crate) struct DispatchContext {
    pub(crate) effect: DispatchEffect,
    pub(crate) k_origin: Continuation,
    pub(crate) original_exception: Option<PyException>,
    pub(crate) active_handler: ActiveHandlerContext,
}

#[derive(Debug, Default, Clone)]
pub(crate) struct DispatchObserver {
    dispatches: HashMap<DispatchId, DispatchContext>,
    segment_dispatch_ids: HashMap<SegmentId, DispatchId>,
}

impl DispatchObserver {
    pub(crate) fn clear(&mut self) {
        self.dispatches.clear();
        self.segment_dispatch_ids.clear();
    }

    pub(crate) fn start_dispatch(
        &mut self,
        dispatch_id: DispatchId,
        effect: DispatchEffect,
        k_origin: Continuation,
        original_exception: Option<PyException>,
        active_handler: ActiveHandlerContext,
    ) {
        self.segment_dispatch_ids
            .insert(active_handler.segment_id, dispatch_id);
        self.dispatches.insert(
            dispatch_id,
            DispatchContext {
                effect,
                k_origin,
                original_exception,
                active_handler,
            },
        );
    }

    pub(crate) fn update_forwarded_dispatch(
        &mut self,
        dispatch_id: DispatchId,
        original_exception: Option<PyException>,
        k_origin: Option<Continuation>,
        active_handler: ActiveHandlerContext,
    ) {
        if let Some(previous_seg_id) = self
            .dispatches
            .get(&dispatch_id)
            .map(|dispatch| dispatch.active_handler.segment_id)
        {
            self.segment_dispatch_ids.remove(&previous_seg_id);
        }
        self.segment_dispatch_ids
            .insert(active_handler.segment_id, dispatch_id);
        if let Some(dispatch) = self.dispatches.get_mut(&dispatch_id) {
            if original_exception.is_some() {
                dispatch.original_exception = original_exception;
            }
            if let Some(k_origin) = k_origin {
                dispatch.k_origin = k_origin;
            }
            dispatch.active_handler = active_handler;
        }
    }

    pub(crate) fn bind_segment(&mut self, seg_id: SegmentId, dispatch_id: DispatchId) {
        self.segment_dispatch_ids.insert(seg_id, dispatch_id);
    }

    pub(crate) fn unbind_segment(&mut self, seg_id: SegmentId) {
        self.segment_dispatch_ids.remove(&seg_id);
    }

    pub(crate) fn segment_dispatch_id(&self, seg_id: SegmentId) -> Option<DispatchId> {
        self.segment_dispatch_ids.get(&seg_id).copied()
    }

    pub(crate) fn dispatch(&self, dispatch_id: DispatchId) -> Option<&DispatchContext> {
        self.dispatches.get(&dispatch_id)
    }

    pub(crate) fn dispatch_mut(&mut self, dispatch_id: DispatchId) -> Option<&mut DispatchContext> {
        self.dispatches.get_mut(&dispatch_id)
    }

    pub(crate) fn iter(&self) -> impl Iterator<Item = (DispatchId, &DispatchContext)> {
        self.dispatches.iter().map(|(dispatch_id, ctx)| (*dispatch_id, ctx))
    }
}
