//! Continuation types for detaching and reattaching fibers.

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

// OwnedControlContinuation is intentionally NOT Clone — Continuation must flow by move (SPEC-VM-021).

impl OwnedControlContinuation {
    /// Returns the identity of this control continuation.
    /// For started continuations, this is fibers[0].
    /// For pending continuations, this is the ContId (no fibers yet).
    pub fn identity(&self) -> Option<FiberId> {
        match self {
            Self::Started(continuation) => continuation.identity(),
            Self::Pending(_) => None,
        }
    }

    /// Returns a ContId for scheduler/tracking use.
    /// For started continuations: derived from fibers[0].
    /// For pending continuations: the stored ContId.
    pub fn cont_id(&self) -> ContId {
        match self {
            Self::Started(continuation) => ContId::from_raw(
                continuation.identity().map(|f| f.index() as u64).unwrap_or(0),
            ),
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
        let continuation = Continuation::placeholder();
        Self {
            continuation,
            pending: Some(pending),
        }
    }

    pub fn continuation_ref(&self) -> Option<&Continuation> {
        self.pending.is_none().then_some(&self.continuation)
    }

    pub fn pending(&self) -> Option<PendingContinuation> {
        self.pending.clone()
    }

    /// Returns a display-friendly identity for this PyK.
    /// Uses FiberId index for started continuations, ContId raw for pending.
    pub fn display_id(&self) -> u64 {
        if let Some(pending) = &self.pending {
            return pending.cont_id.raw();
        }
        self.continuation
            .identity()
            .map(|fid| fid.index() as u64)
            .unwrap_or(0)
    }

    pub fn is_exhausted(&self) -> bool {
        self.pending.is_none()
            && (self.continuation.is_placeholder() || self.continuation.consumed())
    }

    pub fn take_control_continuation(&mut self) -> OwnedControlContinuation {
        if let Some(pending) = self.pending.take() {
            return OwnedControlContinuation::Pending(pending);
        }
        let mut placeholder = Continuation::placeholder();
        placeholder.mark_consumed();
        OwnedControlContinuation::Started(std::mem::replace(&mut self.continuation, placeholder))
    }

    pub fn take_continuation(&mut self) -> Continuation {
        match self.take_control_continuation() {
            OwnedControlContinuation::Started(continuation) => continuation,
            OwnedControlContinuation::Pending(_pending) => {
                let mut placeholder = Continuation::placeholder();
                placeholder.mark_consumed();
                placeholder
            }
        }
    }
}

#[pymethods]
impl PyK {
    fn __repr__(&self) -> String {
        format!("K({})", self.display_id())
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
    fibers: Option<Vec<FiberId>>,
}

// Continuation is intentionally NOT Clone — one-shot semantics enforced by Option::take (SPEC-VM-021).

impl Continuation {
    pub fn placeholder() -> Self {
        memory_stats::register_continuation();
        Self {
            fibers: Some(Vec::new()),
        }
    }

    fn new_captured(fibers: Vec<FiberId>) -> Self {
        memory_stats::register_continuation();
        Self {
            fibers: Some(fibers),
        }
    }

    pub fn from_fiber(fiber_id: FiberId, _captured_caller: Option<SegmentId>) -> Self {
        Self::new_captured(vec![fiber_id])
    }

    pub fn capture(_segment: &Segment, segment_id: SegmentId) -> Self {
        Self::new_captured(vec![segment_id])
    }

    /// Returns the first FiberId as a natural unique identity for this continuation.
    /// Used as dispatch identity (replaces ContId per SPEC-VM-021 Step 6).
    pub fn identity(&self) -> Option<FiberId> {
        self.fibers.as_ref().and_then(|f| f.first().copied())
    }

    pub fn is_started(&self) -> bool {
        self.fibers.as_ref().is_some_and(|f| !f.is_empty())
    }

    pub fn is_placeholder(&self) -> bool {
        matches!(&self.fibers, Some(f) if f.is_empty())
    }

    pub fn segment_id(&self) -> Option<SegmentId> {
        self.fibers.as_ref().and_then(|f| f.first().copied())
    }

    pub fn fibers(&self) -> &[FiberId] {
        self.fibers.as_deref().unwrap_or(&[])
    }

    pub(crate) fn outermost_fiber_id(&self) -> Option<FiberId> {
        self.fibers.as_ref().and_then(|f| f.last().copied())
    }

    pub(crate) fn same_owned_fibers(&self, other: &Continuation) -> bool {
        self.fibers() == other.fibers()
    }

    pub(crate) fn append_owned_fibers(&mut self, mut other: Continuation) {
        if let (Some(ref mut self_fibers), Some(ref mut other_fibers)) =
            (&mut self.fibers, &mut other.fibers)
        {
            if !other_fibers.is_empty() {
                self_fibers.append(other_fibers);
            }
        }
    }

    pub(crate) fn retain_owned_fibers(&mut self, mut keep: impl FnMut(FiberId) -> bool) {
        if let Some(ref mut fibers) = self.fibers {
            fibers.retain(|fiber_id| keep(*fiber_id));
        }
    }

    pub(crate) fn tail_owned_fibers(&self) -> Option<Self> {
        self.fibers
            .as_ref()
            .filter(|f| f.len() > 1)
            .map(|f| Self::new_captured(f[1..].to_vec()))
    }

    /// Returns a ContId derived from fibers[0] for scheduler/tracking compatibility.
    /// Continuation no longer stores ContId; this derives one from the natural fiber identity.
    pub fn derived_cont_id(&self) -> ContId {
        ContId::from_raw(self.identity().map(|f| f.index() as u64).unwrap_or(0))
    }

    pub fn consumed(&self) -> bool {
        self.fibers.is_none()
    }

    pub(crate) fn mark_consumed(&mut self) {
        self.fibers.take();
    }

    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let dict = PyDict::new(py);
        let id = self.identity().map(|f| f.index() as u64).unwrap_or(0);
        dict.set_item("identity", id)?;
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
    fn test_from_fiber_creates_independent_continuation() {
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id);
        let independent = Continuation::from_fiber(cont.fibers()[0], None);

        // Independent continuation has same fiber identity but separate ownership
        assert_eq!(independent.identity(), cont.identity());
        assert_eq!(independent.fibers(), &[seg_id]);
        assert!(!independent.consumed());
    }
}
