//! Driver/event-loop state and exceptions.

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;
use crate::error::VMError;
use crate::py_shared::PyShared;
use crate::python_call::PythonCall;
use crate::value::Value;

#[derive(Debug, Clone)]
pub enum PyException {
    Materialized {
        exc_type: PyShared,
        exc_value: PyShared,
        exc_tb: Option<PyShared>,
    },
    RuntimeError {
        message: String,
    },
    TypeError {
        message: String,
    },
}

#[derive(Debug, Clone)]
pub enum Mode {
    Deliver(Value),
    Throw(PyException),
    HandleYield(DoCtrl),
    Return(Value),
}

#[derive(Debug)]
pub enum StepEvent {
    Continue,
    NeedsPython(PythonCall),
    Done(Value),
    Error(VMError),
}

impl PyException {
    pub fn new(exc_type: Py<PyAny>, exc_value: Py<PyAny>, exc_tb: Option<Py<PyAny>>) -> Self {
        PyException::Materialized {
            exc_type: PyShared::new(exc_type),
            exc_value: PyShared::new(exc_value),
            exc_tb: exc_tb.map(PyShared::new),
        }
    }

    pub fn runtime_error(message: impl Into<String>) -> Self {
        PyException::RuntimeError {
            message: message.into(),
        }
    }

    pub fn type_error(message: impl Into<String>) -> Self {
        PyException::TypeError {
            message: message.into(),
        }
    }

    pub fn value_clone_ref(&self, py: Python<'_>) -> Py<PyAny> {
        match self {
            PyException::Materialized { exc_value, .. } => exc_value.clone_ref(py),
            PyException::RuntimeError { message } => {
                pyo3::exceptions::PyRuntimeError::new_err(message.clone())
                    .value(py)
                    .clone()
                    .into_any()
                    .unbind()
            }
            PyException::TypeError { message } => {
                pyo3::exceptions::PyTypeError::new_err(message.clone())
                    .value(py)
                    .clone()
                    .into_any()
                    .unbind()
            }
        }
    }

    pub fn to_pyerr(&self, py: Python<'_>) -> PyErr {
        PyErr::from_value(self.value_clone_ref(py).bind(py).clone())
    }

    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            PyException::Materialized {
                exc_type,
                exc_value,
                exc_tb,
            } => PyException::Materialized {
                exc_type: PyShared::new(exc_type.clone_ref(py)),
                exc_value: PyShared::new(exc_value.clone_ref(py)),
                exc_tb: exc_tb.as_ref().map(|tb| PyShared::new(tb.clone_ref(py))),
            },
            PyException::RuntimeError { message } => PyException::RuntimeError {
                message: message.clone(),
            },
            PyException::TypeError { message } => PyException::TypeError {
                message: message.clone(),
            },
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
