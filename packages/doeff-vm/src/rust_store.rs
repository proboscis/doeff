//! Store model shared by handlers and VM.

use std::cell::UnsafeCell;
use std::collections::HashMap;
use std::sync::Arc;

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

struct SharedLazyMap<T> {
    inner: Arc<UnsafeCell<HashMap<String, T>>>,
}

impl<T> SharedLazyMap<T> {
    fn new() -> Self {
        Self {
            inner: Arc::new(UnsafeCell::new(HashMap::new())),
        }
    }

    fn get_cloned(&self, key: &str) -> Option<T>
    where
        T: Clone,
    {
        // SAFETY: VM execution is single-threaded and cooperative. Only one task mutates
        // RustStore at a time, so shared lazy maps can be read without OS locks.
        let map = unsafe { &*self.inner.get() };
        map.get(key).cloned()
    }

    fn insert(&self, key: String, value: T) {
        // SAFETY: VM execution is single-threaded and cooperative. Only one task mutates
        // RustStore at a time, so shared lazy maps can be updated without OS locks.
        let map = unsafe { &mut *self.inner.get() };
        map.insert(key, value);
    }
}

impl<T> Clone for SharedLazyMap<T> {
    fn clone(&self) -> Self {
        Self {
            inner: self.inner.clone(),
        }
    }
}

impl<T> std::fmt::Debug for SharedLazyMap<T> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SharedLazyMap").finish_non_exhaustive()
    }
}

// SAFETY: VM + scheduler execution is cooperative and single-threaded in-process.
// SharedLazyMap intentionally uses interior mutability to share lazy coordination
// state across isolated task store snapshots without OS-level mutexes.
unsafe impl<T: Send> Send for SharedLazyMap<T> {}
// SAFETY: See rationale above for `Send`.
unsafe impl<T: Send> Sync for SharedLazyMap<T> {}

#[derive(Debug, Clone)]
pub struct RustStore {
    pub state: HashMap<String, Value>,
    pub env: HashMap<String, Value>,
    pub log: Vec<Value>,
    lazy_cache: SharedLazyMap<LazyCacheEntry>,
    lazy_semaphores: SharedLazyMap<LazySemaphoreEntry>,
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            state: HashMap::new(),
            env: HashMap::new(),
            log: Vec::new(),
            lazy_cache: SharedLazyMap::new(),
            lazy_semaphores: SharedLazyMap::new(),
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
        let entry = self.lazy_cache.get_cloned(key)?;
        if entry.source_id == source_id {
            return Some(entry.value);
        }
        None
    }

    pub fn lazy_cache_put(&self, key: String, source_id: usize, value: Value) {
        self.lazy_cache
            .insert(key, LazyCacheEntry { source_id, value });
    }

    pub fn lazy_semaphore_get(&self, key: &str, source_id: usize) -> Option<Value> {
        let entry = self.lazy_semaphores.get_cloned(key)?;
        if entry.source_id == source_id {
            return Some(entry.semaphore);
        }
        None
    }

    pub fn lazy_semaphore_put(&self, key: String, source_id: usize, semaphore: Value) {
        self.lazy_semaphores.insert(
            key,
            LazySemaphoreEntry {
                source_id,
                semaphore,
            },
        );
    }
}

impl Default for RustStore {
    fn default() -> Self {
        Self::new()
    }
}
