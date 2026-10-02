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

// ── 積み上げの仕事の量(agora-redesign #2851・#2670)───────────────────────────────────
// 上の「今生きている数」と違い、process の全部の VM が進めた歩数と handler を呼んだ回数を、減らさずに積み上げる。
// 検の時間の予算を、機体の負荷で揺れない決まった数で判じるため(doeff-hy-pytest の budget.py が区間の前後の差を取る)。
// 加算は Relaxed の 1 回で、常時の費用はほぼ 0。

static VM_STEPS: AtomicU64 = AtomicU64::new(0);
static HANDLER_CALLS: AtomicU64 = AtomicU64::new(0);

/// process の全部の VM の積み上げの仕事の量 — 区間の前後で読み、差をその区間の仕事にするため。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VmWorkCounts {
    pub steps: u64,
    pub handler_calls: u64,
}

/// 今の積み上げの数を読む。
pub fn work_counts() -> VmWorkCounts {
    VmWorkCounts {
        steps: VM_STEPS.load(Ordering::Relaxed),
        handler_calls: HANDLER_CALLS.load(Ordering::Relaxed),
    }
}

/// VM が 1 歩進めた(`VM::step` の入口)。
pub(crate) fn record_step() {
    VM_STEPS.fetch_add(1, Ordering::Relaxed);
}

/// VM が handler を 1 回呼んだ(`call_handler` の呼び出しごと)。
pub(crate) fn record_handler_call() {
    HANDLER_CALLS.fetch_add(1, Ordering::Relaxed);
}
