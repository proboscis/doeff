//! Driver/event-loop state and exceptions.

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;
use crate::error::VMError;
use crate::py_shared::PyShared;
use crate::python_call::PythonCall;
use crate::value::Value;

#[doc(hidden)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PyExceptionTag {
    HandlerProtocol,
}

#[doc(hidden)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct PyExceptionMetadata {
    synthetic_vm_error: bool,
    tag: Option<PyExceptionTag>,
}

impl PyExceptionMetadata {
    const fn python_origin() -> Self {
        Self {
            synthetic_vm_error: false,
            tag: None,
        }
    }

    pub(crate) const fn synthetic_vm_error() -> Self {
        Self {
            synthetic_vm_error: true,
            tag: None,
        }
    }

    const fn tagged_synthetic_vm_error(tag: PyExceptionTag) -> Self {
        Self {
            synthetic_vm_error: true,
            tag: Some(tag),
        }
    }
}

#[derive(Debug, Clone)]
pub enum PyException {
    Materialized {
        exc_type: PyShared,
        exc_value: PyShared,
        exc_tb: Option<PyShared>,
        metadata: PyExceptionMetadata,
    },
    RuntimeError {
        message: String,
        metadata: PyExceptionMetadata,
    },
    TypeError {
        message: String,
        metadata: PyExceptionMetadata,
    },
}

#[derive(Debug)]
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
        Self::new_with_metadata(
            exc_type,
            exc_value,
            exc_tb,
            PyExceptionMetadata::python_origin(),
        )
    }

    pub(crate) fn new_with_metadata(
        exc_type: Py<PyAny>,
        exc_value: Py<PyAny>,
        exc_tb: Option<Py<PyAny>>,
        metadata: PyExceptionMetadata,
    ) -> Self {
        PyException::Materialized {
            exc_type: PyShared::new(exc_type),
            exc_value: PyShared::new(exc_value),
            exc_tb: exc_tb.map(PyShared::new),
            metadata,
        }
    }

    pub fn runtime_error(message: impl Into<String>) -> Self {
        Self::runtime_error_with_metadata(message, PyExceptionMetadata::synthetic_vm_error())
    }

    pub fn handler_protocol_error(message: impl Into<String>) -> Self {
        Self::runtime_error_with_metadata(
            message,
            PyExceptionMetadata::tagged_synthetic_vm_error(PyExceptionTag::HandlerProtocol),
        )
    }

    fn runtime_error_with_metadata(
        message: impl Into<String>,
        metadata: PyExceptionMetadata,
    ) -> Self {
        PyException::RuntimeError {
            message: message.into(),
            metadata,
        }
    }

    pub fn type_error(message: impl Into<String>) -> Self {
        Self::type_error_with_metadata(message, PyExceptionMetadata::synthetic_vm_error())
    }

    fn type_error_with_metadata(message: impl Into<String>, metadata: PyExceptionMetadata) -> Self {
        PyException::TypeError {
            message: message.into(),
            metadata,
        }
    }

    pub fn value_clone_ref(&self, py: Python<'_>) -> Py<PyAny> {
        match self {
            PyException::Materialized { exc_value, .. } => exc_value.clone_ref(py),
            PyException::RuntimeError { message, .. } => {
                pyo3::exceptions::PyRuntimeError::new_err(message.clone())
                    .value(py)
                    .clone()
                    .into_any()
                    .unbind()
            }
            PyException::TypeError { message, .. } => {
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
                metadata,
            } => PyException::Materialized {
                exc_type: PyShared::new(exc_type.clone_ref(py)),
                exc_value: PyShared::new(exc_value.clone_ref(py)),
                exc_tb: exc_tb.as_ref().map(|tb| PyShared::new(tb.clone_ref(py))),
                metadata: *metadata,
            },
            PyException::RuntimeError { message, metadata } => PyException::RuntimeError {
                message: message.clone(),
                metadata: *metadata,
            },
            PyException::TypeError { message, metadata } => PyException::TypeError {
                message: message.clone(),
                metadata: *metadata,
            },
        }
    }

    pub(crate) fn metadata(&self) -> PyExceptionMetadata {
        match self {
            PyException::Materialized { metadata, .. }
            | PyException::RuntimeError { metadata, .. }
            | PyException::TypeError { metadata, .. } => *metadata,
        }
    }

    pub(crate) fn with_metadata(self, metadata: PyExceptionMetadata) -> Self {
        match self {
            PyException::Materialized {
                exc_type,
                exc_value,
                exc_tb,
                ..
            } => PyException::Materialized {
                exc_type,
                exc_value,
                exc_tb,
                metadata,
            },
            PyException::RuntimeError { message, .. } => {
                PyException::RuntimeError { message, metadata }
            }
            PyException::TypeError { message, .. } => PyException::TypeError { message, metadata },
        }
    }

    pub(crate) fn is_synthetic_vm_error(&self) -> bool {
        self.metadata().synthetic_vm_error
    }

    pub(crate) fn is_materialized_synthetic_vm_error(&self) -> bool {
        matches!(self, PyException::Materialized { .. }) && self.is_synthetic_vm_error()
    }

    pub(crate) fn is_handler_protocol_exception(&self) -> bool {
        self.metadata().tag == Some(PyExceptionTag::HandlerProtocol)
    }

    pub(crate) fn requires_safe_error_context_dispatch(&self) -> bool {
        self.is_synthetic_vm_error() || self.is_handler_protocol_exception()
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

impl From<PyErr> for PyException {
    fn from(err: PyErr) -> Self {
        Python::attach(|py| {
            let exc_type = err.get_type(py).into_any().unbind();
            let exc_value = err.value(py).clone().into_any().unbind();
            let exc_tb = err.traceback(py).map(|tb| tb.into_any().unbind());
            PyException::new_with_metadata(
                exc_type,
                exc_value,
                exc_tb,
                PyExceptionMetadata::python_origin(),
            )
        })
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

#[cfg(test)]
mod tests {
    use super::*;

    use crate::trace_state::TraceState;

    #[test]
    fn handler_protocol_metadata_survives_materialization() {
        let exception = PyException::handler_protocol_error(
            "handler returned without consuming continuation 1",
        );
        let enriched = TraceState::ensure_execution_context(exception);

        assert!(enriched.is_handler_protocol_exception());
        assert!(enriched.is_synthetic_vm_error());
        assert!(TraceState::has_execution_context(&enriched));
    }
}
