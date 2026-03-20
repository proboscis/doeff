//! Segment types for delimited continuations.

use std::sync::Arc;

use crate::continuation::Continuation;
use crate::do_ctrl::InterceptMode;
use crate::frame::CallMetadata;
use crate::frame::Frame;
use crate::ids::{FiberId, Marker, ScopeId};
use crate::kleisli::KleisliRef;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::step::PyException;
use crate::value::Value;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub enum FiberKind {
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

#[derive(Debug, Clone)]
pub struct Fiber {
    pub scope_id: ScopeId,
    pub persistent_epoch: u64,
    pub marker: Marker,
    pub frames: Vec<Frame>,
    pub parent: Option<FiberId>,
    pub state_store: HashMap<String, Value>,
    pub writer_log: Vec<Value>,
    pub kind: FiberKind,
    pub pending_error_context: Option<PyException>,
    pub throw_parent: Option<Continuation>,
    pub interceptor_eval_depth: usize,
    pub interceptor_skip_stack: Vec<Marker>,
}

impl Fiber {
    pub fn new(marker: Marker, parent: Option<FiberId>) -> Self {
        Fiber {
            scope_id: ScopeId::fresh(),
            persistent_epoch: 0,
            marker,
            frames: Vec::new(),
            parent,
            state_store: HashMap::new(),
            writer_log: Vec::new(),
            kind: FiberKind::Normal,
            pending_error_context: None,
            throw_parent: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
        }
    }

    pub fn new_prompt(
        marker: Marker,
        parent: Option<FiberId>,
        handled_marker: Marker,
        handler: KleisliRef,
    ) -> Self {
        Fiber {
            scope_id: ScopeId::fresh(),
            persistent_epoch: 0,
            marker,
            frames: Vec::new(),
            parent,
            state_store: HashMap::new(),
            writer_log: Vec::new(),
            kind: FiberKind::PromptBoundary {
                handled_marker,
                handler,
                types: None,
            },
            pending_error_context: None,
            throw_parent: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
        }
    }

    pub fn new_prompt_with_types(
        marker: Marker,
        parent: Option<FiberId>,
        handled_marker: Marker,
        handler: KleisliRef,
        types: Option<Vec<PyShared>>,
    ) -> Self {
        Fiber {
            scope_id: ScopeId::fresh(),
            persistent_epoch: 0,
            marker,
            frames: Vec::new(),
            parent,
            state_store: HashMap::new(),
            writer_log: Vec::new(),
            kind: FiberKind::PromptBoundary {
                handled_marker,
                handler,
                types,
            },
            pending_error_context: None,
            throw_parent: None,
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
        matches!(self.kind, FiberKind::PromptBoundary { .. })
    }

    pub fn handled_marker(&self) -> Option<Marker> {
        match &self.kind {
            FiberKind::PromptBoundary { handled_marker, .. } => Some(*handled_marker),
            FiberKind::Normal
            | FiberKind::InterceptorBoundary { .. }
            | FiberKind::MaskBoundary { .. } => None,
        }
    }
}

pub type Segment = Fiber;
pub type SegmentKind = FiberKind;

#[cfg(test)]
mod tests {
    use super::*;
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
        assert_eq!(seg.marker, marker);
        assert!(seg.parent.is_none());
        assert!(!seg.is_prompt_boundary());
        assert!(seg.handled_marker().is_none());
    }

    #[test]
    fn test_prompt_segment_creation() {
        let marker = Marker::fresh();
        let handled = Marker::fresh();
        let seg = Fiber::new_prompt(marker, None, handled, std::sync::Arc::new(DummyKleisli));
        assert!(seg.is_prompt_boundary());
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
}
