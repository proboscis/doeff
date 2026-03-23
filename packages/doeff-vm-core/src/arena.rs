//! Fiber arena for stable fiber IDs within a run.

use crate::ids::FiberId;
use crate::segment::Fiber;

pub struct FiberArena {
    fibers: Vec<Option<Fiber>>,
}

impl FiberArena {
    pub fn new() -> Self {
        FiberArena { fibers: Vec::new() }
    }

    pub fn alloc(&mut self, fiber: Fiber) -> FiberId {
        let id = FiberId::from_index(self.fibers.len());
        self.fibers.push(Some(fiber));
        id
    }

    pub fn free(&mut self, id: FiberId) {
        if let Some(slot) = self.fibers.get_mut(id.index()) {
            let _ = slot.take();
        }
    }

    pub fn get(&self, id: FiberId) -> Option<&Fiber> {
        self.fibers.get(id.index()).and_then(|s| s.as_ref())
    }

    pub fn get_mut(&mut self, id: FiberId) -> Option<&mut Fiber> {
        self.fibers.get_mut(id.index()).and_then(|s| s.as_mut())
    }

    pub fn iter(&self) -> impl Iterator<Item = (FiberId, &Fiber)> {
        self.fibers
            .iter()
            .enumerate()
            .filter_map(|(idx, slot)| slot.as_ref().map(|fiber| (FiberId::from_index(idx), fiber)))
    }

    pub fn iter_mut(&mut self) -> impl Iterator<Item = (FiberId, &mut Fiber)> {
        self.fibers
            .iter_mut()
            .enumerate()
            .filter_map(|(idx, slot)| slot.as_mut().map(|fiber| (FiberId::from_index(idx), fiber)))
    }

    /// Rewire children that currently point at `old_parent` so they point to `new_parent`.
    ///
    /// This keeps parent chains valid when a completed parent fiber is freed while
    /// descendant fibers are still alive (for example across scheduler preemption).
    pub fn reparent_children(&mut self, old_parent: FiberId, new_parent: Option<FiberId>) -> usize {
        let mut rewired = 0usize;
        for slot in &mut self.fibers {
            let Some(fiber) = slot.as_mut() else {
                continue;
            };
            if fiber.parent == Some(old_parent) {
                fiber.parent = new_parent;
                rewired += 1;
            }
        }
        rewired
    }

    pub fn len(&self) -> usize {
        self.fibers.iter().filter(|s| s.is_some()).count()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn capacity(&self) -> usize {
        self.fibers.capacity()
    }

    pub fn slot_count(&self) -> usize {
        self.fibers.len()
    }

    pub fn clear(&mut self) {
        self.fibers.clear();
    }

    pub fn shrink_to_fit(&mut self) {
        self.fibers.shrink_to_fit();
    }
}

impl Default for FiberArena {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_arena_alloc_and_get() {
        let mut arena = FiberArena::new();

        let seg1 = Fiber::new(None);
        let id1 = arena.alloc(seg1);

        let seg2 = Fiber::new(None);
        let id2 = arena.alloc(seg2);

        assert_ne!(id1, id2);
        assert_eq!(arena.len(), 2);

        assert!(arena.get(id1).is_some());
    }

    #[test]
    fn test_arena_free_releases_slot_without_reusing_id() {
        let mut arena = FiberArena::new();

        let seg1 = Fiber::new(None);
        let id1 = arena.alloc(seg1);

        assert_eq!(arena.len(), 1);

        arena.free(id1);
        assert_eq!(arena.len(), 0);
        assert!(arena.get(id1).is_none());

        let seg2 = Fiber::new(None);
        let id2 = arena.alloc(seg2);

        assert_ne!(id1, id2);
        assert_eq!(arena.len(), 1);

        assert!(arena.get(id2).is_some());
    }

    #[test]
    fn test_arena_get_mut() {
        let mut arena = FiberArena::new();

        let seg = Fiber::new(None);
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
        let mut arena = FiberArena::new();

        let parent = arena.alloc(Fiber::new(None));
        let caller = arena.alloc(Fiber::new(None));
        let child_a = arena.alloc(Fiber::new(Some(parent)));
        let child_b = arena.alloc(Fiber::new(Some(parent)));
        let unrelated = arena.alloc(Fiber::new(Some(caller)));

        let rewired = arena.reparent_children(parent, Some(caller));
        assert_eq!(rewired, 2);
        assert_eq!(arena.get(child_a).and_then(|seg| seg.parent), Some(caller));
        assert_eq!(arena.get(child_b).and_then(|seg| seg.parent), Some(caller));
        assert_eq!(
            arena.get(unrelated).and_then(|seg| seg.parent),
            Some(caller)
        );
    }
}
