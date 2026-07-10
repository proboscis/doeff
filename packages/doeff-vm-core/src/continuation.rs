//! Continuation: a detached fiber chain.
//!
//! Matches OCaml 5's continuation representation:
//!   field[0]: stack_info*   (head of captured chain)
//!   field[1]: last_fiber    (tail, for O(1) append in reperform)
//!
//! Parent pointers in the arena are the source of truth for chain structure.
//! Continuation owns detached fibers directly while they are outside the arena.
//! One-shot via `Option::take()` — destructive read, like OCaml 5's atomic_swap.
//!
//! ## Move-only ownership (SPEC-VM-021 invariant)
//!
//! The chain lives directly in `Option<DetachedFiberChain>` — no Arc, no Mutex.
//! At every instant the DetachedFiberChain has EXACTLY ONE owning location
//! (the PyK value, a frame slot, or in-flight move). One-shot is enforced by
//! construction: `Option::take()` returns `Some` first time, `None` after.
//! The VM does not store continuations; for exception recovery during handler
//! dispatch, it keeps a `Py<PyK>` reference (a Python handle, not a
//! continuation) owned by the dispatch itself — a local in
//! eval_perform/eval_perform_with_k, then `Frame::Program.handler_k_handle`
//! or `EvalReturnContinuation::ExpandReturn.handler_k_handle` (#492).

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::arena::SlotReclaimQueue;
use crate::ids::FiberId;
use crate::ir_stream::StreamSourceLocation;
use crate::memory_stats;
use crate::py_shared::PyShared;
use crate::segment::Fiber;
use crate::value::{CallableRef, Value};

// ---------------------------------------------------------------------------
// DetachedFiberChain — fibers owned by a continuation while detached
// ---------------------------------------------------------------------------

/// A fiber removed from the arena while its continuation is suspended.
#[derive(Debug)]
pub struct DetachedFiber {
    pub id: FiberId,
    pub fiber: Fiber,
}

/// Kind of callable boundary found while walking a detached fiber chain.
/// `Handler` = prompt boundary (WithHandler), `Observer` = intercept
/// boundary (WithObserve). Mask boundaries carry no callable and are
/// not reported.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BoundaryKind {
    Handler,
    Observer,
}

/// A body-through-boundary fiber chain owned by `Continuation`.
#[derive(Debug)]
pub struct DetachedFiberChain {
    head: FiberId,
    last_fiber: FiberId,
    fibers: Vec<DetachedFiber>,
    /// Where to report still-owned arena slot indices if this chain is
    /// dropped without being reattached (#497). Armed by
    /// `FiberArena::detach_chain`; `None` for chains constructed outside an
    /// arena (tests). Carries bare slot indices only — never fibers or
    /// continuations (ISSUE-VM-001 G1); the chain never touches the arena
    /// directly (Drop can fire at arbitrary Python dealloc points,
    /// including from another thread).
    slot_reclaim_queue: Option<Arc<SlotReclaimQueue>>,
}

impl DetachedFiberChain {
    pub fn new(head: FiberId, last_fiber: FiberId, fibers: Vec<DetachedFiber>) -> Self {
        Self {
            head,
            last_fiber,
            fibers,
            slot_reclaim_queue: None,
        }
    }

    /// Arm slot reclamation: on drop, any fibers this chain still owns are
    /// reported to `queue` so the owning arena can return their slots to
    /// its free list (#497).
    pub(crate) fn arm_slot_reclaim(&mut self, queue: Arc<SlotReclaimQueue>) {
        self.slot_reclaim_queue = Some(queue);
    }

    pub fn head(&self) -> FiberId {
        self.head
    }

    pub fn last_fiber(&self) -> FiberId {
        self.last_fiber
    }

    pub fn fibers(&self) -> &[DetachedFiber] {
        &self.fibers
    }

