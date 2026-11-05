use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VmLiveObjectCounts {
    pub live_segments: usize,
    pub live_continuations: usize,
    pub live_ir_streams: usize,
    pub in_place_reentries: usize,
    pub abandoned_transfer_branch_frees: usize,
}

static LIVE_SEGMENTS: AtomicUsize = AtomicUsize::new(0);
static LIVE_CONTINUATIONS: AtomicUsize = AtomicUsize::new(0);
static LIVE_IR_STREAMS: AtomicUsize = AtomicUsize::new(0);
static IN_PLACE_REENTRIES: AtomicUsize = AtomicUsize::new(0);
static ABANDONED_TRANSFER_BRANCH_FREES: AtomicUsize = AtomicUsize::new(0);

pub fn live_object_counts() -> VmLiveObjectCounts {
    VmLiveObjectCounts {
        live_segments: LIVE_SEGMENTS.load(Ordering::Relaxed),
        live_continuations: LIVE_CONTINUATIONS.load(Ordering::Relaxed),
        live_ir_streams: LIVE_IR_STREAMS.load(Ordering::Relaxed),
        in_place_reentries: IN_PLACE_REENTRIES.load(Ordering::Relaxed),
        abandoned_transfer_branch_frees: ABANDONED_TRANSFER_BRANCH_FREES.load(Ordering::Relaxed),
    }
}

pub(crate) fn register_segment() {
    LIVE_SEGMENTS.fetch_add(1, Ordering::Relaxed);
}

pub(crate) fn unregister_segment() {
    LIVE_SEGMENTS.fetch_sub(1, Ordering::Relaxed);
}

pub(crate) fn register_continuation() {
    LIVE_CONTINUATIONS.fetch_add(1, Ordering::Relaxed);
}

pub(crate) fn unregister_continuation() {
    LIVE_CONTINUATIONS.fetch_sub(1, Ordering::Relaxed);
}

pub(crate) fn register_ir_stream() {
    LIVE_IR_STREAMS.fetch_add(1, Ordering::Relaxed);
}

pub(crate) fn unregister_ir_stream() {
    LIVE_IR_STREAMS.fetch_sub(1, Ordering::Relaxed);
}

pub(crate) fn record_in_place_reentry() {
    IN_PLACE_REENTRIES.fetch_add(1, Ordering::Relaxed);
}

pub(crate) fn record_abandoned_transfer_branch_free() {
    ABANDONED_TRANSFER_BRANCH_FREES.fetch_add(1, Ordering::Relaxed);
}

// -- Accumulated amount of work (#2670) --------------------------------
// Unlike the "currently live counts" above, these accumulate, without decreasing, the steps advanced and handler invocations made by every VM in the process.
// This lets a test's time budget be judged by a fixed count that does not fluctuate with machine load (budget.py in doeff-hy-pytest takes the difference before and after an interval).
// Each addition is a single Relaxed operation, so the always-on cost is nearly 0.

static VM_STEPS: AtomicU64 = AtomicU64::new(0);
static HANDLER_CALLS: AtomicU64 = AtomicU64::new(0);

/// Accumulated amount of work across every VM in the process - read before and after an interval, and the difference is that interval's work.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VmWorkCounts {
    pub steps: u64,
    pub handler_calls: u64,
}

/// Read the current accumulated counts.
pub fn work_counts() -> VmWorkCounts {
    VmWorkCounts {
        steps: VM_STEPS.load(Ordering::Relaxed),
        handler_calls: HANDLER_CALLS.load(Ordering::Relaxed),
    }
}

/// The VM advanced one step (entry of `VM::step`).
pub(crate) fn record_step() {
    VM_STEPS.fetch_add(1, Ordering::Relaxed);
}

/// The VM invoked a handler once (per call of `call_handler`).
pub(crate) fn record_handler_call() {
    HANDLER_CALLS.fetch_add(1, Ordering::Relaxed);
}

// -- Number of entries scanned in the continuation's fiber chain --------------
// Accumulates the number of entries visited each time a fiber is looked up by id within a detached fiber chain (DetachedFiberChain). This is to judge by count rather than
// by clock whether the work of traversing a chain once is proportional to the chain length (number of handler levels) or to its square (a test takes the difference before and after).
// One Relaxed addition per traversal.

static CHAIN_FIBER_PROBES: AtomicU64 = AtomicU64::new(0);

/// Accumulated number of entries visited to look up fibers within a chain.
pub fn chain_fiber_probes() -> u64 {
    CHAIN_FIBER_PROBES.load(Ordering::Relaxed)
}

/// Add `probes`, the number of entries visited during one traversal of a chain.
pub(crate) fn record_chain_fiber_probes(probes: u64) {
    if probes != 0 {
        CHAIN_FIBER_PROBES.fetch_add(probes, Ordering::Relaxed);
    }
}
