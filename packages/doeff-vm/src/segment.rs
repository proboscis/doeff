//! Segment types for delimited continuations.

use crate::frame::Frame;
use crate::handler::HandlerRef;
use crate::ids::{DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::step::{Mode, PendingPython, PyException};

#[derive(Debug, Clone)]
pub enum SegmentKind {
    Normal,
    PromptBoundary {
        handled_marker: Marker,
        handler: HandlerRef,
        return_clause: Option<PyShared>,
        py_identity: Option<PyShared>,
    },
    MaskBoundary {
        masked_effects: Vec<PyShared>,
        behind: bool,
    },
    FinallyBoundary {
        cleanup: PyShared,
    },
}

#[derive(Debug)]
pub struct Segment {
    pub marker: Marker,
    pub frames: Vec<Frame>,
    pub caller: Option<SegmentId>,
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
            caller,
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
        handler: HandlerRef,
        return_clause: Option<PyShared>,
        py_identity: Option<PyShared>,
    ) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            kind: SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                return_clause,
                py_identity,
            },
            dispatch_id: None,
            mode: Mode::Deliver(crate::value::Value::Unit),
            pending_python: None,
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
        }
    }

    pub fn push_frame(&mut self, frame: Frame) {
        self.frames.push(frame);
    }

    pub fn pop_frame(&mut self) -> Option<Frame> {
        self.frames.pop()
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
            | SegmentKind::MaskBoundary { .. }
            | SegmentKind::FinallyBoundary { .. } => None,
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
            std::sync::Arc::new(crate::handler::StateHandlerFactory),
            None,
            None,
        );
        assert!(seg.is_prompt_boundary());
        assert_eq!(seg.handled_marker(), Some(handled));
    }

    #[test]
    fn test_segment_frame_push_pop_o1() {
        let marker = Marker::fresh();
        let mut seg = Segment::new(marker, None);

        seg.push_frame(Frame::FlatMapBindResult);
        seg.push_frame(Frame::HandlerDispatch {
            dispatch_id: DispatchId::fresh(),
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
