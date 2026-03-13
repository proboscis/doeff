//! Value types that flow through the VM.
//!
//! Values can be either Rust-native (for optimization) or opaque runtime
//! objects wrapped in `OpaqueRef`.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString};

use crate::capture::{
    ActiveChainEntry, DispatchAction, EffectResult, HandlerDispatchEntry, HandlerKind,
    HandlerStatus, TraceEntry, TraceHop,
};
use crate::frame::CallMetadata;
use crate::ids::{PromiseId, TaskId};
use crate::kleisli::KleisliRef;
use crate::opaque_ref::OpaqueRef;
use crate::py_shared::OpaqueRefPyExt;
use crate::pyvm::{PyTraceFrame, PyTraceHop};

/// Opaque handle to a spawned task.
#[derive(Clone, Copy, Debug)]
pub struct TaskHandle {
    pub id: TaskId,
}

/// Opaque handle to a promise.
#[derive(Clone, Copy, Debug)]
pub struct PromiseHandle {
    pub id: PromiseId,
}

/// External promise that can be completed from outside the scheduler.
#[derive(Clone, Debug)]
pub struct ExternalPromise {
    pub id: PromiseId,
    pub completion_queue: Option<OpaqueRef>,
}

/// A value that can flow through the VM.
///
/// Can be either a Rust-native value or an opaque runtime object.
/// Rust-native variants avoid Python overhead for common cases.
#[derive(Debug, Clone)]
pub enum Value {
    Opaque(OpaqueRef),
    Unit,
    Int(i64),
    String(String),
    Bool(bool),
    None,
    Continuation(crate::continuation::Continuation),
    Handlers(Vec<KleisliRef>),
    Kleisli(KleisliRef),
    Task(TaskHandle),
    Promise(PromiseHandle),
    ExternalPromise(ExternalPromise),
    CallStack(Vec<CallMetadata>),
    Trace(Vec<TraceEntry>),
    Traceback(Vec<TraceHop>),
    ActiveChain(Vec<ActiveChainEntry>),
    List(Vec<Value>),
}