    pub fn into_fibers(mut self) -> Vec<DetachedFiber> {
        // Drain in place: `self` then drops with no owned fibers, so the
        // Drop impl reports nothing — the caller now owns the fibers.
        std::mem::take(&mut self.fibers)
    }

    pub fn set_parent(&mut self, id: FiberId, parent: Option<FiberId>) -> bool {
        let Some(fiber) = self.fiber_mut(id) else {
            return false;
        };
        fiber.parent = parent;
        true
    }

    pub fn set_tail_parent(&mut self, parent: Option<FiberId>) -> bool {
        self.set_parent(self.last_fiber, parent)
    }

    pub fn append(&mut self, mut other: DetachedFiberChain) {
        let _ = self.set_tail_parent(Some(other.head));
        self.last_fiber = other.last_fiber;
        self.fibers.append(&mut other.fibers);
    }

    pub fn collect_traceback(&self) -> Vec<StreamSourceLocation> {
        let mut frames = Vec::new();
        let mut cursor = Some(self.head);

        while let Some(fid) = cursor {
            let Some(fiber) = self.fiber(fid) else { break };
            for frame in fiber.frames.iter().rev() {
                if let crate::frame::Frame::Program { stream, .. } = frame {
                    if let Some(loc) = stream.source_location() {
                        frames.push(loc);
                    }
                }
            }
            cursor = fiber.parent;
        }

        frames
    }

    pub fn handler_callables(&self) -> Vec<CallableRef> {
        let mut handlers = Vec::new();
        let mut cursor = Some(self.head);

        while let Some(fid) = cursor {
            let Some(fiber) = self.fiber(fid) else { break };
            if let Some(handler) = &fiber.handler {
                if let Some(prompt) = handler.prompt_boundary() {
                    handlers.push(prompt.handler.clone());
                }
            }
            cursor = fiber.parent;
        }

        handlers
    }

    /// Collect the interleaved handler/observer boundary stack, innermost
    /// first (chain head toward parents). The catching handler's prompt
    /// boundary terminates the chain and is included as the last entry,
    /// symmetric with `handler_callables`.
    pub fn boundary_callables(&self) -> Vec<(BoundaryKind, CallableRef)> {
        let mut boundaries = Vec::new();
        let mut cursor = Some(self.head);

        while let Some(fid) = cursor {
            let Some(fiber) = self.fiber(fid) else { break };
            if let Some(handler) = &fiber.handler {
                if let Some(prompt) = handler.prompt_boundary() {
                    boundaries.push((BoundaryKind::Handler, prompt.handler.clone()));
                }
                if let Some(intercept) = handler.intercept_boundary() {
                    boundaries.push((BoundaryKind::Observer, intercept.interceptor.clone()));
                }
            }
            cursor = fiber.parent;
        }

        boundaries
    }

    pub fn collect_rich_context(&self) -> Vec<Value> {
        let mut raw: Vec<(String, String, u32)> = Vec::new();
        let mut first_boundary: Option<FiberId> = None;
        let mut cursor = Some(self.head);

        while let Some(fid) = cursor {
            let Some(fiber) = self.fiber(fid) else { break };

            if first_boundary.is_none()
                && fiber
                    .handler
                    .as_ref()
                    .and_then(|handler| handler.prompt_boundary())
                    .is_some()
            {
                first_boundary = Some(fid);
            }

            for frame in fiber.frames.iter().rev() {
                if let crate::frame::Frame::Program { stream, .. } = frame {
                    if let Some(loc) = stream.source_location() {
                        raw.push((loc.func_name, loc.source_file, loc.source_line));
                    }
                }
            }

            cursor = fiber.parent;
        }

        raw.reverse();

        let mut frames = Vec::new();
        let mut i = 0;
        while i < raw.len() {
            let (ref func, ref file, line) = raw[i];
            let mut count: i64 = 1;
            while i + (count as usize) < raw.len() {
                let (ref nf, ref nfile, nline) = raw[i + count as usize];
                if nf == func && nfile == file && nline == line {
                    count += 1;
                } else {
                    break;
                }
            }
            let mut entry = vec![
                Value::String("frame".to_string()),
                Value::String(func.clone()),
                Value::String(file.clone()),
                Value::Int(line as i64),
            ];
            if count > 1 {
                entry.push(Value::Int(count));
            }
            frames.push(Value::List(entry));
            i += count as usize;
        }

        if let Some(boundary_id) = first_boundary {
            let handler_names: Vec<Value> = self
                .handler_callables_from(boundary_id)
                .into_iter()
                .map(|handler| {
                    Value::String(handler.name().unwrap_or_else(|| "<handler>".to_string()))
                })
                .collect();
            if !handler_names.is_empty() {
                frames.push(Value::List(vec![
                    Value::String("handler".to_string()),
                    Value::String("chain".to_string()),
                    Value::List(handler_names),
                ]));
            }
        }

        frames
    }

