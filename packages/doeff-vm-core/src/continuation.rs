//! Continuation types for capturing and resuming.

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::frame::CallMetadata;
use crate::frame::Frame;
use crate::ids::{ContId, DispatchId, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::segment::Segment;
use crate::step::PyException;

#[pyclass(name = "K")]
pub struct PyK {
    pub cont_id: ContId,
}

impl PyK {
    pub fn from_cont_id(cont_id: ContId) -> Self {
        Self { cont_id }
    }
}

#[pymethods]
impl PyK {
    fn __repr__(&self) -> String {
        format!("K({})", self.cont_id.raw())
    }
}

/// Capturable continuation with frozen segment snapshot.
///
/// Contains an Arc snapshot of the captured segment state.
/// Resume materializes this snapshot into a new execution segment.
///
/// Continuations can be in two states:
/// - **started=true** (captured): Created from a running segment via `capture()`
/// - **started=false** (unstarted): Created via `create()` with a program and handlers
///
/// When resuming:
/// - Captured continuations: materialize segment_snapshot into a new segment
/// - Unstarted continuations: start the program with handlers installed
#[derive(Debug, Clone)]
pub struct Continuation {
    pub cont_id: ContId,
    pub segment_id: SegmentId,
    pub segment_snapshot: Arc<Segment>,

    /// Whether this continuation is already started.
    /// started=true  => captured continuation (from running code)
    /// started=false => created (unstarted) continuation
    pub started: bool,

    pub program: Option<PyShared>,

    /// Handlers to install when started=false (innermost first).
    /// Empty for captured (started=true) continuations.
    pub handlers: Vec<KleisliRef>,

    /// Optional Python identities corresponding to handlers by index.
    /// Used to preserve Rust sentinel identity across continuation round-trips.
    pub handler_identities: Vec<Option<PyShared>>,

    /// Optional call metadata to attach when starting unstarted continuations.
    pub metadata: Option<CallMetadata>,

    /// Parent continuation captured during Delegate chaining.
    pub parent: Option<Arc<Continuation>>,
}

impl Continuation {
    fn captured_frames(segment: &Segment) -> Vec<Frame> {
        let keep_dispatch_origin = segment
            .frames
            .iter()
            .any(|frame| matches!(frame, Frame::HandlerDispatch { .. }));
        // DispatchOrigin frames are only meaningful when the snapshot is resuming the
        // handler segment that owns them. Plain continuation snapshots keep the older
        // behavior and drop orphan origins.
        segment
            .frames
            .iter()
            .filter(|frame| match frame {
                Frame::DispatchOrigin { .. } => keep_dispatch_origin,
                Frame::Program { .. }
                | Frame::InterceptorApply(_)
                | Frame::InterceptorEval(_)
                | Frame::HandlerDispatch { .. }
                | Frame::EvalReturn(_)
                | Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. }
                | Frame::InterceptBodyReturn { .. } => true,
            })
            .cloned()
            .collect()
    }

    fn captured_segment_snapshot(
        segment: &Segment,
        dispatch_id: Option<DispatchId>,
    ) -> Arc<Segment> {
        let mut snapshot = segment.clone();
        snapshot.frames = Self::captured_frames(segment);
        snapshot.dispatch_id = dispatch_id;
        Arc::new(snapshot)
    }

    pub fn capture(
        segment: &Segment,
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id,
            segment_snapshot: Self::captured_segment_snapshot(segment, dispatch_id),
            started: true,
            program: None,
            handlers: Vec::new(),
            handler_identities: Vec::new(),
            metadata: None,
            parent: None,
        }
    }

    pub fn with_id(
        cont_id: ContId,
        segment: &Segment,
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Continuation {
            cont_id,
            segment_id,
            segment_snapshot: Self::captured_segment_snapshot(segment, dispatch_id),
            started: true,
            program: None,
            handlers: Vec::new(),
            handler_identities: Vec::new(),
            metadata: None,
            parent: None,
        }
    }

    pub fn create_unstarted(expr: PyShared, handlers: Vec<KleisliRef>) -> Self {
        Self::create_unstarted_with_metadata(expr, handlers, None)
    }

    pub fn create_unstarted_with_metadata(
        expr: PyShared,
        handlers: Vec<KleisliRef>,
        metadata: Option<CallMetadata>,
    ) -> Self {
        let handler_identities = vec![None; handlers.len()];
        let snapshot = Arc::new(Segment::new(crate::ids::Marker::placeholder(), None));
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId::from_index(0),
            segment_snapshot: snapshot,
            started: false,
            program: Some(expr),
            handlers,
            handler_identities,
            metadata,
            parent: None,
        }
    }

    pub fn create_unstarted_with_identities(
        expr: PyShared,
        handlers: Vec<KleisliRef>,
        handler_identities: Vec<Option<PyShared>>,
    ) -> Self {
        Self::create_unstarted_with_identities_and_metadata(
            expr,
            handlers,
            handler_identities,
            None,
        )
    }

    pub fn create_unstarted_with_identities_and_metadata(
        expr: PyShared,
        handlers: Vec<KleisliRef>,
        handler_identities: Vec<Option<PyShared>>,
        metadata: Option<CallMetadata>,
    ) -> Self {
        let snapshot = Arc::new(Segment::new(crate::ids::Marker::placeholder(), None));
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId::from_index(0),
            segment_snapshot: snapshot,
            started: false,
            program: Some(expr),
            handlers,
            handler_identities,
            metadata,
            parent: None,
        }
    }

    pub fn is_started(&self) -> bool {
        self.started
    }

    pub fn segment(&self) -> &Segment {
        self.segment_snapshot.as_ref()
    }

    pub fn segment_mut(&mut self) -> &mut Segment {
        Arc::make_mut(&mut self.segment_snapshot)
    }

    pub fn frames(&self) -> &[Frame] {
        &self.segment().frames
    }

    pub fn dispatch_id(&self) -> Option<DispatchId> {
        self.segment().dispatch_id
    }

    // Plain Resume/Transfer restore the capture-time caller from the segment snapshot.
    // Dispatch Resume is the explicit override that re-enters the active handler segment.
    pub fn captured_caller(&self) -> Option<SegmentId> {
        self.segment().caller
    }

    pub fn marker(&self) -> crate::ids::Marker {
        self.segment().marker
    }

    pub fn pending_error_context(&self) -> Option<&PyException> {
        self.segment().pending_error_context.as_ref()
    }

    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("cont_id", self.cont_id.raw())?;
        dict.set_item("started", self.started)?;
        if let Some(ref program) = self.program {
            dict.set_item("program", program.bind(py))?;
        }
        if !self.handlers.is_empty() {
            let list = PyList::empty(py);
            for (idx, handler) in self.handlers.iter().enumerate() {
                if let Some(Some(identity)) = self.handler_identities.get(idx) {
                    list.append(identity.bind(py))?;
                    continue;
                }
                if let Some(identity) = handler.py_identity() {
                    list.append(identity.bind(py))?;
                } else {
                    list.append(py.None().into_bound(py))?;
                }
            }
            dict.set_item("handlers", list)?;
        }
        Ok(dict.into_any())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::effect::make_get_execution_context_effect;
    use crate::ids::Marker;

    fn make_test_segment() -> (Segment, SegmentId) {
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = SegmentId::from_index(0);
        (seg, seg_id)
    }

    #[test]
    fn test_continuation_capture() {
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id, None);

        assert_eq!(cont.segment_id, seg_id);
        assert_eq!(cont.captured_caller(), seg.caller);
        assert!(cont.dispatch_id().is_none());
        assert_eq!(cont.segment().marker, seg.marker);
        assert!(cont.frames().is_empty());
        assert!(cont.is_started());
        assert!(cont.program.is_none());
        assert!(cont.handlers.is_empty());
        assert!(cont.handler_identities.is_empty());
        assert!(cont.parent.is_none());
    }

    #[test]
    fn test_continuation_unique_ids() {
        let (seg, seg_id) = make_test_segment();
        let c1 = Continuation::capture(&seg, seg_id, None);
        let c2 = Continuation::capture(&seg, seg_id, None);
        assert_ne!(c1.cont_id, c2.cont_id);
    }

    #[test]
    fn test_continuation_snapshot_is_independent() {
        let marker = Marker::fresh();
        let mut seg = Segment::new(marker, None);
        let seg_id = SegmentId::from_index(0);

        seg.push_frame(Frame::FlatMapBindResult);

        let cont = Continuation::capture(&seg, seg_id, None);
        assert_eq!(cont.frames().len(), 1);

        seg.push_frame(Frame::FlatMapBindResult);
        assert_eq!(cont.frames().len(), 1);
        assert_eq!(seg.frame_count(), 2);
    }

    fn make_dispatch_origin_frame(dispatch_id: DispatchId) -> Frame {
        let origin_seg = Segment::new(Marker::fresh(), None);
        let k_origin =
            Continuation::capture(&origin_seg, SegmentId::from_index(99), Some(dispatch_id));
        Frame::DispatchOrigin {
            dispatch_id,
            effect: make_get_execution_context_effect()
                .expect("test dispatch effect should be constructible"),
            k_origin,
        }
    }

    #[test]
    fn test_continuation_capture_filters_orphan_dispatch_origin_frames() {
        let (mut seg, seg_id) = make_test_segment();
        seg.push_frame(make_dispatch_origin_frame(DispatchId::fresh()));

        let cont = Continuation::capture(&seg, seg_id, None);

        assert!(cont.frames().is_empty());
    }

    #[test]
    fn test_continuation_capture_keeps_dispatch_origin_with_handler_dispatch() {
        let (mut seg, seg_id) = make_test_segment();
        let dispatch_id = DispatchId::fresh();
        let handler_seg = Segment::new(Marker::fresh(), None);
        let handler_cont =
            Continuation::capture(&handler_seg, SegmentId::from_index(7), Some(dispatch_id));
        seg.push_frame(Frame::HandlerDispatch {
            dispatch_id,
            continuation: handler_cont,
            prompt_seg_id: SegmentId::from_index(8),
        });
        seg.push_frame(make_dispatch_origin_frame(dispatch_id));

        let cont = Continuation::capture(&seg, seg_id, None);

        assert_eq!(cont.frames().len(), 2);
        assert!(matches!(
            cont.frames()[1],
            Frame::DispatchOrigin { dispatch_id: kept_id, .. } if kept_id == dispatch_id
        ));
    }
}
