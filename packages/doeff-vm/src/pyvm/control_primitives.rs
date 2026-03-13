use pyo3::exceptions::{PyBaseException, PyRuntimeError, PyStopIteration, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::doeff_generator::DoeffGeneratorFn;
use crate::kleisli::PyKleisli;
use doeff_core_effects::sentinels::PyRustHandlerSentinel;
use doeff_vm_core::{DoExprTag, PyDoCtrlBase, PyDoExprBase, PyK};

use super::helpers::{
    default_discontinued_exception, intercept_mode_from_str, is_effect_base_like,
    lift_effect_to_perform_expr, normalize_handler_types_obj, normalize_intercept_types_obj,
    strict_handler_type_error,
};

// ---------------------------------------------------------------------------
// Pyclass control primitives [R8-C]
// ---------------------------------------------------------------------------

/// Composition primitive — usable in any Program.
#[pyclass(name = "WithHandler", extends=PyDoCtrlBase)]
pub struct PyWithHandler {
    #[pyo3(get)]
    pub handler: Py<PyAny>,
    #[pyo3(get)]
    pub expr: Py<PyAny>,
    #[pyo3(get)]
    pub types: Option<Py<PyAny>>,
    #[pyo3(get)]
    pub handler_name: Option<String>,
    #[pyo3(get)]
    pub handler_file: Option<String>,
    #[pyo3(get)]
    pub handler_line: Option<u32>,
}

#[pymethods]
impl PyWithHandler {
    #[new]
    #[pyo3(signature = (handler, expr, *, types=None, handler_name=None, handler_file=None, handler_line=None))]
    pub fn new(
        py: Python<'_>,
        handler: Py<PyAny>,
        expr: Py<PyAny>,
        types: Option<Py<PyAny>>,
        handler_name: Option<String>,
        handler_file: Option<String>,
        handler_line: Option<u32>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let handler_obj = handler.bind(py);
        let is_rust_handler = handler_obj.is_instance_of::<PyRustHandlerSentinel>();
        let is_dgfn = handler_obj.is_instance_of::<DoeffGeneratorFn>();
        let is_kleisli = handler_obj.is_instance_of::<PyKleisli>();
        if !is_rust_handler && !is_dgfn && !is_kleisli {
            return Err(strict_handler_type_error(
                "WithHandler",
                "handler",
                handler_obj,
            ));
        }

        let expr = lift_effect_to_perform_expr(py, expr)?;
        let normalized_types = normalize_handler_types_obj(py, types)?;

        let expr_obj = expr.bind(py);
        if !expr_obj.is_instance_of::<PyDoExprBase>() {
            return Err(PyTypeError::new_err("WithHandler.expr must be DoExpr"));
        }

        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::WithHandler as u8,
            })
            .add_subclass(PyWithHandler {
                handler,
                expr,
                types: normalized_types,
                handler_name,
                handler_file,
                handler_line,
            }))
    }
}

#[pyclass(name = "WithIntercept", extends=PyDoCtrlBase)]
pub struct PyWithIntercept {
    #[pyo3(get)]
    pub f: Py<PyAny>,
    #[pyo3(get)]
    pub expr: Py<PyAny>,
    #[pyo3(get)]
    pub types: Option<Py<PyAny>>,
    #[pyo3(get)]
    pub mode: String,
    #[pyo3(get)]
    pub meta: Option<Py<PyAny>>,
}

