//! Continuation: a detached fiber chain.
//!
//! Matches OCaml 5's continuation representation:
//!   field[0]: stack_info*   (head of captured chain)
//!   field[1]: last_fiber    (tail, for O(1) append in reperform)
//!
//! Parent pointers in the arena are the source of truth for chain structure.
//! Continuation just holds two pointers into the arena.
//! One-shot via head.take() — destructive read, like OCaml 5's atomic_swap.
//!
//! Orphan reclamation: when a continuation is dropped without being consumed
//! (e.g., scheduler drops TaskCompleted's k), its fiber IDs are pushed to a
//! thread-local queue. The VM drains this queue each step, walking and freeing
//! the orphaned fiber chains from the arena.

use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::ids::FiberId;
use crate::memory_stats;
use crate::py_shared::PyShared;

// ---------------------------------------------------------------------------
// Orphan fiber reclamation — per-VM queue
// ---------------------------------------------------------------------------

/// Per-VM queue for orphaned fiber heads. Each Continuation holds an Arc clone.
/// When a Continuation is dropped without being consumed, its head FiberId is
/// pushed to the owning VM's queue (not a global/thread-local).
pub type OrphanQueue = Arc<Mutex<Vec<FiberId>>>;

/// Create a new orphan queue. Called once per VM instance.
pub fn new_orphan_queue() -> OrphanQueue {
    Arc::new(Mutex::new(Vec::new()))
}

/// Drain all orphaned fiber head IDs from a queue. Called by the VM at each step.
pub fn drain_orphan_fibers(queue: &OrphanQueue) -> Vec<FiberId> {
    let mut v = queue.lock().unwrap();
    std::mem::take(&mut *v)
}

// ---------------------------------------------------------------------------
// Continuation — the detached fiber chain
// ---------------------------------------------------------------------------

/// A captured fiber chain. NOT Clone — one owner, one-shot.
///
/// Created by `perform` (detach chain from handler).
/// Consumed by `continue_k` (reattach chain to caller).
/// Extended by `reperform` (append current fiber to chain).
#[derive(Debug)]
pub struct Continuation {
    /// Head of the detached fiber chain (first fiber).
    /// `take()` enforces one-shot: Some first time, None after.
    head: Option<FiberId>,
    /// Tail of the chain (last fiber). For O(1) append in reperform.
    pub(crate) last_fiber: Option<FiberId>,
    /// Per-VM orphan queue. On drop, unconsumed heads are pushed here
    /// so the owning VM (not another VM on the same thread) reclaims them.
    orphan_queue: Option<OrphanQueue>,
}

impl Continuation {
    /// Create a continuation from a detached fiber chain.
    /// Called by perform after cutting the tail→handler parent pointer.
    pub fn new(head: FiberId, last_fiber: FiberId, orphan_queue: OrphanQueue) -> Self {
        memory_stats::register_continuation();
        Self {
            head: Some(head),
            last_fiber: Some(last_fiber),
            orphan_queue: Some(orphan_queue),
        }
    }

    /// Single-fiber continuation (head == last_fiber).
    pub fn single(fiber_id: FiberId, orphan_queue: OrphanQueue) -> Self {
        Self::new(fiber_id, fiber_id, orphan_queue)
    }

    /// Sentinel for an already-consumed continuation (head=None).
    /// Used by the Python bridge when PyK has already been taken.
    /// The VM core's continue_k will detect this and raise the one-shot error
    /// with full VM context (current_segment, traceback).
    pub fn empty() -> Self {
        // Register so Drop's unregister is balanced.
        memory_stats::register_continuation();
        Self {
            head: None,
            last_fiber: None,
            orphan_queue: None,
        }
    }

    /// One-shot take: returns the head fiber and clears the continuation.
    /// Returns None if already consumed.
    pub fn take_head(&mut self) -> Option<FiberId> {
        self.last_fiber = None;
        self.head.take()
    }

    /// One-shot take: returns (head, last_fiber) and clears.
    pub fn take(&mut self) -> Option<(FiberId, FiberId)> {
        let head = self.head.take()?;
        let last = self.last_fiber.take()?;
        Some((head, last))
    }

    /// Head fiber (identity of this continuation).
    pub fn head(&self) -> Option<FiberId> {
        self.head
    }

    /// Last fiber in the chain.
    pub fn last_fiber(&self) -> Option<FiberId> {
        self.last_fiber
    }

    /// Is this continuation already consumed?
    pub fn consumed(&self) -> bool {
        self.head.is_none()
    }

    /// Is this a live (unconsumed) continuation?
    pub fn is_live(&self) -> bool {
        self.head.is_some()
    }

    /// Identity = head fiber. Used as dispatch identity.
    pub fn identity(&self) -> Option<FiberId> {
        self.head
    }

    /// Append another continuation's chain to this one (reperform).
    /// Sets self.last_fiber.parent = other.head in the arena,
    /// then updates self.last_fiber = other.last_fiber.
    ///
    /// The caller must also set the parent pointer in the arena:
    ///   arena[self.last_fiber].parent = Some(other.head)
    /// This method just updates the Continuation metadata.
    pub fn append_chain(&mut self, other: &mut Continuation) {
        if let Some(other_last) = other.last_fiber.take() {
            // other.head is consumed by the append — the fibers are now part of self
            let _ = other.head.take();
            self.last_fiber = Some(other_last);
        }
    }
}

