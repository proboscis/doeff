//! Segment arena for stable segment IDs within a run.

use crate::ids::SegmentId;
use crate::segment::Segment;

pub struct SegmentArena {
    segments: Vec<Option<Segment>>,
    free_list: Vec<usize>,
}

impl SegmentArena {
    pub fn new() -> Self {
        SegmentArena {
            segments: Vec::new(),
            free_list: Vec::new(),
        }
    }

    pub fn alloc(&mut self, segment: Segment) -> SegmentId {
        if let Some(index) = self.free_list.pop() {
            assert!(
                self.segments.get(index).is_some_and(|slot| slot.is_none()),
                "reused arena slot must be vacant before allocation: index={index}"
            );
            self.segments[index] = Some(segment);
            return SegmentId::from_index(index);
        }
        let id = SegmentId::from_index(self.segments.len());
        self.segments.push(Some(segment));
        id
    }

    pub fn free(&mut self, id: SegmentId) {
        if let Some(slot) = self.segments.get_mut(id.index()) {
            if slot.take().is_some() {
                self.free_list.push(id.index());
            }
        }
    }

    pub fn get(&self, id: SegmentId) -> Option<&Segment> {
        self.segments.get(id.index()).and_then(|s| s.as_ref())
    }

    pub fn get_mut(&mut self, id: SegmentId) -> Option<&mut Segment> {
        self.segments.get_mut(id.index()).and_then(|s| s.as_mut())
    }

    pub fn iter(&self) -> impl Iterator<Item = (SegmentId, &Segment)> {
        self.segments.iter().enumerate().filter_map(|(idx, slot)| {
            slot.as_ref()
                .map(|segment| (SegmentId::from_index(idx), segment))
        })
    }

    /// Rewire children that currently point at `old_parent` so they point to `new_parent`.
    ///
    /// This keeps caller chains valid when a completed parent segment is freed while
    /// descendant segments are still alive (for example across scheduler preemption).
    pub fn reparent_children(
        &mut self,
        old_parent: SegmentId,
        new_caller: Option<SegmentId>,
        new_scope_parent: Option<SegmentId>,
    ) -> usize {
        let mut rewired = 0usize;
        for slot in &mut self.segments {
            let Some(segment) = slot.as_mut() else {
                continue;
            };
            let mut touched = false;
            if segment.caller == Some(old_parent) {
                segment.caller = new_caller;
                touched = true;
            }
            if segment.scope_parent == Some(old_parent) {
                segment.scope_parent = new_scope_parent;
                touched = true;
            }
            if touched {
                rewired += 1;
            }
        }
        rewired
    }

    pub fn len(&self) -> usize {
        self.segments.iter().filter(|s| s.is_some()).count()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn capacity(&self) -> usize {
        self.segments.len()
    }

    pub fn clear(&mut self) {
        self.segments.clear();
        self.free_list.clear();
    }
}

impl Default for SegmentArena {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::Marker;

    #[test]
    fn test_arena_alloc_and_get() {
        let mut arena = SegmentArena::new();

        let marker1 = Marker::fresh();
        let seg1 = Segment::new(marker1, None);
        let id1 = arena.alloc(seg1);

        let marker2 = Marker::fresh();
        let seg2 = Segment::new(marker2, None);
        let id2 = arena.alloc(seg2);

        assert_ne!(id1, id2);
        assert_eq!(arena.len(), 2);

        let retrieved = arena.get(id1).unwrap();
        assert_eq!(retrieved.marker, marker1);
    }

    #[test]
    fn test_arena_free_releases_slot_and_reuses_id() {
        let mut arena = SegmentArena::new();

        let marker1 = Marker::fresh();
        let seg1 = Segment::new(marker1, None);
        let id1 = arena.alloc(seg1);

        assert_eq!(arena.len(), 1);

        arena.free(id1);
        assert_eq!(arena.len(), 0);
        assert!(arena.get(id1).is_none());

        let marker2 = Marker::fresh();
        let seg2 = Segment::new(marker2, None);
        let id2 = arena.alloc(seg2);

        assert_eq!(id1, id2);
        assert_eq!(arena.len(), 1);

        let retrieved = arena.get(id2).unwrap();
        assert_eq!(retrieved.marker, marker2);
    }

    #[test]
    fn test_arena_get_mut() {
        let mut arena = SegmentArena::new();

        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let id = arena.alloc(seg);

        {
            let seg_mut = arena.get_mut(id).unwrap();
            use crate::frame::Frame;
            seg_mut.push_frame(Frame::FlatMapBindResult);
        }

        let seg_ref = arena.get(id).unwrap();
        assert_eq!(seg_ref.frame_count(), 1);
    }

    #[test]
    fn test_reparent_children() {
        let mut arena = SegmentArena::new();
        let marker = Marker::fresh();

        let parent = arena.alloc(Segment::new(marker, None));
        let caller = arena.alloc(Segment::new(marker, None));
        let child_a = arena.alloc(Segment::new(marker, Some(parent)));
        let child_b = arena.alloc(Segment::new(marker, Some(parent)));
        let unrelated = arena.alloc(Segment::new(marker, Some(caller)));

        let rewired = arena.reparent_children(parent, Some(caller), None);
        assert_eq!(rewired, 2);
        assert_eq!(arena.get(child_a).and_then(|seg| seg.caller), Some(caller));
        assert_eq!(arena.get(child_b).and_then(|seg| seg.caller), Some(caller));
        assert_eq!(arena.get(child_a).and_then(|seg| seg.scope_parent), None);
        assert_eq!(arena.get(child_b).and_then(|seg| seg.scope_parent), None);
        assert_eq!(
            arena.get(unrelated).and_then(|seg| seg.caller),
            Some(caller)
        );
    }

    #[test]
    fn test_reparent_children_rewires_scope_parent_independently() {
        let mut arena = SegmentArena::new();
        let marker = Marker::fresh();

        let scope_parent = arena.alloc(Segment::new(marker, None));
        let outer_scope = arena.alloc(Segment::new(marker, None));
        let caller = arena.alloc(Segment::new(marker, None));
        let mut lexical_child = Segment::new(marker, Some(caller));
        lexical_child.scope_parent = Some(scope_parent);
        let lexical_child_id = arena.alloc(lexical_child);

        let rewired = arena.reparent_children(scope_parent, Some(caller), Some(outer_scope));
        assert_eq!(rewired, 1);
        assert_eq!(
            arena.get(lexical_child_id).and_then(|seg| seg.caller),
            Some(caller)
        );
        assert_eq!(
            arena.get(lexical_child_id).and_then(|seg| seg.scope_parent),
            Some(outer_scope)
        );
    }
}
