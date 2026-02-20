//! Effect types that can be yielded by user code.
//!
//! Effects are the requests that user code makes, which handlers respond to.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::py_shared::PyShared;
use crate::pyvm::{DoExprTag, PyEffectBase};
#[cfg(test)]
use crate::value::Value;

// ---------------------------------------------------------------------------
// R11-A: #[pyclass] effect structs for isinstance-based classification
// ---------------------------------------------------------------------------

#[pyclass(frozen, name = "PyGet", extends=PyEffectBase)]
pub struct PyGet {
    #[pyo3(get)]
    pub key: String,
}

#[pyclass(frozen, name = "PyPut", extends=PyEffectBase)]
pub struct PyPut {
    #[pyo3(get)]
    pub key: String,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pyclass(frozen, name = "PyModify", extends=PyEffectBase)]
pub struct PyModify {
    #[pyo3(get)]
    pub key: String,
    #[pyo3(get)]
    pub func: Py<PyAny>,
}

#[pyclass(frozen, name = "PyAsk", extends=PyEffectBase)]
pub struct PyAsk {
    #[pyo3(get)]
    pub key: Py<PyAny>,
}

#[pyclass(frozen, name = "PyLocal", extends=PyEffectBase)]
pub struct PyLocal {
    #[pyo3(get)]
    pub env_update: Py<PyAny>,
    #[pyo3(get)]
    pub sub_program: Py<PyAny>,
}

#[pyclass(frozen, name = "PyTell", extends=PyEffectBase)]
pub struct PyTell {
    #[pyo3(get)]
    pub message: Py<PyAny>,
}

#[pyclass(frozen, name = "SpawnEffect", extends=PyEffectBase)]
pub struct PySpawn {
    #[pyo3(get)]
    pub program: Py<PyAny>,
    #[pyo3(get)]
    pub options: Py<PyAny>,
    #[pyo3(get)]
    pub handlers: Py<PyAny>,
    #[pyo3(get)]
    pub store_mode: Py<PyAny>,
}

#[pyclass(frozen, name = "GatherEffect", extends=PyEffectBase)]
pub struct PyGather {
    #[pyo3(get)]
    pub items: Py<PyAny>,
    #[pyo3(get)]
    pub _partial_results: Py<PyAny>,
}

#[pyclass(frozen, name = "RaceEffect", extends=PyEffectBase)]
pub struct PyRace {
    #[pyo3(get)]
    pub futures: Py<PyAny>,
}

#[pyclass(frozen, name = "CreatePromiseEffect", extends=PyEffectBase)]
pub struct PyCreatePromise;

#[pyclass(frozen, name = "CompletePromiseEffect", extends=PyEffectBase)]
pub struct PyCompletePromise {
    #[pyo3(get)]
    pub promise: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pyclass(frozen, name = "FailPromiseEffect", extends=PyEffectBase)]
pub struct PyFailPromise {
    #[pyo3(get)]
    pub promise: Py<PyAny>,
    #[pyo3(get)]
    pub error: Py<PyAny>,
}

#[pyclass(frozen, name = "CreateExternalPromiseEffect", extends=PyEffectBase)]
pub struct PyCreateExternalPromise;

#[pyclass(frozen, name = "PyCancelEffect", extends=PyEffectBase)]
pub struct PyCancelEffect {
    #[pyo3(get)]
    pub task: Py<PyAny>,
}

#[pyclass(frozen, name = "_SchedulerTaskCompleted", extends=PyEffectBase)]
pub struct PyTaskCompleted {
    #[pyo3(get)]
    pub task: Py<PyAny>,
    #[pyo3(get)]
    pub task_id: Py<PyAny>,
    #[pyo3(get)]
    pub handle_id: Py<PyAny>,
    #[pyo3(get)]
    pub result: Py<PyAny>,
}

#[pyclass(frozen, name = "CreateSemaphoreEffect", extends=PyEffectBase)]
pub struct PyCreateSemaphore {
    #[pyo3(get)]
    pub permits: i64,
}

#[pyclass(frozen, name = "AcquireSemaphoreEffect", extends=PyEffectBase)]
pub struct PyAcquireSemaphore {
    #[pyo3(get)]
    pub semaphore: Py<PyAny>,
}

#[pyclass(frozen, name = "ReleaseSemaphoreEffect", extends=PyEffectBase)]
pub struct PyReleaseSemaphore {
    #[pyo3(get)]
    pub semaphore: Py<PyAny>,
}

