use std::collections::HashMap;

use crate::ids::{SegmentId, VarId};
use crate::py_key::HashedPyKey;
use crate::value::Value;

#[derive(Debug, Default, Clone)]
pub struct VarStore {
    pub cells: HashMap<VarId, Value>,
    state_by_segment: HashMap<SegmentId, HashMap<String, Value>>,
    writer_logs_by_segment: HashMap<SegmentId, Vec<Value>>,
    bindings_by_segment: HashMap<SegmentId, HashMap<HashedPyKey, Value>>,
    overrides_by_segment: HashMap<SegmentId, HashMap<VarId, Value>>,
}

impl VarStore {
    pub fn clear(&mut self) {
        self.cells.clear();
        self.state_by_segment.clear();
        self.writer_logs_by_segment.clear();
        self.bindings_by_segment.clear();
        self.overrides_by_segment.clear();
    }

    pub fn shrink_to_fit(&mut self) {
        self.cells.shrink_to_fit();
        self.state_by_segment.shrink_to_fit();
        self.writer_logs_by_segment.shrink_to_fit();
        self.bindings_by_segment.shrink_to_fit();
        self.overrides_by_segment.shrink_to_fit();
    }

    pub fn init_segment(&mut self, seg_id: SegmentId) {
        self.state_by_segment.entry(seg_id).or_default();
        self.writer_logs_by_segment.entry(seg_id).or_default();
        self.bindings_by_segment.entry(seg_id).or_default();
        self.overrides_by_segment.entry(seg_id).or_default();
    }

    pub fn remove_segment(&mut self, seg_id: SegmentId) {
        self.state_by_segment.remove(&seg_id);
        self.writer_logs_by_segment.remove(&seg_id);
        self.bindings_by_segment.remove(&seg_id);
        self.overrides_by_segment.remove(&seg_id);
    }

    pub fn replace_handler_state(&mut self, seg_id: SegmentId, state: HashMap<String, Value>) {
        self.state_by_segment.insert(seg_id, state);
    }

    pub fn handler_state(&self, seg_id: SegmentId) -> Option<&HashMap<String, Value>> {
        self.state_by_segment.get(&seg_id)
    }

    pub fn handler_state_mut(&mut self, seg_id: SegmentId) -> Option<&mut HashMap<String, Value>> {
        self.state_by_segment.get_mut(&seg_id)
    }

    pub fn handler_state_count(&self) -> usize {
        self.state_by_segment.len()
    }

    pub fn handler_state_capacity(&self) -> usize {
        self.state_by_segment.capacity()
    }

    pub fn append_writer_log(&mut self, seg_id: SegmentId, message: Value) -> bool {
        let Some(logs) = self.writer_logs_by_segment.get_mut(&seg_id) else {
            return false;
        };
        logs.push(message);
        true
    }

    pub fn writer_log(&self, seg_id: SegmentId) -> Option<&Vec<Value>> {
        self.writer_logs_by_segment.get(&seg_id)
    }

    pub fn writer_log_count(&self) -> usize {
        self.writer_logs_by_segment.len()
    }

    pub fn writer_log_capacity(&self) -> usize {
        self.writer_logs_by_segment.capacity()
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