impl Drop for Continuation {
    fn drop(&mut self) {
        memory_stats::unregister_continuation();
        // If the continuation was never consumed, its fibers are orphaned
        // in the arena. Push the head to the owning VM's orphan queue.
        if let Some(head) = self.head.take() {
            if let Some(ref queue) = self.orphan_queue {
                if let Ok(mut v) = queue.lock() {
                    v.push(head);
                }
                // If lock is poisoned, we silently leak the fiber.
                // This is safe (just wastes arena slots) and avoids
                // panicking in Drop.
            }
            // If orphan_queue is None (empty() sentinel), the fiber is
            // not backed by any arena — nothing to reclaim.
        }
    }
}

// ---------------------------------------------------------------------------
// PendingContinuation — not yet started (no fibers allocated)
// ---------------------------------------------------------------------------

/// A continuation for a program that hasn't started execution yet.
/// Once started, it becomes a Continuation with actual fibers.
#[derive(Debug, Clone)]
pub struct PendingContinuation {
    pub program: PyShared,
    pub handlers: Vec<(crate::value::CallableRef, Vec<PyShared>)>,
    pub handler_identities: Vec<Option<String>>,
    pub outside_scope: Option<FiberId>,
}

impl PendingContinuation {
    pub fn create(program: PyShared, handlers: Vec<(crate::value::CallableRef, Vec<PyShared>)>) -> Self {
        memory_stats::register_continuation();
        Self {
            program,
            handlers,
            handler_identities: Vec::new(),
            outside_scope: None,
        }
    }

    pub fn create_with_metadata(
        program: PyShared,
        handlers: Vec<(crate::value::CallableRef, Vec<PyShared>)>,
        handler_identities: Vec<Option<String>>,
        outside_scope: Option<FiberId>,
    ) -> Self {
        memory_stats::register_continuation();
        Self {
            program,
            handlers,
            handler_identities,
            outside_scope,
        }
    }
}

impl Drop for PendingContinuation {
    fn drop(&mut self) {
        memory_stats::unregister_continuation();
    }
}

// ---------------------------------------------------------------------------
// OwnedControlContinuation — either started or pending
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum OwnedControlContinuation {
    Started(Continuation),
    Pending(PendingContinuation),
}

impl OwnedControlContinuation {
    pub fn identity(&self) -> Option<FiberId> {
        match self {
            Self::Started(k) => k.identity(),
            Self::Pending(_) => None,
        }
    }

    pub fn is_started(&self) -> bool {
        matches!(self, Self::Started(_))
    }
}

// ---------------------------------------------------------------------------
// PyK — Python-visible continuation handle (sole owner)
// ---------------------------------------------------------------------------

#[pyclass(name = "K")]
pub struct PyK {
    continuation: Option<OwnedControlContinuation>,
}

impl PyK {
    pub fn from_continuation(k: Continuation) -> Self {
        Self {
            continuation: Some(OwnedControlContinuation::Started(k)),
        }
    }

    pub fn from_pending(pending: PendingContinuation) -> Self {
        Self {
            continuation: Some(OwnedControlContinuation::Pending(pending)),
        }
    }

    /// Take the continuation out (one-shot at PyK level).
    pub fn take(&mut self) -> Option<OwnedControlContinuation> {
        self.continuation.take()
    }

    /// Borrow the continuation (for inspection without consuming).
    pub fn continuation_ref(&self) -> Option<&OwnedControlContinuation> {
        self.continuation.as_ref()
    }

    /// Is this PyK already consumed?
    pub fn is_exhausted(&self) -> bool {
        self.continuation.is_none()
    }

    /// Peek at the head FiberId without consuming.
    /// Used by GetTraceback to walk the chain without taking ownership.
    pub fn peek_head(&self) -> Option<FiberId> {
        match &self.continuation {
            Some(OwnedControlContinuation::Started(k)) => k.head(),
            _ => None,
        }
    }
}

#[pymethods]
impl PyK {
    fn __repr__(&self) -> String {
        match &self.continuation {
            Some(OwnedControlContinuation::Started(k)) => {
                format!("K(head={:?}, last={:?})", k.head(), k.last_fiber())
            }
            Some(OwnedControlContinuation::Pending(_)) => "K(pending)".to_string(),
            None => "K(consumed)".to_string(),
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        match &self.continuation {
            Some(OwnedControlContinuation::Started(k)) => {
                dict.set_item("head", k.head().map(|f| f.index()))?;
                dict.set_item("last_fiber", k.last_fiber().map(|f| f.index()))?;
                dict.set_item("consumed", false)?;
            }
            Some(OwnedControlContinuation::Pending(_)) => {
                dict.set_item("pending", true)?;
                dict.set_item("consumed", false)?;
            }
            None => {
                dict.set_item("consumed", true)?;
            }
        }
        Ok(dict)
    }
}
