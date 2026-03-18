//! Segment arena for stable segment IDs within a run.

use crate::ids::SegmentId;
use crate::segment::Segment;

pub struct SegmentArena {
    segments: Vec<Option<Segment>>,
}

impl SegmentArena {
    pub fn new() -> Self {
        SegmentArena { segments: Vec::new() }
    }

    pub fn alloc(&mut self, segment: Segment) -> SegmentId {
        let id = SegmentId::from_index(self.segments.len());
        self.segments.push(Some(segment));
        id
    }

    pub fn free(&mut self, id: SegmentId) {
        if let Some(slot) = self.segments.get_mut(id.index()) {
            if let Some(segment) = slot.as_mut() {
                segment.frames.clear();
                segment.dispatch_id = None;
                segment.pending_python = None;
                segment.pending_error_context = None;
                segment.mode = crate::step::Mode::Deliver(crate::value::Value::Unit);
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
        new_parent: Option<SegmentId>,
    ) -> usize {
        let mut rewired = 0usize;
        for slot in &mut self.segments {
            let Some(segment) = slot.as_mut() else {
                continue;
            };
            if segment.caller == Some(old_parent) {
                segment.caller = new_parent;
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
    fn test_arena_free_sanitizes_but_does_not_reuse_id() {
        let mut arena = SegmentArena::new();

        let marker1 = Marker::fresh();
        let seg1 = Segment::new(marker1, None);
        let id1 = arena.alloc(seg1);

        assert_eq!(arena.len(), 1);

        arena.free(id1);
        assert_eq!(arena.len(), 1);
        let freed = arena.get(id1).expect("freed segment slot must remain addressable");
        assert_eq!(freed.marker, marker1);
        assert_eq!(freed.frame_count(), 0);
        assert!(freed.dispatch_id.is_none());

        let marker2 = Marker::fresh();
        let seg2 = Segment::new(marker2, None);
        let id2 = arena.alloc(seg2);

        assert_ne!(id1, id2);
        assert_eq!(arena.len(), 2);

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

        let rewired = arena.reparent_children(parent, Some(caller));
        assert_eq!(rewired, 2);
        assert_eq!(arena.get(child_a).and_then(|seg| seg.caller), Some(caller));
        assert_eq!(arena.get(child_b).and_then(|seg| seg.caller), Some(caller));
        assert_eq!(
            arena.get(unrelated).and_then(|seg| seg.caller),
            Some(caller)
        );
    }
}
