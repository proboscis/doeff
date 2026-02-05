//! Continuation types for capturing and resuming.
//!
//! Continuations represent "the rest of the computation" and can be
//! captured by handlers and resumed later.

use crate::ids::{ContId, DispatchId, RunnableId, SegmentId};
use crate::value::Value;

/// Capturable continuation (subject to one-shot check).
///
/// A Continuation represents a point in the computation that can be resumed.
/// One-shot semantics are enforced: each ContId can only be resumed once.
#[derive(Debug, Clone)]
pub struct Continuation {
    /// Unique identifier for one-shot tracking
    pub cont_id: ContId,

    /// The segment this continuation points to
    pub segment_id: SegmentId,

    /// Which dispatch created this (for completion detection).
    /// If Some, resuming this continuation may complete the dispatch.
    pub dispatch_id: Option<DispatchId>,
}

impl Continuation {
    /// Create a new continuation.
    pub fn new(segment_id: SegmentId, dispatch_id: Option<DispatchId>) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id,
            dispatch_id,
        }
    }

    /// Create a continuation with a specific ContId (for testing).
    pub fn with_id(
        cont_id: ContId,
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Continuation {
            cont_id,
            segment_id,
            dispatch_id,
        }
    }
}

/// Ready-to-run continuation. INTERNAL to scheduler.
///
/// Created by ResumeThenTransfer for scheduler queues.
/// This represents a continuation that has been prepared to run
/// with a pending value.
#[derive(Debug)]
pub(crate) struct RunnableContinuation {
    /// Unique identifier for execution tracking
    pub runnable_id: RunnableId,

    /// The segment to execute
    pub segment_id: SegmentId,

    /// Value to deliver when executed
    pub pending_value: Value,
}

impl RunnableContinuation {
    /// Create a new runnable continuation.
    pub fn new(segment_id: SegmentId, value: Value) -> Self {
        RunnableContinuation {
            runnable_id: RunnableId::fresh(),
            segment_id,
            pending_value: value,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_continuation_creation() {
        let seg_id = SegmentId::from_index(0);
        let cont = Continuation::new(seg_id, None);
        assert_eq!(cont.segment_id, seg_id);
        assert!(cont.dispatch_id.is_none());
    }

    #[test]
    fn test_continuation_unique_ids() {
        let seg_id = SegmentId::from_index(0);
        let c1 = Continuation::new(seg_id, None);
        let c2 = Continuation::new(seg_id, None);
        assert_ne!(c1.cont_id, c2.cont_id);
    }

    #[test]
    fn test_runnable_continuation() {
        let seg_id = SegmentId::from_index(0);
        let runnable = RunnableContinuation::new(seg_id, Value::Int(42));
        assert_eq!(runnable.segment_id, seg_id);
        assert!(matches!(runnable.pending_value, Value::Int(42)));
    }
}
