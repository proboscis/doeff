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
pub mod state_handler;
pub mod typing_support;
pub mod unhandled_hint;

// Re-export VM core types
pub use doeff_vm_core::continuation::{OwnedControlContinuation, PendingContinuation, PyK};
pub use doeff_vm_core::segment::Fiber;
pub use doeff_vm_core::value::{Callable, CallableRef};
pub use doeff_vm_core::{
    Continuation, DoCtrl, FiberId, Frame, IRStream, IRStreamRef, Marker, SegmentId, Signal,
    SignalAction, StepResult, StreamStep, VMError, Value, VarId, VarStore, VM,
};

/// Return (live_segments, live_continuations, live_ir_streams).
#[pyfunction]
#[pyo3(name = "_vm_live_counts")]
fn vm_live_counts() -> (usize, usize, usize) {
    let c = doeff_vm_core::memory_stats::live_object_counts();
    (c.live_segments, c.live_continuations, c.live_ir_streams)
}

/// Return (steps, handler_calls) taken by every VM in this process so far —
/// cumulative, never decreasing. doeff-hy-pytest's budget plugin reads it
/// (through the effect `ReadVmWorkCounts`) before and after a test to judge
/// the test by a load-independent count (agora-redesign #2670 / #2851).
#[pyfunction]
#[pyo3(name = "_vm_work_counts")]
fn vm_work_counts() -> (u64, u64) {
    let c = doeff_vm_core::memory_stats::work_counts();
    (c.steps, c.handler_calls)
}

/// Return the entries every scan walked so far, by what it walked — the
/// Rust scheduler's structures (`ready`, `waiters`, `semaphore_waiters`,
/// and the amortized `sweep`) and the fibers looked
/// at to find one in a detached continuation chain (`chain_fibers`).
/// Cumulative, never decreasing: a test reads it before and after, to
/// judge by a load-independent count whether one step's work grows with
/// the parked tasks or the handler depth (agora card ki-9de8d0a54408).
#[pyfunction]
#[pyo3(name = "_vm_scan_counts")]
fn vm_scan_counts(py: Python<'_>) -> PyResult<Bound<'_, pyo3::types::PyDict>> {
    let counts = pyo3::types::PyDict::new(py);
    for (name, count) in scheduler::scan_counts() {
        counts.set_item(name, count)?;
    }
    counts.set_item(
        "chain_fibers",
        doeff_vm_core::memory_stats::chain_fiber_probes(),
    )?;
    Ok(counts)
}

/// True when the per-step runtime conformance oracle is on in this
/// process. Every build carries the checks; they run only when turned on
/// (`DOEFF_VM_INVARIANT_CHECKS=1` or the effect `SetInvariantChecks(True)`).
/// doeff's own pytest sessions turn them on (ADR-DOE-ENFORCE-001 R4).
#[pyfunction]
#[pyo3(name = "_invariant_checks_enabled")]
fn invariant_checks_enabled() -> bool {
    doeff_vm_core::invariant_checks_enabled()
}

/// Turn the per-step runtime conformance oracle on or off for this process.
#[pyfunction]
#[pyo3(name = "_set_invariant_checks")]
fn set_invariant_checks(on: bool) {
    doeff_vm_core::set_invariant_checks(on);
}

/// Bind a function of the module that is no part of its public names: set as
/// an attribute only, so `__all__` (which `add_function` appends to) does not
/// list it. The VM's counts and switches are read and set through doeff's
/// effects (`ReadVmWorkCounts`, `SetInvariantChecks`, ... answered by
/// `doeff_core_effects.handlers.vm_probe_handler`); the functions stay inside
/// for that handler and doeff's own scheduler (agora card ki-27e8ea225b62).
pub(crate) fn bind_internal(
    m: &Bound<'_, PyModule>,
    function: Bound<'_, pyo3::types::PyCFunction>,
) -> PyResult<()> {
    let name: String = function.getattr(pyo3::intern!(m.py(), "__name__"))?.extract()?;
    m.setattr(name.as_str(), function)
}

#[pymodule]
fn doeff_vm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyvm::register_pyvm(m)?;
    scheduler::register(m)?;
    state_handler::register(m)?;

    bind_internal(m, pyo3::wrap_pyfunction!(vm_live_counts, m)?)?;
    bind_internal(m, pyo3::wrap_pyfunction!(vm_work_counts, m)?)?;
    bind_internal(m, pyo3::wrap_pyfunction!(vm_scan_counts, m)?)?;
    bind_internal(m, pyo3::wrap_pyfunction!(invariant_checks_enabled, m)?)?;
    bind_internal(m, pyo3::wrap_pyfunction!(set_invariant_checks, m)?)?;

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
