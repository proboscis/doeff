//! Continuation types for capturing and resuming.

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::frame::CallMetadata;
use crate::frame::Frame;
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::segment::{ScopeStore, Segment};
use crate::step::{Mode, PendingPython, PyException};
use crate::value::Value;

/// Capturable continuation with frozen frame snapshot.
///
/// Contains Arc snapshots of frames at capture time.
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
    pub scope_store: ScopeStore,
    pub frames_snapshot: Arc<Vec<Frame>>,
    pub marker: Marker,
    pub dispatch_id: Option<DispatchId>,
    pub mode: Box<Mode>,
    pub pending_python: Option<Box<PendingPython>>,
    pub pending_error_context: Option<PyException>,
    pub interceptor_eval_depth: usize,
    pub interceptor_skip_stack: Vec<Marker>,

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
    pub fn capture(
        segment: &Segment,
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id,
            scope_store: segment.scope_store.clone(),
            frames_snapshot: Arc::new(segment.frames.clone()),
            marker: segment.marker,
            dispatch_id,
            mode: Box::new(segment.mode.clone()),
            pending_python: segment.pending_python.clone().map(Box::new),
            pending_error_context: segment.pending_error_context.clone(),
            interceptor_eval_depth: segment.interceptor_eval_depth,
            interceptor_skip_stack: segment.interceptor_skip_stack.clone(),
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
            scope_store: segment.scope_store.clone(),
            frames_snapshot: Arc::new(segment.frames.clone()),
            marker: segment.marker,
            dispatch_id,
            mode: Box::new(segment.mode.clone()),
            pending_python: segment.pending_python.clone().map(Box::new),
            pending_error_context: segment.pending_error_context.clone(),
            interceptor_eval_depth: segment.interceptor_eval_depth,
            interceptor_skip_stack: segment.interceptor_skip_stack.clone(),
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
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId::from_index(0),
            scope_store: ScopeStore::default(),
            frames_snapshot: Arc::new(Vec::new()),
            marker: Marker::placeholder(),
            dispatch_id: None,
            mode: Box::new(Mode::Deliver(Value::Unit)),
            pending_python: None,
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
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
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId::from_index(0),
            scope_store: ScopeStore::default(),
            frames_snapshot: Arc::new(Vec::new()),
            marker: Marker::placeholder(),
            dispatch_id: None,
            mode: Box::new(Mode::Deliver(Value::Unit)),
            pending_python: None,
            pending_error_context: None,
            interceptor_eval_depth: 0,
            interceptor_skip_stack: Vec::new(),
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
                if handler.expects_python_effect() {
                    if let Some(identity) = handler.py_identity() {
                        list.append(identity.bind(py))?;
                    } else {
                        list.append(py.None().into_bound(py))?;
                    }
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
        assert!(cont.dispatch_id.is_none());
        assert_eq!(cont.marker, seg.marker);
        assert!(cont.frames_snapshot.is_empty());
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
        assert_eq!(cont.frames_snapshot.len(), 1);

        seg.push_frame(Frame::FlatMapBindResult);
        assert_eq!(cont.frames_snapshot.len(), 1);
        assert_eq!(seg.frame_count(), 2);
    }
}
