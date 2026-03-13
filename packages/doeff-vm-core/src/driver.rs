//! Driver/event-loop state and exceptions.

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;
use crate::error::VMError;
use crate::py_shared::PyShared;
use crate::python_call::PythonCall;
use crate::value::Value;

#[pyclass(frozen, name = "Ok")]
pub struct PyResultOk {
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyResultOk {
    #[new]
    fn new(value: Py<PyAny>) -> Self {
        Self { value }
    }

    #[classattr]
    fn __match_args__() -> (&'static str,) {
        ("value",)
    }

    #[getter]
    fn value(&self, py: Python<'_>) -> Py<PyAny> {
        self.value.clone_ref(py)
    }

    fn is_ok(&self) -> bool {
        true
    }

    fn is_err(&self) -> bool {
        false
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let val_repr = self.value.bind(py).repr()?.to_string();
        Ok(format!("Ok({val_repr})"))
    }

    fn __bool__(&self) -> bool {
        true
    }
}

#[pyclass(frozen, name = "Err")]
pub struct PyResultErr {
    pub error: Py<PyAny>,
    pub captured_traceback: Py<PyAny>,
}

#[pymethods]
impl PyResultErr {
    #[new]
    #[pyo3(signature = (error, captured_traceback=None))]
    fn new(py: Python<'_>, error: Py<PyAny>, captured_traceback: Option<Py<PyAny>>) -> Self {
        Self {
            error,
            captured_traceback: captured_traceback.unwrap_or_else(|| py.None()),
        }
    }

    #[classattr]
    fn __match_args__() -> (&'static str,) {
        ("error",)
    }

    #[getter]
    fn error(&self, py: Python<'_>) -> Py<PyAny> {
        self.error.clone_ref(py)
    }

    #[getter]
    fn captured_traceback(&self, py: Python<'_>) -> Py<PyAny> {
        self.captured_traceback.clone_ref(py)
    }

    fn is_ok(&self) -> bool {
        false
    }

    fn is_err(&self) -> bool {
        true
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let err_repr = self.error.bind(py).repr()?.to_string();
        Ok(format!("Err({err_repr})"))
    }

    fn __bool__(&self) -> bool {
        false
    }
}

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

impl From<PyErr> for PyException {
    fn from(err: PyErr) -> Self {
        Python::attach(|py| {
            let exc_type = err.get_type(py).into_any().unbind();
            let exc_value = err.value(py).clone().into_any().unbind();
            let exc_tb = err.traceback(py).map(|tb| tb.into_any().unbind());
            PyException::new(exc_type, exc_value, exc_tb)
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
