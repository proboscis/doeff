//! Value — the universal type that flows through the VM.
//!
//! The VM is language-agnostic. Python objects are `Opaque` — the VM
//! carries them but does not interpret them. The Python bridge (pyvm.rs)
//! converts between Python and Value at the boundary.

use std::sync::Arc;

use crate::continuation::Continuation;
use crate::error::VMError;
use crate::ids::VarId;
use crate::ir_stream::IRStreamRef;
use crate::py_shared::PyShared;

/// Something the VM can call with Apply.
/// Python callables and Rust handlers both implement this.
/// The VM doesn't know which — it just calls.
pub trait Callable: Send + Sync + std::fmt::Debug {
    /// Call with args, return a Value. Used by Apply.
    fn call(&self, args: Vec<Value>) -> Result<Value, VMError>;

    /// Call as effect handler: returns a DoCtrl to evaluate.
    /// The handler callable MUST return a DoExpr. Anything else is an error.
    fn call_handler(&self, args: Vec<Value>) -> Result<crate::do_ctrl::DoCtrl, VMError> {
        Err(VMError::type_error("callable does not support call_handler"))
    }
}

pub type CallableRef = Arc<dyn Callable>;

/// A value that flows through the VM.
///
/// Language-agnostic. No Python-specific variants.
#[derive(Debug)]
pub enum Value {
    /// The unit value.
    Unit,
    /// Integer.
    Int(i64),
    /// Boolean.
    Bool(bool),
    /// String.
    String(String),
    /// No value (Python None equivalent).
    None,

    /// A callable function/closure.
    Callable(CallableRef),
    /// A running generator/program stream.
    Stream(IRStreamRef),
    /// A detached fiber chain (move-only, one-shot).
    Continuation(Continuation),
    /// A ref cell handle.
    Var(VarId),
    /// A list of values.
    List(Vec<Value>),

    /// Opaque foreign object (Python, etc). VM does not interpret this.
    /// The bridge layer converts to/from Opaque at the VM boundary.
    Opaque(PyShared),
}

impl Clone for Value {
    fn clone(&self) -> Self {
        match self {
            Value::Unit => Value::Unit,
            Value::Int(i) => Value::Int(*i),
            Value::Bool(b) => Value::Bool(*b),
            Value::String(s) => Value::String(s.clone()),
            Value::None => Value::None,
            Value::Var(var) => Value::Var(*var),
            Value::List(list) => Value::List(list.clone()),
            Value::Opaque(obj) => Value::Opaque(obj.clone()),
            Value::Continuation(_) => panic!(
                "Value::Continuation must not be cloned — use move semantics (SPEC-VM-021)"
            ),
            Value::Callable(_) => panic!(
                "Value::Callable must not be cloned — move or Arc::clone the CallableRef"
            ),
            Value::Stream(_) => panic!(
                "Value::Stream must not be cloned — move semantics"
            ),
        }
    }
}
