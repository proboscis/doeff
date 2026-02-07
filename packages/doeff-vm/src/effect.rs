//! Effect types that can be yielded by user code.
//!
//! Effects are the requests that user code makes, which handlers respond to.

use pyo3::prelude::*;

use crate::frame::CallMetadata;
use crate::py_shared::PyShared;
use crate::value::Value;

#[derive(Debug, Clone)]
pub enum KpcArg {
    Value(Value),
    Expr(PyShared),
}

#[derive(Debug, Clone)]
pub struct KpcCallEffect {
    pub call: PyShared,
    pub kernel: PyShared,
    pub args: Vec<KpcArg>,
    pub kwargs: Vec<(String, KpcArg)>,
    pub metadata: CallMetadata,
}

/// An effect that can be yielded by user code.
///
/// Built-in effects have Rust variants for performance.
/// User-defined effects are wrapped as Python objects.
#[derive(Debug, Clone)]
pub enum Effect {
    // === Built-in effects (Rust handlers) ===
    /// Get(key) -> value
    #[cfg(test)]
    Get { key: String },

    /// Put(key, value) -> ()
    #[cfg(test)]
    Put { key: String, value: Value },

    /// Modify(key, f) -> old_value
    #[cfg(test)]
    Modify { key: String, modifier: PyShared },

    /// Ask(key) -> value (Reader effect)
    #[cfg(test)]
    Ask { key: String },

    /// Tell(message) -> () (Writer effect)
    #[cfg(test)]
    Tell { message: Value },

    // === User-defined effects (Python handlers) ===
    /// Any Python effect object
    Python(PyShared),
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

    /// Get a string representation of the effect type.
    pub fn type_name(&self) -> &'static str {
        match self {
            #[cfg(test)]
            Effect::Get { .. } => "Get",
            #[cfg(test)]
            Effect::Put { .. } => "Put",
            #[cfg(test)]
            Effect::Modify { .. } => "Modify",
            #[cfg(test)]
            Effect::Ask { .. } => "Ask",
            #[cfg(test)]
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
        Effect::Python(PyShared::new(obj))
    }

    /// Convert to Python object for passing to Python handlers.
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        #[cfg(not(test))]
        {
            let Effect::Python(obj) = self;
            return Ok(obj.bind(py).clone());
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
