//! Handler chain walking — topology-based handler resolution.

use std::sync::Arc;

use pyo3::prelude::*;

use crate::effect::{dispatch_ref_as_python, DispatchEffect};
use crate::error::VMError;
use crate::ids::{FiberId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::vm::VM;

/// A handler entry found while walking the caller chain.
#[derive(Clone)]
pub(crate) struct HandlerChainEntry {
    pub marker: Marker,
    pub prompt_seg_id: SegmentId,
    pub handler: KleisliRef,
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
    ) -> Option<(SegmentId, KleisliRef, Option<Arc<Vec<PyShared>>>)> {
        self.segments.iter().find_map(|(seg_id, seg)| {
            let handler = seg.handler.as_ref()?;
            let prompt = handler.prompt_boundary()?;
            (prompt.handled_marker == marker)
                .then(|| (seg_id, prompt.handler.clone(), prompt.types.clone()))
        })
    }

    /// Check if two effects have the same Python type.
    pub(super) fn same_effect_python_type(a: &DispatchEffect, b: &DispatchEffect) -> bool {
        let Some(a_obj) = dispatch_ref_as_python(a) else {
            return false;
        };
        let Some(b_obj) = dispatch_ref_as_python(b) else {
            return false;
        };
        Python::attach(|py| {
            let a_ty = a_obj.bind(py).get_type();
            let b_ty = b_obj.bind(py).get_type();
            a_ty.as_ptr() == b_ty.as_ptr()
        })
    }

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
