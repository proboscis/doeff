//! Segment types for delimited continuations.

use std::sync::Arc;

use crate::do_ctrl::InterceptMode;
use crate::driver::PyException;
use crate::frame::CallMetadata;
use crate::frame::Frame;
use crate::frame::ProgramDispatch;
use crate::ids::{FiberId, Marker};
use crate::kleisli::KleisliRef;
use crate::memory_stats;
use crate::py_shared::PyShared;
pub use crate::scope_store::ScopeStore;

#[derive(Debug, Clone)]
pub enum FiberKind {
    Normal {
        marker: Marker,
    },
    PromptBoundary {
        marker: Marker,
        handled_marker: Marker,
        handler: KleisliRef,
        types: Option<Arc<Vec<PyShared>>>,
    },
    InterceptorBoundary {
        marker: Marker,
        interceptor: KleisliRef,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    },
    MaskBoundary {
        marker: Marker,
        masked_effects: Vec<PyShared>,
        behind: bool,
    },
}

#[derive(Debug)]
pub struct Fiber {
    pub frames: Vec<Frame>,
    pub parent: Option<FiberId>,
    pub kind: FiberKind,
    pub(crate) pending_error_context: Option<PyException>,
    pub(crate) interceptor_eval_depth: usize,
    pub(crate) interceptor_skip_stack: Vec<Marker>,
    pub(crate) pending_program_dispatch: Option<ProgramDispatch>,
}

impl Fiber {
    pub fn new(marker: Marker, parent: Option<FiberId>) -> Self {
        memory_stats::register_segment();
        Fiber {
            frames: Vec::new(),
            parent,
            kind: FiberKind::Normal { marker },
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
            pending_program_dispatch: None,
        }
    }

    pub fn new_prompt(
        marker: Marker,
        parent: Option<FiberId>,
        handled_marker: Marker,
        handler: KleisliRef,
    ) -> Self {
        memory_stats::register_segment();
        Fiber {
            frames: Vec::new(),
            parent,
            kind: FiberKind::PromptBoundary {
                marker,
                handled_marker,
                handler,
                types: None,
            },
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
            pending_program_dispatch: None,
        }
    }

    pub fn new_prompt_with_types(
        marker: Marker,
        parent: Option<FiberId>,
        handled_marker: Marker,
        handler: KleisliRef,
        types: Option<Arc<Vec<PyShared>>>,
    ) -> Self {
        memory_stats::register_segment();
        Fiber {
            frames: Vec::new(),
            parent,
            kind: FiberKind::PromptBoundary {
                marker,
                handled_marker,
                handler,
                types,
            },
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
            pending_program_dispatch: None,
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
        matches!(self.kind, FiberKind::PromptBoundary { .. })
    }

    pub fn marker(&self) -> Marker {
        match &self.kind {
            FiberKind::Normal { marker }
            | FiberKind::PromptBoundary { marker, .. }
            | FiberKind::InterceptorBoundary { marker, .. }
            | FiberKind::MaskBoundary { marker, .. } => *marker,
        }
    }

    pub fn boundary_marker(&self) -> Option<Marker> {
        match &self.kind {
            FiberKind::PromptBoundary { marker, .. } => Some(*marker),
            FiberKind::InterceptorBoundary { marker, .. } => Some(*marker),
            FiberKind::Normal { .. } | FiberKind::MaskBoundary { .. } => None,
        }
    }

    pub fn handled_marker(&self) -> Option<Marker> {
        match &self.kind {
            FiberKind::PromptBoundary { handled_marker, .. } => Some(*handled_marker),
            FiberKind::Normal { .. }
            | FiberKind::InterceptorBoundary { .. }
            | FiberKind::MaskBoundary { .. } => None,
        }
    }
}

impl Drop for Fiber {
    fn drop(&mut self) {
        memory_stats::unregister_segment();
    }
}

pub(crate) use Fiber as Segment;
pub(crate) use FiberKind as SegmentKind;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::memory_stats::live_object_counts;
    use pyo3::Python;

    use crate::do_ctrl::DoCtrl;
    use crate::error::VMError;
    use crate::kleisli::{Kleisli, KleisliDebugInfo};

    #[derive(Debug)]
    struct DummyKleisli;

    impl Kleisli for DummyKleisli {
        fn apply(&self, _py: Python<'_>, _args: Vec<Value>) -> Result<DoCtrl, VMError> {
            unreachable!("test dummy should never be invoked")
        }

        fn debug_info(&self) -> KleisliDebugInfo {
            KleisliDebugInfo {
                name: "DummyKleisli".to_string(),
                file: None,
                line: None,
            }
        }
    }

    #[test]
    fn test_segment_creation() {
        let marker = Marker::fresh();
        let seg = Fiber::new(marker, None);
        assert!(seg.parent.is_none());
        assert!(!seg.is_prompt_boundary());
        assert_eq!(seg.marker(), marker);
        assert!(seg.boundary_marker().is_none());
        assert!(seg.handled_marker().is_none());
    }

    #[test]
    fn test_prompt_segment_creation() {
        let marker = Marker::fresh();
        let handled = Marker::fresh();
        let seg = Fiber::new_prompt(marker, None, handled, std::sync::Arc::new(DummyKleisli));
        assert!(seg.is_prompt_boundary());
        assert_eq!(seg.marker(), marker);
        assert_eq!(seg.boundary_marker(), Some(marker));
        assert_eq!(seg.handled_marker(), Some(handled));
    }

    #[test]
    fn test_segment_frame_push_pop_o1() {
        let marker = Marker::fresh();
        let mut seg = Fiber::new(marker, None);
        seg.push_frame(Frame::FlatMapBindResult);
        seg.push_frame(Frame::InterceptBodyReturn { marker });

        assert_eq!(seg.frame_count(), 2);

        // Pop should return frames in LIFO order.
        let f2 = seg.pop_frame().unwrap();
        let f1 = seg.pop_frame().unwrap();

        assert!(matches!(f2, Frame::InterceptBodyReturn { .. }));
        assert!(matches!(f1, Frame::FlatMapBindResult));

        assert!(!seg.has_frames());
        assert!(seg.pop_frame().is_none());
    }

    #[test]
    fn test_segment_live_count_tracks_live_instances() {
        let baseline = live_object_counts().live_segments;
        let seg_a = Fiber::new(Marker::fresh(), None);
        assert_eq!(live_object_counts().live_segments, baseline + 1);

        let seg_b = Fiber::new(Marker::fresh(), None);
        assert_eq!(live_object_counts().live_segments, baseline + 2);

        drop(seg_b);
        assert_eq!(live_object_counts().live_segments, baseline + 1);

        drop(seg_a);
        assert_eq!(live_object_counts().live_segments, baseline);
    }
}
