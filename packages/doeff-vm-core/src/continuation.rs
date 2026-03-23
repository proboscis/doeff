//! Continuation types for detaching and reattaching fibers.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::frame::CallMetadata;
use crate::ids::{ContId, FiberId, SegmentId};
use crate::kleisli::KleisliRef;
use crate::memory_stats;
use crate::py_shared::PyShared;
use crate::segment::Segment;

#[pyclass(name = "K")]
pub struct PyK {
    continuation: Continuation,
    pending: Option<PendingContinuation>,
}

#[derive(Debug)]
pub enum OwnedControlContinuation {
    Started(Continuation),
    Pending(PendingContinuation),
}

impl Clone for OwnedControlContinuation {
    fn clone(&self) -> Self {
        match self {
            Self::Started(continuation) => Self::Started(Continuation::capture_from_fiber_ids(continuation.fibers().to_vec())),
            Self::Pending(pending) => Self::Pending(pending.clone()),
        }
    }
}

impl OwnedControlContinuation {
    pub fn cont_id(&self) -> ContId {
        match self {
            Self::Started(continuation) => continuation.cont_id,
            Self::Pending(pending) => pending.cont_id,
        }
    }

    pub fn is_started(&self) -> bool {
        matches!(self, Self::Started(continuation) if continuation.is_started())
    }

    pub fn is_placeholder(&self) -> bool {
        matches!(self, Self::Started(continuation) if continuation.is_placeholder())
    }

    pub fn into_started(self) -> Option<Continuation> {
        match self {
            Self::Started(continuation) => Some(continuation),
            Self::Pending(_) => None,
        }
    }

    pub fn as_started(&self) -> Option<&Continuation> {
        match self {
            Self::Started(continuation) => Some(continuation),
            Self::Pending(_) => None,
        }
    }

    pub fn as_started_mut(&mut self) -> Option<&mut Continuation> {
        match self {
            Self::Started(continuation) => Some(continuation),
            Self::Pending(_) => None,
        }
    }

    pub fn handlers(&self) -> Option<&[KleisliRef]> {
        match self {
            Self::Started(_) => None,
            Self::Pending(pending) => Some(pending.handlers()),
        }
    }

    pub fn prepend_unstarted_handlers(&mut self, handlers: Vec<KleisliRef>) {
        if let Self::Pending(pending) = self {
            pending.prepend_handlers(handlers);
        }
    }
}

impl PyK {
    pub fn from_continuation(continuation: Continuation) -> Self {
        Self {
            continuation,
            pending: None,
        }
    }

    pub fn from_pending(pending: PendingContinuation) -> Self {
        let continuation = Continuation::placeholder(pending.cont_id);
        Self {
            continuation,
            pending: Some(pending),
        }
    }

    pub fn continuation(&self) -> Option<Continuation> {
        self.pending
            .is_none()
            .then(|| Continuation::capture_from_fiber_ids(self.continuation.fibers().to_vec()))
    }

    pub fn pending(&self) -> Option<PendingContinuation> {
        self.pending.clone()
    }

    pub fn cont_id(&self) -> ContId {
        self.pending
            .as_ref()
            .map(|pending| pending.cont_id)
            .unwrap_or(self.continuation.cont_id)
    }

    pub fn is_exhausted(&self) -> bool {
        self.pending.is_none()
            && (self.continuation.is_placeholder() || self.continuation.consumed())
    }

    pub fn take_control_continuation(&mut self) -> OwnedControlContinuation {
        if let Some(pending) = self.pending.take() {
            return OwnedControlContinuation::Pending(pending);
        }
        let mut placeholder = Continuation::placeholder(self.continuation.cont_id);
        placeholder.mark_consumed();
        OwnedControlContinuation::Started(std::mem::replace(&mut self.continuation, placeholder))
    }

    pub fn take_continuation(&mut self) -> Continuation {
        match self.take_control_continuation() {
            OwnedControlContinuation::Started(continuation) => continuation,
            OwnedControlContinuation::Pending(pending) => {
                let mut placeholder = Continuation::placeholder(pending.cont_id);
                placeholder.mark_consumed();
                placeholder
            }
        }
    }
}

