//! Fiber arena for stable fiber IDs within a run.

use crate::continuation::{DetachedFiber, DetachedFiberChain};
use crate::error::VMError;
use crate::ids::FiberId;
use crate::segment::Fiber;

pub struct FiberArena {
    fibers: Vec<Option<Fiber>>,
    free_list: Vec<usize>,
}

impl FiberArena {
    pub fn new() -> Self {
        FiberArena {
            fibers: Vec::new(),
            free_list: Vec::new(),
        }
    }

    pub fn alloc(&mut self, fiber: Fiber) -> FiberId {
        if let Some(idx) = self.free_list.pop() {
            self.fibers[idx] = Some(fiber);
            FiberId::from_index(idx)
        } else {
            let id = FiberId::from_index(self.fibers.len());
            self.fibers.push(Some(fiber));
            id
        }
    }

    pub fn free(&mut self, id: FiberId) {
        if let Some(slot) = self.fibers.get_mut(id.index()) {
            if slot.take().is_some() {
                self.free_list.push(id.index());
            }
        }
    }

    pub fn detach_chain(
        &mut self,
        head: FiberId,
        last_fiber: FiberId,
    ) -> Result<DetachedFiberChain, VMError> {
        let ids = self.chain_ids(head, last_fiber)?;
        let mut detached = Vec::with_capacity(ids.len());

        for id in ids {
            let idx = id.index();
            let slot = self.fibers.get_mut(idx).ok_or_else(|| {
                VMError::internal(format!("detach_chain: fiber {} out of range", idx))
            })?;
            let fiber = slot.take().ok_or_else(|| {
                VMError::internal(format!("detach_chain: fiber {} is not in arena", idx))
            })?;
            detached.push(DetachedFiber { id, fiber });
        }

        let mut chain = DetachedFiberChain::new(head, last_fiber, detached);
        let _ = chain.set_tail_parent(None);
        Ok(chain)
    }

    pub fn attach_chain(
        &mut self,
        mut chain: DetachedFiberChain,
        tail_parent: Option<FiberId>,
    ) -> Result<FiberId, VMError> {
        let head = chain.head();
        let _ = chain.set_tail_parent(tail_parent);

        for DetachedFiber { id, fiber } in chain.into_fibers() {
            let idx = id.index();
            if self.free_list.contains(&idx) {
                return Err(VMError::internal(format!(
                    "attach_chain: fiber {} slot was released",
                    idx
                )));
            }
            let slot = self.fibers.get_mut(idx).ok_or_else(|| {
                VMError::internal(format!("attach_chain: fiber {} out of range", idx))
            })?;
            if slot.is_some() {
                return Err(VMError::internal(format!(
                    "attach_chain: fiber {} slot is occupied",
                    idx
                )));
            }
            *slot = Some(fiber);
        }

        Ok(head)
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
        self.free_list.clear();
    }

    pub fn shrink_to_fit(&mut self) {
        self.fibers.shrink_to_fit();
        self.free_list.shrink_to_fit();
    }

    fn chain_ids(&self, head: FiberId, last_fiber: FiberId) -> Result<Vec<FiberId>, VMError> {
        let mut ids = Vec::new();
        let mut cursor = head;

        loop {
            let fiber = self.segments_get_for_chain(cursor)?;
            ids.push(cursor);
            if cursor == last_fiber {
                return Ok(ids);
            }
            cursor = fiber.parent.ok_or_else(|| {
                VMError::internal(format!(
                    "detach_chain: fiber {} does not reach tail {}",
                    head.index(),
                    last_fiber.index()
                ))
            })?;
        }
    }

    fn segments_get_for_chain(&self, id: FiberId) -> Result<&Fiber, VMError> {
        self.get(id).ok_or_else(|| {
            VMError::internal(format!("detach_chain: fiber {} not found", id.index()))
        })
    }
}

/// Status of an arena slot, as observed by the invariant checker.
#[cfg(feature = "invariant-checks")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SlotStatus {
    /// Slot holds a live fiber.
    Live,
    /// Slot is empty but NOT on the free list — reserved for a detached fiber
    /// currently owned by a continuation (single-location law).
    VacantReserved,
    /// Slot is empty and on the free list — fiber was freed.
    VacantFree,
    /// Index was never allocated.
    OutOfRange,
}

#[cfg(feature = "invariant-checks")]
impl FiberArena {
    pub(crate) fn slot_status(&self, id: FiberId) -> SlotStatus {
        let idx = id.index();
        match self.fibers.get(idx) {
            None => SlotStatus::OutOfRange,
            Some(Some(_)) => SlotStatus::Live,
            Some(None) => {
                if self.free_list.contains(&idx) {
                    SlotStatus::VacantFree
                } else {
                    SlotStatus::VacantReserved
                }
            }
        }
    }

    /// Free-list hygiene: in-bounds, no duplicates, every entry vacant.
    pub(crate) fn free_list_violations(&self) -> Vec<String> {
        let mut violations = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for &idx in &self.free_list {
            if !seen.insert(idx) {
                violations.push(format!("arena: free_list contains duplicate index {idx}"));
            }
            match self.fibers.get(idx) {
                None => violations.push(format!("arena: free_list index {idx} out of range")),
                Some(Some(_)) => violations.push(format!(
                    "arena: free_list index {idx} points at a live fiber"
                )),
                Some(None) => {}
            }
        }
        violations
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
    fn test_arena_free_reuses_slot() {
        let mut arena = FiberArena::new();

        let seg1 = Fiber::new(None);
        let id1 = arena.alloc(seg1);

        assert_eq!(arena.len(), 1);

        arena.free(id1);
        assert_eq!(arena.len(), 0);
        assert!(arena.get(id1).is_none());

        let seg2 = Fiber::new(None);
        let id2 = arena.alloc(seg2);

        assert_eq!(id1, id2, "freed slot should be reused");
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
        assert_eq!(seg_ref.frames.len(), 1);
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

    #[test]
    fn test_detach_chain_moves_fibers_without_releasing_slots() {
        let mut arena = FiberArena::new();

        let boundary = arena.alloc(Fiber::new(None));
        let body = arena.alloc(Fiber::new(Some(boundary)));

        let chain = arena.detach_chain(body, boundary).unwrap();
        assert_eq!(chain.head(), body);
        assert_eq!(chain.last_fiber(), boundary);
        assert_eq!(arena.len(), 0);
        assert_eq!(arena.slot_count(), 2);

        let unrelated = arena.alloc(Fiber::new(None));
        assert_eq!(
            unrelated.index(),
            2,
            "detached fiber slots stay reserved until the chain is attached or dropped"
        );

        let head = arena.attach_chain(chain, Some(unrelated)).unwrap();
        assert_eq!(head, body);
        assert_eq!(arena.len(), 3);
        assert_eq!(
            arena.get(boundary).and_then(|fiber| fiber.parent),
            Some(unrelated)
        );
    }
}
