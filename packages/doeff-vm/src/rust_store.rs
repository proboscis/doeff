//! Store model shared by handlers and VM.

use std::collections::HashMap;

use crate::value::Value;

#[derive(Debug, Clone)]
pub struct RustStore {
    pub state: HashMap<String, Value>,
    pub env: HashMap<String, Value>,
    pub log: Vec<Value>,
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

    pub fn ask(&self, key: &str) -> Option<&Value> {
        self.env.get(key)
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
        let old: HashMap<String, Value> = bindings
            .keys()
            .filter_map(|k| self.env.get(k).map(|v| (k.clone(), v.clone())))
            .collect();
        let new_keys: Vec<String> = bindings
            .keys()
            .filter(|k| !old.contains_key(*k))
            .cloned()
            .collect();

        for (k, v) in bindings {
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
