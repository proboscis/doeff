//! Segment types for delimited continuations.

use std::collections::HashMap;
use std::sync::Arc;

use crate::do_ctrl::InterceptMode;
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

/// Per-segment scope state used by Local/Ask resolution.
#[derive(Debug, Clone, Default)]
pub struct ScopeStore {
    pub scope_bindings: Vec<Arc<HashMap<HashedPyKey, Value>>>,
}

#[derive(Debug)]
pub struct Segment {
    pub marker: Marker,
    pub frames: Vec<Frame>,
    pub(crate) dispatch_origin_frame_indices: Vec<usize>,
    pub caller: Option<SegmentId>,
    pub scope_store: ScopeStore,
    pub kind: SegmentKind,
    pub dispatch_id: Option<DispatchId>,
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
            dispatch_origin_frame_indices: Vec::new(),
            caller,
            scope_store: ScopeStore::default(),
            kind: SegmentKind::Normal,
            dispatch_id: None,
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
            dispatch_origin_frame_indices: Vec::new(),
            caller,
            scope_store: ScopeStore::default(),
            kind: SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                types: None,
            },
            dispatch_id: None,
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
            dispatch_origin_frame_indices: Vec::new(),
            caller,
            scope_store: ScopeStore::default(),
            kind: SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                types,
            },
            dispatch_id: None,
            mode: Mode::Deliver(crate::value::Value::Unit),
            pending_python: None,
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
        }
    }

    pub(crate) fn dispatch_origin_frame_indices_from_frames(frames: &[Frame]) -> Vec<usize> {
        frames
            .iter()
            .enumerate()
            .filter_map(|(idx, frame)| matches!(frame, Frame::DispatchOrigin { .. }).then_some(idx))
            .collect()
    }

    pub fn push_frame(&mut self, frame: Frame) {
        if matches!(frame, Frame::DispatchOrigin { .. }) {
            self.dispatch_origin_frame_indices.push(self.frames.len());
        }
        self.frames.push(frame);
    }

    pub fn pop_frame(&mut self) -> Option<Frame> {
        let frame = self.frames.pop()?;
        if matches!(frame, Frame::DispatchOrigin { .. }) {
            let expected_idx = self.frames.len();
            debug_assert_eq!(
                self.dispatch_origin_frame_indices.pop(),
                Some(expected_idx),
                "dispatch origin frame cache out of sync"
            );
        }
        Some(frame)
    }

    pub fn clear_frames(&mut self) {
        self.frames.clear();
        self.dispatch_origin_frame_indices.clear();
    }

    pub fn has_frames(&self) -> bool {
        !self.frames.is_empty()
    }

    pub fn frame_count(&self) -> usize {
        self.frames.len()
    }

    pub fn current_dispatch_origin_frame(&self) -> Option<&Frame> {
        let idx = *self.dispatch_origin_frame_indices.last()?;
        self.frames.get(idx)
    }

    pub fn dispatch_origin_frame_for_dispatch(&self, dispatch_id: DispatchId) -> Option<&Frame> {
        for idx in self.dispatch_origin_frame_indices.iter().rev() {
            let frame = self.frames.get(*idx)?;
            match frame {
                Frame::DispatchOrigin {
                    dispatch_id: frame_dispatch_id,
                    ..
                } if *frame_dispatch_id == dispatch_id => return Some(frame),
                Frame::DispatchOrigin { .. } => continue,
                _ => {
                    debug_assert!(
                        false,
                        "dispatch origin frame cache pointed at a non-DispatchOrigin frame"
                    );
                    return None;
                }
            }
        }
        None
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
        let continuation = crate::continuation::Continuation::capture(
            &Segment::new(marker, None),
            SegmentId::from_index(0),
            None,
        );

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

    #[test]
    fn test_segment_dispatch_origin_cache_tracks_push_pop_and_clear() {
        let marker = Marker::fresh();
        let mut seg = Segment::new(marker, None);
        let continuation = crate::continuation::Continuation::capture(
            &Segment::new(marker, None),
            SegmentId::from_index(0),
            None,
        );

        pyo3::Python::attach(|py| {
            let effect = crate::py_shared::PyShared::new(py.None());
            let dispatch_one = DispatchId::fresh();
            let dispatch_two = DispatchId::fresh();

            seg.push_frame(Frame::DispatchOrigin {
                dispatch_id: dispatch_one,
                effect: effect.clone(),
                k_origin: continuation.clone(),
            });
            assert!(matches!(
                seg.current_dispatch_origin_frame(),
                Some(Frame::DispatchOrigin { dispatch_id, .. }) if *dispatch_id == dispatch_one
            ));

            seg.push_frame(Frame::FlatMapBindResult);
            seg.push_frame(Frame::DispatchOrigin {
                dispatch_id: dispatch_two,
                effect,
                k_origin: continuation.clone(),
            });
            assert!(matches!(
                seg.dispatch_origin_frame_for_dispatch(dispatch_one),
                Some(Frame::DispatchOrigin { dispatch_id, .. }) if *dispatch_id == dispatch_one
            ));
            assert!(matches!(
                seg.current_dispatch_origin_frame(),
                Some(Frame::DispatchOrigin { dispatch_id, .. }) if *dispatch_id == dispatch_two
            ));

            let popped = seg.pop_frame();
            assert!(matches!(
                popped,
                Some(Frame::DispatchOrigin { dispatch_id, .. }) if dispatch_id == dispatch_two
            ));
            assert!(matches!(
                seg.current_dispatch_origin_frame(),
                Some(Frame::DispatchOrigin { dispatch_id, .. }) if *dispatch_id == dispatch_one
            ));

            seg.clear_frames();
            assert!(seg.current_dispatch_origin_frame().is_none());
            assert!(seg
                .dispatch_origin_frame_for_dispatch(dispatch_one)
                .is_none());
        });
    }
}
