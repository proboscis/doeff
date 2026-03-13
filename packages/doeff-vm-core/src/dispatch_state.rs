//! Dispatch-domain state and helper logic for VM composition.
//!
//! Dispatches are stored in a HashMap keyed by DispatchId for O(1) lookup.
//! Insertion order is tracked for iteration that requires most-recent-first semantics.

use std::collections::{HashMap, HashSet};

use crate::continuation::Continuation;
use crate::dispatch::Dispatch;
use crate::effect::DispatchEffect;
use crate::error::VMError;
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::step::PyException;
use crate::trace_state::ActiveChainAssemblyState;

#[derive(Debug, Clone, Default)]
pub(crate) struct DispatchState {
    dispatches: HashMap<DispatchId, Dispatch>,
    /// Insertion order for iteration (most recent last).
    insertion_order: Vec<DispatchId>,
}

pub(crate) struct WithHandlerPlan {
    pub(crate) handler_marker: Marker,
    pub(crate) outside_seg_id: SegmentId,
    pub(crate) handler: KleisliRef,
}

impl DispatchState {
    pub(crate) fn depth(&self) -> usize {
        self.dispatches.len()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.dispatches.is_empty()
    }

    /// Ordered slice of dispatches (insertion order) for trace/interceptor compatibility.
    pub(crate) fn contexts_ordered(&self) -> Vec<&Dispatch> {
        self.insertion_order
            .iter()
            .filter_map(|id| self.dispatches.get(id))
            .collect()
    }

    pub(crate) fn push_dispatch(&mut self, ctx: Dispatch) {
        let id = ctx.dispatch_id;
        self.dispatches.insert(id, ctx);
        self.insertion_order.push(id);
    }

    pub(crate) fn find_by_dispatch_id(&self, dispatch_id: DispatchId) -> Option<&Dispatch> {
        self.dispatches.get(&dispatch_id)
    }

    pub(crate) fn find_mut_by_dispatch_id(
        &mut self,
        dispatch_id: DispatchId,
    ) -> Option<&mut Dispatch> {
        self.dispatches.get_mut(&dispatch_id)
    }

    pub(crate) fn effect_for_dispatch(&self, dispatch_id: DispatchId) -> Option<DispatchEffect> {
        self.find_by_dispatch_id(dispatch_id)
            .map(|ctx| ctx.effect.clone())
    }

    pub(crate) fn dispatch_is_execution_context_effect(&self, dispatch_id: DispatchId) -> bool {
        self.find_by_dispatch_id(dispatch_id)
            .is_some_and(|ctx| ctx.is_execution_context_effect)
    }

    pub(crate) fn mark_completed(
        &mut self,
        dispatch_id: DispatchId,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        if let Some(ctx) = self.dispatches.get_mut(&dispatch_id) {
            ctx.completed = true;
            consumed_cont_ids.insert(ctx.k_current().cont_id);
        }
    }

    pub(crate) fn lazy_pop_completed(&mut self) {
        // Only pop consecutive completed entries from the top (most recent),
        // matching the original LIFO stack semantics.
        while let Some(id) = self.insertion_order.last().copied() {
            if self.dispatches.get(&id).is_some_and(|d| d.completed) {
                self.insertion_order.pop();
                self.dispatches.remove(&id);
            } else {
                break;
            }
        }
    }

