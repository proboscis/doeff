//! Variable store and scope visibility — walks parent pointers.

use std::collections::HashMap;

use crate::frame::{EvalReturnContinuation, Frame};
use crate::ids::{FiberId, SegmentId, VarId};
use crate::py_key::HashedPyKey;
use crate::value::Value;
use crate::vm::VM;

impl VM {
    /// Walk parent pointers to collect all visible segments for scope resolution.
    /// In OCaml 5 terms: walk the fiber chain to find lexical scope.
    pub(crate) fn visible_lexical_segments(&self, start_seg_id: SegmentId) -> Vec<SegmentId> {
        let mut ordered = Vec::new();
        let mut seen = std::collections::HashSet::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(segment) = self.segments.get(seg_id) else {
                break;
            };
            if !seen.insert(seg_id) {
                break;
            }
            ordered.push(seg_id);

            // Also include fibers referenced by EvalReturn frames
            for frame in segment.frames.iter().rev() {
                if let Frame::EvalReturn(eval_return) = frame {
                    match eval_return.as_ref() {
                        EvalReturnContinuation::ResumeToContinuation { head_fiber }
                        | EvalReturnContinuation::ReturnToContinuation { head_fiber }
                        | EvalReturnContinuation::EvalInScopeReturn { head_fiber } => {
                            // Walk the chain from head_fiber
                            let mut chain_cursor = Some(*head_fiber);
                            while let Some(fid) = chain_cursor {
                                if !seen.insert(fid) {
                                    break;
                                }
                                ordered.push(fid);
                                chain_cursor = self.segments.get(fid).and_then(|s| s.parent);
                            }
                        }
                        _ => {}
                    }
                }
            }

            cursor = segment.parent;
        }
        ordered
    }

    pub(crate) fn push_lexical_scope_frame(
        &mut self,
        seg_id: SegmentId,
        bindings: HashMap<HashedPyKey, Value>,
    ) -> bool {
        let Some(segment) = self.segments.get_mut(seg_id) else {
            return false;
        };
        segment.frames.insert(
            0,
            Frame::LexicalScope {
                bindings,
                var_overrides: HashMap::new(),
            },
        );
        true
    }

    pub(crate) fn segment_scope_bindings(
        &self,
        seg_id: SegmentId,
    ) -> Option<&HashMap<HashedPyKey, Value>> {
        self.segments
            .get(seg_id)?
            .frames
            .iter()
            .rev()
            .find_map(|frame| {
                if let Frame::LexicalScope { bindings, .. } = frame {
                    Some(bindings)
                } else {
                    None
                }
            })
    }

    pub(crate) fn segment_var_overrides(
        &self,
        seg_id: SegmentId,
    ) -> Option<&HashMap<VarId, Value>> {
        self.segments
            .get(seg_id)?
            .frames
            .iter()
            .rev()
            .find_map(|frame| {
                if let Frame::LexicalScope { var_overrides, .. } = frame {
                    Some(var_overrides)
                } else {
                    None
                }
            })
    }

    pub(crate) fn segment_var_overrides_mut(
        &mut self,
        seg_id: SegmentId,
    ) -> Option<&mut HashMap<VarId, Value>> {
        self.segments
            .get_mut(seg_id)?
            .frames
            .iter_mut()
            .rev()
            .find_map(|frame| {
                if let Frame::LexicalScope { var_overrides, .. } = frame {
                    Some(var_overrides)
                } else {
                    None
                }
            })
    }

    pub fn alloc_scoped_var_in_segment(&mut self, seg_id: SegmentId, initial: Value) -> VarId {
        let var = VarId::fresh(seg_id);
        self.var_store.cells.insert(var, initial);
        var
    }

    pub fn read_scoped_var_from(&self, start_seg_id: SegmentId, var: VarId) -> Option<Value> {
        for seg_id in self.visible_lexical_segments(start_seg_id) {
            if let Some(value) = self
                .segment_var_overrides(seg_id)
                .and_then(|overrides| overrides.get(&var))
            {
                return Some(value.clone());
            }
            if seg_id == var.owner_segment() {
                return self.var_store.cells.get(&var).cloned();
            }
        }
        self.var_store.cells.get(&var).cloned()
    }

    pub fn write_scoped_var_in_current_segment(
        &mut self,
        seg_id: SegmentId,
        var: VarId,
        value: Value,
    ) -> bool {
        if self.segments.get(seg_id).is_none() {
            return false;
        }
        if seg_id == var.owner_segment() {
            self.var_store.cells.insert(var, value);
            return true;
        }
        if self.segment_var_overrides(seg_id).is_none() {
            let _ = self.push_lexical_scope_frame(seg_id, HashMap::new());
        }
        self.segment_var_overrides_mut(seg_id)
            .map(|overrides| overrides.insert(var, value))
            .is_some()
    }

    pub fn write_scoped_var_nonlocal(
        &mut self,
        start_seg_id: SegmentId,
        var: VarId,
        value: Value,
    ) -> bool {
        let visible_segments = self.visible_lexical_segments(start_seg_id);
        for seg_id in visible_segments {
            if self.segments.get(seg_id).is_none() {
                return false;
            }
            if seg_id == var.owner_segment() {
                self.var_store.cells.insert(var, value);
                return true;
            }
            if self
                .segment_var_overrides(seg_id)
                .is_some_and(|overrides| overrides.contains_key(&var))
            {
                return self
                    .segment_var_overrides_mut(seg_id)
                    .map(|overrides| overrides.insert(var, value))
                    .is_some();
            }
        }
        false
    }

    pub fn read_scope_binding_from(
        &self,
        start_seg_id: SegmentId,
        key: &HashedPyKey,
    ) -> Option<Value> {
        for seg_id in self.visible_lexical_segments(start_seg_id) {
            if let Some(value) = self
                .segment_scope_bindings(seg_id)
                .and_then(|bindings| bindings.get(key))
            {
                return Some(value.clone());
            }
        }
        self.var_store.root_scope_bindings().get(key).cloned()
    }
}
