use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;

/// VM protocol wrapper that carries generator metadata and frame resolver callback.
#[pyclass(frozen, name = "DoeffGenerator")]
#[derive(Debug, Clone)]
pub struct DoeffGenerator {
    #[pyo3(get)]
    pub generator: Py<PyAny>,
    #[pyo3(get)]
    pub function_name: String,
    #[pyo3(get)]
    pub source_file: String,
    #[pyo3(get)]
    pub source_line: u32,
    #[pyo3(get)]
    pub get_frame: Py<PyAny>,
}

#[pymethods]
impl DoeffGenerator {
    #[new]
    #[pyo3(signature = (generator, function_name, source_file, source_line, get_frame))]
    fn new(
        py: Python<'_>,
        generator: Py<PyAny>,
        function_name: String,
        source_file: String,
        source_line: u32,
        get_frame: Py<PyAny>,
    ) -> PyResult<Self> {
        if !get_frame.bind(py).is_callable() {
            return Err(PyTypeError::new_err(
                "DoeffGenerator.get_frame must be callable",
            ));
        }
        Ok(Self {
            generator,
            function_name,
            source_file,
            source_line,
            get_frame,
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "DoeffGenerator(function_name={:?}, source_file={:?}, source_line={})",
            self.function_name, self.source_file, self.source_line
        )
    }

    #[getter]
    fn __doeff_inner__(&self, py: Python<'_>) -> Py<PyAny> {
        self.generator.clone_ref(py)
    }
}
