//! Kleisli arrow types for IR-level callables (SPEC-VM-017).

use std::sync::Arc;

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;
use crate::error::VMError;
use crate::py_shared::PyShared;
use crate::value::Value;

/// Debug metadata for a Kleisli arrow.
#[derive(Debug, Clone)]
pub struct KleisliDebugInfo {
    pub name: String,
    pub file: Option<String>,
    pub line: Option<u32>,
}

/// IR-level callable: T -> DoExpr[U]
///
/// A Kleisli arrow takes arguments and produces a DoExpr (computation)
/// that the VM evaluates. This is the IR's concept of a "function into
/// computations" - the same concept as FlatMap's binder.
///
/// SPEC-VM-017 R1-A.
pub trait Kleisli: std::fmt::Debug + Send + Sync {
    /// Apply the arrow to arguments, producing a DoCtrl to evaluate.
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError>;

    /// Debug metadata for tracing/error reporting.
    fn debug_info(&self) -> KleisliDebugInfo;

    /// Optional Python identity for handler self-exclusion (OCaml semantics).
    fn py_identity(&self) -> Option<PyShared> {
        None
    }
}

/// Shared reference to a Kleisli arrow.
pub type KleisliRef = Arc<dyn Kleisli>;
