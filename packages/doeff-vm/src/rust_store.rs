//! Store model shared by handlers and VM.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex, RwLock};

use crate::ids::PromiseId;
use crate::value::Value;

#[derive(Debug, Clone)]
struct LazyCacheEntry {
    source_id: usize,
    value: Value,
}

#[derive(Debug, Clone, Copy)]
struct LazyInflightEntry {
    source_id: usize,
    promise_id: PromiseId,
}

#[derive(Debug, Clone)]
pub struct RustStore {
    pub state: HashMap<String, Value>,
    pub env: HashMap<String, Value>,
    pub log: Vec<Value>,
    lazy_cache: Arc<RwLock<HashMap<String, LazyCacheEntry>>>,
    lazy_inflight: Arc<Mutex<HashMap<String, LazyInflightEntry>>>,
    lazy_active: Arc<Mutex<HashSet<(String, usize)>>>,
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            state: HashMap::new(),
            env: HashMap::new(),
            log: Vec::new(),
            lazy_cache: Arc::new(RwLock::new(HashMap::new())),
            lazy_inflight: Arc::new(Mutex::new(HashMap::new())),
            lazy_active: Arc::new(Mutex::new(HashSet::new())),
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
        let cache = self.lazy_cache.read().expect("lazy_cache lock poisoned");
        let entry = cache.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.value.clone());
        }
        None
    }

    pub fn lazy_cache_put(&self, key: String, source_id: usize, value: Value) {
        let mut cache = self.lazy_cache.write().expect("lazy_cache lock poisoned");
        cache.insert(key, LazyCacheEntry { source_id, value });
    }

    pub fn lazy_inflight_get(&self, key: &str, source_id: usize) -> Option<PromiseId> {
        let inflight = self
            .lazy_inflight
            .lock()
            .expect("lazy_inflight lock poisoned");
        let entry = inflight.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.promise_id);
        }
        None
    }

    pub fn lazy_inflight_put(&self, key: String, source_id: usize, promise_id: PromiseId) {
        let mut inflight = self
            .lazy_inflight
            .lock()
            .expect("lazy_inflight lock poisoned");
        inflight.insert(
            key,
            LazyInflightEntry {
                source_id,
                promise_id,
            },
        );
    }

    pub fn lazy_inflight_remove(&self, key: &str, source_id: usize, promise_id: PromiseId) {
        let mut inflight = self
            .lazy_inflight
            .lock()
            .expect("lazy_inflight lock poisoned");
        let should_remove = inflight
            .get(key)
            .is_some_and(|entry| entry.source_id == source_id && entry.promise_id == promise_id);
        if should_remove {
            inflight.remove(key);
        }
    }

    pub fn lazy_active_contains(&self, key: &str, source_id: usize) -> bool {
        let active = self.lazy_active.lock().expect("lazy_active lock poisoned");
        active.contains(&(key.to_string(), source_id))
    }

    pub fn lazy_active_insert(&self, key: String, source_id: usize) {
        let mut active = self.lazy_active.lock().expect("lazy_active lock poisoned");
        active.insert((key, source_id));
    }

    pub fn lazy_active_remove(&self, key: &str, source_id: usize) {
        let mut active = self.lazy_active.lock().expect("lazy_active lock poisoned");
        active.remove(&(key.to_string(), source_id));
    }
}

impl Default for RustStore {
    fn default() -> Self {
        Self::new()
    }
}