#[pymethods]
impl PyWithIntercept {
    #[new]
    #[pyo3(signature = (f, expr, types=None, mode="include", meta=None))]
    pub fn new(
        py: Python<'_>,
        f: Py<PyAny>,
        expr: Py<PyAny>,
        types: Option<Py<PyAny>>,
        mode: &str,
        meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let f_obj = f.bind(py);
        let is_rust_handler = f_obj.is_instance_of::<PyRustHandlerSentinel>();
        let is_dgfn = f_obj.is_instance_of::<DoeffGeneratorFn>();
        let is_kleisli = f_obj.is_instance_of::<PyKleisli>();
        if !is_rust_handler && !is_dgfn && !is_kleisli {
            return Err(strict_handler_type_error(
                "WithIntercept",
                "interceptor",
                f_obj,
            ));
        }

        let normalized_types = normalize_intercept_types_obj(py, types)?;
        let mode = intercept_mode_from_str(mode)?;

        if let Some(meta_obj) = meta.as_ref() {
            let meta_bound = meta_obj.bind(py);
            if !meta_bound.is_instance_of::<PyDict>() {
                return Err(PyTypeError::new_err(
                    "WithIntercept.meta must be dict with function_name/source_file/source_line",
                ));
            }
        }
        let expr = lift_effect_to_perform_expr(py, expr)?;
        let expr_obj = expr.bind(py);
        if !expr_obj.is_instance_of::<PyDoExprBase>() {
            return Err(PyTypeError::new_err("WithIntercept.expr must be DoExpr"));
        }

        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::WithIntercept as u8,
            })
            .add_subclass(PyWithIntercept {
                f,
                expr,
                types: normalized_types,
                mode: mode.as_str().to_string(),
                meta,
            }))
    }
}

#[pyclass(name = "Discontinue", extends=PyDoCtrlBase)]
pub struct PyDiscontinue {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub exception: Py<PyAny>,
}

#[pymethods]
impl PyDiscontinue {
    #[new]
    #[pyo3(signature = (continuation, exception=None))]
    pub fn new(
        py: Python<'_>,
        continuation: Py<PyAny>,
        exception: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "Discontinue.continuation must be K (opaque continuation handle)",
            ));
        }
        let exception = match exception {
            Some(exception) => {
                if !exception.bind(py).is_instance_of::<PyBaseException>() {
                    return Err(PyTypeError::new_err(
                        "Discontinue.exception must be a BaseException instance",
                    ));
                }
                exception
            }
            None => default_discontinued_exception(py)?,
        };
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Discontinue as u8,
            })
            .add_subclass(PyDiscontinue {
                continuation,
                exception,
            }))
    }
}

#[pyclass(name = "Map", extends=PyDoCtrlBase)]
pub struct PyMap {
    #[pyo3(get)]
    pub source: Py<PyAny>,
    #[pyo3(get)]
    pub mapper: Py<PyAny>,
    #[pyo3(get)]
    pub mapper_meta: Py<PyAny>,
}

#[pyclass(name = "Pure", extends=PyDoCtrlBase)]
pub struct PyPure {
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyPure {
    #[new]
    pub fn new(value: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Pure as u8,
            })
            .add_subclass(PyPure { value })
    }
}

#[pyclass(name = "Apply", extends=PyDoCtrlBase)]
pub struct PyApply {
    #[pyo3(get)]
    pub f: Py<PyAny>,
    #[pyo3(get)]
    pub args: Py<PyAny>,
    #[pyo3(get)]
    pub kwargs: Py<PyAny>,
    #[pyo3(get)]
    pub meta: Option<Py<PyAny>>,
}

#[pymethods]
impl PyApply {
    #[new]
    #[pyo3(signature = (f, args, kwargs, meta=None))]
    pub fn new(
        py: Python<'_>,
        f: Py<PyAny>,
        args: Py<PyAny>,
        kwargs: Py<PyAny>,
        meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if args.bind(py).try_iter().is_err() {
            return Err(PyTypeError::new_err("Apply.args must be iterable"));
        }
        if !kwargs.bind(py).is_instance_of::<PyDict>() {
            return Err(PyTypeError::new_err("Apply.kwargs must be dict"));
        }
        if meta.is_none() {
            return Err(PyTypeError::new_err(
                "Apply.meta is required. \
Program/Kleisli call sites must pass {'function_name', 'source_file', 'source_line'}.",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Apply as u8,
            })
            .add_subclass(PyApply {
                f,
                args,
                kwargs,
                meta,
            }))
    }
}

