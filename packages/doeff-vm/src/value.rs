//! Value types that flow through the VM.
//!
//! Values can be either Rust-native (for optimization) or Python objects.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyInt, PyNone, PyString};

/// A value that can flow through the VM.
///
/// Can be either a Rust-native value or a Python object.
/// Rust-native variants avoid Python overhead for common cases.
#[derive(Debug, Clone)]
pub enum Value {
    /// Python object (GIL-independent storage via Py<T>)
    Python(Py<PyAny>),

    /// Rust unit (for primitives that don't return meaningful values)
    Unit,

    /// Rust integer (optimization for common case)
    Int(i64),

    /// Rust string (optimization for common case)
    String(String),

    /// Rust boolean
    Bool(bool),

    /// None/null
    None,
}

impl Value {
    /// Convert to Python object (requires GIL).
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match self {
            Value::Python(obj) => Ok(obj.bind(py).clone()),
            Value::Unit => Ok(py.None().into_bound(py)),
            Value::Int(i) => Ok(i.into_pyobject(py)?.into_any()),
            Value::String(s) => Ok(PyString::new(py, s).into_any()),
            Value::Bool(b) => Ok(b.into_pyobject(py)?.into_any()),
            Value::None => Ok(py.None().into_bound(py)),
        }
    }

    /// Create from Python object (requires GIL).
    ///
    /// Attempts to extract as primitive for optimization.
    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> Self {
        // Try to extract as primitive for optimization
        // Order matters: check bool before int (bool is subclass of int in Python)
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
}

impl Default for Value {
    fn default() -> Self {
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
}
