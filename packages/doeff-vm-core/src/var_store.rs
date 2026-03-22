use std::collections::HashMap;

use crate::ids::{SegmentId, VarId};
use crate::py_key::HashedPyKey;
use crate::value::Value;

#[derive(Debug, Default, Clone)]
pub struct VarStore {
    pub cells: HashMap<VarId, Value>,
    global_state: HashMap<String, Value>,
    root_scope_bindings: HashMap<HashedPyKey, Value>,
    writer_log: Vec<Value>,
    bindings_by_segment: HashMap<SegmentId, HashMap<HashedPyKey, Value>>,
    overrides_by_segment: HashMap<SegmentId, HashMap<VarId, Value>>,
}

impl VarStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn clear(&mut self) {
        self.cells.clear();
        self.global_state.clear();
        self.root_scope_bindings.clear();
        self.writer_log.clear();
        self.bindings_by_segment.clear();
        self.overrides_by_segment.clear();
    }

    pub fn clear_run_local(&mut self) {
        self.cells.clear();
        self.writer_log.clear();
        self.bindings_by_segment.clear();
        self.overrides_by_segment.clear();
    }

    pub fn shrink_to_fit(&mut self) {
        self.cells.shrink_to_fit();
        self.global_state.shrink_to_fit();
        self.root_scope_bindings.shrink_to_fit();
        self.writer_log.shrink_to_fit();
        self.bindings_by_segment.shrink_to_fit();
        self.overrides_by_segment.shrink_to_fit();
    }

    pub fn shrink_run_local_to_fit(&mut self) {
        self.cells.shrink_to_fit();
        self.writer_log.shrink_to_fit();
        self.bindings_by_segment.shrink_to_fit();
        self.overrides_by_segment.shrink_to_fit();
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.global_state.get(key)
    }

    pub fn put(&mut self, key: String, value: Value) {
        self.global_state.insert(key, value);
    }

    pub fn modify(&mut self, key: &str, f: impl FnOnce(&Value) -> Value) -> Option<Value> {
        let old = self.global_state.get(key)?;
        let new_val = f(old);
        let old_clone = old.clone();
        self.global_state.insert(key.to_string(), new_val);
        Some(old_clone)
    }

    pub fn global_state(&self) -> &HashMap<String, Value> {
        &self.global_state
    }

    pub fn global_state_mut(&mut self) -> &mut HashMap<String, Value> {
        &mut self.global_state
    }

    pub fn root_scope_bindings(&self) -> &HashMap<HashedPyKey, Value> {
        &self.root_scope_bindings
    }

    pub fn root_scope_bindings_mut(&mut self) -> &mut HashMap<HashedPyKey, Value> {
        &mut self.root_scope_bindings
    }

    pub fn insert_root_scope_binding(&mut self, key: HashedPyKey, value: Value) {
        self.root_scope_bindings.insert(key, value);
    }

    pub fn init_segment(&mut self, seg_id: SegmentId) {
        self.bindings_by_segment.entry(seg_id).or_default();
        self.overrides_by_segment.entry(seg_id).or_default();
    }

    pub fn remove_segment(&mut self, seg_id: SegmentId) {
        self.bindings_by_segment.remove(&seg_id);
        self.overrides_by_segment.remove(&seg_id);
    }

    pub fn replace_handler_state(&mut self, _seg_id: SegmentId, state: HashMap<String, Value>) {
        self.global_state = state;
    }

    pub fn handler_state(&self, _seg_id: SegmentId) -> Option<&HashMap<String, Value>> {
        Some(&self.global_state)
    }

    pub fn handler_state_mut(
        &mut self,
        _seg_id: SegmentId,
    ) -> Option<&mut HashMap<String, Value>> {
        Some(&mut self.global_state)
    }

    pub fn handler_state_count(&self) -> usize {
        usize::from(!self.global_state.is_empty())
    }

    pub fn handler_state_capacity(&self) -> usize {
        self.global_state.capacity()
    }

    pub fn append_writer_log(&mut self, _seg_id: SegmentId, message: Value) -> bool {
        self.writer_log.push(message);
        true
    }

    pub fn writer_log(&self, _seg_id: SegmentId) -> Option<&Vec<Value>> {
        Some(&self.writer_log)
    }

    pub fn writer_log_count(&self) -> usize {
        usize::from(!self.writer_log.is_empty())
    }

    pub fn writer_log_capacity(&self) -> usize {
        self.writer_log.capacity()
    }

    pub fn replace_scope_bindings(
        &mut self,
        seg_id: SegmentId,
        bindings: HashMap<HashedPyKey, Value>,
    ) {
        self.bindings_by_segment.insert(seg_id, bindings);
    }

    pub fn scope_bindings(&self, seg_id: SegmentId) -> Option<&HashMap<HashedPyKey, Value>> {
        self.bindings_by_segment.get(&seg_id)
    }

    pub fn replace_segment_var_overrides(
        &mut self,
        seg_id: SegmentId,
        overrides: HashMap<VarId, Value>,
    ) {
        self.overrides_by_segment.insert(seg_id, overrides);
    }

    pub fn segment_var_overrides(&self, seg_id: SegmentId) -> Option<&HashMap<VarId, Value>> {
        self.overrides_by_segment.get(&seg_id)
    }

    pub fn segment_var_overrides_mut(
        &mut self,
        seg_id: SegmentId,
    ) -> Option<&mut HashMap<VarId, Value>> {
        self.overrides_by_segment.get_mut(&seg_id)
    }
}
