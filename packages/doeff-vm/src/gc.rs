//! GC integration helpers shared by every `__traverse__` in this crate.
//!
//! ## Why `PyVisit::call` may not be used directly
//!
//! CPython GC-*tracks* a new instance inside `tp_alloc`
//! (`PyType_GenericAlloc`), but pyo3 writes the Rust value into that
//! instance only *after* the base `tp_new` returns
//! (`PyClassInitializer::create_class_object_of_type`). Any cycle
//! collection that runs inside that window — and for `#[pyclass(dict)]`
//! types a further Python allocation does happen inside `tp_new` — calls
//! `tp_traverse` on an instance whose Rust fields are still all-zero, i.e.
//! every `Py<T>` field is a null pointer.
//!
//! `PyVisit::call` does guard against null, but `Py<T>` is
//! `#[repr(transparent)]` over `NonNull<PyObject>`: an optimizing build
//! folds `ptr.is_null()` to `false` and hands the null straight to
//! CPython's `visit_decref`, which dereferences `Py_TYPE(NULL)` — SIGSEGV
//! at address 0x8. Debug builds keep the check, which is why this only ever
//! reproduced in release wheels (`make sync` builds `--release`), and only
//! on the reference-counted GC of CPython ≤ 3.13 (observed 3.11.2 in the
//! agora-1 pod, 2026-08-26; the free-threaded 3.14 build did not reproduce).
//!
//! Reading the slot through a raw pointer defeats the `NonNull` niche and
//! restores exactly the guard `PyVisit::call` intends to provide.

use std::os::raw::{c_int, c_void};

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::pyclass::{PyTraverseError, PyVisit};
use pyo3::types::PyType;

/// Visit a `Py<T>` field from a `__traverse__` implementation.
///
/// Skips the field when the slot is still null, which is the state the cycle
/// collector can observe between `tp_alloc` and pyo3 writing the Rust
/// contents. See the module docs for why `PyVisit::call` cannot do this on
/// its own.
#[inline]
pub fn visit_py_field<T>(visit: &PyVisit<'_>, field: &Py<T>) -> Result<(), PyTraverseError> {
    // SAFETY: `Py<T>` is `#[repr(transparent)]` over `NonNull<ffi::PyObject>`, so the
    // field occupies exactly one pointer-sized word that is valid to read as a raw
    // pointer for *any* bit pattern — including the all-zero pre-initialization state
    // this guard exists for. No reference to the pointee is formed.
    let raw = unsafe { *(field as *const Py<T>).cast::<*mut ffi::PyObject>() };
    if raw.is_null() {
        return Ok(());
    }
    PyVisit::call(visit, field)
}

// ---------------------------------------------------------------------------
// Conformance oracle
// ---------------------------------------------------------------------------

#[repr(C)]
struct VisitCounters {
    visits: usize,
    nulls: usize,
}

/// `visitproc` that records what `tp_traverse` hands out instead of touching
/// refcounts. Unlike CPython's `visit_decref` it tolerates a null pointer, so
/// the oracle can *report* the defect rather than crash on it.
unsafe extern "C" fn counting_visit(op: *mut ffi::PyObject, arg: *mut c_void) -> c_int {
    // SAFETY: `arg` is the `&mut VisitCounters` handed to `tp_traverse` below,
    // and `tp_traverse` runs synchronously on this thread.
    let counters = unsafe { &mut *arg.cast::<VisitCounters>() };
    counters.visits += 1;
    if op.is_null() {
        counters.nulls += 1;
    }
    0
}

/// Run `ty`'s `tp_traverse` against a stand-in instance whose Rust contents are
/// still all-zero and report `(visits, null_visits)`.
///
/// `null_visits` must be 0 for every doeff pyclass: a null pointer reaching
/// CPython's `visit_decref` dereferences `Py_TYPE(NULL)` and segfaults.
///
/// The stand-in lives in a local buffer, is never GC-tracked and is never sent
/// through `tp_dealloc`, so this cannot disturb interpreter state.
pub fn traverse_zeroed_visits(ty: &Bound<'_, PyType>) -> (usize, usize) {
    let tp = ty.as_type_ptr();
    // SAFETY: `tp` is a live type object for as long as `ty` is borrowed.
    let (traverse, basicsize) = unsafe { ((*tp).tp_traverse, (*tp).tp_basicsize as usize) };
    let Some(traverse) = traverse else {
        return (0, 0);
    };

    // `u128` backing gives 16-byte alignment, at least as strict as any field
    // pyo3 stores in a pyclass instance.
    let words = basicsize.div_ceil(std::mem::size_of::<u128>()).max(1);
    let mut storage = vec![0u128; words];
    let obj = storage.as_mut_ptr().cast::<ffi::PyObject>();
    let mut counters = VisitCounters { visits: 0, nulls: 0 };
    // SAFETY: `obj` points at `basicsize` zeroed, suitably aligned bytes; `ob_type`
    // is filled in below so `tp_traverse` can read `Py_TYPE(obj)` and walk the base
    // chain, which is all pyo3's traverse trampoline reads from the object header.
    unsafe {
        (*obj).ob_type = tp;
        traverse(
            obj,
            counting_visit,
            (&mut counters as *mut VisitCounters).cast::<c_void>(),
        );
    }
    (counters.visits, counters.nulls)
}

