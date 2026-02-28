//! Kleisli arrow types for IR-level callables (SPEC-VM-017).

use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::do_ctrl::DoCtrl;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::IRStreamFactoryRef;
use crate::ir_stream::{IRStreamRef, PythonGeneratorStream};
use crate::py_shared::PyShared;
use crate::value::Value;

/// Debug metadata for a Kleisli arrow.
#[derive(Debug, Clone)]
pub struct KleisliDebugInfo {
    pub name: String,
    pub file: Option<String>,
    pub line: Option<u32>,
}

/// IR-level callable: T -> DoExpr[U]
///
/// A Kleisli arrow takes arguments and produces a DoExpr (computation)
/// that the VM evaluates. This is the IR's concept of a "function into
/// computations" - the same concept as FlatMap's binder.
///
/// SPEC-VM-017 R1-A.
pub trait Kleisli: std::fmt::Debug + Send + Sync {
    /// Apply the arrow to arguments, producing a DoCtrl to evaluate.
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError>;

    /// Debug metadata for tracing/error reporting.
    fn debug_info(&self) -> KleisliDebugInfo;

    /// Optional Python identity for handler self-exclusion (OCaml semantics).
    fn py_identity(&self) -> Option<PyShared> {
        None
    }
}

/// Shared reference to a Kleisli arrow.
pub type KleisliRef = Arc<dyn Kleisli>;

// ---------------------------------------------------------------------------
// PyKleisli — Python-callable Kleisli arrow (#[pyclass])
// ---------------------------------------------------------------------------

/// Wraps a Python callable (the @do-decorated generator factory function)
/// and implements the Kleisli trait. When the VM calls kleisli.apply(py, args),
/// it calls the Python callable with args to get a generator, wraps it in a
/// PythonGeneratorStream, and returns DoCtrl::IRStream.
#[pyclass(name = "PyKleisli")]
#[derive(Debug, Clone)]
pub struct PyKleisli {
    pub(crate) func: PyShared,
    pub(crate) name: String,
    pub(crate) file: Option<String>,
    pub(crate) line: Option<u32>,
}

#[pymethods]
impl PyKleisli {
    #[new]
    fn new(func: Py<PyAny>, name: String, file: Option<String>, line: Option<u32>) -> Self {
        PyKleisli {
            func: PyShared::new(func),
            name,
            file,
            line,
        }
    }

    /// __call__ makes it callable from Python — calling PyKleisli(*args) invokes the
    /// wrapped function. This is needed so PyKleisli can be used as a drop-in replacement
    /// for KleisliProgram in Python code.
    #[pyo3(signature = (*args, **kwargs))]
    fn __call__(
        &self,
        py: Python<'_>,
        args: &Bound<'_, PyTuple>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        self.func.bind(py).call(args, kwargs).map(|r| r.unbind())
    }

    fn __repr__(&self) -> String {
        format!(
            "PyKleisli({}, {}:{})",
            self.name,
            self.file.as_deref().unwrap_or("?"),
            self.line.unwrap_or(0)
        )
    }

    /// Expose the wrapped function for Python introspection.
    #[getter]
    fn func(&self, py: Python<'_>) -> Py<PyAny> {
        self.func.clone_ref(py)
    }

    #[getter]
    fn name(&self) -> &str {
        &self.name
    }

    #[getter]
    fn file(&self) -> Option<&str> {
        self.file.as_deref()
    }

    #[getter]
    fn line(&self) -> Option<u32> {
        self.line
    }
}

impl PyKleisli {
    /// Create a PyKleisli from a Python handler callable (used by PythonHandler migration).
    pub fn from_handler(callable: Py<PyAny>, name: String, file: Option<String>, line: Option<u32>) -> Self {
        PyKleisli {
            func: PyShared::new(callable),
            name,
            file,
            line,
        }
    }

    /// Get a reference to the inner function as PyShared.
    pub fn func_shared(&self) -> &PyShared {
        &self.func
    }
}

impl Kleisli for PyKleisli {
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        let py_args: Vec<Bound<'_, PyAny>> = args
            .iter()
            .map(|v| v.to_pyobject(py))
            .collect::<PyResult<Vec<_>>>()
            .map_err(|e| VMError::python_error(format!("{e}")))?;
        let tuple = PyTuple::new(py, &py_args)
            .map_err(|e| VMError::python_error(format!("{e}")))?;

        let generator = self
            .func
            .bind(py)
            .call1(tuple)
            .map_err(|e| VMError::python_error(format!("{e}")))?;

        let get_frame_fn = py
            .eval(
                c"lambda gen: getattr(gen, 'gi_frame', None)",
                None,
                None,
            )
            .map_err(|e| VMError::python_error(format!("failed to create get_frame: {e}")))?;

        let stream = PythonGeneratorStream::new(
            PyShared::new(generator.unbind()),
            PyShared::new(get_frame_fn.unbind()),
        );
        let stream_ref: IRStreamRef = Arc::new(Mutex::new(Box::new(stream)));

        let metadata = CallMetadata::new(
            self.name.clone(),
            self.file.clone().unwrap_or_default(),
            self.line.unwrap_or(0),
            None,
            None,
        );

        Ok(DoCtrl::IRStream {
            stream: stream_ref,
            metadata: Some(metadata),
        })
    }

    fn debug_info(&self) -> KleisliDebugInfo {
        KleisliDebugInfo {
            name: self.name.clone(),
            file: self.file.clone(),
            line: self.line,
        }
    }

    fn py_identity(&self) -> Option<PyShared> {
        Some(self.func.clone())
    }
}

// ---------------------------------------------------------------------------
// RustKleisli — Rust handler factory Kleisli arrow
// ---------------------------------------------------------------------------

/// Wraps an IRStreamFactory (Rust handler program factory) and implements Kleisli.
/// This allows Rust built-in handlers to be represented as Value::Kleisli in future phases.
#[derive(Debug, Clone)]
pub struct RustKleisli {
    factory: IRStreamFactoryRef,
    name: String,
}

impl RustKleisli {
    pub fn new(factory: IRStreamFactoryRef, name: String) -> Self {
        RustKleisli { factory, name }
    }

    pub fn factory(&self) -> &IRStreamFactoryRef {
        &self.factory
    }
}

impl Kleisli for RustKleisli {
    fn apply(&self, _py: Python<'_>, _args: Vec<Value>) -> Result<DoCtrl, VMError> {
        // RustKleisli expects (effect: DispatchEffect, k: Continuation) as args.
        // For now, this is a placeholder — actual integration happens in Phase 3
        // when WithHandler uses Kleisli for dispatch.
        Err(VMError::internal(
            "RustKleisli.apply() not yet wired — use IRStreamFactory directly until Phase 3",
        ))
    }

    fn debug_info(&self) -> KleisliDebugInfo {
        KleisliDebugInfo {
            name: self.name.clone(),
            file: None,
            line: None,
        }
    }
}
