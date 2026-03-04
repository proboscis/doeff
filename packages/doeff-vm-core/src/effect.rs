//! VM-internal effect base and opaque dispatch wrappers.

use pyo3::prelude::*;
use pyo3::types::PyList;

use crate::py_shared::PyShared;
use crate::pyvm::DoExprTag;

#[derive(Debug, Clone)]
pub struct Effect(pub PyShared);

pub type DispatchEffect = PyShared;

#[pyclass(subclass, frozen, name = "EffectBase")]
pub struct PyEffectBase {
    #[pyo3(get)]
    pub tag: u8,
}

impl PyEffectBase {
    pub fn new_base() -> Self {
        PyEffectBase {
            tag: DoExprTag::Effect as u8,
        }
    }
}

#[pymethods]
impl PyEffectBase {
    #[new]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(_args: &Bound<'_, pyo3::types::PyTuple>, _kwargs: Option<&Bound<'_, pyo3::types::PyDict>>) -> Self {
        Self::new_base()
    }
}

#[pyclass(frozen, name = "GetExecutionContext", extends=PyEffectBase)]
pub struct PyGetExecutionContext {}

#[pyclass(name = "ExecutionContext")]
pub struct PyExecutionContext {
    #[pyo3(get)]
    pub entries: Py<PyList>,
    #[pyo3(get)]
    pub active_chain: Option<Py<PyAny>>,
}

#[pymethods]
impl PyGetExecutionContext {
    #[new]
    pub fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase::new_base()).add_subclass(PyGetExecutionContext {})
    }

    fn __repr__(&self) -> String {
        "GetExecutionContext()".to_string()
    }
}

#[pymethods]
impl PyExecutionContext {
    #[new]
    pub fn new(py: Python<'_>) -> Self {
        PyExecutionContext {
            entries: PyList::empty(py).unbind(),
            active_chain: None,
        }
    }

    pub fn add(&mut self, py: Python<'_>, entry: Py<PyAny>) -> PyResult<()> {
        self.entries.bind(py).append(entry.bind(py))
    }

    pub fn set_active_chain(&mut self, active_chain: Option<Py<PyAny>>) {
        self.active_chain = active_chain;
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let has_active_chain = self.active_chain.is_some();
        format!(
            "ExecutionContext(entries={}, active_chain={has_active_chain})",
            self.entries.bind(py).len()
        )
    }
}

pub fn dispatch_from_shared(obj: PyShared) -> DispatchEffect {
    obj
}

pub fn dispatch_ref_as_python(effect: &DispatchEffect) -> Option<&PyShared> {
    Some(effect)
}

pub fn dispatch_into_python(effect: DispatchEffect) -> Option<PyShared> {
    Some(effect)
}

pub fn make_get_execution_context_effect() -> PyResult<DispatchEffect> {
    Python::attach(|py| {
        let effect = Bound::new(py, PyGetExecutionContext::new())?
            .into_any()
            .unbind();
        Ok(dispatch_from_shared(PyShared::new(effect)))
    })
}

pub fn make_execution_context_object(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let ctx = Bound::new(
        py,
        PyExecutionContext {
            entries: PyList::empty(py).unbind(),
            active_chain: None,
        },
    )?;
    Ok(ctx.into_any().unbind())
}

pub fn dispatch_to_pyobject<'py>(
    py: Python<'py>,
    effect: &DispatchEffect,
) -> PyResult<Bound<'py, PyAny>> {
    Ok(effect.bind(py).clone())
}

impl Effect {
    pub fn is_standard(&self) -> bool {
        false
    }

    pub fn as_python(&self) -> Option<&PyShared> {
        Some(&self.0)
    }

    pub fn from_shared(obj: PyShared) -> Self {
        Effect(obj)
    }

    pub fn into_python(self) -> Option<PyShared> {
        Some(self.0)
    }

    pub fn type_name(&self) -> &'static str {
        "Python"
    }

    pub fn python(obj: Py<PyAny>) -> Self {
        Effect::from_shared(PyShared::new(obj))
    }

    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        if let Some(obj) = self.as_python() {
            return Ok(obj.bind(py).clone());
        }
        unreachable!("runtime Effect is always Python")
    }
}
