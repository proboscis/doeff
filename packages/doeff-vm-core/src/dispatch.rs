//! Dispatch machine-state model.

use crate::continuation::Continuation;
use crate::effect::DispatchEffect;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::step::PyException;

#[derive(Debug, Clone)]
pub struct HandlerActivation {
    pub handler_idx: usize,
    pub active_handler_seg_id: SegmentId,
    pub continuation_passed_to_handler: Continuation,
    /// Continuation used for normal handler return / replay bookkeeping.
    pub current_continuation: Continuation,
    /// Explicit throw replay target for exceptions escaping the handler body.
    pub throw_target: Option<Continuation>,
    pub supports_error_context_conversion: bool,
}

#[derive(Debug, Clone)]
pub struct DispatchFrame {
    pub dispatch_id: DispatchId,
    pub effect: DispatchEffect,
    pub is_execution_context_effect: bool,
    pub handler_chain: Vec<Marker>,
    pub handler_activations: Vec<HandlerActivation>,
    pub k_origin: Continuation,
    pub prompt_seg_id: SegmentId,
    pub completed: bool,
    pub original_exception: Option<PyException>,
}

impl DispatchFrame {
    pub fn current_activation(&self) -> Option<&HandlerActivation> {
        self.handler_activations.last()
    }

    pub fn current_activation_mut(&mut self) -> Option<&mut HandlerActivation> {
        self.handler_activations.last_mut()
    }

    pub fn activation(&self, idx: usize) -> Option<&HandlerActivation> {
        self.handler_activations.get(idx)
    }

    pub fn activation_mut(&mut self, idx: usize) -> Option<&mut HandlerActivation> {
        self.handler_activations.get_mut(idx)
    }

    pub fn current_throw_target(&self) -> Option<&Continuation> {
        self.current_activation()
            .and_then(|activation| activation.throw_target.as_ref())
    }

    pub fn current_continuation(&self) -> Option<&Continuation> {
        self.current_activation()
            .map(|activation| &activation.current_continuation)
    }

    pub fn current_handler_idx(&self) -> Option<usize> {
        self.current_activation().map(|activation| activation.handler_idx)
    }

    pub fn current_active_handler_seg_id(&self) -> Option<SegmentId> {
        self.current_activation()
            .map(|activation| activation.active_handler_seg_id)
    }

    pub fn current_supports_error_context_conversion(&self) -> Option<bool> {
        self.current_activation()
            .map(|activation| activation.supports_error_context_conversion)
    }

    pub fn push_activation(&mut self, activation: HandlerActivation) {
        self.handler_activations.push(activation);
    }

    pub fn replace_current_activation(
        &mut self,
        handler_idx: usize,
        active_handler_seg_id: SegmentId,
        supports_error_context_conversion: bool,
    ) {
        let Some(current) = self.current_activation_mut() else {
            return;
        };
        current.handler_idx = handler_idx;
        current.active_handler_seg_id = active_handler_seg_id;
        current.supports_error_context_conversion = supports_error_context_conversion;
    }

    pub fn replace_activation(
        &mut self,
        idx: usize,
        handler_idx: usize,
        active_handler_seg_id: SegmentId,
        supports_error_context_conversion: bool,
    ) {
        let Some(current) = self.activation_mut(idx) else {
            return;
        };
        current.handler_idx = handler_idx;
        current.active_handler_seg_id = active_handler_seg_id;
        current.supports_error_context_conversion = supports_error_context_conversion;
    }

    pub fn set_current_throw_target(&mut self, continuation: Option<Continuation>) {
        let Some(current) = self.current_activation_mut() else {
            return;
        };
        current.throw_target = continuation;
    }

    pub fn restore_current_continuation_to_passed(&mut self) {
        let Some(current) = self.current_activation_mut() else {
            return;
        };
        current.current_continuation = current.continuation_passed_to_handler.clone();
    }

    pub fn restore_continuation_to_passed(&mut self, idx: usize) {
        let Some(current) = self.activation_mut(idx) else {
            return;
        };
        current.current_continuation = current.continuation_passed_to_handler.clone();
    }

    pub fn set_throw_target_for_activation(
        &mut self,
        idx: usize,
        continuation: Option<Continuation>,
    ) {
        let Some(current) = self.activation_mut(idx) else {
            return;
        };
        current.throw_target = continuation;
    }

    pub fn pop_activation(&mut self) -> Option<HandlerActivation> {
        self.handler_activations.pop()
    }

    pub fn remove_activation(&mut self, idx: usize) -> Option<HandlerActivation> {
        if idx >= self.handler_activations.len() {
            return None;
        }
        Some(self.handler_activations.remove(idx))
    }
}
