//! DoExpr pyclasses — Python-visible program nodes.
//!
//! These replace the plain Python classes in `doeff/program.py`.
//! The VM classifies them via `downcast` (not tag-based `getattr`).
//!
//! ## GC integration (#500)
//!
//! Every class that holds `Py<PyAny>` / `Py<PyK>` fields implements
//! `__traverse__` so CPython's cycle collector can see through it —
//! without it, any reference cycle through a program node is permanently
//! uncollectable. Fields MUST be visited through `crate::gc::visit_py_field`
//! and never through `PyVisit::call`: the collector can reach an instance
//! whose Rust contents are still zeroed (CPython GC-tracks it in `tp_alloc`,
//! pyo3 writes the value afterwards) and a null `Py<T>` slot handed to
//! `visit_decref` segfaults in optimized builds. See the `crate::gc` module
//! docs for the full mechanism (agora-1 pod `ai usage` rc=-11, 2026-08-26). `__clear__` is deliberately NOT implemented: these
//! classes are `frozen` (no `&mut self` access, required by the VM's
//! immutable-program invariant), so their field references cannot be
//! dropped in-place. That is sound for collection: field cycles cannot be
//! constructed among frozen nodes alone (fields are set once at
//! construction), so every real cycle routes through at least one mutable
//! Python object (instance `__dict__`, list, generator frame, ...) whose
//! `tp_clear` breaks the cycle once `__traverse__` has made it visible.
//!
//! Known limitation: the pyo3 `dict` slot (`#[pyclass(dict)]`, used by
//! defp for `__doeff_body__` metadata) is NOT reachable from
//! `__traverse__` in pyo3 0.28, so a cycle routed exclusively through a
//! program node's instance `__dict__` is still invisible to the GC.

use crate::gc::visit_py_field;
use doeff_vm_core::continuation::PyK;
use pyo3::prelude::*;
use pyo3::pyclass::{PyTraverseError, PyVisit};

/// Pure(value) — return a value immediately.
#[pyclass(name = "Pure", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyPure {
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyPure {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }
    #[classmethod]
    fn __class_getitem__<'py>(
        cls: &Bound<'py, pyo3::types::PyType>,
        item: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::class_getitem(cls, item)
    }

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

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.value)
    }
}

/// Perform(effect) — perform an effect (trigger handler lookup).
#[pyclass(name = "Perform", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyPerform {
    #[pyo3(get)]
    pub effect: Py<PyAny>,
}

#[pymethods]
impl PyPerform {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }
    #[classmethod]
    fn __class_getitem__<'py>(
        cls: &Bound<'py, pyo3::types::PyType>,
        item: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::class_getitem(cls, item)
    }

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

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.effect)
    }
}

/// Resume(k, value) — resume continuation with value (non-tail, handler stays alive).
#[pyclass(name = "Resume", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyResume {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyResume {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(k: Py<PyK>, value: Py<PyAny>) -> Self {
        Self {
            continuation: k,
            value,
        }
    }

    fn __repr__(&self) -> &'static str {
        "Resume(k, ...)"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.continuation)?;
        visit_py_field(&visit, &self.value)
    }
}

/// Transfer(k, value) — resume continuation with value (tail, handler done).
#[pyclass(name = "Transfer", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyTransfer {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyTransfer {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(k: Py<PyK>, value: Py<PyAny>) -> Self {
        Self {
            continuation: k,
            value,
        }
    }

    fn __repr__(&self) -> &'static str {
        "Transfer(k, ...)"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.continuation)?;
        visit_py_field(&visit, &self.value)
    }
}

/// Apply(f, args) — call f(args).
#[pyclass(name = "Apply", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyApply {
    #[pyo3(get)]
    pub f: Py<PyAny>,
    #[pyo3(get)]
    pub args: Py<PyAny>,
}

#[pymethods]
impl PyApply {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

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

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.f)?;
        visit_py_field(&visit, &self.args)
    }
}

/// Expand(expr) — evaluate inner expr to Stream, then run it.
#[pyclass(name = "Expand", frozen, dict, subclass, module = "doeff_vm.doeff_vm")]
pub struct PyExpand {
    #[pyo3(get)]
    pub expr: Py<PyAny>,
}

#[pymethods]
impl PyExpand {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }
    #[classmethod]
    fn __class_getitem__<'py>(
        cls: &Bound<'py, pyo3::types::PyType>,
        item: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::class_getitem(cls, item)
    }

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

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.expr)
    }
}

