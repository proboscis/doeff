//! Dispatch context model.

use crate::continuation::Continuation;
use crate::effect::DispatchEffect;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::step::PyException;

/// A single handler activation within a dispatch.
///
/// Each time a handler is invoked (initial dispatch, Delegate, or Pass),
/// an activation record tracks the handler identity, the continuation state,
/// and an optional throw target for exception replay.
#[derive(Debug, Clone)]
pub struct HandlerActivation {
    pub handler_idx: usize,
    pub active_handler_seg_id: SegmentId,
    pub k_passed: Continuation,
    pub k_current: Continuation,
    pub throw_target: Option<Continuation>,
    pub supports_error_context_conversion: bool,
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
    pub fn current_activation(&self) -> Option<&HandlerActivation> {
        self.activations.last()
    }

    pub fn current_activation_mut(&mut self) -> Option<&mut HandlerActivation> {
        self.activations.last_mut()
    }

    pub fn current_handler_idx(&self) -> Option<usize> {
        self.current_activation().map(|a| a.handler_idx)
    }

    pub fn current_k_current(&self) -> Option<&Continuation> {
        self.current_activation().map(|a| &a.k_current)
    }

    pub fn current_active_handler_seg_id(&self) -> Option<SegmentId> {
        self.current_activation().map(|a| a.active_handler_seg_id)
    }

    pub fn current_supports_error_context_conversion(&self) -> bool {
        self.current_activation()
            .is_some_and(|a| a.supports_error_context_conversion)
    }
}

/// Backward-compatible type alias during migration.
pub type DispatchContext = Dispatch;
