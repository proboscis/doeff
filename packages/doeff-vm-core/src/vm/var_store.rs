use std::collections::HashMap;

use super::*;
use crate::ids::VarId;

impl VM {
    pub fn replace_scope_bindings(
        &mut self,
        seg_id: SegmentId,
        bindings: HashMap<HashedPyKey, Value>,
    ) {
        self.var_store.replace_scope_bindings(seg_id, bindings);
    }

    pub fn scope_bindings(&self, seg_id: SegmentId) -> Option<&HashMap<HashedPyKey, Value>> {
        self.var_store.scope_bindings(seg_id)
    }

    pub fn replace_segment_var_overrides(
        &mut self,
        seg_id: SegmentId,
        overrides: HashMap<VarId, Value>,
    ) {
        self.var_store
            .replace_segment_var_overrides(seg_id, overrides);
    }

    pub fn segment_var_overrides(&self, seg_id: SegmentId) -> Option<&HashMap<VarId, Value>> {
        self.var_store.segment_var_overrides(seg_id)
    }

    pub fn alloc_scoped_var_in_segment(&mut self, seg_id: SegmentId, initial: Value) -> VarId {
        let scope_id = self
            .segments
            .get(seg_id)
            .expect("alloc_scoped_var_in_segment requires a live segment")
            .scope_id;
        let var = VarId::fresh(scope_id);
        self.var_store.cells.insert(var, initial);
        var
    }

    pub fn read_scoped_var_from(&self, start_seg_id: SegmentId, var: VarId) -> Option<Value> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            if let Some(value) = self
                .var_store
                .segment_var_overrides(seg_id)
                .and_then(|overrides| overrides.get(&var))
            {
                return Some(value.clone());
            }
            let seg = self.segments.get(seg_id)?;
            if seg.scope_id == var.owner_scope() {
                return self.var_store.cells.get(&var).cloned();
            }
            cursor = self.scope_parent(seg_id);
        }
        self.var_store.cells.get(&var).cloned()
    }

    pub fn write_scoped_var_in_current_segment(
        &mut self,
        seg_id: SegmentId,
        var: VarId,
        value: Value,
    ) -> bool {
        let Some(seg) = self.segments.get(seg_id) else {
            return false;
        };
        if seg.scope_id == var.owner_scope() {
            self.var_store.cells.insert(var, value);
        } else {
            self.var_store
                .segment_var_overrides_mut(seg_id)
                .expect("segment var overrides must exist for live segment")
                .insert(var, value);
        }
        true
    }

    pub fn write_scoped_var_nonlocal(
        &mut self,
        start_seg_id: SegmentId,
        var: VarId,
        value: Value,
    ) -> bool {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                return false;
            };
            if seg.scope_id == var.owner_scope() {
                self.var_store.cells.insert(var, value);
                return true;
            }
            if self
                .var_store
                .segment_var_overrides(seg_id)
                .is_some_and(|overrides| overrides.contains_key(&var))
            {
                self.var_store
                    .segment_var_overrides_mut(seg_id)
                    .expect("segment var overrides must exist for live segment")
                    .insert(var, value);
                return true;
            }
            cursor = self.scope_parent(seg_id);
        }
        false
    }

    pub fn read_scope_binding_from(
        &self,
        start_seg_id: SegmentId,
        key: &HashedPyKey,
    ) -> Option<Value> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            if let Some(value) = self
                .var_store
                .scope_bindings(seg_id)
                .and_then(|bindings| bindings.get(key))
            {
                return Some(value.clone());
            }
            cursor = self.scope_parent(seg_id);
        }
        self.env_store.get(key).cloned()
    }
}
