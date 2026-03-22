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
    Normal,
    Boundary(FiberBoundary),
}

#[derive(Debug, Clone)]
pub struct PromptBoundary {
    pub handled_marker: Marker,
    pub handler: KleisliRef,
    pub types: Option<Arc<Vec<PyShared>>>,
}

#[derive(Debug, Clone)]
pub struct InterceptSpec {
    pub interceptor: KleisliRef,
    pub types: Option<Vec<PyShared>>,
    pub mode: InterceptMode,
    pub metadata: Option<CallMetadata>,
}

#[derive(Debug, Clone)]
pub struct MaskSpec {
    pub masked_effects: Vec<PyShared>,
    pub behind: bool,
}

#[derive(Debug, Clone)]
pub struct FiberBoundary {
    marker: Marker,
    prompt: Option<PromptBoundary>,
    intercept: Option<InterceptSpec>,
    mask: Option<MaskSpec>,
}

impl FiberBoundary {
    pub fn prompt(
        marker: Marker,
        handled_marker: Marker,
        handler: KleisliRef,
        types: Option<Arc<Vec<PyShared>>>,
    ) -> Self {
        Self {
            marker,
            prompt: Some(PromptBoundary {
                handled_marker,
                handler,
                types,
            }),
            intercept: None,
            mask: None,
        }
    }

    pub fn intercept(
        marker: Marker,
        interceptor: KleisliRef,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    ) -> Self {
        Self {
            marker,
            prompt: None,
            intercept: Some(InterceptSpec {
                interceptor,
                types,
                mode,
                metadata,
            }),
            mask: None,
        }
    }

    pub fn mask(marker: Marker, masked_effects: Vec<PyShared>, behind: bool) -> Self {
        Self {
            marker,
            prompt: None,
            intercept: None,
            mask: Some(MaskSpec {
                masked_effects,
                behind,
            }),
        }
    }

    pub fn marker(&self) -> Marker {
        self.marker
    }

    pub fn prompt_boundary(&self) -> Option<&PromptBoundary> {
        self.prompt.as_ref()
    }

    pub fn intercept_boundary(&self) -> Option<&InterceptSpec> {
        self.intercept.as_ref()
    }

    pub fn mask_boundary(&self) -> Option<&MaskSpec> {
        self.mask.as_ref()
    }
}

impl FiberKind {
    pub fn boundary(&self) -> Option<&FiberBoundary> {
        match self {
            FiberKind::Normal => None,
            FiberKind::Boundary(boundary) => Some(boundary),
        }
    }

    pub fn boundary_mut(&mut self) -> Option<&mut FiberBoundary> {
        match self {
            FiberKind::Normal => None,
            FiberKind::Boundary(boundary) => Some(boundary),
        }
    }

    pub fn prompt_boundary(&self) -> Option<&PromptBoundary> {
        self.boundary().and_then(FiberBoundary::prompt_boundary)
    }

    pub fn intercept_boundary(&self) -> Option<&InterceptSpec> {
        self.boundary().and_then(FiberBoundary::intercept_boundary)
    }

    pub fn mask_boundary(&self) -> Option<&MaskSpec> {
        self.boundary().and_then(FiberBoundary::mask_boundary)
    }

    pub fn boundary_marker(&self) -> Option<Marker> {
        self.boundary().map(FiberBoundary::marker)
    }

    pub fn is_prompt_boundary(&self) -> bool {
        self.prompt_boundary().is_some()
    }

    pub fn is_intercept_boundary(&self) -> bool {
        self.intercept_boundary().is_some()
    }

    pub fn is_mask_boundary(&self) -> bool {
        self.mask_boundary().is_some()
    }
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
    pub fn new(_marker: Marker, parent: Option<FiberId>) -> Self {
        memory_stats::register_segment();
        Fiber {
            frames: Vec::new(),
            parent,
            kind: FiberKind::Normal,
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
            kind: FiberKind::Boundary(FiberBoundary::prompt(
                marker,
                handled_marker,
                handler,
                None,
            )),
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
            kind: FiberKind::Boundary(FiberBoundary::prompt(
                marker,
                handled_marker,
                handler,
                types,
            )),
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
            pending_program_dispatch: None,
        }
    }

    pub fn set_boundary(&mut self, boundary: FiberBoundary) {
        self.kind = FiberKind::Boundary(boundary);
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
        self.kind.is_prompt_boundary()
    }

    pub fn marker(&self) -> Marker {
        self.boundary_marker()
            .expect("marker only exists on boundary fibers")
    }

    pub fn boundary_marker(&self) -> Option<Marker> {
        self.kind.boundary_marker()
    }

    pub fn handled_marker(&self) -> Option<Marker> {
        self.kind
            .prompt_boundary()
            .map(|boundary| boundary.handled_marker)
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
    use crate::Value;
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
        assert!(seg.boundary_marker().is_none());
        assert!(seg.handled_marker().is_none());
    }

    #[test]
    fn test_prompt_segment_creation() {
        let marker = Marker::fresh();
        let handled = Marker::fresh();
        let seg = Fiber::new_prompt(marker, None, handled, std::sync::Arc::new(DummyKleisli));
        assert!(seg.is_prompt_boundary());
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
