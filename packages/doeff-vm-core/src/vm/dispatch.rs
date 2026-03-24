//! The five OCaml 5 effect handler operations.
//!
//! 1. match_with  — install handler (create boundary fiber, attach to chain)
//! 2. perform     — yield effect (detach chain at handler, create continuation)
//! 3. continue_k  — resume continuation (reattach chain, deliver value)
//! 4. reperform   — pass effect to outer handler (append fiber to continuation)
//! 5. fiber_return — fiber completes (call handler's handle_value)
//!
//! All operations work by manipulating parent pointers in the arena.
//! No dispatch ID. No accumulated trace state. No ProgramDispatch.

use crate::continuation::Continuation;
use crate::driver::{Mode, StepResult};
use crate::effect::DispatchEffect;
use crate::error::VMError;
use crate::ids::{FiberId, Marker, SegmentId};
use crate::segment::{Fiber, Handler};
use crate::value::Value;
use crate::vm::VM;

impl VM {
    // -----------------------------------------------------------------------
    // 1. match_with — install a handler
    // -----------------------------------------------------------------------

    /// Install a handler: allocate a new boundary fiber, set its parent to
    /// current_fiber, and switch execution to the new fiber.
    ///
    /// OCaml 5 equivalent:
    ///   F_new.handler = H
    ///   F_new.handler.parent = current_stack
    ///   current_stack = F_new
    pub fn match_with(&mut self, handler: Handler) -> FiberId {
        let parent = self.current_segment;
        let fiber = Fiber::new_boundary(parent, handler);
        let fiber_id = self.segments.alloc(fiber);
        self.current_segment = Some(fiber_id);
        fiber_id
    }

    // -----------------------------------------------------------------------
    // 2. perform — yield an effect
    // -----------------------------------------------------------------------

    /// Perform an effect: walk up the chain to find a matching handler,
    /// detach the chain at the handler boundary, create a continuation,
    /// switch to the handler's parent, and call the handler.
    ///
    /// OCaml 5 equivalent:
    ///   old = current_stack
    ///   parent = walk_to_handler(old)
    ///   old.handler.parent = NULL  // detach
    ///   cont = (old, last_fiber=old)
    ///   current_stack = parent
    ///   call handler.handle_effect(effect, cont)
    ///
    /// Returns (continuation, handler_fiber_id, effect) for the step machine to process.
    pub fn perform_effect(
        &mut self,
        effect: &DispatchEffect,
    ) -> Result<PerformResult, StepResult> {
        let current = self.current_segment.ok_or_else(|| {
            StepResult::Error(crate::error::VMError::internal("perform: no current fiber"))
        })?;

        // Walk up parent chain to find a handler boundary.
        // No skip-self needed — handler code runs on the parent fiber (above
        // the boundary), so performs from handler code naturally find outer handlers.
        let (handler_fiber_id, handler_parent) = self
            .find_handler_for_effect(current, effect)
            .ok_or_else(|| {
                StepResult::Error(crate::error::VMError::internal(format!(
                    "perform: no handler found for effect"
                )))
            })?;

        // OCaml 5: continuation includes everything from body up to AND INCLUDING
        // the handler boundary. This way continue_k links boundary.parent = caller,
        // restoring the correct chain topology.
        // Detach: cut boundary from its parent
        let boundary_parent = self.segments.get(handler_fiber_id).and_then(|s| s.parent);
        if let Some(seg) = self.segments.get_mut(handler_fiber_id) {
            seg.parent = None;
        }

        // Create continuation: head = current (body), last = boundary
        let continuation = Continuation::new(current, handler_fiber_id);

        // handler_parent is boundary's former parent
        let handler_parent = boundary_parent;

        // Switch to the handler's parent
        self.current_segment = handler_parent;

        Ok(PerformResult {
            continuation,
            handler_fiber_id,
        })
    }

    // -----------------------------------------------------------------------
    // 3. continue_k — resume a continuation
    // -----------------------------------------------------------------------

    /// Resume a continuation: reattach the chain by linking its tail to the
    /// current fiber, then switch execution to the chain's head.
    ///
    /// OCaml 5 equivalent:
    ///   (head, last) = atomic_swap(cont, NULL)  // one-shot
    ///   last.handler.parent = current_stack      // reattach
    ///   current_stack = head                     // switch
    ///   deliver v to head
    pub fn continue_k(
        &mut self,
        k: &mut Continuation,
        value: Value,
    ) -> Result<(), StepResult> {
        let (head, last) = k.take().ok_or_else(|| {
            StepResult::Error(crate::error::VMError::internal(
                "continue: continuation already consumed (one-shot violation)",
            ))
        })?;

        // Reattach: link tail to current fiber (one pointer write)
        let caller = self.current_segment;
        if let Some(seg) = self.segments.get_mut(last) {
            seg.parent = caller;
        }

        // Switch to the head of the resumed chain
        self.current_segment = Some(head);
        self.mode = Mode::Send(value);

        Ok(())
    }

