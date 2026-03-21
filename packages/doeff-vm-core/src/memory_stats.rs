use std::sync::atomic::{AtomicUsize, Ordering};

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