#[pyclass(name = "Expand", extends=PyDoCtrlBase)]
pub struct PyExpand {
    #[pyo3(get)]
    pub factory: Py<PyAny>,
    #[pyo3(get)]
    pub args: Py<PyAny>,
    #[pyo3(get)]
    pub kwargs: Py<PyAny>,
    #[pyo3(get)]
    pub meta: Option<Py<PyAny>>,
}

#[pymethods]
impl PyExpand {
    #[new]
    #[pyo3(signature = (factory, args, kwargs, meta=None))]
    pub fn new(
        py: Python<'_>,
        factory: Py<PyAny>,
        args: Py<PyAny>,
        kwargs: Py<PyAny>,
        meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if args.bind(py).try_iter().is_err() {
            return Err(PyTypeError::new_err("Expand.args must be iterable"));
        }
        if !kwargs.bind(py).is_instance_of::<PyDict>() {
            return Err(PyTypeError::new_err("Expand.kwargs must be dict"));
        }
        if meta.is_none() {
            return Err(PyTypeError::new_err(
                "Expand.meta is required. \
Program/Kleisli call sites must pass {'function_name', 'source_file', 'source_line'}.",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Expand as u8,
            })
            .add_subclass(PyExpand {
                factory,
                args,
                kwargs,
                meta,
            }))
    }
}

#[pyclass(name = "Eval", extends=PyDoCtrlBase)]
pub struct PyEval {
    #[pyo3(get)]
    pub expr: Py<PyAny>,
}

#[pymethods]
impl PyEval {
    #[new]
    pub fn new(py: Python<'_>, expr: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        let expr = lift_effect_to_perform_expr(py, expr)?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Eval as u8,
            })
            .add_subclass(PyEval { expr }))
    }
}

#[pyclass(name = "EvalInScope", extends=PyDoCtrlBase)]
pub struct PyEvalInScope {
    #[pyo3(get)]
    pub expr: Py<PyAny>,
    #[pyo3(get)]
    pub scope: Py<PyAny>,
}

#[pymethods]
impl PyEvalInScope {
    #[new]
    pub fn new(
        py: Python<'_>,
        expr: Py<PyAny>,
        scope: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let expr = lift_effect_to_perform_expr(py, expr)?;
        if !scope.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "EvalInScope.scope must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::EvalInScope as u8,
            })
            .add_subclass(PyEvalInScope { expr, scope }))
    }
}

#[pyclass(name = "Perform", extends=PyDoCtrlBase)]
pub struct PyPerform {
    #[pyo3(get)]
    pub effect: Py<PyAny>,
}

#[pymethods]
impl PyPerform {
    #[new]
    pub fn new(py: Python<'_>, effect: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        if !is_effect_base_like(py, effect.bind(py))? {
            return Err(PyTypeError::new_err("Perform.effect must be EffectBase"));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Perform as u8,
            })
            .add_subclass(PyPerform { effect }))
    }
}

#[pymethods]
impl PyMap {
    #[new]
    #[pyo3(signature = (source, mapper, mapper_meta=None))]
    pub fn new(
        py: Python<'_>,
        source: Py<PyAny>,
        mapper: Py<PyAny>,
        mapper_meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !mapper.bind(py).is_callable() {
            return Err(PyTypeError::new_err("Map.mapper must be callable"));
        }
        let mapper_meta = mapper_meta.ok_or_else(|| {
            PyTypeError::new_err(
                "Map.mapper_meta is required. \
Program.map() should supply metadata from mapper.__code__. \
Pass mapper_meta={'function_name': ..., 'source_file': ..., 'source_line': ...}.",
            )
        })?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Map as u8,
            })
            .add_subclass(PyMap {
                source,
                mapper,
                mapper_meta,
            }))
    }
}

#[pyclass(name = "FlatMap", extends=PyDoCtrlBase)]
pub struct PyFlatMap {
    #[pyo3(get)]
    pub source: Py<PyAny>,
    #[pyo3(get)]
    pub binder: Py<PyAny>,
    #[pyo3(get)]
    pub binder_meta: Py<PyAny>,
}

