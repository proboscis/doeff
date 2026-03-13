//! Dispatch context model.

use crate::continuation::Continuation;
use crate::effect::DispatchEffect;
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::step::PyException;

#[derive(Debug, Clone)]
pub struct HandlerActivation {
    pub handler_idx: usize,
    pub active_handler_seg_id: SegmentId,
    pub k_passed: Continuation,
    pub k_current: Continuation,
    pub throw_target: Option<Continuation>,
    pub pending_resume_exception: Option<PyException>,
    pub supports_error_context_conversion: bool,
}

impl HandlerActivation {
    pub fn handler_marker(&self, handler_chain: &[Marker]) -> Option<Marker> {
        handler_chain.get(self.handler_idx).copied()
    }

    pub fn matches_cont_id(&self, cont_id: ContId) -> bool {
        self.k_passed.cont_id == cont_id
            || self.k_current.cont_id == cont_id
            || self
                .throw_target
                .as_ref()
                .is_some_and(|target| target.cont_id == cont_id)
    }
}

#[derive(Debug, Clone)]
pub struct Dispatch {
    pub dispatch_id: DispatchId,
    pub effect: DispatchEffect,
    pub is_execution_context_effect: bool,
    pub handler_chain: Vec<Marker>,
    pub activations: Vec<HandlerActivation>,
    pub k_origin: Continuation,
    pub prompt_seg_id: SegmentId,
    pub completed: bool,
    pub original_exception: Option<PyException>,
}

impl Dispatch {
    pub fn active_activation(&self) -> Option<&HandlerActivation> {
        self.activations.last()
    }

    pub fn active_activation_mut(&mut self) -> Option<&mut HandlerActivation> {
        self.activations.last_mut()
    }

    pub fn current_handler_idx(&self) -> Option<usize> {
        self.active_activation()
            .map(|activation| activation.handler_idx)
    }

    pub fn current_handler_marker(&self) -> Option<Marker> {
        self.active_activation()
            .and_then(|activation| activation.handler_marker(&self.handler_chain))
    }

    pub fn current_continuation(&self) -> Option<&Continuation> {
        self.active_activation()
            .map(|activation| &activation.k_current)
    }

    pub fn current_continuation_mut(&mut self) -> Option<&mut Continuation> {
        self.active_activation_mut()
            .map(|activation| &mut activation.k_current)
    }

    pub fn supports_error_context_conversion(&self) -> bool {
        self.active_activation()
            .is_some_and(|activation| activation.supports_error_context_conversion)
    }

    pub fn activation_index_for_cont_id(&self, cont_id: ContId) -> Option<usize> {
        self.activations
            .iter()
            .rposition(|activation| activation.matches_cont_id(cont_id))
    }
}
