//! Effect types for the VM.
//!
//! An effect is an opaque Value to the VM. The VM carries it to the handler
//! but does not interpret it.

use crate::value::Value;

/// An effect dispatched through the handler chain.
/// Opaque to the VM — the handler decides what it means.
pub type DispatchEffect = Value;