#[pyclass(frozen, name = "PythonAsyncioAwaitEffect", extends=PyEffectBase)]
pub struct PyPythonAsyncioAwaitEffect {
    #[pyo3(get)]
    pub awaitable: Py<PyAny>,
}

#[pyclass(frozen, name = "ResultSafeEffect", extends=PyEffectBase)]
pub struct PyResultSafeEffect {
    #[pyo3(get)]
    pub sub_program: Py<PyAny>,
}

#[pyclass(frozen, name = "ProgramTraceEffect", extends=PyEffectBase)]
pub struct PyProgramTrace;

#[pyclass(frozen, name = "ProgramCallStackEffect", extends=PyEffectBase)]
pub struct PyProgramCallStack;

#[pyclass(frozen, name = "ProgramCallFrameEffect", extends=PyEffectBase)]
pub struct PyProgramCallFrame {
    #[pyo3(get)]
    pub depth: i64,
}

fn py_repr_or(py: Python<'_>, value: &Py<PyAny>, fallback: &str) -> String {
    value
        .bind(py)
        .repr()
        .map(|v| v.to_string())
        .unwrap_or_else(|_| fallback.to_string())
}

#[pymethods]
impl PyGet {
    #[new]
    fn new(key: String) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyGet { key })
    }

    fn __repr__(&self) -> String {
        format!("Get({:?})", self.key)
    }
}

#[pymethods]
impl PyPut {
    #[new]
    fn new(key: String, value: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyPut { key, value })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let value_repr = py_repr_or(py, &self.value, "<value>");
        format!("Put({:?}, {})", self.key, value_repr)
    }
}

#[pymethods]
impl PyModify {
    #[new]
    fn new(key: String, func: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyModify { key, func })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let func_repr = py_repr_or(py, &self.func, "<modifier>");
        format!("Modify({:?}, {})", self.key, func_repr)
    }
}

#[pymethods]
impl PyAsk {
    #[new]
    fn new(key: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyAsk { key })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let key_repr = py_repr_or(py, &self.key, "<key>");
        format!("Ask({})", key_repr)
    }
}

#[pymethods]
impl PyLocal {
    #[new]
    fn new(env_update: Py<PyAny>, sub_program: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyLocal {
            env_update,
            sub_program,
        })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let env_repr = py_repr_or(py, &self.env_update, "<env_update>");
        let sub_program_repr = py_repr_or(py, &self.sub_program, "<sub_program>");
        format!("Local({}, {})", env_repr, sub_program_repr)
    }
}

#[pymethods]
impl PyTell {
    #[new]
    fn new(message: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyTell { message })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let message_repr = self
            .message
            .bind(py)
            .repr()
            .map(|value| value.to_string())
            .unwrap_or_else(|_| "<message>".to_string());
        format!("Tell({})", message_repr)
    }
}

impl PySpawn {
    pub(crate) fn create(
        py: Python<'_>,
        program: Py<PyAny>,
        options: Option<Py<PyAny>>,
        handlers: Option<Py<PyAny>>,
        store_mode: Option<Py<PyAny>>,
    ) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PySpawn {
            program,
            options: options.unwrap_or_else(|| pyo3::types::PyDict::new(py).into_any().unbind()),
            handlers: handlers
                .unwrap_or_else(|| pyo3::types::PyList::empty(py).into_any().unbind()),
            store_mode: store_mode.unwrap_or_else(|| py.None()),
        })
    }
}

#[pymethods]
impl PySpawn {
    #[classattr]
    const __doeff_scheduler_spawn__: bool = true;

    #[new]
    #[pyo3(signature = (program, options=None, handlers=None, store_mode=None))]
    fn new(
        py: Python<'_>,
        program: Py<PyAny>,
        options: Option<Py<PyAny>>,
        handlers: Option<Py<PyAny>>,
        store_mode: Option<Py<PyAny>>,
    ) -> PyClassInitializer<Self> {
        Self::create(py, program, options, handlers, store_mode)
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let program_repr = py_repr_or(py, &self.program, "<program>");
        let handlers_repr = py_repr_or(py, &self.handlers, "<handlers>");
        let store_mode_repr = py_repr_or(py, &self.store_mode, "<store_mode>");
        format!(
            "Spawn(program={}, handlers={}, store_mode={})",
            program_repr, handlers_repr, store_mode_repr
        )
    }
}

impl PyGather {
    pub(crate) fn create(
        py: Python<'_>,
        items: Py<PyAny>,
        partial_results: Option<Py<PyAny>>,
    ) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyGather {
            items,
            _partial_results: partial_results.unwrap_or_else(|| py.None()),
        })
    }
}