#[pymethods]
impl PyK {
    fn __repr__(&self) -> String {
        format!("K({})", self.cont_id().raw())
    }
}

#[derive(Debug, Clone)]
pub struct PendingContinuation {
    pub cont_id: ContId,
    program: PyShared,
    handlers: Vec<KleisliRef>,
    handler_identities: Vec<Option<PyShared>>,
    metadata: Option<CallMetadata>,
    outside_scope: Option<SegmentId>,
}

impl PendingContinuation {
    pub fn create(expr: PyShared, handlers: Vec<KleisliRef>) -> Self {
        Self::create_with_metadata(expr, handlers, Vec::new(), None, None)
    }

    pub fn create_with_metadata(
        expr: PyShared,
        handlers: Vec<KleisliRef>,
        handler_identities: Vec<Option<PyShared>>,
        metadata: Option<CallMetadata>,
        outside_scope: Option<SegmentId>,
    ) -> Self {
        memory_stats::register_continuation();
        let normalized_identities = if handler_identities.is_empty() {
            vec![None; handlers.len()]
        } else {
            handler_identities
        };
        Self {
            cont_id: ContId::fresh(),
            program: expr,
            handlers,
            handler_identities: normalized_identities,
            metadata,
            outside_scope,
        }
    }

    pub fn program(&self) -> &PyShared {
        &self.program
    }

    pub fn handlers(&self) -> &[KleisliRef] {
        self.handlers.as_slice()
    }

    pub fn handler_identities(&self) -> &[Option<PyShared>] {
        self.handler_identities.as_slice()
    }

    pub fn metadata(&self) -> Option<&CallMetadata> {
        self.metadata.as_ref()
    }

    pub fn outside_scope(&self) -> Option<SegmentId> {
        self.outside_scope
    }

    pub fn into_parts(
        self,
    ) -> (
        PyShared,
        Vec<KleisliRef>,
        Vec<Option<PyShared>>,
        Option<CallMetadata>,
        Option<SegmentId>,
    ) {
        let this = std::mem::ManuallyDrop::new(self);
        // `PendingContinuation` only drops bookkeeping, so we can move fields out
        // and then release the accounting once here.
        let program = unsafe { std::ptr::read(&this.program) };
        let handlers = unsafe { std::ptr::read(&this.handlers) };
        let handler_identities = unsafe { std::ptr::read(&this.handler_identities) };
        let metadata = unsafe { std::ptr::read(&this.metadata) };
        let outside_scope = this.outside_scope;
        memory_stats::unregister_continuation();
        (
            program,
            handlers,
            handler_identities,
            metadata,
            outside_scope,
        )
    }

    pub fn prepend_handlers(&mut self, mut handlers: Vec<KleisliRef>) {
        if handlers.is_empty() {
            return;
        }
        let extra_count = handlers.len();
        handlers.extend(self.handlers.iter().cloned());
        self.handlers = handlers;

        let mut handler_identities = vec![None; extra_count];
        handler_identities.extend(self.handler_identities.iter().cloned());
        self.handler_identities = handler_identities;
    }

    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("cont_id", self.cont_id.raw())?;
        dict.set_item("started", false)?;
        dict.set_item("program", self.program.bind(py))?;
        let handlers = PyList::empty(py);
        for (idx, handler) in self.handlers.iter().enumerate() {
            if let Some(Some(identity)) = self.handler_identities.get(idx) {
                handlers.append(identity.bind(py))?;
                continue;
            }
            if let Some(identity) = handler.py_identity() {
                handlers.append(identity.bind(py))?;
            } else {
                handlers.append(py.None().into_bound(py))?;
            }
        }
        dict.set_item("handlers", handlers)?;
        Ok(dict.into_any())
    }
}

impl Drop for PendingContinuation {
    fn drop(&mut self) {
        memory_stats::unregister_continuation();
    }
}

#[derive(Debug)]
pub struct Continuation {
    pub cont_id: ContId,
    fibers: Vec<FiberId>,
    consumed: Arc<AtomicBool>,
}

impl Continuation {
    pub fn placeholder(cont_id: ContId) -> Self {
        memory_stats::register_continuation();
        Self {
            cont_id,
            fibers: Vec::new(),
            consumed: Arc::new(AtomicBool::new(false)),
        }
    }