#[pymethods]
impl PyFlatMap {
    #[new]
    #[pyo3(signature = (source, binder, binder_meta=None))]
    pub fn new(
        py: Python<'_>,
        source: Py<PyAny>,
        binder: Py<PyAny>,
        binder_meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !binder.bind(py).is_callable() {
            return Err(PyTypeError::new_err("FlatMap.binder must be callable"));
        }
        let binder_meta = binder_meta.ok_or_else(|| {
            PyTypeError::new_err(
                "FlatMap.binder_meta is required. \
Program.flat_map() should supply metadata from binder.__code__. \
Pass binder_meta={'function_name': ..., 'source_file': ..., 'source_line': ...}.",
            )
        })?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::FlatMap as u8,
            })
            .add_subclass(PyFlatMap {
                source,
                binder,
                binder_meta,
            }))
    }
}

/// Dispatch primitive — handler-only.
#[pyclass(name = "Resume", extends=PyDoCtrlBase)]
pub struct PyResume {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyResume {
    #[new]
    pub fn new(
        py: Python<'_>,
        continuation: Py<PyAny>,
        value: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "Resume.continuation must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Resume as u8,
            })
            .add_subclass(PyResume {
                continuation,
                value,
            }))
    }
}

/// Dispatch primitive — handler-only.
#[pyclass(name = "Delegate", extends=PyDoCtrlBase)]
pub struct PyDelegate {}

#[pymethods]
impl PyDelegate {
    #[new]
    #[pyo3(signature = ())]
    pub fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Delegate as u8,
            })
            .add_subclass(PyDelegate {})
    }
}

/// Dispatch primitive — handler-only.
#[pyclass(name = "Pass", extends=PyDoCtrlBase)]
pub struct PyPass {}

#[pymethods]
impl PyPass {
    #[new]
    #[pyo3(signature = ())]
    pub fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Pass as u8,
            })
            .add_subclass(PyPass {})
    }
}

/// Dispatch primitive — handler-only, one-shot.
#[pyclass(name = "Transfer", extends=PyDoCtrlBase)]
pub struct PyTransfer {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

/// Resume an unstarted continuation produced by CreateContinuation.
#[pyclass(name = "ResumeContinuation", extends=PyDoCtrlBase)]
pub struct PyResumeContinuation {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyResumeContinuation {
    #[new]
    pub fn new(
        py: Python<'_>,
        continuation: Py<PyAny>,
        value: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "ResumeContinuation.continuation must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::ResumeContinuation as u8,
            })
            .add_subclass(PyResumeContinuation {
                continuation,
                value,
            }))
    }
}

#[pymethods]
impl PyTransfer {
    #[new]
    pub fn new(
        py: Python<'_>,
        continuation: Py<PyAny>,
        value: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "Transfer.continuation must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Transfer as u8,
            })
            .add_subclass(PyTransfer {
                continuation,
                value,
            }))
    }
}

/// Create a delimited continuation scope.
#[pyclass(name = "CreateContinuation", extends=PyDoCtrlBase)]
pub struct PyCreateContinuation {
    #[pyo3(get)]
    pub program: Py<PyAny>,
    #[pyo3(get)]
    pub handlers: Py<PyAny>,
}

#[pymethods]
impl PyCreateContinuation {
    #[new]
    pub fn new(
        py: Python<'_>,
        program: Py<PyAny>,
        handlers: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let program = lift_effect_to_perform_expr(py, program)?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::CreateContinuation as u8,
            })
            .add_subclass(PyCreateContinuation { program, handlers }))
    }
}

/// Request traceback frames for a continuation and its parent chain.
#[pyclass(name = "GetTraceback", extends=PyDoCtrlBase)]
pub struct PyGetTraceback {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
}

#[pymethods]
impl PyGetTraceback {
    #[new]
    pub fn new(py: Python<'_>, continuation: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "GetTraceback.continuation must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetTraceback as u8,
            })
            .add_subclass(PyGetTraceback { continuation }))
    }
}

/// Request the current continuation.
#[pyclass(name = "GetContinuation", extends=PyDoCtrlBase)]
pub struct PyGetContinuation;

