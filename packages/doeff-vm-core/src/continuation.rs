//! Continuation types for capturing and resuming.

use std::collections::HashSet;
use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::frame::CallMetadata;
use crate::frame::Frame;
use crate::ids::{ContId, DispatchId, ScopeId, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::segment::Segment;
use crate::step::PyException;
use crate::value::Value;

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
/// - **captured**: Created from a running segment via `capture()`
/// - **unstarted**: Created via `create()` with a program and handlers
///
/// When resuming:
/// - Captured continuations: materialize segment_snapshot into a new segment
/// - Unstarted continuations: start the program with handlers installed
#[derive(Debug, Clone)]
struct UnstartedContinuation {
    program: PyShared,
    handlers: Vec<KleisliRef>,
    handler_identities: Vec<Option<PyShared>>,
    metadata: Option<CallMetadata>,
    outside_scope: Option<SegmentId>,
}

#[derive(Debug, Clone)]
pub struct Continuation {
    pub cont_id: ContId,
    segment_id: Option<SegmentId>,
    segment_snapshot: Option<Arc<Segment>>,
    unstarted: Option<UnstartedContinuation>,
    /// Parent continuation captured during Delegate chaining.
    parent: Option<Arc<Continuation>>,
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
        Arc::new(Segment {
            scope_id: segment.scope_id,
            persistent_epoch: segment.persistent_epoch,
            marker: segment.marker,
            frames: Self::captured_frames(segment),
            caller: segment.caller,
            scope_parent: segment.scope_parent,
            variables: Default::default(),
            named_bindings: segment.named_bindings.clone(),
            state_store: segment.state_store.clone(),
            writer_log: segment.writer_log.clone(),
            kind: segment.kind.clone(),
            dispatch_id,
            mode: segment.mode.clone(),
            pending_python: segment.pending_python.clone(),
            pending_error_context: segment.pending_error_context.clone(),
            throw_parent: segment.throw_parent.clone(),
            interceptor_eval_depth: segment.interceptor_eval_depth,
            interceptor_skip_stack: segment.interceptor_skip_stack.clone(),
        })
    }

    pub fn capture(
        segment: &Segment,
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: Some(segment_id),
            segment_snapshot: Some(Self::captured_segment_snapshot(segment, dispatch_id)),
            unstarted: None,
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
            segment_id: Some(segment_id),
            segment_snapshot: Some(Self::captured_segment_snapshot(segment, dispatch_id)),
            unstarted: None,
            parent: None,
        }
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
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: None,
            segment_snapshot: None,
            unstarted: Some(UnstartedContinuation {
                program: expr,
                handlers,
                handler_identities: vec![None; handler_count],
                metadata,
                outside_scope,
            }),
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
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: None,
            segment_snapshot: None,
            unstarted: Some(UnstartedContinuation {
                program: expr,
                handlers,
                handler_identities,
                metadata,
                outside_scope,
            }),
            parent: None,
        }
    }

    pub fn is_started(&self) -> bool {
        debug_assert_eq!(self.segment_id.is_some(), self.segment_snapshot.is_some());
        debug_assert_eq!(self.segment_snapshot.is_some(), self.unstarted.is_none());
        self.segment_snapshot.is_some()
    }

    pub fn segment_id(&self) -> Option<SegmentId> {
        self.segment_id
    }

    pub fn segment(&self) -> Option<&Segment> {
        self.segment_snapshot.as_deref()
    }

    /// Returns a mutable snapshot view.
    ///
    /// Uses `Arc::make_mut`, so mutating a shared snapshot triggers copy-on-write of the whole
    /// `Segment`.
    pub fn segment_mut(&mut self) -> Option<&mut Segment> {
        self.segment_snapshot.as_mut().map(Arc::make_mut)
    }

    pub fn frames(&self) -> Option<&[Frame]> {
        self.segment().map(|segment| segment.frames.as_slice())
    }

    pub fn dispatch_id(&self) -> Option<DispatchId> {
        self.segment().and_then(|segment| segment.dispatch_id)
    }

    // Plain Resume/Transfer restore the capture-time caller from the segment snapshot.
    // Dispatch Resume is the explicit override that re-enters the active handler segment.
    pub fn captured_caller(&self) -> Option<SegmentId> {
        self.segment().and_then(|segment| segment.caller)
    }

    pub fn pending_error_context(&self) -> Option<&PyException> {
        self.segment()
            .and_then(|segment| segment.pending_error_context.as_ref())
    }

    pub fn program(&self) -> Option<&PyShared> {
        self.unstarted.as_ref().map(|unstarted| &unstarted.program)
    }

    pub fn handlers(&self) -> Option<&[KleisliRef]> {
        self.unstarted
            .as_ref()
            .map(|unstarted| unstarted.handlers.as_slice())
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

    pub fn parent(&self) -> Option<&Continuation> {
        self.parent.as_deref()
    }

    pub(crate) fn set_parent(&mut self, parent: Option<Arc<Continuation>>) {
        self.parent = parent;
    }

    pub(crate) fn refresh_persistent_segment_state(
        &mut self,
        scope_state_store: &std::collections::HashMap<
            ScopeId,
            std::collections::HashMap<String, Value>,
        >,
        scope_writer_logs: &std::collections::HashMap<ScopeId, Vec<Value>>,
        scope_persistent_epochs: &std::collections::HashMap<ScopeId, u64>,
    ) {
        let mut visited = HashSet::new();
        self.refresh_persistent_segment_state_inner(
            scope_state_store,
            scope_writer_logs,
            scope_persistent_epochs,
            &mut visited,
        );
    }

    fn refresh_persistent_segment_state_inner(
        &mut self,
        scope_state_store: &std::collections::HashMap<
            ScopeId,
            std::collections::HashMap<String, Value>,
        >,
        scope_writer_logs: &std::collections::HashMap<ScopeId, Vec<Value>>,
        scope_persistent_epochs: &std::collections::HashMap<ScopeId, u64>,
        visited: &mut HashSet<ContId>,
    ) {
        if !visited.insert(self.cont_id) {
            return;
        }

        if let Some(snapshot) = self.segment_mut() {
            let current_epoch = scope_persistent_epochs
                .get(&snapshot.scope_id)
                .copied()
                .unwrap_or(snapshot.persistent_epoch);
            if snapshot.persistent_epoch < current_epoch {
                snapshot.state_store = scope_state_store
                    .get(&snapshot.scope_id)
                    .cloned()
                    .expect("scope state must exist when epoch is present");
                snapshot.writer_log = scope_writer_logs
                    .get(&snapshot.scope_id)
                    .cloned()
                    .expect("scope logs must exist when epoch is present");
                snapshot.persistent_epoch = current_epoch;
            }

            for frame in &mut snapshot.frames {
                match frame {
                    Frame::HandlerDispatch { continuation, .. } => {
                        continuation.refresh_persistent_segment_state_inner(
                            scope_state_store,
                            scope_writer_logs,
                            scope_persistent_epochs,
                            visited,
                        );
                    }
                    Frame::DispatchOrigin { k_origin, .. } => {
                        k_origin.refresh_persistent_segment_state_inner(
                            scope_state_store,
                            scope_writer_logs,
                            scope_persistent_epochs,
                            visited,
                        );
                    }
                    Frame::EvalReturn(eval_return) => {
                        match eval_return.as_mut() {
                            crate::frame::EvalReturnContinuation::ResumeToContinuation {
                                continuation,
                            }
                            | crate::frame::EvalReturnContinuation::EvalInScopeReturn {
                                continuation,
                            }
                            | crate::frame::EvalReturnContinuation::ReturnToContinuation {
                                continuation,
                            } => {
                                continuation.refresh_persistent_segment_state_inner(
                                    scope_state_store,
                                    scope_writer_logs,
                                    scope_persistent_epochs,
                                    visited,
                                );
                            }
                            _ => {}
                        }
                    }
                    Frame::Program { .. }
                    | Frame::InterceptorApply(_)
                    | Frame::InterceptorEval(_)
                    | Frame::MapReturn { .. }
                    | Frame::FlatMapBindResult
                    | Frame::FlatMapBindSource { .. }
                    | Frame::InterceptBodyReturn { .. } => {}
                }
            }
        }

        if let Some(parent) = self.parent.as_mut() {
            Arc::make_mut(parent).refresh_persistent_segment_state_inner(
                scope_state_store,
                scope_writer_logs,
                scope_persistent_epochs,
                visited,
            );
        }
    }

    pub(crate) fn into_unstarted_parts(
        self,
    ) -> Option<(
        PyShared,
        Vec<KleisliRef>,
        Vec<Option<PyShared>>,
        Option<CallMetadata>,
        Option<SegmentId>,
    )> {
        self.unstarted.map(
            |UnstartedContinuation {
                 program,
                 handlers,
                 handler_identities,
                 metadata,
                 outside_scope,
             }| {
                (
                    program,
                    handlers,
                    handler_identities,
                    metadata,
                    outside_scope,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::do_ctrl::{DoCtrl, InterceptMode};
    use crate::effect::make_get_execution_context_effect;
    use crate::error::VMError;
    use crate::ids::Marker;
    use crate::kleisli::{Kleisli, KleisliDebugInfo};
    use crate::segment::SegmentKind;
    use crate::value::Value;

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

        assert_eq!(cont.segment_id(), Some(seg_id));
        assert_eq!(cont.captured_caller(), seg.caller);
        assert!(cont.dispatch_id().is_none());
        assert_eq!(
            cont.segment().map(|segment| segment.marker),
            Some(seg.marker)
        );
        assert!(cont.frames().is_some_and(|frames| frames.is_empty()));
        assert!(cont.is_started());
        assert!(cont.program().is_none());
        assert!(cont.handlers().is_none());
        assert!(cont.handler_identities().is_none());
        assert!(cont.parent().is_none());
    }

    #[test]
    fn test_continuation_unique_ids() {
        let (seg, seg_id) = make_test_segment();
        let c1 = Continuation::capture(&seg, seg_id, None);
        let c2 = Continuation::capture(&seg, seg_id, None);
        assert_ne!(c1.cont_id, c2.cont_id);
    }

    #[test]
    fn test_unstarted_continuation_has_no_segment_snapshot() {
        Python::attach(|py| {
            let cont = Continuation::create_unstarted(PyShared::new(py.None()), Vec::new());
            assert!(!cont.is_started());
            assert!(cont.segment_id().is_none());
            assert!(cont.segment().is_none());
            assert!(cont.frames().is_none());
        });
    }

    #[test]
    fn test_continuation_snapshot_is_independent() {
        let marker = Marker::fresh();
        let mut seg = Segment::new(marker, None);
        let seg_id = SegmentId::from_index(0);

        seg.push_frame(Frame::FlatMapBindResult);

        let cont = Continuation::capture(&seg, seg_id, None);
        assert_eq!(cont.frames().map(|frames| frames.len()), Some(1));

        seg.push_frame(Frame::FlatMapBindResult);
        assert_eq!(cont.frames().map(|frames| frames.len()), Some(1));
        assert_eq!(seg.frame_count(), 2);
    }

    #[test]
    fn test_continuation_preserves_interceptor_boundary_snapshot_on_resume() {
        let marker = Marker::fresh();
        let seg_id = SegmentId::from_index(0);
        let interceptor = Arc::new(DummyKleisli) as KleisliRef;
        let mut seg = Segment::new(marker, None);
        seg.kind = SegmentKind::InterceptorBoundary {
            interceptor,
            types: None,
            mode: InterceptMode::Include,
            metadata: None,
        };

        let cont = Continuation::capture(&seg, seg_id, None);
        let captured_kind = cont
            .segment()
            .expect("captured continuation should have a segment snapshot")
            .kind
            .clone();
        assert!(matches!(
            captured_kind,
            SegmentKind::InterceptorBoundary {
                mode: InterceptMode::Include,
                ..
            }
        ));

        let resumed_seg = cont
            .segment()
            .expect("captured continuation should have a segment snapshot")
            .clone();
        assert!(matches!(
            resumed_seg.kind,
            SegmentKind::InterceptorBoundary {
                mode: InterceptMode::Include,
                ..
            }
        ));
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
            original_exception: None,
        }
    }

    #[test]
    fn test_continuation_capture_filters_orphan_dispatch_origin_frames() {
        let (mut seg, seg_id) = make_test_segment();
        seg.push_frame(make_dispatch_origin_frame(DispatchId::fresh()));

        let cont = Continuation::capture(&seg, seg_id, None);

        assert!(cont.frames().is_some_and(|frames| frames.is_empty()));
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

        assert_eq!(cont.frames().map(|frames| frames.len()), Some(2));
        assert!(matches!(
            cont.frames().expect("captured continuation should have frames")[1],
            Frame::DispatchOrigin { dispatch_id: kept_id, .. } if kept_id == dispatch_id
        ));
    }

    #[test]
    fn test_refresh_persistent_segment_state_updates_stale_snapshot() {
        let (mut seg, seg_id) = make_test_segment();
        seg.state_store.insert("count".to_string(), Value::Int(1));
        seg.writer_log.push(Value::Int(10));
        seg.persistent_epoch = 1;

        let mut cont = Continuation::capture(&seg, seg_id, None);
        let scope_id = seg.scope_id;
        let scope_state_store = std::collections::HashMap::from([(
            scope_id,
            std::collections::HashMap::from([("count".to_string(), Value::Int(2))]),
        )]);
        let scope_writer_logs = std::collections::HashMap::from([(scope_id, vec![Value::Int(20)])]);
        let scope_persistent_epochs = std::collections::HashMap::from([(scope_id, 2)]);

        cont.refresh_persistent_segment_state(
            &scope_state_store,
            &scope_writer_logs,
            &scope_persistent_epochs,
        );

        let snapshot = cont.segment().expect("continuation snapshot must exist");
        assert_eq!(snapshot.state_store.get("count"), Some(&Value::Int(2)));
        assert_eq!(snapshot.writer_log, vec![Value::Int(20)]);
        assert_eq!(snapshot.persistent_epoch, 2);
    }
}
