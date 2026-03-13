//! Dispatch context model with addressable activation stack.
//!
//! Each `Dispatch` tracks one effect dispatch lifecycle. Handler activations
//! form an explicit stack: Delegate pushes, handler return pops.

use crate::continuation::Continuation;
use crate::effect::DispatchEffect;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::step::PyException;

/// One entry per handler invocation in the dispatch chain.
/// Delegate pushes a new activation. Handler return pops. Pass replaces in-place.
#[derive(Debug, Clone)]
pub struct HandlerActivation {
    pub handler_idx: usize,
    /// Segment currently executing this handler clause.
    /// Used to scope self-dispatch exclusion to handler clause execution only.
    pub active_handler_seg_id: SegmentId,
    /// The continuation passed to this handler invocation.
    pub k_passed: Continuation,
    /// Current continuation — tracks Delegate K_new swaps.
    /// Same as k_passed initially; updated when this activation's handler
    /// issues a Delegate that chains a new parent.
    pub k_current: Continuation,
    /// Explicit exception replay target for this activation.
    /// Set during Delegate to the continuation that should receive exceptions
    /// if the handler chain fails — eliminates parent-chain inference.
    pub throw_target: Option<Continuation>,
    pub supports_error_context_conversion: bool,
}

/// Addressable dispatch with an activation stack.
///
/// Replaces the old flat `DispatchContext` (single mutable handler_idx/k_current
/// snapshot). Stored in `DispatchState` by `DispatchId`. Not embedded in any
/// frame or segment.
#[derive(Debug, Clone)]
pub struct Dispatch {
    pub dispatch_id: DispatchId,
    pub effect: DispatchEffect,
    pub is_execution_context_effect: bool,
    pub handler_chain: Vec<Marker>,
    /// Explicit stack of handler activations.
    /// Initial handler push creates the first entry. Delegate pushes.
    /// Handler return pops. Pass replaces the top entry in-place.
    pub activations: Vec<HandlerActivation>,
    pub k_origin: Continuation,
    pub prompt_seg_id: SegmentId,
    pub completed: bool,
    pub original_exception: Option<PyException>,
}

impl Dispatch {
    /// Current (topmost) handler activation, if any.
    pub fn current_activation(&self) -> Option<&HandlerActivation> {
        self.activations.last()
    }

    /// Mutable access to the current (topmost) handler activation.
    pub fn current_activation_mut(&mut self) -> Option<&mut HandlerActivation> {
        self.activations.last_mut()
    }

    /// Current handler index in the chain (from topmost activation).
    pub fn handler_idx(&self) -> usize {
        self.activations
            .last()
            .map(|a| a.handler_idx)
            .unwrap_or(0)
    }

    /// Segment executing the current handler clause.
    pub fn active_handler_seg_id(&self) -> SegmentId {
        self.activations
            .last()
            .map(|a| a.active_handler_seg_id)
            .unwrap_or(self.prompt_seg_id)
    }

    /// Current continuation (from topmost activation, or k_origin if no activations).
    pub fn k_current(&self) -> &Continuation {
        self.activations
            .last()
            .map(|a| &a.k_current)
            .unwrap_or(&self.k_origin)
    }

    /// Whether the current handler supports error context conversion.
    pub fn supports_error_context_conversion(&self) -> bool {
        self.activations
            .last()
            .map(|a| a.supports_error_context_conversion)
            .unwrap_or(false)
    }

    /// Check if a continuation (by cont_id) is terminal in this dispatch.
    ///
    /// Terminal means it corresponds to the first activation's k_passed
    /// (the original continuation), meaning consuming it completes the dispatch.
    pub fn is_terminal_continuation(&self, cont_id: crate::ids::ContId) -> bool {
        if let Some(first) = self.activations.first() {
            first.k_passed.cont_id == cont_id
        } else {
            self.k_origin.cont_id == cont_id
        }
    }

    /// Find the activation whose k_current matches the given cont_id.
    /// Returns (activation_index, is_terminal).
    pub fn find_activation_by_cont_id(
        &self,
        cont_id: crate::ids::ContId,
    ) -> Option<(usize, bool)> {
        for (idx, activation) in self.activations.iter().enumerate() {
            if activation.k_current.cont_id == cont_id
                || activation.k_passed.cont_id == cont_id
            {
                return Some((idx, idx == 0));
            }
        }
        None
    }
}

// Keep backward-compatible type alias during transition
pub type DispatchContext = Dispatch;