/// DoFunction(function, tail_resume_lines, yields) — a `@do` definition: the
/// undecorated function, the lines of its tail-position resumes, and whether its
/// body yields (a generator function). Built once per definition
/// (`doeff.do.program_factory`) and shared by every `Call` of it. A call to a
/// definition that does not yield performs nothing, so a bind may call it in place
/// (`doeff_core_effects.outcomes.open_bind` — agora-redesign #844).
#[pyclass(name = "DoFunction", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyDoFunction {
    #[pyo3(get)]
    pub function: Py<PyAny>,
    pub tail_resume_lines: Vec<u32>,
    #[pyo3(get)]
    pub yields: bool,
}

#[pymethods]
impl PyDoFunction {
    #[new]
    fn new(function: Py<PyAny>, tail_resume_lines: Vec<u32>, yields: bool) -> Self {
        Self {
            function,
            tail_resume_lines,
            yields,
        }
    }

    #[getter]
    fn tail_resume_lines(&self) -> Vec<u32> {
        self.tail_resume_lines.clone()
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let f = self.function.bind(py).repr()?;
        Ok(format!("DoFunction({})", f))
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>, Vec<u32>, bool))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((
            cls,
            (
                self.function.clone_ref(py),
                self.tail_resume_lines.clone(),
                self.yields,
            ),
        ))
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.function)
    }
}

/// Call(function, args, kwargs) — the program a `@do` function returns: call
/// `function.function(*args, **kwargs)` and run what it returns (a generator
/// runs as the program's stream; any other value is the program's result).
///
/// One node per call, where the wrapper used to build
/// `Expand(Apply(Pure(Callable(thunk)), []))` — five objects and a Python
/// closure (agora-redesign #844). It is an `Expand` (the static type of a `@do`
/// call stays `Expand[T, E]`), and the VM reads it as exactly that DoCtrl shape
/// (`classify_python_object`), so every evaluation path — handler dispatch
/// included — is unchanged. Its `expr` is not a program node: read
/// `function` / `args` / `kwargs`.
#[pyclass(name = "Call", extends = PyExpand, frozen, module = "doeff_vm.doeff_vm")]
pub struct PyCall {
    #[pyo3(get)]
    pub function: Py<PyDoFunction>,
    #[pyo3(get)]
    pub args: Py<pyo3::types::PyTuple>,
    #[pyo3(get)]
    pub kwargs: Py<pyo3::types::PyDict>,
}

#[pymethods]
impl PyCall {
    #[new]
    fn new(
        py: Python<'_>,
        function: Py<PyDoFunction>,
        args: Py<pyo3::types::PyTuple>,
        kwargs: Py<pyo3::types::PyDict>,
    ) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyExpand { expr: py.None() }).add_subclass(Self {
            function,
            args,
            kwargs,
        })
    }

    #[getter]
    fn expr(&self) -> PyResult<Py<PyAny>> {
        Err(pyo3::exceptions::PyAttributeError::new_err(
            "Call has no expr node: read its function / args / kwargs",
        ))
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let f = self.function.bind(py).get().function.bind(py).repr()?;
        Ok(format!("Call({})", f))
    }

    #[allow(clippy::type_complexity)]
    fn __reduce__(
        &self,
        py: Python<'_>,
    ) -> PyResult<(
        Py<PyAny>,
        (
            Py<PyDoFunction>,
            Py<pyo3::types::PyTuple>,
            Py<pyo3::types::PyDict>,
        ),
    )> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((
            cls,
            (
                self.function.clone_ref(py),
                self.args.clone_ref(py),
                self.kwargs.clone_ref(py),
            ),
        ))
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.function)?;
        visit_py_field(&visit, &self.args)?;
        visit_py_field(&visit, &self.kwargs)
    }
}

/// BindOpener(fallback, generator_program) — what `doeff_core_effects.outcomes.open_bind`
/// is: the one callable every doeff-hy bind (`(<- x e)` / `(! e)`) calls on its operand.
///
/// The hot path runs here without entering Python: with no `absent` and an operand that
/// is a `Call` of a definition whose body does not yield (a defk judgment), call the
/// function in place and return `Pure(answer)` — the bind then takes `.value` without a
/// VM round trip. This is the same answer `outcomes._settled` gives (agora-redesign
/// #844 — a screen row binds such judgments 10–16 times, and the Python dispatch
/// `open_bind` → `_opened` → `_settled` was the largest cost left per bind). An answer
/// that is itself a generator runs as a program, as the VM would run it:
/// `generator_program(gen)`. Everything else (an `absent`, a yielding definition, any
/// other operand) goes to `fallback(expr[, absent])`, the Python dispatch, unchanged.
#[pyclass(name = "BindOpener", frozen, module = "doeff_vm.doeff_vm")]
pub struct PyBindOpener {
    #[pyo3(get)]
    pub fallback: Py<PyAny>,
    #[pyo3(get)]
    pub generator_program: Py<PyAny>,
}

