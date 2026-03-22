//! Continuation types for detaching and reattaching fibers.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::OnceLock;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::frame::CallMetadata;
use crate::ids::{ContId, DispatchId, FiberId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::memory_stats;
use crate::py_shared::PyShared;
use crate::segment::Segment;

#[pyclass(name = "K")]
pub struct PyK {
    continuation: Continuation,
}

impl PyK {
    pub fn from_continuation(continuation: &Continuation) -> Self {
        Self {
            continuation: continuation.clone_handle(),
        }
    }

    pub fn continuation(&self) -> Continuation {
        self.continuation.clone_handle()
    }
}

#[pymethods]
impl PyK {
    fn __repr__(&self) -> String {
        format!("K({})", self.continuation.cont_id.raw())
    }
}

#[derive(Debug, Clone)]
struct UnstartedContinuation {
    program: PyShared,
    handlers: Vec<KleisliRef>,
    handler_identities: Vec<Option<PyShared>>,
    metadata: Option<CallMetadata>,
    outside_scope: Option<SegmentId>,
    start_return_to: Option<Box<Continuation>>,
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct DispatchHandlerHint {
    pub(crate) marker: Marker,
    pub(crate) prompt_seg_id: SegmentId,
}

#[derive(Debug, Clone, Copy)]
struct ContinuationMetadata {
    resume_dispatch_id: Option<DispatchId>,
    dispatch_handler_hint: Option<DispatchHandlerHint>,
    captured_caller: Option<SegmentId>,
}

#[derive(Debug)]
pub struct Continuation {
    pub cont_id: ContId,
    dispatch_id: Option<DispatchId>,
    fibers: Vec<FiberId>,
    owns_fibers: bool,
    consumed: bool,
    consumed_state: Arc<AtomicBool>,
    metadata_state: Arc<Mutex<ContinuationMetadata>>,
    unstarted: Option<UnstartedContinuation>,
}

pub(crate) fn panic_on_started_continuation_clone_enabled() -> bool {
    static ENABLED: OnceLock<bool> = OnceLock::new();

    *ENABLED.get_or_init(|| std::env::var_os("DOEFF_PANIC_ON_STARTED_CONT_CLONE").is_some())
}

impl Clone for Continuation {
    #[track_caller]
    fn clone(&self) -> Self {
        if self.is_started() && self.owns_fibers && panic_on_started_continuation_clone_enabled() {
            panic!(
                "started continuation clone detected for cont_id {} at {}\n{}",
                self.cont_id.raw(),
                std::panic::Location::caller(),
                std::backtrace::Backtrace::force_capture()
            );
        }

        let metadata = self.shared_metadata();
        memory_stats::register_continuation();
        let mut continuation = Continuation {
            cont_id: self.cont_id,
            dispatch_id: self.dispatch_id,
            fibers: self.fibers.clone(),
            owns_fibers: self.owns_fibers,
            consumed: self.consumed(),
            consumed_state: Arc::clone(&self.consumed_state),
            metadata_state: Self::new_metadata_state(metadata.captured_caller),
            unstarted: self.unstarted.clone(),
        };
        continuation.set_resume_dispatch_id(metadata.resume_dispatch_id);
        continuation.set_dispatch_handler_hint(metadata.dispatch_handler_hint);
        continuation
    }
}

impl Continuation {
    fn new_consumed_state(consumed: bool) -> Arc<AtomicBool> {
        Arc::new(AtomicBool::new(consumed))
    }

    fn new_metadata_state(captured_caller: Option<SegmentId>) -> Arc<Mutex<ContinuationMetadata>> {
        Arc::new(Mutex::new(ContinuationMetadata {
            resume_dispatch_id: None,
            dispatch_handler_hint: None,
            captured_caller,
        }))
    }

    fn shared_metadata(&self) -> ContinuationMetadata {
        *self
            .metadata_state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    pub fn placeholder(cont_id: ContId) -> Self {
        memory_stats::register_continuation();
        Continuation {
            cont_id,
            dispatch_id: None,
            fibers: Vec::new(),
            owns_fibers: false,
            consumed: false,
            consumed_state: Self::new_consumed_state(false),
            metadata_state: Self::new_metadata_state(None),
            unstarted: None,
        }
    }

    fn new_captured(
        cont_id: ContId,
        fibers: Vec<FiberId>,
        captured_caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        memory_stats::register_continuation();
        Continuation {
            cont_id,
            dispatch_id,
            fibers,
            owns_fibers: true,
            consumed: false,
            consumed_state: Self::new_consumed_state(false),
            metadata_state: Self::new_metadata_state(captured_caller),
            unstarted: None,
        }
    }

    pub(crate) fn from_fiber(
        fiber_id: FiberId,
        captured_caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Self::new_captured(
            ContId::fresh(),
            vec![fiber_id],
            captured_caller,
            dispatch_id,
        )
    }

    pub fn capture(
        segment: &Segment,
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Self::new_captured(
            ContId::fresh(),
            vec![segment_id],
            segment.parent,
            dispatch_id,
        )
    }

    pub fn with_id(
        cont_id: ContId,
        fiber_id: FiberId,
        captured_caller: Option<SegmentId>,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Self::new_captured(cont_id, vec![fiber_id], captured_caller, dispatch_id)
    }

    pub fn create_unstarted(expr: PyShared, handlers: Vec<KleisliRef>) -> Self {
        Self::create_unstarted_with_metadata(expr, handlers, None, None)
    }

    pub fn create_unstarted_with_metadata(
        expr: PyShared,
        handlers: Vec<KleisliRef>,
        metadata: Option<CallMetadata>,
        outside_scope: Option<SegmentId>,
    ) -> Self {
        let handler_count = handlers.len();
        memory_stats::register_continuation();
        Continuation {
            cont_id: ContId::fresh(),
            dispatch_id: None,
            fibers: Vec::new(),
            owns_fibers: true,
            consumed: false,
            consumed_state: Self::new_consumed_state(false),
            metadata_state: Self::new_metadata_state(None),
            unstarted: Some(UnstartedContinuation {
                program: expr,
                handlers,
                handler_identities: vec![None; handler_count],
                metadata,
                outside_scope,
                start_return_to: None,
            }),
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
            None,
        )
    }

    pub fn create_unstarted_with_identities_and_metadata(
        expr: PyShared,
        handlers: Vec<KleisliRef>,
        handler_identities: Vec<Option<PyShared>>,
        metadata: Option<CallMetadata>,
        outside_scope: Option<SegmentId>,
    ) -> Self {
        memory_stats::register_continuation();
        Continuation {
            cont_id: ContId::fresh(),
            dispatch_id: None,
            fibers: Vec::new(),
            owns_fibers: true,
            consumed: false,
            consumed_state: Self::new_consumed_state(false),
            metadata_state: Self::new_metadata_state(None),
            unstarted: Some(UnstartedContinuation {
                program: expr,
                handlers,
                handler_identities,
                metadata,
                outside_scope,
                start_return_to: None,
            }),
        }
    }

    pub fn is_started(&self) -> bool {
        !self.fibers.is_empty()
    }

    pub fn is_placeholder(&self) -> bool {
        self.fibers.is_empty() && self.unstarted.is_none()
    }

    pub fn owns_fibers(&self) -> bool {
        self.owns_fibers
    }

    pub fn clone_handle(&self) -> Self {
        memory_stats::register_continuation();
        Continuation {
            cont_id: self.cont_id,
            dispatch_id: self.dispatch_id,
            fibers: self.fibers.clone(),
            owns_fibers: self.owns_fibers,
            consumed: self.consumed(),
            consumed_state: Arc::clone(&self.consumed_state),
            metadata_state: Arc::clone(&self.metadata_state),
            unstarted: self.unstarted.clone(),
        }
    }

    pub(crate) fn into_owned(mut self) -> Self {
        self.owns_fibers = true;
        self
    }

    pub fn segment_id(&self) -> Option<SegmentId> {
        self.fibers.first().copied()
    }

    pub(crate) fn fibers(&self) -> &[FiberId] {
        &self.fibers
    }

    pub(crate) fn outermost_fiber_id(&self) -> Option<FiberId> {
        self.fibers.last().copied()
    }

    pub(crate) fn same_owned_fibers(&self, other: &Continuation) -> bool {
        self.fibers == other.fibers && self.captured_caller() == other.captured_caller()
    }

    pub(crate) fn append_owned_fibers(&mut self, mut other: Continuation) {
        debug_assert!(
            self.unstarted.is_none(),
            "cannot append fibers to unstarted continuation"
        );
        debug_assert!(
            self.owns_fibers,
            "cannot append fibers to non-owning continuation handle"
        );
        debug_assert!(
            other.unstarted.is_none(),
            "cannot append unstarted continuation fibers"
        );
        debug_assert!(
            other.owns_fibers,
            "cannot append non-owning continuation handle fibers"
        );
        if !other.fibers.is_empty() {
            self.fibers.append(&mut other.fibers);
        }
        self.set_captured_caller(other.captured_caller().or(self.captured_caller()));
    }

    pub(crate) fn tail_owned_fibers(&self) -> Option<Self> {
        if self.fibers.len() <= 1 {
            return None;
        }
        let mut tail = Self::new_captured(
            ContId::fresh(),
            self.fibers[1..].to_vec(),
            self.captured_caller(),
            self.dispatch_id,
        );
        let metadata = self.shared_metadata();
        tail.set_resume_dispatch_id(metadata.resume_dispatch_id);
        tail.set_dispatch_handler_hint(metadata.dispatch_handler_hint);
        Some(tail)
    }

    pub fn dispatch_id(&self) -> Option<DispatchId> {
        self.dispatch_id
    }

    pub(crate) fn resume_dispatch_id(&self) -> Option<DispatchId> {
        self.shared_metadata().resume_dispatch_id
    }

    pub(crate) fn set_resume_dispatch_id(&mut self, dispatch_id: Option<DispatchId>) {
        self.metadata_state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .resume_dispatch_id = dispatch_id;
    }

    pub(crate) fn dispatch_handler_hint(&self) -> Option<DispatchHandlerHint> {
        self.shared_metadata().dispatch_handler_hint
    }

    pub(crate) fn set_dispatch_handler_hint(&mut self, hint: Option<DispatchHandlerHint>) {
        self.metadata_state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .dispatch_handler_hint = hint;
    }

    pub fn captured_caller(&self) -> Option<SegmentId> {
        self.shared_metadata().captured_caller
    }

    pub fn set_captured_caller_hint(&mut self, captured_caller: Option<SegmentId>) {
        self.set_captured_caller(captured_caller);
    }

    pub fn set_unstarted_start_return_to_hint(&mut self, continuation: Option<Continuation>) {
        if let Some(unstarted) = self.unstarted.as_mut() {
            unstarted.start_return_to = continuation.map(|continuation| {
                Box::new(continuation.clone_handle())
            });
        }
    }

    pub(crate) fn set_captured_caller(&mut self, captured_caller: Option<SegmentId>) {
        self.metadata_state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .captured_caller = captured_caller;
    }

    pub fn consumed(&self) -> bool {
        self.consumed || self.consumed_state.load(Ordering::Relaxed)
    }

    pub(crate) fn mark_consumed(&mut self) {
        self.consumed = true;
        self.consumed_state.store(true, Ordering::Relaxed);
    }

    pub fn program(&self) -> Option<&PyShared> {
        self.unstarted.as_ref().map(|unstarted| &unstarted.program)
    }

    pub fn handlers(&self) -> Option<&[KleisliRef]> {
        self.unstarted
            .as_ref()
            .map(|unstarted| unstarted.handlers.as_slice())
    }

    pub fn prepend_unstarted_handlers(&mut self, mut handlers: Vec<KleisliRef>) {
        let Some(unstarted) = self.unstarted.as_mut() else {
            return;
        };
        if handlers.is_empty() {
            return;
        }
        let extra_count = handlers.len();
        handlers.extend(unstarted.handlers.iter().cloned());
        unstarted.handlers = handlers;

        let mut handler_identities = vec![None; extra_count];
        handler_identities.extend(unstarted.handler_identities.iter().cloned());
        unstarted.handler_identities = handler_identities;
    }

    pub fn handler_identities(&self) -> Option<&[Option<PyShared>]> {
        self.unstarted
            .as_ref()
            .map(|unstarted| unstarted.handler_identities.as_slice())
    }

    pub fn metadata(&self) -> Option<&CallMetadata> {
        self.unstarted
            .as_ref()
            .and_then(|unstarted| unstarted.metadata.as_ref())
    }

    pub fn outside_scope(&self) -> Option<SegmentId> {
        self.unstarted
            .as_ref()
            .and_then(|unstarted| unstarted.outside_scope)
    }

    pub(crate) fn clone_for_dispatch(&self, dispatch_id: Option<DispatchId>) -> Self {
        // Dispatch forwarding sometimes needs a fresh one-shot token for the
        // same detached fiber chain while the original continuation survives
        // under its existing `cont_id`. This is safe because the fork is only
        // used for dispatch bookkeeping; user-visible one-shot consumption is
        // still keyed by the fresh `cont_id` on the forwarded branch.
        let metadata = self.shared_metadata();
        memory_stats::register_continuation();
        let mut continuation = Continuation {
            cont_id: ContId::fresh(),
            dispatch_id,
            fibers: self.fibers.clone(),
            owns_fibers: true,
            consumed: self.consumed(),
            consumed_state: Self::new_consumed_state(self.consumed()),
            metadata_state: Self::new_metadata_state(metadata.captured_caller),
            unstarted: self.unstarted.clone(),
        };
        continuation.set_resume_dispatch_id(metadata.resume_dispatch_id);
        continuation.set_dispatch_handler_hint(metadata.dispatch_handler_hint);
        continuation
    }

    pub(crate) fn into_unstarted_parts(
        self,
    ) -> Option<(
        PyShared,
        Vec<KleisliRef>,
        Vec<Option<PyShared>>,
        Option<CallMetadata>,
        Option<SegmentId>,
        Option<Continuation>,
    )> {
        self.unstarted.clone().map(
            |UnstartedContinuation {
                 program,
                 handlers,
                 handler_identities,
                 metadata,
                 outside_scope,
                 start_return_to,
             }| {
                (
                    program,
                    handlers,
                    handler_identities,
                    metadata,
                    outside_scope,
                    start_return_to.map(|continuation| continuation.as_ref().clone_handle()),
                )
            },
        )
    }

    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("cont_id", self.cont_id.raw())?;
        dict.set_item("started", self.is_started())?;
        if let Some(program) = self.program() {
            dict.set_item("program", program.bind(py))?;
        }
        if let Some(handlers) = self.handlers() {
            let list = PyList::empty(py);
            for (idx, handler) in handlers.iter().enumerate() {
                if let Some(Some(identity)) = self.handler_identities().and_then(|ids| ids.get(idx))
                {
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

impl Drop for Continuation {
    fn drop(&mut self) {
        memory_stats::unregister_continuation();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    use crate::do_ctrl::DoCtrl;
    use crate::error::VMError;
    use crate::ids::{Marker, SegmentId};
    use crate::kleisli::{Kleisli, KleisliDebugInfo};
    use crate::memory_stats::live_object_counts;

    #[derive(Debug)]
    struct DummyKleisli;

    impl Kleisli for DummyKleisli {
        fn apply(
            &self,
            _py: Python<'_>,
            _args: Vec<crate::value::Value>,
        ) -> Result<DoCtrl, VMError> {
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

    fn make_test_segment() -> (Segment, SegmentId) {
        let seg = Segment::new(Marker::fresh(), None);
        let seg_id = SegmentId::from_index(0);
        (seg, seg_id)
    }

    #[test]
    fn test_continuation_capture_records_existing_fiber_id() {
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id, None);

        assert_eq!(cont.segment_id(), Some(seg_id));
        assert_eq!(cont.fibers(), &[seg_id]);
        assert_eq!(cont.captured_caller(), seg.parent);
        assert!(cont.is_started());
        assert!(cont.owns_fibers());
        assert!(!cont.consumed());
    }

    #[test]
    fn test_continuation_unique_ids() {
        let (seg, seg_id) = make_test_segment();
        let c1 = Continuation::capture(&seg, seg_id, None);
        let c2 = Continuation::capture(&seg, seg_id, None);
        assert_ne!(c1.cont_id, c2.cont_id);
    }

    #[test]
    fn test_unstarted_continuation_has_no_captured_fibers() {
        Python::attach(|py| {
            let baseline = live_object_counts().live_continuations;
            let cont = Continuation::create_unstarted(PyShared::new(py.None()), Vec::new());
            assert!(!cont.is_started());
            assert!(cont.segment_id().is_none());
            assert!(cont.fibers().is_empty());
            assert!(cont.program().is_some());
            assert_eq!(live_object_counts().live_continuations, baseline + 1);
            drop(cont);
            assert_eq!(live_object_counts().live_continuations, baseline);
        });
    }

    #[test]
    fn test_clone_for_dispatch_keeps_same_fiber_id_but_fresh_cont_id() {
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id, None);
        let cloned = cont.clone_for_dispatch(Some(DispatchId::fresh()));

        assert_eq!(cloned.segment_id(), Some(seg_id));
        assert_eq!(cloned.fibers(), &[seg_id]);
        assert_ne!(cloned.cont_id, cont.cont_id);
        assert!(cloned.dispatch_id().is_some());
        assert!(cloned.owns_fibers());
    }

    #[test]
    fn test_clone_handle_keeps_fiber_ids_without_ownership() {
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id, None);
        let handle = cont.clone_handle();

        assert_eq!(handle.cont_id, cont.cont_id);
        assert_eq!(handle.fibers(), &[seg_id]);
        assert!(!handle.owns_fibers());
    }

    #[test]
    fn test_same_owned_fibers_compares_entire_fiber_chain() {
        let cont = Continuation::with_id(
            ContId::fresh(),
            SegmentId::from_index(0),
            Some(SegmentId::from_index(2)),
            None,
        );
        let mut extended = Continuation::with_id(
            ContId::fresh(),
            SegmentId::from_index(0),
            Some(SegmentId::from_index(2)),
            None,
        );
        extended.append_owned_fibers(Continuation::with_id(
            ContId::fresh(),
            SegmentId::from_index(1),
            Some(SegmentId::from_index(2)),
            None,
        ));

        assert!(!cont.same_owned_fibers(&extended));
        assert!(!extended.same_owned_fibers(&cont));
    }

    #[test]
    fn test_create_unstarted_with_identities_keeps_handler_metadata() {
        Python::attach(|py| {
            let program = PyShared::new(py.None());
            let handlers = vec![Arc::new(DummyKleisli) as KleisliRef];
            let identities = vec![Some(PyShared::new(py.None()))];
            let cont =
                Continuation::create_unstarted_with_identities(program, handlers, identities);

            assert!(cont.program().is_some());
            assert_eq!(cont.handlers().map(|handlers| handlers.len()), Some(1));
            assert_eq!(
                cont.handler_identities().map(|identities| identities.len()),
                Some(1)
            );
        });
    }

    #[test]
    fn test_continuation_live_count_tracks_clone_lifetime() {
        let baseline = live_object_counts().live_continuations;
        let (seg, seg_id) = make_test_segment();
        let cont = Continuation::capture(&seg, seg_id, None);
        assert_eq!(live_object_counts().live_continuations, baseline + 1);

        let cont_clone = cont.clone();
        assert_eq!(live_object_counts().live_continuations, baseline + 2);

        drop(cont_clone);
        assert_eq!(live_object_counts().live_continuations, baseline + 1);

        drop(cont);
        assert_eq!(live_object_counts().live_continuations, baseline);
    }
}
