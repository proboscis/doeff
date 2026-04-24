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
use crate::driver::{Signal, StepResult};
use crate::effect::DispatchEffect;
use crate::ids::FiberId;
use crate::segment::{Fiber, Handler};
use crate::value::{CallableRef, Value};
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
    pub fn perform_effect(&mut self, effect: &DispatchEffect) -> Result<PerformResult, StepResult> {
        let current = self.current_segment.ok_or_else(|| StepResult::Error {
            error: crate::error::VMError::internal("perform: no current fiber"),
            context: None,
        })?;

        // Walk up parent chain to find a handler boundary.
        // No skip-self needed — handler code runs on the parent fiber (above
        // the boundary), so performs from handler code naturally find outer handlers.
        let (handler_fiber_id, _handler_parent) = self
            .find_handler_for_effect(current, effect)
            .ok_or_else(|| {
                // Capture execution context before erroring so traceback is available.
                StepResult::Error {
                    error: crate::error::VMError::unhandled_effect(effect.clone()),
                    context: Some(self.collect_rich_execution_context()),
                }
            })?;

        let handler_callable = self
            .segments
            .get(handler_fiber_id)
            .and_then(|seg| seg.prompt_handler().cloned())
            .ok_or_else(|| StepResult::Error {
                error: crate::error::VMError::internal("perform: handler has no callable"),
                context: None,
            })?;

        // OCaml 5: continuation includes everything from body up to AND INCLUDING
        // the handler boundary. This way continue_k links boundary.parent = caller,
        // restoring the correct chain topology.
        let boundary_parent = self.segments.get(handler_fiber_id).and_then(|s| s.parent);
        let chain = self
            .segments
            .detach_chain(current, handler_fiber_id)
            .map_err(|error| StepResult::Error {
                error,
                context: None,
            })?;
        let continuation = Continuation::from_chain(chain);

        // handler_parent is boundary's former parent
        let handler_parent = boundary_parent;

        // Switch to the handler's parent
        self.current_segment = handler_parent;

        Ok(PerformResult {
            continuation,
            handler_fiber_id,
            handler_callable,
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
    pub fn continue_k(&mut self, k: &mut Continuation) -> Result<(), crate::error::VMError> {
        let chain = k.take().ok_or_else(|| {
            let current = self
                .current_segment
                .map(|f| format!(" (current fiber={})", f.index()))
                .unwrap_or_default();
            crate::error::VMError::internal(format!(
                "Resume: continuation already consumed (one-shot violation){current}"
            ))
        })?;

        let caller = self.current_segment;
        let head = self.segments.attach_chain(chain, caller)?;
        self.current_segment = Some(head);

        Ok(())
    }

    pub(crate) fn continue_attached_chain(
        &mut self,
        head: FiberId,
        last_fiber: FiberId,
    ) -> Result<(), crate::error::VMError> {
        let caller = self.current_segment;
        let Some(tail) = self.segments.get_mut(last_fiber) else {
            return Err(crate::error::VMError::internal(format!(
                "continue_attached_chain: tail fiber {} not found",
                last_fiber.index()
            )));
        };
        tail.parent = caller;
        self.current_segment = Some(head);
        Ok(())
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
            None => {
                return StepResult::Error {
                    error: crate::error::VMError::internal("fiber_return: no current fiber"),
                    context: None,
                };
            }
        };

        let parent = self.segments.get(current).and_then(|s| s.parent);

        // Switch to parent
        self.current_segment = parent;

        // Free the completed fiber
        self.segments.free(current);

        // Deliver return value to parent
        StepResult::Continue(Signal::send(value))
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
    /// Find the first boundary (interceptor or handler) walking up from start.
    /// Returns (fiber_id, parent, is_interceptor).
    pub(crate) fn find_next_boundary(
        &self,
        start: FiberId,
        _effect: &DispatchEffect,
    ) -> Option<(FiberId, Option<FiberId>, bool)> {
        let mut cursor = Some(start);
        while let Some(fid) = cursor {
            let seg = self.segments.get(fid)?;
            if seg.is_intercept_boundary() {
                return Some((fid, seg.parent, true));
            }
            if seg.is_prompt_boundary() {
                return Some((fid, seg.parent, false));
            }
            cursor = seg.parent;
        }
        None
    }

    /// Find the first prompt (handler) boundary, skipping interceptors.
    pub(crate) fn find_handler_for_effect(
        &self,
        start: FiberId,
        effect: &DispatchEffect,
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
    pub(crate) fn find_fiber_before(&self, start: FiberId, target: FiberId) -> Option<FiberId> {
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
    pub handler_callable: CallableRef,
}
