//! Step state machine types and execution.

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::effect::Effect;
use crate::error::VMError;
use crate::handler::StdlibHandler;
use crate::value::Value;

#[derive(Debug, Clone)]
pub struct PyException {
    pub exc_type: Py<PyAny>,
    pub exc_value: Py<PyAny>,
    pub exc_tb: Option<Py<PyAny>>,
}

#[derive(Debug, Clone)]
pub enum Mode {
    Deliver(Value),
    Throw(PyException),
    HandleYield(Yielded),
    Return(Value),
}

#[derive(Debug)]
pub enum StepEvent {
    Continue,
    NeedsPython(PythonCall),
    Done(Value),
    Error(VMError),
}

#[derive(Debug, Clone)]
pub enum PythonCall {
    StartProgram {
        program: Py<PyAny>,
    },
    CallFunc {
        func: Py<PyAny>,
        args: Vec<Value>,
    },
    CallHandler {
        handler: Py<PyAny>,
        effect: Effect,
        continuation: Continuation,
    },
    GenNext {
        gen: Py<PyAny>,
    },
    GenSend {
        gen: Py<PyAny>,
        value: Value,
    },
    GenThrow {
        gen: Py<PyAny>,
        exc: Py<PyAny>,
    },
}

#[derive(Debug, Clone)]
pub enum PendingPython {
    StartProgramFrame,
    StepUserGenerator {
        generator: Py<PyAny>,
    },
    CallPythonHandler {
        k_user: Continuation,
        effect: Effect,
    },
    StdlibContinuation {
        handler: StdlibHandler,
        k: Continuation,
        context: HandlerContext,
    },
}

#[derive(Debug, Clone)]
pub enum HandlerContext {
    ModifyPending { key: String, old_value: Value },
}

#[derive(Debug, Clone)]
pub enum Yielded {
    Primitive(ControlPrimitive),
    Effect(Effect),
    Program(Py<PyAny>),
    Unknown(Py<PyAny>),
}

#[derive(Debug, Clone)]
pub enum ControlPrimitive {
    Resume { k: Continuation, value: Value },
    Transfer { k: Continuation, value: Value },
    WithHandler { handler: Py<PyAny>, body: Py<PyAny> },
    Delegate,
    GetContinuation,
    Pure(Value),
}

#[derive(Debug, Clone)]
pub enum PyCallOutcome {
    Value(Value),
    GenYield(Yielded),
    GenReturn(Value),
    GenError(PyException),
}

impl PyException {
    pub fn new(exc_type: Py<PyAny>, exc_value: Py<PyAny>, exc_tb: Option<Py<PyAny>>) -> Self {
        PyException {
            exc_type,
            exc_value,
            exc_tb,
        }
    }
}

impl Mode {
    pub fn deliver(value: impl Into<Value>) -> Self {
        Mode::Deliver(value.into())
    }

    pub fn return_value(value: impl Into<Value>) -> Self {
        Mode::Return(value.into())
    }

    pub fn is_deliver(&self) -> bool {
        matches!(self, Mode::Deliver(_))
    }

    pub fn is_throw(&self) -> bool {
        matches!(self, Mode::Throw(_))
    }

    pub fn is_return(&self) -> bool {
        matches!(self, Mode::Return(_))
    }
}

impl StepEvent {
    pub fn is_done(&self) -> bool {
        matches!(self, StepEvent::Done(_))
    }

    pub fn is_error(&self) -> bool {
        matches!(self, StepEvent::Error(_))
    }

    pub fn is_needs_python(&self) -> bool {
        matches!(self, StepEvent::NeedsPython(_))
    }
}

impl Yielded {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            Yielded::Primitive(p) => Yielded::Primitive(p.clone_ref(py)),
            Yielded::Effect(e) => Yielded::Effect(e.clone()),
            Yielded::Program(p) => Yielded::Program(p.clone_ref(py)),
            Yielded::Unknown(p) => Yielded::Unknown(p.clone_ref(py)),
        }
    }
}

impl ControlPrimitive {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            ControlPrimitive::Resume { k, value } => ControlPrimitive::Resume {
                k: k.clone(),
                value: value.clone(),
            },
            ControlPrimitive::Transfer { k, value } => ControlPrimitive::Transfer {
                k: k.clone(),
                value: value.clone(),
            },
            ControlPrimitive::WithHandler { handler, body } => ControlPrimitive::WithHandler {
                handler: handler.clone_ref(py),
                body: body.clone_ref(py),
            },
            ControlPrimitive::Delegate => ControlPrimitive::Delegate,
            ControlPrimitive::GetContinuation => ControlPrimitive::GetContinuation,
            ControlPrimitive::Pure(v) => ControlPrimitive::Pure(v.clone()),
        }
    }
}

impl PyException {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        PyException {
            exc_type: self.exc_type.clone_ref(py),
            exc_value: self.exc_value.clone_ref(py),
            exc_tb: self.exc_tb.as_ref().map(|t| t.clone_ref(py)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mode_deliver() {
        let mode = Mode::deliver(42i64);
        assert!(mode.is_deliver());
        assert!(!mode.is_throw());
        assert!(!mode.is_return());
    }

    #[test]
    fn test_mode_return() {
        let mode = Mode::return_value("done");
        assert!(mode.is_return());
        assert!(!mode.is_deliver());
    }

    #[test]
    fn test_step_event_checks() {
        let done = StepEvent::Done(Value::Int(1));
        assert!(done.is_done());
        assert!(!done.is_error());

        let err = StepEvent::Error(VMError::internal("test"));
        assert!(err.is_error());
        assert!(!err.is_done());

        let cont = StepEvent::Continue;
        assert!(!cont.is_done());
        assert!(!cont.is_error());
    }
}
