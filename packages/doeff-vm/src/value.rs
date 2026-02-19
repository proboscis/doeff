//! Value types that flow through the VM.
//!
//! Values can be either Rust-native (for optimization) or Python objects.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString};

use crate::capture::{
    ActiveChainEntry, DispatchAction, EffectResult, HandlerDispatchEntry, HandlerKind,
    HandlerStatus, TraceEntry,
};
use crate::frame::CallMetadata;
use crate::handler::Handler;
use crate::scheduler::{ExternalPromise, PromiseHandle, TaskHandle};

/// A value that can flow through the VM.
///
/// Can be either a Rust-native value or a Python object.
/// Rust-native variants avoid Python overhead for common cases.
#[derive(Debug, Clone)]
pub enum Value {
    Python(Py<PyAny>),
    Unit,
    Int(i64),
    String(String),
    Bool(bool),
    None,
    Continuation(crate::continuation::Continuation),
    Handlers(Vec<Handler>),
    Task(TaskHandle),
    Promise(PromiseHandle),
    ExternalPromise(ExternalPromise),
    CallStack(Vec<CallMetadata>),
    Trace(Vec<TraceEntry>),
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
        let trace_mod = py.import("doeff.trace").ok();
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
                sub_program_repr,
            } => {
                dict.set_item("kind", "program_yield")?;
                dict.set_item("function_name", function_name)?;
                dict.set_item("source_file", source_file)?;
                dict.set_item("source_line", *source_line)?;
                dict.set_item("sub_program_repr", sub_program_repr)?;
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
            ActiveChainEntry::SpawnBoundary {
                task_id,
                parent_task,
                spawn_site,
            } => {
                dict.set_item("kind", "spawn_boundary")?;
                dict.set_item("task_id", *task_id)?;
                dict.set_item("parent_task", *parent_task)?;
                if let Some(site) = spawn_site {
                    let site_dict = PyDict::new(py);
                    site_dict.set_item("function_name", site.function_name.as_str())?;
                    site_dict.set_item("source_file", site.source_file.as_str())?;
                    site_dict.set_item("source_line", site.source_line)?;
                    dict.set_item("spawn_site", site_dict)?;
                } else {
                    dict.set_item("spawn_site", py.None())?;
                }
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
            Value::Python(obj) => Ok(obj.bind(py).clone()),
            Value::Unit => Ok(py.None().into_bound(py)),
            Value::Int(i) => Ok(i.into_pyobject(py)?.into_any()),
            Value::String(s) => Ok(PyString::new(py, s).into_any()),
            Value::Bool(b) => Ok(PyBool::new(py, *b).to_owned().into_any()),
            Value::None => Ok(py.None().into_bound(py)),
            Value::Continuation(k) => k.to_pyobject(py),
            Value::Handlers(handlers) => {
                let list = PyList::empty(py);
                for h in handlers {
                    match h {
                        Handler::Python {
                            callable: py_handler,
                            ..
                        } => {
                            let bound = py_handler.bind(py);
                            if let Ok(original) = bound.getattr("__doeff_original_handler__") {
                                list.append(original)?;
                            } else {
                                list.append(bound)?;
                            }
                        }
                        Handler::RustProgram(_) => {
                            list.append(py.None().into_bound(py))?;
                        }
                    }
                }
                Ok(list.into_any())
            }
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
            Value::ActiveChain(entries) => {
                let list = PyList::empty(py);
                for entry in entries {
                    list.append(Self::active_chain_entry_to_pyobject(py, entry)?)?;
                }
                Ok(list.into_any())
            }
        }
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
        Value::Python(obj.clone().unbind())
    }

    /// Create from Python object, consuming it.
    pub fn from_pyobject_owned(obj: Bound<'_, PyAny>) -> Self {
        Self::from_pyobject(&obj)
    }

    /// Check if this is a None/Unit value.
    pub fn is_none(&self) -> bool {
        matches!(self, Value::None | Value::Unit)
    }

    /// Check if this is a Python object.
    pub fn is_python(&self) -> bool {
        matches!(self, Value::Python(_))
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
    pub fn as_handlers(&self) -> Option<&[Handler]> {
        match self {
            Value::Handlers(h) => Some(h),
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
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            Value::Python(obj) => Value::Python(obj.clone_ref(py)),
            Value::Unit => Value::Unit,
            Value::Int(i) => Value::Int(*i),
            Value::String(s) => Value::String(s.clone()),
            Value::Bool(b) => Value::Bool(*b),
            Value::None => Value::None,
            Value::Continuation(k) => Value::Continuation(k.clone()),
            Value::Handlers(handlers) => Value::Handlers(handlers.clone()),
            Value::Task(h) => Value::Task(*h),
            Value::Promise(h) => Value::Promise(*h),
            Value::ExternalPromise(h) => Value::ExternalPromise(h.clone()),
            Value::CallStack(stack) => Value::CallStack(stack.clone()),
            Value::Trace(entries) => Value::Trace(entries.clone()),
            Value::ActiveChain(entries) => Value::ActiveChain(entries.clone()),
            Value::List(items) => Value::List(items.iter().map(|v| v.clone_ref(py)).collect()),
        }
    }
}

impl Value {
    pub fn from_effect(effect: &crate::effect::Effect) -> Self {
        if let Some(py_obj) = effect.as_python() {
            let py = unsafe { pyo3::Python::assume_attached() };
            return Value::Python(py_obj.clone_ref(py));
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
        let handlers = vec![Handler::RustProgram(std::sync::Arc::new(
            crate::handler::StateHandlerFactory,
        ))];
        let val = Value::Handlers(handlers);
        assert!(val.as_handlers().is_some());
        assert_eq!(val.as_handlers().unwrap().len(), 1);
    }

    #[test]
    fn test_value_task_and_promise() {
        use crate::ids::{PromiseId, TaskId};
        use crate::scheduler::{ExternalPromise, PromiseHandle, TaskHandle};

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
}