#[pymethods]
impl PyGather {
    #[classattr]
    const __doeff_scheduler_gather__: bool = true;

    #[new]
    #[pyo3(signature = (items, _partial_results=None))]
    fn new(
        py: Python<'_>,
        items: Py<PyAny>,
        _partial_results: Option<Py<PyAny>>,
    ) -> PyClassInitializer<Self> {
        Self::create(py, items, _partial_results)
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let items_repr = py_repr_or(py, &self.items, "<items>");
        format!("Gather({})", items_repr)
    }
}

#[pymethods]
impl PyRace {
    #[classattr]
    const __doeff_scheduler_race__: bool = true;

    #[new]
    fn new(futures: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyRace { futures })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let futures_repr = py_repr_or(py, &self.futures, "<futures>");
        format!("Race({})", futures_repr)
    }
}

#[pymethods]
impl PyCreatePromise {
    #[classattr]
    const __doeff_scheduler_create_promise__: bool = true;

    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyCreatePromise)
    }

    fn __repr__(&self) -> String {
        "CreatePromise()".to_string()
    }
}

#[pymethods]
impl PyCompletePromise {
    #[classattr]
    const __doeff_scheduler_complete_promise__: bool = true;

    #[new]
    fn new(promise: Py<PyAny>, value: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyCompletePromise { promise, value })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let promise_repr = py_repr_or(py, &self.promise, "<promise>");
        let value_repr = py_repr_or(py, &self.value, "<value>");
        format!("CompletePromise({}, {})", promise_repr, value_repr)
    }
}

#[pymethods]
impl PyFailPromise {
    #[classattr]
    const __doeff_scheduler_fail_promise__: bool = true;

    #[new]
    fn new(promise: Py<PyAny>, error: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyFailPromise { promise, error })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let promise_repr = py_repr_or(py, &self.promise, "<promise>");
        let error_repr = py_repr_or(py, &self.error, "<error>");
        format!("FailPromise({}, {})", promise_repr, error_repr)
    }
}

#[pymethods]
impl PyCreateExternalPromise {
    #[classattr]
    const __doeff_scheduler_create_external_promise__: bool = true;

    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyCreateExternalPromise)
    }

    fn __repr__(&self) -> String {
        "CreateExternalPromise()".to_string()
    }
}

#[pymethods]
impl PyCancelEffect {
    #[classattr]
    const __doeff_scheduler_cancel__: bool = true;

    #[new]
    fn new(task: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyCancelEffect { task })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let task_repr = py_repr_or(py, &self.task, "<task>");
        format!("CancelTask({})", task_repr)
    }
}

#[pymethods]
impl PyTaskCompleted {
    #[classattr]
    const __doeff_scheduler_task_completed__: bool = true;

    #[new]
    #[pyo3(signature = (*, task=None, task_id=None, handle_id=None, result=None))]
    fn new(
        py: Python<'_>,
        task: Option<Py<PyAny>>,
        task_id: Option<Py<PyAny>>,
        handle_id: Option<Py<PyAny>>,
        result: Option<Py<PyAny>>,
    ) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyTaskCompleted {
            task: task.unwrap_or_else(|| py.None()),
            task_id: task_id.unwrap_or_else(|| py.None()),
            handle_id: handle_id.unwrap_or_else(|| py.None()),
            result: result.unwrap_or_else(|| py.None()),
        })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let task_repr = py_repr_or(py, &self.task, "<task>");
        let result_repr = py_repr_or(py, &self.result, "<result>");
        format!("TaskCompleted(task={}, result={})", task_repr, result_repr)
    }
}

#[pymethods]
impl PyCreateSemaphore {
    #[new]
    fn new(permits: i64) -> PyResult<PyClassInitializer<Self>> {
        if permits < 1 {
            return Err(PyValueError::new_err("permits must be >= 1"));
        }
        Ok(PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyCreateSemaphore { permits }))
    }

    fn __repr__(&self) -> String {
        format!("CreateSemaphore({})", self.permits)
    }
}

#[pymethods]
impl PyAcquireSemaphore {
    #[new]
    fn new(semaphore: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyAcquireSemaphore { semaphore })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let semaphore_repr = py_repr_or(py, &self.semaphore, "<semaphore>");
        format!("AcquireSemaphore({})", semaphore_repr)
    }
}