    fn fiber(&self, id: FiberId) -> Option<&Fiber> {
        self.fibers
            .iter()
            .find_map(|entry| (entry.id == id).then_some(&entry.fiber))
    }

    fn fiber_mut(&mut self, id: FiberId) -> Option<&mut Fiber> {
        self.fibers
            .iter_mut()
            .find_map(|entry| (entry.id == id).then_some(&mut entry.fiber))
    }

    fn handler_callables_from(&self, start: FiberId) -> Vec<CallableRef> {
        let mut handlers = Vec::new();
        let mut cursor = Some(start);

        while let Some(fid) = cursor {
            let Some(fiber) = self.fiber(fid) else { break };
            if let Some(handler) = &fiber.handler {
                if let Some(prompt) = handler.prompt_boundary() {
                    handlers.push(prompt.handler.clone());
                }
            }
            cursor = fiber.parent;
        }

        handlers
    }
}

impl Drop for DetachedFiberChain {
    /// A chain dropped while still owning fibers was abandoned without
    /// reattachment (abort-style handler, scheduler cancellation, dropped
    /// parked K). Report the owned slot indices to the arena's reclaim
    /// queue so they return to the free list instead of stranding as
    /// vacant-reserved until run end (#497). Consumption paths
    /// (`into_fibers`, `append`) drain `fibers` first, so they report
    /// nothing here.
    fn drop(&mut self) {
        if self.fibers.is_empty() {
            return;
        }
        let Some(queue) = &self.slot_reclaim_queue else {
            // Chain never belonged to an arena (direct construction in
            // tests) — there is no free list to return slots to.
            return;
        };
        queue.report_dropped_slots(self.fibers.iter().map(|entry| entry.id.index()));
    }
}

// ---------------------------------------------------------------------------
// Continuation — the detached fiber chain
// ---------------------------------------------------------------------------

/// A captured fiber chain. NOT Clone — one semantic owner, one-shot.
///
/// The chain lives directly in `Option<DetachedFiberChain>` — move-only by
/// construction. `Option::take()` enforces one-shot: Some first time, None
/// after. No Arc, no Mutex, no shared handles (SPEC-VM-021 invariants 1–4).
///
/// Created by `perform` (detach chain from handler).
/// Consumed by `reattach_chain` / `continue_k` (reattach chain to caller).
/// Extended by `reperform` (append current fiber to chain).
#[derive(Debug)]
pub struct Continuation {
    /// The detached fiber chain. Some = live, None = consumed.
    chain: Option<DetachedFiberChain>,
}

impl Continuation {
    /// Create a continuation from a detached fiber chain.
    /// Called by perform after moving fibers out of the arena.
    pub fn from_chain(chain: DetachedFiberChain) -> Self {
        memory_stats::register_continuation();
        Self {
            chain: Some(chain),
        }
    }

    /// Sentinel for an already-consumed continuation (chain=None).
    /// Used by the Python bridge when PyK has already been taken.
    /// The VM core's reattach_chain will detect this and raise the one-shot
    /// error with full VM context (current_segment, traceback).
    pub fn empty() -> Self {
        // Register so Drop's unregister is balanced.
        memory_stats::register_continuation();
        Self { chain: None }
    }