    // -----------------------------------------------------------------------
    // 4. reperform — pass/delegate effect to outer handler
    // -----------------------------------------------------------------------

    /// Reperform: the current handler doesn't handle this effect.
    /// Append the current fiber to the continuation chain, then re-perform
    /// at the parent handler.
    ///
    /// OCaml 5 equivalent:
    ///   last_fiber.handler.parent = current_stack  // append self to chain
    ///   last_fiber = current_stack
    ///   parent = current_stack.handler.parent
    ///   current_stack.handler.parent = NULL         // detach self
    ///   current_stack = parent
    ///   call parent.handler.handle_effect(effect, cont, last_fiber)
    pub fn reperform(
        &mut self,
        k: &mut Continuation,
        effect: &DispatchEffect,
    ) -> Result<PerformResult, StepResult> {
        let current = self.current_segment.ok_or_else(|| {
            StepResult::Error(crate::error::VMError::internal("reperform: no current fiber"))
        })?;

        // Append current fiber to the continuation chain
        // Link continuation's last_fiber → current
        if let Some(last) = k.last_fiber() {
            if let Some(seg) = self.segments.get_mut(last) {
                seg.parent = Some(current);
            }
        }
        // Update continuation's last_fiber to current
        // (we need the current fiber's parent as the next handler target)
        let current_parent = self.segments.get(current).and_then(|s| s.parent);

        // Detach current from its parent
        if let Some(seg) = self.segments.get_mut(current) {
            seg.parent = None;
        }
        k.last_fiber = Some(current); // update tail — direct field access since we own the mutation

        // Find the next outer handler
        let (handler_fiber_id, handler_parent) = current_parent
            .and_then(|p| self.find_handler_for_effect(p, effect))
            .ok_or_else(|| {
                StepResult::Error(crate::error::VMError::internal(
                    "reperform: no outer handler found",
                ))
            })?;

        // Switch to handler's parent
        self.current_segment = handler_parent;

        Ok(PerformResult {
            continuation: Continuation::single(FiberId::from_index(0)), // placeholder — k is updated in place
            handler_fiber_id,
        })
    }

    // -----------------------------------------------------------------------
    // 5. fiber_return — fiber completes normally
    // -----------------------------------------------------------------------

    /// A fiber completed: switch to parent, free the fiber, call handle_value.
    ///
    /// OCaml 5 equivalent:
    ///   old = current_stack
    ///   parent = old.handler.parent
    ///   hval = old.handler.handle_value
    ///   current_stack = parent
    ///   free(old)
    ///   hval(return_value)
    pub fn fiber_return(&mut self, value: Value) -> StepResult {
        let current = match self.current_segment {
            Some(id) => id,
            None => return StepResult::Error(crate::error::VMError::internal(
                "fiber_return: no current fiber",
            )),
        };

        let parent = self.segments.get(current).and_then(|s| s.parent);

        // Switch to parent
        self.current_segment = parent;

        // Free the completed fiber
        self.segments.free(current);

        // Deliver return value to parent
        self.mode = Mode::Send(value);
        StepResult::Continue
    }

    // -----------------------------------------------------------------------
    // Helpers — chain walking
    // -----------------------------------------------------------------------

    /// Walk the chain from `start` following parent pointers.
    pub fn walk_chain(&self, start: FiberId) -> Vec<FiberId> {
        let mut chain = Vec::new();
        let mut cursor = Some(start);
        while let Some(fid) = cursor {
            chain.push(fid);
            cursor = self.segments.get(fid).and_then(|s| s.parent);
        }
        chain
    }

    /// Find a handler boundary that handles the given effect,
    /// walking up from `start` through parent pointers.
    /// Returns (handler_fiber_id, handler_parent).
    pub(crate) fn find_handler_for_effect(
        &self,
        start: FiberId,
        _effect: &DispatchEffect,
    ) -> Option<(FiberId, Option<FiberId>)> {
        let mut cursor = Some(start);
        while let Some(fid) = cursor {
            let seg = self.segments.get(fid)?;
            if seg.is_prompt_boundary() {
                let parent = seg.parent;
                return Some((fid, parent));
            }
            cursor = seg.parent;
        }
        None
    }

    /// Find the fiber just before `target` in the chain starting from `start`.
    /// Returns None if start == target (single fiber).
    pub(crate) fn find_fiber_before(
        &self,
        start: FiberId,
        target: FiberId,
    ) -> Option<FiberId> {
        if start == target {
            return None;
        }
        let mut cursor = start;
        loop {
            let seg = self.segments.get(cursor)?;
            match seg.parent {
                Some(parent) if parent == target => return Some(cursor),
                Some(parent) => cursor = parent,
                None => return None,
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Result types
// ---------------------------------------------------------------------------

pub struct PerformResult {
    pub continuation: Continuation,
    pub handler_fiber_id: FiberId,
}
