//! Kleisli arrow types for IR-level callables (SPEC-VM-017).

use std::any::Any;
use std::sync::{Arc, Mutex};

use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::do_ctrl::DoCtrl;
use crate::doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::{Handler, IRStreamFactoryRef};
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

    /// Downcasting hook for specialized VM behavior.
    fn as_any(&self) -> &dyn Any;

    /// Optional Python identity for handler self-exclusion (OCaml semantics).
    fn py_identity(&self) -> Option<PyShared> {
        None
    }
}

/// Shared reference to a Kleisli arrow.
pub type KleisliRef = Arc<dyn Kleisli>;

/// Python-backed Kleisli arrow.
///
/// `func` remains callable from Python (`__call__`) so this can serve as a
/// drop-in replacement for `KleisliProgram` at call sites.
#[pyclass(name = "PyKleisli", dict)]
#[derive(Debug, Clone)]
pub struct PyKleisli {
    func: PyShared,
    name: String,
    file: Option<String>,
    line: Option<u32>,
}

#[pymethods]
impl PyKleisli {
    #[new]
    #[pyo3(signature = (func, name, file=None, line=None))]
    fn new(
        py: Python<'_>,
        func: Py<PyAny>,
        name: String,
        file: Option<String>,
        line: Option<u32>,
    ) -> PyResult<Self> {
        if !func.bind(py).is_callable() {
            return Err(PyTypeError::new_err("PyKleisli.func must be callable"));
        }
        Ok(Self {
            func: PyShared::new(func),
            name,
            file,
            line,
        })
    }

    #[pyo3(signature = (*args, **kwargs))]
    fn __call__(
        &self,
        py: Python<'_>,
        args: &Bound<'_, PyTuple>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        self.func
            .bind(py)
            .call(args, kwargs)
            .map(|result| result.unbind())
    }

    fn __get__(
        slf: Py<Self>,
        py: Python<'_>,
        instance: Option<Py<PyAny>>,
        _owner: Option<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        if let Some(instance_obj) = instance {
            let method_type = py.import("types")?.getattr("MethodType")?;
            return method_type
                .call1((slf, instance_obj))
                .map(|bound| bound.unbind());
        }
        Ok(slf.into_any())
    }

    fn __rshift__(&self, py: Python<'_>, binder: Py<PyAny>) -> PyResult<Py<PyAny>> {
        self.func
            .bind(py)
            .call_method1("__rshift__", (binder,))
            .map(|result| result.unbind())
    }

    #[pyo3(signature = (*args, **kwargs))]
    fn partial(
        &self,
        py: Python<'_>,
        args: &Bound<'_, PyTuple>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        self.func
            .bind(py)
            .call_method("partial", args, kwargs)
            .map(|result| result.unbind())
    }

    fn and_then_k(&self, py: Python<'_>, binder: Py<PyAny>) -> PyResult<Py<PyAny>> {
        self.func
            .bind(py)
            .call_method1("and_then_k", (binder,))
            .map(|result| result.unbind())
    }

    fn fmap(&self, py: Python<'_>, mapper: Py<PyAny>) -> PyResult<Py<PyAny>> {
        self.func
            .bind(py)
            .call_method1("fmap", (mapper,))
            .map(|result| result.unbind())
    }

    fn __getattr__(&self, py: Python<'_>, name: &str) -> PyResult<Py<PyAny>> {
        self.func.bind(py).getattr(name).map(|bound| bound.unbind())
    }

    fn __repr__(&self) -> String {
        format!(
            "PyKleisli({}, {}:{})",
            self.name,
            self.file.as_deref().unwrap_or("?"),
            self.line.unwrap_or(0),
        )
    }
}

impl PyKleisli {
    fn map_pyerr(err: PyErr) -> VMError {
        Python::attach(|py| {
            if err.is_instance_of::<PyTypeError>(py) {
                VMError::type_error(err.to_string())
            } else {
                VMError::python_error(err.to_string())
            }
        })
    }

    pub fn from_handler(py: Python<'_>, func: Py<PyAny>) -> PyResult<Self> {
        if !func.bind(py).is_callable() {
            return Err(PyTypeError::new_err("handler callable must be callable"));
        }

        if func.bind(py).is_instance_of::<DoeffGeneratorFn>() {
            let dgfn: PyRef<'_, DoeffGeneratorFn> = func.bind(py).extract()?;
            return Ok(Self {
                func: PyShared::new(func),
                name: dgfn.function_name.clone(),
                file: Some(dgfn.source_file.clone()),
                line: Some(dgfn.source_line),
            });
        }

        let callable = func.bind(py);
        let name = callable
            .getattr("__qualname__")
            .ok()
            .and_then(|bound| bound.extract::<String>().ok())
            .or_else(|| {
                callable
                    .getattr("__name__")
                    .ok()
                    .and_then(|bound| bound.extract::<String>().ok())
            })
            .unwrap_or_else(|| "<python_handler>".to_string());
        let (file, line) = Self::source_info(callable);

        Ok(Self {
            func: PyShared::new(func),
            name,
            file,
            line,
        })
    }