#[pymethods]
impl PyReleaseSemaphore {
    #[new]
    fn new(semaphore: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyReleaseSemaphore { semaphore })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let semaphore_repr = py_repr_or(py, &self.semaphore, "<semaphore>");
        format!("ReleaseSemaphore({})", semaphore_repr)
    }
}

#[pymethods]
impl PyPythonAsyncioAwaitEffect {
    #[new]
    fn new(awaitable: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyPythonAsyncioAwaitEffect { awaitable })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let awaitable_repr = py_repr_or(py, &self.awaitable, "<awaitable>");
        format!("PythonAsyncioAwaitEffect({})", awaitable_repr)
    }
}

#[pymethods]
impl PyResultSafeEffect {
    #[new]
    fn new(sub_program: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyResultSafeEffect { sub_program })
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let sub_program_repr = py_repr_or(py, &self.sub_program, "<sub_program>");
        format!("ResultSafe({})", sub_program_repr)
    }
}

#[pymethods]
impl PyProgramTrace {
    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyProgramTrace)
    }

    fn __repr__(&self) -> String {
        "ProgramTrace()".to_string()
    }
}

#[pymethods]
impl PyProgramCallStack {
    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyProgramCallStack)
    }

    fn __repr__(&self) -> String {
        "ProgramCallStack()".to_string()
    }
}

#[pymethods]
impl PyProgramCallFrame {
    #[new]
    #[pyo3(signature = (depth=0))]
    fn new(depth: i64) -> PyResult<PyClassInitializer<Self>> {
        if depth < 0 {
            return Err(PyValueError::new_err("depth must be >= 0"));
        }
        Ok(PyClassInitializer::from(PyEffectBase {
            tag: DoExprTag::Effect as u8,
        })
        .add_subclass(PyProgramCallFrame { depth }))
    }

    fn __repr__(&self) -> String {
        format!("ProgramCallFrame(depth={})", self.depth)
    }
}

#[cfg(not(test))]
#[derive(Debug, Clone)]
pub struct Effect(pub PyShared);

#[cfg(not(test))]
pub type DispatchEffect = PyShared;

#[cfg(test)]
/// An effect that can be yielded by user code.
///
/// Test-only enum keeps legacy fixtures for unit tests while runtime uses
/// opaque Python effects.
#[derive(Debug, Clone)]
pub enum Effect {
    Get { key: String },
    Put { key: String, value: Value },
    Modify { key: String, modifier: PyShared },
    Ask { key: String },
    Tell { message: Value },
    Python(PyShared),
}

#[cfg(test)]
pub type DispatchEffect = Effect;

pub fn dispatch_from_shared(obj: PyShared) -> DispatchEffect {
    #[cfg(test)]
    {
        return Effect::Python(obj);
    }
    #[cfg(not(test))]
    {
        obj
    }
}

pub fn dispatch_ref_as_python(effect: &DispatchEffect) -> Option<&PyShared> {
    #[cfg(test)]
    {
        return effect.as_python();
    }
    #[cfg(not(test))]
    {
        Some(effect)
    }
}

pub fn dispatch_into_python(effect: DispatchEffect) -> Option<PyShared> {
    #[cfg(test)]
    {
        return effect.into_python();
    }
    #[cfg(not(test))]
    {
        Some(effect)
    }
}

pub fn dispatch_clone_as_effect(effect: &DispatchEffect) -> Effect {
    #[cfg(test)]
    {
        effect.clone()
    }
    #[cfg(not(test))]
    {
        Effect(effect.clone())
    }
}

pub fn dispatch_into_effect(effect: DispatchEffect) -> Effect {
    #[cfg(test)]
    {
        effect
    }
    #[cfg(not(test))]
    {
        Effect(effect)
    }
}

pub fn dispatch_to_pyobject<'py>(
    py: Python<'py>,
    effect: &DispatchEffect,
) -> PyResult<Bound<'py, PyAny>> {
    #[cfg(test)]
    {
        return effect.to_pyobject(py);
    }
    #[cfg(not(test))]
    {
        Ok(effect.bind(py).clone())
    }
}

impl Effect {
    /// Check if this effect has a built-in Rust handler.
    /// Check if this is a standard effect (state/reader/writer only).
    /// NOTE: This does NOT mean bypass — all effects still go through dispatch.
    pub fn is_standard(&self) -> bool {
        #[cfg(test)]
        {
            return matches!(
                self,
                Effect::Get { .. }
                    | Effect::Put { .. }
                    | Effect::Modify { .. }
                    | Effect::Ask { .. }
                    | Effect::Tell { .. }
            );
        }
        #[cfg(not(test))]
        {
            false
        }
    }