#[pymethods]
impl PyBindOpener {
    #[new]
    fn new(fallback: Py<PyAny>, generator_program: Py<PyAny>) -> Self {
        Self {
            fallback,
            generator_program,
        }
    }

    #[pyo3(signature = (expr, absent=None))]
    fn __call__(
        &self,
        py: Python<'_>,
        expr: &Bound<'_, PyAny>,
        absent: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let absent = absent.filter(|a| !a.is_none());
        if absent.is_none() {
            if let Ok(call) = expr.cast::<PyCall>() {
                let call = call.get();
                let definition = call.function.bind(py).get();
                if !definition.yields {
                    let value = definition
                        .function
                        .bind(py)
                        .call(call.args.bind(py), Some(call.kwargs.bind(py)))?;
                    // SAFETY: `value` is a live object held by this frame.
                    if unsafe { pyo3::ffi::PyGen_CheckExact(value.as_ptr()) } != 0 {
                        return Ok(self.generator_program.bind(py).call1((value,))?.unbind());
                    }
                    return Ok(Py::new(
                        py,
                        PyPure {
                            value: value.unbind(),
                        },
                    )?
                    .into_any());
                }
            }
        }
        match absent {
            None => Ok(self.fallback.bind(py).call1((expr,))?.unbind()),
            Some(absent) => Ok(self.fallback.bind(py).call1((expr, absent))?.unbind()),
        }
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let f = self.fallback.bind(py).repr()?;
        Ok(format!("BindOpener({})", f))
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.fallback)?;
        visit_py_field(&visit, &self.generator_program)
    }
}

/// Pass(effect, k) — handler doesn't handle, forward to outer.
#[pyclass(name = "Pass", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyPass {
    #[pyo3(get)]
    pub effect: Py<PyAny>,
    #[pyo3(get)]
    pub continuation: Py<PyK>,
}

#[pymethods]
impl PyPass {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(effect: Py<PyAny>, k: Py<PyK>) -> Self {
        Self {
            effect,
            continuation: k,
        }
    }

    fn __repr__(&self) -> &'static str {
        "Pass(effect, k)"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.effect)?;
        visit_py_field(&visit, &self.continuation)
    }
}

/// WithHandler(handler, body) — install handler and run body under it.
#[pyclass(name = "WithHandler", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyWithHandler {
    #[pyo3(get)]
    pub handler: Py<PyAny>,
    #[pyo3(get)]
    pub body: Py<PyAny>,
}

#[pymethods]
impl PyWithHandler {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }
    #[classmethod]
    fn __class_getitem__<'py>(
        cls: &Bound<'py, pyo3::types::PyType>,
        item: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::class_getitem(cls, item)
    }

    #[new]
    fn new(handler: Py<PyAny>, body: Py<PyAny>) -> PyResult<Self> {
        Python::attach(|py| {
            let h = handler.bind(py);
            if !h.is_callable() {
                let type_name = h
                    .get_type()
                    .qualname()
                    .map(|s| s.to_string())
                    .unwrap_or_else(|_| "?".to_string());
                return Err(pyo3::exceptions::PyTypeError::new_err(format!(
                    "WithHandler: handler must be callable, got {}",
                    type_name,
                )));
            }
            Ok(Self { handler, body })
        })
    }

    fn __repr__(&self) -> &'static str {
        "WithHandler(handler, body)"
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>, Py<PyAny>))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, (self.handler.clone_ref(py), self.body.clone_ref(py))))
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.handler)?;
        visit_py_field(&visit, &self.body)
    }
}

/// ResumeThrow(k, exception) — throw exception into continuation (non-tail).
#[pyclass(name = "ResumeThrow", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyResumeThrow {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
    #[pyo3(get)]
    pub exception: Py<PyAny>,
}

#[pymethods]
impl PyResumeThrow {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(k: Py<PyK>, exception: Py<PyAny>) -> Self {
        Self {
            continuation: k,
            exception,
        }
    }

    fn __repr__(&self) -> &'static str {
        "ResumeThrow(k, ...)"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.continuation)?;
        visit_py_field(&visit, &self.exception)
    }
}

