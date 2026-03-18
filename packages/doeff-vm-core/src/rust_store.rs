//! Store model shared by handlers and VM.

use std::collections::HashMap;

use pyo3::exceptions::PyTypeError;
use pyo3::prelude::{Bound, PyAny, PyResult};
#[cfg(not(test))]
use pyo3::types::PyString;
#[cfg(not(test))]
use pyo3::Python;

use crate::py_key::HashedPyKey;
use crate::value::Value;

#[derive(Debug, Clone)]
pub struct RustStore {
    state_slots: HashMap<String, Value>,
    bindings: HashMap<HashedPyKey, Value>,
    log_entries: Vec<Value>,
}

impl From<&HashedPyKey> for HashedPyKey {
    fn from(value: &HashedPyKey) -> Self {
        value.clone()
    }
}

#[cfg(test)]
impl From<&str> for HashedPyKey {
    fn from(value: &str) -> Self {
        HashedPyKey::from_test_string(value)
    }
}

#[cfg(test)]
impl From<&String> for HashedPyKey {
    fn from(value: &String) -> Self {
        HashedPyKey::from_test_string(value)
    }
}

#[cfg(test)]
impl From<String> for HashedPyKey {
    fn from(value: String) -> Self {
        HashedPyKey::from_test_string(value)
    }
}

fn py_key_to_hashed(key_obj: &Bound<'_, PyAny>) -> PyResult<HashedPyKey> {
    HashedPyKey::from_bound(key_obj).map_err(|err| {
        PyTypeError::new_err(format!(
            "environment key must be hashable, got error: {err}"
        ))
    })
}

fn string_key_to_hashed(key: &str) -> PyResult<HashedPyKey> {
    #[cfg(test)]
    {
        return Ok(HashedPyKey::from_test_string(key));
    }

    #[cfg(not(test))]
    {
        Python::attach(|py| {
            let py_key = PyString::new(py, key).into_any();
            py_key_to_hashed(&py_key)
        })
    }
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            state_slots: HashMap::new(),
            bindings: HashMap::new(),
            log_entries: Vec::new(),
        }
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.state_slots.get(key)
    }

    pub fn put(&mut self, key: String, value: Value) {
        self.state_slots.insert(key, value);
    }

    pub fn state_entries(&self) -> &HashMap<String, Value> {
        &self.state_slots
    }

    pub fn ask(&self, key: impl Into<HashedPyKey>) -> Option<&Value> {
        let key = key.into();
        self.bindings.get(&key)
    }

    pub fn insert_binding(&mut self, key: HashedPyKey, value: Value) {
        self.bindings.insert(key, value);
    }

    pub fn bindings(&self) -> &HashMap<HashedPyKey, Value> {
        &self.bindings
    }

    #[cfg(test)]
    pub fn ask_str(&self, key: &str) -> Option<&Value> {
        self.bindings.get(&HashedPyKey::from_test_string(key))
    }

    #[cfg(test)]
    pub fn set_env_str(&mut self, key: impl Into<String>, value: Value) {
        self.bindings
            .insert(HashedPyKey::from_test_string(key), value);
    }

    pub fn tell(&mut self, message: Value) {
        self.log_entries.push(message);
    }

    pub fn logs(&self) -> &[Value] {
        &self.log_entries
    }

    pub fn modify(&mut self, key: &str, f: impl FnOnce(&Value) -> Value) -> Option<Value> {
        let old = self.state_slots.get(key)?;
        let new_val = f(old);
        let old_clone = old.clone();
        self.state_slots.insert(key.to_string(), new_val);
        Some(old_clone)
    }

    pub fn with_local<F, R>(&mut self, bindings: HashMap<String, Value>, f: F) -> PyResult<R>
    where
        F: FnOnce(&mut Self) -> R,
    {
        let hashed_bindings: HashMap<HashedPyKey, Value> = bindings
            .into_iter()
            .map(|(k, v)| string_key_to_hashed(&k).map(|hashed| (hashed, v)))
            .collect::<PyResult<_>>()?;

        let old: HashMap<HashedPyKey, Value> = hashed_bindings
            .keys()
            .filter_map(|k| self.bindings.get(k).map(|v| (k.clone(), v.clone())))
            .collect();
        let new_keys: Vec<HashedPyKey> = hashed_bindings
            .keys()
            .filter(|k| !old.contains_key(*k))
            .cloned()
            .collect();

        for (k, v) in hashed_bindings {
            self.bindings.insert(k, v);
        }

        let result = f(self);

        for (k, v) in old {
            self.bindings.insert(k, v);
        }
        for k in new_keys {
            self.bindings.remove(&k);
        }

        Ok(result)
    }

    pub fn clear_logs(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.log_entries)
    }
}

impl Default for RustStore {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use pyo3::exceptions::PyTypeError;
    use pyo3::types::{PyAnyMethods, PyModule};
    use pyo3::Python;

    use super::py_key_to_hashed;

    #[test]
    fn py_key_to_hashed_maps_unhashable_to_type_error() {
        Python::attach(|py| {
            let module = PyModule::from_code(
                py,
                pyo3::ffi::c_str!("class Unhashable:\n    __hash__ = None\nobj = Unhashable()\n"),
                pyo3::ffi::c_str!("test_key.py"),
                pyo3::ffi::c_str!("test_key"),
            )
            .expect("failed to create test module");
            let obj = module.getattr("obj").expect("obj must be present");
            let err = py_key_to_hashed(&obj).expect_err("unhashable key must error");
            assert!(err.is_instance_of::<PyTypeError>(py));
        });
    }
}
