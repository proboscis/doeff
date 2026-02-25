//! Dispatch context model.

use crate::continuation::Continuation;
use crate::driver::Mode;
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
    pub supports_error_context_conversion: bool,
    pub k_user: Continuation,
    pub prompt_seg_id: SegmentId,
    pub(crate) saved_mode: Mode,
    pub(crate) saved_segment: Option<SegmentId>,
    pub completed: bool,
    pub original_exception: Option<PyException>,
}