    fn source_info(callable: &Bound<'_, PyAny>) -> (Option<String>, Option<u32>) {
        let maybe_code = callable.getattr("__code__").ok().or_else(|| {
            callable
                .getattr("__call__")
                .ok()
                .and_then(|method| method.getattr("__code__").ok())
        });

        let Some(code) = maybe_code else {
            return (None, None);
        };

        let file = code
            .getattr("co_filename")
            .ok()
            .and_then(|bound| bound.extract::<String>().ok());
        let line = code
            .getattr("co_firstlineno")
            .ok()
            .and_then(|bound| bound.extract::<u32>().ok());
        (file, line)
    }

    fn resolve_apply_callable(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if let Ok(factory) = self.func.bind(py).getattr("_doeff_generator_factory") {
            return Ok(factory.unbind());
        }
        Ok(self.func.clone_ref(py))
    }

    fn default_get_frame(py: Python<'_>) -> Result<Py<PyAny>, VMError> {
        let callable = py
            .import("doeff.do")
            .and_then(|mod_| mod_.getattr("_default_get_frame"))
            .map_err(|e| {
                VMError::python_error(format!("failed to resolve default get_frame: {e}"))
            })?;
        Ok(callable.unbind())
    }
}

impl Kleisli for PyKleisli {
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        let arg_values: Vec<Bound<'_, PyAny>> = args
            .iter()
            .map(|value| value.to_pyobject(py))
            .collect::<PyResult<Vec<_>>>()
            .map_err(Self::map_pyerr)?;
        let arg_tuple = PyTuple::new(py, &arg_values).map_err(Self::map_pyerr)?;
        let args_repr = arg_tuple
            .repr()
            .ok()
            .and_then(|repr| repr.extract::<String>().ok())
            .map(|repr| format!("args={repr}, kwargs={{}}"));

        let apply_callable = self.resolve_apply_callable(py).map_err(Self::map_pyerr)?;
        let produced = apply_callable
            .bind(py)
            .call1(arg_tuple)
            .map_err(Self::map_pyerr)?;

        let (generator, get_frame) = if produced.is_instance_of::<DoeffGenerator>() {
            let doeff_gen = produced
                .extract::<PyRef<'_, DoeffGenerator>>()
                .map_err(|e| VMError::python_error(e.to_string()))?;
            (
                doeff_gen.generator.clone_ref(py),
                doeff_gen.get_frame.clone_ref(py),
            )
        } else {
            let is_generator_like = produced.hasattr("__next__").unwrap_or(false)
                && produced.hasattr("send").unwrap_or(false)
                && produced.hasattr("throw").unwrap_or(false);
            if !is_generator_like {
                let found = produced
                    .get_type()
                    .name()
                    .map(|name| name.to_string())
                    .unwrap_or_else(|_| "<unknown>".to_string());
                return Err(VMError::type_error(format!(
                    "Kleisli {} must return a generator-like object, got {found}",
                    self.name
                )));
            }
            (produced.unbind(), Self::default_get_frame(py)?)
        };

        let stream = PythonGeneratorStream::new(PyShared::new(generator), PyShared::new(get_frame));
        let stream_ref: IRStreamRef = Arc::new(Mutex::new(Box::new(stream)));
        let metadata = CallMetadata::new(
            self.name.clone(),
            self.file.clone().unwrap_or_else(|| "<unknown>".to_string()),
            self.line.unwrap_or(0),
            args_repr,
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

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn py_identity(&self) -> Option<PyShared> {
        Python::attach(|py| {
            if let Ok(factory) = self.func.bind(py).getattr("_doeff_generator_factory") {
                if let Ok(callable) = factory.getattr("callable") {
                    return Some(PyShared::new(callable.unbind()));
                }
            }
            Some(self.func.clone())
        })
    }
}

#[derive(Debug, Clone)]
pub struct DgfnKleisli {
    inner: PyKleisli,
    callable_identity: PyShared,
}

impl DgfnKleisli {
    pub fn from_dgfn(
        py: Python<'_>,
        dgfn_obj: Py<PyAny>,
        callable_identity: Py<PyAny>,
    ) -> PyResult<Self> {
        let inner = PyKleisli::from_handler(py, dgfn_obj)?;
        Ok(Self {
            inner,
            callable_identity: PyShared::new(callable_identity),
        })
    }
}

impl Kleisli for DgfnKleisli {
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        self.inner.apply(py, args)
    }