    pub(crate) fn capture_from_fiber_ids(fiber_ids: Vec<FiberId>) -> Self {
        Self::new_captured(ContId::fresh(), fiber_ids)
    }

    fn new_captured(cont_id: ContId, fibers: Vec<FiberId>) -> Self {
        memory_stats::register_continuation();
        Self {
            cont_id,
            fibers,
            consumed: Arc::new(AtomicBool::new(false)),
        }
    }

    pub(crate) fn from_fiber(fiber_id: FiberId, _captured_caller: Option<SegmentId>) -> Self {
        Self::new_captured(ContId::fresh(), vec![fiber_id])
    }

    pub fn capture(_segment: &Segment, segment_id: SegmentId) -> Self {
        Self::new_captured(ContId::fresh(), vec![segment_id])
    }

    pub fn with_id(
        cont_id: ContId,
        fiber_id: FiberId,
        _captured_caller: Option<SegmentId>,
    ) -> Self {
        Self::new_captured(cont_id, vec![fiber_id])
    }

    pub fn is_started(&self) -> bool {
        !self.fibers.is_empty()
    }

    pub fn is_placeholder(&self) -> bool {
        self.fibers.is_empty()
    }

    pub fn segment_id(&self) -> Option<SegmentId> {
        self.fibers.first().copied()
    }

    pub(crate) fn fibers(&self) -> &[FiberId] {
        self.fibers.as_slice()
    }

    pub(crate) fn outermost_fiber_id(&self) -> Option<FiberId> {
        self.fibers.last().copied()
    }

    pub(crate) fn same_owned_fibers(&self, other: &Continuation) -> bool {
        self.fibers.as_slice() == other.fibers.as_slice()
    }

    pub(crate) fn append_owned_fibers(&mut self, mut other: Continuation) {
        if !other.fibers.is_empty() {
            self.fibers.append(&mut other.fibers);
        }
    }

    pub(crate) fn retain_owned_fibers(&mut self, mut keep: impl FnMut(FiberId) -> bool) {
        self.fibers.retain(|fiber_id| keep(*fiber_id));
    }

    pub(crate) fn tail_owned_fibers(&self) -> Option<Self> {
        (self.fibers.len() > 1)
            .then(|| Self::new_captured(ContId::fresh(), self.fibers[1..].to_vec()))
    }

    pub fn consumed(&self) -> bool {
        self.consumed.load(Ordering::Relaxed)
    }

    pub(crate) fn mark_consumed(&mut self) {
        self.consumed.store(true, Ordering::Relaxed);
    }

    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("cont_id", self.cont_id.raw())?;
        dict.set_item("started", self.is_started())?;
        Ok(dict.into_any())
    }
}

impl Drop for Continuation {
    fn drop(&mut self) {
        memory_stats::unregister_continuation();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::Marker;
    use crate::memory_stats::live_object_counts;

    fn make_test_segment() -> (Segment, SegmentId) {
        let seg = Segment::new(None);
        let seg_id = SegmentId::from_index(0);
        (seg, seg_id)
    }

    #[test]
    fn test_continuation_capture_records_existing_fiber_id() {
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id);

        assert_eq!(cont.segment_id(), Some(seg_id));
        assert_eq!(cont.fibers(), &[seg_id]);
        assert!(cont.is_started());
        assert!(!cont.consumed());
    }

    #[test]
    fn test_pending_continuation_has_no_captured_fibers() {
        Python::attach(|py| {
            let baseline = live_object_counts().live_continuations;
            let cont = PendingContinuation::create(PyShared::new(py.None()), Vec::new());
            assert_eq!(cont.handlers().len(), 0);
            assert_eq!(live_object_counts().live_continuations, baseline + 1);
            drop(cont);
            assert_eq!(live_object_counts().live_continuations, baseline);
        });
    }

    #[test]
    fn test_capture_from_fiber_ids_creates_independent_continuation() {
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id);
        let independent = Continuation::capture_from_fiber_ids(cont.fibers().to_vec());

        // Independent continuation has its own cont_id and consumed flag
        assert_ne!(independent.cont_id, cont.cont_id);
        assert_eq!(independent.fibers(), &[seg_id]);
        assert!(!independent.consumed());
    }
}
