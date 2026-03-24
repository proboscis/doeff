//! Handler chain walking — topology-based handler resolution.

use std::sync::Arc;

use crate::effect::DispatchEffect;
use crate::error::VMError;
use crate::ids::{FiberId, Marker, SegmentId};
use crate::value::CallableRef;
use crate::py_shared::PyShared;
use crate::vm::VM;

/// A handler entry found while walking the caller chain.
#[derive(Clone)]
pub(crate) struct HandlerChainEntry {
    pub marker: Marker,
    pub prompt_seg_id: SegmentId,
    pub handler: CallableRef,
    pub types: Option<Arc<Vec<PyShared>>>,
}

impl VM {
    /// Find the first prompt boundary marker walking up from start_seg_id.
    pub(super) fn handler_marker_in_caller_chain(&self, start_seg_id: SegmentId) -> Option<Marker> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let Some(handler) = &seg.handler {
                if let Some(prompt) = handler.prompt_boundary() {
                    return Some(prompt.handled_marker);
                }
            }
            cursor = seg.parent;
        }
        None
    }

    /// Collect all prompt handlers walking up the caller chain.
    pub(super) fn handlers_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
    ) -> Vec<HandlerChainEntry> {
        let mut chain = Vec::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let Some(handler) = &seg.handler {
                if let Some(prompt) = handler.prompt_boundary() {
                    chain.push(HandlerChainEntry {
                        marker: prompt.handled_marker,
                        prompt_seg_id: seg_id,
                        handler: prompt.handler.clone(),
                        types: prompt.types.clone(),
                    });
                }
            }
            cursor = seg.parent;
        }
        chain
    }

    /// Find a prompt boundary with a specific marker walking up from start.
    pub(super) fn find_prompt_boundary_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
        marker: Marker,
    ) -> Option<SegmentId> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let Some(handler) = &seg.handler {
                if handler.handled_marker() == Some(marker) {
                    return Some(seg_id);
                }
            }
            cursor = seg.parent;
        }
        None
    }

    /// Find a prompt boundary by marker in the entire arena.
    pub(super) fn find_prompt_boundary_by_marker(
        &self,
        marker: Marker,
    ) -> Option<(SegmentId, CallableRef, Option<Arc<Vec<PyShared>>>)> {
        self.segments.iter().find_map(|(seg_id, seg)| {
            let handler = seg.handler.as_ref()?;
            let prompt = handler.prompt_boundary()?;
            (prompt.handled_marker == marker)
                .then(|| (seg_id, prompt.handler.clone(), prompt.types.clone()))
        })
    }

    // same_effect_python_type removed — Python-specific, belongs in bridge layer

    /// Get the current handler chain from current_segment.
    pub(super) fn current_handler_chain(&self) -> Vec<HandlerChainEntry> {
        let Some(seg_id) = self.current_segment else {
            return Vec::new();
        };
        self.handlers_in_caller_chain(seg_id)
    }

    /// Find the handler index for a specific marker in the caller chain.
    pub(super) fn handler_index_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
        marker: Marker,
    ) -> Option<usize> {
        let mut cursor = Some(start_seg_id);
        let mut handler_index = 0usize;
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let Some(handler) = &seg.handler {
                if handler.handled_marker() == Some(marker) {
                    return Some(handler_index);
                }
                if handler.prompt_boundary().is_some() {
                    handler_index += 1;
                }
            }
            cursor = seg.parent;
        }
        None
    }
}
