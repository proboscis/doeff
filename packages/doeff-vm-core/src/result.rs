use pyo3::prelude::*;

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
