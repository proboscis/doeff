//! DoExpr pyclasses — Python-visible program nodes.
//!
//! These replace the plain Python classes in `doeff/program.py`.
//! The VM classifies them via `downcast` (not tag-based `getattr`).

use pyo3::prelude::*;
use doeff_vm_core::continuation::PyK;

/// Pure(value) — return a value immediately.
#[pyclass(name = "Pure", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyPure {
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyPure {
    #[new]
    fn new(value: Py<PyAny>) -> Self {
        Self { value }
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let v = self.value.bind(py).repr()?;
        Ok(format!("Pure({})", v))
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>,))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, (self.value.clone_ref(py),)))
    }
}

/// Perform(effect) — perform an effect (trigger handler lookup).
#[pyclass(name = "Perform", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyPerform {
    #[pyo3(get)]
    pub effect: Py<PyAny>,
}

#[pymethods]
impl PyPerform {
    #[new]
    fn new(effect: Py<PyAny>) -> Self {
        Self { effect }
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let e = self.effect.bind(py).repr()?;
        Ok(format!("Perform({})", e))
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>,))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, (self.effect.clone_ref(py),)))
    }
}

/// Resume(k, value) — resume continuation with value (non-tail, handler stays alive).
#[pyclass(name = "Resume", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyResume {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyResume {
    #[new]
    fn new(k: Py<PyK>, value: Py<PyAny>) -> Self {
        Self { continuation: k, value }
    }

    fn __repr__(&self) -> &'static str {
        "Resume(k, ...)"
    }
}

/// Transfer(k, value) — resume continuation with value (tail, handler done).
#[pyclass(name = "Transfer", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyTransfer {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyTransfer {
    #[new]
    fn new(k: Py<PyK>, value: Py<PyAny>) -> Self {
        Self { continuation: k, value }
    }

    fn __repr__(&self) -> &'static str {
        "Transfer(k, ...)"
    }
}

/// Apply(f, args) — call f(args).
#[pyclass(name = "Apply", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyApply {
    #[pyo3(get)]
    pub f: Py<PyAny>,
    #[pyo3(get)]
    pub args: Py<PyAny>,
}

#[pymethods]
impl PyApply {
    #[new]
    fn new(f: Py<PyAny>, args: Py<PyAny>) -> Self {
        Self { f, args }
    }

    fn __repr__(&self) -> &'static str {
        "Apply(f, args)"
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>, Py<PyAny>))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, (self.f.clone_ref(py), self.args.clone_ref(py))))
    }
}

/// Expand(expr) — evaluate inner expr to Stream, then run it.
#[pyclass(name = "Expand", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyExpand {
    #[pyo3(get)]
    pub expr: Py<PyAny>,
}

#[pymethods]
impl PyExpand {
    #[new]
    fn new(expr: Py<PyAny>) -> Self {
        Self { expr }
    }

    fn __repr__(&self) -> &'static str {
        "Expand(...)"
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>,))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, (self.expr.clone_ref(py),)))
    }
}

/// Pass(effect, k) — handler doesn't handle, forward to outer.
#[pyclass(name = "Pass", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyPass {
    #[pyo3(get)]
    pub effect: Py<PyAny>,
    #[pyo3(get)]
    pub continuation: Py<PyK>,
}

#[pymethods]
impl PyPass {
    #[new]
    fn new(effect: Py<PyAny>, k: Py<PyK>) -> Self {
        Self { effect, continuation: k }
    }

    fn __repr__(&self) -> &'static str {
        "Pass(effect, k)"
    }
}

/// WithHandler(handler, body) — install handler and run body under it.
#[pyclass(name = "WithHandler", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyWithHandler {
    #[pyo3(get)]
    pub handler: Py<PyAny>,
    #[pyo3(get)]
    pub body: Py<PyAny>,
}

#[pymethods]
impl PyWithHandler {
    #[new]
    fn new(handler: Py<PyAny>, body: Py<PyAny>) -> Self {
        Self { handler, body }
    }

    fn __repr__(&self) -> &'static str {
        "WithHandler(handler, body)"
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>, Py<PyAny>))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, (self.handler.clone_ref(py), self.body.clone_ref(py))))
    }
}

/// ResumeThrow(k, exception) — throw exception into continuation (non-tail).
#[pyclass(name = "ResumeThrow", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyResumeThrow {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
    #[pyo3(get)]
    pub exception: Py<PyAny>,
}

#[pymethods]
impl PyResumeThrow {
    #[new]
    fn new(k: Py<PyK>, exception: Py<PyAny>) -> Self {
        Self { continuation: k, exception }
    }

    fn __repr__(&self) -> &'static str {
        "ResumeThrow(k, ...)"
    }
}

/// TransferThrow(k, exception) — throw exception into continuation (tail).
#[pyclass(name = "TransferThrow", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyTransferThrow {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
    #[pyo3(get)]
    pub exception: Py<PyAny>,
}

#[pymethods]
impl PyTransferThrow {
    #[new]
    fn new(k: Py<PyK>, exception: Py<PyAny>) -> Self {
        Self { continuation: k, exception }
    }

    fn __repr__(&self) -> &'static str {
        "TransferThrow(k, ...)"
    }
}

/// WithObserve(observer, body) — install observer and run body under it.
#[pyclass(name = "WithObserve", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyWithObserve {
    #[pyo3(get)]
    pub observer: Py<PyAny>,
    #[pyo3(get)]
    pub body: Py<PyAny>,
}

#[pymethods]
impl PyWithObserve {
    #[new]
    fn new(observer: Py<PyAny>, body: Py<PyAny>) -> Self {
        Self { observer, body }
    }

    fn __repr__(&self) -> &'static str {
        "WithObserve(observer, body)"
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>, Py<PyAny>))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, (self.observer.clone_ref(py), self.body.clone_ref(py))))
    }
}

/// GetTraceback(k) — query traceback from continuation without consuming it.
#[pyclass(name = "GetTraceback", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyGetTraceback {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
}

#[pymethods]
impl PyGetTraceback {
    #[new]
    fn new(k: Py<PyK>) -> Self {
        Self { continuation: k }
    }

    fn __repr__(&self) -> &'static str {
        "GetTraceback(k)"
    }
}

/// GetExecutionContext() — get current execution context.
#[pyclass(name = "GetExecutionContext", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyGetExecutionContext;

#[pymethods]
impl PyGetExecutionContext {
    #[new]
    fn new() -> Self {
        Self
    }

    fn __repr__(&self) -> &'static str {
        "GetExecutionContext()"
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, ())> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, ()))
    }
}

/// GetHandlers(k) — extract handler callables from continuation's fiber chain.
#[pyclass(name = "GetHandlers", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyGetHandlers {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
}

#[pymethods]
impl PyGetHandlers {
    #[new]
    fn new(k: Py<PyK>) -> Self {
        Self { continuation: k }
    }

    fn __repr__(&self) -> &'static str {
        "GetHandlers(k)"
    }
}
