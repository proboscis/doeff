//! doeff-vm: Python bridge for the language-agnostic VM.
//!
//! This crate connects Python (via PyO3) to doeff-vm-core.
//! It provides:
//!   - PythonGeneratorStream: Python generator → IRStream adapter
//!   - classify_yielded: Python object → DoCtrl conversion
//!   - Value ↔ Python conversion
//!   - run() entry point

use pyo3::prelude::*;
use pyo3::types::PyType;

pub mod do_expr;
pub mod gc;
pub mod python_generator_stream;
pub mod pyvm;
pub mod result;
pub mod scheduler;
pub mod typing_support;

// Re-export VM core types
pub use doeff_vm_core::continuation::{OwnedControlContinuation, PendingContinuation, PyK};
pub use doeff_vm_core::segment::Fiber;
pub use doeff_vm_core::value::{Callable, CallableRef};
pub use doeff_vm_core::{
    Continuation, DoCtrl, FiberId, Frame, IRStream, IRStreamRef, Marker, SegmentId, Signal,
    SignalAction, StepResult, StreamStep, VMError, Value, VarId, VarStore, VM,
};

#[pymodule]
fn doeff_vm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyvm::register_pyvm(m)?;
    scheduler::register(m)?;

    /// Return (live_segments, live_continuations, live_ir_streams).
    #[pyfn(m)]
    fn vm_live_counts() -> (usize, usize, usize) {
        let c = doeff_vm_core::memory_stats::live_object_counts();
        (c.live_segments, c.live_continuations, c.live_ir_streams)
    }

    /// Return (steps, handler_calls) taken by every VM in this process so far —
    /// cumulative, never decreasing. doeff-hy-pytest's budget plugin reads it
    /// before and after a test to judge the test by a load-independent count
    /// (agora-redesign #2670 / #2851). Deliberately NOT re-exported from
    /// `doeff_vm/__init__.py` for the same reason as `gc_traverse_zeroed_visits`
    /// below: callers import it from `doeff_vm.doeff_vm`.
    #[pyfn(m)]
    fn vm_work_counts() -> (u64, u64) {
        let c = doeff_vm_core::memory_stats::work_counts();
        (c.steps, c.handler_calls)
    }

    /// True when the per-step runtime conformance oracle is on in this
    /// process. Every build carries the checks; they run only when turned on
    /// (`DOEFF_VM_INVARIANT_CHECKS=1` or `set_invariant_checks(True)`).
    /// doeff's own pytest sessions turn them on (ADR-DOE-ENFORCE-001 R4).
    #[pyfn(m)]
    fn invariant_checks_enabled() -> bool {
        doeff_vm_core::invariant_checks_enabled()
    }

    /// Turn the per-step runtime conformance oracle on or off for this process.
    #[pyfn(m)]
    fn set_invariant_checks(on: bool) {
        doeff_vm_core::set_invariant_checks(on);
    }

    /// Conformance oracle for the GC traverse invariant (see the `gc` module docs):
    /// run `cls`'s `tp_traverse` against a stand-in instance whose Rust contents are
    /// still all-zero — the state the cycle collector can observe between `tp_alloc`
    /// (which GC-tracks the instance) and pyo3 writing the Rust value — and report
    /// `(visits, null_visits)`.
    ///
    /// `null_visits` must be 0. A null handed to CPython's `visit_decref`
    /// dereferences `Py_TYPE(NULL)` and segfaults (agora-1 pod `ai usage`, 2026-08-26).
    ///
    /// Deliberately NOT re-exported from `doeff_vm/__init__.py`: that file is
    /// shared by every ABI-tagged `.so` in the tree, so a name added there turns
    /// any stale extension build into an import-time `AttributeError` that takes
    /// down every doeff consumer at once (observed 2026-08-26 — 7 stale `.so`
    /// files in the checkout, `ai`/`agmsg` unusable). Callers import it from
    /// `doeff_vm.doeff_vm`, which is version-locked to the extension itself.
    #[pyfn(m)]
    fn gc_traverse_zeroed_visits(cls: &Bound<'_, PyType>) -> (usize, usize) {
        gc::traverse_zeroed_visits(cls)
    }

    Ok(())
}

/// The VM allocates and frees small Rust objects (frames, fibers, boxed
/// DoCtrl, Vec) on every effect. On macOS the system allocator's `free` read
/// the clock (`mach_absolute_time`) on each call and took ~20% of an effect's
/// time in a profile; mimalloc made one effect 25% cheaper (1.27 → 0.95 µs,
/// 2026-09-23) with no RSS growth over 1M effects. Only Rust-side allocations
/// use it; Python objects keep CPython's allocator.
#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;
