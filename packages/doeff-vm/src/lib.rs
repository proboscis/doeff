//! doeff-vm: Python bridge for the language-agnostic VM.
//!
//! This crate connects Python (via PyO3) to doeff-vm-core.
//! It provides:
//!   - PythonGeneratorStream: Python generator → IRStream adapter
//!   - classify_yielded: Python object → DoCtrl conversion
//!   - Value ↔ Python conversion
//!   - run() entry point

use pyo3::prelude::*;

pub mod do_expr;
pub mod python_generator_stream;
pub mod pyvm;
pub mod result;

// Re-export VM core types
pub use doeff_vm_core::{
    Continuation, DoCtrl, FiberId, Frame, IRStream, IRStreamRef, Marker, Mode, SegmentId,
    StepResult, StreamStep, Value, VarId, VarStore, VMError, VM,
};
pub use doeff_vm_core::value::{Callable, CallableRef};
pub use doeff_vm_core::segment::Fiber;
pub use doeff_vm_core::continuation::{OwnedControlContinuation, PendingContinuation, PyK};

#[pymodule]
fn doeff_vm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyvm::register_pyvm(m)?;

    /// Return (live_segments, live_continuations, live_ir_streams).
    #[pyfn(m)]
    fn vm_live_counts() -> (usize, usize, usize) {
        let c = doeff_vm_core::memory_stats::live_object_counts();
        (c.live_segments, c.live_continuations, c.live_ir_streams)
    }

    Ok(())
}
