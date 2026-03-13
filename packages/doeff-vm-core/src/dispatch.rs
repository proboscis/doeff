//! Dispatch context model.

use crate::continuation::Continuation;
use crate::effect::DispatchEffect;
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::step::PyException;

#[derive(Debug, Clone)]
pub(crate) struct HandlerActivation {
    pub(crate) handler_idx: usize,
    pub(crate) active_handler_seg_id: SegmentId,
    pub(crate) k_passed: Continuation,
    pub(crate) k_current: Continuation,
    pub(crate) throw_target: Option<Continuation>,
    pub(crate) pending_resume_exception: Option<PyException>,
    pub(crate) supports_error_context_conversion: bool,
}

impl HandlerActivation {
    pub(crate) fn handler_marker(&self, handler_chain: &[Marker]) -> Option<Marker> {
        handler_chain.get(self.handler_idx).copied()
    }

    pub(crate) fn matches_cont_id(&self, cont_id: ContId) -> bool {
        self.k_passed.cont_id == cont_id
            || self.k_current.cont_id == cont_id
            || self
                .throw_target
                .as_ref()
                .is_some_and(|target| target.cont_id == cont_id)
    }
}

#[derive(Debug, Clone)]
pub struct DispatchContext {
    pub dispatch_id: DispatchId,
    pub effect: DispatchEffect,
    pub is_execution_context_effect: bool,
    pub handler_chain: Vec<Marker>,
    pub handler_idx: usize,
    /// Segment currently executing the active handler clause (ctx.handler_idx).
    /// Used to scope self-dispatch exclusion to handler clause execution only.
    pub active_handler_seg_id: SegmentId,
    pub supports_error_context_conversion: bool,
    pub k_origin: Continuation,
    pub k_current: Continuation,
    pub prompt_seg_id: SegmentId,
    pub completed: bool,
    pub original_exception: Option<PyException>,
    pub(crate) activations: Vec<HandlerActivation>,
}

impl DispatchContext {
    pub(crate) fn active_activation(&self) -> Option<&HandlerActivation> {
        self.activations.last()
    }

    pub(crate) fn active_activation_mut(&mut self) -> Option<&mut HandlerActivation> {
        self.activations.last_mut()
    }

    pub(crate) fn sync_active_fields(&mut self) {
        let Some((
            handler_idx,
            active_handler_seg_id,
            supports_error_context_conversion,
            k_current,
        )) = self.active_activation().map(|activation| {
            (
                activation.handler_idx,
                activation.active_handler_seg_id,
                activation.supports_error_context_conversion,
                activation.k_current.clone(),
            )
        }) else {
            return;
        };
        self.handler_idx = handler_idx;
        self.active_handler_seg_id = active_handler_seg_id;
        self.supports_error_context_conversion = supports_error_context_conversion;
        self.k_current = k_current;
    }

    pub(crate) fn current_handler_idx(&self) -> Option<usize> {
        self.active_activation().map(|activation| activation.handler_idx)
    }

    pub(crate) fn current_handler_marker(&self) -> Option<Marker> {
        self.active_activation()
            .and_then(|activation| activation.handler_marker(&self.handler_chain))
    }

    pub(crate) fn current_continuation(&self) -> Option<&Continuation> {
        self.active_activation().map(|activation| &activation.k_current)
    }

    pub(crate) fn current_continuation_mut(&mut self) -> Option<&mut Continuation> {
        self.active_activation_mut()
            .map(|activation| &mut activation.k_current)
    }

    pub(crate) fn current_supports_error_context_conversion(&self) -> bool {
        self.active_activation()
            .is_some_and(|activation| activation.supports_error_context_conversion)
    }

    pub(crate) fn activation_index_for_cont_id(&self, cont_id: ContId) -> Option<usize> {
        self.activations
            .iter()
            .rposition(|activation| activation.matches_cont_id(cont_id))
    }
}
