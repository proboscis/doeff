//! Store model shared by handlers and VM.

use std::any::Any;
use std::collections::HashMap;
use std::sync::Arc;

use pyo3::exceptions::PyTypeError;
use pyo3::prelude::{Bound, PyAny, PyResult};
#[cfg(not(test))]
use pyo3::types::PyString;
#[cfg(not(test))]
use pyo3::Python;

use crate::ids::HandlerScopeId;
use crate::py_key::HashedPyKey;
use crate::value::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct HandlerStateKey(&'static str);

impl HandlerStateKey {
    pub const fn new(name: &'static str) -> Self {
        Self(name)
    }

    pub const fn as_str(self) -> &'static str {
        self.0
    }
}

#[derive(Debug, Clone)]
pub struct RustStore {
    pub state: HashMap<String, Value>,
    pub env: HashMap<HashedPyKey, Value>,
    pub log: Vec<Value>,
    pub handler_value_cells: HashMap<HandlerScopeId, HashMap<HashedPyKey, Value>>,
    pub handler_rust_cells:
        HashMap<HandlerScopeId, HashMap<HandlerStateKey, Arc<dyn Any + Send + Sync>>>,
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
            state: HashMap::new(),
            env: HashMap::new(),
            log: Vec::new(),
            handler_value_cells: HashMap::new(),
            handler_rust_cells: HashMap::new(),
        }
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.state.get(key)
    }

    pub fn put(&mut self, key: String, value: Value) {
        self.state.insert(key, value);
    }

    pub fn ask(&self, key: impl Into<HashedPyKey>) -> Option<&Value> {
        let key = key.into();
        self.env.get(&key)
    }

    #[cfg(test)]
    pub fn ask_str(&self, key: &str) -> Option<&Value> {
        self.env.get(&HashedPyKey::from_test_string(key))
    }

    #[cfg(test)]
    pub fn set_env_str(&mut self, key: impl Into<String>, value: Value) {
        self.env.insert(HashedPyKey::from_test_string(key), value);
    }

    pub fn tell(&mut self, message: Value) {
        self.log.push(message);
    }

    pub fn logs(&self) -> &[Value] {
        &self.log
    }

    pub fn modify(&mut self, key: &str, f: impl FnOnce(&Value) -> Value) -> Option<Value> {
        let old = self.state.get(key)?;
        let new_val = f(old);
        let old_clone = old.clone();
        self.state.insert(key.to_string(), new_val);
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
            .filter_map(|k| self.env.get(k).map(|v| (k.clone(), v.clone())))
            .collect();
        let new_keys: Vec<HashedPyKey> = hashed_bindings
            .keys()
            .filter(|k| !old.contains_key(*k))
            .cloned()
            .collect();

        for (k, v) in hashed_bindings {
            self.env.insert(k, v);
        }

        let result = f(self);

        for (k, v) in old {
            self.env.insert(k, v);
        }
        for k in new_keys {
            self.env.remove(&k);
        }

        Ok(result)
    }

    pub fn clear_logs(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.log)
    }

    pub fn handler_has(
        &self,
        scope_id: HandlerScopeId,
        key: impl Into<HashedPyKey>,
    ) -> bool {
        let key = key.into();
        self.handler_value_cells
            .get(&scope_id)
            .is_some_and(|cells| cells.contains_key(&key))
    }

    pub fn handler_get(
        &self,
        scope_id: HandlerScopeId,
        key: impl Into<HashedPyKey>,
    ) -> Option<&Value> {
        let key = key.into();
        self.handler_value_cells.get(&scope_id)?.get(&key)
    }

    pub fn handler_set(
        &mut self,
        scope_id: HandlerScopeId,
        key: impl Into<HashedPyKey>,
        value: Value,
    ) {
        let key = key.into();
        self.handler_value_cells
            .entry(scope_id)
            .or_default()
            .insert(key, value);
    }

    pub fn clear_handler_scope(&mut self, scope_id: HandlerScopeId) {
        self.handler_value_cells.remove(&scope_id);
        self.handler_rust_cells.remove(&scope_id);
    }

    pub fn handler_rust_set<T>(
        &mut self,
        scope_id: HandlerScopeId,
        key: HandlerStateKey,
        value: T,
    ) where
        T: Any + Send + Sync + 'static,
    {
        self.handler_rust_cells
            .entry(scope_id)
            .or_default()
            .insert(key, Arc::new(value));
    }

    pub fn handler_rust_get<T>(&self, scope_id: HandlerScopeId, key: HandlerStateKey) -> Option<&T>
    where
        T: Any + Send + Sync + 'static,
    {
        self.handler_rust_cells
            .get(&scope_id)?
            .get(&key)?
            .as_ref()
            .downcast_ref::<T>()
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
