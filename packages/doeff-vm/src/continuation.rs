//! Continuation types for capturing and resuming.

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::frame::CallMetadata;
use crate::frame::Frame;
use crate::handler::Handler;
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::segment::Segment;

/// Capturable continuation with frozen frame snapshot.
///
/// Contains Arc snapshots of frames and scope_chain at capture time.
/// Resume materializes this snapshot into a new execution segment.
///
/// Continuations can be in two states:
/// - **started=true** (captured): Created from a running segment via `capture()`
/// - **started=false** (unstarted): Created via `create()` with a program and handlers
///
/// When resuming:
/// - Captured continuations: materialize frames_snapshot into a new segment
/// - Unstarted continuations: start the program with handlers installed
#[derive(Debug, Clone)]
pub struct Continuation {
    pub cont_id: ContId,
    pub segment_id: SegmentId,
    pub frames_snapshot: Arc<Vec<Frame>>,
    pub scope_chain: Arc<Vec<Marker>>,
    pub marker: Marker,
    pub dispatch_id: Option<DispatchId>,

    /// Whether this continuation is already started.
    /// started=true  => captured continuation (from running code)
    /// started=false => created (unstarted) continuation
    pub started: bool,

    pub program: Option<PyShared>,

    /// Handlers to install when started=false (innermost first).
    /// Empty for captured (started=true) continuations.
    pub handlers: Vec<Handler>,

    /// Optional Python identities corresponding to handlers by index.
    /// Used to preserve Rust sentinel identity across continuation round-trips.
    pub handler_identities: Vec<Option<PyShared>>,

    /// Optional call metadata to attach when starting unstarted continuations.
    pub metadata: Option<CallMetadata>,
}

impl Continuation {
    pub fn capture(
        segment: &Segment,
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id,
            frames_snapshot: Arc::new(segment.frames.clone()),
            scope_chain: Arc::new(segment.scope_chain.clone()),
            marker: segment.marker,
            dispatch_id,
            started: true,
            program: None,
            handlers: Vec::new(),
            handler_identities: Vec::new(),
            metadata: None,
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
            frames_snapshot: Arc::new(segment.frames.clone()),
            scope_chain: Arc::new(segment.scope_chain.clone()),
            marker: segment.marker,
            dispatch_id,
            started: true,
            program: None,
            handlers: Vec::new(),
            handler_identities: Vec::new(),
            metadata: None,
        }
    }

    pub fn create_unstarted(expr: PyShared, handlers: Vec<Handler>) -> Self {
        Self::create_unstarted_with_metadata(expr, handlers, None)
    }

    pub fn create_unstarted_with_metadata(
        expr: PyShared,
        handlers: Vec<Handler>,
        metadata: Option<CallMetadata>,
    ) -> Self {
        let handler_identities = vec![None; handlers.len()];
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId::from_index(0),
            frames_snapshot: Arc::new(Vec::new()),
            scope_chain: Arc::new(Vec::new()),
            marker: Marker::placeholder(),
            dispatch_id: None,
            started: false,
            program: Some(expr),
            handlers,
            handler_identities,
            metadata,
        }
    }

    pub fn create_unstarted_with_identities(
        expr: PyShared,
        handlers: Vec<Handler>,
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
        handlers: Vec<Handler>,
        handler_identities: Vec<Option<PyShared>>,
        metadata: Option<CallMetadata>,
    ) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId::from_index(0),
            frames_snapshot: Arc::new(Vec::new()),
            scope_chain: Arc::new(Vec::new()),
            marker: Marker::placeholder(),
            dispatch_id: None,
            started: false,
            program: Some(expr),
            handlers,
            handler_identities,
            metadata,
        }
    }

    pub fn is_started(&self) -> bool {
        self.started
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
                match handler {
                    Handler::Python(py_handler) => {
                        list.append(py_handler.bind(py))?;
                    }
                    Handler::RustProgram(_) => {
                        list.append(py.None().into_bound(py))?;
                    }
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

    fn make_test_segment() -> (Segment, SegmentId) {
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = SegmentId::from_index(0);
        (seg, seg_id)
    }

    #[test]
    fn test_continuation_capture() {
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id, None);

        assert_eq!(cont.segment_id, seg_id);
        assert!(cont.dispatch_id.is_none());
        assert_eq!(cont.marker, seg.marker);
        assert!(cont.frames_snapshot.is_empty());
        assert_eq!(cont.scope_chain.len(), 1);
        assert!(cont.is_started());
        assert!(cont.program.is_none());
        assert!(cont.handlers.is_empty());
        assert!(cont.handler_identities.is_empty());
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
        let mut seg = Segment::new(marker, None, vec![marker]);
        let seg_id = SegmentId::from_index(0);

        use crate::ids::CallbackId;
        seg.push_frame(Frame::rust_return(CallbackId::fresh()));

        let cont = Continuation::capture(&seg, seg_id, None);
        assert_eq!(cont.frames_snapshot.len(), 1);

        seg.push_frame(Frame::rust_return(CallbackId::fresh()));
        assert_eq!(cont.frames_snapshot.len(), 1);
        assert_eq!(seg.frame_count(), 2);
    }
}