/// TransferThrow(k, exception) — throw exception into continuation (tail).
#[pyclass(name = "TransferThrow", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyTransferThrow {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
    #[pyo3(get)]
    pub exception: Py<PyAny>,
}

#[pymethods]
impl PyTransferThrow {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(k: Py<PyK>, exception: Py<PyAny>) -> Self {
        Self {
            continuation: k,
            exception,
        }
    }

    fn __repr__(&self) -> &'static str {
        "TransferThrow(k, ...)"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.continuation)?;
        visit_py_field(&visit, &self.exception)
    }
}

/// WithObserve(observer, body) — install observer and run body under it.
#[pyclass(name = "WithObserve", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyWithObserve {
    #[pyo3(get)]
    pub observer: Py<PyAny>,
    #[pyo3(get)]
    pub body: Py<PyAny>,
}

#[pymethods]
impl PyWithObserve {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }
    #[classmethod]
    fn __class_getitem__<'py>(
        cls: &Bound<'py, pyo3::types::PyType>,
        item: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::class_getitem(cls, item)
    }

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

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.observer)?;
        visit_py_field(&visit, &self.body)
    }
}

/// GetTraceback(k) — query traceback from continuation without consuming it.
#[pyclass(name = "GetTraceback", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyGetTraceback {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
}

#[pymethods]
impl PyGetTraceback {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(k: Py<PyK>) -> Self {
        Self { continuation: k }
    }

    fn __repr__(&self) -> &'static str {
        "GetTraceback(k)"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.continuation)
    }
}

/// GetExecutionContext() — get current execution context.
#[pyclass(
    name = "GetExecutionContext",
    frozen,
    dict,
    module = "doeff_vm.doeff_vm"
)]
pub struct PyGetExecutionContext;

#[pymethods]
impl PyGetExecutionContext {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

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
#[pyclass(name = "GetHandlers", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyGetHandlers {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
}

#[pymethods]
impl PyGetHandlers {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(k: Py<PyK>) -> Self {
        Self { continuation: k }
    }

    fn __repr__(&self) -> &'static str {
        "GetHandlers(k)"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.continuation)
    }
}

/// GetBoundaries(k) — extract the interleaved handler/observer boundary
/// stack from the continuation's fiber chain, innermost first. Each entry is
/// a `["handler" | "observer", callable]` pair. The catching handler is
/// included as the last entry, symmetric with GetHandlers(k).
///
/// Used by the scheduler to reinstall the full spawn-site boundary stack —
/// handlers AND WithObserve observers, preserving their relative nesting
/// order — on Spawn'd tasks (issue scheduler-spawn-drops-observer-boundary).
#[pyclass(name = "GetBoundaries", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyGetBoundaries {
    #[pyo3(get)]
    pub continuation: Py<PyK>,
}

#[pymethods]
impl PyGetBoundaries {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(k: Py<PyK>) -> Self {
        Self { continuation: k }
    }

    fn __repr__(&self) -> &'static str {
        "GetBoundaries(k)"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.continuation)
    }
}

/// GetOuterHandlers — extract handlers installed ABOVE the current handler.
///
/// When a handler catches an effect, its segment's parent is detached from the
/// chain. This means GetHandlers(k) cannot reach handlers installed above the
/// catching handler. GetOuterHandlers walks from the VM's current_segment
/// upward, capturing those outer handlers.
///
/// Used by MCP server runners that need the COMPLETE handler stack as it was
/// at the Launch site — both inner (from GetHandlers(k)) and outer (from this).
#[pyclass(name = "GetOuterHandlers", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyGetOuterHandlers {}

#[pymethods]
impl PyGetOuterHandlers {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new() -> Self {
        Self {}
    }

    fn __repr__(&self) -> &'static str {
        "GetOuterHandlers()"
    }
}

/// TailEval(expr) — evaluate a DoExpr in tail position (pop the current
/// handler stream frame before evaluating). Used by the scheduler to avoid
/// orphaned stream frames that accumulate memory.
#[pyclass(name = "TailEval", frozen, dict, module = "doeff_vm.doeff_vm")]
pub struct PyTailEval {
    #[pyo3(get)]
    pub expr: Py<PyAny>,
}

#[pymethods]
impl PyTailEval {
    /// `x = yield from node` ≡ `x = yield node` (see crate::typing_support).
    fn __iter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, PyAny>> {
        crate::typing_support::bind_iter(slf.as_any())
    }

    #[new]
    fn new(expr: Py<PyAny>) -> Self {
        Self { expr }
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let e = self.expr.bind(py).repr()?;
        Ok(format!("TailEval({})", e))
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.expr)
    }
}
