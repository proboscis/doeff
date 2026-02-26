//! Store model shared by handlers and VM.

use std::collections::HashMap;

#[cfg(not(test))]
use pyo3::types::PyString;
#[cfg(not(test))]
use pyo3::Python;

use crate::py_key::HashedPyKey;
use crate::value::Value;

#[derive(Debug, Clone)]
pub struct RustStore {
    pub state: HashMap<String, Value>,
    pub env: HashMap<HashedPyKey, Value>,
    pub log: Vec<Value>,
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

fn string_key_to_hashed(key: &str) -> HashedPyKey {
    #[cfg(test)]
    {
        return HashedPyKey::from_test_string(key);
    }

    #[cfg(not(test))]
    {
        Python::attach(|py| {
            let py_key = PyString::new(py, key).into_any();
            HashedPyKey::from_bound(&py_key).expect("Python string keys must be hashable")
        })
    }
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            state: HashMap::new(),
            env: HashMap::new(),
            log: Vec::new(),
        }
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.state.get(key)
    }

    pub fn put(&mut self, key: String, value: Value) {
        self.state.insert(key, value);
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

    pub fn with_local<F, R>(&mut self, bindings: HashMap<String, Value>, f: F) -> R
    where
        F: FnOnce(&mut Self) -> R,
    {
        let hashed_bindings: HashMap<HashedPyKey, Value> = bindings
            .into_iter()
            .map(|(k, v)| (string_key_to_hashed(&k), v))
            .collect();

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

        result
    }

    pub fn clear_logs(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.log)
    }
}

impl Default for RustStore {
    fn default() -> Self {
        Self::new()
    }
}
