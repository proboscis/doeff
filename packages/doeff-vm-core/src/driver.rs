//! VM driver types — Mode, StepResult, and error representation.
//!
//! Language-agnostic. No Python types at the VM level.
//! Exceptions are Values (opaque to the VM).

use crate::do_ctrl::DoCtrl;
use crate::error::VMError;
use crate::value::Value;

/// What the VM should do next.
#[derive(Debug)]
pub enum Mode {
    /// Evaluate a DoCtrl instruction.
    Eval(DoCtrl),
    /// Send a value to the current stream (stream.resume(value)).
    Send(Value),
    /// Signal an error to the current stream (stream.throw(error)).
    /// The error is a Value (opaque to the VM — could be a Python exception, a string, etc.)
    Raise(Value),
}

/// What a single VM step produces.
#[derive(Debug)]
pub enum StepResult {
    /// More steps needed. Call step() again.
    Continue,
    /// Execution complete. Here is the final value.
    Done(Value),
    /// Execution failed with a VM-level error.
    Error(VMError),
    /// VM needs an external computation.
    /// The driver should execute the call and feed the result back via receive_external_result().
    External(ExternalCall),
}

/// An external computation the VM cannot perform itself.
/// The driver (which has access to Python/GIL/etc.) executes this and returns the result.
#[derive(Debug)]
pub struct ExternalCall {
    /// Opaque callable — the driver knows how to interpret this.
    pub callable: Value,
    /// Arguments to pass.
    pub args: Vec<Value>,
    /// What to do with the result — deliver, raise, or eval.
    pub on_result: ExternalCallContinuation,
}

/// How to process the result of an external call.
#[derive(Debug)]
pub enum ExternalCallContinuation {
    /// Deliver the result value to the current stream.
    Deliver,
    /// Evaluate the result as a DoCtrl.
    Eval,
}

impl Mode {
    pub fn send(value: Value) -> Self {
        Mode::Send(value)
    }

    pub fn raise(error: Value) -> Self {
        Mode::Raise(error)
    }

    pub fn eval(doctrl: DoCtrl) -> Self {
        Mode::Eval(doctrl)
    }
}

impl StepResult {
    pub fn is_done(&self) -> bool {
        matches!(self, StepResult::Done(_))
    }

    pub fn is_error(&self) -> bool {
        matches!(self, StepResult::Error(_))
    }

    pub fn is_external(&self) -> bool {
        matches!(self, StepResult::External(_))
    }
}
