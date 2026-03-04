//! Shared PyO3 bridge types used by VM core and the cdylib glue.

use std::sync::OnceLock;

use pyo3::exceptions::{PyRuntimeError, PyStopIteration};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::do_ctrl::DoCtrl;
use crate::driver::PyException;
use crate::vm::VM;
pub use crate::effect::PyEffectBase;

/// Discriminant stored as `tag: u8` on control/effect base classes.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DoExprTag {
    Pure = 0,
    Map = 2,
    FlatMap = 3,
    WithHandler = 4,
    Perform = 5,
    Resume = 6,
    Transfer = 7,
    Delegate = 8,
    GetContinuation = 9,
    GetHandlers = 10,
    GetCallStack = 11,
    Eval = 12,
    CreateContinuation = 13,
    ResumeContinuation = 14,
    AsyncEscape = 15,
    Apply = 16,
    Expand = 17,
    Pass = 19,
    GetTraceback = 20,
    WithIntercept = 21,
    Finally = 22,
    EvalInScope = 23,
    Effect = 128,
    Unknown = 255,
}

impl TryFrom<u8> for DoExprTag {
    type Error = u8;

    fn try_from(v: u8) -> Result<Self, u8> {
        match v {
            0 => Ok(DoExprTag::Pure),
            2 => Ok(DoExprTag::Map),
            3 => Ok(DoExprTag::FlatMap),
            4 => Ok(DoExprTag::WithHandler),
            5 => Ok(DoExprTag::Perform),
            6 => Ok(DoExprTag::Resume),
            7 => Ok(DoExprTag::Transfer),
            8 => Ok(DoExprTag::Delegate),
            9 => Ok(DoExprTag::GetContinuation),
            10 => Ok(DoExprTag::GetHandlers),
            11 => Ok(DoExprTag::GetCallStack),
            12 => Ok(DoExprTag::Eval),
            13 => Ok(DoExprTag::CreateContinuation),
            14 => Ok(DoExprTag::ResumeContinuation),
            15 => Ok(DoExprTag::AsyncEscape),
            16 => Ok(DoExprTag::Apply),
            17 => Ok(DoExprTag::Expand),
            19 => Ok(DoExprTag::Pass),
            20 => Ok(DoExprTag::GetTraceback),
            21 => Ok(DoExprTag::WithIntercept),
            22 => Ok(DoExprTag::Finally),
            23 => Ok(DoExprTag::EvalInScope),
            128 => Ok(DoExprTag::Effect),
            255 => Ok(DoExprTag::Unknown),
            other => Err(other),
        }
    }
}

#[pyclass(subclass, frozen, name = "DoExpr")]
pub struct PyDoExprBase;

impl PyDoExprBase {
    fn new_base() -> Self {
        PyDoExprBase
    }
}

#[pymethods]
impl PyDoExprBase {
    #[new]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(_args: &Bound<'_, PyTuple>, _kwargs: Option<&Bound<'_, PyDict>>) -> Self {
        PyDoExprBase::new_base()
    }

    fn to_generator(slf: Py<Self>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let expr = slf.into_any();
        let gen = Bound::new(
            py,
            DoExprOnceGenerator {
                expr: Some(expr),
                done: false,
            },
        )?
        .into_any()
        .unbind();
        Ok(gen)
    }
}

#[pyclass(name = "_DoExprOnceGenerator")]
struct DoExprOnceGenerator {
    expr: Option<Py<PyAny>>,
    done: bool,
}

#[pymethods]
impl DoExprOnceGenerator {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        if self.done {
            return Ok(None);
        }
        self.done = true;
        let expr = self
            .expr
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("DoExprOnceGenerator already consumed"))?;
        let _ = py;
        Ok(Some(expr))
    }

    fn send(&mut self, py: Python<'_>, value: Py<PyAny>) -> PyResult<Py<PyAny>> {
        if !self.done {
            return match self.__next__(py)? {
                Some(v) => Ok(v),
                None => Err(PyStopIteration::new_err(py.None())),
            };
        }
        Err(PyStopIteration::new_err((value,)))
    }

    fn throw(&mut self, _py: Python<'_>, exc: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        Err(PyErr::from_value(exc))
    }
}

