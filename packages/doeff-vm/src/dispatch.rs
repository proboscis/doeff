//! Dispatch context model.

use crate::continuation::Continuation;
use crate::effect::DispatchEffect;
use crate::ids::{DispatchId, Marker, SegmentId};

#[derive(Debug, Clone)]
pub struct DispatchContext {
    pub dispatch_id: DispatchId,
    pub effect: DispatchEffect,
    pub handler_chain: Vec<Marker>,
    pub handler_idx: usize,
    pub k_user: Continuation,
    pub prompt_seg_id: SegmentId,
    pub completed: bool,
}