    /// Check dispatch completion using the activation stack.
    ///
    /// If the continuation being consumed matches the first activation's k_passed
    /// (i.e., it's the terminal/origin continuation), the dispatch is complete.
    /// No parent-chain walking needed.
    pub(crate) fn check_dispatch_completion(&mut self, k: &Continuation) {
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(ctx) = self.dispatches.get_mut(&dispatch_id) {
                if ctx.is_terminal_continuation(k.cont_id) {
                    ctx.completed = true;
                }
            }
        }
    }

    pub(crate) fn dispatch_supports_error_context_conversion(
        &self,
        dispatch_id: DispatchId,
    ) -> bool {
        self.find_by_dispatch_id(dispatch_id)
            .is_some_and(|ctx| ctx.supports_error_context_conversion())
    }

    pub(crate) fn active_error_dispatch_original_exception(&self) -> Option<PyException> {
        // Walk insertion order in reverse to find most recent active error dispatch
        self.insertion_order
            .iter()
            .rev()
            .filter_map(|id| self.dispatches.get(id))
            .find(|ctx| !ctx.completed && ctx.original_exception.is_some())
            .and_then(|ctx| ctx.original_exception.clone())
    }

    pub(crate) fn original_exception_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<PyException> {
        self.find_by_dispatch_id(dispatch_id)
            .and_then(|ctx| ctx.original_exception.clone())
    }

    /// Find error dispatch info for a continuation using the activation stack.
    ///
    /// Returns (dispatch_id, original_exception, is_terminal) where is_terminal
    /// means the continuation is the terminal one for the dispatch (first activation).
    pub(crate) fn error_dispatch_for_continuation(
        &self,
        k: &Continuation,
    ) -> Option<(DispatchId, PyException, bool)> {
        let dispatch_id = k.dispatch_id?;
        let ctx = self.find_by_dispatch_id(dispatch_id)?;
        let original = ctx.original_exception.clone()?;

        // Use activation stack to determine terminality
        if let Some((_idx, is_terminal)) = ctx.find_activation_by_cont_id(k.cont_id) {
            return Some((dispatch_id, original, is_terminal));
        }

        // Also check k_origin directly (for cases where k is the origin itself)
        if ctx.k_origin.cont_id == k.cont_id {
            return Some((dispatch_id, original, true));
        }

        None
    }

    pub(crate) fn mark_dispatch_threw(
        &mut self,
        dispatch_id: DispatchId,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        self.mark_completed(dispatch_id, consumed_cont_ids);
    }

    pub(crate) fn mark_dispatch_completed(
        &mut self,
        dispatch_id: DispatchId,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        self.mark_completed(dispatch_id, consumed_cont_ids);
    }

    pub(crate) fn dispatch_has_terminal_handler_action(
        &self,
        dispatch_id: DispatchId,
        active_chain_state: &ActiveChainAssemblyState,
    ) -> bool {
        active_chain_state.dispatch_has_terminal_result(dispatch_id)
    }

    pub(crate) fn prepare_with_handler(
        handler: KleisliRef,
        current_segment: Option<SegmentId>,
    ) -> Result<WithHandlerPlan, VMError> {
        let handler_marker = Marker::fresh();
        let Some(outside_seg_id) = current_segment else {
            return Err(VMError::internal("no current segment for WithHandler"));
        };

        Ok(WithHandlerPlan {
            handler_marker,
            outside_seg_id,
            handler,
        })
    }

    /// Iterate all non-completed dispatches, calling a closure on each.
    pub(crate) fn for_each_active<F>(&self, mut f: F)
    where
        F: FnMut(&Dispatch),
    {
        for id in &self.insertion_order {
            if let Some(ctx) = self.dispatches.get(id) {
                if !ctx.completed {
                    f(ctx);
                }
            }
        }
    }

    /// Iterate ALL dispatches (including completed) in insertion order, mutable.
    pub(crate) fn for_each_dispatch_mut<F>(&mut self, mut f: F)
    where
        F: FnMut(&mut Dispatch),
    {
        let order: Vec<DispatchId> = self.insertion_order.clone();
        for id in order {
            if let Some(ctx) = self.dispatches.get_mut(&id) {
                f(ctx);
            }
        }
    }

    /// Iterate non-completed dispatches in insertion order, calling a mutable closure.
    pub(crate) fn for_each_active_mut<F>(&mut self, mut f: F)
    where
        F: FnMut(DispatchId, &mut Dispatch),
    {
        let order: Vec<DispatchId> = self.insertion_order.clone();
        for id in order {
            if let Some(ctx) = self.dispatches.get_mut(&id) {
                if !ctx.completed {
                    f(id, ctx);
                }
            }
        }
    }
}
