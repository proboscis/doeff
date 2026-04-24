//! VM driver types — Signal, StepResult, and error representation.
//!
//! Language-agnostic. No Python types at the VM level.
//! Exceptions are Values (opaque to the VM).

use crate::do_ctrl::DoCtrl;
use crate::error::VMError;
use crate::value::Value;

/// What the VM should do next.
#[derive(Debug)]
pub struct Signal {
    pub action: SignalAction,
    pub error_context: Option<Vec<Value>>,
}

/// The next action for a VM step.
#[derive(Debug)]
pub enum SignalAction {
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
    /// More steps needed. Call step() again with this signal.
    Continue(Signal),
    /// Execution complete. Here is the final value.
    Done(Value),
    /// Execution failed with a VM-level error.
    Error {
        error: VMError,
        context: Option<Vec<Value>>,
    },
    /// VM needs an external computation.
    /// The driver should execute the call and feed the result back as a Signal.
    External {
        call: ExternalCall,
        context: Option<Vec<Value>>,
    },
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

impl Signal {
    pub fn send(value: Value) -> Self {
        Signal {
            action: SignalAction::Send(value),
            error_context: None,
        }
    }

    pub fn raise(error: Value) -> Self {
        Signal {
            action: SignalAction::Raise(error),
            error_context: None,
        }
    }

    pub fn eval(doctrl: DoCtrl) -> Self {
        Signal {
            action: SignalAction::Eval(doctrl),
            error_context: None,
        }
    }

    pub fn with_error_context(mut self, context: Option<Vec<Value>>) -> Self {
        self.error_context = context;
        self
    }

    pub fn from_external_result(result: Result<Value, Value>) -> Self {
        match result {
            Ok(value) => Signal::send(value),
            Err(error) => Signal::raise(error),
        }
    }
}

impl StepResult {
    pub fn is_done(&self) -> bool {
        matches!(self, StepResult::Done(_))
    }

    pub fn is_error(&self) -> bool {
        matches!(self, StepResult::Error { .. })
    }

    pub fn is_external(&self) -> bool {
        matches!(self, StepResult::External { .. })
    }
}
