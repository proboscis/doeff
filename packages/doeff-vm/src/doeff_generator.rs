use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

/// Typed generator factory carrying metadata and frame resolver callback.
#[pyclass(frozen, name = "DoeffGeneratorFn")]
#[derive(Debug, Clone)]
pub struct DoeffGeneratorFn {
    #[pyo3(get)]
    pub callable: Py<PyAny>,
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
impl DoeffGeneratorFn {
    #[new]
    #[pyo3(signature = (callable, function_name, source_file, source_line, get_frame))]
    fn new(
        py: Python<'_>,
        callable: Py<PyAny>,
        function_name: String,
        source_file: String,
        source_line: u32,
        get_frame: Py<PyAny>,
    ) -> PyResult<Self> {
        if !callable.bind(py).is_callable() {
            return Err(PyTypeError::new_err(
                "DoeffGeneratorFn.callable must be callable",
            ));
        }
        if !get_frame.bind(py).is_callable() {
            return Err(PyTypeError::new_err(
                "DoeffGeneratorFn.get_frame must be callable",
            ));
        }
        Ok(Self {
            callable,
            function_name,
            source_file,
            source_line,
            get_frame,
        })
    }

    #[pyo3(signature = (*args, **kwargs))]
    fn __call__(
        slf: PyRef<'_, Self>,
        py: Python<'_>,
        args: &Bound<'_, PyTuple>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<DoeffGenerator> {
        let handler_name = slf.function_name.clone();
        let callable = slf.callable.clone_ref(py);
        let produced = match kwargs {
            Some(kwargs) => callable.bind(py).call(args, Some(kwargs))?,
            None => callable.bind(py).call1(args)?,
        };

        if produced.is_instance_of::<DoeffGenerator>() {
            return Ok(produced.extract::<DoeffGenerator>()?);
        }

        let is_generator_like = produced.hasattr("__next__")?
            && produced.hasattr("send")?
            && produced.hasattr("throw")?;
        if !is_generator_like {
            let ty = produced
                .get_type()
                .name()
                .map(|n| n.to_string())
                .unwrap_or_else(|_| "<unknown>".to_string());
            return Err(PyTypeError::new_err(format!(
                "Handler {handler_name} must return a generator, got {ty}. Did you forget 'yield'?"
            )));
        }

        let factory_ref: Py<DoeffGeneratorFn> = slf.into();
        DoeffGenerator::from_factory(py, produced.unbind(), factory_ref)
    }
}

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
    #[pyo3(get)]
    pub factory: Option<Py<DoeffGeneratorFn>>,
}

#[pymethods]
impl DoeffGenerator {
    #[new]
    #[pyo3(signature = (generator, function_name=None, source_file=None, source_line=None, get_frame=None, factory=None))]
    fn new(
        py: Python<'_>,
        generator: Py<PyAny>,
        function_name: Option<String>,
        source_file: Option<String>,
        source_line: Option<u32>,
        get_frame: Option<Py<PyAny>>,
        factory: Option<Py<DoeffGeneratorFn>>,
    ) -> PyResult<Self> {
        let (default_function_name, default_source_file, default_source_line, default_get_frame) =
            if let Some(factory_ref) = &factory {
                let factory_borrow = factory_ref.bind(py).borrow();
                (
                    Some(factory_borrow.function_name.clone()),
                    Some(factory_borrow.source_file.clone()),
                    Some(factory_borrow.source_line),
                    Some(factory_borrow.get_frame.clone_ref(py)),
                )
            } else {
                (None, None, None, None)
            };

        let function_name = function_name.or(default_function_name).ok_or_else(|| {
            PyTypeError::new_err("DoeffGenerator.function_name is required when factory is absent")
        })?;
        let source_file = source_file.or(default_source_file).ok_or_else(|| {
            PyTypeError::new_err("DoeffGenerator.source_file is required when factory is absent")
        })?;
        let source_line = source_line.or(default_source_line).ok_or_else(|| {
            PyTypeError::new_err("DoeffGenerator.source_line is required when factory is absent")
        })?;
        let get_frame = get_frame.or(default_get_frame).ok_or_else(|| {
            PyTypeError::new_err("DoeffGenerator.get_frame is required when factory is absent")
        })?;

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
            factory,
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

impl DoeffGenerator {
    pub fn from_factory(
        py: Python<'_>,
        generator: Py<PyAny>,
        factory: Py<DoeffGeneratorFn>,
    ) -> PyResult<Self> {
        let factory_borrow = factory.bind(py).borrow();
        let get_frame = factory_borrow.get_frame.clone_ref(py);
        if !get_frame.bind(py).is_callable() {
            return Err(PyTypeError::new_err(
                "DoeffGeneratorFn.get_frame must be callable",
            ));
        }
        Ok(Self {
            generator,
            function_name: factory_borrow.function_name.clone(),
            source_file: factory_borrow.source_file.clone(),
            source_line: factory_borrow.source_line,
            get_frame,
            factory: Some(factory.clone_ref(py)),
        })
    }
}

#[cfg(test)]
mod tests {
    use pyo3::types::PyDict;

    use super::*;

    #[test]
    fn test_doeff_generator_fn_call_wraps_result_with_factory_back_pointer() {
        Python::attach(|py| {
            let locals = PyDict::new(py);
            py.run(
                c"def build_gen():\n    yield 1\n\ndef get_frame(g):\n    return g.gi_frame\n",
                Some(&locals),
                Some(&locals),
            )
            .expect("failed to define generator factory fixtures");

            let callable = locals
                .get_item("build_gen")
                .expect("locals.get_item failed")
                .expect("build_gen missing")
                .unbind();
            let get_frame = locals
                .get_item("get_frame")
                .expect("locals.get_item failed")
                .expect("get_frame missing")
                .unbind();

            let factory = Bound::new(
                py,
                DoeffGeneratorFn::new(
                    py,
                    callable,
                    "build_gen".to_string(),
                    "/tmp/sample.py".to_string(),
                    12,
                    get_frame,
                )
                .expect("failed to build DoeffGeneratorFn"),
            )
            .expect("failed to bind factory");

            let wrapped: PyRef<'_, DoeffGenerator> = factory
                .call0()
                .expect("factory call failed")
                .extract()
                .expect("expected DoeffGenerator result");

            assert_eq!(wrapped.function_name, "build_gen");
            assert_eq!(wrapped.source_file, "/tmp/sample.py");
            assert_eq!(wrapped.source_line, 12);
            let factory_back = wrapped
                .factory
                .as_ref()
                .expect("missing factory back-pointer");
            assert_eq!(factory_back.bind(py).as_ptr(), factory.as_any().as_ptr());
        });
    }
}
