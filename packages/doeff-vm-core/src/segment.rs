//! Segment types for delimited continuations.

use std::collections::HashMap;
use std::sync::Arc;

use crate::continuation::Continuation;
use crate::do_ctrl::InterceptMode;
use crate::effect::DispatchEffect;
use crate::frame::CallMetadata;
use crate::frame::Frame;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::step::{Mode, PendingPython, PyException};
use crate::value::Value;

#[derive(Debug, Clone)]
pub enum SegmentKind {
    Normal,
    PromptBoundary {
        handled_marker: Marker,
        handler: KleisliRef,
        types: Option<Vec<PyShared>>,
    },
    InterceptorBoundary {
        interceptor: KleisliRef,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    },
    MaskBoundary {
        masked_effects: Vec<PyShared>,
        behind: bool,
    },
}

/// Cached dispatch origin data for O(1) lookup.
///
/// Populated when a `Frame::DispatchOrigin` is pushed to the segment,
/// cleared when it is popped. This is derived from frame state (not
/// independently maintained) and eliminates the O(frames) scan per segment.
#[derive(Debug, Clone)]
pub struct CachedDispatchOrigin {
    pub dispatch_id: DispatchId,
    pub effect: DispatchEffect,
    pub k_origin: Continuation,
}

/// Per-segment scope state used by Local/Ask resolution.
#[derive(Debug, Clone, Default)]
pub struct ScopeStore {
    pub scope_bindings: Vec<Arc<HashMap<HashedPyKey, Value>>>,
}

#[derive(Debug)]
pub struct Segment {
    pub marker: Marker,
    pub frames: Vec<Frame>,
    pub caller: Option<SegmentId>,
    pub scope_store: ScopeStore,
    pub kind: SegmentKind,
    pub dispatch_id: Option<DispatchId>,
    pub cached_dispatch_origin: Option<CachedDispatchOrigin>,
    pub mode: Mode,
    pub pending_python: Option<PendingPython>,
    pub pending_error_context: Option<PyException>,
    pub interceptor_eval_depth: usize,
    pub interceptor_skip_stack: Vec<Marker>,
}

impl Segment {
    pub fn new(marker: Marker, caller: Option<SegmentId>) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_store: ScopeStore::default(),
            kind: SegmentKind::Normal,
            dispatch_id: None,
            cached_dispatch_origin: None,
            mode: Mode::Deliver(crate::value::Value::Unit),
            pending_python: None,
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
        }
    }

    pub fn new_prompt(
        marker: Marker,
        caller: Option<SegmentId>,
        handled_marker: Marker,
        handler: KleisliRef,
    ) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_store: ScopeStore::default(),
            kind: SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                types: None,
            },
            dispatch_id: None,
            cached_dispatch_origin: None,
            mode: Mode::Deliver(crate::value::Value::Unit),
            pending_python: None,
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
        }
    }

    pub fn new_prompt_with_types(
        marker: Marker,
        caller: Option<SegmentId>,
        handled_marker: Marker,
        handler: KleisliRef,
        types: Option<Vec<PyShared>>,
    ) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_store: ScopeStore::default(),
            kind: SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                types,
            },
            dispatch_id: None,
            cached_dispatch_origin: None,
            mode: Mode::Deliver(crate::value::Value::Unit),
            pending_python: None,
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
        }
    }

    pub fn push_frame(&mut self, frame: Frame) {
        if let Frame::DispatchOrigin {
            dispatch_id,
            ref effect,
            ref k_origin,
        } = frame
        {
            self.cached_dispatch_origin = Some(CachedDispatchOrigin {
                dispatch_id,
                effect: effect.clone(),
                k_origin: k_origin.clone(),
            });
        }
        self.frames.push(frame);
    }

    pub fn pop_frame(&mut self) -> Option<Frame> {
        let frame = self.frames.pop()?;
        if matches!(frame, Frame::DispatchOrigin { .. }) {
            self.cached_dispatch_origin = None;
        }
        Some(frame)
    }

    pub fn clear_frames(&mut self) {
        self.frames.clear();
        self.cached_dispatch_origin = None;
    }

    /// Rebuild the cached dispatch origin from frames.
    /// Used when restoring a segment from a continuation snapshot.
    pub fn rebuild_dispatch_origin_cache(&mut self) {
        self.cached_dispatch_origin = self.frames.iter().rev().find_map(|frame| match frame {
            Frame::DispatchOrigin {
                dispatch_id,
                effect,
                k_origin,
            } => Some(CachedDispatchOrigin {
                dispatch_id: *dispatch_id,
                effect: effect.clone(),
                k_origin: k_origin.clone(),
            }),
            _ => None,
        });
    }

    pub fn has_frames(&self) -> bool {
        !self.frames.is_empty()
    }

    pub fn frame_count(&self) -> usize {
        self.frames.len()
    }

    pub fn is_prompt_boundary(&self) -> bool {
        matches!(self.kind, SegmentKind::PromptBoundary { .. })
    }

    pub fn handled_marker(&self) -> Option<Marker> {
        match &self.kind {
            SegmentKind::PromptBoundary { handled_marker, .. } => Some(*handled_marker),
            SegmentKind::Normal
            | SegmentKind::InterceptorBoundary { .. }
            | SegmentKind::MaskBoundary { .. } => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_segment_creation() {
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        assert_eq!(seg.marker, marker);
        assert!(seg.caller.is_none());
        assert!(!seg.is_prompt_boundary());
        assert!(seg.handled_marker().is_none());
    }

    #[test]
    fn test_prompt_segment_creation() {
        let marker = Marker::fresh();
        let handled = Marker::fresh();
        let seg = Segment::new_prompt(
            marker,
            None,
            handled,
            std::sync::Arc::new(crate::kleisli::RustKleisli::new(
                std::sync::Arc::new(crate::handler::StateHandlerFactory),
                "StateHandler".to_string(),
            )),
            None,
        );
        assert!(seg.is_prompt_boundary());
        assert_eq!(seg.handled_marker(), Some(handled));
    }

    #[test]
    fn test_segment_frame_push_pop_o1() {
        let marker = Marker::fresh();
        let mut seg = Segment::new(marker, None);
        let continuation =
            crate::continuation::Continuation::capture(&Segment::new(marker, None), SegmentId::from_index(0), None);

        seg.push_frame(Frame::FlatMapBindResult);
        seg.push_frame(Frame::HandlerDispatch {
            dispatch_id: DispatchId::fresh(),
            continuation,
            prompt_seg_id: SegmentId::from_index(0),
        });
        seg.push_frame(Frame::InterceptBodyReturn { marker });

        assert_eq!(seg.frame_count(), 3);

        // Pop should return frames in LIFO order.
        let f3 = seg.pop_frame().unwrap();
        let f2 = seg.pop_frame().unwrap();
        let f1 = seg.pop_frame().unwrap();

        assert!(matches!(f3, Frame::InterceptBodyReturn { .. }));
        assert!(matches!(f2, Frame::HandlerDispatch { .. }));
        assert!(matches!(f1, Frame::FlatMapBindResult));

        assert!(!seg.has_frames());
        assert!(seg.pop_frame().is_none());
    }
}