impl Value {
    fn handler_kind_to_str(kind: &HandlerKind) -> &'static str {
        match kind {
            HandlerKind::Python => "python",
            HandlerKind::RustBuiltin => "rust_builtin",
        }
    }

    fn dispatch_action_to_str(action: &DispatchAction) -> &'static str {
        match action {
            DispatchAction::Active => "active",
            DispatchAction::Resumed => "resumed",
            DispatchAction::Transferred => "transferred",
            DispatchAction::Returned => "returned",
            DispatchAction::Threw => "threw",
        }
    }

    fn handler_status_to_str(status: HandlerStatus) -> &'static str {
        match status {
            HandlerStatus::Active => "active",
            HandlerStatus::Pending => "pending",
            HandlerStatus::Passed => "passed",
            HandlerStatus::Delegated => "delegated",
            HandlerStatus::Resumed => "resumed",
            HandlerStatus::Transferred => "transferred",
            HandlerStatus::Returned => "returned",
            HandlerStatus::Threw => "threw",
        }
    }

    fn trace_entry_to_pyobject<'py>(
        py: Python<'py>,
        entry: &TraceEntry,
    ) -> PyResult<Bound<'py, PyAny>> {
        let trace_mod = py
            .import("importlib")
            .ok()
            .and_then(|mod_| mod_.call_method1("import_module", ("doeff.trace",)).ok());
        match entry {
            TraceEntry::Frame {
                frame_id,
                function_name,
                source_file,
                source_line,
                args_repr,
            } => {
                if let Some(mod_) = &trace_mod {
                    let cls = mod_.getattr("TraceFrame")?;
                    let obj = cls.call1((
                        *frame_id,
                        function_name.as_str(),
                        source_file.as_str(),
                        *source_line,
                        args_repr.clone(),
                    ))?;
                    Ok(obj.into_any())
                } else {
                    let dict = PyDict::new(py);
                    dict.set_item("kind", "frame")?;
                    dict.set_item("frame_id", *frame_id)?;
                    dict.set_item("function_name", function_name)?;
                    dict.set_item("source_file", source_file)?;
                    dict.set_item("source_line", *source_line)?;
                    dict.set_item("args_repr", args_repr.clone())?;
                    Ok(dict.into_any())
                }
            }
            TraceEntry::Dispatch {
                dispatch_id,
                effect_repr,
                handler_name,
                handler_kind,
                handler_source_file,
                handler_source_line,
                delegation_chain,
                action,
                value_repr,
                exception_repr,
            } => {
                if let Some(mod_) = &trace_mod {
                    let delegation_cls = mod_.getattr("TraceDelegationEntry")?;
                    let chain_items = PyList::empty(py);
                    for item in delegation_chain {
                        let obj = delegation_cls.call1((
                            item.handler_name.as_str(),
                            Self::handler_kind_to_str(&item.handler_kind),
                            item.handler_source_file.clone(),
                            item.handler_source_line,
                        ))?;
                        chain_items.append(obj)?;
                    }
                    let cls = mod_.getattr("TraceDispatch")?;
                    let obj = cls.call1((
                        dispatch_id.raw(),
                        effect_repr.as_str(),
                        handler_name.as_str(),
                        Self::handler_kind_to_str(handler_kind),
                        handler_source_file.clone(),
                        *handler_source_line,
                        chain_items.to_tuple(),
                        Self::dispatch_action_to_str(action),
                        value_repr.clone(),
                        exception_repr.clone(),
                    ))?;
                    Ok(obj.into_any())
                } else {
                    let dict = PyDict::new(py);
                    dict.set_item("kind", "dispatch")?;
                    dict.set_item("dispatch_id", dispatch_id.raw())?;
                    dict.set_item("effect_repr", effect_repr)?;
                    dict.set_item("handler_name", handler_name)?;
                    dict.set_item("handler_kind", Self::handler_kind_to_str(handler_kind))?;
                    dict.set_item("handler_source_file", handler_source_file.clone())?;
                    dict.set_item("handler_source_line", *handler_source_line)?;
                    let chain = PyList::empty(py);
                    for item in delegation_chain {
                        let row = PyDict::new(py);
                        row.set_item("handler_name", item.handler_name.as_str())?;
                        row.set_item(
                            "handler_kind",
                            Self::handler_kind_to_str(&item.handler_kind),
                        )?;
                        row.set_item("source_file", item.handler_source_file.clone())?;
                        row.set_item("source_line", item.handler_source_line)?;
                        chain.append(row)?;
                    }
                    dict.set_item("delegation_chain", chain)?;
                    dict.set_item("action", Self::dispatch_action_to_str(action))?;
                    dict.set_item("value_repr", value_repr.clone())?;
                    dict.set_item("exception_repr", exception_repr.clone())?;
                    Ok(dict.into_any())
                }
            }
            TraceEntry::ResumePoint {
                dispatch_id,
                handler_name,
                resumed_function_name,
                source_file,
                source_line,
                value_repr,
            } => {
                if let Some(mod_) = &trace_mod {
                    let cls = mod_.getattr("TraceResumePoint")?;
                    let obj = cls.call1((
                        dispatch_id.raw(),
                        handler_name.as_str(),
                        resumed_function_name.as_str(),
                        source_file.as_str(),
                        *source_line,
                        value_repr.clone(),
                    ))?;
                    Ok(obj.into_any())
                } else {
                    let dict = PyDict::new(py);
                    dict.set_item("kind", "resume_point")?;
                    dict.set_item("dispatch_id", dispatch_id.raw())?;
                    dict.set_item("handler_name", handler_name)?;
                    dict.set_item("resumed_function_name", resumed_function_name)?;
                    dict.set_item("source_file", source_file)?;
                    dict.set_item("source_line", *source_line)?;
                    dict.set_item("value_repr", value_repr.clone())?;
                    Ok(dict.into_any())
                }
            }
        }
    }

    fn effect_result_to_pyobject<'py>(
        py: Python<'py>,
        result: &EffectResult,
    ) -> PyResult<Bound<'py, PyAny>> {
        let dict = PyDict::new(py);
        match result {
            EffectResult::Resumed { value_repr } => {
                dict.set_item("kind", "resumed")?;
                dict.set_item("value_repr", value_repr)?;
            }
            EffectResult::Threw {
                handler_name,
                exception_repr,
            } => {
                dict.set_item("kind", "threw")?;
                dict.set_item("handler_name", handler_name)?;
                dict.set_item("exception_repr", exception_repr)?;
            }
            EffectResult::Transferred {
                handler_name,
                target_repr,
            } => {
                dict.set_item("kind", "transferred")?;
                dict.set_item("handler_name", handler_name)?;
                dict.set_item("target_repr", target_repr)?;
            }
            EffectResult::Active => {
                dict.set_item("kind", "active")?;
            }
        }
        Ok(dict.into_any())
    }

    fn handler_dispatch_to_pyobject<'py>(
        py: Python<'py>,
        entry: &HandlerDispatchEntry,
    ) -> PyResult<Bound<'py, PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("handler_name", entry.handler_name.as_str())?;
        dict.set_item(
            "handler_kind",
            Self::handler_kind_to_str(&entry.handler_kind),
        )?;
        dict.set_item("source_file", entry.source_file.clone())?;
        dict.set_item("source_line", entry.source_line)?;
        dict.set_item("status", Self::handler_status_to_str(entry.status))?;
        Ok(dict.into_any())
    }

    fn active_chain_entry_to_pyobject<'py>(
        py: Python<'py>,
        entry: &ActiveChainEntry,
    ) -> PyResult<Bound<'py, PyAny>> {
        let dict = PyDict::new(py);
        match entry {
            ActiveChainEntry::ProgramYield {
                function_name,
                source_file,
                source_line,
                args_repr,
                sub_program_repr,
                handler_kind,
            } => {
                dict.set_item("kind", "program_yield")?;
                dict.set_item("function_name", function_name)?;
                dict.set_item("source_file", source_file)?;
                dict.set_item("source_line", *source_line)?;
                dict.set_item("args_repr", args_repr.clone())?;
                dict.set_item("sub_program_repr", sub_program_repr)?;
                dict.set_item(
                    "handler_kind",
                    handler_kind.as_ref().map(Self::handler_kind_to_str),
                )?;
            }
            ActiveChainEntry::EffectYield {
                function_name,
                source_file,
                source_line,
                effect_repr,
                handler_stack,
                result,
            } => {
                dict.set_item("kind", "effect_yield")?;
                dict.set_item("function_name", function_name)?;
                dict.set_item("source_file", source_file)?;
                dict.set_item("source_line", *source_line)?;
                dict.set_item("effect_repr", effect_repr)?;
                let stack = PyList::empty(py);
                for item in handler_stack {
                    stack.append(Self::handler_dispatch_to_pyobject(py, item)?)?;
                }
                dict.set_item("handler_stack", stack)?;
                dict.set_item("result", Self::effect_result_to_pyobject(py, result)?)?;
            }
            ActiveChainEntry::ContextEntry { data } => {
                dict.set_item("kind", "context_entry")?;
                dict.set_item("data", data.bind(py))?;
            }
            ActiveChainEntry::ExceptionSite {
                function_name,
                source_file,
                source_line,
                exception_type,
                message,
            } => {
                dict.set_item("kind", "exception_site")?;
                dict.set_item("function_name", function_name)?;
                dict.set_item("source_file", source_file)?;
                dict.set_item("source_line", *source_line)?;
                dict.set_item("exception_type", exception_type)?;
                dict.set_item("message", message)?;
            }
        }
        Ok(dict.into_any())
    }

    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match self {
            Value::Opaque(obj) => Ok(obj.bind(py).clone()),
            Value::Unit => Ok(py.None().into_bound(py)),
            Value::Int(i) => Ok(i.into_pyobject(py)?.into_any()),
            Value::String(s) => Ok(PyString::new(py, s).into_any()),
            Value::Bool(b) => Ok(PyBool::new(py, *b).to_owned().into_any()),
            Value::None => Ok(py.None().into_bound(py)),
            Value::Continuation(k) => k.to_pyobject(py),
            Value::Handlers(handlers) => {
                let list = PyList::empty(py);
                for h in handlers {
                    if h.is_rust_builtin() {
                        list.append(py.None().into_bound(py))?;
                    } else if let Some(identity) = h.py_identity() {
                        list.append(identity.bind(py))?;
                    } else {
                        list.append(py.None().into_bound(py))?;
                    }
                }
                Ok(list.into_any())
            }
            Value::Kleisli(_) => unreachable!(
                "Value::Kleisli should never be converted to Python object — it is consumed internally by Apply/Expand"
            ),
            Value::Task(handle) => {
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("type", "Task")?;
                dict.set_item("task_id", handle.id.raw())?;
                Ok(dict.into_any())
            }
            Value::Promise(handle) => {
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("type", "Promise")?;
                dict.set_item("promise_id", handle.id.raw())?;
                Ok(dict.into_any())
            }
            Value::ExternalPromise(handle) => {
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("type", "ExternalPromise")?;
                dict.set_item("promise_id", handle.id.raw())?;
                if let Some(queue) = &handle.completion_queue {
                    dict.set_item("completion_queue", queue.bind(py))?;
                }
                Ok(dict.into_any())
            }
            Value::List(items) => {
                let list = PyList::empty(py);
                for item in items {
                    list.append(item.to_pyobject(py)?)?;
                }
                Ok(list.into_any())
            }
            Value::CallStack(stack) => {
                let list = PyList::empty(py);
                for m in stack {
                    let dict = PyDict::new(py);
                    dict.set_item("frame_id", m.frame_id)?;
                    dict.set_item("function_name", &m.function_name)?;
                    dict.set_item("source_file", &m.source_file)?;
                    dict.set_item("source_line", m.source_line)?;
                    dict.set_item("args_repr", m.args_repr.clone())?;
                    if let Some(ref pc) = m.program_call {
                        dict.set_item("program_call", pc.bind(py))?;
                    } else {
                        dict.set_item("program_call", py.None())?;
                    }
                    list.append(dict)?;
                }
                Ok(list.into_any())
            }
            Value::Trace(trace_entries) => {
                let list = PyList::empty(py);
                for entry in trace_entries {
                    list.append(Self::trace_entry_to_pyobject(py, entry)?)?;
                }
                Ok(list.into_any())
            }
            Value::Traceback(hops) => {
                let list = PyList::empty(py);
                for hop in hops {
                    let mut frames: Vec<Py<PyTraceFrame>> = Vec::with_capacity(hop.frames.len());
                    for frame in &hop.frames {
                        let py_frame = Py::new(
                            py,
                            PyTraceFrame {
                                func_name: frame.func_name.clone(),
                                source_file: frame.source_file.clone(),
                                source_line: frame.source_line,
                            },
                        )?;
                        frames.push(py_frame);
                    }
                    let py_hop = Py::new(py, PyTraceHop { frames })?;
                    list.append(py_hop)?;
                }
                Ok(list.into_any())
            }
            Value::ActiveChain(entries) => {
                let list = PyList::empty(py);
                for entry in entries {
                    list.append(Self::active_chain_entry_to_pyobject(py, entry)?)?;
                }
                Ok(list.into_any())
            }
        }
    }

    /// Preserve a Python object as-is without VM-side type coercion.
    pub fn from_python_opaque(obj: &Bound<'_, PyAny>) -> Self {
        Value::Opaque(OpaqueRef::new(obj.clone().unbind()))
    }

    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> Self {
        if obj.is_none() {
            return Value::None;
        }
        if let Ok(b) = obj.cast::<PyBool>() {
            return Value::Bool(b.is_true());
        }
        if let Ok(i) = obj.extract::<i64>() {
            return Value::Int(i);
        }
        if let Ok(s) = obj.extract::<String>() {
            return Value::String(s);
        }
        Value::Opaque(OpaqueRef::new(obj.clone().unbind()))
    }

    /// Create from Python object, consuming it.
    pub fn from_pyobject_owned(obj: Bound<'_, PyAny>) -> Self {
        Self::from_pyobject(&obj)
    }

    /// Check if this is a None/Unit value.
    pub fn is_none(&self) -> bool {
        matches!(self, Value::None | Value::Unit)
    }

    /// Check if this is an opaque runtime object.
    pub fn is_opaque(&self) -> bool {
        matches!(self, Value::Opaque(_))
    }

    /// Try to get as i64.
    pub fn as_int(&self) -> Option<i64> {
        match self {
            Value::Int(i) => Some(*i),
            _ => None,
        }
    }

    /// Try to get as string reference.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::String(s) => Some(s),
            _ => None,
        }
    }

    /// Try to get as bool.
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Value::Bool(b) => Some(*b),
            _ => None,
        }
    }

    /// Try to get as handlers slice.
    pub fn as_handlers(&self) -> Option<&[KleisliRef]> {
        match self {
            Value::Handlers(h) => Some(h),
            _ => None,
        }
    }

    /// Try to get the inner OpaqueRef.
    pub fn as_opaque(&self) -> Option<&OpaqueRef> {
        match self {
            Value::Opaque(o) => Some(o),
            _ => None,
        }
    }
}

