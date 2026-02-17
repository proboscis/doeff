//! Store model shared by handlers and VM.

use std::collections::HashMap;

use crate::value::Value;

#[derive(Debug, Clone)]
struct LazyCacheEntry {
    source_id: usize,
    value: Value,
}

#[derive(Debug, Clone)]
struct LazySemaphoreEntry {
    source_id: usize,
    semaphore: Value,
}

#[derive(Debug, Clone)]
pub struct RustStore {
    pub state: HashMap<String, Value>,
    pub env: HashMap<String, Value>,
    pub log: Vec<Value>,
    lazy_cache: HashMap<String, LazyCacheEntry>,
    lazy_semaphores: HashMap<String, LazySemaphoreEntry>,
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            state: HashMap::new(),
            env: HashMap::new(),
            log: Vec::new(),
            lazy_cache: HashMap::new(),
            lazy_semaphores: HashMap::new(),
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

    pub fn lazy_cache_get(&self, key: &str, source_id: usize) -> Option<Value> {
        let entry = self.lazy_cache.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.value.clone());
        }
        None
    }

    pub fn lazy_cache_put(&mut self, key: String, source_id: usize, value: Value) {
        self.lazy_cache
            .insert(key, LazyCacheEntry { source_id, value });
    }

    pub fn lazy_semaphore_get(&self, key: &str, source_id: usize) -> Option<Value> {
        let entry = self.lazy_semaphores.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.semaphore.clone());
        }
        None
    }

    pub fn lazy_semaphore_put(&mut self, key: String, source_id: usize, semaphore: Value) {
        self.lazy_semaphores.insert(
            key,
            LazySemaphoreEntry {
                source_id,
                semaphore,
            },
        );
    }

    /// Merge lazy Ask coordination state from another store snapshot.
    ///
    /// This intentionally syncs only lazy caches/semaphores; regular state/env/log
    /// isolation semantics remain unchanged.
    pub fn merge_lazy_from(&mut self, other: &RustStore) {
        for (key, entry) in &other.lazy_cache {
            self.lazy_cache.insert(key.clone(), entry.clone());
        }
        for (key, entry) in &other.lazy_semaphores {
            self.lazy_semaphores.insert(key.clone(), entry.clone());
        }
    }
}

impl Default for RustStore {
    fn default() -> Self {
        Self::new()
    }
}