#[pymethods]
impl PyGetContinuation {
    #[new]
    pub fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetContinuation as u8,
            })
            .add_subclass(PyGetContinuation)
    }
}

/// Request the current handler stack.
#[pyclass(name = "GetHandlers", extends=PyDoCtrlBase)]
pub struct PyGetHandlers;

#[pymethods]
impl PyGetHandlers {
    #[new]
    pub fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetHandlers as u8,
            })
            .add_subclass(PyGetHandlers)
    }
}

/// Request the current call stack.
#[pyclass(name = "GetCallStack", extends=PyDoCtrlBase)]
pub struct PyGetCallStack;

#[pymethods]
impl PyGetCallStack {
    #[new]
    pub fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetCallStack as u8,
            })
            .add_subclass(PyGetCallStack)
    }
}

/// Escape hatch for Python async syntax (await bridge).
#[pyclass(name = "AsyncEscape", extends=PyDoCtrlBase)]
pub struct PyAsyncEscape {
    #[pyo3(get)]
    pub action: Py<PyAny>,
}

#[pymethods]
impl PyAsyncEscape {
    #[new]
    pub fn new(action: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::AsyncEscape as u8,
            })
            .add_subclass(PyAsyncEscape { action })
    }
}

// ---------------------------------------------------------------------------
// NestingStep + NestingGenerator — WithHandler nesting chain [ADR-13]
// ---------------------------------------------------------------------------

/// ProgramBase that yields one WithHandler(handler, inner), then returns
/// the inner result. Used by run() to build handler nesting chains.
/// ADR-13: run() is defined in terms of WithHandler, not install_handler.
#[pyclass(name = "_NestingStep")]
pub struct NestingStep {
    handler: Py<PyAny>,
    inner: Py<PyAny>,
}

#[pymethods]
impl NestingStep {
    fn to_generator(slf: PyRef<'_, Self>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let gen = Bound::new(
            py,
            NestingGenerator {
                handler: Some(slf.handler.clone_ref(py)),
                inner: Some(slf.inner.clone_ref(py)),
                done: false,
            },
        )?
        .into_any()
        .unbind();
        Ok(gen)
    }
}

/// Generator for NestingStep. Two phases:
/// 1. `__next__()` → yields PyWithHandler { handler, inner }
/// 2. `send(value)` → raises StopIteration(value) (pass-through)
#[pyclass(name = "_NestingGenerator")]
pub struct NestingGenerator {
    handler: Option<Py<PyAny>>,
    inner: Option<Py<PyAny>>,
    done: bool,
}

#[pymethods]
impl NestingGenerator {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        if self.done {
            return Ok(None);
        }
        let handler = self
            .handler
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("NestingGenerator already consumed"))?;
        let inner = self
            .inner
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("NestingGenerator already consumed"))?;
        let inner = lift_effect_to_perform_expr(py, inner)?;
        self.done = true;
        let wh = PyWithHandler {
            handler,
            expr: inner,
            types: None,
            handler_name: None,
            handler_file: None,
            handler_line: None,
        };
        let bound = Bound::new(
            py,
            PyClassInitializer::from(PyDoExprBase)
                .add_subclass(PyDoCtrlBase {
                    tag: DoExprTag::WithHandler as u8,
                })
                .add_subclass(wh),
        )?;
        Ok(Some(bound.into_any().unbind()))
    }

    fn send(&mut self, py: Python<'_>, value: Py<PyAny>) -> PyResult<Py<PyAny>> {
        if !self.done {
            // First call (send(None)) — equivalent to __next__
            return match self.__next__(py)? {
                Some(v) => Ok(v),
                None => Err(PyStopIteration::new_err(py.None())),
            };
        }
        // After yielding WithHandler, the inner result comes back via send.
        // Pass through as StopIteration(value).
        Err(PyStopIteration::new_err((value,)))
    }

    fn throw(&mut self, _py: Python<'_>, exc: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        Err(PyErr::from_value(exc))
    }
}