impl Default for Value {
    fn default() -> Self {
        Value::None
    }
}

impl Value {
    pub fn from_effect(effect: &crate::effect::Effect) -> Self {
        if let Some(opaque) = effect.as_opaque() {
            return Value::Opaque(opaque.clone());
        }
        Value::None
    }
}

impl From<i64> for Value {
    fn from(i: i64) -> Self {
        Value::Int(i)
    }
}

impl From<String> for Value {
    fn from(s: String) -> Self {
        Value::String(s)
    }
}

impl From<&str> for Value {
    fn from(s: &str) -> Self {
        Value::String(s.to_string())
    }
}

impl From<bool> for Value {
    fn from(b: bool) -> Self {
        Value::Bool(b)
    }
}

impl From<()> for Value {
    fn from(_: ()) -> Self {
        Value::Unit
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_value_from_primitives() {
        assert!(matches!(Value::from(42i64), Value::Int(42)));
        assert!(matches!(Value::from("hello"), Value::String(s) if s == "hello"));
        assert!(matches!(Value::from(true), Value::Bool(true)));
        assert!(matches!(Value::from(()), Value::Unit));
    }

    #[test]
    fn test_value_accessors() {
        assert_eq!(Value::Int(42).as_int(), Some(42));
        assert_eq!(Value::String("hello".into()).as_str(), Some("hello"));
        assert_eq!(Value::Bool(true).as_bool(), Some(true));
        assert!(Value::None.is_none());
        assert!(Value::Unit.is_none());
    }

    #[test]
    fn test_value_handlers() {
        let handlers = vec![std::sync::Arc::new(crate::kleisli::RustKleisli::new(
            std::sync::Arc::new(crate::handler::StateHandlerFactory),
            "StateHandler".to_string(),
        )) as KleisliRef];
        let val = Value::Handlers(handlers);
        assert!(val.as_handlers().is_some());
        assert_eq!(val.as_handlers().unwrap().len(), 1);
    }

    #[test]
    fn test_value_task_and_promise() {
        use crate::ids::{PromiseId, TaskId};

        let task = Value::Task(TaskHandle {
            id: TaskId::from_raw(1),
        });
        let promise = Value::Promise(PromiseHandle {
            id: PromiseId::from_raw(2),
        });
        let ext = Value::ExternalPromise(ExternalPromise {
            id: PromiseId::from_raw(3),
            completion_queue: None,
        });

        // Verify they are distinct Value variants
        assert!(matches!(task, Value::Task(_)));
        assert!(matches!(promise, Value::Promise(_)));
        assert!(matches!(ext, Value::ExternalPromise(_)));
    }

    #[test]
    fn test_value_opaque_clone() {
        // OpaqueRef clone is cheap (Arc clone) — no GIL needed
        let opaque = OpaqueRef::new(42u64);
        let val = Value::Opaque(opaque);
        let cloned = val.clone();
        assert!(cloned.is_opaque());
    }
}