// ---------------------------------------------------------------------------
// Census by type without holding the objects
// ---------------------------------------------------------------------------

#[cfg(Py_3_12)]
unsafe extern "C" {
    /// CPython 3.12+: call `callback(op, arg)` for every object the cycle collector
    /// tracks (the free-threaded build stops the world for the walk; the build with
    /// the GIL walks under the GIL). Iteration stops when the callback returns 0.
    fn PyUnstable_GC_VisitObjects(
        callback: unsafe extern "C" fn(*mut ffi::PyObject, *mut c_void) -> c_int,
        arg: *mut c_void,
    );
}

/// The visitor of [`gc_type_counts`]: count `op` under its type. It reads the
/// type pointer only — no reference to `op` is taken, and it runs no Python code
/// (the free-threaded build runs it with every other thread stopped). The first
/// time a type is met it takes one reference to the type, so the type outlives
/// the walk while the dict of the answer is built.
#[cfg(Py_3_12)]
unsafe extern "C" fn count_by_type(op: *mut ffi::PyObject, arg: *mut c_void) -> c_int {
    // SAFETY: `arg` is the `&mut HashMap` handed to `PyUnstable_GC_VisitObjects`
    // below, which calls this synchronously on the calling thread; `op` is a live
    // object for the duration of the call.
    let counts = unsafe { &mut *arg.cast::<std::collections::HashMap<usize, usize>>() };
    let ty = unsafe { ffi::Py_TYPE(op) };
    match counts.entry(ty as usize) {
        std::collections::hash_map::Entry::Occupied(mut seen) => *seen.get_mut() += 1,
        std::collections::hash_map::Entry::Vacant(first) => {
            // SAFETY: `ty` is the live type of a live object.
            unsafe { ffi::Py_INCREF(ty.cast::<ffi::PyObject>()) };
            first.insert(1);
        }
    }
    1
}

/// Count the objects the cycle collector tracks, by type — the same population as
/// `gc.get_objects()` (objects frozen by `gc.freeze()` are not in it) — without
/// building the list of them.
///
/// `gc.get_objects()` holds a strong reference to every object until the list is
/// dropped. Taken from a thread other than the one using an object, the list can
/// keep alive a temporary the code relies on dying at once (asyncio's
/// `StreamReader.readexactly` takes `bytes(memoryview(buf)[:n])` then
/// `del buf[:n]`, which raises `BufferError` while the memoryview lives). Taking
/// the list in a forked copy avoids that, but the copy writes the reference count
/// of every listed object and so copies the pages that hold them: a census of 2M
/// tracked objects copied 140 MiB (38% of the process) locally and 280 MiB within
/// two seconds in a production job, which the memory watch rightly counted as
/// memory. This walk holds nothing past the visit and copies nothing.
#[cfg(Py_3_12)]
pub fn gc_type_counts(py: Python<'_>) -> PyResult<Bound<'_, pyo3::types::PyDict>> {
    let mut counts: std::collections::HashMap<usize, usize> =
        std::collections::HashMap::with_capacity(4096);
    // SAFETY: the caller holds the thread state (`py`); the visitor touches only
    // `counts` and the type it reads, and returns 1 to walk every object.
    unsafe {
        PyUnstable_GC_VisitObjects(
            count_by_type,
            (&mut counts as *mut std::collections::HashMap<usize, usize>).cast::<c_void>(),
        );
    }
    let answer = pyo3::types::PyDict::new(py);
    for (ty, count) in counts {
        // SAFETY: the visitor took one reference to `ty` when it first met it; the
        // bound object takes that reference over and releases it when dropped.
        let ty = unsafe { Bound::from_owned_ptr(py, ty as *mut ffi::PyObject) };
        answer.set_item(ty, count)?;
    }
    Ok(answer)
}

/// CPython 3.11 has no call that walks the tracked objects without listing them.
#[cfg(not(Py_3_12))]
pub fn gc_type_counts(_py: Python<'_>) -> PyResult<Bound<'_, pyo3::types::PyDict>> {
    Err(pyo3::exceptions::PyNotImplementedError::new_err(
        "gc_type_counts needs CPython 3.12+ (PyUnstable_GC_VisitObjects)",
    ))
}
