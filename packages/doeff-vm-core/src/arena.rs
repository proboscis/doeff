//! Fiber arena for stable fiber IDs within a run.

use std::sync::{Arc, Mutex};

use crate::continuation::{DetachedFiber, DetachedFiberChain};
use crate::error::VMError;
use crate::ids::FiberId;
use crate::segment::Fiber;

/// Channel through which a dropped `DetachedFiberChain` returns its arena
/// slot indices for reuse (#497).
///
/// This is allocator bookkeeping, NOT fiber ownership (ISSUE-VM-001 G1 /
/// SPEC-VM-021): no Fiber, chain, or continuation ever flows through it.
/// The chain's fibers move into Continuation ownership at detach and are
/// destroyed by the chain's own Drop; only the now-permanently-vacant slot
/// indices are reported here so the arena can return them to its free list
/// instead of stranding them until run end. Reports may arrive from
/// arbitrary Python dealloc points, on any thread — hence the Mutex.
#[derive(Debug, Default)]
pub struct SlotReclaimQueue {
    dropped_slot_indices: Mutex<Vec<usize>>,
}

impl SlotReclaimQueue {
    /// Called from `DetachedFiberChain::drop` with the slot indices the
    /// chain still owned when it was abandoned.
    pub(crate) fn report_dropped_slots(&self, indices: impl Iterator<Item = usize>) {
        let mut queue = self.dropped_slot_indices.lock().unwrap();
        queue.extend(indices);
    }

    /// Drain all pending reports. Cheap when empty (no allocation).
    fn take_pending(&self) -> Vec<usize> {
        let mut queue = self.dropped_slot_indices.lock().unwrap();
        std::mem::take(&mut *queue)
    }
}

pub struct FiberArena {
    fibers: Vec<Option<Fiber>>,
    free_list: Vec<usize>,
    /// `on_free_list[idx]` is true exactly when `idx` is on `free_list` —
    /// the O(1) answer to "was this vacant slot freed, or is it reserved for
    /// a detached fiber?". `attach_chain` asks it for every fiber of every
    /// resumed continuation; a linear `free_list.contains` there made each
    /// resume cost (chain length) x (free slots), which grew with the peak
    /// number of fibers a long-running process ever held (a 60-handler-deep
    /// effect went from 34 us to 13.7 ms after 5,000 finished tasks).
    on_free_list: Vec<bool>,
    /// Reclaim reports from detached chains dropped without reattachment
    /// (#497). Drained back into `free_list` before allocating. Replaced
    /// wholesale on `clear()` so a chain outliving its run session cannot
    /// poison the next session's free list.
    slot_reclaim: Arc<SlotReclaimQueue>,
}

impl FiberArena {
    pub fn new() -> Self {
        FiberArena {
            fibers: Vec::new(),
            free_list: Vec::new(),
            on_free_list: Vec::new(),
            slot_reclaim: Arc::new(SlotReclaimQueue::default()),
        }
    }

    pub fn alloc(&mut self, fiber: Fiber) -> FiberId {
        self.reclaim_dropped_chain_slots();
        if let Some(idx) = self.free_list.pop() {
            self.on_free_list[idx] = false;
            self.fibers[idx] = Some(fiber);
            FiberId::from_index(idx)
        } else {
            let id = FiberId::from_index(self.fibers.len());
            self.fibers.push(Some(fiber));
            self.on_free_list.push(false);
            id
        }
    }

    /// Return slots abandoned by dropped detached chains to the free list.
    ///
    /// A detached chain owns its arena slots (single-location law): they
    /// stay vacant-reserved while the continuation is live. When the chain
    /// is dropped without reattachment its Drop impl reports the indices to
    /// `slot_reclaim`; this reconciliation makes them allocatable again
    /// instead of stranding until `clear()` at run end (#497).
    pub fn reclaim_dropped_chain_slots(&mut self) {
        for idx in self.slot_reclaim.take_pending() {
            if crate::invariant_switch::invariant_checks_enabled() {
                if !matches!(self.fibers.get(idx), Some(None)) {
                    panic!(
                        "arena: dropped-chain slot {idx} is not vacant-reserved \
                         (single-location law violated by reclaim)"
                    );
                }
                if self.on_free_list[idx] {
                    panic!("arena: dropped-chain slot {idx} is already on the free list");
                }
            }
            self.push_free(idx);
        }
    }

