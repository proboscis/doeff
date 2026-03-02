//! Dispatch context model.

use crate::continuation::Continuation;
use crate::effect::DispatchEffect;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::step::PyException;

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
    pub k_user: Continuation,
    pub prompt_seg_id: SegmentId,
    pub completed: bool,
    pub original_exception: Option<PyException>,
}