    fn debug_info(&self) -> KleisliDebugInfo {
        self.inner.debug_info()
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn py_identity(&self) -> Option<PyShared> {
        Some(self.callable_identity.clone())
    }
}

/// Python-callable Kleisli that returns the callable's raw value.
///
/// This is used for interceptor functions that can return a transformed
/// DoExpr/effect directly (not necessarily a generator).
#[derive(Debug, Clone)]
pub struct PyCallableKleisli {
    func: PyShared,
    name: String,
    file: Option<String>,
    line: Option<u32>,
}

impl PyCallableKleisli {
    pub fn from_callable(py: Python<'_>, func: Py<PyAny>) -> PyResult<Self> {
        if !func.bind(py).is_callable() {
            return Err(PyTypeError::new_err("callable must be callable"));
        }
        let callable = func.bind(py);
        let name = callable
            .getattr("__qualname__")
            .ok()
            .and_then(|bound| bound.extract::<String>().ok())
            .or_else(|| {
                callable
                    .getattr("__name__")
                    .ok()
                    .and_then(|bound| bound.extract::<String>().ok())
            })
            .unwrap_or_else(|| "<python_callable>".to_string());
        let (file, line) = PyKleisli::source_info(callable);
        Ok(Self {
            func: PyShared::new(func),
            name,
            file,
            line,
        })
    }
}

impl Kleisli for PyCallableKleisli {
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        let arg_values: Vec<Bound<'_, PyAny>> = args
            .iter()
            .map(|value| value.to_pyobject(py))
            .collect::<PyResult<Vec<_>>>()
            .map_err(PyKleisli::map_pyerr)?;
        let arg_tuple = PyTuple::new(py, &arg_values).map_err(PyKleisli::map_pyerr)?;
        let produced = self
            .func
            .bind(py)
            .call1(arg_tuple)
            .map_err(PyKleisli::map_pyerr)?;
        Ok(DoCtrl::Pure {
            value: Value::Python(produced.unbind()),
        })
    }

    fn debug_info(&self) -> KleisliDebugInfo {
        KleisliDebugInfo {
            name: self.name.clone(),
            file: self.file.clone(),
            line: self.line,
        }
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn py_identity(&self) -> Option<PyShared> {
        Some(self.func.clone())
    }
}

#[derive(Debug, Clone)]
pub struct HandlerSentinelKleisli {
    handler: Handler,
    identity: PyShared,
    debug: KleisliDebugInfo,
}

impl HandlerSentinelKleisli {
    pub fn new(handler: Handler, identity: PyShared) -> Self {
        let info = handler.handler_debug_info();
        Self {
            handler,
            identity,
            debug: KleisliDebugInfo {
                name: info.name,
                file: info.file,
                line: info.line,
            },
        }
    }

    pub fn handler(&self) -> Handler {
        self.handler.clone()
    }
}

impl Kleisli for HandlerSentinelKleisli {
    fn apply(&self, _py: Python<'_>, _args: Vec<Value>) -> Result<DoCtrl, VMError> {
        Err(VMError::internal(
            "RustHandler sentinel Kleisli cannot be invoked directly",
        ))
    }

    fn debug_info(&self) -> KleisliDebugInfo {
        self.debug.clone()
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn py_identity(&self) -> Option<PyShared> {
        Some(self.identity.clone())
    }
}

#[derive(Debug, Clone)]
pub struct RustKleisli {
    factory: IRStreamFactoryRef,
    name: String,
}

impl RustKleisli {
    pub fn new(factory: IRStreamFactoryRef, name: String) -> Self {
        Self { factory, name }
    }

    pub fn factory(&self) -> &IRStreamFactoryRef {
        &self.factory
    }
}

impl Kleisli for RustKleisli {
    fn apply(&self, _py: Python<'_>, _args: Vec<Value>) -> Result<DoCtrl, VMError> {
        Err(VMError::internal(
            "RustKleisli.apply() not yet wired - use IRStreamFactory directly until Phase 3",
        ))
    }

    fn debug_info(&self) -> KleisliDebugInfo {
        KleisliDebugInfo {
            name: self.name.clone(),
            file: None,
            line: None,
        }
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}
