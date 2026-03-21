use std::collections::HashMap;

use crate::ids::{SegmentId, VarId};
use crate::py_key::HashedPyKey;
use crate::value::Value;

#[derive(Debug, Default, Clone)]
pub struct VarStore {
    pub cells: HashMap<VarId, Value>,
    bindings_by_segment: HashMap<SegmentId, HashMap<HashedPyKey, Value>>,
    overrides_by_segment: HashMap<SegmentId, HashMap<VarId, Value>>,
}

impl VarStore {
    pub fn clear(&mut self) {
        self.cells.clear();
        self.bindings_by_segment.clear();
        self.overrides_by_segment.clear();
    }

    pub fn init_segment(&mut self, seg_id: SegmentId) {
        self.bindings_by_segment.entry(seg_id).or_default();
        self.overrides_by_segment.entry(seg_id).or_default();
    }

    pub fn remove_segment(&mut self, seg_id: SegmentId) {
        self.bindings_by_segment.remove(&seg_id);
        self.overrides_by_segment.remove(&seg_id);
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