#[pyclass(subclass, frozen, extends=PyDoExprBase, name = "DoCtrlBase")]
pub struct PyDoCtrlBase {
    #[pyo3(get)]
    pub tag: u8,
}

#[pymethods]
impl PyDoCtrlBase {
    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase).add_subclass(PyDoCtrlBase {
            tag: DoExprTag::Unknown as u8,
        })
    }
}

#[pyclass(name = "K")]
pub struct PyK {
    pub cont_id: crate::ids::ContId,
}

impl PyK {
    pub fn from_cont_id(cont_id: crate::ids::ContId) -> Self {
        Self { cont_id }
    }
}

#[pymethods]
impl PyK {
    fn __repr__(&self) -> String {
        format!("K({})", self.cont_id.raw())
    }
}

#[pyclass(frozen, name = "TraceFrame")]
pub struct PyTraceFrame {
    #[pyo3(get)]
    pub func_name: String,
    #[pyo3(get)]
    pub source_file: String,
    #[pyo3(get)]
    pub source_line: u32,
}

#[pymethods]
impl PyTraceFrame {
    #[new]
    fn new(func_name: String, source_file: String, source_line: u32) -> Self {
        Self {
            func_name,
            source_file,
            source_line,
        }
    }
}

#[pyclass(frozen, name = "TraceHop")]
pub struct PyTraceHop {
    #[pyo3(get)]
    pub frames: Vec<Py<PyTraceFrame>>,
}

#[pymethods]
impl PyTraceHop {
    #[new]
    fn new(frames: Vec<Py<PyTraceFrame>>) -> Self {
        Self { frames }
    }
}

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

pub fn is_effect_base_like(obj: &Bound<'_, PyAny>) -> bool {
    obj.is_instance_of::<crate::effect::PyEffectBase>()
}

pub fn is_doexpr_like(obj: &Bound<'_, PyAny>) -> bool {
    obj.is_instance_of::<PyDoExprBase>() || obj.is_instance_of::<crate::doeff_generator::DoeffGenerator>()
}

pub fn doctrl_tag(obj: &Bound<'_, PyAny>) -> Option<DoExprTag> {
    obj.extract::<PyRef<'_, PyDoCtrlBase>>()
        .ok()
        .and_then(|base| DoExprTag::try_from(base.tag).ok())
}

pub type ClassifyYieldedHook = for<'py> fn(
    &VM,
    Python<'py>,
    &Bound<'py, PyAny>,
) -> Result<DoCtrl, PyException>;

pub type DoctrlToPyexprHook = fn(&DoCtrl) -> Result<Option<Py<PyAny>>, PyException>;

#[derive(Clone, Copy)]
pub struct VmHooks {
    pub classify_yielded: ClassifyYieldedHook,
    pub doctrl_to_pyexpr: DoctrlToPyexprHook,
}

static VM_HOOKS: OnceLock<VmHooks> = OnceLock::new();

pub fn install_vm_hooks(hooks: VmHooks) {
    let _ = VM_HOOKS.set(hooks);
}

pub fn classify_yielded_for_vm(
    vm: &VM,
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<DoCtrl, PyException> {
    let hooks = VM_HOOKS
        .get()
        .ok_or_else(|| PyException::runtime_error("VM hooks not installed: classify_yielded"))?;
    (hooks.classify_yielded)(vm, py, obj)
}

pub fn doctrl_to_pyexpr_for_vm(yielded: &DoCtrl) -> Result<Option<Py<PyAny>>, PyException> {
    let hooks = VM_HOOKS
        .get()
        .ok_or_else(|| PyException::runtime_error("VM hooks not installed: doctrl_to_pyexpr"))?;
    (hooks.doctrl_to_pyexpr)(yielded)
}
