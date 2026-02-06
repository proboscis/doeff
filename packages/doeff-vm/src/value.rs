//! Value types that flow through the VM.
//!
//! Values can be either Rust-native (for optimization) or Python objects.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyList, PyString};

use crate::handler::Handler;

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
}

impl Value {
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
                        Handler::Python(py_handler) => {
                            list.append(py_handler.bind(py))?;
                        }
                        Handler::Stdlib(stdlib_handler) => {
                            let label = match stdlib_handler {
                                crate::handler::StdlibHandler::State => "stdlib:State",
                                crate::handler::StdlibHandler::Reader => "stdlib:Reader",
                                crate::handler::StdlibHandler::Writer => "stdlib:Writer",
                            };
                            list.append(PyString::new(py, label))?;
                        }
                        Handler::RustProgram(_) => {
                            list.append(PyString::new(py, "rust_program_handler"))?;
                        }
                    }
                }
                Ok(list.into_any())
            }
        }
    }

    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> Self {
        if obj.is_none() {
            return Value::None;
        }
        if let Ok(b) = obj.downcast::<PyBool>() {
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
        }
    }
}

impl Value {
    pub fn from_effect(effect: &crate::effect::Effect) -> Self {
        match effect {
            crate::effect::Effect::Python(py_obj) => Value::Python(py_obj.clone()),
            _ => Value::None,
        }
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
        let handlers = vec![Handler::Stdlib(crate::handler::StdlibHandler::State)];
        let val = Value::Handlers(handlers);
        assert!(val.as_handlers().is_some());
        assert_eq!(val.as_handlers().unwrap().len(), 1);
    }
}