    pub fn as_python(&self) -> Option<&PyShared> {
        #[cfg(test)]
        {
            if let Effect::Python(obj) = self {
                return Some(obj);
            }
            return None;
        }
        #[cfg(not(test))]
        {
            Some(&self.0)
        }
    }

    pub fn from_shared(obj: PyShared) -> Self {
        #[cfg(test)]
        {
            return Effect::Python(obj);
        }
        #[cfg(not(test))]
        {
            Effect(obj)
        }
    }

    pub fn into_python(self) -> Option<PyShared> {
        #[cfg(test)]
        {
            if let Effect::Python(obj) = self {
                return Some(obj);
            }
            return None;
        }
        #[cfg(not(test))]
        {
            Some(self.0)
        }
    }

    /// Get a string representation of the effect type.
    pub fn type_name(&self) -> &'static str {
        #[cfg(not(test))]
        {
            return "Python";
        }
        #[cfg(test)]
        match self {
            Effect::Get { .. } => "Get",
            Effect::Put { .. } => "Put",
            Effect::Modify { .. } => "Modify",
            Effect::Ask { .. } => "Ask",
            Effect::Tell { .. } => "Tell",
            Effect::Python(_) => "Python",
        }
    }

    /// Create a Get effect.
    #[cfg(test)]
    pub fn get(key: impl Into<String>) -> Self {
        Effect::Get { key: key.into() }
    }

    /// Create a Put effect.
    #[cfg(test)]
    pub fn put(key: impl Into<String>, value: impl Into<Value>) -> Self {
        Effect::Put {
            key: key.into(),
            value: value.into(),
        }
    }

    /// Create an Ask effect.
    #[cfg(test)]
    pub fn ask(key: impl Into<String>) -> Self {
        Effect::Ask { key: key.into() }
    }

    /// Create a Tell effect.
    #[cfg(test)]
    pub fn tell(message: impl Into<Value>) -> Self {
        Effect::Tell {
            message: message.into(),
        }
    }

    pub fn python(obj: Py<PyAny>) -> Self {
        Effect::from_shared(PyShared::new(obj))
    }

    /// Convert to Python object for passing to Python handlers.
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        #[cfg(not(test))]
        {
            if let Some(obj) = self.as_python() {
                return Ok(obj.bind(py).clone());
            }
            unreachable!("runtime Effect is always Python")
        }

        #[cfg(test)]
        match self {
            Effect::Python(obj) => Ok(obj.bind(py).clone()),
            // For built-in effects, we could create a Python wrapper
            // but typically these are handled in Rust directly
            _ => {
                // Create a dict representation for debugging
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("type", self.type_name())?;
                match self {
                    #[cfg(test)]
                    Effect::Get { key } => {
                        dict.set_item("key", key)?;
                    }
                    #[cfg(test)]
                    Effect::Put { key, value } => {
                        dict.set_item("key", key)?;
                        dict.set_item("value", value.to_pyobject(py)?)?;
                    }
                    #[cfg(test)]
                    Effect::Ask { key } => {
                        dict.set_item("key", key)?;
                    }
                    #[cfg(test)]
                    Effect::Tell { message } => {
                        dict.set_item("message", message.to_pyobject(py)?)?;
                    }
                    _ => {}
                }
                Ok(dict.into_any())
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_effect_constructors() {
        let get = Effect::get("key");
        assert!(matches!(get, Effect::Get { key } if key == "key"));

        let put = Effect::put("key", 42i64);
        assert!(matches!(put, Effect::Put { key, .. } if key == "key"));

        let ask = Effect::ask("env");
        assert!(matches!(ask, Effect::Ask { key } if key == "env"));

        let tell = Effect::tell("message");
        assert!(matches!(tell, Effect::Tell { .. }));
    }

    #[test]
    fn test_builtin_detection() {
        assert!(Effect::get("x").is_standard());
        assert!(Effect::put("x", 1i64).is_standard());
        assert!(Effect::ask("x").is_standard());
        assert!(Effect::tell("x").is_standard());
    }

    /// G14: opaque Python effects are NOT standard (state/reader/writer only).
    #[test]
    fn test_python_effect_not_standard() {
        let py = Python::attach(|py| py.None().into_any());
        let sched = Effect::Python(PyShared::new(py));
        assert!(
            !sched.is_standard(),
            "opaque Python effects should not be standard"
        );
    }
}
