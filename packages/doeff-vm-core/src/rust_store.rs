//! Store model shared by handlers and VM.

use std::collections::HashMap;

use crate::value::Value;

#[derive(Debug, Clone)]
pub struct RustStore {
    pub entries: HashMap<String, Value>,
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            entries: HashMap::new(),
        }
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.entries.get(key)
    }

    pub fn put(&mut self, key: String, value: Value) {
        self.entries.insert(key, value);
    }

    pub fn modify(&mut self, key: &str, f: impl FnOnce(&Value) -> Value) -> Option<Value> {
        let old = self.entries.get(key)?;
        let new_val = f(old);
        let old_clone = old.clone();
        self.entries.insert(key.to_string(), new_val);
        Some(old_clone)
    }
}

impl Default for RustStore {
    fn default() -> Self {
        Self::new()
    }
}