    pub fn free(&mut self, id: FiberId) {
        if let Some(slot) = self.fibers.get_mut(id.index()) {
            if slot.take().is_some() {
                self.push_free(id.index());
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
        chain.arm_slot_reclaim(Arc::clone(&self.slot_reclaim));
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
            if self.on_free_list.get(idx).copied().unwrap_or(false) {
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
        self.on_free_list.clear();
        // Detach from chains that outlive this session: their late drops
        // report into the replaced queue, which nobody drains — a stale
        // index from a previous session must never reach the new free list.
        self.slot_reclaim = Arc::new(SlotReclaimQueue::default());
    }

    pub fn shrink_to_fit(&mut self) {
        self.fibers.shrink_to_fit();
        self.free_list.shrink_to_fit();
        self.on_free_list.shrink_to_fit();
    }

    /// Put a vacant slot on the free list, keeping `on_free_list` in step.
    fn push_free(&mut self, idx: usize) {
        self.on_free_list[idx] = true;
        self.free_list.push(idx);
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

impl FiberArena {
    pub(crate) fn slot_status(&self, id: FiberId) -> SlotStatus {
        let idx = id.index();
        match self.fibers.get(idx) {
            None => SlotStatus::OutOfRange,
            Some(Some(_)) => SlotStatus::Live,
            Some(None) => {
                if self.on_free_list[idx] {
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
            if !self.on_free_list.get(idx).copied().unwrap_or(false) {
                violations.push(format!("arena: free_list index {idx} is not marked on_free_list"));
            }
            match self.fibers.get(idx) {
                None => violations.push(format!("arena: free_list index {idx} out of range")),
                Some(Some(_)) => violations.push(format!(
                    "arena: free_list index {idx} points at a live fiber"
                )),
                Some(None) => {}
            }
        }
        let marked = self.on_free_list.iter().filter(|&&on| on).count();
        if marked != self.free_list.len() {
            violations.push(format!(
                "arena: {marked} slots marked on_free_list but free_list holds {}",
                self.free_list.len()
            ));
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

    #[test]
    fn test_detached_chains_keep_their_fibers_in_chain_order() {
        // DetachedFiberChain finds a fiber by its position
        // : a walk from the head expects each parent right
        // after the fiber before it, and the tail last. A detached chain and
        // the chain a Pass appends behind it keep that order.
        let mut arena = FiberArena::new();
        let outer = arena.alloc(Fiber::new(None));
        let inner = arena.alloc(Fiber::new(Some(outer)));
        let middle = arena.alloc(Fiber::new(Some(inner)));
        let body = arena.alloc(Fiber::new(Some(middle)));

        let mut chain = arena.detach_chain(body, middle).unwrap();
        chain.append(arena.detach_chain(inner, outer).unwrap());
        let ids: Vec<FiberId> = chain.fibers().iter().map(|entry| entry.id).collect();
        assert_eq!(ids, vec![body, middle, inner, outer]);
        let parents: Vec<Option<FiberId>> =
            chain.fibers().iter().map(|entry| entry.fiber.parent).collect();
        assert_eq!(parents, vec![Some(middle), Some(inner), Some(outer), None]);
        assert_eq!(chain.last_fiber(), outer);
    }

    #[test]
    fn test_a_chain_out_of_chain_order_still_finds_its_fibers() {
        // The position is only where a fiber is looked for first: a chain
        // laid out in another order still finds each one.
        let tail = FiberId::from_index(0);
        let head = FiberId::from_index(1);
        let mut chain = DetachedFiberChain::new(
            head,
            tail,
            vec![
                DetachedFiber {
                    id: tail,
                    fiber: Fiber::new(None),
                },
                DetachedFiber {
                    id: head,
                    fiber: Fiber::new(Some(tail)),
                },
            ],
        );
        let reattach_to = FiberId::from_index(7);
        assert!(chain.set_tail_parent(Some(reattach_to)));
        assert!(chain.set_parent(head, Some(tail)));
        assert_eq!(chain.fibers()[0].fiber.parent, Some(reattach_to));
        assert_eq!(chain.fibers()[1].fiber.parent, Some(tail));
    }

    #[test]
    fn test_free_marks_follow_the_free_list() {
        // on_free_list is the O(1) answer attach_chain and slot_status read:
        // it must agree with free_list through free, reuse and reclaim.
        let mut arena = FiberArena::new();
        let ids: Vec<FiberId> = (0..6).map(|_| arena.alloc(Fiber::new(None))).collect();
        arena.free(ids[1]);
        arena.free(ids[4]);
        assert_eq!(arena.slot_status(ids[1]), SlotStatus::VacantFree);
        assert_eq!(arena.slot_status(ids[0]), SlotStatus::Live);

        let boundary = arena.alloc(Fiber::new(None));
        let body = arena.alloc(Fiber::new(Some(boundary)));
        let chain = arena.detach_chain(body, boundary).unwrap();
        assert_eq!(arena.slot_status(body), SlotStatus::VacantReserved);
        assert!(arena.free_list_violations().is_empty());

        drop(chain);
        let reused = arena.alloc(Fiber::new(None));
        assert_eq!(arena.slot_status(reused), SlotStatus::Live);
        assert!(arena.free_list_violations().is_empty());

        arena.free(ids[0]);
        arena.free(reused);
        assert!(arena.free_list_violations().is_empty());
        arena.clear();
        assert!(arena.free_list_violations().is_empty());
    }

    #[test]
    fn test_attach_chain_refuses_a_released_slot() {
        // The O(1) mark keeps the refusal the linear search gave: a chain
        // that names a slot already back on the free list is not attached.
        let mut arena = FiberArena::new();
        let released = arena.alloc(Fiber::new(None));
        arena.free(released);
        let chain = DetachedFiberChain::new(
            released,
            released,
            vec![DetachedFiber {
                id: released,
                fiber: Fiber::new(None),
            }],
        );
        let error = arena.attach_chain(chain, None).unwrap_err();
        assert!(format!("{error:?}").contains("slot was released"), "{error:?}");
    }

    #[test]
    fn test_dropped_chain_slots_are_reclaimed_on_next_alloc() {
        // #497: a detached chain dropped without reattachment (abort-style
        // handler, scheduler cancellation) must return its slots to the
        // free list instead of stranding them until run end.
        let mut arena = FiberArena::new();

        let boundary = arena.alloc(Fiber::new(None));
        let body = arena.alloc(Fiber::new(Some(boundary)));

        let chain = arena.detach_chain(body, boundary).unwrap();
        assert_eq!(arena.len(), 0);
        assert_eq!(arena.slot_count(), 2);

        drop(chain);

        let reused_a = arena.alloc(Fiber::new(None));
        let reused_b = arena.alloc(Fiber::new(None));
        assert!(reused_a.index() < 2, "first alloc must reuse a reclaimed slot");
        assert!(reused_b.index() < 2, "second alloc must reuse a reclaimed slot");
        assert_eq!(
            arena.slot_count(),
            2,
            "slot vector must not grow after an abandoned chain is dropped"
        );
    }

    #[test]
    fn test_abort_loop_slot_count_is_bounded() {
        // #497 regression shape: repeated detach-then-drop cycles (one per
        // abandoned dispatch) must keep the slot vector bounded.
        let mut arena = FiberArena::new();
        for _ in 0..100 {
            let boundary = arena.alloc(Fiber::new(None));
            let body = arena.alloc(Fiber::new(Some(boundary)));
            let chain = arena.detach_chain(body, boundary).unwrap();
            drop(chain);
        }
        assert_eq!(arena.len(), 0);
        assert_eq!(
            arena.slot_count(),
            2,
            "100 abandoned chains must reuse the same two slots"
        );
    }

    #[test]
    fn test_chain_dropped_after_clear_does_not_poison_next_session() {
        // A chain can outlive its run session (a Python K held across
        // run()). Its late drop must not inject stale indices into the
        // next session's free list.
        let mut arena = FiberArena::new();
        let boundary = arena.alloc(Fiber::new(None));
        let body = arena.alloc(Fiber::new(Some(boundary)));
        let chain = arena.detach_chain(body, boundary).unwrap();

        arena.clear(); // run session ends while the chain is still owned outside

        let live = arena.alloc(Fiber::new(None));
        drop(chain); // stale report goes to the orphaned queue

        let next = arena.alloc(Fiber::new(None));
        assert_ne!(next, live, "stale reclaim must not hand out a live slot");
        assert!(arena.get(live).is_some(), "live fiber must survive stale drops");
        assert_eq!(arena.len(), 2);
    }

    #[test]
    fn test_attached_chain_does_not_reclaim_slots() {
        // Consuming a chain via attach_chain must NOT report its slots:
        // the fibers are live in the arena again.
        let mut arena = FiberArena::new();
        let boundary = arena.alloc(Fiber::new(None));
        let body = arena.alloc(Fiber::new(Some(boundary)));
        let chain = arena.detach_chain(body, boundary).unwrap();

        arena.attach_chain(chain, None).unwrap();
        arena.reclaim_dropped_chain_slots();

        assert_eq!(arena.len(), 2);
        let fresh = arena.alloc(Fiber::new(None));
        assert_eq!(
            fresh.index(),
            2,
            "no slot may be recycled while its fiber is live"
        );
    }
}