    /// One-shot take: returns the detached chain and clears the cell.
    /// First call returns `Some(chain)`, subsequent calls return `None`.
    pub fn take(&mut self) -> Option<DetachedFiberChain> {
        self.chain.take()
    }

    /// Head fiber (identity of this continuation).
    pub fn head(&self) -> Option<FiberId> {
        self.chain.as_ref().map(DetachedFiberChain::head)
    }

    /// Last fiber in the chain.
    pub fn last_fiber(&self) -> Option<FiberId> {
        self.chain.as_ref().map(DetachedFiberChain::last_fiber)
    }

    /// Is this continuation already consumed?
    pub fn consumed(&self) -> bool {
        self.chain.is_none()
    }

    /// Is this a live (unconsumed) continuation?
    pub fn is_live(&self) -> bool {
        self.chain.is_some()
    }

    /// Identity = head fiber. Used as dispatch identity.
    pub fn identity(&self) -> Option<FiberId> {
        self.head()
    }

    pub(crate) fn append_chain(&mut self, chain: DetachedFiberChain) -> bool {
        let Some(existing) = self.chain.as_mut() else {
            return false;
        };
        existing.append(chain);
        true
    }

    pub fn collect_traceback(&self) -> Option<Vec<StreamSourceLocation>> {
        self.chain.as_ref().map(DetachedFiberChain::collect_traceback)
    }

    pub fn handler_callables(&self) -> Option<Vec<CallableRef>> {
        self.chain.as_ref().map(DetachedFiberChain::handler_callables)
    }

    pub fn boundary_callables(&self) -> Option<Vec<(BoundaryKind, CallableRef)>> {
        self.chain
            .as_ref()
            .map(DetachedFiberChain::boundary_callables)
    }

    pub fn collect_rich_context(&self) -> Option<Vec<Value>> {
        self.chain.as_ref().map(DetachedFiberChain::collect_rich_context)
    }

    /// Inspect the current chain contents without consuming (None = consumed).
    pub(crate) fn inspect_chain<R>(&self, f: impl FnOnce(Option<&DetachedFiberChain>) -> R) -> R {
        f(self.chain.as_ref())
    }
}

impl Drop for Continuation {
    fn drop(&mut self) {
        memory_stats::unregister_continuation();
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
    pub fn create(
        program: PyShared,
        handlers: Vec<(crate::value::CallableRef, Vec<PyShared>)>,
    ) -> Self {
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

/// GC note (#500): PyK deliberately implements NEITHER `__traverse__` nor
/// `__clear__`, unlike the other Py-holding pyclasses.
///
/// - `__clear__` is unsafe by construction: a live PyK is the SOLE owner of
///   a detached fiber chain (SPEC-VM-021 move-only invariant), and during a
///   handler dispatch the VM may still recover that chain through a
///   dispatch-owned `Py<PyK>` handle to reattach it for exception
///   propagation (#492). Dropping the chain from the GC's `tp_clear` while
///   such a handle exists would destroy the one-shot ownership invariant
///   mid-dispatch.
/// - `__traverse__` alone is not implementable with the current internals:
///   the Python references inside the chain live behind `PyShared` handles
///   in fibers → frames → `dyn IRStream` streams → `Value`s, none of which
///   expose a GC-visit API. Walking them would require threading a visitor
///   through the whole frame/stream abstraction.
///
/// Consequence: the GC treats PyK as an opaque leaf. Cycles that merely
/// pass through a PyK handle held in a DoExpr field (Resume/Transfer/...)
/// are still detected via those classes' `__traverse__`; a cycle that
/// closes through PyK's interior (e.g. a suspended generator captured in
/// the chain referencing the PyK itself) remains uncollectable until the
/// continuation is consumed or the PyK is dropped by refcount.
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
